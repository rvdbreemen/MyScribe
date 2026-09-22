"""Environment gate: `python -m scribe.doctor`.

Answers one question — will this machine run the pipeline — before anything
tries to. Every check reports what it found and, when it fails, the exact
thing to do about it.

GPU checks are registered here but optional until the CUDA pin set is frozen
(plan Task 8); they become required once `gpu_smoke` transcribes a clip.

The settings page asks the same questions from a browser, with one
difference: the GPU checks load a model, and the web process never does
(ADR-001). So they are a job - type `doctor`, one stage, registered in
`scribe.runner.STAGES` - whose runner child runs `GPU_CHECKS` and stores the
result in the `doctor_last` setting for the page to show. The page also
lists the model weights on this machine (`installed_models`), measured and
never opened.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from scribe import accel, credentials, cuda_setup, db, jobs, paths
from scribe.ingest import urls  # version and age only; nothing here fetches a URL

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

DISK_FLOOR_GB = 10
"""Refuse to start work with less headroom than this; media plus model
downloads eat gigabytes and a disk-full mid-transcription is a corrupt job."""


class NotEnoughDisk(RuntimeError):
    """Free space where the data lands is below `DISK_FLOOR_GB`.

    A refusal, not a failure: nothing was attempted and nothing is broken.
    Its own class, so `runner._ERROR_CODES` can give it a code the board tells
    apart from a network error (`DOWNLOAD_FAILED`) and from a write that
    already hit a full volume (`DISK_FULL`).
    """


def disk_probe_path() -> Path:
    """Where free space is measured: the data directory, or its parent while
    the data directory does not exist yet.

    One definition, so the gate's advice and the guard's refusal can never end
    up being about two different volumes.
    """
    target = paths.DATA_DIR
    return target if target.exists() else target.parent


def free_disk_gb() -> float:
    """Free GiB at `disk_probe_path()`; raises OSError if it cannot measure."""
    return shutil.disk_usage(disk_probe_path()).free / 2**30


def require_disk_headroom() -> None:
    """Raise `NotEnoughDisk` below the floor; return quietly above it.

    Called by a stage that is about to write a lot, once per job rather than
    once per batch: a feed that fans out into five hundred downloads has to
    stop when the disk runs low, not when the queue empties.

    It takes no floor argument on purpose. A parameter is a second number
    waiting to drift away from the one the doctor advises about, and TASK-043
    exists because two numbers is exactly what Robert did not want.

    A volume that cannot be measured does not refuse: "we do not know" must
    not become "nothing may be imported on this machine". The gate already
    reports an unmeasurable volume as a red check, which is where that
    belongs.

    The message names the volume it measured, and promises nothing about any
    other: the model cache lives wherever Hugging Face puts it, which on this
    machine is a different drive from the data directory (`hf_cache_dir` and
    `disk_probe_path` genuinely disagree), and a first-run model download does
    not pass through here at all.
    """
    try:
        free = free_disk_gb()
    except OSError:
        return
    if free < DISK_FLOOR_GB:
        raise NotEnoughDisk(
            f"only {free:.1f} GB free at {disk_probe_path()}, and this app keeps "
            f"{DISK_FLOOR_GB} GB clear on the drive your recordings land on. "
            "Free up space and retry; nothing was downloaded."
        )


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix_hint: str = ""
    optional: bool = False
    tested: bool = True
    """False when the check was never run, with the reason in `detail`.

    A third state, because two were not enough for `scribe.setup --prove`: a
    required check nobody measured rendered FAIL, which reads as "this machine
    is broken" about a machine nothing was asked of (TASK-089.13). `ok` is
    still the verdict and `optional` still what the gate counts, so the exit
    rule needs no change - a required line that was not tested is not ok and
    exits 1 - and a `doctor_last` row written before this field still loads.
    """

    def __post_init__(self) -> None:
        # "Anything skipped reads 'not tested', never 'ok'" (TASK-089.13
        # criterion 1), made impossible rather than tested per caller.
        if self.ok and not self.tested:
            raise ValueError(f"{self.name}: a check that was not tested cannot be ok")


def _run(cmd: list[str]) -> tuple[bool, str]:
    """Run a command, return (ok, first line of output). Never raises."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (proc.stdout or proc.stderr).strip().splitlines()
    return proc.returncode == 0, out[0] if out else ""


def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 12)
    return Check(
        name="python",
        ok=ok,
        detail=f"{v.major}.{v.minor}.{v.micro} at {sys.executable}",
        fix_hint="" if ok else "Python 3.12 or newer is required; recreate .venv with a newer interpreter.",
    )


def check_sqlite() -> Check:
    version = sqlite3.sqlite_version
    new_enough = sqlite3.sqlite_version_info >= (3, 35, 0)

    fts5 = False
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        fts5 = True
    except sqlite3.OperationalError:
        fts5 = False
    finally:
        probe.close()

    ok = new_enough and fts5
    detail = f"{version}, FTS5 {'available' if fts5 else 'MISSING'}"
    hint = ""
    if not new_enough:
        hint = "SQLite 3.35+ is required for UPDATE ... RETURNING (the atomic job claim)."
    elif not fts5:
        hint = "This Python was built without FTS5; library search cannot work. Use a python.org build."
    return Check(name="sqlite", ok=ok, detail=detail, fix_hint=hint)


def _ffmpeg_hint() -> str:
    """The command that installs ffmpeg on *this* machine (README.md's table).

    One hint for three package managers was wrong on two of them: a Mac and a
    Linux box were both told to run winget.
    """
    if sys.platform == "win32":
        command = "winget install Gyan.FFmpeg"
    elif sys.platform == "darwin":
        command = "brew install ffmpeg"
    else:
        command = "apt install ffmpeg"
    return f"Install ffmpeg and put it on PATH ({command})."


