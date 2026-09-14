"""The Feeds page: what is being followed, when it was last checked, and what
that check found.

ADR-008 (revised). Robert asked for feeds to exist beside jobs as something you
can see and manage, and this is that page. It is the jobs board's shape - one
module, one NAV entry, a page template and a fragment that polls itself -
because a second page of that shape is cheap and a person already knows how to
read it.

Three things it owes a reader, and each is a way a subscription becomes a
surprise when it is missing:

* **When it last looked, and what it found.** "Nothing new" is information;
  silence is not.
* **When it stopped working.** A feed whose host has been down for a week says
  so rather than quietly never producing anything again.
* **A way out.** Pause, poll now, unsubscribe - and unsubscribing keeps the
  episodes the feed brought in, because those are the person's, not the
  subscription's.

Polling from this page happens in the request rather than on the watcher's
thread, deliberately: somebody pressed a button and is waiting for the answer,
and the answer is worth the seconds. It runs in the threadpool for the same
reason the preview does - a probe against a third party has no business on the
event loop.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from scribe import applog, db
from scribe.ingest import feeds
from scribe.web import render

router = APIRouter()

# How long a check may take when a person pressed the button, rather than the
# watcher's own budget. The listing carve-out's patient number (ADR-008): the
# person is waiting and said so by clicking.
POLL_TIMEOUT_SECONDS = 60.0


def _feed_view(feed: dict, *, now: float) -> dict:
    """One row as the template reads it.

    `due_in` is negative when a feed is overdue, which is the honest state of a
    feed the watcher has not reached yet rather than an error.
    """
    checked_at = feed["checked_at"]
    interval = int(feed["interval_seconds"] or feeds.DEFAULT_INTERVAL_SECONDS)
    failures = int(feed["failures"] or 0)
    return {
        **feed,
        "checked_at": checked_at,
        "never_checked": checked_at is None,
        "due_in": None if checked_at is None else (checked_at + interval) - now,
        "failing": failures >= feeds.MAX_FAILURES_REPORTED,
        "failures": failures,
        # TASK-044: a feed that has not been answered yet asks on this page,
        # every time it is rendered - a question a closed tab cannot lose.
        "asking": feed["backfill_answered_at"] is None,
        "backfill_offer": feeds.backfill_offer(feed),
    }


def board_context(conn: sqlite3.Connection) -> dict:
    now = time.time()
    rows = feeds.all_feeds(conn)
    return {
        "feeds": [_feed_view(row, now=now) for row in rows],
        "any_failing": any(
            int(row["failures"] or 0) >= feeds.MAX_FAILURES_REPORTED for row in rows
        ),
        "interval_hours": feeds.DEFAULT_INTERVAL_SECONDS // 3600,
        "cap": feeds.MAX_NEW_PER_POLL,
    }


def _get_feed(conn: sqlite3.Connection, feed_id: int) -> dict:
    with db.LOCK:
        row = conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no feed with id {feed_id}")
    return dict(row)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _after_change(request: Request, conn: sqlite3.Connection) -> Response:
    """The fragment for htmx, a redirect for a plain post - the way every
    other action page in this app answers."""
    if _is_htmx(request):
        return render(request, "_feeds_fragment.html", **board_context(conn))
    return RedirectResponse("/feeds", status_code=303)


@router.get("/feeds", include_in_schema=False)
def feeds_page(request: Request) -> Response:
    conn = request.app.state.conn
    ctx = board_context(conn)
    name = "_feeds_fragment.html" if _is_htmx(request) else "feeds.html"
    return render(request, name, **ctx)


@router.get("/feeds/fragment", include_in_schema=False)
def feeds_fragment(request: Request) -> Response:
    """What the board polls. Slower than the jobs board's two seconds: a feed
    changes on the hour, not on the tick."""
    conn = request.app.state.conn
    return render(request, "_feeds_fragment.html", **board_context(conn))


@router.post("/feeds/{feed_id}/pause", include_in_schema=False)
def pause_feed(
    feed_id: int, request: Request, paused: Annotated[str, Form()] = "1"
) -> Response:
    conn = request.app.state.conn
    _get_feed(conn, feed_id)
    feeds.set_paused(conn, feed_id, paused not in ("0", "", "false"))
    return _after_change(request, conn)


@router.post("/feeds/{feed_id}/unsubscribe", include_in_schema=False)
def unsubscribe_feed(feed_id: int, request: Request) -> Response:
    """Stop following. The episodes stay: they are the person's, and they keep
    their own provenance (media.source_url, media.source_id)."""
    conn = request.app.state.conn
    _get_feed(conn, feed_id)
    feeds.unsubscribe(conn, feed_id)
    return _after_change(request, conn)


@router.post("/feeds/{feed_id}/poll", include_in_schema=False)
async def poll_feed(feed_id: int, request: Request) -> Response:
    """Check one feed now, whatever its due date says.

    The button exists to bypass the schedule; it does not bypass the cap. A
    feed that looks entirely new still queues at most MAX_NEW_PER_POLL, because
    the reason for that ceiling - a night of unattended transcription - does
    not become a good idea because somebody is watching this time.
    """
    conn = request.app.state.conn
    feed = _get_feed(conn, feed_id)

    # Imported here rather than at the top: ingest_ui imports this package's
    # render, and a top-level import would be a cycle.
    from scribe.web import ingest_ui, transcribe_dialog

    options = transcribe_dialog.read_defaults(conn).to_params()
    await run_in_threadpool(
        feeds.poll,
        conn,
        feed,
        probe=ingest_ui.urls.probe,
        known_sources=ingest_ui.known_sources,
        options=options,
    )
    return _after_change(request, conn)


@router.post("/feeds/{feed_id}/backfill", include_in_schema=False)
async def backfill_feed(
    feed_id: int,
    request: Request,
    choice: Annotated[str, Form()] = "nothing",
) -> Response:
    """Answer a new feed's question: how much of the back catalogue to fetch.

    The choices are `feeds.BACKFILL_CHOICES`, "all", or "nothing" - anything
    else is a 400 rather than a guess, and so is a choice that would queue
    more than `url_stage.MAX_FAN_OUT` (the message names both numbers, the way
    the episode dialog's refusal does).

    Whatever the answer, the question closes and the feed is followed from
    here like any other.
    """
    conn = request.app.state.conn
    feed = _get_feed(conn, feed_id)

    from scribe.web import ingest_ui, transcribe_dialog

    options = transcribe_dialog.read_defaults(conn).to_params()
    try:
        out = await run_in_threadpool(
            feeds.backfill,
            conn,
            feed,
            choice.strip(),
            probe=ingest_ui.urls.probe,
            known_sources=ingest_ui.known_sources,
            options=options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    applog.log("feeds.answered", feed=feed_id, choice=choice.strip(), queued=len(out["queued"]))
    return _after_change(request, conn)
