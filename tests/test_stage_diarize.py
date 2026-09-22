"""pyannote: who was talking, with sub-stage progress (Phase 2 Task 6).

Diarization is the one stage where the machine this runs on fights back, so the
tests are split by what can actually go wrong:

* **The progress hook** is a pure function over pyannote's callback signature.
  Every awkward case - a step that reports no counter, an unknown step name, a
  counter that goes backwards, a zero total - is two lines here and an hour of
  luck against a real pipeline.
* **The waveform loader** is the one test that has to touch a real file,
  because it exists entirely to work around a broken decoder. `torchaudio.load`
  and `torchcodec` both fail on this machine with `[WinError 127]` (FFmpeg 8.1
  against torchcodec 0.16.0's older libav* symbols), so pyannote cannot decode
  from a path here at all. A test that stubbed the loader would prove nothing
  about the thing that breaks.
* **The orchestration** - what reaches pyannote, and what is let go of
  afterwards - runs against a stand-in pipeline. Two properties matter most:
  pyannote is handed samples and never a path, and the pipeline is released
  before the next stage asks for its VRAM.
* **Two real diarizations** of the fixture clip, both marked `gpu`. One uses
  the pipeline the spec ships and skips on this machine, because
  `pyannote/speaker-diarization-community-1` is gated and this account is not
  on the list; a skip that says why is honest, a green tick would not be. The
  other substitutes the two public checkpoints that same pipeline is built
  from and actually runs on the card, so "turns and one embedding per cluster
  come out of a real clip" is a passing test here rather than a promise.
"""

import gc
import json
import struct
import wave
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribe import applog, db, jobs, media, paths, runner
from scribe.stages import attribute, diarize

FIXTURES = Path(__file__).parent / "fixtures"
CLIP = FIXTURES / "clip30.wav"


# --- fixtures and stand-ins ---------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    (data / "media").mkdir(parents=True)
    return data


class FakeAnnotation:
    """The slice of pyannote's Annotation this module actually touches."""

    def __init__(self, turns):
        self._turns = [(float(s), float(e), label) for s, e, label in turns]

    def __len__(self):
        return len(self._turns)

    def itertracks(self, yield_label=False):
        for start, end, label in self._turns:
            segment = SimpleNamespace(start=start, end=end)
            yield (segment, "_", label) if yield_label else (segment, "_")

    def labels(self):
        return sorted({label for _, _, label in self._turns})


def an_annotation(turns=((0.0, 5.0, "SPEAKER_00"), (5.0, 9.0, "SPEAKER_01"))):
    return FakeAnnotation(turns)


def a_diarize_output(annotation=None, embeddings=None):
    """pyannote 4's DiarizeOutput dataclass, as far as we read it."""
    annotation = an_annotation() if annotation is None else annotation
    return SimpleNamespace(
        speaker_diarization=annotation,
        exclusive_speaker_diarization=annotation,
        speaker_embeddings=embeddings,
    )


# The step sequence pyannote 4.0.7 actually produces, read off the hook call
# sites in pyannote/audio/pipelines/speaker_diarization.py: a counted step, then
# the same name once more as a finished artifact, and so on.
REAL_STEPS = [
    ("segmentation", 4, 0),
    ("segmentation", 4, 2),
    ("segmentation", 4, 4),
    ("segmentation", None, None),
    ("speaker_counting", None, None),
    ("embeddings", 3, 0),
    ("embeddings", 3, 1),
    ("embeddings", 3, 2),
    ("embeddings", None, None),
    ("discrete_diarization", None, None),
]


class FakePipeline:
    """A stand-in that records what it was handed and drives the hook.

    Its `__call__` closes over `self` the way a real pipeline's inference does,
    which is what makes the weakref tests mean something.
    """

    def __init__(self, output=None, steps=REAL_STEPS, raises=None):
        self.output = a_diarize_output() if output is None else output
        self.steps = list(steps)
        self.raises = raises
        self.calls: list[dict] = []
        self.device = None

    def to(self, device):
        self.device = device
        return self

    def __call__(self, file, **options):
        self.calls.append({"file": file, **options})
        hook = options.get("hook")
        if hook is not None:
            for name, total, completed in self.steps:
                hook(name, None, file=file, total=total, completed=completed)
        if self.raises is not None:
            raise self.raises
        return self.output


class FakeWaveform:
    """Stands in for a torch tensor; deliberately not a str or a Path."""

    def __init__(self, samples=48000):
        self.shape = (1, samples)


def install(monkeypatch, pipeline=None, waveform=None, sample_rate=16000):
    """Replace both seams: the pipeline loader and the waveform loader."""
    pipeline = FakePipeline() if pipeline is None else pipeline
    waveform = FakeWaveform() if waveform is None else waveform
    loaded: list = []

    def load_pipeline(source=None, device=None, *, token=None):
        loaded.append({"source": source, "device": device, "token": token})
        return pipeline

    monkeypatch.setattr(diarize, "load_pipeline_with_source", lambda *a, **k: (load_pipeline(*a, **k), "fake"))
    monkeypatch.setattr(
        diarize, "load_waveform", lambda wav: (waveform, sample_rate)
    )
    return pipeline, loaded


def diarized(monkeypatch, pipeline=None, **kwargs):
    pipeline, loaded = install(monkeypatch, pipeline)
    kwargs.setdefault("on_progress", lambda p: None)
    kwargs.setdefault("device", "cpu")
    turns, embeddings = diarize.diarize(Path("audio.wav"), **kwargs)
    return pipeline, loaded, turns, embeddings


def make_ctx(conn, job_id, state=None, progress=None):
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    ctx = runner.RunnerContext(
        conn=conn,
        job=job,
        params=json.loads(job["params_json"] or "{}"),
        report=(lambda p: None) if progress is None else progress.append,
        cancelled=lambda: False,
        media_path=None,
    )
    ctx.state.update(state or {})
    return ctx


def a_job_with_a_run(conn, params=None):
    """A media row, a queued transcribe job, and the run transcribe created."""
    row = media.ingest_path(conn, CLIP, title="clip30")
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"], params=params or {})
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO run(media_id, model, compute_type, created_at)"
            " VALUES (?, 'tiny', 'int8', 0)",
            (row["id"],),
        )
        conn.commit()
    return job_id, cur.lastrowid


def stage_ctx(conn, job_id, run_id, monkeypatch, pipeline=None, progress=None):
    pipeline, loaded = install(monkeypatch, pipeline)
    ctx = make_ctx(
        conn,
        job_id,
        state={"wav": Path("audio.wav"), "run_id": run_id},
        progress=progress,
    )
    return ctx, pipeline, loaded


