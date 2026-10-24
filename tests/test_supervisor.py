import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from scribe import db, jobs, paths, supervisor


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


def _row(conn, job_id):
    return conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()


def _wait_for(predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _script_cmd(tmp_path, name, body):
    """Write a tiny scripted fake runner and return its runner_cmd prefix."""
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


@pytest.fixture
def ok_runner_cmd(tmp_path, db_path):
    """Fake runner: writes the done verdict itself, then exits 0."""
    return _script_cmd(
        tmp_path,
        "ok_runner.py",
        f"""\
import sqlite3, sys, time
conn = sqlite3.connect({str(db_path)!r})
conn.execute(
    "UPDATE job SET status='done', finished_at=? WHERE id=? AND status IN ('running','queued')",
    (time.time(), int(sys.argv[1])),
)
conn.commit()
conn.close()
""",
    )


@pytest.fixture
def dying_runner_cmd(tmp_path):
    """Fake runner: exits 1 without ever delivering a verdict."""
    return _script_cmd(tmp_path, "dying_runner.py", "raise SystemExit(1)\n")


@pytest.fixture
def sleeping_runner_cmd(tmp_path):
    """Fake runner: hangs (ignores the cancel flag) until killed."""
    return _script_cmd(
        tmp_path, "sleeping_runner.py", "import time\ntime.sleep(30)\n"
    )




# --- happy path ---------------------------------------------------------------


def test_job_goes_queued_running_done_via_scripted_runner(
    conn, db_path, ok_runner_cmd
):
    job_id = jobs.enqueue(conn, "fake")
    assert _row(conn, job_id)["status"] == "queued"

    sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=ok_runner_cmd)
    sup.start()
    try:
        assert _wait_for(lambda: _row(conn, job_id)["status"] == "done"), (
            "job never reached done"
        )
        # the supervisor stored the child's pid on the row before watching it
        assert _wait_for(lambda: _row(conn, job_id)["pid"] is not None, timeout=2.0)
    finally:
        sup.stop()


# --- safety net ---------------------------------------------------------------


def test_runner_death_without_verdict_marks_failed_runner_died(
    conn, db_path, dying_runner_cmd
):
    job_id = jobs.enqueue(conn, "fake")

    sup = supervisor.Supervisor(
        db_path, poll_interval=0.05, runner_cmd=dying_runner_cmd
    )
    sup.start()
    try:
        assert _wait_for(lambda: _row(conn, job_id)["status"] == "failed"), (
            "safety net never fired"
        )
    finally:
        sup.stop()

    row = _row(conn, job_id)
    assert row["error_code"] == "RUNNER_DIED"
    assert row["finished_at"] is not None


# --- reconcile and the pid ----------------------------------------------------


def test_reconcile_leaves_a_just_claimed_job_alone(conn):
    """TASK-064: claim_next publishes 'running' with pid=NULL.

    The supervisor writes the child's pid a moment later (supervisor.py:254).
    In that gap the row says running and carries no pid, and pid_alive(None)
    is False - so a reconcile running right then declares a job dead that is
    about to start, or is already decoding. The runner's own verdict is then
    refused, because finish() only moves a row that is still 'running'.
    """
    jobs.enqueue(conn, "fake")
    claimed = jobs.claim_next(conn)

    assert claimed is not None and claimed["pid"] is None
    supervisor.reconcile(conn)

    assert _row(conn, claimed["id"])["status"] == "running"


def test_reconcile_flips_a_job_whose_pid_belongs_to_a_younger_process(conn):
    """TASK-065: a bare pid is not proof the job's own child is alive.

    Windows hands out pids again after a reboot, so a job interrupted by a
    power cut can find its pid held by something unrelated - and reconcile,
    seeing it alive, leaves the row on 'running' for ever. A process that
    started *after* the job did cannot be that job's runner.
    """
    job_id = jobs.enqueue(conn, "fake")
    long_ago = time.time() - 3600
    with db.LOCK:
        conn.execute(
            "UPDATE job SET status='running', started_at=?, pid=? WHERE id=?",
            (long_ago, os.getpid(), job_id),  # this process started just now
        )
        conn.commit()

    supervisor.reconcile(conn)

    assert _row(conn, job_id)["status"] == "interrupted"


