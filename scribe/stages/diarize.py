"""Who was talking, and when.

Whisper hears words; pyannote hears people. This stage runs the second half and
hands `attribute` a list of turns to join the words onto. It also keeps the mean
embedding of every cluster, because pyannote's `SPEAKER_00`, `SPEAKER_01` labels
are arbitrary per run: without a vector to match against, re-diarizing a file
scrambles every name the user typed (spec section 8, named there as a known hard
risk).

Five things this module is careful about.

**pyannote is handed samples, never a path** - and this is a deliberate,
documented deviation from the plan's global constraint that says the opposite.
On this machine `import torchcodec` dies with `[WinError 127]`: torchcodec
0.16.0 links against libav* symbols that FFmpeg 8.1 no longer exports, and
torchaudio 2.8 dispatches through it, so `torchaudio.load` fails too. pyannote
therefore cannot open a file here at all, and its own error message says the
supported alternative is a preloaded ``{"waveform": tensor, "sample_rate": int}``
dictionary. The cost is real and worth stating: the samples sit in RAM for the
length of the stage, 230 MB per hour of audio at 16 kHz float32. That is what
the constraint exists to prevent, so the waveform is released the moment
diarization returns rather than at the end of the job. Transcription, which is
the stage that would actually meet a four-hour file, still streams.

**The progress is measured where it can be and ordered where it cannot.**
pyannote calls its hook with `completed`/`total` inside its long steps, and
those fractions are real. The width of each step's band is not a measurement -
it is a fixed ordering prior, and it is written down as one rather than dressed
up as knowledge. What the bar cannot do is go backwards or invent a position
for a step name this version of pyannote did not have.

**The weights come from here first, the hub second.** The spec wants no Hugging
Face token dance, so a pipeline directory in `MODELS_DIR/pyannote` wins over the
gated repository every time. When neither works the error says which two places
were tried and what to do about it, because "returned None" is what pyannote
offers otherwise - it does not raise for a gated repo, it returns None and
prints a hint to stdout that nobody is reading.

**And when none of them works, the job keeps its transcript.** Every route
into this stage is gated - measured 2026-09-22, an unauthenticated HEAD on
`pyannote/segmentation-3.0`, which the assembled 3.1 fallback is built from,
answers 401 - so a machine without a token has none at all. That used to fail
the job in its fifth stage, an hour of audio after the words were written.
`WeightsUnavailable` is caught here instead, the run carries a note saying
what is missing and the three ways to fix it, and nothing else is caught:
a broken wav or a card out of memory still fails, because a job reporting
"done" for one of those would be lying about the transcript.

**The pipeline is gone before the next stage loads one.** Three gigabytes of
pyannote and six of Whisper do not both fit; the release happens in a `finally`
so the failure path - which is where a leak actually costs you - frees the card
too.
"""

from __future__ import annotations

import array
import gc
import json
import os
import sqlite3
import traceback
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

from scribe import accel, applog, credentials, cuda_setup, db, jobs, paths
from scribe.stages.attribute import Turn, turns_from_diarization

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

DEFAULT_PIPELINE = "pyannote/speaker-diarization-community-1"
"""The spec's pipeline. Gated on Hugging Face, which is exactly why
`MODELS_DIR/pyannote` is tried first."""

WEIGHTS_SUBDIR = "pyannote"
"""Where a re-hosted copy of the pipeline lives under MODELS_DIR."""

SETTING_TOKEN = credentials.HUGGINGFACE.setting_key
"""The settings key holding a Hugging Face token, when one is needed at all.
Named there because the resolver has to read the same row this stage does."""

