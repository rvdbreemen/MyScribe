"""How a stopping server treats the requests still open (TASK-112.09).

Robert, 2026-10-25: "exempt uploads". A read that only ends when a job does
is cut after the grace; an upload is let finish, up to a bound counted from
the moment the stop began, and cut there. A real uvicorn server on
127.0.0.1, on a thread, with an app of two routes and a grace and bound in
fractions of a second.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import uvicorn

from scribe.web import stopping

GRACE = 0.5
BOUND = 2.0


async def _app(scope, receive, send):
    if scope["type"] != "http":
        return
    if scope["path"] == "/up":
        size = 0
        while True:
            message = await receive()
            size += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": str(size).encode()})
        return
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/event-stream")]})
    while True:  # a job page's stream: it only ends when its job does
        await send({"type": "http.response.body", "body": b"data: tick\n\n", "more_body": True})
        await asyncio.sleep(0.1)


def _serve(**kw):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(_app, lifespan="off", log_config=None)
    server = stopping.StoppingServer(
        config, is_upload=lambda scope: scope["path"] == "/up", grace=GRACE, upload_seconds=BOUND, **kw)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    return server, thread, port


def _stream(port: int, ended: list[float]) -> threading.Thread:
    def read():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=30) as sock:
                sock.sendall(f"GET /sse HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
                while sock.recv(4096):
                    pass
        except OSError:
            pass
        finally:
            ended.append(time.monotonic())

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


def _half_upload(port: int, size: int) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=30)
    sock.sendall(f"POST /up HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {size}\r\n"
                 "Connection: close\r\n\r\n".encode() + b"x" * (size // 2))
    return sock


def _answer(sock: socket.socket) -> bytes:
    answer = b""
    try:
        while chunk := sock.recv(4096):
            answer += chunk
    except OSError:
        pass
    return answer


def test_the_stream_is_cut_after_the_grace_and_the_upload_finishes():
    server, thread, port = _serve()
    ended: list[float] = []
    _stream(port, ended)
    upload = _half_upload(port, 1000)
    time.sleep(0.3)
    stopped = time.monotonic()
    server.should_exit = True
    time.sleep(GRACE + 0.7)
    assert ended and ended[0] - stopped < GRACE + 0.5, "the stream was not cut after the grace"
    assert thread.is_alive(), "the server went under an upload in flight"
    upload.sendall(b"x" * 500)
    answer = _answer(upload)
    upload.close()
    assert answer.startswith(b"HTTP/1.1 201") and b"\r\n1000\r\n" in answer, answer
    thread.join(5)
    assert not thread.is_alive()
    assert time.monotonic() - stopped < BOUND, "the server waited out the bound after the upload was done"


def test_an_upload_still_going_at_the_bound_is_cut_there():
    server, thread, port = _serve()
    upload = _half_upload(port, 1000)
    time.sleep(0.3)
    stopped = time.monotonic()
    server.should_exit = True
    thread.join(BOUND + 3)
    took = time.monotonic() - stopped
    assert not thread.is_alive()
    assert BOUND - 0.3 < took < BOUND + 1.5, f"the server went after {took:.1f} s"
    assert not _answer(upload).startswith(b"HTTP/1.1 201")
    upload.close()


def test_an_earlier_deadline_shortens_the_bound_but_never_the_grace():
    """The door closes late in the app's stop, with the deadline the app's
    stop began; a deadline already passed leaves the upload the grace only."""
    stops: list[float] = []
    server, thread, port = _serve(on_stopping=stops.append)
    server.upload_deadline = time.monotonic() - 5
    upload = _half_upload(port, 1000)
    time.sleep(0.3)
    stopped = time.monotonic()
    server.should_exit = True
    thread.join(BOUND + 3)
    took = time.monotonic() - stopped
    assert not thread.is_alive()
    assert GRACE - 0.2 < took < GRACE + 1.0, f"the server went after {took:.1f} s"
    assert stops == [server.upload_deadline]
    upload.close()


def test_the_app_routes_that_carry_a_recording_in_are_the_uploads():
    def post(path, method="POST"):
        return stopping.is_app_upload({"type": "http", "method": method, "path": path})

    assert post("/api/media") and post("/transcribe/upload")
    assert post("/record/abc123/chunk") and post("/record/abc123/finish")
    assert not post("/api/media", "GET")
    assert not post("/api/jobs/1/stream", "GET")
    assert not post("/transcribe/url") and not post("/record/start") and not post("/record/a/b/chunk")