# --- stopping ---------------------------------------------------------------------


def test_a_stop_that_could_not_join_says_so_and_keeps_the_thread(db_path, capsys, monkeypatch):
    """TASK-073: the loop cannot see the stop event while _watch is inside a
    kill - terminate, then up to two waits of _KILL_WAIT_SECONDS - so a
    shutdown that lands mid-cancel returns from stop() with the loop still
    running. Dropping the handle either way made that indistinguishable from
    a clean stop, and start() would begin a second loop beside the first.
    Same rule as watching.Watcher.stop(): say so, keep it."""
    monkeypatch.setattr(supervisor, "STOP_JOIN_SECONDS", 0.05)  # every stop joins this long (ADR-032)
    sup = supervisor.Supervisor(db_path, poll_interval=0.05)
    stuck = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    stuck.start()
    sup._thread = stuck

    sup.stop(timeout=0.05)

    assert sup._thread is stuck, "a thread that never stopped was forgotten"
    assert "still running 0.05s after" in capsys.readouterr().err
    sup.start()
    assert sup._thread is stuck, "a second loop was started beside the first"


def test_a_stop_that_joins_in_time_drops_the_handle_and_start_runs_a_fresh_loop(db_path):
    sup = supervisor.Supervisor(db_path, poll_interval=0.05)
    sup.start()
    first = sup._thread

    sup.stop(timeout=5.0)

    assert sup._thread is None
    assert not first.is_alive()
    sup.start()
    try:
        assert sup._thread is not first and sup._thread.is_alive()
    finally:
        sup.stop()


# --- one runner at a time, across lives and instances --------------------------------


def _left_running_by_a_previous_life(conn, job_id, pid):
    """A row a stopped app left behind: running, with the pid it had."""
    with db.LOCK:
        conn.execute(
            "UPDATE job SET status='running', started_at=?, pid=? WHERE id=?",
            (time.time(), pid, job_id),
        )
        conn.commit()


def test_a_dead_orphan_from_a_previous_life_does_not_hold_the_queue(
    conn, db_path, ok_runner_cmd
):
    """TASK-070, the half that must not starve: a claim is refused while a
    row says running, so a runner that died after the startup reconcile ran
    would block the queue until the next restart. The loop reconciles when it
    finds nothing to claim, flips the dead one, and claims."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    orphan = jobs.enqueue(conn, "fake")
    _left_running_by_a_previous_life(conn, orphan, dead.pid)
    job_id = jobs.enqueue(conn, "fake")

    sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=ok_runner_cmd)
    sup.start()
    try:
        assert _wait_for(lambda: _row(conn, job_id)["status"] == "done"), (
            "a dead orphan held the queue"
        )
    finally:
        sup.stop()

    assert _row(conn, orphan)["status"] == "interrupted"


def test_a_live_orphan_from_a_previous_life_holds_the_queue_until_it_ends(
    conn, db_path, ok_runner_cmd
):
    """TASK-070, the half the finding is about: stop() leaves a running child
    alone, the next life's reconcile keeps its row because the pid is alive,
    and the new supervisor claimed the next job beside it - two runners on one
    card. Now the claim waits for the row to leave running."""
    # Shaped like a runner: `_spawn` starts each in a session of its own.
    session = {} if sys.platform == "win32" else {"start_new_session": True}
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **session)
    try:
        orphan = jobs.enqueue(conn, "fake")
        _left_running_by_a_previous_life(conn, orphan, live.pid)
        job_id = jobs.enqueue(conn, "fake")

        sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=ok_runner_cmd)
        sup.start()
        try:
            time.sleep(0.5)  # ten polls: plenty for the old behaviour to claim
            assert _row(conn, job_id)["status"] == "queued", "claimed beside a live runner"
            assert _row(conn, orphan)["status"] == "running"

            live.kill()
            live.wait()
            assert _wait_for(lambda: _row(conn, job_id)["status"] == "done"), (
                "the queue never moved after the orphan ended"
            )
        finally:
            sup.stop()
    finally:
        if live.poll() is None:
            live.kill()

    assert _row(conn, orphan)["status"] == "interrupted"


# --- cancel + kill grace ------------------------------------------------------


@pytest.fixture
def grandchild_runner_cmd(tmp_path):
    """Fake runner that starts a child of its own, the way prepare runs ffmpeg.

    It writes the grandchild's pid where the test can find it, then blocks on
    that child - never looking at the cancel flag, like a runner inside a
    long conversion. Returns the runner_cmd prefix; the marker file sits at
    tmp_path / "grandchild.pid".
    """
    marker = tmp_path / "grandchild.pid"
    return _script_cmd(
        tmp_path,
        "grandchild_runner.py",
        f"""\
import pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path({str(marker)!r}).write_text(str(child.pid), encoding="utf-8")
child.wait()
""",
    )


def test_cancel_kills_the_grandchild_and_removes_the_scratch(
    conn, db_path, grandchild_runner_cmd, tmp_path, monkeypatch
):
    """TASK-069: the kill reached the runner and nothing below it.

    A runner blocked in ffmpeg never sees the cancel flag; the supervisor
    terminated the runner alone, so ffmpeg went on converting a two-hour file
    and the runner's own finally - the one that removes the job's scratch -
    never ran. The launcher kills the whole tree for the app; the supervisor
    has to do the same for a job.
    """
    monkeypatch.setattr(paths, "WORK_DIR", tmp_path / "work")
    grace = 1.0
    job_id = jobs.enqueue(conn, "fake")
    marker = tmp_path / "grandchild.pid"

    sup = supervisor.Supervisor(
        db_path, poll_interval=0.05, runner_cmd=grandchild_runner_cmd, kill_grace=grace
    )
    sup.start()
    try:
        assert _wait_for(lambda: marker.is_file() and marker.read_text().strip()), (
            "the runner never started its child"
        )
        grandchild = int(marker.read_text(encoding="utf-8").strip())
        assert supervisor.pid_alive(grandchild)
        scratch = paths.job_work_dir(job_id)
        scratch.mkdir(parents=True)
        (scratch / "audio.wav").write_bytes(b"scratch")

        jobs.request_cancel(conn, job_id)
        assert _wait_for(
            lambda: _row(conn, job_id)["status"] == "cancelled", timeout=grace + 8.0
        ), "job was not cancelled"

        assert _wait_for(lambda: not supervisor.pid_alive(grandchild), timeout=3.0), (
            "the grandchild outlived the cancel"
        )
        assert _wait_for(lambda: not scratch.exists(), timeout=3.0), (
            "the job's scratch was left behind"
        )
    finally:
        sup.stop()
        if marker.is_file():
            try:
                pid = int(marker.read_text(encoding="utf-8").strip())
                if supervisor.pid_alive(pid):
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            except (ValueError, OSError):
                pass


def test_cancel_kills_hung_runner_within_grace_and_cancels_job(
    conn, db_path, sleeping_runner_cmd
):
    grace = 1.0
    job_id = jobs.enqueue(conn, "fake")

    sup = supervisor.Supervisor(
        db_path, poll_interval=0.05, runner_cmd=sleeping_runner_cmd, kill_grace=grace
    )
    sup.start()
    try:
        assert _wait_for(
            lambda: (r := _row(conn, job_id))["status"] == "running"
            and r["pid"] is not None
        ), "runner never spawned"
        pid = _row(conn, job_id)["pid"]

        started = time.monotonic()
        jobs.request_cancel(conn, job_id)
        assert _wait_for(
            lambda: _row(conn, job_id)["status"] == "cancelled",
            timeout=grace + 2.0,
        ), "job was not cancelled within grace + 2 s"
        assert time.monotonic() - started <= grace + 2.0
        assert _wait_for(lambda: not supervisor.pid_alive(pid), timeout=2.0), (
            "hung runner process survived the kill"
        )
    finally:
        sup.stop()


# --- reconcile ----------------------------------------------------------------


def test_reconcile_flips_dead_and_absent_pid_running_jobs(conn):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    # Shaped like a runner: `_spawn` starts each in a session of its own (#32).
    session = {} if sys.platform == "win32" else {"start_new_session": True}
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **session)
    try:
        j_dead = jobs.enqueue(conn, "fake")
        j_absent = jobs.enqueue(conn, "fake")
        j_alive = jobs.enqueue(conn, "fake")
        j_queued = jobs.enqueue(conn, "fake")
        conn.execute(
            "UPDATE job SET status='running', pid=? WHERE id=?", (dead.pid, j_dead)
        )
        conn.execute(
            "UPDATE job SET status='running', pid=NULL WHERE id=?", (j_absent,)
        )
        conn.execute(
            "UPDATE job SET status='running', pid=? WHERE id=?", (live.pid, j_alive)
        )
        conn.commit()

        assert supervisor.reconcile(conn) == 2

        assert _row(conn, j_dead)["status"] == "interrupted"
        assert _row(conn, j_absent)["status"] == "interrupted"
        assert _row(conn, j_alive)["status"] == "running"
        assert _row(conn, j_queued)["status"] == "queued"

        # nothing left to reconcile on a second pass
        assert supervisor.reconcile(conn) == 0
    finally:
        live.kill()
        live.wait()


# --- pid_alive ----------------------------------------------------------------


def test_pid_alive_detects_live_and_dead_processes():
    assert supervisor.pid_alive(os.getpid()) is True

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert supervisor.pid_alive(p.pid) is False

    assert supervisor.pid_alive(None) is False
    assert supervisor.pid_alive(0) is False


# --- what the app log hears ---------------------------------------------------------


@pytest.fixture
def crashing_runner_cmd(tmp_path):
    """Fake runner: says why on stderr and dies before any verdict - the DLL
    that would not load, the import that failed. Before the app log, that
    sentence went nowhere: the runner was spawned with no stderr at all."""
    return _script_cmd(
        tmp_path,
        "crashing_runner.py",
        "import sys\n"
        "print('ImportError: DLL load failed while importing ctranslate2', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
    )


def test_a_runner_that_dies_before_its_first_stage_leaves_its_reason_in_the_app_log(
    conn, db_path, crashing_runner_cmd
):
    from scribe import applog

    job_id = jobs.enqueue(conn, "fake")
    sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=crashing_runner_cmd)
    sup.start()
    try:
        assert _wait_for(lambda: _row(conn, job_id)["status"] == "failed")
        # The exit line is written after the verdict; give it a beat.
        assert _wait_for(lambda: "runner.exited" in applog.path().read_text(encoding="utf-8"))
    finally:
        sup.stop()

    lines, _ = applog.tail(0)
    events = {line["event"]: line for line in lines}
    assert events["job.enqueued"]["job"] == job_id
    assert events["job.claimed"]["job"] == job_id
    assert events["runner.spawned"]["pid"] == _row(conn, job_id)["pid"]
    exited = events["runner.exited"]
    assert exited["code"] == 1 and exited["level"] == "error"
    assert exited["status"] == "failed" and exited["error_code"] == "RUNNER_DIED"
    assert "DLL load failed while importing ctranslate2" in exited["stderr"]
    # And the full stderr file is kept when it has something in it.
    assert supervisor._runner_stderr_path(job_id).exists()


def test_a_quiet_successful_runner_leaves_no_stderr_file_behind(conn, db_path, ok_runner_cmd):
    from scribe import applog

    job_id = jobs.enqueue(conn, "fake")
    sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=ok_runner_cmd)
    sup.start()
    try:
        assert _wait_for(lambda: _row(conn, job_id)["status"] == "done")
        assert _wait_for(lambda: "runner.exited" in applog.path().read_text(encoding="utf-8"))
    finally:
        sup.stop()

    lines, _ = applog.tail(0)
    exited = next(line for line in lines if line["event"] == "runner.exited")
    assert exited["code"] == 0 and exited["level"] == "info" and exited["stderr"] is None
    assert not supervisor._runner_stderr_path(job_id).exists()


def test_old_runner_stderr_files_are_swept_at_startup_and_fresh_ones_kept(tmp_path, monkeypatch):
    """CR-006. One file per job that wrote anything to stderr - CUDA warns on
    every start - with nothing to clean them. Now a week."""
    from scribe import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    old = supervisor._runner_stderr_path(1)
    fresh = supervisor._runner_stderr_path(2)
    other = tmp_path / "app.log"
    for f in (old, fresh, other):
        f.write_text("x", encoding="utf-8")
    stale = time.time() - supervisor.STDERR_KEEP_SECONDS - 60
    os.utime(old, (stale, stale))

    assert supervisor.sweep_stderr() == 1

    assert not old.exists() and fresh.exists() and other.exists()


def test_the_stderr_tail_is_read_by_seeking_not_by_loading_the_file(tmp_path):
    big = tmp_path / "runner-9.stderr"
    big.write_bytes(b"a" * 100_000 + b"\nlast words")

    tail = supervisor._read_tail(big, limit=20)

    assert tail.endswith("last words") and len(tail) <= 20


# --- the pid-age question, answered on this platform too ---------------------------


def test_this_process_start_time_is_readable_here():
    """TASK-065 implemented the Windows answer and left POSIX returning None,
    so the guard above it never fired on a Mac or on Linux - which is why
    `test_reconcile_flips_a_job_whose_pid_belongs_to_a_younger_process` was red
    on every machine that is not Windows. Every platform can answer this."""
    started = supervisor.process_started_at(os.getpid())

    assert started is not None, f"no process start time on {sys.platform}"
    assert 0 <= time.time() - started < 86400, "a start time that is not in living memory"


def test_a_pid_nobody_holds_has_no_start_time():
    """"Cannot tell" and "is not there" both have to be None: the caller reads
    None as no evidence and leaves the row where the bare-pid check put it."""
    assert supervisor.process_started_at(0) is None
    assert supervisor.process_started_at(-1) is None
    assert supervisor.process_started_at(None) is None


def test_an_unreadable_answer_is_no_evidence(monkeypatch):
    """A `ps` that is missing, refuses, or prints something unexpected must not
    raise into a reconcile that runs at startup."""
    if sys.platform == "darwin":
        monkeypatch.setattr(
            supervisor.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no ps here")),
        )
    elif sys.platform == "linux":
        monkeypatch.setattr(supervisor.Path, "read_text", lambda self, **k: "nonsense")
    else:
        pytest.skip("covered by the Windows path")

    assert supervisor.process_started_at(os.getpid()) is None


# --- a cancel has to reach a runner nobody is watching (jobs 127, 129) -------------


def test_a_runner_nobody_watches_is_ended_by_a_cancel(db_path, tmp_path):
    """`stop()` leaves a working child alone on purpose, so a long
    transcription survives an app restart and still delivers its verdict. The
    cost is an orphan: no loop reads the cancel flag for it, and `reconcile`
    leaves its row alone because the pid is alive and older than the job. The
    board said "cancelled" while the work carried on."""
    import subprocess as sp

    child = sp.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
    )
    try:
        assert supervisor.pid_alive(child.pid)

        assert supervisor.kill_orphan(child.pid) is True

        # A zombie until somebody reaps it - in production the orphan's parent
        # is the app that died, so init does; here the parent is this test.
        assert supervisor.is_zombie(child.pid) or not supervisor.pid_alive(child.pid)
        child.wait(timeout=5)
        assert not supervisor.pid_alive(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_killing_what_is_already_gone_says_so_rather_than_raising():
    assert supervisor.kill_orphan(None) is False
    assert supervisor.kill_orphan(0) is False


def test_kill_orphan_refuses_a_process_too_young_to_be_the_runner():
    """A row's pid is reused: the process holding it started an hour after
    the job was claimed, so it cannot be the runner the row meant - and the
    kill would land on a stranger's process group."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        assert supervisor.pid_alive(child.pid)
        if supervisor.process_started_at(child.pid) is None:
            pytest.skip("this platform cannot say when a process started")

        started_an_hour_ago = time.time() - 3600
        assert supervisor.kill_orphan(child.pid, job_started_at=started_an_hour_ago) is False
        assert supervisor.pid_alive(child.pid), "the stranger was signalled anyway"
    finally:
        child.kill()
        child.wait(timeout=5)