# What runs when the shipped default is gated for this token. Measured here on
# 2026-09-02: both pipeline configs (community-1 and 3.1) answer 403 for a
# token that lacks gated-repo access, while the two checkpoints 3.1 is made of
# load from the cache in under a second. So the fallback is 3.1 assembled by
# hand - the same segmentation and embedding models, the same clustering, the
# same hyperparameters lifted from 3.1's own config.yaml - and it is named as
# such on the run, the way a translate job names its model substitution.
FALLBACK_SEGMENTATION = "pyannote/segmentation-3.0"
FALLBACK_EMBEDDING = "pyannote/wespeaker-voxceleb-resnet34-LM"
FALLBACK_SOURCE = "assembled:speaker-diarization-3.1"
FALLBACK_NOTE = (
    f"{DEFAULT_PIPELINE} is gated for this token; diarized with the "
    "speaker-diarization-3.1 pipeline assembled from segmentation-3.0 and "
    "wespeaker-voxceleb-resnet34-LM. Accept the model conditions on hf.co to "
    "use community-1 (measurably better on overlapping speech)."
)
FALLBACK_PARAMS = {
    "segmentation": {"min_duration_off": 0.0},
    "clustering": {
        "method": "centroid",
        "min_cluster_size": 12,
        "threshold": 0.7045654963945799,
    },
}

# Where each of pyannote's steps sits on the 0..1 bar. The ORDER is fact, read
# off the hook call sites in pyannote/audio/pipelines/speaker_diarization.py.
# The WIDTHS are a prior, not a measurement: embeddings get the largest share
# because they run a second network over every speaker chunk, but nothing here
# has been timed. Revisit once stage_perf has samples - and until then do not
# read a band edge as a claim about how long anything takes.
STEP_BANDS: dict[str, tuple[float, float]] = {
    "segmentation": (0.00, 0.40),
    "speaker_counting": (0.40, 0.45),
    "embeddings": (0.45, 0.90),
    "discrete_diarization": (0.90, 1.00),
}

# prepare writes pcm_s16le and nothing else, so this is the only width read.
SAMPLE_WIDTH_BYTES = 2
_FULL_SCALE = float(1 << 15)

# Wav files are little-endian; `array` is native. Equal on every machine this
# runs on - checked rather than assumed, because being wrong about it is silent
# and sounds like static.
_BIG_ENDIAN = array.array("h", b"\x01\x00")[0] != 1

# Frames per read in load_waveform. A megabyte-ish of int16 at a time: small
# enough that the temporary never matters, large enough that a four-hour file
# is a few thousand reads rather than a few million.
_READ_FRAMES = 1 << 19


class WeightsUnavailable(Exception):
    """No diarization weights could be loaded, and here is what to do."""


# --- the progress hook ---------------------------------------------------------


class ProgressHook:
    """pyannote's hook signature, mapped onto one 0..1 fraction.

    pyannote calls ``hook(step_name, artifact, file=..., total=..., completed=...)``
    - repeatedly with a counter during a long step, then once more without one
    when the step's artifact is ready. That second shape is not "no
    information": pyannote's own ProgressHook reads `completed is None` as
    `completed = total = 1`, and so does this one. Reading it as "ignore" is
    what leaves a bar frozen at 45% through the whole clustering pass.

    Unknown step names report nothing. A name this version of pyannote did not
    have is not a position in the file, and guessing one would be exactly the
    invented progress the plan forbids.

    It is a context manager so it can be dropped into pyannote's ``Hooks()``
    beside ArtifactHook and TimingHook without a wrapper.
    """

    def __init__(self, on_progress: Callable[[float], None]) -> None:
        self._on_progress = on_progress
        # Below zero so a genuine 0.0 at the start is still worth saying once.
        self._highest = -1.0

    def __enter__(self) -> "ProgressHook":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def __call__(
        self,
        step_name: str,
        step_artifact: Any = None,
        file: Any = None,
        total: int | None = None,
        completed: int | None = None,
    ) -> None:
        band = STEP_BANDS.get(step_name)
        if band is None:
            return

        low, high = band
        if completed is None:
            within = 1.0  # the step is finished, which is what None means here
        elif not total or total <= 0:
            # A counter with no denominator. Not a finish - the step has said
            # it started and nothing more - so it reports the floor of its
            # band. Sharing the branch above would announce a completed
            # segmentation pass before a single frame had been through the
            # network. pyannote 4.0.7 always passes a positive total, so this
            # is defence against a future version rather than a live case.
            within = 0.0
        else:
            within = min(1.0, max(0.0, float(completed) / float(total)))

        value = low + (high - low) * within
        if value <= self._highest:
            return  # a bar that retreats is worse than one that pauses
        self._highest = value
        self._on_progress(value)


