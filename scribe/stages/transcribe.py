"""Whisper, consumed as it decodes.

`WhisperModel.transcribe()` returns in milliseconds and does no work: it hands
back a generator, and the GPU only runs when somebody asks it for the next
segment. Draining that generator into a list is the obvious thing to write and
it throws away the one honest progress signal in the whole pipeline. Consumed
one segment at a time, `segment.end / info.duration` is not an estimate - it is
the position in the file the decoder has actually reached (spec section 3). So
this stage never materialises the generator; the loop over it *is* the progress
bar, and it is also the only place a cancel can be honoured, because between
two segments is the only moment Whisper is not inside CTranslate2.

Four things this module is careful about.

**Words are canonical** (spec section 2). Segments are stored because Whisper
produced them and because they carry the confidence numbers and the FTS index,
but nothing here writes a paragraph, a cue, or any other grouping. Word text
keeps the leading space faster-whisper gives it, so `"".join(w["text"])`
reproduces the transcript exactly - including in languages that put no spaces
between words, where re-inserting them at export time would be wrong.

**Turbo cannot translate.** large-v3-turbo was distilled without the translate
task; asking for it produces confident nonsense rather than an error. So the
substitution happens here, once, and is written into the run row - a user who
later wonders why their translation says "large-v3" can find the answer.

**The model must be gone before the next stage loads one.** 16 GB of VRAM does
not fit Whisper and pyannote at once. The trap is that the generator holds a
reference to the model, so abandoning it mid-stream - which is exactly what a
cancel does - keeps the weights resident even after every name is dropped. The
generator is closed explicitly for that reason, before the `del`/`gc`/
`empty_cache` the plan's global constraints require.

**Confidence is persisted, not discarded**: word probability, and per segment
avg_logprob, no_speech_prob, compression_ratio and temperature. They are what
the transcript view tints and what the A/B enhancement harness will compare;
throwing them away here would make both impossible later.
"""

from __future__ import annotations

import gc
import json
import sqlite3
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Sequence

import numpy as np

from scribe import accel, cuda_setup, db, glossary, jobs
from scribe.stages import mlx_backend

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

DEFAULT_MODEL = "large-v3-turbo"
"""Spec default: 809M params, ~4x faster than large-v3, ~6 GB VRAM - which is
what leaves room for pyannote and a small local LLM on the same 16 GB card."""

TRANSLATE_MODEL = "large-v3"
"""What translate falls back to. The only full model that still has the task."""

TRANSLATE_SUBSTITUTION = "turbo cannot translate; using large-v3"

# The two tiers a user picks from (spec section 3, ADR-004): Turbo is the
# default model and Maximaal the full large-v3 - the same checkpoint translate
# falls back to, so neither name is spelled anywhere but in this module.
TIER_MODELS: dict[str, str] = {"turbo": DEFAULT_MODEL, "max": TRANSLATE_MODEL}

