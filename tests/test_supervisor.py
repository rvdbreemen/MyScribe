import os
import subprocess
import sys
import time

import pytest

from scribe import db, jobs, supervisor


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


# --- cancel + kill grace ------------------------------------------------------


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