# --- the samples ----------------------------------------------------------------


def load_waveform(wav: str | Path) -> tuple[Any, int]:
    """Read a prepared wav into a ``(channel, time)`` float32 torch tensor.

    Stdlib :mod:`wave` rather than torchaudio, and that is the whole point of
    this function: torchaudio 2.8 decodes through torchcodec, and torchcodec
    0.16.0 will not even import on this machine. prepare has already produced
    exactly one format - mono 16 kHz pcm_s16le - so reading it is a fixed-width
    unpack, not a decoder.

    Read in chunks straight into the float32 buffer that is returned. The
    obvious `frombuffer(readframes(all)).astype(float32)` holds the int16 copy
    and the float32 copy at once, which for a long recording is half a gigabyte
    of peak that buys nothing.
    """
    import torch

    with wave.open(str(wav), "rb") as source:
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        frames = source.getnframes()
        if source.getsampwidth() != SAMPLE_WIDTH_BYTES:
            raise ValueError(
                f"{Path(wav).name} is not 16-bit PCM "
                f"({source.getsampwidth() * 8}-bit); the prepare stage writes "
                "pcm_s16le and reading anything else as int16 would hand "
                "pyannote noise that looks like audio"
            )

        total = frames * channels
        samples = torch.empty(total, dtype=torch.float32)
        offset = 0
        while offset < total:
            block = source.readframes(_READ_FRAMES)
            if not block:
                break  # a header that over-promised; trust the bytes
            values = array.array("h")
            values.frombytes(block)
            if _BIG_ENDIAN:  # pragma: no cover - no such machine here
                values.byteswap()
            chunk = torch.frombuffer(values, dtype=torch.int16)
            samples[offset : offset + len(chunk)] = chunk  # widened on the way in
            offset += len(chunk)

    samples = samples[:offset].div_(_FULL_SCALE)  # in place; see the docstring
    # (channel, time), which pyannote insists on and complains about only from
    # deep inside Audio.validate_file, where the message names none of this.
    # Mono reshapes for free; a transpose would copy the whole recording again.
    if channels == 1:
        return samples.reshape(1, -1), sample_rate
    return samples.reshape(-1, channels).T.contiguous(), sample_rate


# --- the weights ----------------------------------------------------------------


def local_weights_dir() -> Path:
    """Where a re-hosted pipeline lives; read fresh so tests can move it."""
    return paths.MODELS_DIR / WEIGHTS_SUBDIR


def hf_token(conn: sqlite3.Connection | None = None) -> str | None:
    """The Hugging Face token, from wherever this machine keeps it.

    Every place it may live is `scribe.credentials` (TASK-089.04): the
    settings row, the environment, `.env`, the Windows registry and Hugging
    Face's own login file. It used to be this function's own two-line lookup,
    and the doctor, `python -m scribe.models` and the settings page each had a
    different one - so a token saved in Settings was invisible to the very
    command the error message told the user to run.

    None rather than "" when there is none: an empty string is a token as far
    as Hugging Face is concerned, and it answers 401 instead of serving the
    public copy anonymously.
    """
    return credentials.resolve(conn, credentials.HUGGINGFACE).value


def open_pipeline(source: str | Path, *, device: str, token: str | None) -> Any | None:
    """Load one candidate onto `device`; None when it is simply not there.

    The single seam between this module and pyannote, for the same reason
    `transcribe.load_model` is one: everything around a model can then be
    tested without loading one.

    `from_pretrained` returning None instead of raising is pyannote's actual
    behaviour for a missing, private or gated repository - it catches the
    RepositoryNotFoundError itself and prints a hint to stdout. A caller that
    only handled exceptions would sail straight past the most common failure.
    """
    # pyannote 4 ships opentelemetry metrics switched on, pointed at
    # otel.pyannote.ai. This is a local-first transcriber; it does not phone
    # anybody about the files it is given. setdefault, so an operator who wants
    # to send them can still say so in the environment.
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
    # Apple Silicon: a handful of pyannote's ops have no Metal kernel, and
    # without this PyTorch raises on the first one instead of running it on
    # the CPU. Read at import, so it has to be set before torch is.
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    cuda_setup.ensure_cuda_libs()
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(str(source), token=token)
    if pipeline is None:
        return None
    return pipeline.to(torch.device(device))


