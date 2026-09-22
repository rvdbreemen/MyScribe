"""mlx-whisper behind faster-whisper's interface, for Apple Silicon.

`transcribe.transcribe_audio` talks to a model through two calls: it is built
by `load_model`, and `model.transcribe(samples, **options)` returns a
generator of segments plus an info object. faster-whisper's segments carry
`start`, `end`, `text`, `avg_logprob`, `no_speech_prob`,
`compression_ratio`, `temperature` and `words`, each word with `word`,
`start`, `end`, `probability`. This module gives mlx-whisper's result that
exact shape, so `collect_segments`, the live text, the run row and every
stage after read nothing new.

What differs, and what is done about it:

* mlx-whisper decodes a whole array at once and returns a dict with
  ``segments`` (each with ``words`` when asked) and ``language``. The window
  loop in `transcribe_audio` still hands it 5-minute windows (`iter_windows`),
  so memory stays bounded the same way; the dict is turned into a generator
  so the caller's ``close()`` and the cancel between segments keep working.
* No ``vad_filter`` and no ``hotwords``. VAD is dropped: the `prepare` stage
  has already normalised the audio and Whisper's own no-speech handling
  stands. Hotwords become ``initial_prompt``, which mlx-whisper applies to the
  first window of each call - and each `iter_windows` window *is* a call, so
  the glossary terms reach every window after all.
* ``language_probability`` is not reported by mlx-whisper; it is None, and
  the run row allows that.
* The model name is a faster-whisper name; MLX weights live under
  ``mlx-community/`` on the Hub and `repo_for` derives one from the other.
  A name with a slash in it is taken as a repository id, so a person can
  point at any converted checkpoint, quantised ones included.

Written without a Mac to run it on (2026-09-07); the shapes above are from
mlx-whisper 0.4's ``transcribe()`` and faster-whisper 1.2's ``Segment`` /
``Word``. `tests/test_stage_transcribe_mlx.py` pins the mapping against a
fake ``mlx_whisper`` module; Jim's MacBook is where the real one is proved.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Iterator

from scribe import models

MLX_ORG = "mlx-community"
"""Where the converted Whisper weights live on the Hub. The repository for a
faster-whisper model name follows one rule - ``whisper-<name>`` for the turbo
checkpoint, ``whisper-<name>-mlx`` for the others - and `repo_for` applies it
rather than keeping a table, so the default model's name is spelled in one
file only (ADR-004's grep)."""

DEVICE = "mlx"
COMPUTE_TYPE = "float16"
"""What the run row records. mlx-community's default conversions are fp16;
the quantised ones (``-4bit``, ``-8bit``) are separate repositories a person
can name explicitly."""


def repo_for(model_name: str) -> str:
    """The Hub repository for a model name; a name with a slash already is one."""
    name = (model_name or "").strip()
    if "/" in name:
        return name
    suffix = "" if name.endswith("turbo") else "-mlx"
    return f"{MLX_ORG}/whisper-{name}{suffix}"


class MlxWhisperModel:
    """The object `load_model` returns on Apple Silicon: `transcribe()` in
    faster-whisper's shape over `mlx_whisper.transcribe`."""

    def __init__(self, model_name: str, *, module: Any | None = None) -> None:
        if module is None:
            import mlx_whisper  # noqa: PLC0415 - only importable on Apple Silicon

            module = mlx_whisper
        self._mlx = module
        self.model_name = model_name
        # The copy setup downloaded, when it downloaded one, and the hub id
        # otherwise. mlx-whisper documents `path_or_hf_repo` as taking either,
        # but nobody here has a Mac to run it on: whether it really loads from
        # a folder is TASK-089.16's criterion 8, and until somebody answers it
        # the fallback is the path every Mac has actually used.
        local = models.local_dir(model_name, DEVICE)
        self.repo = str(local) if local else repo_for(model_name)

    def transcribe(
        self,
        audio: Any,
        *,
        language: str | None = None,
        task: str = "transcribe",
        hotwords: str | None = None,
        word_timestamps: bool = True,
        vad_filter: bool = True,  # accepted and ignored: see the module docstring
        condition_on_previous_text: bool = True,
        compression_ratio_threshold: float | None = None,
        **_ignored: Any,
    ) -> tuple[Iterator[Any], Any]:
        options: dict[str, Any] = {
            "path_or_hf_repo": self.repo,
            "task": task,
            "word_timestamps": word_timestamps,
            "condition_on_previous_text": condition_on_previous_text,
            "verbose": None,
        }
        if language:
            options["language"] = language
        if hotwords:
            options["initial_prompt"] = hotwords
        if compression_ratio_threshold is not None:
            options["compression_ratio_threshold"] = compression_ratio_threshold

        result = self._mlx.transcribe(audio, **options)
        segments = [as_segment(raw) for raw in (result.get("segments") or [])]
        duration = float(segments[-1].end) if segments else 0.0
        info = SimpleNamespace(
            language=result.get("language") or language,
            language_probability=None,
            duration=duration,
            duration_after_vad=None,  # no VAD ran, so there is no speech count (TASK-056)
        )
        return iter(segments), info


def as_segment(raw: dict) -> SimpleNamespace:
    """One mlx-whisper segment dict as a faster-whisper-shaped object."""
    words = [
        SimpleNamespace(
            word=str(w.get("word", "")),
            start=float(w.get("start", 0.0)),
            end=float(w.get("end", 0.0)),
            probability=_maybe_float(w.get("probability")),
        )
        for w in (raw.get("words") or [])
    ]
    return SimpleNamespace(
        start=float(raw.get("start", 0.0)),
        end=float(raw.get("end", 0.0)),
        text=str(raw.get("text", "")),
        avg_logprob=_maybe_float(raw.get("avg_logprob")),
        no_speech_prob=_maybe_float(raw.get("no_speech_prob")),
        compression_ratio=_maybe_float(raw.get("compression_ratio")),
        temperature=_maybe_float(raw.get("temperature")),
        words=words,
    )


def _maybe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