def test_a_supervisor_says_which_job_it_is_watching(db_path):
    sup = supervisor.Supervisor(db_path)

    assert sup.owns(1) is False

    sup._watching = 7
    assert sup.owns(7) is True and sup.owns(8) is False


def test_a_cancelled_job_whose_runner_is_gone_says_cancelled(conn):
    """The loop reconciles on every idle tick, so it raced the cancel route to
    the verdict and usually won - a job the user cancelled came to rest saying
    "interrupted". The runner ending after a cancel is the request being
    carried out, not something that happened to it."""
    job_id = jobs.enqueue(conn, "fake")
    with db.LOCK:
        conn.execute(
            "UPDATE job SET status='running', started_at=?, pid=?, cancel_requested=1 WHERE id=?",
            (time.time() - 5, 999_999, job_id),  # a pid nobody holds
        )
        conn.commit()

    supervisor.reconcile(conn)

    assert _row(conn, job_id)["status"] == "cancelled"


def test_a_job_nobody_cancelled_is_still_interrupted(conn):
    job_id = jobs.enqueue(conn, "fake")
    with db.LOCK:
        conn.execute(
            "UPDATE job SET status='running', started_at=?, pid=? WHERE id=?",
            (time.time() - 5, 999_999, job_id),
        )
        conn.commit()

    supervisor.reconcile(conn)

    assert _row(conn, job_id)["status"] == "interrupted"