def assemble_fallback_pipeline(*, device: str, token: str | None) -> Any:
    """speaker-diarization-3.1, built from its two checkpoints instead of its config.

    A pipeline built in Python is not instantiated, and the defaults pyannote
    would fill in are VBx hyperparameters that mean nothing to agglomerative
    clustering - hence the explicit `instantiate(FALLBACK_PARAMS)`. The PLDA
    stand-in satisfies the constructor's signature and is never read: it only
    matters to the VBx clustering this pipeline does not use.
    """
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")
    cuda_setup.ensure_cuda_libs()
    import torch
    from pyannote.audio.core.plda import PLDA
    from pyannote.audio.pipelines import SpeakerDiarization

    pipeline = SpeakerDiarization(
        segmentation=FALLBACK_SEGMENTATION,
        embedding=FALLBACK_EMBEDDING,
        clustering="AgglomerativeClustering",
        segmentation_batch_size=32,
        embedding_batch_size=32,
        embedding_exclude_overlap=True,
        plda=object.__new__(PLDA),
        token=token,
    )
    pipeline.instantiate(FALLBACK_PARAMS)
    return pipeline.to(torch.device(device))


def load_pipeline_with_source(
    model_dir_or_id: str | Path | None = None,
    device: str | None = None,
    *,
    token: str | None = None,
) -> tuple[Any, str]:
    """The diarization pipeline plus the name of what actually loaded.

    With no `model_dir_or_id`, `MODELS_DIR/pyannote` is tried first, the hub
    default second, and the assembled 3.1 fallback last - so a token without
    gated-repo access still diarizes, and the run says with what. An explicit
    source is tried alone: someone who names a pipeline wants that pipeline,
    not a silent fallback to a different one. That someone is Python code - a
    test, a script - and never a job: the stage passes no source (TASK-034),
    because a pipeline source is code pyannote will run.

    Raises :class:`WeightsUnavailable` listing every place that was tried and
    what it said, because the failures look identical from the outside and
    have completely different fixes.
    """
    device = resolve_device(device)
    reasons: list[str] = []

    if model_dir_or_id is not None:
        sources: list[str | Path] = [model_dir_or_id]
    else:
        sources = []
        local = local_weights_dir()
        if local.is_dir():
            sources.append(local)
        else:
            # Not tried on purpose: a path that is not a directory is treated
            # as a hub id by pyannote, which turns a typo into a download.
            reasons.append(f"{local}: no local pipeline directory")
        sources.append(DEFAULT_PIPELINE)

    for source in sources:
        try:
            pipeline = open_pipeline(source, device=device, token=token)
        except Exception as exc:  # noqa: BLE001 - every failure is one hint line
            reasons.append(f"{source}: {type(exc).__name__}: {exc}")
            continue
        if pipeline is None:
            reasons.append(f"{source}: missing, private or gated")
            continue
        return pipeline, str(source)

    if model_dir_or_id is None:
        try:
            return assemble_fallback_pipeline(device=device, token=token), FALLBACK_SOURCE
        except Exception as exc:  # noqa: BLE001 - the last hint line
            reasons.append(f"{FALLBACK_SOURCE}: {type(exc).__name__}: {exc}")

    raise WeightsUnavailable(_no_weights_hint(reasons))


def load_pipeline(
    model_dir_or_id: str | Path | None = None,
    device: str | None = None,
    *,
    token: str | None = None,
) -> Any:
    """The diarization pipeline alone; see :func:`load_pipeline_with_source`."""
    return load_pipeline_with_source(model_dir_or_id, device, token=token)[0]


