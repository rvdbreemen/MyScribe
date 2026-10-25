"""Stopping the server this request came in on (TASK-112.03, ADR-028).

One function for the two doors that ask - the JSON stop route `MyScribe --stop`
calls and the Stop button in Settings - so they cannot drift apart on who may
stop the app or on what the stop does to a running job.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Request

from scribe import applog, db


LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1"})


def stop_requested(request: Request) -> dict:
    """Ask the server this request came in on to stop; what the stop route
    and the Settings button share (TASK-112.03).

    `request_exit` is what `scribe.__main__` hands the app - uvicorn's
    `should_exit` - so the lifespan runs to its end and the supervisor, the
    watcher and the feeds stop through it. The flag tells that lifespan the
    stop was asked for, which is what makes the supervisor requeue the
    running job (ADR-028) instead of leaving it to reconcile.
    """
    client = request.client.host if request.client else ""
    if client not in LOOPBACK_CLIENTS:
        raise HTTPException(403, "MyScribe can only be stopped from this computer")
    app = request.app
    request_exit = getattr(app.state, "request_exit", None)
    if request_exit is None:
        raise HTTPException(503, "this server was not started in a way that can stop itself")
    # No job starts once a stop has begun (Robert, 2026-10-25; ADR-032):
    # claims stop before the running job is named, so the job named here is
    # the one that goes back, and nothing queued starts in the upload window.
    sup = getattr(app.state, "supervisor", None)
    if sup is not None:
        sup.stop_claiming()
    with db.LOCK:
        row = app.state.conn.execute(
            "SELECT id FROM job WHERE status='running' LIMIT 1"
        ).fetchone()
    running = row["id"] if row else None
    app.state.stop_requested = True
    applog.log("app.stop_requested", running_job=running)
    request_exit()
    return {"stopping": True, "pid": os.getpid(), "running_job": running}