# --- a deliberate stop (TASK-112.03, ADR-028) -----------------------------------


def _running_with_pid(conn, job_id):
    assert _wait_for(
        lambda: (r := _row(conn, job_id))["status"] == "running" and r["pid"] is not None
    ), "runner never spawned"
    return _row(conn, job_id)["pid"]


def test_a_deliberate_stop_ends_the_runner_and_puts_its_job_at_the_front(
    conn, db_path, sleeping_runner_cmd
):
    """ADR-028: stop now, lose nothing. The runner goes first, so the row can
    never be queued while a child still holds the card."""
    first = jobs.enqueue(conn, "fake", priority=5)
    sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=sleeping_runner_cmd)
    sup.start()
    try:
        pid = _running_with_pid(conn, first)
        waiting = jobs.enqueue(conn, "fake", priority=5)
    finally:
        sup.stop(requeue=True)

    row = _row(conn, first)
    assert not supervisor.pid_alive(pid), "the runner outlived the stop"
    assert row["status"] == "queued" and row["priority"] == 5
    assert row["pid"] is None and row["started_at"] is None and row["stage"] is None
    assert jobs.claim_next(conn)["id"] == first, f"job {waiting} was put ahead of the stopped one"


def test_a_stop_nobody_asked_for_ends_the_runner_and_leaves_the_job_to_reconcile(
    conn, db_path, sleeping_runner_cmd
):
    """ADR-032 (amending ADR-028): every shutdown the app lives through ends
    the runner's tree - a SIGTERM or Ctrl+C to the app as much as the stop
    route - so no runner outlives the app on macOS and Linux. Only the stop
    route requeues; this one leaves the row `running`, and the next start's
    reconcile marks it `interrupted` (ADR-001)."""
    job_id = jobs.enqueue(conn, "fake")
    sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=sleeping_runner_cmd)
    sup.start()
    pid = _running_with_pid(conn, job_id)
    sup.stop()
    try:
        assert not supervisor.pid_alive(pid), "the runner outlived the stop"
        assert _row(conn, job_id)["status"] == "running"
        supervisor.reconcile(conn)
        assert _row(conn, job_id)["status"] == "interrupted"
    finally:
        if supervisor.pid_alive(pid):
            os.kill(pid, signal.SIGTERM)