# Every language Whisper was trained on, as faster-whisper's tokenizer accepts
# the codes (`faster_whisper.tokenizer._LANGUAGE_CODES`), each with a name for
# a <select>. Copied rather than imported: the web process never loads
# faster_whisper (ADR-001), and the set is a property of the weights, not of
# the library version - tests/test_web_transcribe_dialog.py pins it to the
# installed copy all the same. Sorted by name, which is how a person scans it.
LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = (
    ("af", "Afrikaans"), ("sq", "Albanian"), ("am", "Amharic"), ("ar", "Arabic"),
    ("hy", "Armenian"), ("as", "Assamese"), ("az", "Azerbaijani"), ("ba", "Bashkir"),
    ("eu", "Basque"), ("be", "Belarusian"), ("bn", "Bengali"), ("bs", "Bosnian"),
    ("br", "Breton"), ("bg", "Bulgarian"), ("my", "Burmese"), ("yue", "Cantonese"),
    ("ca", "Catalan"), ("zh", "Chinese"), ("hr", "Croatian"), ("cs", "Czech"),
    ("da", "Danish"), ("nl", "Dutch"), ("en", "English"), ("et", "Estonian"),
    ("fo", "Faroese"), ("fi", "Finnish"), ("fr", "French"), ("gl", "Galician"),
    ("ka", "Georgian"), ("de", "German"), ("el", "Greek"), ("gu", "Gujarati"),
    ("ht", "Haitian Creole"), ("ha", "Hausa"), ("haw", "Hawaiian"), ("he", "Hebrew"),
    ("hi", "Hindi"), ("hu", "Hungarian"), ("is", "Icelandic"), ("id", "Indonesian"),
    ("it", "Italian"), ("ja", "Japanese"), ("jw", "Javanese"), ("kn", "Kannada"),
    ("kk", "Kazakh"), ("km", "Khmer"), ("ko", "Korean"), ("lo", "Lao"),
    ("la", "Latin"), ("lv", "Latvian"), ("ln", "Lingala"), ("lt", "Lithuanian"),
    ("lb", "Luxembourgish"), ("mk", "Macedonian"), ("mg", "Malagasy"), ("ms", "Malay"),
    ("ml", "Malayalam"), ("mt", "Maltese"), ("mi", "Maori"), ("mr", "Marathi"),
    ("mn", "Mongolian"), ("ne", "Nepali"), ("no", "Norwegian"), ("nn", "Norwegian Nynorsk"),
    ("oc", "Occitan"), ("ps", "Pashto"), ("fa", "Persian"), ("pl", "Polish"),
    ("pt", "Portuguese"), ("pa", "Punjabi"), ("ro", "Romanian"), ("ru", "Russian"),
    ("sa", "Sanskrit"), ("sr", "Serbian"), ("sn", "Shona"), ("sd", "Sindhi"),
    ("si", "Sinhala"), ("sk", "Slovak"), ("sl", "Slovenian"), ("so", "Somali"),
    ("es", "Spanish"), ("su", "Sundanese"), ("sw", "Swahili"), ("sv", "Swedish"),
    ("tl", "Tagalog"), ("tg", "Tajik"), ("ta", "Tamil"), ("tt", "Tatar"),
    ("te", "Telugu"), ("th", "Thai"), ("bo", "Tibetan"), ("tr", "Turkish"),
    ("tk", "Turkmen"), ("uk", "Ukrainian"), ("ur", "Urdu"), ("uz", "Uzbek"),
    ("vi", "Vietnamese"), ("cy", "Welsh"), ("yi", "Yiddish"), ("yo", "Yoruba"),
)
LANGUAGE_CODES = frozenset(code for code, _ in LANGUAGE_CHOICES)

# The hotword composition lives in `scribe.glossary` (Phase 6 Task 5), because
# the post-pass that corrects what the bias failed to prevent has to read the
# same terms through the same regex, and two spellings of one rule drift. These
# names stay bound here - to the very objects, not to copies - so every caller
# and every Phase 2 test that reached for `transcribe.compose_hotwords` still
# finds it.
HOTWORD_TOKEN_LIMIT = glossary.HOTWORD_TOKEN_LIMIT
_WORD = glossary._WORD
estimate_tokens = glossary.estimate_tokens
glossary_terms = glossary.glossary_terms
name_candidates = glossary.name_candidates
compose_hotwords = glossary.compose_hotwords

# The job params key carrying names the ingest door already knew - a downloaded
# video's title, uploader and chapters (`ingest.urls.hotword_terms`). They ride
# in the params because the info-json they came from lives in the download
# job's work directory, which the runner deletes when that job ends, long
# before this stage runs.
EXTRA_HOTWORDS_KEY = "extra_hotwords"

# Whisper's own default; named here because a decoder default that changes
# under us would change every transcript without changing this repository.
COMPRESSION_RATIO_THRESHOLD = 2.4

# The file is fed to Whisper in windows of this many seconds, cut at the
# quietest moment in the last SEARCH_SECONDS before each nominal boundary.
#
# Why windows at all: handed a path, faster-whisper decodes the whole file and
# computes its spectrogram in one array - complex128, 201 bins per 10 ms frame,
# so ~750 MB for 40 minutes and 3.4 GB for three hours, before the model has
# done anything. Measured here on 2026-09-02 when a 40-minute interview died
# with `Unable to allocate 743 MiB` on a machine at 95% commit. Ten minutes
# bounds that intermediate at ~190 MB whatever the file's length, and the
# window itself is 38 MB of float32. Cutting at a silence rather than on the
# nominal second is what keeps a word from being split across two decodes.
WINDOW_SECONDS = 600.0
SEARCH_SECONDS = 5.0
# How much of the next window each decode hears past its own cut; what it
# says there is dropped (`collect_segments`' limit) and said again, properly,
# by the next window. Without it every cut is, to Whisper, the end of the
# file, and the end of a file is where Whisper hallucinates. Measured
# 2026-09-10: 7 of 50 Hacker History episodes carried a run of ~100 identical
# words ("um, um, um") in 0.3 s just before a multiple of 600 s, and decoding
# those windows again gave end-of-window garbage every run, while the same
# windows with 30 s appended came back clean. Capped at half a window.
LOOKAHEAD_SECONDS = 30.0
# A tail shorter than this rides along with the previous window: Whisper has
# nothing to say about half a second, and the extra decode costs a model call.
MIN_TAIL_SECONDS = 1.0
# The prepare stage writes exactly this; everything below assumes it.
SAMPLE_RATE = 16000
_CUT_HOP_SECONDS = 0.1


