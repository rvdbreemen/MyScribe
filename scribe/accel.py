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
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

MLX_MODULE = "mlx_whisper"

_NVIDIA_PCI_VENDOR = "10de"
"""NVIDIA's PCI vendor id. The one signal that does not need a driver: the
firmware enumerates a card whether or not anything can talk to it."""

_NVIDIA_SMI_LOCATIONS = (
    Path(r"C:\Windows\System32\nvidia-smi.exe"),
    Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"),
    Path("/usr/bin/nvidia-smi"),
    Path("/usr/local/nvidia/bin/nvidia-smi"),
)
"""Where the driver leaves its own tool when it is not on this process's PATH."""

_SYSFS_PCI_DEVICES = Path("/sys/bus/pci/devices")
"""Linux's PCI enumeration: one directory per device, each with a `vendor` file."""


def nvidia_hardware_present() -> bool:
    """Is there an NVIDIA card in this machine - driver or no driver?

    The question `torch.cuda.is_available()` cannot answer. It says only that
    no device could be opened, which is equally true of a laptop that never
    had a card and of a 3080 behind a driver that broke this morning. One of
    those is a machine working as designed and the other has quietly stopped
    transcribing, so the doctor has to tell them apart before it decides
    whether to fail.

    Two signals, and deliberately not `CUDA_VISIBLE_DEVICES` - that variable
    simulates both cases identically, which is the whole reason this function
    exists. The driver's own `nvidia-smi` being installed is one; the PCI
    vendor id the firmware enumerated is the other, and it is there before any
    driver is. `nvidia-smi` is only ever looked for, never run: on a broken
    driver it exits non-zero while the card is still in the machine, and
    reading that as "no hardware" would soften exactly the case ADR-012 wants
    red.

    Every doubt answers True. Being wrong that way costs a fix hint somebody
    does not need; being wrong the other way hides a dead card. So an error
    reading the registry or sysfs, a list that cannot be read at all, and an
    operating system this function has not been taught are all hardware
    present.

    Not tried on a real machine without an NVIDIA card by anybody
    (TASK-089.12, criterion 9). What the tests pin is the rule, with this
    function replaced.
    """
    if shutil.which("nvidia-smi") or any(path.exists() for path in _NVIDIA_SMI_LOCATIONS):
        return True
    if sys.platform == "win32":
        return _nvidia_in_windows_pci()
    if sys.platform.startswith("linux"):
        return _nvidia_in_sysfs()
    return True


def _nvidia_in_windows_pci() -> bool:
    """The PCI devices Windows enumerated, read from the registry.

    `Enum\\PCI` holds one subkey per vendor and device, named `VEN_10DE&DEV_`
    and the rest, and it is filled by enumeration rather than by a driver
    install - which is the case a `nvidia-smi` lookup misses. Measured here:
    0.3 ms and two vendor keys. `wmic` is gone in Windows 11 24H2 and a CIM
    call costs a second and a process.

    The loop asks for exactly the number of subkeys `QueryInfoKey` reports, so
    a read that is refused raises out of it instead of ending it: a denied
    enumeration must never be read as "finished, none found".
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\PCI") as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                if f"ven_{_NVIDIA_PCI_VENDOR}" in winreg.EnumKey(key, index).lower():
                    return True
    except (ImportError, OSError):
        return True
    return False


def _nvidia_in_sysfs() -> bool:
    """The PCI devices the kernel enumerated: one `vendor` file per device.

    The same ground truth as the registry read above, and the same rule: a
    directory that is not there, a list that comes back empty and a file that
    will not open are all "learned nothing", and that counts as present.
    """
    try:
        vendors = sorted(_SYSFS_PCI_DEVICES.glob("*/vendor"))
    except OSError:
        return True
    if not vendors:
        return True
    for vendor in vendors:
        try:
            found = vendor.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return True
        if found == f"0x{_NVIDIA_PCI_VENDOR}":
            return True
    return False


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


def memory() -> int | None:
    """Bytes of memory on CUDA device 0 as torch reports them, or None without
    a card.

    The number the Ollama model offer compares against (TASK-089.18): Robert's
    nominal 16 GB card reports 17,179,344,896 here, 15.9995 GiB, which is why
    that threshold is set in bytes and says its unit. Reading it goes through
    torch's lazy init and opens a CUDA context on device 0, the same cost
    `doctor.check_gpu_runtime` pays - so the setup engine asks only when it is
    building an offer, on a machine with no Ollama, and never from the web
    process (ADR-001).
    """
    if not cuda_available():
        return None
    import torch

    return int(torch.cuda.get_device_properties(0).total_memory)


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


# --- no quiet CPU fallback behind a card (TASK-092) ---------------------------------
#
# Robert's decision of 2026-09-22: a machine with NVIDIA hardware never moves
# to the CPU by itself. The job is refused with a sentence naming the driver,
# and one switch in Settings, off by default, is the way to say yes. A machine
# without NVIDIA hardware runs on the CPU as it always did (README.md), and so
# does one whose card somebody hid with CUDA_VISIBLE_DEVICES: setting that is a
# choice, and the doctor already says out loud when it is set (TASK-089.12).

SETTING_CPU_FALLBACK = "cpu_fallback"
"""The switch's `setting` row. Exactly `CPU_FALLBACK_ON` is on; a missing row,
"0" or anything else is off - no row, no fallback, the shape ADR-016 gives the
provider."""

CPU_FALLBACK_ON = "1"

CPU_FALLBACK_LABEL = "Transcribe on the CPU when the GPU is unavailable"
"""The switch's name, as Settings shows it and as the refusal quotes it."""

