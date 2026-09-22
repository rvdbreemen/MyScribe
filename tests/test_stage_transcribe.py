"""faster-whisper: the generator is the progress bar (Phase 2 Task 4).

Four kinds of test, because four different things can go wrong here and only
one of them needs a model:

* **The decisions** - which model, which hotwords - are pure functions over a
  database and a media row. Cheap, exhaustive, no audio in sight.
* **The consumption** of Whisper's segment generator is where progress and
  cancellation live, and it is a pure function over an iterable. Tested with
  scripted segments so the awkward cases (a stall, an unknown duration, a
  cancel between segments, a segment with no words) can be handed to it
  instead of provoked out of a real decoder.
* **The orchestration** - what gets asked of the model, and what is let go of
  afterwards - is tested against a stand-in model. The stand-in exists for one
  property in particular: a real `WhisperModel` is kept alive by the generator
  it returns, so a stage that abandons that generator mid-stream leaks a model
  into the next stage's VRAM. A weakref proves it did not.
* **One real transcription** of the fixture clip on CPU with `tiny`, run once
  for the whole session, because that is what proves the shapes above match
  what faster-whisper actually produces.

Nothing here asserts a word of Dutch. `tiny` mishears most of the clip (it
reports the language as English with 0.81 confidence, which is wrong) and that
is fine: the contract under test is timestamps, probabilities and progress, not
accuracy. Accuracy is what the GPU models are for.
"""

import gc
import json
import hashlib
import weakref
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scribe import db, jobs, media, paths, runner
from scribe.stages import transcribe

FIXTURES = Path(__file__).parent / "fixtures"


def test_the_default_model_name_is_spelled_once_in_the_package():
    """ADR-004's own verification: `grep -rn "large-v3-turbo" scribe/` matches
    only stages/transcribe.py. A template comment that spells it is a second
    place for the name to drift, and the grep no longer says what it should."""
    package = Path(transcribe.__file__).resolve().parent.parent
    spelled = sorted(
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.suffix in (".py", ".html", ".js", ".css")
        and transcribe.DEFAULT_MODEL in path.read_text(encoding="utf-8")
    )
    assert spelled == ["stages/transcribe.py"]
CLIP = FIXTURES / "clip30.wav"


# --- fixtures and stand-ins ---------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Tmp DB that runner.main() also finds via the default paths.DB_PATH."""
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
    (data / "media").mkdir(parents=True)
    return data


def fake_word(start, end, text=" word", probability=0.9):
    return SimpleNamespace(start=start, end=end, word=text, probability=probability)


def fake_segment(
    start,
    end,
    text=" hello there.",
    words=None,
    avg_logprob=-0.31,
    no_speech_prob=0.02,
    compression_ratio=1.4,
    temperature=0.0,
):
    if words is None:
        words = [fake_word(start, end, text)]
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=words,
        avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob,
        compression_ratio=compression_ratio,
        temperature=temperature,
    )


def fake_info(duration=30.0, language="nl", language_probability=0.98):
    return SimpleNamespace(
        duration=duration,
        language=language,
        language_probability=language_probability,
        duration_after_vad=duration - 2.0,
    )


class FakeWhisper:
    """A stand-in for WhisperModel that records what it was asked for.

    Its `transcribe` returns a generator that closes over `self`, exactly as
    faster-whisper's does - which is the whole reason a stand-in is worth
    having: that reference is how an abandoned generator keeps a model (and its
    VRAM) alive, and it cannot be reproduced with a plain list.
    """

    def __init__(self, segments, info=None):
        self._segments = list(segments)
        self.info = info if info is not None else fake_info()
        self.calls: list[dict] = []
        self.closed = False

    def transcribe(self, audio, **options):
        self.calls.append({"audio": audio, **options})

        def stream():
            try:
                for segment in self._segments:
                    yield segment
            except GeneratorExit:
                self.closed = True
                raise

        return stream(), self.info


def install(monkeypatch, model, device="cpu", compute_type="int8"):
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, **kw: (model, device, compute_type)
    )


@pytest.fixture(autouse=True)
def single_window_for_fake_paths(monkeypatch):
    """Tests here fake the model and hand it `Path("audio.wav")`, a file that
    does not exist: they are about what the model is asked and what comes
    back, not about reading audio. Since the stage feeds Whisper windows read
    from the wav (see transcribe.iter_windows), a fake path would now fail in
    `wave.open` before the fake model is ever reached. So for a path that is
    not there, yield one silent window and report the fake's 30 s; a real wav
    still goes through the real reader, so the windowing tests stay honest.
    """
    import numpy as np

    real_windows = transcribe.iter_windows
    real_duration = transcribe.wav_duration

    def windows(wav, **kw):
        if Path(wav).exists():
            yield from real_windows(wav, **kw)
        else:
            yield transcribe.Window(0.0, np.zeros(transcribe.SAMPLE_RATE, dtype=np.float32))

    def duration(wav):
        return real_duration(wav) if Path(wav).exists() else 30.0

    monkeypatch.setattr(transcribe, "iter_windows", windows)
    monkeypatch.setattr(transcribe, "wav_duration", duration)