class Cancelled(Exception):
    """The job asked to stop and this stage stopped between two segments.

    Not a failure: the runner catches it and delivers a `cancelled` verdict.
    It lives here because transcription is the first stage long enough that
    waiting for a stage boundary is not good enough (prepare says so in its own
    docstring), and diarization will raise the same one.
    """


# --- which model ---------------------------------------------------------------


def resolve_model(requested: str | None, task: str) -> tuple[str, str | None]:
    """Pick the model to actually load; returns (model, substitution note).

    The note is None when nothing was substituted, and a sentence fit for a UI
    when something was. Any checkpoint whose name says turbo is treated as
    turbo, because the missing task is a property of the weights and CT2
    conversions get republished under all sorts of repository ids.
    """
    model = (requested or "").strip() or DEFAULT_MODEL
    if task == "translate" and "turbo" in model.lower():
        return TRANSLATE_MODEL, TRANSLATE_SUBSTITUTION
    return model, None


class NotASpeechModel(ValueError):
    """A model name was handed to the transcribe stage that is not a checkpoint."""


# The names faster-whisper resolves to a repository by itself
# (`faster_whisper.utils._MODELS`). Copied rather than imported for the reason
# LANGUAGE_CHOICES is: the web process never loads faster_whisper (ADR-001).
# tests/test_stage_transcribe.py pins the copy to the installed library.
SPEECH_MODEL_ALIASES = frozenset(
    {
        "tiny", "tiny.en", "base", "base.en", "small", "small.en",
        "medium", "medium.en", "large", "large-v1", "large-v2", "large-v3",
        "large-v3-turbo", "turbo",
        "distil-small.en", "distil-medium.en",
        "distil-large-v2", "distil-large-v3", "distil-large-v3.5",
    }
)


def is_speech_model(name: str) -> bool:
    """Is this the name of a Whisper checkpoint rather than some other model?

    Three ways to be one, in the order they cost anything: a name
    faster-whisper resolves itself, a directory on this disk (a conversion may
    be called whatever its author liked), or a repository id that says whisper
    somewhere - owner included, because `distil-whisper/distil-large-v3.5-ct2`
    only says it in the owner.

    The repository rule is a judgement, not a proof, and it is deliberately the
    strict direction: a CT2 conversion whose id never mentions whisper is
    refused and its owner points the stage at a local path or renames nothing -
    one clear message, one setting. The other direction cost an afternoon.
    """
    text = (name or "").strip()
    if not text:
        return False
    if text in SPEECH_MODEL_ALIASES:
        return True
    if Path(text).is_dir():
        return True
    return "whisper" in text.lower()


def ensure_speech_model(name: str) -> str:
    """`name` if it is a checkpoint; otherwise say so before the network does.

    A chat model reaching here used to become a HuggingFace 404 naming a
    repository nobody ever claimed existed - which reads like the app inventing
    a fact rather than a value arriving in the wrong field (2026-09-03,
    `openai/gpt-5.6-luna`).
    """
    if is_speech_model(name):
        return name
    raise NotASpeechModel(
        f"{name!r} is not a speech model. That looks like a chat model, and this "
        f"stage loads a Whisper checkpoint: a tier such as {DEFAULT_MODEL!r}, a "
        f"faster-whisper repository, or a directory holding a converted model. "
        f"Chat models belong to the AI panel, not to transcription."
    )


def perf_model_for(params: dict) -> str:
    """The one key a job's stage_perf rows are filed under, and read back by.

    Both the runner (writing timings) and the job API (computing an ETA) must
    derive it from the job params the same way, or a default job files its
    history under "large-v3-turbo" and looks it up under None - which is
    exactly what happened, and left every ETA past prepare permanently empty.
    """
    return resolve_model(params.get("model"), params.get("task") or "transcribe")[0]