def collected(steps):
    """Every fraction the hook reports for a scripted step sequence."""
    seen: list[float] = []
    hook = diarize.ProgressHook(seen.append)
    for name, total, completed in steps:
        hook(name, None, file={}, total=total, completed=completed)
    return seen


# --- ProgressHook -------------------------------------------------------------


def test_a_real_step_sequence_walks_from_zero_to_one():
    seen = collected(REAL_STEPS)

    assert seen == sorted(seen)
    assert seen[0] == 0.0
    assert seen[-1] == 1.0
    assert all(0.0 <= p <= 1.0 for p in seen)


def test_progress_inside_a_step_is_the_counter_pyannote_gave():
    # The measured half of the bargain: within one step the fraction is
    # completed/total, so a long embedding pass is not a frozen bar.
    seen = collected(
        [("embeddings", 4, 0), ("embeddings", 4, 1), ("embeddings", 4, 2)]
    )

    assert len(seen) == 3
    assert seen[0] < seen[1] < seen[2]


def test_a_step_without_a_counter_is_a_finished_step():
    # pyannote's own ProgressHook reads completed=None as completed=total=1;
    # speaker_counting and discrete_diarization only ever report that way, so
    # treating it as "no information" would leave the bar stuck at 45%.
    counted = collected([("segmentation", 4, 2)])
    finished = collected([("segmentation", None, None)])

    assert finished[-1] > counted[-1]


def test_the_steps_stay_in_the_order_pyannote_runs_them():
    segmentation = collected([("segmentation", None, None)])[-1]
    embeddings = collected([("embeddings", None, None)])[-1]

    assert segmentation < embeddings < 1.0


def test_an_unknown_step_name_is_ignored_rather_than_guessed():
    # pyannote adds and renames steps between versions. A name we do not know
    # is not a position in the file, so it reports nothing at all.
    seen = collected(
        [("segmentation", 4, 2), ("plda_calibration", 8, 4), ("segmentation", 4, 3)]
    )

    assert len(seen) == 2
    assert seen == sorted(seen)


def test_progress_never_goes_backwards():
    seen = collected([("segmentation", 10, 8), ("segmentation", 10, 3)])

    assert len(seen) == 1


def test_a_step_that_reports_a_zero_total_starts_its_band_instead_of_ending_it():
    """No denominator is not a finish, and it is not a division by zero either.

    The two shapes mean different things and used to share a branch.
    `completed=None` is pyannote saying the step is done, and earns the band
    ceiling. A counter with a zero total is pyannote saying the step has begun
    with nothing measurable behind it - the band floor. The ceiling would
    announce a finished segmentation pass before a single frame had been
    through the network.

    Pinned to exact values on purpose. The previous version of this test
    asserted `all(0.0 <= p <= 1.0 for p in seen)`, which an empty list also
    satisfies: it passed just as happily against a hook that reported nothing
    at all, so it pinned no behaviour whatsoever.
    """
    assert collected([("segmentation", 0, 0)]) == [0.0]
    assert collected([("embeddings", 0, 0)]) == [0.45]
    # A counter with no total at all is the same claim, differently spelled.
    assert collected([("embeddings", None, 3)]) == [0.45]


def test_a_counter_past_its_total_still_reports_a_fraction():
    seen = collected([("embeddings", 3, 9)])

    assert seen and seen[-1] <= 1.0


def test_the_hook_is_a_context_manager_like_pyannotes_own():
    # So it can be dropped into pyannote's Hooks() next to ArtifactHook.
    seen: list[float] = []
    with diarize.ProgressHook(seen.append) as hook:
        hook("segmentation", None, total=2, completed=1)

    assert seen


def test_the_hook_matches_the_signature_pyannote_calls_it_with():
    # pyannote calls hook(step_name, artifact) positionally and the rest by
    # keyword; a hook that insists on more than that raises inside the pipeline.
    seen: list[float] = []
    diarize.ProgressHook(seen.append)("segmentation", None)

    assert seen


# --- load_waveform: the torchcodec workaround ---------------------------------


def test_the_prepared_clip_loads_without_torchaudio(tmp_path):
    """The test this module exists for.

    `torchaudio.load` and `import torchcodec` both die on this machine with
    [WinError 127], so the samples are read with stdlib `wave`. If that ever
    stops working, diarization stops working, and this is where it shows.
    """
    waveform, sample_rate = diarize.load_waveform(CLIP)

    assert sample_rate == 16000
    assert waveform.ndim == 2
    assert waveform.shape[0] == 1
    assert waveform.shape[1] == pytest.approx(30 * 16000, rel=0.05)


def test_the_waveform_is_shaped_the_way_pyannote_demands():
    # pyannote rejects anything that is not (channel, time) with more samples
    # than channels - and says so only from deep inside Audio.validate_file.
    waveform, _ = diarize.load_waveform(CLIP)

    assert waveform.shape[0] <= waveform.shape[1]


def test_the_samples_are_floats_in_the_range_models_expect():
    waveform, _ = diarize.load_waveform(CLIP)

    assert str(waveform.dtype) == "torch.float32"
    assert float(waveform.abs().max()) <= 1.0
    assert float(waveform.abs().max()) > 0.0  # not silence