def make_ctx(conn, job_id, state=None, progress=None, cancelled=None):
    """A RunnerContext as the runner would build it, minus the throttle."""
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    ctx = runner.RunnerContext(
        conn=conn,
        job=job,
        params=json.loads(job["params_json"] or "{}"),
        report=(lambda p: None) if progress is None else progress.append,
        cancelled=(lambda: False) if cancelled is None else cancelled,
        media_path=None,
    )
    ctx.state.update(state or {})
    return ctx


def a_job_with_media(conn, params=None, name="clip30.wav"):
    """A media row plus a queued transcribe job pointing at it."""
    row = media.ingest_path(conn, CLIP, title=Path(name).stem)
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"], params=params or {})
    return row, job_id


# --- resolve_model ------------------------------------------------------------


def test_turbo_cannot_translate_so_large_v3_is_substituted():
    model, note = transcribe.resolve_model("large-v3-turbo", "translate")

    assert model == "large-v3"
    assert note == "turbo cannot translate; using large-v3"


def test_turbo_transcribing_is_left_alone():
    assert transcribe.resolve_model("large-v3-turbo", "transcribe") == (
        "large-v3-turbo",
        None,
    )


def test_large_v3_translating_is_left_alone():
    assert transcribe.resolve_model("large-v3", "translate") == ("large-v3", None)


def test_a_turbo_checkpoint_under_another_name_is_still_turbo():
    # The tier is a property of the weights, not of the string the UI sends;
    # a CT2 conversion published under its own repo id is the same model.
    model, note = transcribe.resolve_model(
        "deepdml/faster-whisper-large-v3-turbo-ct2", "translate"
    )

    assert model == "large-v3"
    assert note


def test_a_small_model_may_translate():
    # Only turbo lost the translate task; the hidden CPU fallback keeps it.
    assert transcribe.resolve_model("tiny", "translate") == ("tiny", None)


def test_asking_for_nothing_gets_the_default_model():
    assert transcribe.resolve_model(None, "transcribe") == (transcribe.DEFAULT_MODEL, None)
    assert transcribe.resolve_model("", "transcribe") == (transcribe.DEFAULT_MODEL, None)


def test_the_default_model_is_the_one_doctor_smoke_tests():
    # Two names for one decision would drift the day someone changes the tier.
    from scribe import doctor

    assert doctor.DEFAULT_MODEL is transcribe.DEFAULT_MODEL


# --- compose_hotwords ---------------------------------------------------------


def glossary(conn, *terms_with_weights):
    with db.LOCK:
        conn.executemany(
            "INSERT INTO vocab(term, weight) VALUES (?, ?)", terms_with_weights
        )
        conn.commit()


def test_hotwords_lead_with_the_glossary(conn):
    glossary(conn, ("WHYcast", 1.0))
    row = {"title": "Gesprek met Marieke", "orig_name": "Gesprek met Marieke.mp3"}

    assert transcribe.compose_hotwords(conn, row).startswith("WHYcast")


def test_hotwords_take_the_heaviest_glossary_terms_first(conn):
    glossary(conn, ("Aap", 1.0), ("Noot", 5.0), ("Mies", 3.0))

    assert transcribe.compose_hotwords(conn, {}) == "Noot, Mies, Aap"


def test_hotwords_pick_up_proper_nouns_from_the_title(conn):
    row = {"title": "Interview met Marieke Vermeulen", "orig_name": "raw-042.mp3"}

    assert transcribe.compose_hotwords(conn, row).split(", ")[-2:] == [
        "Marieke",
        "Vermeulen",
    ]


def test_hotwords_ignore_words_nobody_capitalised(conn):
    # Guessing which lowercase words are names is how a glossary fills up with
    # "gesprek" and stops biasing anything.
    row = {"title": "gesprek met de buren", "orig_name": "gesprek met de buren.m4a"}

    assert transcribe.compose_hotwords(conn, row) == ""


def test_hotwords_do_not_include_the_file_extension(conn):
    row = {"title": "Vergadering", "orig_name": "Vergadering.MP3"}

    assert transcribe.compose_hotwords(conn, row) == "Vergadering"


def test_hotwords_read_container_tags_when_the_media_row_carries_them(conn):
    row = {
        "title": "raw-042",
        "orig_name": "raw-042.mkv",
        "tags": {"artist": "Douglas Adams", "album": "Vogon Poetry"},
    }

    assert transcribe.compose_hotwords(conn, row) == "Douglas, Adams, Vogon, Poetry"


def test_hotwords_never_repeat_a_term(conn):
    glossary(conn, ("WHYcast", 1.0))
    row = {"title": "WHYcast met whycast", "orig_name": "WHYcast.mp3"}

    assert transcribe.compose_hotwords(conn, row) == "WHYcast"