# --- consuming the generator ---------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One stretch of the prepared wav, as Whisper will hear it."""

    offset: float  # seconds from the start of the file
    samples: np.ndarray  # float32, mono, SAMPLE_RATE
    # The start of the next window, heard but not kept (LOOKAHEAD_SECONDS).
    # Empty for the last window: the real end of the file is where it ends.
    lookahead: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float32))


def quietest_cut(samples: np.ndarray, sample_rate: int, *, search_seconds: float) -> int:
    """Index in ``samples`` to cut at: the start of the quietest 100 ms hop in
    the last ``search_seconds``. A buffer shorter than the span is cut at its
    end - there is nothing to look back over.

    RMS over fixed hops rather than anything cleverer: the question is only
    "where is the least sound", and a 100 ms hop is finer than any word gap
    worth cutting in. Ties go to the latest hop, which keeps windows as close
    to their nominal length as the audio allows.
    """
    n = len(samples)
    span = int(search_seconds * sample_rate)
    hop = max(1, int(_CUT_HOP_SECONDS * sample_rate))
    if span <= hop or n <= span:
        return n
    tail = samples[n - span :]
    usable = (len(tail) // hop) * hop
    frames = tail[:usable].reshape(-1, hop).astype(np.float64)
    energy = np.sqrt(np.mean(frames * frames, axis=1))
    # argmin on the reversed array finds the LAST minimum; ties go late.
    quietest = len(energy) - 1 - int(np.argmin(energy[::-1]))
    return n - span + quietest * hop


def iter_windows(
    wav: str | Path,
    *,
    window_seconds: float = WINDOW_SECONDS,
    search_seconds: float = SEARCH_SECONDS,
    min_tail_seconds: float = MIN_TAIL_SECONDS,
    lookahead_seconds: float = LOOKAHEAD_SECONDS,
) -> Iterator[Window]:
    """Yield the prepared wav one window at a time, never holding the file.

    Reads pcm_s16le straight from the wave module - no decoder, no torchaudio
    (which cannot import here, see diarize) - and converts each window to
    float32 as it goes, so the peak is one window plus the int16 block being
    converted. Each window ends at `quietest_cut` of a nominal-length read,
    and the samples past the cut are carried into the next window, so the
    windows are contiguous and add up to the file exactly.

    Every window but the last also carries `lookahead`: the first
    `lookahead_seconds` of the next one (at most half a window), read ahead
    into the carry, so the decoder hears speech go on past the cut.
    """
    with wave.open(str(wav), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError(
                f"{Path(wav).name} is not mono 16-bit PCM; the prepare stage writes "
                "exactly that and nothing else is read here"
            )
        rate = source.getframerate()
        nominal = int(window_seconds * rate)
        min_tail = int(min_tail_seconds * rate)
        # Half a window at most: a carry longer than that would make the next
        # read smaller than the window it is meant to fill.
        lookahead = int(min(lookahead_seconds, window_seconds / 2) * rate)
        total = source.getnframes()
        carry = np.empty(0, dtype=np.float32)
        consumed = 0  # frames read from the file
        offset_frames = 0  # where the next window starts, in frames

        while True:
            need = nominal - len(carry)
            block = source.readframes(need) if need > 0 else b""
            fresh = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768.0
            consumed += len(fresh)
            buffer = np.concatenate([carry, fresh]) if len(carry) else fresh
            at_end = consumed >= total or len(fresh) < need
            if len(buffer) == 0:
                return
            if at_end:
                yield Window(offset_frames / rate, buffer)
                return
            cut = quietest_cut(buffer, rate, search_seconds=search_seconds)
            remaining_after = total - consumed + (len(buffer) - cut)
            if remaining_after < min_tail:
                # A sliver would be left over; take it now instead.
                rest = np.frombuffer(source.readframes(total - consumed), dtype="<i2").astype(np.float32) / 32768.0
                yield Window(offset_frames / rate, np.concatenate([buffer, rest]))
                return
            carry = buffer[cut:].copy()
            short = lookahead - len(carry)
            if short > 0 and consumed < total:
                # Read the look-ahead now; it is the next window's start, so
                # the next read is that much smaller and nothing is read twice.
                ahead = np.frombuffer(source.readframes(short), dtype="<i2").astype(np.float32) / 32768.0
                consumed += len(ahead)
                carry = np.concatenate([carry, ahead])
            yield Window(offset_frames / rate, buffer[:cut], carry[:lookahead])
            offset_frames += cut


def read_wav(wav: str | Path) -> "np.ndarray":
    """The whole prepared wav as float32 samples - for a caller that has one
    short clip and a backend that takes arrays rather than paths (MLX). The
    stage itself never uses this: it windows with `iter_windows`."""
    with wave.open(str(wav), "rb") as source:
        frames = source.readframes(source.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def collect_segments(
    segments: Iterable[Any],
    duration: float,
    *,
    on_progress: Callable[[float], None],
    cancelled: Callable[[], bool] | None = None,
    offset: float = 0.0,
    idx0: int = 0,
    word_idx0: int = 0,
    on_segment: Callable[[dict], None] | None = None,
    limit: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """Walk Whisper's segment generator, reporting where it has got to.

    `limit`, in seconds of this window, is where the window's own audio ends
    and its look-ahead begins (`LOOKAHEAD_SECONDS`). A word that starts there
    or later is the next window's to say, so it is dropped; the segment it
    ends is cut back to the words before it; and the first segment that starts
    past the limit ends the walk without the generator being asked for more,
    which is what keeps the look-ahead from costing a decode of its own.

    `on_segment`, when given, sees each segment row the moment it is built -
    the hook the live log's glimpse of the text hangs on. It is called after
    the row is appended and before the next segment is demanded, so a slow
    listener delays the decoder rather than losing a segment.

    Split out from the model for the same reason `prepare.progress_fractions`
    is split out from ffmpeg: the awkward cases are far easier to hand this
    function than to provoke out of a decoder. A segment Whisper re-emitted
    after a temperature fallback (progress would go backwards), a VAD-padded
    end past the file's duration, a container whose duration nobody knows, a
    segment with no words, a cancel between two segments - all of them are two
    lines of test here and an afternoon of luck against a real model.

    `duration` of 0 means unknown, and then nothing is reported in between:
    the rule is 0 and then 1, never an invented number.

    Raises :class:`Cancelled` when the flag goes up, checked before the first
    segment is demanded and after each one is handled - which is where a cancel
    costs nothing, because asking for the next segment is what starts the next
    window of GPU work.
    """
    duration = float(duration or 0.0)
    segment_rows: list[dict] = []
    word_rows: list[dict] = []
    # Starts at where the previous window got to, so a second window's first
    # segment does not report the bar back to zero.
    highest = min(1.0, offset / duration) if duration > 0 else 0.0

    def stop_if_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise Cancelled(
                f"cancel requested after {len(segment_rows)} segments "
                f"({len(word_rows)} words)"
            )

    stop_if_cancelled()
    for segment in segments:
        if limit is not None and float(segment.start) >= limit:
            break
        words = list(segment.words or ())
        end = float(segment.end)
        text = (segment.text or "").strip()
        if limit is not None:
            kept = [word for word in words if _midpoint(word) < limit]
            if words and not kept:
                break
            if len(kept) < len(words):
                words = kept
                end = float(kept[-1].end)
                text = "".join(word.word for word in kept).strip()
        idx = idx0 + len(segment_rows)
        segment_rows.append(
            {
                "idx": idx,
                "start": offset + float(segment.start),
                "end": offset + end,
                "text": text,
                "avg_logprob": _as_float(segment.avg_logprob),
                "no_speech_prob": _as_float(segment.no_speech_prob),
                "compression_ratio": _as_float(segment.compression_ratio),
                "temperature": _as_float(getattr(segment, "temperature", None)),
            }
        )
        if on_segment is not None:
            on_segment(segment_rows[-1])
        for word in words:
            word_rows.append(
                {
                    "idx": word_idx0 + len(word_rows),
                    "segment_idx": idx,
                    "start": offset + float(word.start),
                    "end": offset + float(word.end),
                    "text": word.word,
                    "probability": _as_float(word.probability),
                }
            )

        if duration > 0:
            reached = end if limit is None else min(end, limit)
            fraction = min(1.0, (offset + reached) / duration)
            highest = max(highest, fraction)
            on_progress(highest)

        stop_if_cancelled()

    return segment_rows, word_rows


def _midpoint(word: Any) -> float:
    """Where most of a word's sound is - which side of a cut it belongs to.

    Not its start: a cut can run through a word (it falls at the quietest
    100 ms, which is not always a pause), and then both windows hear part of
    it. Measured on clip30 cut at 19.2 s: "we" ran 19.12-19.48, the window
    before the cut kept it by its start, the window after heard the rest and
    wrote it again - "we we". By its midpoint it is the second window's, which
    heard most of it; "Revspace" (27.88-28.48, cut at 28.4) stays with the
    first, which heard nearly all of it."""
    return (float(word.start) + float(word.end)) / 2


# --- the model ------------------------------------------------------------------


def load_model(
    model_name: str,
    *,
    device: str | None = None,
    compute_type: str | None = None,
) -> tuple[Any, str, str]:
    """Construct a WhisperModel; returns (model, device, compute_type).

    A seam, not ceremony: it is the one line the tests replace to exercise
    everything around a model without loading one, and it is where
    `ensure_cuda_libs()` has to run - before `faster_whisper` is imported at
    all, because CTranslate2 delay-loads cuDNN and a late fix arrives after the
    DLL search has already failed.
    """
    if device is None:
        device = accel.transcription_backend()
    if device == mlx_backend.DEVICE:
        # Apple Silicon: the same weights on the Apple GPU through Metal.
        # CTranslate2 has no Metal backend, so faster-whisper is not an option
        # there; mlx_backend gives mlx-whisper faster-whisper's shape.
        return mlx_backend.MlxWhisperModel(model_name), device, mlx_backend.COMPUTE_TYPE

    cuda_setup.ensure_cuda_libs()
    from faster_whisper import WhisperModel

    if compute_type is None:
        compute_type = "float16" if device.startswith("cuda") else "int8"

    return WhisperModel(model_name, device=device, compute_type=compute_type), device, compute_type


def transcribe_audio(
    wav: str | Path,
    *,
    model_name: str = DEFAULT_MODEL,
    language: str | None = None,
    task: str = "transcribe",
    hotwords: str | None = None,
    on_progress: Callable[[float], None],
    cancelled: Callable[[], bool] | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    window_seconds: float = WINDOW_SECONDS,
    search_seconds: float = SEARCH_SECONDS,
    lookahead_seconds: float = LOOKAHEAD_SECONDS,
    on_segment: Callable[[dict], None] | None = None,
) -> tuple[dict, list[dict], list[dict]]:
    """Transcribe one prepared wav; returns (info, segments, words).

    The wav goes to Whisper as windows (`iter_windows`), never as a path: a
    path makes faster-whisper spectrogram the whole file in one array, which
    is what took a 40-minute interview down. Each window's segments come back
    with their times shifted by the window's offset and their indices
    continuing from the previous window's, so the result is indistinguishable
    from one decode of the whole file - except that it fits in memory.

    Each decode hears its window plus the window's look-ahead, and keeps only
    what was said before the cut (`collect_segments`' `limit`): a cut is not
    the end of the recording, and must not sound like one to the decoder.

    Language is detected on the first window only and then held for the rest.
    Letting every window detect for itself would let a recording that switches
    language mid-way, or a window of mostly silence, come out as two languages.

    `info` describes the run that actually happened - the model, device and
    compute type that were used rather than the ones that were asked for, plus
    the language Whisper detected - because that is what goes in the run row.
    """
    model, device, compute_type = load_model(
        model_name, device=device, compute_type=compute_type
    )
    duration = wav_duration(wav)
    segments: list[dict] = []
    words: list[dict] = []
    detected_language = language
    language_probability: float | None = None
    duration_after_vad = 0.0
    stream = None
    try:
        for window in iter_windows(
            wav,
            window_seconds=window_seconds,
            search_seconds=search_seconds,
            lookahead_seconds=lookahead_seconds,
        ):
            heard = (
                np.concatenate([window.samples, window.lookahead])
                if len(window.lookahead)
                else window.samples
            )
            limit = len(window.samples) / SAMPLE_RATE if len(window.lookahead) else None
            stream, raw = model.transcribe(
                heard,
                language=detected_language,
                task=task,
                # hotwords, not initial_prompt: re-injected into every decode
                # window instead of only the first (spec section 3).
                hotwords=hotwords or None,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=True,
                compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
            )
            if detected_language is None:
                detected_language = raw.language
            if language_probability is None:
                language_probability = _as_float(raw.language_probability)
            # Speech Whisper was handed, look-ahead included: up to
            # LOOKAHEAD_SECONDS per cut is counted in two windows.
            duration_after_vad += _as_float(getattr(raw, "duration_after_vad", None)) or 0.0
            new_segments, new_words = collect_segments(
                stream,
                duration,
                on_progress=on_progress,
                cancelled=cancelled,
                offset=window.offset,
                idx0=len(segments),
                word_idx0=len(words),
                on_segment=on_segment,
                limit=limit,
            )
            segments.extend(new_segments)
            words.extend(new_words)
            # Each window's generator is closed as soon as it is drained; the
            # `finally` below only has to worry about the one in flight.
            if hasattr(stream, "close"):
                stream.close()
            stream = None
        info = {
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
            "task": task,
            "language": detected_language,
            "language_probability": language_probability,
            "duration": duration,
            "duration_after_vad": duration_after_vad,
        }
    finally:
        # Closing comes first and is not optional. An abandoned generator keeps
        # its frame, its frame keeps the model, and on the cancel path the
        # raised exception's traceback keeps the frame alive well past every
        # `del` below - so without this the weights sit in VRAM while pyannote
        # tries to load its own.
        if stream is not None and hasattr(stream, "close"):
            stream.close()
        del stream, model
        gc.collect()
        _empty_cuda_cache(device)

    return info, segments, words


# --- the stage ------------------------------------------------------------------


def run(ctx: "RunnerContext") -> None:
    """The stage: transcribe this job's prepared wav into words and segments."""
    wav = ctx.state.get("wav")
    if wav is None:
        raise RuntimeError(
            "transcribe needs the wav the prepare stage writes, "
            "but ctx.state['wav'] is empty"
        )

    task = (ctx.params.get("task") or "transcribe").strip()
    model_name, substitution = resolve_model(ctx.params.get("model"), task)
    # Before the download: a name from another kind of model fails here, in a
    # sentence, rather than as a 404 for a repository that never existed.
    ensure_speech_model(model_name)
    media_row = _media_row(ctx)
    hotwords = compose_hotwords(ctx.conn, media_row, _extra_hotwords(ctx.params))

    glimpse = LiveText(ctx)
    info, segments, words = transcribe_audio(
        wav,
        model_name=model_name,
        language=ctx.params.get("language"),
        task=task,
        hotwords=hotwords,
        on_progress=ctx.report,
        cancelled=ctx.cancelled,
        # Overridable so a CPU test can stay a CPU test on a machine with a
        # perfectly good GPU sitting right there.
        device=ctx.params.get("device"),
        compute_type=ctx.params.get("compute_type"),
        on_segment=glimpse.add,
    )
    glimpse.flush()

    run_id = _persist(
        ctx, info=info, segments=segments, words=words,
        substitution=substitution, hotwords=hotwords,
    )

    # The next stages read from here rather than the database: attribute needs
    # the words, finalize needs the run they belong to, and the runner files
    # this stage's timing under the model that actually ran.
    ctx.state.update(run_id=run_id, words=words, model=model_name)

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "transcribe",
        run_id=run_id,
        model=model_name,
        substitution=substitution,
        language=info["language"],
        language_probability=info["language_probability"],
        device=info["device"],
        compute_type=info["compute_type"],
        n_segments=len(segments),
        n_words=len(words),
    )
    ctx.report(1.0)


LIVE_TEXT_SECONDS = 8.0
"""How long the live text waits before it becomes a job event, and so a line
on the job page. A glimpse per segment would be a row every second or two of
audio - thousands for an hour - and `job_event` is coordination, not a
transcript (ADR-002). Eight seconds of wall clock, or the segment cap below,
whichever comes first: a few hundred rows for an hour, each a sentence or
two, and a reader watching the page sees text within seconds of it being
decoded."""

LIVE_TEXT_SEGMENTS = 12
"""The other flush trigger: a fast GPU decodes twelve segments in well under
eight seconds, and a line of twelve sentences is still a line."""


class LiveText:
    """Segments in, one `log` event per batch out: the text as it appears.

    Nothing reads these events to decide anything (ADR-007's rule for the
    application log holds here too): the words table is the transcript, and
    finalize builds from it. This is what the job page shows while the words
    are still being written - and after, since a finished job's log is the
    events it left.
    """

    def __init__(self, ctx: "RunnerContext", *, clock: Callable[[], float] = time.monotonic) -> None:
        self.ctx = ctx
        self.clock = clock
        self.pending: list[dict] = []
        self.since = clock()

    def add(self, segment: dict) -> None:
        text = (segment.get("text") or "").strip()
        if not text:
            return
        self.pending.append(segment)
        if len(self.pending) >= LIVE_TEXT_SEGMENTS or self.clock() - self.since >= LIVE_TEXT_SECONDS:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            self.since = self.clock()
            return
        first, last = self.pending[0], self.pending[-1]
        jobs.emit(
            self.ctx.conn,
            self.ctx.job["id"],
            "log",
            at=float(first["start"]),
            until=float(last["end"]),
            text=" ".join((s.get("text") or "").strip() for s in self.pending),
        )
        self.pending = []
        self.since = self.clock()


def _persist(
    ctx: "RunnerContext",
    *,
    info: dict,
    segments: Sequence[dict],
    words: Sequence[dict],
    substitution: str | None,
    hotwords: str,
) -> int:
    """Write the run, its segments and its words in one transaction.

    One transaction because a run row without its words is worse than no run at
    all: the library would list a finished transcript that opens empty. The
    FTS triggers fire inside it too, so a rollback takes the index with it.

    `is_current` stays 0 and `xrt` stays NULL - which run a user sees, and how
    fast it was, are both finalize's call.
    """
    params = {
        "requested_model": (ctx.params.get("model") or "").strip() or DEFAULT_MODEL,
        "substitution": substitution,
        "hotwords": hotwords,
        "word_timestamps": True,
        "vad_filter": True,
        "condition_on_previous_text": True,
        "compression_ratio_threshold": COMPRESSION_RATIO_THRESHOLD,
        "device": info["device"],
        "duration": info["duration"],
        "duration_after_vad": info["duration_after_vad"],
        "language_probability": info["language_probability"],
    }

    with db.LOCK:
        try:
            cur = ctx.conn.execute(
                "INSERT INTO run(media_id, engine, model, compute_type, language,"
                " task, params_json, is_current, created_at)"
                " VALUES (?, 'faster-whisper', ?, ?, ?, ?, ?, 0, ?)",
                (
                    ctx.job["media_id"],
                    info["model"],
                    info["compute_type"],
                    info["language"],
                    info["task"],
                    json.dumps(params),
                    time.time(),
                ),
            )
            run_id = cur.lastrowid
            ctx.conn.executemany(
                "INSERT INTO segment(run_id, idx, start, end, text, avg_logprob,"
                " no_speech_prob, compression_ratio, temperature)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, s["idx"], s["start"], s["end"], s["text"],
                        s["avg_logprob"], s["no_speech_prob"],
                        s["compression_ratio"], s["temperature"],
                    )
                    for s in segments
                ],
            )
            ctx.conn.executemany(
                "INSERT INTO word(run_id, idx, start, end, text, probability)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (run_id, w["idx"], w["start"], w["end"], w["text"], w["probability"])
                    for w in words
                ],
            )
            ctx.conn.execute(
                "UPDATE job SET run_id=? WHERE id=?", (run_id, ctx.job["id"])
            )
            ctx.conn.commit()
        except BaseException:
            ctx.conn.rollback()
            raise
    return run_id