def test_a_stereo_wav_keeps_its_channels_in_the_right_axis(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(struct.pack("<" + "h" * 200, *([1000, -1000] * 100)))

    waveform, sample_rate = diarize.load_waveform(path)

    assert waveform.shape == (2, 100)
    assert sample_rate == 16000


def test_a_wav_that_is_not_16_bit_pcm_says_so_instead_of_reading_noise(tmp_path):
    # prepare writes pcm_s16le and nothing else; if that ever changes, reading
    # the bytes as int16 anyway would hand pyannote convincing garbage.
    path = tmp_path / "eight-bit.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(1)
        out.setframerate(16000)
        out.writeframes(bytes(100))

    with pytest.raises(ValueError, match="16-bit"):
        diarize.load_waveform(path)


# --- load_pipeline ------------------------------------------------------------


def opens(monkeypatch, outcome_for):
    """Record every source load_pipeline tries; `outcome_for(source)` decides.

    Always a callable, never "a pipeline or a callable": a FakePipeline is
    itself callable, so a helper that sniffed for that would quietly call the
    stand-in instead of returning it.
    """
    tried: list = []

    def open_pipeline(source, *, device, token):
        tried.append(source)
        outcome = outcome_for(source)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(diarize, "open_pipeline", open_pipeline)
    return tried


def test_weights_nowhere_is_a_plain_language_error(data_dir, monkeypatch):
    opens(monkeypatch, lambda source: None)
    # The assembled fallback is the last resort; here it is out too.
    monkeypatch.setattr(
        diarize,
        "assemble_fallback_pipeline",
        lambda *, device, token: (_ for _ in ()).throw(OSError("offline")),
    )

    with pytest.raises(diarize.WeightsUnavailable) as caught:
        diarize.load_pipeline(device="cpu")

    message = str(caught.value)
    assert diarize.DEFAULT_PIPELINE in message
    assert str(diarize.local_weights_dir()) in message
    assert "hf.co" in message  # where to accept the conditions
    assert "HF_TOKEN" in message  # and where to put the token afterwards


def test_local_weights_are_preferred_over_the_hub(data_dir, monkeypatch):
    local = diarize.local_weights_dir()
    local.mkdir(parents=True)
    (local / "config.yaml").write_text("pipeline: {}", encoding="utf-8")
    pipeline = FakePipeline()
    tried = opens(monkeypatch, lambda source: pipeline)

    assert diarize.load_pipeline(device="cpu") is pipeline
    assert tried == [local]  # the hub was never asked


def test_the_hub_is_the_fallback_when_nothing_is_stored_locally(data_dir, monkeypatch):
    pipeline = FakePipeline()
    tried = opens(monkeypatch, lambda source: pipeline if source == diarize.DEFAULT_PIPELINE else None)

    assert diarize.load_pipeline(device="cpu") is pipeline
    assert tried[-1] == diarize.DEFAULT_PIPELINE


def test_a_local_directory_that_is_not_a_pipeline_falls_through_to_the_hub(
    data_dir, monkeypatch
):
    local = diarize.local_weights_dir()
    local.mkdir(parents=True)  # exists, but holds no config.yaml
    pipeline = FakePipeline()

    def result(source):
        if source == diarize.DEFAULT_PIPELINE:
            return pipeline
        raise FileNotFoundError(f"{source}/config.yaml")

    tried = opens(monkeypatch, result)

    assert diarize.load_pipeline(device="cpu") is pipeline
    assert tried == [local, diarize.DEFAULT_PIPELINE]


def test_a_gated_repository_is_reported_with_the_reason_it_gave(data_dir, monkeypatch):
    # pyannote returns None for a gated repo on the config, and raises from
    # deeper down for a gated sub-model. Both have to end up in the hint.
    opens(monkeypatch, lambda source: RuntimeError("403 Client Error: gated repo"))
    monkeypatch.setattr(
        diarize,
        "assemble_fallback_pipeline",
        lambda *, device, token: (_ for _ in ()).throw(OSError("offline")),
    )

    with pytest.raises(diarize.WeightsUnavailable) as caught:
        diarize.load_pipeline(device="cpu")

    assert "403" in str(caught.value)


def test_an_explicit_source_is_the_only_one_tried(data_dir, monkeypatch):
    tried = opens(monkeypatch, lambda source: None)

    with pytest.raises(diarize.WeightsUnavailable):
        diarize.load_pipeline("someone/their-own-pipeline", device="cpu")

    assert tried == ["someone/their-own-pipeline"]


def test_the_local_weights_directory_lives_under_the_models_directory(data_dir):
    assert diarize.local_weights_dir().parent == paths.MODELS_DIR


# --- the token ----------------------------------------------------------------


def test_the_token_comes_from_the_settings_table_first(conn, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment")
    with db.LOCK:
        conn.execute("INSERT INTO setting(key, value) VALUES ('hf_token', 'hf_stored')")
        conn.commit()

    assert diarize.hf_token(conn) == "hf_stored"


def test_the_environment_is_the_fallback_for_the_token(conn, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment")

    assert diarize.hf_token(conn) == "hf_from_the_environment"


def test_no_token_anywhere_is_none_not_an_empty_string(conn, monkeypatch):
    # Hugging Face treats "" as a token and answers 401; None means anonymous.
    # All three names the resolver looks for: the hub's own spelling is one a
    # developer's machine may well have, and this test is about the absence.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    with db.LOCK:
        conn.execute("INSERT INTO setting(key, value) VALUES ('hf_token', '  ')")
        conn.commit()

    assert diarize.hf_token(conn) is None


# --- diarize: pyannote gets samples, never a path -----------------------------


def test_pyannote_is_handed_samples_and_never_a_path(monkeypatch):
    """The workaround, asserted from the inside.

    torchcodec cannot open a file on this machine, so a path reaching pyannote
    is not a style question - it is [WinError 127] and a failed job.
    """
    pipeline, _, _, _ = diarized(monkeypatch)

    handed = pipeline.calls[0]["file"]
    assert isinstance(handed, dict)
    assert set(handed) == {"waveform", "sample_rate"}
    assert handed["sample_rate"] == 16000
    assert not isinstance(handed["waveform"], (str, Path))


def test_nothing_that_looks_like_a_filename_reaches_pyannote(monkeypatch):
    pipeline, _, _, _ = diarized(monkeypatch)

    passed = [pipeline.calls[0]["file"], *pipeline.calls[0].values()]
    assert not any(isinstance(value, (str, Path)) for value in passed)


def test_the_wav_path_goes_to_the_loader_not_to_the_model(monkeypatch):
    seen: list = []
    install(monkeypatch)
    monkeypatch.setattr(
        diarize, "load_waveform", lambda wav: (seen.append(wav), (FakeWaveform(), 16000))[1]
    )

    diarize.diarize(Path("work/7/audio.wav"), on_progress=lambda p: None, device="cpu")

    assert seen == [Path("work/7/audio.wav")]


# --- diarize: what it asks for and what it returns ----------------------------


def test_the_speaker_hints_reach_the_pipeline(monkeypatch):
    pipeline, _, _, _ = diarized(monkeypatch, min_speakers=2, max_speakers=6)

    assert pipeline.calls[0]["min_speakers"] == 2
    assert pipeline.calls[0]["max_speakers"] == 6


def test_an_exact_speaker_count_reaches_the_pipeline(monkeypatch):
    pipeline, _, _, _ = diarized(monkeypatch, num_speakers=3)

    assert pipeline.calls[0]["num_speakers"] == 3


def test_contradictory_speaker_bounds_are_refused_before_a_model_loads(monkeypatch):
    _, loaded = install(monkeypatch)

    with pytest.raises(ValueError, match="min_speakers"):
        diarize.diarize(
            Path("audio.wav"),
            min_speakers=5,
            max_speakers=2,
            on_progress=lambda p: None,
            device="cpu",
        )

    assert loaded == []  # nothing was downloaded to find that out


def test_the_turns_come_back_in_the_shape_attribution_expects(monkeypatch):
    _, _, turns, _ = diarized(monkeypatch)

    assert turns == [
        attribute.Turn(0.0, 5.0, "SPEAKER_00"),
        attribute.Turn(5.0, 9.0, "SPEAKER_01"),
    ]
    assert all(type(t.start) is float for t in turns)


def test_the_turns_are_the_ones_the_attribute_stage_can_join_on(monkeypatch):
    # The two halves of the join, wired together once so a change to either
    # side cannot silently stop fitting.
    _, _, turns, _ = diarized(monkeypatch)

    words = [{"start": 1.0, "end": 1.4}, {"start": 6.0, "end": 6.5}]
    assert [w["speaker"] for w in attribute.join(words, turns)] == [
        "SPEAKER_00",
        "SPEAKER_01",
    ]


def test_a_bare_annotation_is_understood_as_well(monkeypatch):
    # A re-hosted pipeline config may set legacy=True, which returns the
    # Annotation on its own instead of pyannote 4's DiarizeOutput.
    _, _, turns, embeddings = diarized(monkeypatch, FakePipeline(output=an_annotation()))

    assert len(turns) == 2
    assert embeddings == {}


def test_a_pyannote_3_style_tuple_is_understood_as_well(monkeypatch):
    # pyannote 3's return_embeddings=True handed back (annotation, embeddings).
    output = (an_annotation(), [[1.0, 2.0], [3.0, 4.0]])
    _, _, turns, embeddings = diarized(monkeypatch, FakePipeline(output=output))

    assert len(turns) == 2
    assert set(embeddings) == {"SPEAKER_00", "SPEAKER_01"}


def test_silence_diarizes_to_no_turns_rather_than_an_error(monkeypatch):
    _, _, turns, embeddings = diarized(
        monkeypatch, FakePipeline(output=a_diarize_output(an_annotation([])))
    )

    assert turns == []
    assert embeddings == {}


# --- diarize: the embeddings --------------------------------------------------


def test_one_embedding_comes_back_per_cluster(monkeypatch):
    output = a_diarize_output(embeddings=[[0.5, -0.25, 0.125], [1.0, 2.0, 3.0]])

    _, _, _, embeddings = diarized(monkeypatch, FakePipeline(output=output))

    assert set(embeddings) == {"SPEAKER_00", "SPEAKER_01"}
    assert all(isinstance(blob, bytes) for blob in embeddings.values())


def test_an_embedding_survives_the_round_trip_to_bytes(monkeypatch):
    # These are stored to re-anchor speaker names after a re-diarization, which
    # only works if the numbers that come back are the numbers that went in.
    output = a_diarize_output(embeddings=[[0.5, -0.25, 0.125], [1.0, 2.0, 3.0]])

    _, _, _, embeddings = diarized(monkeypatch, FakePipeline(output=output))

    assert diarize.decode_embedding(embeddings["SPEAKER_00"]) == [0.5, -0.25, 0.125]
    assert len(embeddings["SPEAKER_00"]) == 3 * 4  # float32, so the dim is recoverable


def test_the_embedding_rows_line_up_with_the_speaker_labels(monkeypatch):
    output = a_diarize_output(embeddings=[[1.0, 1.0], [9.0, 9.0]])

    _, _, _, embeddings = diarized(monkeypatch, FakePipeline(output=output))

    assert diarize.decode_embedding(embeddings["SPEAKER_01"]) == [9.0, 9.0]


def test_a_zero_padded_embedding_is_not_stored(monkeypatch):
    # pyannote pads with zero rows when it has more labels than centroids. A
    # zero vector has no direction, so a later cosine match against it is
    # meaningless; storing it would look like evidence and be none.
    output = a_diarize_output(embeddings=[[1.0, 2.0], [0.0, 0.0]])

    _, _, _, embeddings = diarized(monkeypatch, FakePipeline(output=output))

    assert set(embeddings) == {"SPEAKER_00"}


def test_an_embedding_full_of_nans_is_not_stored(monkeypatch):
    output = a_diarize_output(embeddings=[[1.0, 2.0], [float("nan"), float("nan")]])

    _, _, _, embeddings = diarized(monkeypatch, FakePipeline(output=output))

    assert set(embeddings) == {"SPEAKER_00"}


def test_a_pipeline_that_returns_no_embeddings_is_not_a_failure(monkeypatch):
    # OracleClustering hands back None; the transcript is still perfectly good.
    _, _, turns, embeddings = diarized(monkeypatch)

    assert len(turns) == 2
    assert embeddings == {}


# --- diarize: the VRAM ---------------------------------------------------------


def test_the_pipeline_is_let_go_of_before_the_stage_returns(monkeypatch):
    holder: dict[str, weakref.ref] = {}

    def load_pipeline(source=None, device=None, *, token=None):
        pipeline = FakePipeline()
        holder["ref"] = weakref.ref(pipeline)
        return pipeline

    monkeypatch.setattr(diarize, "load_pipeline_with_source", lambda *a, **k: (load_pipeline(*a, **k), "fake"))
    monkeypatch.setattr(diarize, "load_waveform", lambda wav: (FakeWaveform(), 16000))

    diarize.diarize(Path("audio.wav"), on_progress=lambda p: None, device="cpu")
    gc.collect()

    assert holder["ref"]() is None


def test_the_pipeline_is_let_go_of_even_when_diarization_fails(monkeypatch):
    """The one that matters: the failure path is where VRAM leaks.

    A raised exception's traceback pins the frame that holds the pipeline, and
    anything still holding the exception - a logger, the runner writing its
    verdict - holds 3 GB of weights with it. `caught` is deliberately still in
    scope at the assertion, because that is the shape of the real failure.
    """
    holder: dict[str, weakref.ref] = {}

    def load_pipeline(source=None, device=None, *, token=None):
        pipeline = FakePipeline(raises=RuntimeError("CUDA out of memory"))
        holder["ref"] = weakref.ref(pipeline)
        return pipeline

    monkeypatch.setattr(diarize, "load_pipeline_with_source", lambda *a, **k: (load_pipeline(*a, **k), "fake"))
    monkeypatch.setattr(diarize, "load_waveform", lambda wav: (FakeWaveform(), 16000))

    with pytest.raises(RuntimeError) as caught:
        diarize.diarize(Path("audio.wav"), on_progress=lambda p: None, device="cpu")
    gc.collect()

    assert caught.value.args
    assert holder["ref"]() is None


def test_the_samples_are_let_go_of_too(monkeypatch):
    # An hour of 16 kHz float32 is 230 MB of RAM; four hours is nearly a
    # gigabyte. Holding it while the next stage runs is the same mistake as
    # holding the weights, just in the other kind of memory.
    holder: dict[str, weakref.ref] = {}

    class ForgetfulPipeline:
        """Does not keep the samples it was handed - and neither does pyannote.

        The recording stand-in used everywhere else would hold them itself, and
        the test would then prove a property of the test.
        """

        def __call__(self, file, **options):
            return a_diarize_output()

    monkeypatch.setattr(
        diarize, "load_pipeline_with_source", lambda *a, **kw: (ForgetfulPipeline(), "fake")
    )

    def load_waveform(wav):
        waveform = FakeWaveform()
        holder["ref"] = weakref.ref(waveform)
        return waveform, 16000

    monkeypatch.setattr(diarize, "load_waveform", load_waveform)

    diarize.diarize(Path("audio.wav"), on_progress=lambda p: None, device="cpu")
    gc.collect()

    assert holder["ref"]() is None


def test_the_progress_of_the_real_steps_reaches_the_caller(monkeypatch):
    seen: list[float] = []
    install(monkeypatch)

    diarize.diarize(Path("audio.wav"), on_progress=seen.append, device="cpu")

    assert seen == sorted(seen)
    assert seen[-1] == 1.0


# --- the stage ----------------------------------------------------------------


def test_the_stage_stores_one_embedding_row_per_cluster(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    output = a_diarize_output(embeddings=[[1.0, 2.0], [3.0, 4.0]])
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, FakePipeline(output=output))

    diarize.run(ctx)

    rows = conn.execute(
        "SELECT * FROM speaker_embedding WHERE run_id=? ORDER BY cluster_label", (run_id,)
    ).fetchall()
    assert [r["cluster_label"] for r in rows] == ["SPEAKER_00", "SPEAKER_01"]
    assert diarize.decode_embedding(rows[0]["embedding"]) == [1.0, 2.0]


def test_re_running_diarization_replaces_the_previous_embeddings(
    conn, data_dir, monkeypatch
):
    # A diarize-only re-run writes over the same (run, cluster) keys. A plain
    # insert dies there on the UNIQUE constraint and loses the whole
    # transaction; the label that survives must also carry the new numbers.
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    first = a_diarize_output(embeddings=[[1.0, 2.0], [3.0, 4.0]])
    second = a_diarize_output(embeddings=[[9.0, 9.0], [8.0, 8.0]])

    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, FakePipeline(output=first))
    diarize.run(ctx)
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, FakePipeline(output=second))
    diarize.run(ctx)

    rows = conn.execute(
        "SELECT * FROM speaker_embedding WHERE run_id=? ORDER BY cluster_label", (run_id,)
    ).fetchall()
    assert len(rows) == 2
    assert diarize.decode_embedding(rows[0]["embedding"]) == [9.0, 9.0]


def test_a_re_run_that_finds_fewer_speakers_leaves_no_stale_clusters(
    conn, data_dir, monkeypatch
):
    # The one an upsert alone does not cover, and the one that matters most:
    # these embeddings exist to re-anchor display names after a re-diarization
    # (spec section 8). A row for a cluster that no longer has any turns is a
    # candidate the matcher will happily hang somebody's name on.
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    two = a_diarize_output(embeddings=[[1.0, 2.0], [3.0, 4.0]])
    one = a_diarize_output(
        an_annotation([(0.0, 9.0, "SPEAKER_00")]), embeddings=[[5.0, 6.0]]
    )

    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, FakePipeline(output=two))
    diarize.run(ctx)
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, FakePipeline(output=one))
    diarize.run(ctx)

    rows = conn.execute(
        "SELECT * FROM speaker_embedding WHERE run_id=?", (run_id,)
    ).fetchall()
    assert [r["cluster_label"] for r in rows] == ["SPEAKER_00"]
    assert diarize.decode_embedding(rows[0]["embedding"]) == [5.0, 6.0]


