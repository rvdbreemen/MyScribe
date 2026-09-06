"""Per-job runner child: `python -m scribe.runner <job_id>`.

Opens its own DB connection, walks the stage registry for the job's
type, and delivers the terminal verdict itself (first verdict wins —
the supervisor's safety net only fires if this process dies without
one). Exit codes: 0 done, 1 failed, 2 cancelled.

Cancellation is cooperative: between stages the runner re-reads the
job's cancel_requested flag, and long-running stages poll ctx.cancelled()
themselves (see stages_fake.slow).
"""

import errno
import json
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from scribe import applog, cuda_setup, db, doctor, jobs, paths, stages_fake
from scribe.ingest import urls
from scribe.llm import privacy as llm_privacy
from scribe.llm.base import LlmError
# The registry by name, not the module: _run() binds a local named `stages`.
from scribe.stages import (
    TRANSCRIBE_STAGES, correct, llm_stage, prepare, probe, transcribe, url_stage,
)

# Minimum seconds between stage-progress writes from ctx.report().
REPORT_MIN_INTERVAL = 0.4

# Ordered (stage_name, fn) lists per job type. "fake" drives the tests;
# "transcribe" is the real pipeline, filled in task by task in Phase 2;
# "doctor" is the GPU checks and "llm" is a question about a transcript, both
# run here because neither a model nor a provider may be touched by the web
# process (ADR-001); "ingest_url" downloads a pasted link, here for the same
# reason plus a second one - a slow network must never block a page; "correct"
# re-applies the glossary to a transcript that already exists, which is a job
# only because the settings page queues one per media and a library-wide sweep
# has no business happening inside a request.
STAGES: dict[str, list[tuple[str, Callable]]] = {
    "fake": stages_fake.STAGES,
    "transcribe": TRANSCRIBE_STAGES,
    "doctor": doctor.DOCTOR_STAGES,
    llm_stage.JOB_TYPE: llm_stage.STAGES,
    url_stage.JOB_TYPE: url_stage.STAGES,
    correct.JOB_TYPE: correct.STAGES,
}

# Exception class -> error_code taxonomy. Walked along the exception's MRO, so
# the most specific class wins: PermissionError and FileNotFoundError are both
# OSErrors and both get their own code.
#
# FILE_MISSING is about the user's media - probe raises it by name when the file
# in the store is gone (probe.probe_media). One ambiguity worth stating: a
# missing *ffprobe or ffmpeg binary* raises the same class from subprocess and
# will wear the same code. The error_detail says which ("The system cannot find
# the file specified"), and `python -m scribe.doctor` is where a missing ffmpeg
# is supposed to be caught, long before a job runs.
_ERROR_CODES: dict[type[BaseException], str] = {
    RuntimeError: "RUNTIME",
    probe.NotMediaError: "FFMPEG_DECODE",
    # Decodes fine and holds no sound: a dead or wrong microphone, not a bad
    # file. Its own code because the remedy is different - record again after
    # fixing the input, rather than finding another copy of the file.
    probe.SilentAudioError: "SILENT_AUDIO",
    prepare.ConversionError: "FFMPEG_DECODE",
    FileNotFoundError: "FILE_MISSING",
    PermissionError: "FILE_LOCKED",
    doctor.CheckFailed: "CHECK_FAILED",
    # An llm job that asked a provider for something it would not give: no key,
    # a model id that does not exist, a prompt that did not fit, an answer that
    # was not the shape it promised. One code because one thing has to change -
    # the provider, the model or the question - and error_detail says which.
    LlmError: "LLM_FAILED",
    # Not an LlmError, and deliberately: this is a refusal, not a failure. It
    # gets its own code so the board can say "this recording is pinned private"
    # rather than making a user read it out of a message.
    llm_privacy.PrivacyRefused: "PRIVACY_REFUSED",
    # A URL import, split three ways because the three call for three different
    # actions: fix the link, find another copy, or fix the tool. The base class
    # covers DownloadFailed, and sits before RuntimeError in its MRO.
    urls.UnsupportedUrl: "UNSUPPORTED_URL",
    urls.Unavailable: "UNAVAILABLE",
    # A fourth, and it is neither a bad link nor a failure: the playlist is
    # simply bigger than this app will queue in one press of a button.
    urls.TooManyEntries: "TOO_MANY_ENTRIES",
    urls.UrlError: "DOWNLOAD_FAILED",
}