def check_ffmpeg() -> Check:
    ok, line = _run(["ffmpeg", "-version"])
    return Check(
        name="ffmpeg",
        ok=ok,
        detail=line or "not found",
        fix_hint="" if ok else _ffmpeg_hint(),
    )


def check_ffprobe() -> Check:
    ok, line = _run(["ffprobe", "-version"])
    return Check(
        name="ffprobe",
        ok=ok,
        detail=line or "not found",
        fix_hint="" if ok else "ffprobe ships with ffmpeg; install ffmpeg and put it on PATH.",
    )


def check_data_dir_writable() -> Check:
    target = paths.DATA_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target, suffix=".probe", delete=True):
            pass
    except OSError as exc:
        return Check(
            name="data-dir",
            ok=False,
            detail=f"{target}: {exc}",
            fix_hint=f"Grant write access to {target}, or point SCRIBE_DATA_DIR at a writable location.",
        )
    return Check(name="data-dir", ok=True, detail=f"{target} writable")


def check_disk_space(floor_gb: int | None = None) -> Check:
    # Resolved on the call, not bound as a default: a default freezes the floor
    # at import, so moving DISK_FLOOR_GB would move the guard and leave the
    # advice behind - the two numbers TASK-043 exists to prevent.
    floor_gb = DISK_FLOOR_GB if floor_gb is None else floor_gb
    probe = disk_probe_path()
    try:
        free_gb = free_disk_gb()
    except OSError as exc:
        return Check(
            name="disk-space",
            ok=False,
            detail=str(exc),
            fix_hint=f"Could not measure free space at {probe}.",
        )
    ok = free_gb >= floor_gb
    return Check(
        name="disk-space",
        ok=ok,
        detail=f"{free_gb:.1f} GB free at {probe}",
        # The same promise as the guard's refusal, and no larger: this
        # measures the drive the recordings land on, and the model cache is
        # wherever Hugging Face put it - a different drive here.
        fix_hint="" if ok else f"Free up space: {floor_gb} GB is the working floor at {probe}.",
    )


def check_ytdlp() -> Check:
    """Is yt-dlp installed, and how old is the copy we have?

    Age is the whole point of this check. yt-dlp works by keeping up with sites
    that change their players without telling anyone, so a pin that was fine in
    March silently stops fetching YouTube in June - with no error anywhere
    except inside a failed job. Reporting the release date here is what lets
    somebody connect "my URL imports started failing" to "the tool is old".

    It never fails the gate. A stale yt-dlp still works on most sites, and
    everything else in this app - files, folders, the whole pipeline - does not
    care whether it is installed at all.
    """
    version = urls.installed_version()
    if version is None:
        return Check(
            name="yt-dlp",
            ok=False,
            optional=True,
            detail="not installed; importing media from a URL will not work",
            fix_hint="uv sync (yt-dlp is pinned in pyproject.toml).",
        )

    age = urls.age_days()
    age_text = "release date unknown" if age is None else f"{age} days old"
    detail = f"yt-dlp {version} ({age_text})"
    if urls.is_stale():
        # In the detail rather than the fix_hint, because the check is green:
        # render() only prints a fix hint for a check that failed, and this is
        # the sentence that has to be seen.
        detail += (
            " - out of date, which is the usual reason a download starts failing;"
            f" update it with: {urls.UPDATE_COMMAND}"
        )
    return Check(name="yt-dlp", ok=True, detail=detail)