def test_a_re_run_that_finds_no_embeddings_clears_the_old_ones(
    conn, data_dir, monkeypatch
):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    before = a_diarize_output(embeddings=[[1.0, 2.0], [3.0, 4.0]])

    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, FakePipeline(output=before))
    diarize.run(ctx)
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)  # no embeddings
    diarize.run(ctx)

    assert conn.execute(
        "SELECT COUNT(*) FROM speaker_embedding WHERE run_id=?", (run_id,)
    ).fetchone()[0] == 0


def test_one_runs_embeddings_are_not_another_runs_business(conn, data_dir, monkeypatch):
    # The delete is scoped to the run: re-diarizing one run must not empty the
    # rows of the other run over the same media that the user is comparing it to.
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    media_id = conn.execute("SELECT media_id FROM run WHERE id=?", (run_id,)).fetchone()[0]
    with db.LOCK:
        other = conn.execute(
            "INSERT INTO run(media_id, model, compute_type, created_at)"
            " VALUES (?, 'large-v3', 'float16', 0)",
            (media_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO speaker_embedding(run_id, cluster_label, embedding)"
            " VALUES (?, 'SPEAKER_00', ?)",
            (other, diarize.encode_embedding([7.0, 7.0])),
        )
        conn.commit()

    ctx, _, _ = stage_ctx(
        conn, job_id, run_id, monkeypatch,
        FakePipeline(output=a_diarize_output(embeddings=[[1.0, 2.0], [3.0, 4.0]])),
    )
    diarize.run(ctx)

    assert conn.execute(
        "SELECT COUNT(*) FROM speaker_embedding WHERE run_id=?", (other,)
    ).fetchone()[0] == 1


def test_the_stage_hands_the_turns_to_the_attribute_stage(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)

    diarize.run(ctx)

    assert ctx.state["turns"] == [
        attribute.Turn(0.0, 5.0, "SPEAKER_00"),
        attribute.Turn(5.0, 9.0, "SPEAKER_01"),
    ]


def test_the_stage_announces_what_it_found(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)

    diarize.run(ctx)

    events = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "diarize"]
    assert len(events) == 1
    assert events[0]["payload"]["n_turns"] == 2
    assert events[0]["payload"]["speakers"] == ["SPEAKER_00", "SPEAKER_01"]


