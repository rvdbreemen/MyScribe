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

import json
import sqlite3
import sys
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urldefrag

from scribe import applog, db, doctor, jobs
from scribe.ingest import urls
from scribe.options import read_defaults
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


def newest_first(entries: Sequence[dict]) -> list[dict]:
    """The listing in the order a person should be offered it: newest first.

    Deliberately not a sort. `urls._entries` keeps yt-dlp's document order, and
    a feed puts its newest episode at the top; `timestamp` is optional and a
    flat YouTube listing has none at all, so sorting on it would reorder half
    the sources this app accepts and pick an episode other than the one at the
    top of the list the person just looked at.

    The one case this gets wrong is a feed published oldest-first, and this
    function is the single place to fix it if one ever turns up.
    """
    return list(entries)


def _entry_params(
    entry: dict,
    *,
    feed_id: int,
    feed_url: str,
    feed_title: str,
    folder_id: int | None,
    options: dict,
) -> dict:
    """The params one episode's `ingest_url` job runs with.

    Three callers build this now - a new feed's first episode, an answer to
    the question, and a poll - so a key added for one arrives for all three.
    """
    return {
        "url": entry["url"],
        "folder_id": folder_id,
        url_stage.OPTIONS_KEY: options,
        "from_playlist": True,
        url_stage.ENTRY_KEY: {
            "title": entry.get("name") or entry.get("title") or "",
            "source_id": entry.get("source_id") or "",
        },
        url_stage.SOURCE_KEY: {"url": feed_url, "title": feed_title},
        url_stage.FEED_KEY: feed_id,
    }


def _mark_seen(conn: sqlite3.Connection, feed_id: int, entries: Sequence[dict]) -> None:
    for entry in entries:
        source_id = str(entry.get("source_id") or "").strip()
        if source_id:
            conn.execute(
                "INSERT OR IGNORE INTO feed_seen(feed_id, source_id) VALUES (?, ?)",
                (feed_id, source_id),
            )


def is_asking(conn: sqlite3.Connection, feed_id: int) -> bool:
    """Is this feed still waiting for an answer about its back catalogue?"""
    row = conn.execute(
        "SELECT backfill_answered_at FROM feed WHERE id=?", (feed_id,)
    ).fetchone()
    return row is not None and row["backfill_answered_at"] is None


def asking(conn: sqlite3.Connection) -> list[dict]:
    """Every feed with the question still open, oldest first.

    The Feeds page reads this on every GET, which is what makes the question
    survive a closed tab: it is a row on a page, not a flash somebody missed.
    """
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM feed WHERE backfill_answered_at IS NULL ORDER BY created_at, id"
        )
    ]


def answer(conn: sqlite3.Connection, feed_id: int, *, now: float | None = None) -> None:
    """Close the question: from here the feed is followed like any other.

    Queues nothing by itself - what to fetch is the caller's decision (the
    Feeds page offers one episode, the last 3, 5 or 10, or all) and this only
    records that the decision was made. Idempotent, and it never reopens.
    """
    stamp = time.time() if now is None else now
    with db.LOCK:
        conn.execute(
            "UPDATE feed SET backfill_answered_at=COALESCE(backfill_answered_at, ?) WHERE id=?",
            (stamp, feed_id),
        )
        conn.commit()