# What Windows calls a full disk. Python maps most ENOSPC conditions onto
# errno 28 already; this is the raw one, kept because a code that only
# sometimes fires is worse than no code at all.
_ERROR_DISK_FULL = 112


@dataclass
class RunnerContext:
    """What a stage function gets to work with."""

    conn: sqlite3.Connection
    job: dict
    params: dict
    report: Callable[[float], None]  # progress 0..1, throttled
    cancelled: Callable[[], bool]  # re-reads cancel_requested
    # Absolute path to the job's media in the store, resolved once here so no
    # stage has to know that media.store_path is relative to DATA_DIR.
    # None for a job that carries no media (the fake type, maintenance jobs).
    media_path: Path | None = None
    # Scratch shared by the stages of one job and nothing else: prepare leaves
    # the normalized wav here, transcribe the words, diarize the turns. It is
    # deliberately not the database - these are handoffs between stages of one
    # run, not facts anybody wants back after the job ends. A fresh dict per
    # context (default_factory, never a shared literal).
    state: dict = field(default_factory=dict)


def _is_disk_full(exc: BaseException) -> bool:
    """A full disk, whichever half of the OS reported it.

    Not in _ERROR_CODES because Python has no ENOSPC exception class: a full
    disk arrives as a plain OSError and only its errno says so, while an
    OSError with any other errno is nothing of the kind.
    """
    return isinstance(exc, OSError) and (
        exc.errno == errno.ENOSPC
        or getattr(exc, "winerror", None) == _ERROR_DISK_FULL
    )


def _is_cuda_oom(exc: BaseException) -> bool:
    """torch's out-of-memory, recognised without importing torch to find out.

    `torch.cuda.OutOfMemoryError` is a RuntimeError subclass, so putting it in
    the table above would work - at the price of two seconds of torch import in
    every runner, including the ones that never go near a GPU. Reading
    sys.modules instead is not a shortcut but the stronger statement: if torch
    was never imported it cannot have raised this, so there is nothing to miss.

    Known gap: CTranslate2 runs out of VRAM on its own account and reports it
    as a plain RuntimeError whose message mentions CUDA. That still lands as
    RUNTIME. Matching on message text is a guess this module has not needed to
    make yet; the day it does, it belongs here with a test.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    cuda = getattr(torch, "cuda", None)
    oom = getattr(cuda, "OutOfMemoryError", None) or getattr(
        torch, "OutOfMemoryError", None
    )
    return isinstance(oom, type) and isinstance(exc, oom)


def _error_code(exc: BaseException) -> str:
    # Both checks come before the table because neither is a question about the
    # exception's class: one reads an errno, the other a module that may not be
    # loaded.
    if _is_disk_full(exc):
        return "DISK_FULL"
    if _is_cuda_oom(exc):
        return "CUDA_OOM"
    for cls in type(exc).__mro__:
        if cls in _ERROR_CODES:
            return _ERROR_CODES[cls]
    return "RUNTIME"


def _media_duration(conn: sqlite3.Connection, job: dict) -> float:
    if job.get("media_id"):
        row = conn.execute(
            "SELECT duration FROM media WHERE id=?", (job["media_id"],)
        ).fetchone()
        if row is not None and row["duration"]:
            return float(row["duration"])
    return 0.0


def _media_path(conn: sqlite3.Connection, job: dict) -> Path | None:
    """Where this job's media actually sits on disk, or None if it has none."""
    if not job.get("media_id"):
        return None
    row = conn.execute(
        "SELECT store_path FROM media WHERE id=?", (job["media_id"],)
    ).fetchone()
    if row is None or not row["store_path"]:
        return None
    # store_path is relative so the whole data directory stays movable.
    return paths.DATA_DIR / row["store_path"]