def test_the_stage_finishes_at_one(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    progress: list[float] = []
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, progress=progress)

    diarize.run(ctx)

    assert progress[-1] == 1.0


def test_the_stage_passes_the_jobs_speaker_hints_through(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(
        conn, params={"device": "cpu", "min_speakers": 2, "max_speakers": 4}
    )
    ctx, pipeline, _ = stage_ctx(conn, job_id, run_id, monkeypatch)

    diarize.run(ctx)

    assert pipeline.calls[0]["min_speakers"] == 2
    assert pipeline.calls[0]["max_speakers"] == 4


def test_the_stage_uses_the_token_this_library_stored(conn, data_dir, monkeypatch):
    with db.LOCK:
        conn.execute("INSERT INTO setting(key, value) VALUES ('hf_token', 'hf_stored')")
        conn.commit()
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, loaded = stage_ctx(conn, job_id, run_id, monkeypatch)

    diarize.run(ctx)

    assert loaded[0]["token"] == "hf_stored"


def test_diarization_can_be_turned_off_without_loading_a_model(
    conn, data_dir, monkeypatch
):
    # A single-speaker recording does not need three gigabytes of weights, and
    # the e2e test on CPU cannot afford them at all.
    job_id, run_id = a_job_with_a_run(conn, params={"diarize": False})
    progress: list[float] = []
    ctx, _, loaded = stage_ctx(conn, job_id, run_id, monkeypatch, progress=progress)

    diarize.run(ctx)

    assert loaded == []
    assert ctx.state["turns"] == []
    assert progress[-1] == 1.0
    assert conn.execute("SELECT COUNT(*) FROM speaker_embedding").fetchone()[0] == 0


def test_the_stage_without_a_prepared_wav_refuses_to_guess(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    install(monkeypatch)
    ctx = make_ctx(conn, job_id, state={"run_id": run_id})

    with pytest.raises(RuntimeError, match="prepare"):
        diarize.run(ctx)


def test_the_stage_without_a_transcription_run_refuses_to_guess(
    conn, data_dir, monkeypatch
):
    # Embeddings hang off a run row. Without one there is nowhere to put them,
    # and inventing a run would orphan the words that belong to the real one.
    job_id, _ = a_job_with_a_run(conn, params={"device": "cpu"})
    install(monkeypatch)
    ctx = make_ctx(conn, job_id, state={"wav": Path("audio.wav")})

    with pytest.raises(RuntimeError, match="transcribe"):
        diarize.run(ctx)


def test_a_stage_that_finds_no_speakers_still_leaves_turns_behind(
    conn, data_dir, monkeypatch
):
    # attribute reads ctx.state["turns"] unconditionally; a missing key there
    # would turn a silent recording into a crash two stages later.
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(
        conn, job_id, run_id, monkeypatch, FakePipeline(output=a_diarize_output(an_annotation([])))
    )

    diarize.run(ctx)

    assert ctx.state["turns"] == []


# --- real diarizations ----------------------------------------------------------

# The two public checkpoints pyannote's own 3.1 pipeline is assembled from.
# Neither is gated, which is the whole reason the substituted run below can
# exist on an account that cannot reach the shipped default.
PUBLIC_SEGMENTATION = "pyannote/segmentation-3.0"
PUBLIC_EMBEDDING = "pyannote/wespeaker-voxceleb-resnet34-LM"

# Lifted verbatim from pyannote/speaker-diarization-3.1's own config.yaml.
# A pipeline built in Python is not instantiated, and the defaults that would
# fill in (`default_parameters`, speaker_diarization.py:288) are VBx
# hyperparameters - Fa/Fb - which mean nothing to AgglomerativeClustering.
PUBLIC_PARAMS = {
    "segmentation": {"min_duration_off": 0.0},
    "clustering": {
        "method": "centroid",
        "min_cluster_size": 12,
        "threshold": 0.7045654963945799,
    },
}


def assert_a_real_diarization(turns, embeddings, seen):
    """What "it diarized the clip" means, whichever weights ran.

    The equality on the labels is the point, and it used to be a `<=`. Subset
    is vacuous: `set() <= anything` holds, so the old form passed just as
    happily against a run that produced no embeddings at all - in the one test
    that exists to prove there is one embedding per cluster.
    """
    assert len(turns) >= 1
    assert all(turn.end > turn.start for turn in turns)
    assert embeddings
    assert set(embeddings) == {turn.speaker for turn in turns}
    # float32, little-endian: the dimension comes back out of the length alone.
    assert all(len(blob) > 0 and len(blob) % 4 == 0 for blob in embeddings.values())
    assert all(len(diarize.decode_embedding(blob)) > 0 for blob in embeddings.values())
    assert seen == sorted(seen)
    assert seen[0] >= 0.0
    assert seen[-1] == 1.0


@pytest.mark.gpu
def test_diarizing_the_clip_finds_speakers_and_their_embeddings():
    """The shipped default, end to end - and the one test allowed to skip.

    On this machine it skips: `pyannote/speaker-diarization-community-1` is
    gated and this account is not authorized. pyannote 4.0.7 also pulls its
    PLDA from that same repo in `SpeakerDiarization.__init__`, before it knows
    which clustering was asked for, so even `pyannote/speaker-diarization-3.1`
    - whose own config is cached here and whose two component checkpoints are
    public - dies on the same 403. Verified directly, not inferred:

        >>> Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        GatedRepoError: 403 Client Error

    The remedy is a human one: accept the conditions at
    https://hf.co/pyannote/speaker-diarization-community-1, or drop re-hosted
    weights into MODELS_DIR/pyannote, which is what the spec intends. Until
    then the substituted run below is what actually exercises the card.
    """
    seen: list[float] = []
    try:
        turns, embeddings = diarize.diarize(CLIP, on_progress=seen.append)
    except diarize.WeightsUnavailable as exc:
        pytest.skip(f"no diarization weights on this machine: {exc}")

    assert_a_real_diarization(turns, embeddings, seen)


@pytest.mark.gpu
def test_diarizing_the_clip_with_substituted_public_weights(monkeypatch):
    """The acceptance run, on the weights this account can actually reach.

    What it proves is this module: real samples off the fixture clip, real
    networks on the card, real turns and one real embedding per cluster out,
    and a progress bar that only goes forwards. What it deliberately does not
    prove is the pipeline the spec ships - nothing here can, until somebody
    accepts those conditions. The name says "substituted" for that reason.

    One piece of stagecraft, stated rather than hidden. pyannote 4.0.7 loads a
    PLDA unconditionally in `SpeakerDiarization.__init__`
    (speaker_diarization.py:231) and its default lives in the gated repo, so
    the pipeline cannot be built here without one. `_plda` is then read in
    exactly one place, speaker_diarization.py:275, to construct VBxClustering;
    with AgglomerativeClustering nothing ever touches it. So it gets an
    un-initialised instance - enough to satisfy `get_plda`'s isinstance check,
    and never called. The assertion on the clustering class pins the
    precondition that makes that safe, so a future pyannote that starts using
    the PLDA elsewhere fails here loudly instead of quietly using a stub.
    """
    import huggingface_hub

    from scribe import cuda_setup

    cuda_setup.ensure_cuda_libs()
    import torch

    if not torch.cuda.is_available():
        pytest.skip("no CUDA device")

    from pyannote.audio.core.plda import PLDA
    from pyannote.audio.pipelines import SpeakerDiarization

    def build(source, *, device, token):
        assert source == "substituted-public-components"
        try:
            pipeline = SpeakerDiarization(
                segmentation=PUBLIC_SEGMENTATION,
                embedding=PUBLIC_EMBEDDING,
                clustering="AgglomerativeClustering",
                segmentation_batch_size=32,
                embedding_batch_size=32,
                embedding_exclude_overlap=True,
                plda=object.__new__(PLDA),  # see the docstring; never read
                token=token,
            )
        except (OSError, huggingface_hub.errors.HfHubHTTPError) as exc:
            pytest.skip(f"public component weights unreachable: {exc}")
        assert type(pipeline.clustering).__name__ == "AgglomerativeClustering"
        pipeline.instantiate(PUBLIC_PARAMS)
        return pipeline.to(torch.device(device))

    # The same seam the CPU tests use, so everything between the loader and
    # the returned turns is this module's own code doing its real job.
    monkeypatch.setattr(diarize, "open_pipeline", build)

    seen: list[float] = []
    turns, embeddings = diarize.diarize(
        CLIP,
        on_progress=seen.append,
        device="cuda",
        model_dir_or_id="substituted-public-components",
        token=diarize.hf_token(),
    )

    assert_a_real_diarization(turns, embeddings, seen)
    # 30 s of one Dutch speaker: the turns belong to the clip, not to silence.
    assert max(turn.end for turn in turns) <= 30.5
    assert sum(turn.end - turn.start for turn in turns) > 5.0


# --- the assembled 3.1 fallback -------------------------------------------------
#
# Measured 2026-09-02: a token without gated-repo access gets 403 on both
# pipeline configs while the two checkpoints load from the cache. The stage
# must diarize anyway and say what it used.


def test_a_gated_default_falls_back_to_the_assembled_pipeline(data_dir, monkeypatch):
    tried = opens(monkeypatch, lambda source: None)
    fallback = FakePipeline()
    calls = []
    monkeypatch.setattr(
        diarize,
        "assemble_fallback_pipeline",
        lambda *, device, token: calls.append((device, token)) or fallback,
    )

    pipeline, source = diarize.load_pipeline_with_source(device="cpu", token="hf_x")

    assert pipeline is fallback
    assert source == diarize.FALLBACK_SOURCE
    assert tried[-1] == diarize.DEFAULT_PIPELINE  # the default was tried first
    assert calls == [("cpu", "hf_x")]


def test_load_pipeline_keeps_returning_the_pipeline_alone(data_dir, monkeypatch):
    opens(monkeypatch, lambda source: None)
    fallback = FakePipeline()
    monkeypatch.setattr(diarize, "assemble_fallback_pipeline", lambda *, device, token: fallback)

    assert diarize.load_pipeline(device="cpu") is fallback


def test_an_explicit_source_never_falls_back(data_dir, monkeypatch):
    opens(monkeypatch, lambda source: None)
    monkeypatch.setattr(
        diarize,
        "assemble_fallback_pipeline",
        lambda *, device, token: pytest.fail("explicit source must not fall back"),
    )

    with pytest.raises(diarize.WeightsUnavailable):
        diarize.load_pipeline_with_source("somebody/their-pipeline", device="cpu")


def test_the_hub_default_wins_over_the_fallback_when_it_loads(data_dir, monkeypatch):
    wanted = FakePipeline()
    opens(monkeypatch, lambda source: wanted if source == diarize.DEFAULT_PIPELINE else None)
    monkeypatch.setattr(
        diarize,
        "assemble_fallback_pipeline",
        lambda *, device, token: pytest.fail("fallback must not run when the default loads"),
    )

    pipeline, source = diarize.load_pipeline_with_source(device="cpu")

    assert pipeline is wanted
    assert source == diarize.DEFAULT_PIPELINE


def test_a_failing_fallback_is_named_in_the_error(data_dir, monkeypatch):
    opens(monkeypatch, lambda source: None)
    monkeypatch.setattr(
        diarize,
        "assemble_fallback_pipeline",
        lambda *, device, token: (_ for _ in ()).throw(OSError("no cache")),
    )

    with pytest.raises(diarize.WeightsUnavailable) as caught:
        diarize.load_pipeline_with_source(device="cpu")

    assert diarize.FALLBACK_SOURCE in str(caught.value)
    assert "no cache" in str(caught.value)


def test_fallback_params_are_exactly_pyannote_3_1s():
    # Lifted from pyannote/speaker-diarization-3.1's config.yaml; a drift here
    # silently changes every speaker count the fallback produces.
    assert diarize.FALLBACK_PARAMS == {
        "segmentation": {"min_duration_off": 0.0},
        "clustering": {"method": "centroid", "min_cluster_size": 12, "threshold": 0.7045654963945799},
    }


def test_the_stage_records_the_fallback_on_the_run_and_in_its_event(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    monkeypatch.setattr(
        diarize,
        "load_pipeline_with_source",
        lambda *a, **k: (FakePipeline(), diarize.FALLBACK_SOURCE),
    )

    diarize.run(ctx)

    params = json.loads(conn.execute("SELECT params_json FROM run WHERE id=?", (run_id,)).fetchone()["params_json"])
    assert params["diarization_pipeline"] == diarize.FALLBACK_SOURCE
    assert "community-1 is gated" in params["diarization_note"]
    event = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "diarize"][-1]
    assert event["payload"]["fallback"] is True
    assert event["payload"]["pipeline"] == diarize.FALLBACK_SOURCE


def test_the_stage_records_the_default_pipeline_without_a_note(conn, data_dir, monkeypatch):
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    monkeypatch.setattr(
        diarize,
        "load_pipeline_with_source",
        lambda *a, **k: (FakePipeline(), diarize.DEFAULT_PIPELINE),
    )

    diarize.run(ctx)

    params = json.loads(conn.execute("SELECT params_json FROM run WHERE id=?", (run_id,)).fetchone()["params_json"])
    assert params["diarization_pipeline"] == diarize.DEFAULT_PIPELINE
    assert "diarization_note" not in params
    event = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "diarize"][-1]
    assert event["payload"]["fallback"] is False


# --- weights that will not load cost the speakers, not the transcript (TASK-089.08) ---
#
# Measured 2026-09-22, unauthenticated HEAD on
# https://huggingface.co/pyannote/segmentation-3.0/resolve/main/config.yaml:
# HTTP 401. So the assembled fallback is gated too, and a machine with no
# token has no route at all. Until this task WeightsUnavailable left the fifth
# of eight stages uncaught: an hour of audio ended as a failed job with no
# transcript, which is the one thing the user actually waited for.


def no_weights(monkeypatch):
    """Every route refuses, the way a tokenless machine's does."""

    def refuse(*args, **kwargs):
        raise diarize.WeightsUnavailable("No speaker diarization weights could be loaded.")

    monkeypatch.setattr(diarize, "load_pipeline_with_source", refuse)


def run_params(conn, run_id):
    row = conn.execute("SELECT params_json FROM run WHERE id=?", (run_id,)).fetchone()
    return json.loads(row["params_json"] or "{}")


def diarize_events(conn, job_id):
    return [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "diarize"]


def test_weights_that_will_not_load_keep_the_transcript_and_skip_the_speakers(
    conn, data_dir, monkeypatch
):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    progress: list[float] = []
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch, progress=progress)
    no_weights(monkeypatch)

    diarize.run(ctx)

    # attribute reads this next; [] is the same state "diarize": false leaves.
    assert ctx.state["turns"] == []
    assert progress[-1] == 1.0