def test_hotwords_stop_at_the_token_cap(conn):
    glossary(conn, *[(f"Term{n:03d}", 1.0) for n in range(400)])

    hotwords = transcribe.compose_hotwords(conn, {})

    assert transcribe.estimate_tokens(hotwords) <= 223
    assert hotwords.startswith("Term000, Term001")


def test_a_tighter_cap_yields_fewer_terms(conn):
    glossary(conn, *[(f"Term{n:03d}", 1.0) for n in range(400)])

    assert len(transcribe.compose_hotwords(conn, {}, limit_tokens=20).split(", ")) < len(
        transcribe.compose_hotwords(conn, {}, limit_tokens=223).split(", ")
    )


def test_nothing_to_say_is_an_empty_string_not_a_stray_comma(conn):
    assert transcribe.compose_hotwords(conn, {}) == ""


# --- collect_segments ---------------------------------------------------------


def collected(segments, duration=30.0, cancelled=None):
    seen: list[float] = []
    rows, words = transcribe.collect_segments(
        segments, duration, on_progress=seen.append, cancelled=cancelled
    )
    return rows, words, seen


def test_progress_is_the_segment_end_over_the_duration():
    _, _, seen = collected([fake_segment(0.0, 7.5), fake_segment(7.5, 15.0)], 30.0)

    assert seen == [0.25, 0.5]


def test_progress_never_runs_past_the_end_of_the_file():
    # VAD padding can push the last segment a hair past info.duration.
    _, _, seen = collected([fake_segment(0.0, 30.4)], 30.0)

    assert seen == [1.0]


def test_progress_never_goes_backwards():
    # Whisper re-emits an earlier window after a temperature fallback; a
    # progress bar that retreats is worse than one that pauses.
    _, _, seen = collected(
        [fake_segment(0.0, 20.0), fake_segment(5.0, 10.0), fake_segment(20.0, 30.0)],
        30.0,
    )

    assert seen == [pytest.approx(2 / 3), pytest.approx(2 / 3), 1.0]


def test_nothing_is_reported_when_the_duration_is_unknown():
    # The rule is 0 and then 1, never an invented number in between.
    _, _, seen = collected([fake_segment(0.0, 10.0)], 0.0)

    assert seen == []


def test_segments_and_words_are_numbered_from_zero_across_the_whole_run():
    segments = [
        fake_segment(0.0, 2.0, words=[fake_word(0.0, 1.0), fake_word(1.0, 2.0)]),
        fake_segment(2.0, 3.0, words=[fake_word(2.0, 3.0)]),
    ]

    rows, words, _ = collected(segments)

    assert [r["idx"] for r in rows] == [0, 1]
    assert [w["idx"] for w in words] == [0, 1, 2]


def test_each_word_knows_which_segment_it_came_from():
    segments = [
        fake_segment(0.0, 2.0, words=[fake_word(0.0, 1.0), fake_word(1.0, 2.0)]),
        fake_segment(2.0, 3.0, words=[fake_word(2.0, 3.0)]),
    ]

    _, words, _ = collected(segments)

    assert [w["segment_idx"] for w in words] == [0, 0, 1]


def test_whispers_confidence_numbers_are_kept_not_discarded():
    rows, _, _ = collected(
        [
            fake_segment(
                0.0,
                2.0,
                avg_logprob=-0.42,
                no_speech_prob=0.11,
                compression_ratio=1.87,
                temperature=0.2,
            )
        ]
    )

    assert rows[0]["avg_logprob"] == -0.42
    assert rows[0]["no_speech_prob"] == 0.11
    assert rows[0]["compression_ratio"] == 1.87
    assert rows[0]["temperature"] == 0.2


def test_segment_text_is_stripped_but_word_text_is_not():
    # Words are canonical: "".join(word.text) has to reproduce the transcript,
    # including in languages that put no spaces between words at all. Segment
    # text is display and search text, so its leading space is noise.
    segments = [fake_segment(0.0, 2.0, text=" Hallo daar.", words=[fake_word(0.0, 2.0, " Hallo")])]

    rows, words, _ = collected(segments)

    assert rows[0]["text"] == "Hallo daar."
    assert words[0]["text"] == " Hallo"


def test_a_segment_whisper_gave_no_words_contributes_none():
    rows, words, _ = collected([fake_segment(0.0, 2.0, words=None), fake_segment(2.0, 4.0)])
    rows2, words2, _ = collected([fake_segment(0.0, 2.0), fake_segment(2.0, 4.0)])

    assert len(rows) == len(rows2) == 2
    assert len(words2) == 2


def test_a_segment_with_an_empty_word_list_is_not_a_crash():
    rows, words, _ = collected([fake_segment(0.0, 2.0, words=[])])

    assert len(rows) == 1
    assert words == []