def _no_weights_hint(reasons: Sequence[str]) -> str:
    tried = "\n".join(f"  - {reason}" for reason in reasons)
    return (
        "No speaker diarization weights could be loaded.\n"
        f"Tried:\n{tried}\n"
        f"Fix it either way round: accept the conditions at "
        f"https://hf.co/{DEFAULT_PIPELINE} and store the token as the "
        f"'{SETTING_TOKEN}' setting (or in HF_TOKEN), or put a pipeline "
        f"directory - a config.yaml and the checkpoints it names - at "
        f"{local_weights_dir()}."
    )


def resolve_device(device: str | None) -> str:
    """The device to run on; an explicit one is never second-guessed.

    Overridable so a CPU test stays a CPU test on a machine with a perfectly
    good card sitting right there - and so asking for one costs no torch import.
    """
    if device:
        return device
    return accel.diarization_device()


# --- the embeddings ---------------------------------------------------------------


def encode_embedding(vector: Iterable[float]) -> bytes:
    """One cluster's mean embedding as a blob.

    Little-endian float32, explicitly, so the bytes mean the same thing on the
    machine that reads them back and `len(blob) // 4` recovers the dimension
    without a second column to keep in step.
    """
    packed = array.array("f", [float(value) for value in vector])
    if _BIG_ENDIAN:  # pragma: no cover - no such machine here
        packed.byteswap()
    return packed.tobytes()


def decode_embedding(blob: bytes) -> list[float]:
    """The inverse of :func:`encode_embedding`."""
    values = array.array("f")
    values.frombytes(bytes(blob))
    if _BIG_ENDIAN:  # pragma: no cover - no such machine here
        values.byteswap()
    return list(values)


def embeddings_by_label(annotation: Any, embeddings: Any) -> dict[str, bytes]:
    """Pair pyannote's centroid rows with the labels they belong to.

    pyannote returns `(num_speakers, dimension)` rows explicitly re-ordered to
    match `annotation.labels()`, so zipping them is the documented contract and
    not a guess. Two rows are dropped rather than stored: an all-zero one, which
    is the padding pyannote adds when it has more labels than centroids, and a
    non-finite one. Both would sit in the database looking like evidence about a
    speaker, and a cosine match against either means nothing.
    """
    if embeddings is None:
        return {}
    rows = [list(row) for row in embeddings]
    out: dict[str, bytes] = {}
    for label, vector in zip(annotation.labels(), rows):
        if not _is_usable(vector):
            continue
        out[str(label)] = encode_embedding(vector)
    return out


def _is_usable(vector: Sequence[float]) -> bool:
    total = 0.0
    for value in vector:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return False
        total += abs(number)
    return total > 0.0


# --- diarizing ---------------------------------------------------------------------


