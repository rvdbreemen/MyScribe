"""Supervisor: claim loop, spawn runner children, watch, kill, reconcile.

One daemon thread per app process. It claims queued jobs one at a time
(GPU serialization: exactly one runner child at a time), spawns
`python -m scribe.runner <job_id>`, stores the child's pid on the job
row, and watches it. The runner normally delivers its own verdict
(first verdict wins); the supervisor only steps in as a safety net:

- child exits without a verdict      -> failed / RUNNER_DIED
- cancel requested, child hangs past -> terminate()/kill(), then
  the kill grace (10 s default)         cancelled

reconcile() runs at startup: any job still marked running whose pid is
dead or absent belongs to a previous app life and becomes interrupted
(one-click retry later, in the UI phase). It runs again whenever the loop
finds nothing to claim, because a claim is refused while any row says
running (jobs.claim_next, TASK-070): a runner that died after startup, or
one that outlived a stopped app and then died, would otherwise hold the
queue until the next restart. A live orphan holds it, on purpose - that is
the one runner at a time.
"""

import os
import subprocess
from datetime import datetime
import sys
import threading
import time
import traceback
from pathlib import Path

from scribe import applog, db, jobs, paths

# Seconds a cancel-requested child gets to honour the cooperative flag
# before terminate()/kill() — pyannote has no interruption point.
KILL_GRACE_SECONDS = 10.0

# How often the watch loop polls the child and the cancel flag.
_WATCH_POLL_SECONDS = 0.1

# Seconds to wait after terminate()/kill() for the process to go away.
_KILL_WAIT_SECONDS = 5.0

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
else:  # pragma: no cover - Windows is the platform; POSIX kept for dev boxes
    import os
    import signal


def pid_alive(pid: int | None) -> bool:
    """Is a process with this pid still running? psutil-free.

    Windows: OpenProcess + GetExitCodeProcess == STILL_ACTIVE (a handle
    to an exited process can still open, so the exit code is what
    decides). POSIX: os.kill(pid, 0).
    """
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:  # pragma: no cover
        try:
            os.kill(int(pid), 0)
        except OSError:
            return False
        return True


def process_started_at(pid: int | None) -> float | None:
    """When the process holding `pid` started, as unix time, or None.

    None means "cannot tell", never "it is young": every caller must treat an
    unanswerable question as no evidence and leave the row alone.

    Every platform can answer this, which the first version of TASK-065 did not
    believe: Windows has GetProcessTimes, Linux has `/proc/<pid>/stat` field 22
    against the boot time in `/proc/stat`, and macOS has `ps -o lstart=`. Only
    Windows was implemented, so on a Mac and on Linux the pid-recycling guard
    never fired at all - which is exactly what
    `test_reconcile_flips_a_job_whose_pid_belongs_to_a_younger_process` has
    been red about since it was written, on every machine that is not Windows.
    """
    if not pid or pid <= 0:
        return None
    if sys.platform == "linux":
        return _linux_process_started_at(int(pid))
    if sys.platform == "darwin":
        return _darwin_process_started_at(int(pid))
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        created = ctypes.wintypes.FILETIME()
        unused = ctypes.wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(unused),
            ctypes.byref(unused),
            ctypes.byref(unused),
        )
        if not ok:
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        # FILETIME counts 100-nanosecond intervals since 1601-01-01.
        return ticks / 1e7 - 11644473600
    finally:
        kernel32.CloseHandle(handle)