def test_silence_transcribes_to_nothing_rather_than_an_error():
    rows, words, seen = collected([])

    assert (rows, words, seen) == ([], [], [])


def test_the_numbers_that_come_out_are_plain_python_floats():
    # faster-whisper hands out numpy scalars; they behave like floats until
    # something downstream (an exporter, a JSON payload) meets one and does not.
    segment = fake_segment(
        np.float64(0.0),
        np.float64(2.0),
        words=[fake_word(np.float64(0.0), np.float64(2.0), probability=np.float64(0.5))],
    )

    rows, words, _ = collected([segment])

    assert type(rows[0]["start"]) is float
    assert type(rows[0]["end"]) is float
    assert type(words[0]["start"]) is float
    assert type(words[0]["probability"]) is float


def test_the_generator_is_consumed_lazily():
    """The property no assertion about the values can reach.

    A generator drained into a list before anyone looks at it produces exactly
    the same numbers as one consumed as it goes - and after the fact is
    precisely when a four-hour transcription's progress is worthless. So this
    generator refuses to produce its second segment until the first one's
    progress has been heard.
    """
    heard: list[float] = []

    def segments():
        yield fake_segment(0.0, 15.0)
        assert heard, "nobody heard the first segment before the second was demanded"
        yield fake_segment(15.0, 30.0)

    rows, _ = transcribe.collect_segments(
        segments(), 30.0, on_progress=heard.append, cancelled=None
    )

    assert len(rows) == 2
    assert heard == [0.5, 1.0]


def test_a_cancel_between_segments_stops_the_run():
    stop = {"now": False}

    def segments():
        yield fake_segment(0.0, 15.0)
        stop["now"] = True
        yield fake_segment(15.0, 30.0)
        pytest.fail("the transcription kept going after it was cancelled")

    with pytest.raises(transcribe.Cancelled):
        transcribe.collect_segments(
            segments(),
            30.0,
            on_progress=lambda p: None,
            cancelled=lambda: stop["now"],
        )


def test_a_cancel_before_the_first_segment_costs_no_decoding():
    def segments():
        pytest.fail("the model was asked for a segment after the job was cancelled")
        yield  # pragma: no cover

    with pytest.raises(transcribe.Cancelled):
        transcribe.collect_segments(
            segments(), 30.0, on_progress=lambda p: None, cancelled=lambda: True
        )


# --- transcribe_audio (against the stand-in model) ----------------------------


def transcribed(monkeypatch, model, **kwargs):
    install(monkeypatch, model)
    kwargs.setdefault("model_name", "tiny")
    kwargs.setdefault("on_progress", lambda p: None)
    return transcribe.transcribe_audio(Path("audio.wav"), **kwargs)


def test_whisper_is_asked_for_word_timestamps_and_vad(monkeypatch):
    model = FakeWhisper([fake_segment(0.0, 30.0)])

    transcribed(monkeypatch, model)

    options = model.calls[0]
    assert options["word_timestamps"] is True
    assert options["vad_filter"] is True
    assert options["condition_on_previous_text"] is True
    assert options["compression_ratio_threshold"] == 2.4


def test_language_task_and_hotwords_reach_the_model(monkeypatch):
    model = FakeWhisper([fake_segment(0.0, 30.0)])

    transcribed(
        monkeypatch, model, language="nl", task="translate", hotwords="WHYcast, Marieke"
    )

    options = model.calls[0]
    assert options["language"] == "nl"
    assert options["task"] == "translate"
    assert options["hotwords"] == "WHYcast, Marieke"


def test_an_empty_hotword_list_is_sent_as_no_hotwords(monkeypatch):
    model = FakeWhisper([fake_segment(0.0, 30.0)])

    transcribed(monkeypatch, model, hotwords="")

    assert model.calls[0]["hotwords"] is None


def test_transcribe_audio_reports_what_it_actually_ran(monkeypatch):
    model = FakeWhisper([fake_segment(0.0, 30.0)], info=fake_info(language="nl"))

    info, segments, words = transcribed(monkeypatch, model, model_name="tiny")

    assert info["model"] == "tiny"
    assert info["device"] == "cpu"
    assert info["compute_type"] == "int8"
    assert info["language"] == "nl"
    assert info["language_probability"] == 0.98
    assert info["duration"] == 30.0
    assert len(segments) == 1 and len(words) == 1


def test_the_model_is_let_go_of_before_the_stage_returns(monkeypatch):
    holder: dict[str, weakref.ref] = {}

    def load(name, **kw):
        model = FakeWhisper([fake_segment(0.0, 30.0)])
        holder["ref"] = weakref.ref(model)
        return model, "cpu", "int8"

    monkeypatch.setattr(transcribe, "load_model", load)

    transcribe.transcribe_audio(
        Path("audio.wav"), model_name="tiny", on_progress=lambda p: None
    )
    gc.collect()

    assert holder["ref"]() is None


