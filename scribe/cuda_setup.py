"""Make CUDA DLLs findable before CTranslate2 loads them (Windows).

CTranslate2 delay-loads cuBLAS and cuDNN on the first GPU compute call, not at
import. The failure mode that produces is nasty: `WhisperModel(device="cuda")`
constructs fine, the model downloads, and only the first `transcribe()` dies
with "Library cudnn_ops64_9.dll is not found". So the DLL directories have to
be registered before `faster_whisper`/`ctranslate2` is imported at all.

Two mechanisms are needed, not one. `os.add_dll_directory()` covers modern
LoadLibraryEx lookups; delay-loaded imports resolved through the classic Win32
search order ignore it, so the same directories are also prepended to PATH.
Measured on this machine (2026-08-24, WHYcast): add_dll_directory alone is not
sufficient.

Source of the DLLs, in priority order:
  1. torch/lib   - torch's Windows wheels ship cudnn_ops64_9.dll, cublas64_12.dll
                   and friends. pyannote pulls torch in anyway, so this is free.
  2. nvidia/*/bin, nvidia/*/lib - the standalone nvidia-* pip wheels, when present.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_applied: list[str] = []


def _site_packages() -> Path | None:
    """The site-packages holding this interpreter's third-party code."""
    for entry in sys.path:
        if entry and entry.replace("\\", "/").endswith("site-packages"):
            path = Path(entry)
            if path.is_dir():
                return path
    return None


def candidate_dirs() -> list[Path]:
    """Directories that may hold CUDA DLLs, most authoritative first."""
    site = _site_packages()
    if site is None:
        return []

    dirs: list[Path] = []

    torch_lib = site / "torch" / "lib"
    if torch_lib.is_dir():
        dirs.append(torch_lib)

    nvidia_root = site / "nvidia"
    if nvidia_root.is_dir():
        for package in sorted(nvidia_root.iterdir()):
            for sub in ("bin", "lib"):
                candidate = package / sub
                if candidate.is_dir():
                    dirs.append(candidate)

    return dirs


def ensure_cuda_libs() -> list[str]:
    """Register CUDA DLL directories. Idempotent; safe without torch installed.

    Returns the directories added by this call - empty on a second call, and
    empty when nothing that ships CUDA DLLs is installed.
    """
    if _applied:
        return []

    added: list[str] = []
    for directory in candidate_dirs():
        text = str(directory)
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(text)
            except OSError:
                continue
        added.append(text)

    if added:
        existing = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join([*added, existing]) if existing else os.pathsep.join(added)
        _applied.extend(added)

    return added


# Windows error-mode bits. SEM_FAILCRITICALERRORS is the one measured to matter
# here; SEM_NOOPENFILEERRORBOX is the same class of hang for a missing file and
# costs nothing to add.
SEM_FAILCRITICALERRORS = 0x0001
SEM_NOOPENFILEERRORBOX = 0x8000


def silence_loader_dialogs() -> int:
    """Make a DLL that will not load an exception rather than a modal box.

    A background process must never be able to stop on a dialog nobody is
    looking at. Measured on 2026-09-03: pyannote imports torchcodec at module
    load (`pyannote/audio/core/io.py:43`), torchcodec 0.16 cannot resolve
    `torch_get_const_data_ptr` against torch 2.8 (ADR-005), and pyannote
    catches that perfectly well - it warns and falls back to the preloaded
    waveform this app already hands it. Whether the job survives that handled
    failure came down to the process error mode, which is *not* what a parent
    passes down reliably: a child launched through PowerShell's `Start-Process`
    starts at 0, where the loader puts up "Entry Point Not Found" and waits
    forever for an OK nobody will click; the same child launched from a shell
    that had set 3 raised straight through and the job finished. Inheriting
    that is luck, so every process that loads a model sets it for itself.

    Returns the error mode now in effect (0 off Windows, where there is no
    such thing and nothing to silence).
    """
    if sys.platform != "win32":
        return 0

    import ctypes

    kernel32 = ctypes.windll.kernel32
    # SetErrorMode returns the previous mode; OR ours onto it rather than
    # replacing, so a caller that had already silenced something keeps it.
    previous = kernel32.SetErrorMode(0)
    kernel32.SetErrorMode(previous | SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX)
    return kernel32.GetErrorMode()