CPU_FALLBACK_WHERE = "Settings > This machine"
"""Where the switch is. tests/test_cpu_fallback.py holds it to the section
label in `scribe.web.settings.SECTIONS`, so a renamed section breaks a test
rather than the sentence."""


class GpuUnreachable(RuntimeError):
    """This machine has an NVIDIA card, CUDA cannot reach it, and nobody said
    the CPU will do. A refusal before any model is built; the runner files it
    as GPU_UNREACHABLE."""


REFUSAL = (
    "This machine has an NVIDIA card, but CUDA cannot reach it, so MyScribe did not "
    "fall back to the CPU (about thirty times slower). Check the NVIDIA driver with "
    "nvidia-smi; the driver must be newer than the CUDA runtime. To run on the CPU "
    f"anyway, turn on '{CPU_FALLBACK_LABEL}' in {CPU_FALLBACK_WHERE}."
)


def cpu_fallback_allowed(conn: "sqlite3.Connection") -> bool:
    """Is the switch on? A missing row is off."""
    from scribe import db

    with db.LOCK:
        row = conn.execute(
            "SELECT value FROM setting WHERE key=?", (SETTING_CPU_FALLBACK,)
        ).fetchone()
    return row is not None and row[0] == CPU_FALLBACK_ON


def _refuse_the_cpu(cpu_fallback: bool) -> None:
    """Raise `GpuUnreachable` when running on the CPU would hide a dead card.

    Asked only after CUDA has said no. In this order, cheapest first:
    the switch, the environment variable (membership: an empty value hides a
    card as surely as -1), macOS, and last the hardware probe. macOS has no
    CUDA at all and the probe answers True for an operating system it was not
    taught, so without that line every Mac without MLX or MPS would be refused;
    whether Metal deserves the same rule is left open (task text).
    """
    if cpu_fallback or "CUDA_VISIBLE_DEVICES" in os.environ:
        return
    if sys.platform == "darwin":
        return
    if nvidia_hardware_present():
        raise GpuUnreachable(REFUSAL)


def transcription_backend(*, cpu_fallback: bool = False) -> str:
    """``cuda``, ``mlx`` or ``cpu``: what `transcribe.load_model` builds by
    default. CUDA first because it is the measured fast path (ADR-004's
    numbers); MLX next because on the machines that have it there is no CUDA.
    ``cpu`` only where that hides no card: see `_refuse_the_cpu`."""
    if cuda_available():
        return "cuda"
    if mlx_available():
        return "mlx"
    _refuse_the_cpu(cpu_fallback)
    return "cpu"


def diarization_device(*, cpu_fallback: bool = False) -> str:
    """``cuda``, ``mps`` or ``cpu``: what `diarize.resolve_device` picks, under
    the same rule as transcription."""
    if cuda_available():
        return "cuda"
    if mps_available():
        return "mps"
    _refuse_the_cpu(cpu_fallback)
    return "cpu"


def describe() -> str:
    """One line for the doctor: what each stage will run on.

    Never raises: the doctor's accel check runs under --no-gpu too, and on the
    machine the refusal exists for it has to print the table, not a traceback.
    """
    try:
        return (
            f"transcription on {transcription_backend()}, diarization on {diarization_device()}"
        )
    except GpuUnreachable:
        return (
            "jobs refused: an NVIDIA card is present but CUDA cannot reach it, and "
            f"'{CPU_FALLBACK_LABEL}' is off"
        )