def _linux_process_started_at(pid: int) -> float | None:
    """Field 22 of `/proc/<pid>/stat`, in ticks since boot, plus the boot time.

    The comm field can hold spaces and parentheses - a process is free to call
    itself `(evil) 1 2 3` - so the fields are counted from the *last* `)`,
    which is the only place the kernel's format is unambiguous.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        after_comm = raw[raw.rindex(")") + 1:].split()
        ticks = int(after_comm[19])  # field 22 overall: state is field 3
        hertz = os.sysconf("SC_CLK_TCK")
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1]) + ticks / float(hertz)
    except Exception:  # noqa: BLE001 - an unreadable /proc is "cannot tell"
        return None
    return None


def _darwin_process_started_at(pid: int) -> float | None:
    """`ps -o lstart=`, which macOS has and `etimes` is not.

    `LC_ALL=C` because the format is the locale's otherwise, and this has to
    parse on a machine set to any language. An absolute stamp rather than
    `etime`, so nothing has to be subtracted from a clock that may have moved.
    """
    try:
        out = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
            env={"LC_ALL": "C", "PATH": "/bin:/usr/bin"},
        )
        stamp = " ".join(out.stdout.split())
        if out.returncode != 0 or not stamp:
            return None
        return datetime.strptime(stamp, "%a %b %d %H:%M:%S %Y").timestamp()
    except Exception:  # noqa: BLE001 - no ps, a refusal, an unexpected format
        return None


# A process that started this long after the job may still be its runner: the
# two clocks are the same one here, but a claim and a spawn are not atomic.
_PID_AGE_SLACK_SECONDS = 60.0


def _pid_is_younger_than_job(row) -> bool:
    """Did the process holding this row's pid start after the job did?

    Only a yes is an answer. No `started_at`, no readable process time, or a
    platform that cannot say (POSIX) all return False, which leaves the row
    exactly where the bare-pid check put it. The slack is there because the
    claim and the spawn are two statements, not one.
    """
    started_at = row["started_at"]
    if started_at is None:
        return False
    process_start = process_started_at(row["pid"])
    if process_start is None:
        return False
    return process_start > float(started_at) + _PID_AGE_SLACK_SECONDS


def _kill_tree(proc: subprocess.Popen) -> None:
    """End the runner and everything it started, then wait for the runner.

    A runner blocked inside ffmpeg never reads the cancel flag, and ending the
    runner alone left that ffmpeg converting on (TASK-069). The launcher
    already does this for the app as a whole; this is the same shape for one
    job: on Windows taskkill walks the tree the process group defines, on
    POSIX the session gets SIGTERM and then SIGKILL for stragglers.
    """
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            proc.wait(timeout=_KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=_KILL_WAIT_SECONDS)
        return
    try:  # pragma: no cover - POSIX
        group = os.getpgid(proc.pid)
    except ProcessLookupError:
        proc.wait(timeout=_KILL_WAIT_SECONDS)
        return
    os.killpg(group, signal.SIGTERM)
    try:
        proc.wait(timeout=_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=_KILL_WAIT_SECONDS)


def reconcile(conn) -> int:
    """Flip orphaned running jobs (dead or absent pid) to interrupted.

    Run at startup, before the supervisor starts. Returns how many jobs
    were flipped.

    Two things a bare pid cannot tell us, both found in the 2026-09-16 review:

    * **No pid yet is not a dead pid.** `claim_next` publishes a job as
      running with `pid=NULL` and the supervisor writes the child's pid a
      moment later. A reconcile landing in that gap used to declare a job dead
      that was about to start - and the runner's own verdict is then refused,
      because `finish` only moves a row that is still running (TASK-064).
    * **A pid can be somebody else's.** Windows hands out pids again after a
      reboot, so a job interrupted by a power cut can find its pid held by an
      unrelated process and sit on running for ever. A process that started
      well after the job did cannot be that job's runner (TASK-065).
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT id, pid, started_at FROM job WHERE status='running'"
        ).fetchall()
    count = 0
    for row in rows:
        if row["pid"] is None and row["started_at"] is not None:
            # Claimed, not yet spawned: the supervisor owns this row and will
            # write the pid in a moment. `claim_next` (jobs.py:135) is the only
            # thing that sets a job running, and it always stamps started_at -
            # so a running row with neither pid nor start time is not a job in
            # that gap but a leftover, and keeps the old treatment.
            continue
        if pid_alive(row["pid"]) and not _pid_is_younger_than_job(row):
            continue
        with db.LOCK:
            cur = conn.execute(
                "UPDATE job SET status='interrupted', finished_at=?"
                " WHERE id=? AND status='running'",
                (time.time(), row["id"]),
            )
            conn.commit()
            count += cur.rowcount
    return count


# Where a runner child's stderr lands while it runs. Kept when non-empty, so a
# crash that the app log only quotes the tail of can still be read in full.
def _runner_stderr_path(job_id: int) -> Path:
    return paths.LOGS_DIR / f"runner-{job_id}.stderr"