def test_a_run_without_speakers_says_so_in_words_and_names_the_three_routes(
    conn, data_dir, monkeypatch
):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    no_weights(monkeypatch)

    diarize.run(ctx)

    note = run_params(conn, run_id)["diarization_note"]
    assert "Settings" in note
    assert "HF_TOKEN" in note
    assert "MODELS_DIR/pyannote" in note
    # By name and not by resolved path: this note goes into run.params_json
    # and the JSON export writes that out verbatim (exports/jsonw._params),
    # so a shared transcript would otherwise carry the directory layout of
    # the machine that made it.
    assert str(diarize.local_weights_dir()) not in note
    # Nothing loaded, so there is no pipeline to name: an empty name would
    # read as "diarized with the pipeline called ''" in the JSON export.
    assert "diarization_pipeline" not in run_params(conn, run_id)


def test_a_skipped_diarization_is_announced_rather_than_passed_over(
    conn, data_dir, monkeypatch
):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    no_weights(monkeypatch)

    diarize.run(ctx)

    (event,) = diarize_events(conn, job_id)
    # Structured keys, not prose: the jobs board truncates every value at 80
    # characters (jobs_ui._short), and `reason` is what tells this apart from
    # the user switching "Recognise speakers" off - which emits skipped too.
    assert event["payload"]["skipped"] is True
    assert event["payload"]["reason"] == "weights-unavailable"
    assert event["payload"]["token_found"] is False
    assert event["payload"]["n_turns"] == 0