def test_the_model_is_let_go_of_even_while_the_cancel_is_still_being_reported(
    monkeypatch,
):
    """The one that matters, and the one that fails without an explicit close.

    A raised `Cancelled` carries a traceback, and that traceback pins every
    frame between the raise and the caller - including the frame holding the
    half-consumed generator, which holds the model, which holds the VRAM the
    next stage is about to ask for. Anything that keeps the exception around
    keeps all of that with it: a logger, an error report, a supervisor that has
    not written its verdict yet. So `caught` is deliberately still in scope at
    the assertion, because that is the shape of the real failure. Refcounting
    alone would clean this up the moment nobody held the exception, and then
    the test would prove nothing.
    """
    holder: dict[str, weakref.ref] = {}
    heard: list[float] = []

    def load(name, **kw):
        model = FakeWhisper([fake_segment(0.0, 15.0), fake_segment(15.0, 30.0)])
        holder["ref"] = weakref.ref(model)
        return model, "cpu", "int8"

    monkeypatch.setattr(transcribe, "load_model", load)

    with pytest.raises(transcribe.Cancelled) as caught:
        transcribe.transcribe_audio(
            Path("audio.wav"),
            model_name="tiny",
            on_progress=heard.append,
            # Cancel once the first segment is in, so the generator is left
            # suspended mid-stream - which is where the reference lives.
            cancelled=lambda: bool(heard),
        )
    gc.collect()

    assert caught.value.args  # the exception is still held, as a logger holds it
    assert holder["ref"]() is None


def test_the_abandoned_generator_is_closed_not_dropped(monkeypatch):
    # The same property from the other side: dropping the last reference and
    # letting the collector finalise the generator is not the same as closing
    # it, because on the cancel path nobody has dropped it yet.
    model = FakeWhisper([fake_segment(0.0, 15.0), fake_segment(15.0, 30.0)])
    install(monkeypatch, model)
    heard: list[float] = []

    with pytest.raises(transcribe.Cancelled) as caught:
        transcribe.transcribe_audio(
            Path("audio.wav"),
            model_name="tiny",
            on_progress=heard.append,
            cancelled=lambda: bool(heard),
        )

    assert caught.value.args
    assert model.closed


# --- one real transcription ---------------------------------------------------


@pytest.fixture(scope="session")
def clip_transcription():
    """The fixture clip, transcribed once on CPU with `tiny`, for the session.

    Thirteen seconds of CPU is worth paying once and not four times. `tiny` is
    the plan's CPU model; nothing here depends on it hearing the words right.
    """
    progress: list[float] = []
    info, segments, words = transcribe.transcribe_audio(
        CLIP,
        model_name="tiny",
        language=None,
        task="transcribe",
        hotwords=None,
        on_progress=progress.append,
        cancelled=lambda: False,
        device="cpu",
        compute_type="int8",
    )
    return info, segments, words, progress


def test_the_clip_yields_words_with_usable_timestamps(clip_transcription):
    _, _, words, _ = clip_transcription

    assert len(words) > 10
    assert [w["start"] for w in words] == sorted(w["start"] for w in words)
    assert all(w["end"] >= w["start"] for w in words)
    assert all(0.0 <= w["start"] <= 30.5 for w in words)


def test_every_word_carries_a_probability(clip_transcription):
    _, _, words, _ = clip_transcription

    assert all(isinstance(w["probability"], float) for w in words)
    assert all(0.0 <= w["probability"] <= 1.0 for w in words)


def test_the_progress_bar_is_non_decreasing_and_nearly_finished(clip_transcription):
    _, _, _, progress = clip_transcription

    assert progress == sorted(progress)
    assert progress[-1] >= 0.8
    assert all(0.0 <= p <= 1.0 for p in progress)


def test_the_real_segments_carry_the_confidence_columns(clip_transcription):
    _, segments, _, _ = clip_transcription

    assert segments
    for segment in segments:
        assert segment["avg_logprob"] < 0
        assert 0.0 <= segment["no_speech_prob"] <= 1.0
        assert segment["compression_ratio"] > 0


def test_the_words_reconstruct_the_segment_text(clip_transcription):
    _, segments, words, _ = clip_transcription

    first = "".join(w["text"] for w in words if w["segment_idx"] == 0).strip()
    assert first == segments[0]["text"]


# --- the stage ----------------------------------------------------------------


def stage_ctx(conn, job_id, monkeypatch, model=None, progress=None, cancelled=None):
    install(monkeypatch, model or FakeWhisper([fake_segment(0.0, 30.0)]))
    return make_ctx(
        conn,
        job_id,
        state={"wav": Path("audio.wav")},
        progress=progress,
        cancelled=cancelled,
    )


