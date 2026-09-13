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
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from scribe import accel, cuda_setup, db, jobs, paths
from scribe.ingest import urls  # version and age only; nothing here fetches a URL

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

DISK_FLOOR_GB = 10
"""Refuse to start work with less headroom than this; media plus model
downloads eat gigabytes and a disk-full mid-transcription is a corrupt job."""


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix_hint: str = ""
    optional: bool = False


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


def check_ffmpeg() -> Check:
    ok, line = _run(["ffmpeg", "-version"])
    return Check(
        name="ffmpeg",
        ok=ok,
        detail=line or "not found",
        fix_hint="" if ok else "Install ffmpeg and put it on PATH (winget install Gyan.FFmpeg).",
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


def check_disk_space(floor_gb: int = DISK_FLOOR_GB) -> Check:
    target = paths.DATA_DIR
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return Check(
            name="disk-space",
            ok=False,
            detail=str(exc),
            fix_hint=f"Could not measure free space at {probe}.",
        )
    free_gb = usage.free / 2**30
    ok = free_gb >= floor_gb
    return Check(
        name="disk-space",
        ok=ok,
        detail=f"{free_gb:.1f} GB free at {probe}",
        fix_hint="" if ok else f"Free up space: {floor_gb} GB is the working floor for media and models.",
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
from scribe.stages.diarize import local_weights_dir  # noqa: E402

SMOKE_CLIP = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "clip30.wav"

_PIN_HINT = "Install the locked stack: uv sync (on Windows it takes torch from the cu128 index)"


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
                fix_hint=None if (mlx and mps) else "uv sync (the mlx packages are locked for macOS)",
            )
        return Check(
            name="gpu-runtime",
            ok=False,
            detail=f"torch {torch.__version__} is a CPU-only build",
            fix_hint="Reinstall from the CUDA index: pip install torch --index-url https://download.pytorch.org/whl/cu128",
        )
    if not torch.cuda.is_available():
        return Check(
            name="gpu-runtime",
            ok=False,
            detail=f"torch {torch.__version__} (CUDA {torch.version.cuda}) cannot reach a device",
            fix_hint="Check the NVIDIA driver with nvidia-smi; the driver must be newer than the CUDA runtime.",
        )

    name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 2**30
    return Check(
        name="gpu-runtime",
        ok=True,
        detail=f"torch {torch.__version__} CUDA {torch.version.cuda} on {name} ({vram:.1f} GB)",
    )


def gpu_smoke(model_name: str = DEFAULT_MODEL, clip: Path | None = None) -> Check:
    """Transcribe a short clip on the GPU and record the timing.

    This is the check that matters: the DLL failure this guards against only
    appears on the first real compute call, long after the model has loaded.
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

    try:
        conn = db.connect()
        try:
            db.migrate(conn)
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
WEB_SAFE_CHECKS = tuple(check for check in CPU_CHECKS if check is not check_accelerators)


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


def checks(include_gpu: bool = True) -> list[Check]:
    """Run the checks. `include_gpu=False` keeps the unit suite off the card."""
    selected = CPU_CHECKS + (GPU_CHECKS if include_gpu else ())
    return [fn() for fn in selected]


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
        if c.ok:
            mark = "OK  "
        elif c.optional:
            mark = "SKIP"
        else:
            mark = "FAIL"
        lines.append(f"[{mark}] {c.name.ljust(width)}  {c.detail}")
        if not c.ok and c.fix_hint:
            lines.append(f"       {' ' * width}  -> {c.fix_hint}")
    required_failures = [c for c in results if not c.ok and not c.optional]
    lines.append("")
    if required_failures:
        lines.append(f"{len(required_failures)} required check(s) failed.")
    else:
        skipped = sum(1 for c in results if not c.ok and c.optional)
        suffix = f" ({skipped} optional check(s) not wired yet)" if skipped else ""
        lines.append(f"All required checks passed{suffix}.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # The doctor loads a model too, so it can meet the same modal box the
    # runner can. A check that hangs is worse than a check that fails.
    cuda_setup.silence_loader_dialogs()

    parser = argparse.ArgumentParser(prog="scribe.doctor", description="Check that this machine can run scribe.")
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="skip the GPU runtime and smoke checks (they load a model and transcribe a clip)",
    )
    args = parser.parse_args(argv)

    results = checks(include_gpu=not args.no_gpu)
    print(render(results))
    return 1 if any(not c.ok and not c.optional for c in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