def check_database() -> Check:
    try:
        conn = db.connect()
        try:
            db.migrate(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - any failure here is a red check, not a crash
        return Check(
            name="database",
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            fix_hint=f"Delete or move {paths.DB_PATH} if it is corrupt, then run doctor again.",
        )
    ok = version == db.SCHEMA_VERSION
    return Check(
        name="database",
        ok=ok,
        detail=f"schema v{version} at {paths.DB_PATH}",
        fix_hint="" if ok else f"Expected schema v{db.SCHEMA_VERSION}; migration did not complete.",
    )


# The pipeline owns the default model; doctor smoke-tests whatever the
# pipeline will actually load, so it imports the choice rather than repeating
# it (two spellings of one decision drift the day somebody changes the tier).
from scribe.stages.transcribe import DEFAULT_MODEL  # noqa: E402

# Likewise the diarize stage owns where a re-hosted pyannote pipeline lives;
# the model inventory lists that directory under the name the stage uses.
from scribe import env  # noqa: E402
from scribe.stages.diarize import local_weights_dir  # noqa: E402

SMOKE_CLIP = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "clip30.wav"

_PIN_HINT = "Install the locked stack: uv sync (on Windows it takes torch from the cu128 index)"

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


def check_gpu_runtime() -> Check:
    """Is there a CUDA torch, and does it see the card?

    The trap this catches: a dependency marker of `platform_machine == 'x86_64'`
    does not match Windows (which reports AMD64), so pip silently installs the
    CPU build. torch imports fine, `torch.version.cuda` is None, and everything
    runs 30x slower with no error anywhere.
    """
    try:
        cuda_setup.ensure_cuda_libs()
        import torch
    except ImportError as exc:
        return Check(name="gpu-runtime", ok=False, detail=f"torch not installed ({exc})", fix_hint=_PIN_HINT)

    if torch.version.cuda is None:
        if accel.is_apple_silicon():
            # No CUDA to be had here; what counts is Metal, twice over.
            mlx = accel.mlx_available()
            mps = accel.mps_available()
            return Check(
                name="gpu-runtime",
                ok=mlx and mps,
                detail=(
                    f"torch {torch.__version__} on Apple Silicon: "
                    f"MLX {'available' if mlx else 'missing'} for transcription, "
                    f"MPS {'available' if mps else 'missing'} for diarization"
                ),
                # "" and not None: this value is printed as JSON, where a null
                # is a shape every reader has to special-case.
                fix_hint="" if (mlx and mps) else "uv sync (the mlx packages are locked for macOS)",
            )
        # ADR-012's Must, and it does not soften for a machine without a card:
        # a CPU-only build on Windows or Linux means the lock was not honoured.
        # Only the advice changes - this environment has no pip.
        return Check(
            name="gpu-runtime",
            ok=False,
            detail=f"torch {torch.__version__} is a CPU-only build",
            fix_hint=_PIN_HINT,
        )
    if not torch.cuda.is_available():
        # Three machines land here and only one of them is broken. A card
        # hidden with CUDA_VISIBLE_DEVICES looks exactly like a driver that
        # stopped working, so the variable softens nothing; what decides is
        # whether this machine has NVIDIA hardware at all.
        hidden = "CUDA_VISIBLE_DEVICES" in os.environ
        if not hidden and not nvidia_hardware_present():
            # Information, not a failure: CPU transcription is a supported
            # mode (README.md), and telling a machine that never had a driver
            # to check one is advice it cannot follow. The shape of the Apple
            # Silicon branch above, and required all the same.
            return Check(
                name="gpu-runtime",
                ok=True,
                detail=(
                    f"torch {torch.__version__} (CUDA {torch.version.cuda}): "
                    "no NVIDIA GPU on this machine; transcription on cpu"
                ),
            )
        # Membership and not truthiness: an empty value hides every device as
        # surely as -1 does, and the reader has to be told which it is.
        hidden_by = (
            f"; CUDA_VISIBLE_DEVICES={os.environ['CUDA_VISIBLE_DEVICES']!r} is set here" if hidden else ""
        )
        return Check(
            name="gpu-runtime",
            ok=False,
            detail=f"torch {torch.__version__} (CUDA {torch.version.cuda}) cannot reach a device{hidden_by}",
            fix_hint="Check the NVIDIA driver with nvidia-smi; the driver must be newer than the CUDA runtime.",
        )

    name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 2**30
    return Check(
        name="gpu-runtime",
        ok=True,
        detail=f"torch {torch.__version__} CUDA {torch.version.cuda} on {name} ({vram:.1f} GB)",
    )


def gpu_smoke(model_name: str = DEFAULT_MODEL, clip: Path | None = None, *, record: bool = True) -> Check:
    """Transcribe a short clip on the GPU and record the timing.

    This is the check that matters: the DLL failure this guards against only
    appears on the first real compute call, long after the model has loaded.

    `record=False` skips the `stage_perf` row, for a caller that may not write
    to the library it is reporting on (`scribe.setup --prove`). The sample is
    telemetry under its own stage name, and one of it is worth less than a
    proof that leaves the library byte-identical (TASK-089.13 criterion 2).
    `python -m scribe.doctor` from a terminal still writes it.
    """
    clip = clip or SMOKE_CLIP
    if not clip.exists():
        return Check(
            name="gpu-smoke",
            ok=False,
            detail=f"clip missing: {clip}",
            fix_hint="Re-cut it: ffmpeg -i <any audio> -t 30 -ac 1 -ar 16000 tests/fixtures/clip30.wav",
        )

    # The backend the app itself would pick - CUDA, MLX on Apple Silicon, or
    # the CPU - through the same `load_model` the transcribe stage calls, so
    # what this measures is what a job gets. A CPU machine is slow here and
    # says so; that is the honest number.
    from scribe.stages import transcribe as transcribe_stage

    try:
        load_start = time.perf_counter()
        model, device, compute_type = transcribe_stage.load_model(model_name)
        load_seconds = time.perf_counter() - load_start

        run_start = time.perf_counter()
        segments, info = model.transcribe(
            transcribe_stage.read_wav(clip) if device == "mlx" else str(clip),
            word_timestamps=True,
            vad_filter=True,
        )
        words = sum(len(seg.words or []) for seg in segments)
        wall = time.perf_counter() - run_start
    except ImportError as exc:
        return Check(name="gpu-smoke", ok=False, detail=f"not installed ({exc})", fix_hint=_PIN_HINT)
    except Exception as exc:  # noqa: BLE001 - any GPU failure is a red check, not a crash
        return Check(
            name="gpu-smoke",
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            fix_hint="A missing cudnn_ops64_9.dll here means cuda_setup did not find torch/lib.",
        )

    if words < 10:
        return Check(
            name="gpu-smoke",
            ok=False,
            detail=f"only {words} words from {info.duration:.0f}s of audio",
            fix_hint="The clip may be silent or the model mismatched; check tests/fixtures/clip30.wav.",
        )

    if record:
        try:
            conn = db.connect()
            try:
                # Deliberately NOT stage "transcribe": measured on this machine, a
                # 30 s clip runs at ~1.1x realtime while a 5-minute one runs at
                # ~14.5x, because the first transcribe() call in a process pays for
                # CUDA kernel warmup and that fixed cost swamps a short clip.
                # Filing this sample as a transcribe timing would drag the rolling
                # median in eta_seconds() down and make every ETA in the app
                # absurdly pessimistic. It is kept under its own stage name so it
                # stays visible without poisoning the estimates.
                jobs.record_stage_perf(conn, "smoke", model_name, info.duration, wall)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - telemetry must never fail the check
            pass

    return Check(
        name="gpu-smoke",
        ok=True,
        detail=(
            f"{model_name} on {device}/{compute_type}: {words} words from {info.duration:.0f}s "
            f"in {wall:.1f}s (load {load_seconds:.1f}s; short-clip timing is warmup-dominated, "
            f"not a throughput measure)"
        ),
    )


def check_accelerators() -> Check:
    """What each stage will run on, as `scribe.accel` decides it. Always OK:
    this is information, and the gpu-runtime check is the one that judges.

    Kept out of the web process by WEB_SAFE_CHECKS below rather than by being
    filed under the GPU, so `doctor --no-gpu` still says what the machine
    would transcribe on (TASK-022)."""
    return Check(name="accel", ok=True, detail=accel.describe())


def check_gpu_smoke() -> Check:
    return gpu_smoke()


def check_diarization() -> Check:
    """Can this machine name who is speaking, without loading the pipeline?

    The gap this closes: on 2026-09-18 the doctor said "All required checks
    passed" on a machine where `speaker-diarization-community-1`, its 3.1
    fallback and the local directory were all unavailable, and the first
    transcribe job found that out at the diarize stage, an hour of audio later.
    Every other required check answers a question the pipeline needs; this one
    was simply missing.

    Deliberately *not* a load. `open_pipeline` pulls torch, pyannote and the
    weights - minutes, and a model in a process ADR-001 keeps free of them.
    What decides the answer is the same three routes `_no_weights_hint` lists,
    asked cheaply: a local pipeline directory, or a token the Hub will accept
    for the gated repo. A HEAD for the config the loader would fetch is one
    request and no weights.

    Optional, because diarization is a feature and not the app: a machine that
    can transcribe is usable. It is reported all the same, which is the whole
    point - "SKIP" with the reason beats a green card and a failed job.
    """
    return _diarization(_diarization_token)


def _diarization(token_of: Callable[[], str | None]) -> Check:
    """The check itself, with where the token comes from handed in.

    One argument, one seam: the read-only twin differs from `check_diarization`
    in nothing but how it opens the library to read a settings row, and two
    copies of these branches is how they would come to disagree.
    """
    from scribe.stages import diarize

    local = diarize.local_weights_dir()
    if (local / "config.yaml").exists():
        return Check(name="diarization", ok=True, detail=f"local pipeline at {local}")

    token = token_of()
    if not token:
        return Check(
            name="diarization",
            ok=False,
            optional=True,
            detail="no local pipeline and no Hugging Face token",
            fix_hint=_diarization_hint(local),
        )

    reachable, why = _gated_repo_reachable(diarize.DEFAULT_PIPELINE, token)
    if reachable:
        return Check(name="diarization", ok=True, detail=f"{diarize.DEFAULT_PIPELINE} reachable with this token")
    return Check(
        name="diarization",
        ok=False,
        optional=True,
        detail=f"{diarize.DEFAULT_PIPELINE}: {why}",
        fix_hint=_diarization_hint(local),
    )


def _diarization_token() -> str | None:
    """The token the diarize stage would use, the settings row included.

    `hf_token(None)` was this whole lookup, and None there means "no
    database": somebody who saved the token where this check's own fix hint
    told them to was then told by this check that there was no token. The
    library's database is opened for the question and closed again, the way
    `check_database` opens one; not having a database is no row and never an
    error, because this is a question about a credential.

    A database that will not open is not an answer about the token either:
    the environment is asked again without it, so a machine with HF_TOKEN
    plainly set is never told by this check that it has no token.
    """
    from scribe.stages import diarize

    try:
        with credentials.library_db() as conn:
            return diarize.hf_token(conn)
    except Exception:  # noqa: BLE001 - a credential lookup never fails a check
        pass
    try:
        return diarize.hf_token(None)
    except Exception:  # noqa: BLE001 - nor does the lookup without it
        return None


def _diarization_hint(local: "Path") -> str:
    from scribe.stages import diarize

    return (
        f"Accept the conditions at https://hf.co/{diarize.DEFAULT_PIPELINE} and store the token "
        f"as the '{diarize.SETTING_TOKEN}' setting or in HF_TOKEN, or put a pipeline directory "
        f"(a config.yaml and the checkpoints it names) at {local}. Transcription works without it; "
        "only speaker separation does not."
    )


def _gated_repo_reachable(repo: str, token: str) -> tuple[bool, str]:
    """Would the Hub serve this repo's config to this token? One HEAD, no weights.

    401 and 403 are the two answers that mean "not for you", and they are not
    the same problem: 401 is a token the Hub does not know, which is replaced,
    and 403 is a token it knows whose account never accepted the conditions,
    which is not. They shared one sentence - "the conditions are not accepted
    for this token" - and it sent somebody with an expired token to a
    conditions page they had already agreed to. Anything else (offline, DNS, a
    500) is not an answer about the token and says so rather than blaming it.
    """
    url = f"https://huggingface.co/{repo}/resolve/main/config.yaml"
    request = urllib.request.Request(url, method="HEAD", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed https host
            return 200 <= response.status < 300, str(response.status)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "HTTP 401 - this token was not accepted; it is missing, expired or another account's"
        if exc.code == 403:
            return False, f"HTTP 403 - this account has not accepted the conditions at hf.co/{repo}"
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - no network is not a verdict on the token
        return False, f"could not be asked ({exc})"


def check_models() -> Check:
    """Are the weights here, and if not, how much is still to come?

    The package ships without them (TASK-040.05), so on a fresh install this
    is the difference between "nothing works and I do not know why" and "1.6 GB
    to download, here is the command". Asked cheaply - existence and size, not
    a hash of 1.6 GB - because this renders on a card.

    Optional: a machine that has not downloaded them yet is not broken, it is
    new. It fails loudly enough to be read.

    Only the rows this platform and tier actually load are counted. On
    2026-09-22 this card said "1.6 GB still to download" on Robert's machine
    while the gpu-smoke two lines below transcribed with weights that were
    already in the hub cache: the missing entry was the Apple conversion, which
    nothing on Windows can open. `models.wanted_here` is now the one answer to
    that question and the plan reads the same one.

    The tier is the stored default rather than this library's setting. Reading
    that row *can* be done without writing - `setup.read_only()` opens the
    library `immutable=1` and measured that it creates no `-wal` and no `-shm`
    - but this check may not import `scribe.setup`: it renders on a settings
    page (WEB_SAFE_CHECKS, ADR-001) and setup pulls `diarize`, `ai_ui` and
    `transcribe_dialog` into the web process behind it. A second spelling of
    the immutable open, here, is the duplication this task exists to remove. So
    a machine set to the largest model is told about the default one; that is a
    smaller error than a card that demands weights nothing here can load, and
    it is written down in the task rather than left to be rediscovered.
    """
    from scribe import models

    rows = [row for row in models.status() if row["wanted"]]
    absent = [row for row in rows if not row["here"]]
    if not absent:
        return Check(name="models", ok=True, detail=f"{len(rows)} model(s) present")

    outstanding = sum(row["bytes"] for row in absent)
    # The short name is honest now that the rows are filtered: what is left is
    # a repository this machine loads, so the name reads as the weights the
    # user's own transcriptions use. Unfiltered, the split stripped the
    # `mlx-community/` that was the only clue it was the wrong platform.
    names = ", ".join(f"{row['repo'].split('/')[-1]} ({models.human(row['bytes'])})" for row in absent)
    gated = any(row["gated"] for row in absent)
    return Check(
        name="models",
        ok=False,
        optional=True,
        detail=f"{models.human(outstanding)} still to download: {names}",
        fix_hint=(
            "Run `python -m scribe.models --fetch`"
            + (", after saving a Hugging Face token (Settings) for the gated one." if gated else ".")
        ),
    )


def check_ollama() -> Check:
    """Is there an Ollama here, and what state is it in?

    Reported, never acted on. This is the detection half of ADR-017: an Ollama
    that is present is left alone in every state, so this line says what is
    true and stops - it starts nothing, pulls nothing and installs nothing.

    Optional whatever it finds, so it can never fail the gate: a machine with
    no Ollama is not broken, it just has no local AI, exactly as a machine with
    no yt-dlp cannot import from a URL. All five states are worth printing
    because each ends in a different sentence - "not installed" and "installed
    but stopped" used to be the same one - and only one of the five is a tick.

    `ollama_setup` is imported here and not at the top, the way `check_models`
    imports `scribe.models`: it pulls in `scribe.llm`, and the doctor is also a
    library that `scribe.runner` imports (ADR-001).
    """
    from scribe import ollama_setup

    found = ollama_setup.state()
    return Check(
        name="ollama",
        # READY and not `present`: the mark means "is this working", and
        # `[OK  ] ollama  installed at ... but not answering` contradicted the
        # sentence beside it. `present` is the right question for the install
        # offer (ADR-017) and the wrong one here. Optional in every state, so
        # the other four render [SKIP] and still fail nothing.
        ok=found.state == ollama_setup.READY,
        optional=True,
        detail=ollama_setup.describe(found),
        # `render()` prints a hint for every check that is not ok, and absent
        # is the only state where installing one is the answer: an Ollama that
        # is there is left alone (ADR-017), and the other sentences carry their
        # own fix - "start it", "ollama pull ..." - in `describe`.
        fix_hint=(
            "Install it from ollama.com/download if you want AI that never leaves this machine."
            if found.state == ollama_setup.ABSENT
            else ""
        ),
    )


# --- the same questions, asked of a library that may not change -----------------------
#
# `python -m scribe.setup --prove` reports on a library that may be live, and a
# proof that migrates it under an older app that is still serving is what
# TASK-089.13 criterion 2 forbids. Four checks write, so four have a twin here.
# Three were expected - the data-dir probe, the database and the smoke's
# `stage_perf` row - and the fourth was found by running them: `check_diarization`
# reads its token from the settings row through `credentials.library_db`, which
# opens with `db.connect` and sets `journal_mode`. They are plain zero-argument
# functions and not
# `functools.partial`: `CHECK_LABELS` is keyed on the function object, and a
# partial has no identity to key on.


def check_scratch_dir_writable() -> Check:
    """Can this process write at all - measured somewhere it may.

    `check_data_dir_writable` creates the data directory and a probe file in
    it. A proof may do neither, so the probe moves to a scratch directory the
    operating system hands out and takes away again. The detail names the
    directory it measured, so a green line about a temp folder can never be
    read as a green line about the library.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="myscribe-probe-") as scratch:
            with tempfile.NamedTemporaryFile(dir=scratch, suffix=".probe", delete=True):
                pass
            measured = scratch
    except OSError as exc:
        return Check(
            name="data-dir",
            ok=False,
            detail=f"no writable scratch directory: {exc}",
            fix_hint="Point TMP (or TMPDIR) at a writable location.",
        )
    return Check(name="data-dir", ok=True, detail=f"{measured} writable (a scratch directory, not the library)")


def check_database_read_only() -> Check:
    """The library's schema version, read without migrating it.

    `check_database` opens through `db.connect` - a `journal_mode` pragma,
    which is a write - and then migrates. Run from a proof after a `git pull`,
    with an older app still serving, that migrates the live library out from
    under it.

    A library one version behind is not broken: the app migrates it the next
    time it starts, and saying so is the answer. An absent one is not broken
    either - that is a first run.

    `scribe.setup` is imported here and not at the top, the way `check_models`
    imports `scribe.models`: setup pulls `diarize`, `ai_ui` and
    `transcribe_dialog` in behind it, and the doctor is a module the web
    process imports (ADR-001). This check is outside `WEB_SAFE_CHECKS` for
    that reason, and `setup.read_only` is the one immutable open in this
    repository rather than a second spelling of it.
    """
    from scribe import setup

    path = paths.DB_PATH
    if not path.exists():
        return Check(name="database", ok=True, detail=f"no library yet at {path}")

    version = None
    with setup.read_only(path) as conn:
        if conn is not None:
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
            except sqlite3.Error:
                version = None
    if version is None:
        return Check(
            name="database",
            ok=False,
            detail=f"{path} could not be opened read-only",
            fix_hint=f"Delete or move {path} if it is corrupt, then run the doctor again.",
        )
    if version > db.SCHEMA_VERSION:
        # Behind and ahead are not the same relaxation. `db.migrate` walks
        # `range(version + 1, SCHEMA_VERSION + 1)`, which is empty going down:
        # nothing migrates a library backwards, so this checkout would open a
        # schema it does not know. `check_database` fails any version that is
        # not this one, and the twin asks the same question - it only forgives
        # the direction the app itself repairs.
        return Check(
            name="database",
            ok=False,
            detail=f"schema v{version} at {path}, read-only: written by a newer MyScribe than this one",
            fix_hint=(
                f"This copy speaks schema v{db.SCHEMA_VERSION}. Update MyScribe, or point "
                "SCRIBE_DATA_DIR at the library this copy wrote."
            ),
        )
    behind = (
        ""
        if version == db.SCHEMA_VERSION
        else f"; the app migrates it to v{db.SCHEMA_VERSION} the next time it starts"
    )
    return Check(name="database", ok=True, detail=f"schema v{version} at {path}, read-only{behind}")


def check_diarization_read_only() -> Check:
    """The diarization check with the settings row read, not written to.

    `_diarization_token` asks `credentials.library_db`, which opens through
    `db.connect` - a `journal_mode` pragma, which is a write, on a library
    this caller promised not to touch. Nothing else about the check differs;
    the network HEAD is the same one request and no weights.
    """
    return _diarization(_diarization_token_read_only)


def _diarization_token_read_only() -> str | None:
    """`_diarization_token` through the immutable open.

    The same two routes in the same order - this library's settings row, then
    the environment - so a machine answers identically whichever mode asked.
    """
    from scribe import setup
    from scribe.stages import diarize

    try:
        with setup.read_only(paths.DB_PATH) as conn:
            token = diarize.hf_token(conn)
        if token:
            return token
    except Exception:  # noqa: BLE001 - a credential lookup never fails a check
        pass
    try:
        return diarize.hf_token(None)
    except Exception:  # noqa: BLE001 - nor does the lookup without it
        return None


def check_gpu_smoke_read_only() -> Check:
    """The smoke without its `stage_perf` row: see `gpu_smoke(record=...)`."""
    return gpu_smoke(record=False)


CPU_CHECKS = (
    check_python,
    check_sqlite,
    check_ffmpeg,
    check_ffprobe,
    check_ytdlp,
    check_data_dir_writable,
    check_disk_space,
    check_database,
    check_accelerators,
    check_diarization,
    check_models,
    check_ollama,
)

GPU_CHECKS = (
    check_gpu_runtime,
    check_gpu_smoke,
)

# The checks the WEB process may run, which is not the same question as
# whether a check touches the card (TASK-022).
#
# `check_accelerators` calls accel.describe(), which asks accel.cuda_available()
# whether there is a device, which does `import torch`. That is 687 modules and
# a model runtime inside the one process ADR-001 says must never hold one - and
# opening /settings was enough to do it, because the settings page renders a
# doctor panel and the panel ran the CPU checks in the request.
#
# The distinction is deliberately not folded into `include_gpu`. That flag means
# "do not load a model", and `python -m scribe.doctor --no-gpu` should still say
# what the machine would transcribe on: importing torch in the CLI costs a
# second and breaks nothing. Naming the real constraint separately keeps both
# answers right instead of trading one for the other.
WEB_SAFE_CHECKS = tuple(
    check
    for check in CPU_CHECKS
    if check not in (check_accelerators, check_diarization, check_ollama)
)
# `check_diarization` is out for the same reason as `check_accelerators`: it
# imports `scribe.stages.diarize`, and that module's import graph is the one
# ADR-001 keeps out of the web process. It also reaches the network, which a
# settings page render must not.
#
# `check_ollama` is out on a different ground, and the decision is deliberate
# rather than an oversight (TASK-089.06). Its loopback GET would be safe - the
# settings page already makes exactly that one, through `provider_rows` - but
# `chat_models()` falls back to one POST /api/show *per model* on a daemon too
# old to report capabilities, and a page render that quietly became nine
# requests is the shape of problem this tuple exists to prevent. The doctor's
# own command still runs it, which is where the question was asked.

CHECK_LABELS = {
    check_python: "python",
    check_sqlite: "sqlite",
    check_ffmpeg: "ffmpeg",
    check_ffprobe: "ffprobe",
    check_ytdlp: "yt-dlp",
    check_data_dir_writable: "data-dir",
    check_disk_space: "disk-space",
    check_database: "database",
    check_accelerators: "accel",
    check_diarization: "diarization",
    check_models: "models",
    check_ollama: "ollama",
    check_gpu_runtime: "gpu-runtime",
    check_gpu_smoke: "gpu-smoke",
    check_scratch_dir_writable: "data-dir",
    check_database_read_only: "database",
    check_diarization_read_only: "diarization",
    check_gpu_smoke_read_only: "gpu-smoke",
}
"""The name each registered check will report, known *before* it runs.

`checks()` announces a check as it starts, and at that moment there is no
result to read a name off. A test pins every entry against the name the check
does return, because a label that drifted would announce one thing and report
another.
"""

READ_ONLY_SUBSTITUTES = {
    check_data_dir_writable: check_scratch_dir_writable,
    check_database: check_database_read_only,
    check_diarization: check_diarization_read_only,
    check_gpu_smoke: check_gpu_smoke_read_only,
}
"""The four checks that write, each beside the twin that asks the same
question without writing. `checks(read_only=True)` swaps them in place, so
neither the order of the report nor the name of a line changes with the mode -
what changes is only where the writing happened."""

READ_ONLY_CHECKS = tuple(READ_ONLY_SUBSTITUTES.values())
"""Registered like any other check, so the label test covers them too."""

SKIP_MEANINGS = {
    "yt-dlp": "importing media from a link will not work",
    "diarization": "speaker separation not set up",
    "models": "weights not downloaded",
    "ollama": "no local AI on this machine",
}
"""What a SKIP costs, in words somebody can act on.

The closing line used to count the skipped checks and call them unwired,
which reads as unfinished work in this repository rather than as something
missing on the reader's own machine - and somebody who skipped the token was
told about the developer's backlog instead of about speaker separation. A
name with no entry here falls back to the name: a check added tomorrow must
not crash the summary.
"""


# --- the GPU checks as a job ---------------------------------------------------

JOB_TYPE = "doctor"
"""The job type that runs the GPU checks in a runner child - the one place a
model may be loaded (ADR-001), which is why the settings page cannot run them
itself."""

SETTING_LAST = "doctor_last"
"""The setting row holding the last doctor job's result, as JSON:
``{"ts": epoch, "job_id": int | null, "checks": [Check as a dict, ...]}``."""


class CheckFailed(RuntimeError):
    """A required check came back red inside the doctor job."""


def store_last_run(
    conn: sqlite3.Connection, checks: Sequence[Check], *, job_id: int | None = None
) -> None:
    """Remember ``checks`` as the last GPU run, replacing the previous one."""
    payload = {"ts": time.time(), "job_id": job_id, "checks": [asdict(c) for c in checks]}
    with db.LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            (SETTING_LAST, json.dumps(payload)),
        )
        conn.commit()


def last_run(conn: sqlite3.Connection) -> dict | None:
    """The stored result of the last doctor job: ``{ts, job_id, checks}`` with
    ``checks`` as Check objects. None when there is none yet, or when the row
    was edited into something this cannot read - a page that shows nothing is
    better than one that cannot load."""
    with db.LOCK:
        row = conn.execute(
            "SELECT value FROM setting WHERE key=?", (SETTING_LAST,)
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["value"])
        checks = [
            Check(
                name=str(c["name"]),
                ok=bool(c["ok"]),
                detail=str(c.get("detail", "")),
                fix_hint=str(c.get("fix_hint", "")),
                optional=bool(c.get("optional", False)),
                # Defaulted, so a row stored before `tested` existed still
                # loads: everything a doctor job ran was, by definition, run.
                tested=bool(c.get("tested", True)),
            )
            for c in payload["checks"]
        ]
        ts = float(payload.get("ts") or 0.0)
        job_id = payload.get("job_id")
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return {
        "ts": ts,
        "job_id": job_id if isinstance(job_id, int) and not isinstance(job_id, bool) else None,
        "checks": checks,
    }


def gpu_stage(ctx: RunnerContext) -> None:
    """The doctor job's one stage: run the GPU checks and store what they found.

    Stored before the verdict, so a red check still leaves its detail and fix
    hint where the settings page reads them; then the job fails, naming the
    check, which is what the jobs board shows. Progress is 0 and then 1: a
    model load followed by a 30 s clip has no honest middle to report.
    """
    ctx.report(0.0)
    results = [fn() for fn in GPU_CHECKS]
    store_last_run(ctx.conn, results, job_id=ctx.job["id"])
    ctx.report(1.0)
    failed = [c for c in results if not c.ok and not c.optional]
    if failed:
        raise CheckFailed("; ".join(f"{c.name}: {c.detail}" for c in failed))


# Ordered registry entry for job type "doctor" (consumed by runner.STAGES).
DOCTOR_STAGES: list[tuple[str, Callable]] = [("gpu-checks", gpu_stage)]


# --- the models on this machine ------------------------------------------------

# The hub cache folders this app's stages load from, as glob patterns over
# huggingface_hub's `models--<org>--<name>` layout. Whisper weights for
# faster-whisper come from more than one org (Systran, mobiuslabsgmbh), hence
# the leading wildcard; anything else in the cache is somebody else's.
MODEL_REPO_PATTERNS = ("models--*faster-whisper*", "models--pyannote*")


@dataclass(frozen=True)
class CachedModel:
    name: str  # "Systran/faster-whisper-large-v3", or "pyannote (local)"
    path: Path
    size_bytes: int
    source: str  # "hub" for the Hugging Face cache, "local" for MODELS_DIR


def hf_cache_dir() -> Path:
    """Where huggingface_hub keeps its downloads, by the library's own
    precedence: HF_HUB_CACHE, then HF_HOME/hub, then ~/.cache/huggingface/hub."""
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return Path(explicit)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _dir_size(root: Path) -> int:
    """Bytes on disk under ``root``. Links are counted as links, not as what
    they point at: a hub snapshot that links into blobs/ is one copy."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, filename)).st_size
            except OSError:
                continue
    return total


def installed_models(cache_dir: Path | None = None) -> list[CachedModel]:
    """The speech and speaker weights on this machine, measured, never opened.

    The hub cache entries matching `MODEL_REPO_PATTERNS`, then the re-hosted
    pyannote pipeline under MODELS_DIR if there is one - the copy the diarize
    stage tries first. An absent cache is an empty list, not an error.
    """
    cache = hf_cache_dir() if cache_dir is None else cache_dir
    found: list[CachedModel] = []
    if cache.is_dir():
        seen: set[Path] = set()
        for pattern in MODEL_REPO_PATTERNS:
            for repo in sorted(cache.glob(pattern)):
                if not repo.is_dir() or repo in seen:
                    continue
                seen.add(repo)
                name = repo.name.removeprefix("models--").replace("--", "/")
                found.append(CachedModel(name, repo, _dir_size(repo), "hub"))
    local = local_weights_dir()
    if local.is_dir():
        found.append(CachedModel("pyannote (local)", local, _dir_size(local), "local"))
    return found


def checks(
    include_gpu: bool = True,
    on_start: Callable[[str], None] | None = None,
    *,
    read_only: bool = False,
) -> list[Check]:
    """Run the checks. `include_gpu=False` keeps the unit suite off the card.

    `on_start` is called with a check's label just before it runs, so a caller
    can say what is happening. It matters for one check in particular: a cold
    `gpu-smoke` downloads 1.6 GB and transcribes a clip, and a terminal that
    prints nothing until the last check has finished reads as a hang.

    `read_only=True` swaps in `READ_ONLY_SUBSTITUTES`, for a caller reporting
    on a library it may not change - `python -m scribe.setup --prove`. Nothing
    else about the run differs, which is the point: the same questions, in the
    same order, under the same names.
    """
    selected = CPU_CHECKS + (GPU_CHECKS if include_gpu else ())
    if read_only:
        selected = tuple(READ_ONLY_SUBSTITUTES.get(fn, fn) for fn in selected)
    results = []
    for fn in selected:
        if on_start is not None:
            on_start(CHECK_LABELS.get(fn, fn.__name__))
        results.append(fn())
    return results


def web_checks() -> list[Check]:
    """The checks the web process may run (TASK-022).

    A function rather than a comprehension at the call site, so there is one
    seam to patch in a test and one place that answers "what may the web
    process ask" - the same reason `checks()` exists for the other callers.
    """
    return [check() for check in WEB_SAFE_CHECKS]


def render(results: list[Check]) -> str:
    width = max(len(c.name) for c in results)
    lines = []
    for c in results:
        # `tested` before `optional`: a check the caller gated away is not a
        # skip, which says it was asked and answered. It reads "not tested",
        # which is the word TASK-089.13 criterion 1 asks for.
        if not c.tested:
            mark = "----"
        elif c.ok:
            mark = "OK  "
        elif c.optional:
            mark = "SKIP"
        else:
            mark = "FAIL"
        lines.append(f"[{mark}] {c.name.ljust(width)}  {c.detail}")
        if not c.ok and c.fix_hint:
            lines.append(f"       {' ' * width}  -> {c.fix_hint}")
    required_failures = [c for c in results if not c.ok and not c.optional and c.tested]
    required_untested = [c for c in results if not c.tested and not c.optional]
    lines.append("")
    said = []
    if required_failures:
        said.append(f"{len(required_failures)} required check(s) failed")
    if required_untested:
        # Its own sentence, not folded into the failures: a machine nobody
        # measured is not a machine that came back broken, and the exit code
        # counts them the same only because neither one is a pass.
        said.append(f"{len(required_untested)} required check(s) not tested")
    closing = ", ".join(said) if said else "All required checks passed"
    # On both branches, not only the passing one: a machine that has no ffmpeg
    # still has to learn that speaker separation will not run either.
    skipped = [c for c in results if not c.ok and c.optional and c.tested]
    if skipped:
        closing += " (skipped: " + "; ".join(SKIP_MEANINGS.get(c.name, c.name) for c in skipped) + ")"
    lines.append(f"{closing}.")
    return "\n".join(lines)


def exit_code(results: list[Check]) -> int:
    """0 when every required check passed, 1 otherwise.

    One function because there are now two commands that end on this rule -
    `python -m scribe.doctor` and `python -m scribe.setup --prove` - and two
    spellings of "did this machine pass" is how they would come to disagree.
    A required check that was never tested is not ok, so it exits 1: nobody
    measured it, and a proof may not round that up to a pass.
    """
    return 1 if any(not c.ok and not c.optional for c in results) else 0


def _stderr_is_a_terminal() -> bool:
    """Is somebody watching this run? Never raises: under pythonw `sys.stderr`
    is None, and a stream that has been closed or replaced may answer with a
    ValueError instead of a boolean."""
    try:
        return bool(sys.stderr is not None and sys.stderr.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    # The doctor loads a model too, so it can meet the same modal box the
    # runner can. A check that hangs is worse than a check that fails.
    cuda_setup.silence_loader_dialogs()
    # The same `.env` the app reads (`scribe/__main__.py`). Without this the
    # doctor answers about a different machine than the one the app runs on:
    # the diarization check reported "no Hugging Face token" with a token
    # sitting in the file beside it, which is exactly the false green this
    # check exists to prevent.
    env.load_dotenv()

    parser = argparse.ArgumentParser(prog="scribe.doctor", description="Check that this machine can run scribe.")
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="skip the GPU runtime and smoke checks (they load a model and transcribe a clip)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the checks as JSON, one object per check, instead of the table",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="ask the same questions without writing: no migration, no probe file in the "
             "data directory, and no timing row from the smoke",
    )
    args = parser.parse_args(argv)

    def announce(label: str) -> None:
        print(f"checking {label}", file=sys.stderr, flush=True)

    # Progress goes to stderr, so `--json > file` stays something a program
    # can parse, and only to a terminal, so a redirected run keeps exactly the
    # report it had. Plain lines, no carriage returns and no ANSI: legacy
    # conhost is still what a lot of these machines open.
    watched = not args.json and _stderr_is_a_terminal()

    results = checks(
        include_gpu=not args.no_gpu,
        on_start=announce if watched else None,
        read_only=args.read_only,
    )
    if args.json:
        print(json.dumps([asdict(c) for c in results], indent=2))
    else:
        print(render(results))
    return exit_code(results)


if __name__ == "__main__":
    # `.env` may name the data directory, and this module imported
    # scribe.paths before anybody read the file: see paths.refresh(). Here and
    # not in main() - tests/test_doctor.py runs main() against paths of its
    # own, and a refresh in there would send it to the developer's library.
    env.bootstrap()
    paths.refresh()
    raise SystemExit(main())