def diarize(
    wav: str | Path,
    *,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    on_progress: Callable[[float], None],
    device: str | None = None,
    model_dir_or_id: str | Path | None = None,
    token: str | None = None,
    source_out: dict | None = None,
) -> tuple[list[Turn], dict[str, bytes]]:
    """Diarize one prepared wav; returns (turns, embedding per cluster).

    The speaker hints are pyannote's own: `num_speakers` fixes the count,
    `min_speakers`/`max_speakers` bound it, and all three default to None,
    which is pyannote's "decide for yourself".

    `source_out`, when given, receives `{"source": <what loaded>}` so the
    caller can record a fallback on the run without this function handing the
    pipeline object out - the object must not outlive this frame (see the
    `finally` below), and the return shape stays (turns, embeddings).

    Set a maximum generously if you set one at all. Guessing too high costs a
    cosmetic split - one person under two labels, which a rename can merge.
    Guessing too low puts two people under one label, and nothing downstream
    can undo that: one label is one name, in the database and in the editor.
    """
    if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
        # Checked here rather than left to pyannote, which raises from inside
        # the clustering with a message that names neither parameter - and only
        # after the weights have been downloaded and moved onto the card.
        raise ValueError(
            f"min_speakers ({min_speakers}) is greater than max_speakers ({max_speakers})"
        )

    device = resolve_device(device)
    pipeline, source = load_pipeline_with_source(model_dir_or_id, device, token=token)
    if source_out is not None:
        source_out["source"] = source
    waveform = None
    try:
        waveform, sample_rate = load_waveform(wav)
        output = pipeline(
            # Samples, not a path: see the module docstring. torchcodec cannot
            # open a file on this machine, so this is the only door in.
            {"waveform": waveform, "sample_rate": sample_rate},
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            hook=ProgressHook(on_progress),
        )
        annotation, embeddings = _unpack(output)
        turns = turns_from_diarization(annotation)
        vectors = embeddings_by_label(annotation, embeddings)
    except BaseException as exc:
        # The `del` below is not enough on this path, and this is the failure
        # that costs a card. A traceback keeps every frame it passed through
        # alive, and those frames are pyannote's own - holding the pipeline,
        # holding the samples. Anything that then holds the exception holds all
        # of it: a logger, an error report, the runner that has not written its
        # verdict yet. `clear_frames` drops the frames' locals and keeps the
        # file/line/function of every entry, so the traceback the user reads is
        # unchanged; only a post-mortem debugger would notice. This frame is
        # still executing, so clear_frames skips it and the `finally` below
        # still has the names it needs.
        traceback.clear_frames(exc.__traceback__)
        raise
    finally:
        del pipeline, waveform
        gc.collect()
        _empty_cuda_cache(device)

    return turns, vectors


def _unpack(output: Any) -> tuple[Any, Any]:
    """(annotation, embeddings) out of whatever this pyannote handed back.

    Three shapes are in the wild and a stored pipeline config decides which one
    turns up: pyannote 4's DiarizeOutput dataclass (the default), the bare
    Annotation it returns with `legacy=True`, and pyannote 3's
    `(annotation, embeddings)` tuple from `return_embeddings=True`.
    """
    if hasattr(output, "speaker_diarization"):
        return output.speaker_diarization, output.speaker_embeddings
    if isinstance(output, tuple) and len(output) == 2:
        return output
    return output, None


# --- the stage ------------------------------------------------------------------


def run(ctx: "RunnerContext") -> None:
    """The stage: find the speakers in this job's prepared wav."""
    wav = ctx.state.get("wav")
    if wav is None:
        raise RuntimeError(
            "diarize needs the wav the prepare stage writes, "
            "but ctx.state['wav'] is empty"
        )
    run_id = ctx.state.get("run_id")
    if not run_id:
        raise RuntimeError(
            "diarize needs the run the transcribe stage created, "
            "but ctx.state['run_id'] is empty"
        )

    # Opt-out rather than opt-in: a transcript without speakers is the poorer
    # product, so it takes an explicit `"diarize": false` to get one. What that
    # buys is a run with no weights at all - which is how the CPU end-to-end
    # test can exist, and how a single-speaker dictation skips three gigabytes.
    if not ctx.params.get("diarize", True):
        ctx.state["turns"] = []
        jobs.emit(ctx.conn, ctx.job["id"], "diarize", skipped=True, n_turns=0)
        ctx.report(1.0)
        return

    # No `model_dir_or_id`, and never one from the job (TASK-034). This read
    # `params["diarization_model"]` until 2026-09-11, and a job's params came
    # off `POST /api/media` unread: pyannote's `from_pretrained` builds the
    # class a pipeline config names and loads its checkpoints with
    # `weights_only=False`, so any local client chose code this child ran.
    # Nothing in the app ever wrote the key. Choosing a pipeline is done by
    # putting it at MODELS_DIR/pyannote, which takes write access to the data
    # directory - the same trust as editing this file. The door now refuses
    # the key too, but a job queued before the upgrade is never re-validated;
    # not reading it here is what covers that one.
    loaded: dict = {}
    # Resolved here rather than through `hf_token`, which carries the value
    # alone: when nothing loads, the note below has to say *where* the token
    # came from, and that is the one thing a bare string cannot tell it.
    token = credentials.resolve(ctx.conn, credentials.HUGGINGFACE)
    try:
        turns, embeddings = diarize(
            wav,
            num_speakers=ctx.params.get("num_speakers"),
            min_speakers=ctx.params.get("min_speakers"),
            max_speakers=ctx.params.get("max_speakers"),
            on_progress=ctx.report,
            device=ctx.params.get("device"),
            token=token.value,
            source_out=loaded,
        )
    except WeightsUnavailable as exc:
        _skip_speakers(ctx, run_id, token, exc)
        return

    _persist(ctx, run_id, embeddings)

    # attribute reads this next. Always set, even when empty: a silent
    # recording is not a reason for the next stage to meet a KeyError.
    ctx.state["turns"] = turns

    # What diarized is a fact about the run, the way the model that
    # transcribed is: it goes on the run row, and the fallback says so in
    # words a person can act on (ADR-004 does the same for turbo/translate).
    source = loaded.get("source", "")
    fallback = source == FALLBACK_SOURCE
    _note_run(ctx, run_id, source, FALLBACK_NOTE if fallback else None)

    speakers = sorted({turn.speaker for turn in turns})
    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "diarize",
        run_id=run_id,
        n_turns=len(turns),
        speakers=speakers,
        n_embeddings=len(embeddings),
        pipeline=source,
        fallback=fallback,
    )
    ctx.report(1.0)


