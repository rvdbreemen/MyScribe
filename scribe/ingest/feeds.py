"""Feeds as subscriptions: what is due, what is new, and what that queues.

ADR-008 (revised 2026-09-09). A feed you started with stays watched, and every
episode that appears afterwards becomes one `ingest_url` job like any other.

Three decisions shape this file, and each answers a way it could go wrong.

**Due-ness is a date, not a countdown.** `feed.checked_at` is compared against
the clock; nothing sleeps for a day. This is a desktop app that is off more
than it is on, so a laptop closed for a week has to come back and find
everything overdue, and a restart must lose nothing. "Check daily" and "check
at startup" are then the same code rather than two mechanisms that can
disagree, and `checked_at IS NULL` - a feed nobody has looked at yet - is
exactly the feed to look at.

**New means unknown to the library, not merely unseen here.** `feed_seen`
records what a feed held the day it was subscribed, so following The Daily does
not queue its 2970 back episodes. But the real test is `known_sources`, which
asks the media rows and the live jobs: an episode already downloaded, already
queued, or in the trash is not queued again whatever this table remembers.
Deleting something was a decision.

**A poll is bounded.** `MAX_NEW_PER_POLL` caps what one check may start,
because a feed that rewrites its guids looks entirely new and the alternative
is a night of unattended transcription. The cap is reported rather than hidden:
the feed's last result says it was hit.

Nothing here opens a socket by itself - `probe` is passed in, so the tests
drive it and the watcher supplies the real one.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Sequence

from scribe import applog, db, jobs
from scribe.ingest import urls
from scribe.stages import url_stage

MAX_NEW_PER_POLL = 25
"""How many episodes one check of one feed may queue.