def test_the_stage_creates_a_run_row_for_this_media(conn, data_dir, monkeypatch):
    row, job_id = a_job_with_media(conn)

    transcribe.run(stage_ctx(conn, job_id, monkeypatch))

    runs = conn.execute("SELECT * FROM run").fetchall()
    assert len(runs) == 1
    assert runs[0]["media_id"] == row["id"]
    assert runs[0]["engine"] == "faster-whisper"
    assert runs[0]["model"] == transcribe.DEFAULT_MODEL
    assert runs[0]["compute_type"] == "int8"
    assert runs[0]["task"] == "transcribe"
    assert runs[0]["language"] == "nl"
    assert runs[0]["is_current"] == 0  # finalize decides which run is current


def test_the_stage_records_the_model_substitution_on_the_run(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn, params={"task": "translate"})

    transcribe.run(stage_ctx(conn, job_id, monkeypatch))

    run_row = conn.execute("SELECT * FROM run").fetchone()
    assert run_row["model"] == "large-v3"
    params = json.loads(run_row["params_json"])
    assert params["requested_model"] == transcribe.DEFAULT_MODEL
    assert params["substitution"] == "turbo cannot translate; using large-v3"


def test_the_stage_persists_every_segment_and_every_word(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)
    model = FakeWhisper(
        [
            fake_segment(0.0, 2.0, words=[fake_word(0.0, 1.0), fake_word(1.0, 2.0)]),
            fake_segment(2.0, 4.0, words=[fake_word(2.0, 4.0)]),
        ]
    )

    transcribe.run(stage_ctx(conn, job_id, monkeypatch, model=model))

    assert conn.execute("SELECT COUNT(*) FROM segment").fetchone()[0] == 2
    words = conn.execute("SELECT * FROM word ORDER BY idx").fetchall()
    assert [w["idx"] for w in words] == [0, 1, 2]
    assert all(w["speaker"] is None for w in words)  # attribute fills these in


def test_the_persisted_segments_are_searchable(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)
    model = FakeWhisper([fake_segment(0.0, 2.0, text=" Vogon poetry is the third worst.")])

    transcribe.run(stage_ctx(conn, job_id, monkeypatch, model=model))

    hits = conn.execute("SELECT rowid FROM segment_fts WHERE segment_fts MATCH 'Vogon'").fetchall()
    assert len(hits) == 1


def test_the_stage_points_the_job_at_its_run(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)

    transcribe.run(stage_ctx(conn, job_id, monkeypatch))

    run_id = conn.execute("SELECT id FROM run").fetchone()["id"]
    assert conn.execute("SELECT run_id FROM job WHERE id=?", (job_id,)).fetchone()[0] == run_id


def test_the_stage_hands_the_next_stages_what_they_need(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)
    ctx = stage_ctx(conn, job_id, monkeypatch)

    transcribe.run(ctx)

    assert ctx.state["run_id"] == conn.execute("SELECT id FROM run").fetchone()["id"]
    assert ctx.state["model"] == transcribe.DEFAULT_MODEL
    assert [w["idx"] for w in ctx.state["words"]] == [0]


def test_the_stage_announces_what_it_ran(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)

    transcribe.run(stage_ctx(conn, job_id, monkeypatch))

    events = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "transcribe"]
    assert len(events) == 1
    assert events[0]["payload"]["model"] == transcribe.DEFAULT_MODEL
    assert events[0]["payload"]["language"] == "nl"
    assert events[0]["payload"]["n_words"] == 1


def test_the_stage_finishes_at_one(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)
    progress: list[float] = []

    transcribe.run(stage_ctx(conn, job_id, monkeypatch, progress=progress))

    assert progress[-1] == 1.0


def test_the_stage_uses_the_glossary_of_this_library(conn, data_dir, monkeypatch):
    glossary(conn, ("WHYcast", 1.0))
    _, job_id = a_job_with_media(conn)
    model = FakeWhisper([fake_segment(0.0, 30.0)])

    transcribe.run(stage_ctx(conn, job_id, monkeypatch, model=model))

    assert "WHYcast" in model.calls[0]["hotwords"]


def test_the_stage_without_a_prepared_wav_refuses_to_guess(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)
    install(monkeypatch, FakeWhisper([]))

    with pytest.raises(RuntimeError):
        transcribe.run(make_ctx(conn, job_id))


def test_a_cancelled_stage_writes_no_half_run(conn, data_dir, monkeypatch):
    _, job_id = a_job_with_media(conn)
    ctx = stage_ctx(
        conn,
        job_id,
        monkeypatch,
        model=FakeWhisper([fake_segment(0.0, 15.0), fake_segment(15.0, 30.0)]),
        cancelled=lambda: True,
    )

    with pytest.raises(transcribe.Cancelled):
        transcribe.run(ctx)

    assert conn.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 0