def test_a_job_cancelled_before_the_stop_is_cancelled_not_requeued(
    conn, db_path, sleeping_runner_cmd
):
    job_id = jobs.enqueue(conn, "fake")
    sup = supervisor.Supervisor(
        db_path, poll_interval=0.05, runner_cmd=sleeping_runner_cmd, kill_grace=60.0
    )
    sup.start()
    try:
        _running_with_pid(conn, job_id)
        jobs.request_cancel(conn, job_id)
    finally:
        sup.stop(requeue=True)

    assert _row(conn, job_id)["status"] == "cancelled"


# --- a row no runner will ever answer for (GitHub #29, #32) ----------------------


def _not_a_runner():
    """A live process that is no runner of this app: on POSIX it shares this
    test's session instead of leading one (`_spawn` starts every runner with
    `start_new_session`), on Windows its image is cmd.exe and not python."""
    if sys.platform == "win32":
        return subprocess.Popen(["cmd.exe", "/c", "ping -n 60 127.0.0.1 >nul"],
                                creationflags=subprocess.CREATE_NO_WINDOW)
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_a_claim_whose_spawn_never_came_does_not_hold_the_queue_for_ever(conn):
    """#29: claim_next writes running with no pid; the app died before the
    pid was written. The claim-to-spawn gap is milliseconds, so a row still
    without a pid a minute later has no writer left."""
    job_id = jobs.enqueue(conn, "fake")
    with db.LOCK:
        conn.execute("UPDATE job SET status='running', pid=NULL, started_at=? WHERE id=?",
                     (time.time() - 2 * supervisor._PID_AGE_SLACK_SECONDS, job_id))
        conn.commit()

    assert supervisor.reconcile(conn) == 1
    assert _row(conn, job_id)["status"] == "interrupted"


