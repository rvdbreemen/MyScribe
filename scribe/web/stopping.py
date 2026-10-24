"""A uvicorn server that stops in two steps: reads first, uploads later.

uvicorn has one `timeout_graceful_shutdown`: when it runs out, every open
request is cancelled at once. MyScribe has two kinds that want different
things (TASK-112.09, Robert's "exempt uploads" on 2026-10-25, ADR-032):

- a read that only ends when a job does - a job page's event stream - which
  without a limit held a stop for as long as the job ran. It is cut after
  the server's grace (3 s for the app, `SHUTDOWN_GRACE_SECONDS`).
- an upload - a recording on its way in from the laptop or the phone - which
  is let finish, up to `UPLOAD_FINISH_SECONDS` after the stop began. One cut
  there loses the user a recording; the next start does not bring it back.

How: an ASGI wrapper marks the request task of an upload (the task uvicorn
keeps in `server_state.tasks`; the wrapper runs in it, outside every
middleware that might start a task of its own). At the grace, the stop
cancels every task that is not marked. uvicorn's own shutdown then waits for
the rest with its timeout set to what is left of the bound, cancels what is
still going, and runs the lifespan's end. Nothing of uvicorn's is copied.
"""

from __future__ import annotations

import asyncio
import re
import time
import weakref
from collections.abc import Callable

import uvicorn

from scribe import applog

UPLOAD_FINISH_SECONDS = 30.0
"""How long after a stop begins an upload in flight may still finish.

A cap on the stop, not a promise for every file: an hour's voice memo
(AAC at 128 kbit/s, about 58 MB) needs about 12 s at 5 MB/s on home Wi-Fi,
and a laptop upload goes over the loopback. A phone video of a gigabyte
does not make it, is cut at the bound, and has to be sent again. The
launcher's waits are sized on this (`APP_SIGTERM_WAIT`, `STOP_WAIT`);
`tests/test_launcher.py` adds them up."""

_RECORDING_ROUTE = re.compile(r"/record/[^/]+/(chunk|finish)")
APP_UPLOAD_PATHS = frozenset({"/api/media", "/transcribe/upload"})
"""The app's routes that carry a recording in: the API's and the transcribe
dialog's upload, plus the microphone's chunks and its finish, which turns
the chunks into a media row (`_RECORDING_ROUTE`)."""


def is_app_upload(scope: dict) -> bool:
    """True for a POST that brings a recording into the app."""
    if scope.get("type") != "http" or scope.get("method", "").upper() != "POST":
        return False
    path = scope.get("path", "")
    return path in APP_UPLOAD_PATHS or _RECORDING_ROUTE.fullmatch(path) is not None


class _MarkUploads:
    """ASGI: remember the request task of every upload."""

    def __init__(self, app, is_upload: Callable[[dict], bool], marked: weakref.WeakSet) -> None:
        self._app = app
        self._is_upload = is_upload
        self._marked = marked

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and self._is_upload(scope):
            task = asyncio.current_task()
            if task is not None:
                self._marked.add(task)
        await self._app(scope, receive, send)


class StoppingServer(uvicorn.Server):
    """`uvicorn.Server` whose stop cuts reads after ``grace`` and uploads at a bound.

    ``upload_deadline``: a `time.monotonic()` moment set before the stop, for a
    server that stops as part of a larger stop - the phone door, closed late in
    the app's own - so its uploads share the app's bound instead of getting a
    second one. An upload never gets less than ``grace``.

    ``on_stopping`` is told the deadline this stop gives its uploads, once,
    as the stop begins.
    """

    def __init__(
        self,
        config: uvicorn.Config,
        *,
        is_upload: Callable[[dict], bool],
        grace: float,
        upload_seconds: float = UPLOAD_FINISH_SECONDS,
        on_stopping: Callable[[float], None] | None = None,
        name: str = "app",
    ) -> None:
        self._uploads: weakref.WeakSet = weakref.WeakSet()
        config.app = _MarkUploads(config.app, is_upload, self._uploads)
        config.timeout_graceful_shutdown = grace
        super().__init__(config)
        self.grace = grace
        self.upload_seconds = upload_seconds
        self.upload_deadline: float | None = None
        self._on_stopping = on_stopping
        self._name = name

    async def shutdown(self, sockets=None) -> None:
        began = time.monotonic()
        deadline = began + self.upload_seconds
        if self.upload_deadline is not None:
            deadline = min(deadline, self.upload_deadline)
        if self._on_stopping is not None:
            self._on_stopping(deadline)
        self.config.timeout_graceful_shutdown = max(self.grace, deadline - began)
        cutter = asyncio.create_task(self._cut_all_but_uploads(deadline))
        try:
            await super().shutdown(sockets)
        finally:
            cutter.cancel()

    async def _cut_all_but_uploads(self, deadline: float) -> None:
        await asyncio.sleep(self.grace)
        uploads = 0
        for task in list(self.server_state.tasks):
            if task in self._uploads:
                uploads += not task.done()
            else:
                task.cancel(msg="Task cancelled, the stop's grace is over")
        if uploads:
            applog.log("server.stop_waits_for_uploads", server=self._name, uploads=uploads,
                       seconds=round(max(0.0, deadline - time.monotonic()), 1))