def test_a_second_opinion_is_on_the_record_of_the_run(conn, data_dir, monkeypatch):
    """TASK-032. Whatever a second opinion decided about a stretch - here the
    fake says the same loop every time it is asked, so the stored text stays -
    the run's params say which stretch was looked at, why, and what became of
    it, and the job's events say so while it runs."""
    _, job_id = a_job_with_media(conn)
    loop = [fake_word(10.0 + i * 0.01, 10.01 + i * 0.01, " we") for i in range(8)]
    model = FakeWhisper([
        fake_segment(0.0, 5.0, text=" hello there."),
        fake_segment(9.0, 20.0, text=" we we we we we we we we", words=loop),
        fake_segment(22.0, 28.0, text=" goodbye."),
    ])
    install(monkeypatch, model)

    transcribe.run(make_ctx(conn, job_id, state={"wav": CLIP}))

    params = json.loads(conn.execute("SELECT params_json FROM run").fetchone()["params_json"])
    (record,) = params["second_opinions"]
    assert (record["flag"], record["phrase"], record["repeats"]) == ("loop", "we", 8)
    assert record["outcome"].startswith("kept")
    assert record["before"] == "we we we we we we we we"
    assert len(model.calls) == 2  # the window, and one opinion that heard the loop too
    (event,) = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "second-opinion"]
    assert event["payload"]["records"] == params["second_opinions"]


# --- runner wiring ------------------------------------------------------------


def test_transcribe_follows_prepare_in_the_registry():
    names = [name for name, _ in runner.STAGES["transcribe"]]
    assert names[:4] == ["probe", "prepare", "proxy", "transcribe"]
    assert runner.STAGES["transcribe"][3][1] is transcribe.run


def test_a_cancelled_transcription_is_a_cancelled_job_not_a_failed_one(conn, monkeypatch):
    def stop(ctx):
        raise transcribe.Cancelled("cancel requested after 2 segments")

    monkeypatch.setitem(runner.STAGES, "transcribe", [("transcribe", stop)])
    job_id = jobs.enqueue(conn, "transcribe")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 2

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "cancelled"
    assert row["error_code"] is None
    assert row["finished_at"] is not None


# --- a speech model is not a chat model -------------------------------------------


def test_the_known_tiers_and_a_faster_whisper_repo_are_speech_models():
    for name in ("large-v3-turbo", "large-v3", "small", "distil-large-v3.5", "turbo"):
        assert transcribe.is_speech_model(name), name
    for repo in (
        "Systran/faster-whisper-large-v3",
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "distil-whisper/distil-large-v3.5-ct2",  # 'whisper' is in the owner, not the name
        "deepdml/faster-whisper-large-v3-turbo-ct2",
    ):
        assert transcribe.is_speech_model(repo), repo


def test_a_chat_model_is_not_a_speech_model():
    """The one that got here: the AI panel's OpenRouter model reached the
    transcribe stage on 2026-09-03 and was sent to HuggingFace as if it were a
    checkpoint, which answered 404. A chat model and a speech model are not
    the same kind of thing and the stage now says so before the network does."""
    for name in (
        "openai/gpt-5.6-luna",
        "anthropic/claude-sonnet-5",
        "qwen3.5:4b",  # an Ollama tag
        "gpt-4o-mini",
    ):
        assert not transcribe.is_speech_model(name), name


def test_the_alias_table_matches_the_installed_faster_whisper():
    """Copied rather than imported, like LANGUAGE_CHOICES and for the same
    reason (ADR-001: the web process never loads faster_whisper). Copies drift,
    so this is the test that notices."""
    from faster_whisper.utils import available_models

    assert transcribe.SPEECH_MODEL_ALIASES == frozenset(available_models())


def test_ensure_speech_model_returns_the_name_or_says_what_is_wrong():
    assert transcribe.ensure_speech_model("large-v3-turbo") == "large-v3-turbo"

    with pytest.raises(transcribe.NotASpeechModel) as exc:
        transcribe.ensure_speech_model("openai/gpt-5.6-luna")

    message = str(exc.value)
    assert "openai/gpt-5.6-luna" in message  # which name was wrong
    assert "large-v3-turbo" in message  # and what a right one looks like


def test_a_local_directory_is_taken_at_its_word(tmp_path):
    """A converted checkpoint on disk can be called anything at all."""
    local = tmp_path / "my-own-conversion"
    local.mkdir()

    assert transcribe.is_speech_model(str(local))


# --- the live text ----------------------------------------------------------------------