def test_a_stale_claim_lets_the_next_job_run(conn, db_path, ok_runner_cmd):
    stale = jobs.enqueue(conn, "fake")
    with db.LOCK:
        conn.execute("UPDATE job SET status='running', pid=NULL, started_at=? WHERE id=?",
                     (time.time() - 3600, stale))
        conn.commit()
    job_id = jobs.enqueue(conn, "fake")

    sup = supervisor.Supervisor(db_path, poll_interval=0.05, runner_cmd=ok_runner_cmd)
    sup.start()
    try:
        assert _wait_for(lambda: _row(conn, job_id)["status"] == "done"), "the stale claim held the queue"
    finally:
        sup.stop()


def test_a_pid_reused_by_something_that_is_no_runner_does_not_keep_the_row(conn):
    """#32: the pid is alive and the process is not younger than the job -
    reused inside the minute of slack - but it is not a runner at all."""
    stranger = _not_a_runner()
    try:
        job_id = jobs.enqueue(conn, "fake")
        _left_running_by_a_previous_life(conn, job_id, stranger.pid)

        supervisor.reconcile(conn)

        assert _row(conn, job_id)["status"] == "interrupted"
        assert stranger.poll() is None, "reconcile touched the stranger"
    finally:
        stranger.kill()
        stranger.wait(10)


def test_cancel_never_signals_a_process_that_is_no_runner():
    """#32: kill_orphan used to take the stranger's whole process group."""
    stranger = _not_a_runner()
    try:
        assert supervisor.kill_orphan(stranger.pid) is False
        time.sleep(0.3)
        assert stranger.poll() is None, "kill_orphan ended a process that was no runner"
    finally:
        stranger.kill()
        stranger.wait(10)


def test_a_runner_shaped_process_is_recognised(tmp_path):
    """The other half: a real runner still counts, or a long transcription
    would lose its row at the next restart."""
    session = {} if sys.platform == "win32" else {"start_new_session": True}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    runner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                              creationflags=flags, **session)
    try:
        assert supervisor.could_be_runner(runner.pid)
    finally:
        runner.kill()
        runner.wait(10)