def subscribe(
    conn: sqlite3.Connection,
    url: str,
    *,
    title: str = "",
    folder_id: int | None = None,
    entries: Sequence[dict] = (),
    options: dict | None = None,
    answered: bool = False,
    total: int | None = None,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    now: float | None = None,
) -> int:
    """Start watching a feed; fetch its newest episode and ask about the rest.

    A feed nobody has heard is a promise, not a request for the archive - but
    the old rule (record everything as seen, queue nothing) meant the person
    who just added it heard nothing at all until tomorrow, and the dialog's
    "tick everything" meant five hundred downloads. So a new feed queues
    exactly one episode, `newest_first(entries)[0]`, marks only that one seen,
    and leaves `backfill_answered_at` NULL: the Feeds page asks how to go on,
    and until it is answered nothing else is fetched and nothing else is
    marked, so answering "everything" later still gets the rest (TASK-044).

    That first episode queues at the default priority, deliberately: somebody
    is standing there waiting to hear whether this feed is any good. Every
    other thing a feed queues runs at `url_stage.BULK_PRIORITY` (TASK-046).

    `answered=True` is the dialog's path: a person who ticked episodes has
    answered the question by answering it, so the whole listing is recorded as
    seen and nothing is queued here - the ticked jobs are the route's.

    `total` is how long the listing is, when the caller holds the count but not
    the entries. The "follow, decide later" button is that caller: the dialog
    has 2970 episodes on screen and posts back the newest one and the number,
    rather than 2970 JSON blobs, and `backfill_offer` needs the number to say
    what "everything" would fetch. It defaults to `len(entries)`.

    An existing subscription is updated rather than duplicated, and importing
    the same feed twice never reopens a question that was closed.
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
        if answered:
            _mark_seen(conn, feed_id, entries)
            conn.execute(
                "UPDATE feed SET backfill_answered_at=COALESCE(backfill_answered_at, ?)"
                " WHERE id=?",
                (stamp, feed_id),
            )
        conn.commit()

    first = newest_first(entries)[:1] if (not answered and row is None) else []
    if first:
        with db.LOCK:
            _mark_seen(conn, feed_id, first)
            conn.execute(
                "UPDATE feed SET backfill_total=? WHERE id=?",
                (len(entries) if total is None else max(int(total), len(entries)), feed_id),
            )
            conn.commit()
        jobs.enqueue(
            conn,
            url_stage.JOB_TYPE,
            params=_entry_params(
                first[0],
                feed_id=feed_id,
                feed_url=url,
                feed_title=title or url,
                folder_id=folder_id,
                options=options or {},
            ),
        )
    return feed_id


BACKFILL_CHOICES: tuple[int, ...] = (3, 5, 10)
"""How many of the newest episodes an answer may ask for, besides "everything"
and "nothing more". Three fixed numbers rather than a box to type in: the
questions a person actually has are "just that one", "the last few" and "all
of it", and a free number invites the two hundred TASK-044 exists to prevent.
"""


def backfill_offer(feed: dict) -> list[dict]:
    """What the question offers, with what each choice would fetch.

    Counted against the listing recorded when the feed was subscribed, minus
    the one episode that already came: the point of showing a number before
    the click is that "everything" on a 2970-episode archive should look like
    what it is.
    """
    total = int(feed["backfill_total"] or 0)
    rest = max(total - 1, 0)
    offer = [{"choice": str(n), "label": f"the last {n}", "count": max(min(n, total) - 1, 0)}
             for n in BACKFILL_CHOICES]
    offer.append({"choice": "all", "label": "everything", "count": rest})
    return [item for item in offer if item["count"] > 0]


def backfill(
    conn: sqlite3.Connection,
    feed: dict,
    choice: str,
    *,
    probe: Callable[..., urls.UrlInfo],
    known_sources: Callable[[sqlite3.Connection, list[dict]], Sequence[str | None]],
    options: dict,
    now: float | None = None,
) -> dict:
    """Answer the question: fetch the newest `choice` episodes, then follow.

    "nothing" closes the question and queues nothing; a number takes that many
    from the top of the listing; "all" takes the listing. Whatever the answer,
    the question closes - a person who said "just that one" is not asked again
    every day.

    Everything queued here is bulk (`url_stage.BULK_PRIORITY`): the person is
    not waiting for it the way they waited for the first episode.
    """
    feed_id = int(feed["id"])
    if choice == "nothing":
        answer(conn, feed_id, now=now)
        return {"queued": [], "result": "nothing more"}
    if choice != "all" and choice not in {str(n) for n in BACKFILL_CHOICES}:
        raise ValueError(f"{choice!r} is not one of the choices on offer")
    # The sharp case for TASK-043's floor: this marks everything it queues as
    # seen and closes the question, both unconditionally, so a disk below the
    # floor would turn one press of "everything" into a back catalogue that
    # failed DISK_LOW and is never offered again. Refuse before anything is
    # queued, marked or answered, and the question stays open for later.
    doctor.require_disk_headroom()

    # `public`: the feed's address is the feed's word by now, not the person's
    # - its document may 302 anywhere since it was followed (GitHub #41).
    info = probe(feed["url"], limit=None, public=True)
    entries = newest_first(list(info.entries or []))
    wanted = entries if choice == "all" else entries[: int(choice)]
    states = known_sources(conn, list(wanted))
    fresh = new_entries(conn, feed_id, wanted, states)
    if len(fresh) > url_stage.MAX_FAN_OUT:
        raise ValueError(
            f"that would queue {len(fresh)} downloads, and this app queues at most "
            f"{url_stage.MAX_FAN_OUT} from one link. Choose fewer."
        )

    params_list = [
        _entry_params(
            entry,
            feed_id=feed_id,
            feed_url=feed["url"],
            feed_title=info.title or feed["title"],
            folder_id=feed["folder_id"],
            options=options,
        )
        for entry in fresh
    ]
    queued = (
        jobs.enqueue_many(conn, url_stage.JOB_TYPE, params_list, priority=url_stage.BULK_PRIORITY)
        if params_list
        else []
    )
    with db.LOCK:
        _mark_seen(conn, feed_id, fresh)
        conn.commit()
    answer(conn, feed_id, now=now)
    applog.log("feed.backfill", feed=feed_id, choice=choice, queued=len(queued))
    return {"queued": queued, "result": f"queued {len(queued)}"}


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
    # A feed whose question is still open is left alone, and the gate is here
    # rather than in `due_feeds`: the watcher reaches a new feed within one
    # poll interval (its checked_at is NULL, so it is due at once), and the
    # Feeds page's "check now" button bypasses due_feeds entirely. One gate
    # covers the thread and the button (TASK-044).
    if is_asking(conn, feed_id):
        result = "asking how much of the back catalogue to fetch"
        _record(conn, feed_id, result=result, failed=False, now=stamp)
        return {"queued": [], "new": 0, "result": result}
    # Before the probe, because an episode this poll queues is marked seen the
    # moment it is queued and never offered again - so queueing into a disk
    # that will refuse every download (TASK-043) does not delay the catalogue,
    # it loses it. Checking here costs one statvfs and leaves the entries
    # unseen for the next interval.
    try:
        doctor.require_disk_headroom()
    except doctor.NotEnoughDisk as exc:
        result = f"not checked: {exc}"
        # TASK-091: the stored line gives up the path's middle, never the
        # amounts or "nothing was downloaded"; the answer keeps it whole.
        prefix = "not checked: "
        stored = prefix + exc.fitted(200 - len(prefix))
        _record(conn, feed_id, result=stored[:200], failed=True, now=stamp)
        return {"queued": [], "new": 0, "error": result}
    # The whole listing, not the top of it (GitHub #36). `new_entries` is a
    # set difference and finds the new episode wherever it sits; a cap
    # (`playlistend`) would keep the first entries in document order, and a
    # source that lists oldest first would never show its new ones again.
    # On the watcher's thread the wait for a huge listing is bounded by
    # `PROBE_TIMEOUT_SECONDS`, and a listing that answers later is polled on
    # a later wake; the Feeds page's "check now" hands in the plain probe and
    # waits for it, on a threadpool worker.
    try:
        # Unattended, so under the public guard: the feed document, every
        # redirect it answers with and a page it is discovered from may not
        # point at this machine or its network (GitHub #41, TASK-072).
        info = probe(feed["url"], limit=None, public=True)
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
        _entry_params(
            entry,
            feed_id=feed_id,
            feed_url=feed["url"],
            feed_title=info.title or feed["title"],
            folder_id=feed["folder_id"],
            options=options,
        )
        for entry in capped
    ]
    # Below the default, like every other bulk import: the watcher queues
    # what nobody asked for right now, so a recording started by hand goes
    # first (url_stage.BULK_PRIORITY).
    queued = (
        jobs.enqueue_many(conn, url_stage.JOB_TYPE, params_list, priority=url_stage.BULK_PRIORITY)
        if params_list
        else []
    )

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

TICK_BUDGET_SECONDS = 180.0
"""How long one wake may spend polling feeds before the rest wait a wake.