def _log_events(conn, job_id):
    rows = conn.execute(
        "SELECT payload_json FROM job_event WHERE job_id=? AND kind='log' ORDER BY seq", (job_id,)
    ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def test_the_stage_leaves_the_text_as_log_events_while_it_decodes(conn, data_dir, monkeypatch):
    """What the job page shows while the words are still being written: one
    `log` event per batch, carrying the time and the text."""
    segments = [fake_segment(i * 2.0, i * 2.0 + 1.5, text=f" sentence {i}.") for i in range(30)]
    row, job_id = a_job_with_media(conn)

    transcribe.run(stage_ctx(conn, job_id, monkeypatch, model=FakeWhisper(segments)))

    events = _log_events(conn, job_id)
    assert events, "no live text was emitted"
    assert events[0]["at"] == 0.0
    assert events[0]["text"].startswith("sentence 0. sentence 1.")
    assert " ".join(e["text"] for e in events) == " ".join(f"sentence {i}." for i in range(30))
    # Batched, not one row per segment.
    assert len(events) <= 30 // transcribe.LIVE_TEXT_SEGMENTS + 1


def test_live_text_flushes_on_the_segment_cap_on_the_clock_and_at_the_end(conn, data_dir):
    row, job_id = a_job_with_media(conn)
    now = [0.0]
    live = transcribe.LiveText(make_ctx(conn, job_id), clock=lambda: now[0])

    for i in range(transcribe.LIVE_TEXT_SEGMENTS - 1):
        live.add({"start": float(i), "end": i + 0.5, "text": f"s{i}"})
    assert _log_events(conn, job_id) == []
    live.add({"start": 11.0, "end": 11.5, "text": "s11"})  # the cap
    assert len(_log_events(conn, job_id)) == 1

    live.add({"start": 12.0, "end": 12.5, "text": "later"})
    now[0] = transcribe.LIVE_TEXT_SECONDS + 1  # the clock
    live.add({"start": 13.0, "end": 13.5, "text": "and later"})
    events = _log_events(conn, job_id)
    assert len(events) == 2
    assert events[1] == {"at": 12.0, "until": 13.5, "text": "later and later"}

    live.add({"start": 14.0, "end": 14.5, "text": "   "})  # nothing to show
    live.add({"start": 15.0, "end": 15.5, "text": "last"})
    live.flush()  # the end of the run
    assert _log_events(conn, job_id)[-1]["text"] == "last"
    live.flush()  # nothing pending: no empty row
    assert len(_log_events(conn, job_id)) == 3


# --- the copy setup downloaded is the copy that loads (TASK-089.16) ---------------


def one_entry(monkeypatch, tmp_path, alias, backends):
    """A one-row catalogue pinning a four-byte file under ``alias``.

    The real pin is 1.6 GB and this test is about which path reaches the
    loader; tests/test_models.py pins the alias against faster-whisper's own
    table, so the row here standing in for it cannot drift.
    """
    from scribe import models

    body = b"ct2!"
    entry = models.Model(
        repo="demo/weights",
        revision="a" * 40,
        files={"model.bin": {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}},
        license="mit",
        credit="Demo, MIT.",
        gated=False,
        backends=backends,
        tier=models.DEFAULT_TIER,
        alias=alias,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {entry.repo: entry})
    monkeypatch.setattr(models, "root", lambda: tmp_path)
    return entry, body


def _fake_whisper(monkeypatch):
    """faster-whisper's constructor, recorded rather than run."""
    import faster_whisper

    from scribe import cuda_setup

    opened: list = []

    class Fake:
        def __init__(self, name_or_directory, **_kwargs):
            opened.append(name_or_directory)

    monkeypatch.setattr(cuda_setup, "ensure_cuda_libs", lambda: None)
    monkeypatch.setattr(faster_whisper, "WhisperModel", Fake)
    return opened


def test_the_weights_setup_downloaded_are_the_weights_that_load(tmp_path, monkeypatch):
    """A machine that watched a progress bar during setup used to watch nothing
    at all while the first job downloaded the same weights again: the name was
    handed to faster-whisper, which resolves it through the Hub."""
    opened = _fake_whisper(monkeypatch)
    entry, body = one_entry(monkeypatch, tmp_path, transcribe.DEFAULT_MODEL, ("cuda", "cpu"))
    folder = tmp_path / entry.folder
    folder.mkdir(parents=True)
    (folder / "model.bin").write_bytes(body)

    transcribe.load_model(transcribe.DEFAULT_MODEL, device="cpu")

    assert opened == [str(folder)]


def test_a_folder_that_is_not_whole_is_not_used(tmp_path, monkeypatch):
    """faster-whisper would open it and fail, where the bare name still
    resolves through the hub cache."""
    opened = _fake_whisper(monkeypatch)
    entry, _body = one_entry(monkeypatch, tmp_path, transcribe.DEFAULT_MODEL, ("cuda", "cpu"))
    (tmp_path / entry.folder).mkdir(parents=True)

    transcribe.load_model(transcribe.DEFAULT_MODEL, device="cpu")

    assert opened == [transcribe.DEFAULT_MODEL]


def test_another_model_name_is_handed_over_as_before(tmp_path, monkeypatch):
    """Nothing in the catalogue serves it, so it resolves the way it always
    did - the hidden CPU fallback and anybody's own checkpoint included."""
    opened = _fake_whisper(monkeypatch)
    one_entry(monkeypatch, tmp_path, transcribe.DEFAULT_MODEL, ("cuda", "cpu"))

    transcribe.load_model("tiny", device="cpu")

    assert opened == ["tiny"]