def test_the_note_says_no_token_was_found_when_there_is_none(conn, data_dir, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    no_weights(monkeypatch)

    diarize.run(ctx)

    note = run_params(conn, run_id)["diarization_note"]
    assert "no Hugging Face token was found" in note


def test_the_note_names_where_a_token_that_did_not_open_the_model_came_from(
    conn, data_dir, monkeypatch
):
    # The 401-versus-403 distinction as far as this stage can honestly know
    # it: pyannote hands back None for missing, private and gated alike, so
    # the stage never sees a status code. What it does know is whether a
    # token resolved at all, and from where.
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    with db.LOCK:
        conn.execute("INSERT INTO setting(key, value) VALUES ('hf_token', 'hf_stored')")
        conn.commit()
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    no_weights(monkeypatch)

    diarize.run(ctx)

    note = run_params(conn, run_id)["diarization_note"]
    assert "no Hugging Face token was found" not in note
    assert "token from settings" in note  # where it came from, never what it is
    assert "hf_stored" not in note
    (event,) = diarize_events(conn, job_id)
    assert event["payload"]["token_found"] is True
    # The jobs board reads this line: "a token was found, here" is a different
    # job to look at from "no token anywhere", and the place is the only part
    # of a token that may be shown.
    assert event["payload"]["token_source"] == "settings"


def test_only_the_first_line_of_the_failure_reaches_the_application_log(
    conn, data_dir, monkeypatch
):
    """The per-route lines stay out of app.log, on purpose.

    Under the fixed first sentence `_no_weights_hint` lists what every route
    said, which is huggingface_hub's text - and a download error quotes a
    signed CDN URL. applog replaces a query string only when the value starts
    with the URL, and `detail` is not a name it redacts, so a mid-sentence one
    would be written whole.
    """
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    monkeypatch.setattr(
        diarize,
        "load_pipeline_with_source",
        lambda *a, **k: (_ for _ in ()).throw(
            diarize.WeightsUnavailable(
                "No speaker diarization weights could be loaded.\n"
                "Tried:\n  - https://cdn-lfs.hf.co/x/model.bin?Expires=1&Signature=s3cr3t"
            )
        ),
    )

    diarize.run(ctx)

    lines, _ = applog.tail(0)
    (skipped,) = [line for line in lines if line["event"] == "diarize.skipped"]
    assert skipped["detail"] == "No speaker diarization weights could be loaded."
    assert "Signature" not in json.dumps(skipped)


def test_a_failure_with_no_message_at_all_is_still_a_kept_transcript(
    conn, data_dir, monkeypatch
):
    """The rescue may not raise. `"".splitlines()` is `[]`, and an IndexError
    here would turn this recovery back into the failed job it exists to
    prevent - no route raises an empty one today, which is exactly why
    nothing would catch it."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    monkeypatch.setattr(
        diarize,
        "load_pipeline_with_source",
        lambda *a, **k: (_ for _ in ()).throw(diarize.WeightsUnavailable("")),
    )

    diarize.run(ctx)

    assert ctx.state["turns"] == []
    assert "diarization_note" in run_params(conn, run_id)


def test_any_other_failure_in_the_stage_still_fails_the_job(conn, data_dir, monkeypatch):
    # Only WeightsUnavailable is a success with a note. A broken wav, a card
    # that ran out of memory, a bug in this module: those are failures, and a
    # job that reported "done" for one would be lying about the transcript.
    job_id, run_id = a_job_with_a_run(conn, params={"device": "cpu"})
    ctx, _, _ = stage_ctx(conn, job_id, run_id, monkeypatch)
    monkeypatch.setattr(
        diarize,
        "diarize",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("the waveform is not 16-bit")),
    )

    with pytest.raises(ValueError, match="16-bit"):
        diarize.run(ctx)