def _extra_hotwords(params: dict) -> tuple[str, ...]:
    """The names the ingest door already knew, defensively read.

    The params come out of the job's JSON, which a previous phase wrote and a
    user could in principle have edited; anything that is not a list of
    non-empty strings is worth nothing here and is dropped rather than
    argued about.
    """
    given = params.get(EXTRA_HOTWORDS_KEY)
    if not isinstance(given, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in given if str(item).strip())


def _media_row(ctx: "RunnerContext") -> dict:
    if not ctx.job.get("media_id"):
        return {}
    with db.LOCK:
        row = ctx.conn.execute(
            "SELECT * FROM media WHERE id=?", (ctx.job["media_id"],)
        ).fetchone()
    return dict(row) if row is not None else {}


def _cuda_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def _empty_cuda_cache(device: str) -> None:
    """Hand the freed VRAM back before the next stage asks for it.

    Only on a CUDA run: on CPU there is nothing to give back, and importing
    torch to find that out would cost the unit suite two seconds per test.
    """
    if not device.startswith("cuda"):
        return
    try:
        import torch
    except ImportError:  # pragma: no cover - a cuda device without torch
        return
    torch.cuda.empty_cache()


def _as_float(value: Any) -> float | None:
    """Plain Python floats out of numpy scalars, and None left alone.

    faster-whisper hands out `numpy.float64`, which behaves like a float right
    up until something downstream meets one and does not.
    """
    return None if value is None else float(value)


def wav_duration(wav: str | Path) -> float:
    """Seconds of audio in a wav, from its header - the whole-media denominator
    every window's progress is reported against."""
    with wave.open(str(wav), "rb") as source:
        rate = source.getframerate()
        return source.getnframes() / rate if rate else 0.0
