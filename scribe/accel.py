"""What this machine can accelerate with, decided in one place.

Three answers, each a string the stages already understand:

* `transcription_backend()` - ``cuda`` (faster-whisper on CTranslate2 with a
  CUDA card), ``mlx`` (mlx-whisper on an Apple GPU through Metal), or
  ``cpu`` (faster-whisper int8). CTranslate2 has no Metal backend, which is
  the whole reason a second transcription engine exists: on Apple Silicon
  faster-whisper can only use the CPU, and mlx-whisper runs the same Whisper
  weights on the GPU with word timestamps.
* `diarization_device()` - ``cuda``, ``mps`` or ``cpu``, for pyannote, which
  is PyTorch and so *does* have a Metal path. OpenTranscribe measured an M2
  Max on MPS at 2-3.5x slower than an RTX 3080 for the two GPU-bound stages
  (segmentation, embeddings) and faster than its Linux container for the CPU
  ones - well ahead of running the whole thing on the CPU. A few pyannote ops
  have no MPS kernel; `PYTORCH_ENABLE_MPS_FALLBACK` lets those run on the CPU
  instead of failing, and `diarize` sets it before torch is imported.
* `describe()` - the sentence the doctor prints.

Every probe here is cheap and import-guarded: asking costs no model load, and
a machine without torch or mlx simply answers ``cpu``. An explicit device
passed by a caller is never second-guessed anywhere; these are defaults.

Written without a Mac (2026-09-07) and accepted on an M2 (2026-09-11): the
doctor picked mlx and mps there, and a diarized file went through the app.
See TASK-020.

`cuda_available` imports torch, so nothing that runs in the web process may
ask (ADR-001); the doctor's `accel` line is a GPU check for that reason.
"""

from __future__ import annotations

import importlib.util
import platform
import sys

MLX_MODULE = "mlx_whisper"


def is_apple_silicon() -> bool:
    """macOS on an ARM CPU - the only place MLX and MPS exist."""
    return sys.platform == "darwin" and platform.machine() in ("arm64", "aarch64")


def mlx_available() -> bool:
    """mlx-whisper is importable on Apple Silicon. Nothing is imported: the
    package pulls in Metal and the models, and `find_spec` answers the same
    question for free."""
    return is_apple_silicon() and importlib.util.find_spec(MLX_MODULE) is not None


def cuda_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def mps_available() -> bool:
    """PyTorch's Metal backend, built and with a device behind it."""
    if not is_apple_silicon():
        return False
    try:
        import torch
    except ImportError:
        return False
    backend = getattr(torch.backends, "mps", None)
    return bool(backend is not None and backend.is_available())


def transcription_backend() -> str:
    """``cuda``, ``mlx`` or ``cpu``: what `transcribe.load_model` builds by
    default. CUDA first because it is the measured fast path (ADR-004's
    numbers); MLX next because on the machines that have it there is no CUDA."""
    if cuda_available():
        return "cuda"
    if mlx_available():
        return "mlx"
    return "cpu"


def diarization_device() -> str:
    """``cuda``, ``mps`` or ``cpu``: what `diarize.resolve_device` picks."""
    if cuda_available():
        return "cuda"
    if mps_available():
        return "mps"
    return "cpu"


def describe() -> str:
    """One line for the doctor: what each stage will run on."""
    return (
        f"transcription on {transcription_backend()}, diarization on {diarization_device()}"
    )