def _read_tail(path: Path, limit: int = 4000) -> str:
    """The last `limit` bytes, by seeking - a child that looped on a warning
    can leave megabytes, and the app log wants a paragraph of it."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", "replace").strip()


# How long a kept stderr file stays. Kept at all because a crash the app log
# only quotes the tail of can be read in full here; not kept forever because
# CUDA and ctranslate2 warn on every start, and one file per job with no
# cleaner is a directory nobody can find anything in.
STDERR_KEEP_SECONDS = 7 * 24 * 3600


def sweep_stderr(older_than: float = STDERR_KEEP_SECONDS) -> int:
    """Remove runner-<job>.stderr files older than `older_than` seconds;
    returns how many went. Called at startup next to reconcile()."""
    removed = 0
    cutoff = time.time() - older_than
    try:
        candidates = list(paths.LOGS_DIR.glob("runner-*.stderr"))
    except OSError:
        return 0
    for file in candidates:
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink()
                removed += 1
        except OSError:
            continue
    return removed


class Supervisor:
    """Claim-spawn-watch loop on a daemon thread.

    runner_cmd overrides the default `[sys.executable, -m, scribe.runner]`
    prefix so tests can point at a scripted fake runner; the job id is
    always appended as the last argument.
    """

    def __init__(
        self,
        db_path: str | Path,
        poll_interval: float = 1.0,
        runner_cmd: list[str] | None = None,
        kill_grace: float = KILL_GRACE_SECONDS,
    ) -> None:
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.runner_cmd = runner_cmd
        self.kill_grace = kill_grace
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the claim loop on a daemon thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="scribe-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the loop to exit and join it. A still-running child is left
        alone; the next startup's reconcile() marks its job interrupted.

        A join that runs out of time is said on stderr and the thread is
        *kept*, the way `watching.Watcher.stop` keeps its own (TASK-073).
        The loop cannot see the stop event while `_watch` is inside a kill -
        terminate, then up to two waits of `_KILL_WAIT_SECONDS` - so a
        shutdown that lands mid-cancel comes back here with the loop still
        running. Dropping the handle either way made that indistinguishable
        from a clean stop, and `start()` would then begin a second loop
        beside the first in the same process.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout)
        if thread.is_alive():
            print(
                f"scribe: the supervisor thread is still running {timeout:g}s after"
                " being asked to stop; it is most likely ending a cancelled runner"
                " and will finish on its own.",
                file=sys.stderr,
                flush=True,
            )
            return
        self._thread = None

    # --- internals ------------------------------------------------------------

    def _loop(self) -> None:
        applog.configure("supervisor", this_thread_only=True)
        conn = db.connect(self.db_path)
        try:
            while not self._stop_event.is_set():
                try:
                    job = jobs.claim_next(conn)
                    if job is None:
                        # Nothing queued, or something running. If that
                        # something is a row whose runner is gone, the claim
                        # would stay refused for ever; reconcile flips it.
                        flipped = reconcile(conn)
                        if flipped:
                            applog.log("supervisor.reconciled", level="warn", flipped=flipped)
                        self._stop_event.wait(self.poll_interval)
                        continue
                    self._run_one(conn, job)
                except Exception:  # keep the supervisor alive on transient errors
                    traceback.print_exc()
                    self._stop_event.wait(self.poll_interval)
        finally:
            conn.close()

    def _spawn(self, job_id: int) -> subprocess.Popen:
        prefix = (
            list(self.runner_cmd)
            if self.runner_cmd
            else [sys.executable, "-m", "scribe.runner"]
        )
        # Its own process group, so a cancel can reach what the runner starts
        # underneath it - prepare's ffmpeg above all (TASK-069). The same
        # flags the launcher uses for the app: on Windows a group for
        # taskkill /T to walk, on POSIX a session for killpg.
        creationflags = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        )
        session = {} if sys.platform == "win32" else {"start_new_session": True}
        # The child's stderr goes to a file, not a pipe and not nowhere. A pipe
        # nobody drains deadlocks a chatty child; nowhere is where the one
        # sentence explaining a DLL that would not load used to go. The file
        # is read back when the child exits and its tail lands in the app log.
        stderr_path = _runner_stderr_path(job_id)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        errors = open(stderr_path, "wb")
        try:
            return subprocess.Popen(
                prefix + [str(job_id)], creationflags=creationflags,
                stdout=subprocess.DEVNULL, stderr=errors, **session,
            )
        finally:
            errors.close()  # the child holds its own handle

    def _run_one(self, conn, job: dict) -> None:
        job_id = job["id"]
        applog.log("job.claimed", job=job_id, type=job.get("type"), media=job.get("media_id"))
        try:
            proc = self._spawn(job_id)
        except OSError as exc:
            applog.log("runner.spawn_failed", level="error", job=job_id, error=str(exc))
            try:
                _runner_stderr_path(job_id).unlink()  # opened, never written
            except OSError:
                pass
            jobs.finish(
                conn,
                job_id,
                "failed",
                error_code="RUNNER_DIED",
                error_detail=f"could not spawn runner: {exc}",
            )
            return
        with db.LOCK:
            conn.execute("UPDATE job SET pid=? WHERE id=?", (proc.pid, job_id))
            conn.commit()
        applog.log("runner.spawned", job=job_id, pid=proc.pid)
        started = time.monotonic()
        self._watch(conn, job_id, proc)
        self._report_exit(conn, job_id, proc, time.monotonic() - started)

    def _report_exit(self, conn, job_id: int, proc: subprocess.Popen, seconds: float) -> None:
        """One line per runner exit, with the verdict the row now carries and
        whatever the child wrote to stderr. A child that died before its first
        stage wrote its reason there and nowhere else."""
        if proc.returncode is None:
            # The app is shutting down with the child still running. Its
            # stderr file is left for the next start's sweep: the child may
            # still be writing to it, and reconcile() will mark the job.
            applog.log("runner.abandoned", level="warn", job=job_id, pid=proc.pid)
            return
        with db.LOCK:
            row = conn.execute(
                "SELECT status, error_code FROM job WHERE id=?", (job_id,)
            ).fetchone()
        stderr_tail = _read_tail(_runner_stderr_path(job_id))
        applog.log(
            "runner.exited",
            level="error" if proc.returncode not in (0, 2) else "info",
            job=job_id, pid=proc.pid, code=proc.returncode, seconds=round(seconds, 1),
            status=row["status"] if row else None,
            error_code=row["error_code"] if row else None,
            stderr=stderr_tail or None,
        )
        if not stderr_tail:
            try:
                _runner_stderr_path(job_id).unlink()
            except OSError:
                pass

    def _cancel_requested(self, conn, job_id: int) -> bool:
        with db.LOCK:
            row = conn.execute(
                "SELECT cancel_requested FROM job WHERE id=?", (job_id,)
            ).fetchone()
        return bool(row is not None and row["cancel_requested"])

    def _watch(self, conn, job_id: int, proc: subprocess.Popen) -> None:
        cancel_seen: float | None = None
        while proc.poll() is None:
            if self._stop_event.is_set():
                return  # app shutting down; reconcile() cleans up next boot
            if cancel_seen is None and self._cancel_requested(conn, job_id):
                cancel_seen = time.monotonic()
            if (
                cancel_seen is not None
                and time.monotonic() - cancel_seen >= self.kill_grace
            ):
                _kill_tree(proc)
                # The runner's own finally removes this on every way out it
                # lives to see; a kill is the one it does not (TASK-069).
                paths.remove_job_work_dir(job_id)
                break
            time.sleep(_WATCH_POLL_SECONDS)

        # Child is gone. If it delivered no verdict, deliver one for it.
        with db.LOCK:
            row = conn.execute(
                "SELECT status, cancel_requested FROM job WHERE id=?", (job_id,)
            ).fetchone()
        if row is None or row["status"] != "running":
            return  # runner (or cancel route) already delivered the verdict
        if row["cancel_requested"]:
            jobs.finish(conn, job_id, "cancelled")
        else:
            jobs.finish(
                conn,
                job_id,
                "failed",
                error_code="RUNNER_DIED",
                error_detail=(
                    f"runner exited with code {proc.returncode} without a verdict"
                ),
            )
