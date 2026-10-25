"""A shutdown nobody asked for through the stop route still ends the runner
(TASK-112.09, Robert's option b, ADR-032 amending ADR-028).

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
On macOS and Linux the launcher's hard stop SIGKILLs the app
`APP_SIGTERM_WAIT` (50 s) after its SIGTERM, so an hour's job would have
outlived it.
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


def _start_app(library: Path, port: int, *extra: str) -> subprocess.Popen:
    environ = dict(os.environ, SCRIBE_FAKE_STAGES="1", SCRIBE_DATA_DIR=str(library))
    for name in ("MYSCRIBE_LAUNCHER", "MYSCRIBE_LAUNCHER_PID"):  # never a launcher's login entry
        environ.pop(name, None)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(
        [sys.executable, "-m", "scribe", "--port", str(port), "--no-browser", *extra],
        cwd=REPO, env=environ, creationflags=flags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _signal(app: subprocess.Popen, app_pid: int) -> None:
    if sys.platform == "win32":
        app.send_signal(signal.CTRL_BREAK_EVENT)  # the group the redirector leads
    else:
        os.kill(app_pid, signal.SIGTERM)


def test_an_upload_in_flight_at_the_stop_finishes_and_an_open_stream_is_still_cut(
        _library_under_tmp_path):
    """Robert, 2026-10-25: "exempt uploads". The 3 s cut-off of a stop
    (`SHUTDOWN_GRACE_SECONDS`) is for reads that only end when a job does - a
    job page's event stream. A recording on its way in is allowed to finish
    (`stopping.UPLOAD_FINISH_SECONDS`), and its media row exists afterwards.

    Half the upload's body goes out, then the stop, then a wait well past the
    grace, during which the stream must end and the app must still be there;
    then the rest of the body, which must get its 201. Before this the upload
    was cancelled with the stream, at 3 s."""
    from scribe import __main__ as scribe_main

    library = _library_under_tmp_path
    port = _free_port()
    app = _start_app(library, port, "--no-supervisor")  # the job below stays queued
    conn = None
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
        lines: list[str] = []
        stream_ended: list[float] = []

        def read_stream():
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/jobs/{job_id}/stream", timeout=60) as response:
                    for line in response:
                        lines.append(line.decode("utf-8", "replace").strip())
            except OSError:
                pass
            finally:
                stream_ended.append(time.monotonic())

        reader = threading.Thread(target=read_stream, name="job-page", daemon=True)
        reader.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not lines:
            time.sleep(0.1)
        assert lines, "the stream never answered"

        payload = os.urandom(256 * 1024)
        boundary = "myscribe-upload-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="Voice memo 42.m4a"\r\n'
            "Content-Type: audio/mp4\r\n\r\n"
        ).encode("ascii") + payload + f"\r\n--{boundary}--\r\n".encode("ascii")
        head = (
            "POST /api/media HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        half = len(body) // 2
        upload = socket.create_connection(("127.0.0.1", port), timeout=60)
        try:
            upload.sendall(head + body[:half])
            time.sleep(0.5)  # the request has begun reading its body

            signalled = time.monotonic()
            _signal(app, app_pid)
            reader.join(scribe_main.SHUTDOWN_GRACE_SECONDS + 10)
            assert stream_ended, "the job page's stream was not cut off"
            cut = stream_ended[0] - signalled
            assert cut < scribe_main.SHUTDOWN_GRACE_SECONDS + 3, f"the stream was cut after {cut:.1f} s"
            time.sleep(max(0.0, signalled + scribe_main.SHUTDOWN_GRACE_SECONDS + 2 - time.monotonic()))
            assert app.poll() is None, "the app went before the upload had finished"

            answer = b""
            try:
                upload.sendall(body[half:])
                while chunk := upload.recv(65536):
                    answer += chunk
            except OSError as exc:
                answer += f"<{type(exc).__name__}>".encode()
        finally:
            upload.close()
        status_line = answer.split(b"\r\n", 1)[0].decode("latin-1")
        assert status_line.startswith("HTTP/1.1 201"), f"the upload got {answer[:200]!r}"

        app.wait(timeout=90)
        row = conn.execute(
            "SELECT size_bytes FROM media WHERE orig_name=?", ("Voice memo 42.m4a",)).fetchone()
        assert row is not None and row["size_bytes"] == len(payload), "no media row for the upload"
    finally:
        if conn is not None:
            conn.close()
        if app.poll() is None:
            _end_process_tree(app.pid)
            app.wait(timeout=30)