def _skip_speakers(
    ctx: "RunnerContext", run_id: int, token: credentials.Resolved, exc: Exception
) -> None:
    """No weights anywhere: keep the transcript, drop the speakers, say so.

    This is the fifth of eight stages and the words were written by the
    fourth. Until TASK-089.08 `WeightsUnavailable` left the stage and the
    runner turned it into a failed job, so somebody who skipped the token -
    which is allowed, and stays allowed - waited an hour for a recording and
    was handed nothing. The transcript is what they came for; the speakers
    are the extra, and a run that keeps the one and names the other is a
    success with a note.

    There is no setting behind this. A watch folder, a feed, a URL and the
    recorder all carry their own options and never read `default_diarize`
    (brief: W6), so an install-time guard could not have covered them; the
    honest fallback has to live in the stage.
    """
    source = credentials.short_source(token.source)
    applog.log(
        "diarize.skipped",
        level="warn",
        job=ctx.job["id"],
        run=run_id,
        reason="weights-unavailable",
        credential=source or "none",
        # The first line only - the fixed sentence - and the per-route lines
        # under it are dropped here rather than carried anywhere else. They
        # are whatever pyannote and huggingface_hub said, and a download
        # error quotes a signed CDN URL: applog replaces a query string only
        # when the value *starts* with the URL (`_URL_WITH_QUERY` is anchored
        # at ^) and `detail` is not a name it redacts, so a mid-sentence one
        # would be written whole. What a person gets instead is the note on
        # the run, and `python -m scribe.doctor`, which asks the Hub with
        # this token and reports what it answered (ADR-014: the log
        # observes). `split` and not `splitlines()[0]`: an exception with no
        # text at all would raise IndexError inside the rescue.
        detail=str(exc).split("\n", 1)[0],
    )
    # The same state `"diarize": false` leaves (:609), so from here on the two
    # skips are one path. Keeping them identical is the point rather than the
    # next stage needing it: attribute reads `ctx.state.get("turns")`.
    ctx.state["turns"] = []
    _note_run(ctx, run_id, "", skipped_note(source))
    # Structured keys rather than the sentence: the jobs board truncates every
    # value at 80 characters (`jobs_ui._short`), and `reason` is what tells
    # this apart from the user switching "Recognise speakers" off - which
    # emits `skipped` too.
    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "diarize",
        run_id=run_id,
        skipped=True,
        reason="weights-unavailable",
        token_found=token.found,
        token_source=source,
        n_turns=0,
    )
    ctx.report(1.0)