Feeds are polled serially: a string of slow hosts would otherwise hold every
later feed hostage for as long as they take. Checked between feeds, so it
cannot stop a probe already running; `PROBE_TIMEOUT_SECONDS` bounds each one.
Whatever the budget does not reach stays due: `checked_at` is only stamped
for feeds the tick got to, so the next wake picks up exactly where this one
stopped. Three minutes is a few slow feeds' worth, and comfortably inside the
five-minute wake.
"""

PROBE_TIMEOUT_SECONDS = 150.0
"""How long the watcher waits for one feed's probe before giving up on it.

GitHub #36. `urls.SOCKET_TIMEOUT` bounds one socket read, not the probe: a
host that drips a byte every few seconds never trips it, and a listing that
keeps answering runs as long as it has entries. So the probe runs on a
worker thread and the tick waits this long; past it the feed is recorded as
failed, like a probe that raised, and the tick goes on to the next feed.

Sized for the whole listing a poll asks for. A feed is one document, read in
seconds even at a few thousand episodes. A channel is paged, at the 40 entries
a second measured in `urls.probe_opts` (2026-09-08): 3000 entries take about
75 s, so two and a half minutes is twice that, and a listing of up to about
6000 entries still answers within it. The bound is how long the tick waits,
not how much work a feed may do: a larger channel is recorded as not
answering for now, and the listing its worker finishes later is polled on
the next wake (GitHub #36, from the review of PR #62). A dripping host costs
at most this once per due time, since a feed whose worker still runs is
skipped, and one that never finishes keeps counting as failing. The budget
is looked at between feeds, so the longest tick is `TICK_BUDGET_SECONDS`
plus this, about five and a half minutes; the next wake then waits its
interval from there.
Read at call time: the tests patch it.
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


def _job_params(text: str | None) -> dict:
    try:
        params = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return params if isinstance(params, dict) else {}


_KEYS_PER_STATEMENT = 5000
"""Bound parameters per `known_sources` media query: well under SQLite's
32766 (>= 3.35, asserted in `db.connect`), and a dialog listing of
`ingest_ui.MAX_LISTED` entries is still one statement per column (GitHub #36)."""


def known_sources(conn: sqlite3.Connection, entries: list[dict]) -> list[str | None]:
    """One state per listed entry: "queued", "library", "trash" or None.

    Matched by the entry's URL without its fragment and by its extractor-
    scoped `source_id`, against the media rows' provenance (v10) and the
    `ingest_url` jobs still queued or running. Precedence queued > library >
    trash: the transient fact that stops a duplicate download wins, and "in
    library" reappears on its own once the job finishes. A trashed recording
    is named as such rather than hidden, because re-importing it would dedupe
    onto the trashed row and the finished transcript would be invisible.

    The media side is asked in chunks of `_KEYS_PER_STATEMENT` keys. One
    statement for the whole listing bound two parameters per entry, and a
    feed poll lists the whole source: past 16383 entries that was SQLite's
    "too many SQL variables" (32766), raised outside the probe's `try`, so the
    feed was never recorded and stayed due on every wake (GitHub #36). Under
    `db.LOCK`, like every read in this process (ADR-002).

    Lives here rather than in `scribe.web.ingest_ui`, where it was born: the
    feed watcher asks the same question on a thread that may not import
    scribe.web.
    """
    url_keys = [urldefrag(str(entry.get("url") or "")).url for entry in entries]
    id_keys = [entry.get("source_id") or None for entry in entries]
    wanted_urls = sorted({key for key in url_keys if key})
    wanted_ids = sorted({key for key in id_keys if key})
    by_url: dict[str, str] = {}
    by_id: dict[str, str] = {}

    with db.LOCK:
        for column, wanted in (("source_url", wanted_urls), ("source_id", wanted_ids)):
            for start in range(0, len(wanted), _KEYS_PER_STATEMENT):
                chunk = wanted[start : start + _KEYS_PER_STATEMENT]
                rows = conn.execute(
                    "SELECT source_url, source_id, trashed_at FROM media"
                    f" WHERE {column} IN ({','.join('?' * len(chunk))})",
                    chunk,
                ).fetchall()
                # A row can come back from more than one chunk; "library"
                # winning over "trash" makes that harmless.
                for row in rows:
                    state = "library" if row["trashed_at"] is None else "trash"
                    for key, table in ((row["source_url"], by_url), (row["source_id"], by_id)):
                        if key and table.get(key) != "library":
                            table[key] = state
        live = conn.execute(
            "SELECT params_json FROM job WHERE type=? AND status IN ('queued', 'running')",
            (url_stage.JOB_TYPE,),
        ).fetchall()

    queued_urls: set[str] = set()
    queued_ids: set[str] = set()
    for row in live:
        params = _job_params(row["params_json"])
        queued_urls.add(urldefrag(str(params.get("url") or "")).url)
        entry = params.get(url_stage.ENTRY_KEY)
        if isinstance(entry, dict) and entry.get("source_id"):
            queued_ids.add(str(entry["source_id"]))

    out: list[str | None] = []
    for url_key, id_key in zip(url_keys, id_keys):
        if (url_key and url_key in queued_urls) or (id_key and id_key in queued_ids):
            out.append("queued")
            continue
        found = {by_url.get(url_key or ""), by_id.get(id_key or "")} - {None}
        out.append("library" if "library" in found else ("trash" if "trash" in found else None))
    return out


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


def _still_running(conn: sqlite3.Connection, feed_id: int) -> None:
    """Record a due feed whose previous probe has not come back as failed."""
    _record(
        conn,
        feed_id,
        result=f"did not answer: the check given up on after {PROBE_TIMEOUT_SECONDS:g} s"
        " is still running",
        failed=True,
        now=time.time(),
    )


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
        # The probe each feed's tick stopped waiting for, by feed id: still
        # running, or done and not yet polled. A probe that answers in time is
        # never left here. Read and written only by the tick, on this
        # watcher's thread (GitHub #36).
        self._probing: dict[int, Future] = {}

    def start(self) -> None:
        """Start the tick on a daemon thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="scribe-feeds", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the loop to finish and join it.

        A join that runs out of time is said on stderr and the thread is
        *kept*, the same answer `watching.Watcher.stop` gives for the same
        reason: a thread mid-poll will finish on its own, and dropping the
        reference made that indistinguishable from a clean stop.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout)
        if thread.is_alive():
            print(
                f"scribe: the feed-checking thread is still running {timeout:g}s after"
                " being asked to stop; it is most likely mid-poll and will finish"
                " on its own.",
                file=sys.stderr,
                flush=True,
            )
            return
        self._thread = None

    def _bounded(
        self, feed_id: int, probe: Callable[..., urls.UrlInfo]
    ) -> Callable[..., urls.UrlInfo]:
        """`probe` with a wall clock on it: run on a worker thread, given up on
        after `PROBE_TIMEOUT_SECONDS` with a `TimeoutError` that `poll` records
        like any other failed probe (GitHub #36).

        Only the probe runs on the worker - network I/O and nothing else. The
        recording, `known_sources` and the queueing stay in `poll` on this
        watcher's thread and connection, so a worker that answers after it was
        given up on writes nothing itself: its Future stays in `_probing` and
        `_poll_late` hands its listing to `poll` on a later wake.

        A daemon thread rather than a subprocess, which could be killed:
        `probe` is passed in (the tests hand it a closure, which a spawned
        child cannot unpickle), a child would pay an interpreter start and a
        yt-dlp import per feed, and `UrlInfo` would have to cross back pickled.
        What a thread cannot be is stopped, so its Future is remembered in
        `_probing` and its feed is skipped, and recorded as failing, while it
        runs - at most one stuck thread per feed, and `urls.SOCKET_TIMEOUT`
        still ends one whose host goes silent.
        The same trade `ingest_ui.probe_or_none` makes for the dialog.
        """

        def bounded(url: str, **kwargs) -> urls.UrlInfo:
            outcome: Future = Future()

            def run() -> None:
                try:
                    outcome.set_result(probe(url, **kwargs))
                except BaseException as exc:  # noqa: BLE001 - handed to the tick as is
                    outcome.set_exception(exc)

            worker = threading.Thread(target=run, name=f"scribe-feed-{feed_id}", daemon=True)
            self._probing[feed_id] = outcome
            worker.start()
            worker.join(PROBE_TIMEOUT_SECONDS)
            if not outcome.done():
                raise TimeoutError(f"did not answer within {PROBE_TIMEOUT_SECONDS:g} s")
            # Answered in time and polled now, so not left for `_poll_late`
            # to poll a second time on the next wake.
            del self._probing[feed_id]
            return outcome.result()

        return bounded

    def _poll_late(
        self, conn: sqlite3.Connection, feed_id: int, outcome: Future, options: dict
    ) -> None:
        """Poll the listing a probe brought back after the tick stopped waiting.

        GitHub #36, from the review of PR #62: a channel of 6000-16000 entries
        lists for longer than `PROBE_TIMEOUT_SECONDS`, and throwing its answer
        away failed it at every due time where the unbounded poll had followed
        it, slowly. Its listing goes through `poll` like any other - here, on
        the watcher's thread and connection - so it is queued and recorded the
        same way, gates included.

        A late failure is dropped: the timeout already recorded this due
        time's failure, and a second record would count one check twice. A
        feed unsubscribed or paused in the meantime is not polled either.
        """
        if outcome.exception() is not None:
            return
        with db.LOCK:
            row = conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone()
        if row is None or row["paused"]:
            return
        info = outcome.result()
        _poll_one(conn, dict(row), lambda url, **kwargs: info, known_sources, options)

    def tick(self, conn: sqlite3.Connection) -> int:
        """Check every feed that is due; returns how many were checked.

        Each feed is wrapped on its own, so a podcast whose host is down costs
        that feed its turn and nothing else. And the wake itself is bounded
        (`TICK_BUDGET_SECONDS`): a string of slow feeds spends the budget and
        stops, leaving the rest due for the next wake rather than stacking an
        afternoon of polling in front of them. The budget is looked at between
        feeds; one probe that hangs is bounded by `PROBE_TIMEOUT_SECONDS`
        (GitHub #36). A listing that answered after an earlier tick stopped
        waiting is polled first, whether or not its feed is due: the timeout
        stamped `checked_at`, so it is not.
        """
        options = read_defaults(conn).to_params()
        late = [(fid, outcome) for fid, outcome in self._probing.items() if outcome.done()]
        for feed_id, outcome in late:
            # Out of `_probing` before the poll, so one that raises under
            # `_survive` is not polled again on every wake.
            del self._probing[feed_id]
            _survive(self._poll_late, conn, feed_id, outcome, options)
        due = due_feeds(conn)
        if not due:
            return len(late)
        probe = self._probe or urls.probe
        deadline = time.monotonic() + TICK_BUDGET_SECONDS
        checked = 0
        for feed in due:
            if checked and time.monotonic() >= deadline:
                break
            feed_id = int(feed["id"])
            previous = self._probing.get(feed_id)
            if previous is not None and not previous.done():
                # Its last probe was given up on and is still running. Not
                # probed a second time - another thread every wake is how
                # hung workers would pile up - but recorded as this due
                # time's failure: left due and unrecorded, the counter never
                # reached MAX_FAILURES_REPORTED and the Feeds page said
                # "trying again" for ever. `checked_at` is stamped, so it
                # counts once per due time, not once per wake (GitHub #36).
                _survive(_still_running, conn, feed_id)
                continue
            # One that finished during this tick is superseded by a fresh poll.
            self._probing.pop(feed_id, None)
            # `poll` takes its collaborators by keyword, so the call is bound
            # here and handed to `_survive` as one thunk. Wrapped per feed and
            # not per tick: a podcast whose host is down costs that feed its
            # turn, not the others theirs.
            _survive(
                _poll_one, conn, feed, self._bounded(feed_id, probe), known_sources, options
            )
            checked += 1
        return checked + len(late)

    def _loop(self) -> None:
        """Its own connection, opened here and closed here."""
        conn = db.connect(self.db_path)
        try:
            while not self._stop_event.is_set():
                _survive(self.tick, conn)
                self._stop_event.wait(self.poll_interval)
        finally:
            conn.close()
