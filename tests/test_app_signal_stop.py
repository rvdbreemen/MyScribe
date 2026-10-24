"""A shutdown nobody asked for through the stop route still ends the runner
(TASK-112.09, Robert's option b, ADR-031 amending ADR-028).

A real `python -m scribe` with a temporary library, its real supervisor and a
real runner running the slow fake job (10 s). The app is then signalled the
way an operating system or a terminal ends a process: SIGTERM on macOS and
Linux, Ctrl+Break on Windows, where no SIGTERM reaches a process and uvicorn
answers SIGBREAK the same way. Before TASK-112.09 the runner - a session or
process group of its own - ran on after the app had gone and wrote `done`
(measured in WSL on 2026-10-21). The row must instead stay `running` with
no runner behind it, for the next start's reconcile to mark `interrupted`:
not requeued, because nobody asked for the stop (ADR-028's Must Not).

With the job's page open, its event stream is a request that only ends when
the job does, and uvicorn's graceful shutdown waited for it without a limit
before the lifespan - and so the runner's end - ever ran (measured
2026-10-24: the slow job ran to `done`, the app went 10.1 s after Ctrl+Break).
On macOS and Linux the launcher's hard stop SIGKILLs the app 15 s after its
SIGTERM, so an hour's job would have outlived it.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from scribe import db, jobs, supervisor

REPO = Path(__file__).resolve().parents[1]
SLOW_SECONDS = 10.0  # stages_fake._SLOW_TOTAL_SECONDS


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _end_process_tree(pid: int | None) -> None:
    if not pid or not supervisor.pid_alive(pid):
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _hold_the_stream_open(port: int, job_id: int, lines: list[str]) -> None:
    """Read the job's event stream the way its page does, on a thread."""
    def read():
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/jobs/{job_id}/stream", timeout=60) as response:
                for line in response:
                    lines.append(line.decode("utf-8", "replace").strip())
        except OSError:
            pass

    threading.Thread(target=read, name="job-page", daemon=True).start()


@pytest.mark.parametrize("page_open", [False, True], ids=["no-page", "job-page-open"])
def test_a_signal_to_the_app_ends_the_runner_without_requeueing(_library_under_tmp_path, page_open):
    library = _library_under_tmp_path
    port = _free_port()
    environ = dict(os.environ, SCRIBE_FAKE_STAGES="1", SCRIBE_DATA_DIR=str(library))
    for name in ("MYSCRIBE_LAUNCHER", "MYSCRIBE_LAUNCHER_PID"):  # never a launcher's login entry
        environ.pop(name, None)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    app = subprocess.Popen(
        [sys.executable, "-m", "scribe", "--port", str(port), "--no-browser"],
        cwd=REPO, env=environ, creationflags=flags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    conn = None
    runner = None
    try:
        deadline = time.monotonic() + 120
        health = None
        while time.monotonic() < deadline and app.poll() is None:
            health = _health(port)
            if health:
                break
            time.sleep(0.2)
        assert health and Path(health["data_dir"]).resolve() == library.resolve(), health
        app_pid = health["pid"]

        conn = db.connect(library / "myscribe.db")
        job_id = jobs.enqueue(conn, "fake", params={"scenario": "slow"})
        deadline = time.monotonic() + 60
        row = None
        while time.monotonic() < deadline:
            row = conn.execute("SELECT status, pid, stage FROM job WHERE id=?", (job_id,)).fetchone()
            if row["status"] == "running" and row["pid"] and row["stage"] == "one":
                break
            time.sleep(0.1)
        assert row["status"] == "running" and row["stage"] == "one", "the slow job never started"
        runner = row["pid"]
        lines: list[str] = []
        if page_open:
            _hold_the_stream_open(port, job_id, lines)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not any(line.startswith("event:") for line in lines):
                time.sleep(0.1)
            assert any(line.startswith("event:") for line in lines), f"the stream never answered: {lines}"
        signalled = time.monotonic()

        if sys.platform == "win32":
            app.send_signal(signal.CTRL_BREAK_EVENT)  # the group the redirector leads
        else:
            os.kill(app_pid, signal.SIGTERM)  # the app alone, not its group
        app.wait(timeout=60)
        took = time.monotonic() - signalled
        assert not supervisor.pid_alive(app_pid), "the app outlived its signal"
        assert took < SLOW_SECONDS - 2, f"the app took {took:.1f} s to go: it waited for the job"

        assert not supervisor.pid_alive(runner), "the runner outlived the app"
        # The orphan of old wrote its verdict when the slow stage was done:
        # wait past that, then the row must still be the one nobody finished.
        time.sleep(max(0.0, signalled + SLOW_SECONDS + 3 - time.monotonic()))
        row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        assert row["status"] == "running", f"the job became {row['status']} after the app had gone"
        supervisor.reconcile(conn)
        row = conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
        assert row["status"] == "interrupted", row["status"]
    finally:
        if conn is not None:
            conn.close()
        _end_process_tree(runner)
        if app.poll() is None:
            _end_process_tree(app.pid)
            app.wait(timeout=30)