def skipped_note(source: str) -> str:
    """Why this run has no speakers, and the three ways to get them.

    Two openings, because the two failures have different first moves. With no
    token at all, nobody has ever been asked for one. With a token that opens
    nothing, the token is there and its owner has almost always not accepted
    the model's conditions - and `source` says which token, by the name of the
    place it came from and never by its value.

    What this deliberately does not claim is 401 versus 403. pyannote hands
    back None for a missing, a private and a gated repository alike, so no
    status code ever reaches this stage; the doctor is where a token is
    actually asked about. "A token was found and it opened nothing" is the
    whole of what is known here, and it is said as that.

    The third route is named `MODELS_DIR/pyannote` and not the resolved path.
    This note is written to `run.params_json`, which the JSON export writes
    out verbatim (`exports/jsonw._params`), so a shared transcript would
    otherwise carry the directory layout of the machine that made it. The
    doctor prints the resolved path, because that is a report about one
    machine and never leaves it.
    """
    if source:
        opening = (
            f"Speakers were not worked out: the Hugging Face token from {source} "
            "opened none of the diarization pipelines - most often the model's "
            "conditions have not been accepted for it."
        )
    else:
        opening = (
            "Speakers were not worked out: no Hugging Face token was found, and "
            "every diarization pipeline this stage can load is gated."
        )
    return (
        f"{opening} The transcript is complete; only the speakers are missing. "
        "Three ways to get them next time: accept the conditions at "
        f"https://hf.co/{DEFAULT_PIPELINE} and save the token under Settings > "
        "Transcription; or set HF_TOKEN in the environment or in .env; or put a "
        "pipeline directory at MODELS_DIR/pyannote."
    )


def _note_run(ctx: "RunnerContext", run_id: int, source: str, note: str | None) -> None:
    """Merge the diarization source (and any substitution note) into run.params_json."""
    with db.LOCK:
        row = ctx.conn.execute("SELECT params_json FROM run WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        params = json.loads(row["params_json"] or "{}")
        if source:
            params["diarization_pipeline"] = source
        else:
            # Nothing loaded. An empty name would read as "diarized with the
            # pipeline called ''" everywhere run.params is shown or exported.
            params.pop("diarization_pipeline", None)
        if note:
            params["diarization_note"] = note
        else:
            params.pop("diarization_note", None)
        ctx.conn.execute(
            "UPDATE run SET params_json=? WHERE id=?", (json.dumps(params), run_id)
        )
        ctx.conn.commit()


def _persist(ctx: "RunnerContext", run_id: int, embeddings: dict[str, bytes]) -> None:
    """Replace this run's embeddings with the ones just measured.

    Delete-then-insert, in one transaction, rather than an upsert. The table
    holds current state - one row per (run, cluster), no history - and an
    upsert only refreshes the labels that happen to come back. A re-run that
    finds three speakers where the last one found four leaves the fourth
    behind: a cluster with no turns, still carrying an embedding. That is not
    a harmless orphan, because these vectors exist precisely to re-anchor
    display names after a re-diarization (spec section 8), and the matcher
    would happily hang somebody's name on it.

    Which is also why the empty case is not an early return: finding no
    embeddings this time is a result, and it has to clear the last ones.

    Scoped to `run_id`, never to the media: two runs over the same file are
    exactly what a user compares, and one must not empty the other.
    """
    with db.LOCK:
        try:
            ctx.conn.execute(
                "DELETE FROM speaker_embedding WHERE run_id=?", (run_id,)
            )
            ctx.conn.executemany(
                "INSERT INTO speaker_embedding(run_id, cluster_label, embedding)"
                " VALUES (?, ?, ?)",
                [(run_id, label, blob) for label, blob in sorted(embeddings.items())],
            )
            ctx.conn.commit()
        except BaseException:
            ctx.conn.rollback()
            raise


def _cuda_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def _empty_cuda_cache(device: str) -> None:
    """Hand the freed VRAM back before the next stage asks for it."""
    if not device.startswith("cuda"):
        return
    try:
        import torch
    except ImportError:  # pragma: no cover - a cuda device without torch
        return
    torch.cuda.empty_cache()