def _run(conn: sqlite3.Connection, job_id: int) -> int:
    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return 1
    job = dict(row)

    stages = STAGES.get(job["type"])
    if stages is None:
        jobs.finish(
            conn,
            job_id,
            "failed",
            error_code="UNKNOWN_JOB_TYPE",
            error_detail=f"no stage registry for job type {job['type']!r}",
        )
        return 1

    params = json.loads(job["params_json"] or "{}")
    # Resolved up front so every stage of one job - probe and prepare included,
    # which never touch a model - files its timing under the same key the ETA
    # reads (transcribe.perf_model_for is that single definition).
    model = transcribe.perf_model_for(params)

    state = {"stage": "", "last_write": 0.0}

    def report(progress: float) -> None:
        now = time.monotonic()
        if now - state["last_write"] < REPORT_MIN_INTERVAL:
            return
        state["last_write"] = now
        jobs.set_stage(conn, job_id, state["stage"], float(progress))

    def cancelled() -> bool:
        r = conn.execute(
            "SELECT cancel_requested FROM job WHERE id=?", (job_id,)
        ).fetchone()
        return bool(r is not None and r["cancel_requested"])

    ctx = RunnerContext(conn, job, params, report, cancelled, _media_path(conn, job))

    try:
        for stage_name, fn in stages:
            if cancelled():
                jobs.finish(conn, job_id, "cancelled")
                return 2
            state["stage"] = stage_name
            state["last_write"] = time.monotonic()
            jobs.set_stage(conn, job_id, stage_name, 0.0)
            jobs.emit(conn, job_id, "stage", name=stage_name)
            applog.log("stage.begin", job=job_id, stage=stage_name)
            started = time.monotonic()
            fn(ctx)
            applog.log("stage.end", job=job_id, stage=stage_name,
                       seconds=round(time.monotonic() - started, 2))
            jobs.record_stage_perf(
                conn,
                stage_name,
                # The model that actually ran. A translate job substitutes
                # large-v3 for turbo, and filing that timing under turbo would
                # make every later turbo estimate four times too pessimistic.
                ctx.state.get("model") or model,
                # Read after the stage, not before the first one: a brand-new
                # file has no media.duration until probe writes it, and
                # eta_seconds discards every sample whose duration is 0 - so a
                # duration captured up front meant the ETA never calibrated.
                _media_duration(conn, job),
                time.monotonic() - started,
            )
        if cancelled():  # flag raised during the final stage
            jobs.finish(conn, job_id, "cancelled")
            return 2
        jobs.finish(conn, job_id, "done")
        return 0
    except transcribe.Cancelled:
        # A stage long enough to need interrupting says so by raising, not by
        # returning: the verdict is cancelled, not failed, and the difference
        # is what the user sees on the jobs board.
        jobs.finish(conn, job_id, "cancelled")
        return 2
    except Exception as exc:
        jobs.emit(conn, job_id, "error", trace=traceback.format_exc())
        applog.log("job.failed", level="error", job=job_id, stage=state["stage"],
                   error_code=_error_code(exc), error=str(exc))
        jobs.finish(
            conn,
            job_id,
            "failed",
            error_code=_error_code(exc),
            error_detail=str(exc),
        )
        return 1
    finally:
        # Every way out of the stage loop - done, failed, cancelled - ends with
        # the scratch gone. finalize already removes it on success; here it is
        # a no-op, and on every other path it is the only thing that does.
        paths.remove_job_work_dir(job_id)


def main(argv: list[str]) -> int:
    """Child entry point; argv is [job_id]."""
    # First, before anything can try to load a DLL: a child nobody is looking
    # at must not be able to stop on a modal box. See cuda_setup for what put
    # this here.
    cuda_setup.silence_loader_dialogs()

    if len(argv) != 1:
        print("usage: python -m scribe.runner <job_id>", file=sys.stderr)
        return 1
    try:
        job_id = int(argv[0])
    except ValueError:
        print(f"job id must be an integer, got {argv[0]!r}", file=sys.stderr)
        return 1

    applog.configure(f"runner:{job_id}")
    applog.log("runner.start", job=job_id, python=sys.version.split()[0])
    conn = db.connect()
    try:
        code = _run(conn, job_id)
    except BaseException as exc:
        # Anything that escaped _run's own handling is exactly the kind of
        # failure this log exists for: it would otherwise be a bare exit code.
        applog.log("runner.crashed", level="error", job=job_id, error=repr(exc),
                   trace=traceback.format_exc())
        raise
    finally:
        conn.close()
    applog.log("runner.exit", job=job_id, code=code)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