A feed that changed its guids, or a channel that reordered, looks entirely new;
without a ceiling the next unattended poll would start hundreds of downloads
and the transcriptions behind them. Twenty-five is a fortnight of a daily
podcast and several months of a weekly one, so a feed that genuinely raced
ahead still catches up over a few polls, while a feed that broke its own
identity stops at something a person can look at and undo."""

DEFAULT_INTERVAL_SECONDS = 86400
"""A day. Robert asked for daily or at startup, and due-dates give both."""

MAX_FAILURES_REPORTED = 5
"""After this many consecutive failures the Feeds page stops saying "trying
again" and starts saying the feed is not answering. It keeps polling: a podcast
host that is down for a week is not a reason to forget the podcast."""


def subscribe(
    conn: sqlite3.Connection,
    url: str,
    *,
    title: str = "",
    folder_id: int | None = None,
    entries: Sequence[dict] = (),
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    now: float | None = None,
) -> int:
    """Start watching a feed, and queue nothing.

    The episodes it holds today are recorded as seen. Subscribing is a promise
    about the future, not a request for the archive - and an existing
    subscription is updated rather than duplicated, because a person who
    imports from the same feed twice meant to follow it, not to follow it
    twice.
    """
    stamp = time.time() if now is None else now
    with db.LOCK:
        row = conn.execute("SELECT id FROM feed WHERE url=?", (url,)).fetchone()
        if row is None:
            feed_id = int(
                conn.execute(
                    "INSERT INTO feed(url, title, interval_seconds, folder_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?) RETURNING id",
                    (url, title[:200], int(interval_seconds), folder_id, stamp),
                ).fetchone()["id"]
            )
        else:
            feed_id = int(row["id"])
            conn.execute(
                "UPDATE feed SET title=COALESCE(NULLIF(?, ''), title),"
                " folder_id=COALESCE(?, folder_id), paused=0 WHERE id=?",
                (title[:200], folder_id, feed_id),
            )
        for entry in entries:
            source_id = str(entry.get("source_id") or "").strip()
            if source_id:
                conn.execute(
                    "INSERT OR IGNORE INTO feed_seen(feed_id, source_id) VALUES (?, ?)",
                    (feed_id, source_id),
                )
        conn.commit()
    return feed_id


def unsubscribe(conn: sqlite3.Connection, feed_id: int) -> None:
    """Stop watching. The episodes it brought in stay: they are yours, and they
    still say where they came from (media.source_url, media.source_id)."""
    with db.LOCK:
        conn.execute("DELETE FROM feed WHERE id=?", (feed_id,))
        conn.commit()


def set_paused(conn: sqlite3.Connection, feed_id: int, paused: bool) -> None:
    with db.LOCK:
        conn.execute("UPDATE feed SET paused=? WHERE id=?", (1 if paused else 0, feed_id))
        conn.commit()


def all_feeds(conn: sqlite3.Connection) -> list[dict]:
    with db.LOCK:
        rows = conn.execute("SELECT * FROM feed ORDER BY title COLLATE NOCASE, id").fetchall()
    return [dict(row) for row in rows]


def due_feeds(conn: sqlite3.Connection, *, now: float | None = None) -> list[dict]:
    """The feeds it is time to check.

    A feed never checked is due, which is what makes a fresh subscription and a
    restart behave the same. A paused one never is.
    """
    stamp = time.time() if now is None else now
    with db.LOCK:
        rows = conn.execute(
            "SELECT * FROM feed WHERE paused = 0"
            " AND (checked_at IS NULL OR checked_at + interval_seconds <= ?)"
            " ORDER BY checked_at IS NOT NULL, checked_at, id",
            (stamp,),
        ).fetchall()
    return [dict(row) for row in rows]


def _record(
    conn: sqlite3.Connection, feed_id: int, *, result: str, failed: bool, now: float
) -> None:
    with db.LOCK:
        conn.execute(
            "UPDATE feed SET checked_at=?, last_result=?,"
            " failures = CASE WHEN ? THEN failures + 1 ELSE 0 END WHERE id=?",
            (now, result[:500], 1 if failed else 0, feed_id),
        )
        conn.commit()


def new_entries(
    conn: sqlite3.Connection,
    feed_id: int,
    entries: Sequence[dict],
    states: Sequence[str | None],
) -> list[dict]:
    """The entries this feed has not already accounted for.

    Two questions, and both have to say yes. `feed_seen` remembers what the
    feed held when the subscription started; `states` is `known_sources`'
    answer per entry - "queued", "library", "trash" or None - which is the
    same answer the episode list shows a person. Anything but None means the
    library has already decided about this episode, the trash included:
    deleting it was a decision.

    An entry with no source id at all is skipped rather than guessed at; it
    would be queued again on every poll for ever.
    """
    with db.LOCK:
        seen = {
            str(row["source_id"])
            for row in conn.execute(
                "SELECT source_id FROM feed_seen WHERE feed_id=?", (feed_id,)
            )
        }
    out: list[dict] = []
    for entry, state in zip(entries, list(states) + [None] * len(entries)):
        source_id = str(entry.get("source_id") or "").strip()
        if not source_id or source_id in seen or state is not None:
            continue
        seen.add(source_id)  # one poll must not queue the same episode twice
        out.append(entry)
    return out


def poll(
    conn: sqlite3.Connection,
    feed: dict,
    *,
    probe: Callable[..., urls.UrlInfo],
    known_sources: Callable[[sqlite3.Connection, list[dict]], Sequence[str | None]],
    options: dict,
    now: float | None = None,
) -> dict:
    """Check one feed and queue what is new. Returns what it did.

    Every exit records `checked_at`, including the failures: a feed whose host
    is down must not be retried in a tight loop, and the next attempt is one
    interval away like any other.
    """
    stamp = time.time() if now is None else now
    feed_id = int(feed["id"])
    try:
        info = probe(feed["url"], limit=None)
    except Exception as exc:  # noqa: BLE001 - any failure is the feed's failure
        result = f"{type(exc).__name__}: {exc}"
        _record(conn, feed_id, result=result[:200], failed=True, now=stamp)
        return {"queued": [], "new": 0, "error": result}

    entries = list(info.entries or [])
    if info.kind != "playlist":
        result = "answered as a single item, not a feed"
        _record(conn, feed_id, result=result, failed=True, now=stamp)
        return {"queued": [], "new": 0, "error": result}

    # The listing's own function, given the same shape it gets there: the
    # entries themselves, and one state back per entry. Using it rather than a
    # query of our own is what stops the marks a person sees and the poller's
    # judgement from ever drifting apart (ADR-008).
    states = known_sources(conn, list(entries))
    fresh = new_entries(conn, feed_id, entries, states)
    capped = fresh[:MAX_NEW_PER_POLL]

    params_list = [
        {
            "url": entry["url"],
            "folder_id": feed["folder_id"],
            url_stage.OPTIONS_KEY: options,
            "from_playlist": True,
            url_stage.ENTRY_KEY: {
                "title": entry.get("title") or "",
                "source_id": entry.get("source_id") or "",
            },
            url_stage.SOURCE_KEY: {"url": feed["url"], "title": info.title or feed["title"]},
            url_stage.FEED_KEY: feed_id,
        }
        for entry in capped
    ]
    queued = jobs.enqueue_many(conn, url_stage.JOB_TYPE, params_list) if params_list else []

    with db.LOCK:
        for entry in capped:
            conn.execute(
                "INSERT OR IGNORE INTO feed_seen(feed_id, source_id) VALUES (?, ?)",
                (feed_id, str(entry.get("source_id") or "")),
            )
        conn.commit()

    if not fresh:
        result = "nothing new"
    elif len(fresh) > len(capped):
        result = (
            f"{len(fresh)} new, queued {len(capped)} - the rest wait for the next check"
        )
    else:
        result = f"{len(capped)} new" if capped else "nothing new"
    _record(conn, feed_id, result=result, failed=False, now=stamp)

    if queued:
        applog.log("feeds.poll", feed=feed["url"], new=len(fresh), queued=len(queued))
    return {"queued": queued, "new": len(fresh), "capped": len(fresh) > len(capped)}


# --- the thread that does the checking -----------------------------------------------

POLL_INTERVAL_SECONDS = 300.0
"""How often the thread wakes to ask which feeds are due.

