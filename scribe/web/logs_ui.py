"""The Log page: the application log, on screen, live while you look at it.

Two routes. ``GET /logs`` is the page: the last :data:`FIRST_PAINT` lines,
newest at the bottom, and the file's path so the same thing can be opened in
an editor. ``GET /logs/tail?after=<offset>`` is what the page asks every two
seconds while it is open: only the lines written since that byte offset, with
the next offset in the response, so the browser never re-reads what it has.

The filter (a level, a substring) is applied here rather than in the browser
for one reason: a line that does not match is not sent, and at 32 MB the
difference between "send everything and hide" and "send what matches" is the
difference between a page that keeps up and one that does not.

Read-only. Nothing on this page writes to the log, and the log is not read by
anything to decide anything - it is observation, and ADR-002 is untouched.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from starlette.responses import Response

from scribe import applog
from scribe.web import library, render

router = APIRouter()

FIRST_PAINT = 300
POLL_SECONDS = 2


def _wanted(line: dict, level: str, q: str) -> bool:
    if level:
        own = line.get("level", "info")
        rank = applog.LEVELS.index(own) if own in applog.LEVELS else applog.LEVELS.index("info")
        if rank < applog.LEVELS.index(level):
            return False
    if q:
        # Searched in the form the page shows - `job=24`, not `24` - so what a
        # person reads in a row is what they can type to find more like it.
        hay = " ".join(f"{k}={v}" for k, v in line.items()).lower()
        return q in hay
    return True


def _view(line: dict) -> dict:
    """One line as the template shows it: the fixed columns pulled out, and
    the rest as `key=value` in a stable order."""
    fixed = {"ts", "level", "proc", "pid", "event"}
    ts = line.get("ts")
    return {
        "when": time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "",
        "level": line.get("level", "info"),
        "proc": line.get("proc") or "",
        "event": line.get("event", ""),
        "fields": [(k, line[k]) for k in sorted(line) if k not in fixed],
    }


def _filters(request: Request) -> tuple[str, str]:
    level = str(request.query_params.get("level") or "").strip().lower()
    if level not in applog.LEVELS:
        level = ""
    q = str(request.query_params.get("q") or "").strip().lower()[:200]
    return level, q


@router.get("/logs", include_in_schema=False)
def logs_page(request: Request) -> Response:
    level, q = _filters(request)
    lines, offset = applog.tail(0, limit=FIRST_PAINT)
    shown = [_view(line) for line in lines if _wanted(line, level, q)]
    return render(
        request, "logs.html",
        lines=shown, offset=offset, level=level, q=q, levels=applog.LEVELS,
        path=str(applog.path()), poll=POLL_SECONDS, first_paint=FIRST_PAINT,
    )


@router.get("/logs/tail", include_in_schema=False)
def logs_tail(request: Request) -> Response:
    """The lines since `after`, as rows to append, plus the new offset out of
    band. Answers a plain request too, as JSON-ish text, for a curl."""
    level, q = _filters(request)
    try:
        after = max(0, int(request.query_params.get("after") or 0))
    except ValueError:
        after = 0
    # `after=0` would mean "the last 300 again", and so would an offset past
    # the end of the file - which is what a page holds after a rotation. Both
    # resync to the end: the rows already on the page are not sent twice.
    if after == 0 or after > applog.size():
        _, after = applog.tail(0, limit=1)
    lines, offset = applog.tail(after)
    shown = [_view(line) for line in lines if _wanted(line, level, q)]
    return render(request, "_log_lines.html", lines=shown, offset=offset, level=level, q=q)
