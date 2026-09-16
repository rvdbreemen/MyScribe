import os
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


def test_a_stop_that_could_not_join_says_so_and_keeps_the_thread(db_path, capsys):
    """TASK-073: the loop cannot see the stop event while _watch is inside a
    kill - terminate, then up to two waits of _KILL_WAIT_SECONDS - so a
    shutdown that lands mid-cancel returns from stop() with the loop still
    running. Dropping the handle either way made that indistinguishable from
    a clean stop, and start() would begin a second loop beside the first.
    Same rule as watching.Watcher.stop(): say so, keep it."""
    sup = supervisor.Supervisor(db_path, poll_interval=0.05)
    stuck = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    stuck.start()
    sup._thread = stuck

    sup.stop(timeout=0.05)

    assert sup._thread is stuck, "a thread that never stopped was forgotten"
    assert "still running" in capsys.readouterr().err
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
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
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
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
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