Not how often a feed is checked - that is the feed's own interval, a day by
default. This is only the granularity of "is it time yet", so a feed comes due
within five minutes of its hour rather than on the second. Cheap: one indexed
query over a table with as many rows as the person has podcasts.

`watching.Watcher` ticks every two seconds because a dropped file should appear
at once. Nothing about a daily feed rewards that.
"""


def _survive(step: Callable, *args) -> None:
    """Run one step and live through whatever it does.

    `Supervisor._loop` and `watching.Watcher` do the same, for the same reason:
    a background thread that dies on a transient error - a host that went away,
    a database busy timeout - stops doing its job with nobody to tell. Here it
    wraps each FEED as well as each tick, so one podcast whose host is down
    does not stop the others being checked.
    """
    try:
        step(*args)
    except Exception:  # noqa: BLE001 - the whole point is to catch everything
        traceback.print_exc()


def _poll_one(
    conn: sqlite3.Connection,
    feed: dict,
    probe: Callable[..., urls.UrlInfo],
    known_sources: Callable[[sqlite3.Connection, list[dict]], Sequence[str | None]],
    options: dict,
) -> dict:
    """`poll` with its keyword collaborators bound, so `_survive` can take it.

    A named function rather than a lambda in the loop: a traceback that says
    `_poll_one` names the thing that failed, and a closure over the loop
    variable is the classic way to check every feed against the last one.
    """
    return poll(conn, feed, probe=probe, known_sources=known_sources, options=options)


class FeedWatcher:
    """Checks the feeds that are due, on its own thread.

    Deliberately not part of `Supervisor._loop`, whose tick is `jobs.claim_next`
    and nothing else (ADR-001 keeps it thin on purpose): feed I/O there would
    delay claiming, and a slow feed would stall the queue. Shaped after
    `watching.Watcher` instead - its own connection, `_survive` around every
    step - because that is the pattern this process already runs for periodic
    work.

    Its own connection, never `app.state.conn`: a probe takes seconds against a
    third party, and the jobs board must go on answering while it does.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        probe: Callable[..., urls.UrlInfo] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.poll_interval = poll_interval
        self._probe = probe
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the tick on a daemon thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="scribe-feeds", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def tick(self, conn: sqlite3.Connection) -> int:
        """Check every feed that is due; returns how many were checked.

        Each feed is wrapped on its own, so a podcast whose host is down costs
        that feed its turn and nothing else.
        """
        from scribe.web import ingest_ui, transcribe_dialog

        due = due_feeds(conn)
        if not due:
            return 0
        options = transcribe_dialog.read_defaults(conn).to_params()
        probe = self._probe or urls.probe
        for feed in due:
            # `poll` takes its collaborators by keyword, so the call is bound
            # here and handed to `_survive` as one thunk. Wrapped per feed and
            # not per tick: a podcast whose host is down costs that feed its
            # turn, not the others theirs.
            _survive(
                _poll_one,
                conn,
                feed,
                probe,
                ingest_ui.known_sources,
                options,
            )
        return len(due)

    def _loop(self) -> None:
        """Its own connection, opened here and closed here."""
        conn = db.connect(self.db_path)
        try:
            while not self._stop_event.is_set():
                _survive(self.tick, conn)
                self._stop_event.wait(self.poll_interval)
        finally:
            conn.close()
