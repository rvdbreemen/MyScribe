"""A stop that cuts an upload while its file is being stored (TASK-112.09).

Robert, 2026-10-25: an upload cut at the bound must not leave a recording
without its job, nor touch the database after the lifespan closed it. The
request's task is cancelled at the bound, but the worker thread it started
goes on (`run_in_threadpool` cannot stop a thread), and uvicorn runs the
lifespan's end at once, without waiting for it.

The real app behind a real `StoppingServer` on 127.0.0.1, with a grace and a
bound in fractions of a second. The step that stores the file is held on a
gate until after the cut, so the thread is still inside it when the lifespan
ends; then it is let go.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from scribe import db, fsbrowse, media, paths, shutdown
from scribe.app import create_app
from scribe.ingest import recording
from scribe.web import stopping

GRACE = 0.3
BOUND = 1.0
CHUNK = Path(__file__).resolve().parent / "fixtures" / "chunk.webm"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))
    return data


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    return path


class _Gate:
    """Holds the storing step until `release`; records what it raised."""

    def __init__(self):
        self.entered = threading.Event()
        self.released = threading.Event()
        self.left = threading.Event()
        self.errors: list[BaseException] = []

    def around(self, real):
        def held(*args, **kwargs):
            self.entered.set()
            self.released.wait(15)
            try:
                return real(*args, **kwargs)
            except BaseException as exc:
                self.errors.append(exc)
                raise
            finally:
                self.left.set()

        return held


def _serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = stopping.StoppingServer(
        uvicorn.Config(app, log_config=None), is_upload=stopping.is_app_upload,
        grace=GRACE, upload_seconds=BOUND)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    return server, thread, f"http://127.0.0.1:{port}"


def _post_in_background(url: str, **kwargs) -> threading.Thread:
    def post():
        try:
            httpx.post(url, timeout=20, **kwargs)
        except httpx.HTTPError:
            pass  # cut at the bound: the answer is not what this test reads

    thread = threading.Thread(target=post, daemon=True)
    thread.start()
    return thread


def _rows(db_path):
    c = db.connect(db_path)
    try:
        media_ids = [r["id"] for r in c.execute("SELECT id FROM media")]
        jobs_for = [r["media_id"] for r in c.execute("SELECT media_id FROM job")]
    finally:
        c.close()
    return media_ids, jobs_for


def _start_recording(base: str) -> str:
    session = httpx.post(f"{base}/record/start", timeout=10).json()["session"]
    answer = httpx.post(f"{base}/record/{session}/chunk", content=CHUNK.read_bytes(),
                        headers={"Content-Type": "audio/webm;codecs=opus"}, timeout=10)
    assert answer.status_code == 200, answer.text
    return session


def _door(name, tmp_path):
    """(what to hold, how to post) for each of the app's ways a file comes in."""
    src = tmp_path / "talk.webm"
    src.write_bytes(CHUNK.read_bytes())
    if name == "api multipart":
        return media, "ingest_stream", lambda base: _post_in_background(
            f"{base}/api/media", files={"file": ("talk.webm", CHUNK.read_bytes())})
    if name == "api path":
        return media, "ingest_path", lambda base: _post_in_background(
            f"{base}/api/media", json={"path": str(src)})
    if name == "dialog path":
        return media, "ingest_path", lambda base: _post_in_background(
            f"{base}/transcribe/path", data={"path": str(src)})
    if name == "dialog upload":
        return media, "ingest_stream", lambda base: _post_in_background(
            f"{base}/transcribe/upload", files={"files": ("talk.webm", CHUNK.read_bytes())})
    if name == "recorder finish":
        def finish(base):
            session = _start_recording(base)
            return _post_in_background(f"{base}/record/{session}/finish", data={})
        return recording, "finalize", finish
    raise AssertionError(name)


DOORS = ["api multipart", "api path", "dialog path", "dialog upload", "recorder finish"]


@pytest.mark.parametrize("door", DOORS)
def test_a_file_still_being_stored_at_the_cut_lands_with_its_job(door, db_path, data_dir, tmp_path,
                                                                  monkeypatch):
    module, attr, post = _door(door, tmp_path)
    gate = _Gate()
    monkeypatch.setattr(module, attr, gate.around(getattr(module, attr)))
    app = create_app(db_path=db_path, start_supervisor=False, start_watcher=False)
    server, thread, base = _serve(app)
    try:
        post(base)
        assert gate.entered.wait(10), "the route never reached the storing step"
        server.should_exit = True
        time.sleep(BOUND + 0.7)  # past the cut: the lifespan's end has begun
    finally:
        gate.released.set()
    thread.join(15)
    assert not thread.is_alive()
    assert gate.left.wait(10)
    time.sleep(0.3)  # whatever the route does after the stored file

    assert gate.errors == [], "the stored file touched a closed database"
    media_ids, jobs_for = _rows(db_path)
    assert len(media_ids) == 1, "the file did not land"
    assert jobs_for == media_ids, "the recording landed without its job"


def test_a_file_stored_past_the_wait_still_finds_the_database_open(db_path, data_dir, tmp_path,
                                                                     monkeypatch):
    """The lifespan waits `INGEST_FINISH_SECONDS` for a file being stored; one
    still going after that does not get a closed connection: the lifespan
    leaves it open for the thread, which the interpreter waits for."""
    monkeypatch.setattr(shutdown, "INGEST_FINISH_SECONDS", 0.2)
    module, attr, post = _door("api path", tmp_path)
    gate = _Gate()
    monkeypatch.setattr(module, attr, gate.around(getattr(module, attr)))
    app = create_app(db_path=db_path, start_supervisor=False, start_watcher=False)
    server, thread, base = _serve(app)
    try:
        post(base)
        assert gate.entered.wait(10)
        server.should_exit = True
        thread.join(BOUND + 5)
        assert not thread.is_alive(), "the stop waited for the file past its bound"
    finally:
        gate.released.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _rows(db_path)[1] == []:
        time.sleep(0.05)

    assert gate.errors == []
    media_ids, jobs_for = _rows(db_path)
    assert len(media_ids) == 1 and jobs_for == media_ids
