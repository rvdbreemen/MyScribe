"""TASK-025: what a subscription is, when it is due, and what a poll queues.

Nothing here opens a socket: `probe` is passed in, so a feed can be made to
answer with anything - including by raising, which is the case that decides
whether one bad feed stops the watching.
"""

from __future__ import annotations

import json

import pytest

from scribe import db, jobs
from scribe.ingest import feeds, urls
from scribe.stages import url_stage
from tests.test_llm_tasks import conn  # noqa: F401  (fixture)

FEED_URL = "https://example.test/podcast.xml"
OPTIONS = {"tier": "turbo"}


def entry(n: int) -> dict:
    return {
        "url": f"https://cdn.test/ep{n}.mp3",
        "title": f"Episode {n}",
        "source_id": f"Generic:g{n}",
    }


def playlist(*entries: dict, title: str = "The Feed") -> urls.UrlInfo:
    return urls.UrlInfo(
        kind="playlist",
        title=title,
        duration=None,
        uploader="",
        webpage_url=FEED_URL,
        entries=list(entries),
    )


def probing(info, *, raises: Exception | None = None):
    def probe(url, **kwargs):
        if raises is not None:
            raise raises
        return info

    return probe


def nothing_known(conn, entries):
    """`known_sources`' shape: one state per entry, None meaning the library
    has never decided anything about it."""
    return [None] * len(entries)


def _queued(conn) -> list[dict]:
    return [
        json.loads(row["params_json"])
        for row in conn.execute(
            "SELECT params_json FROM job WHERE type=? ORDER BY id", (url_stage.JOB_TYPE,)
        )
    ]


def _feed(conn, feed_id) -> dict:
    return dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())


def _poll(conn, feed_id, info, *, raises=None, known=nothing_known, now=1000.0):
    return feeds.poll(
        conn,
        _feed(conn, feed_id),
        probe=probing(info, raises=raises),
        known_sources=known,
        options=OPTIONS,
        now=now,
    )


# --- subscribing ------------------------------------------------------------------


def test_subscribing_queues_nothing_and_remembers_what_was_there(conn):
    """Subscribing is a promise about the future, not a request for the
    archive. Following The Daily must not queue its 2970 back episodes."""
    feed_id = feeds.subscribe(conn, FEED_URL, title="The Feed", entries=[entry(1), entry(2)])

    assert _queued(conn) == []
    seen = {
        row["source_id"]
        for row in conn.execute("SELECT source_id FROM feed_seen WHERE feed_id=?", (feed_id,))
    }
    assert seen == {"Generic:g1", "Generic:g2"}


def test_subscribing_twice_follows_one_feed_not_two(conn):
    """A person who imports from the same feed again meant to follow it, not
    to follow it twice - and two rows would race each other into queueing every
    new episode twice."""
    first = feeds.subscribe(conn, FEED_URL, title="The Feed", entries=[entry(1)])
    second = feeds.subscribe(conn, FEED_URL, title="Renamed", entries=[entry(2)])

    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM feed").fetchone()[0] == 1
    assert _feed(conn, first)["title"] == "Renamed"


def test_subscribing_again_unpauses(conn):
    """Importing from a feed you had paused is a clear statement about wanting
    it followed."""
    feed_id = feeds.subscribe(conn, FEED_URL)
    feeds.set_paused(conn, feed_id, True)

    feeds.subscribe(conn, FEED_URL)

    assert _feed(conn, feed_id)["paused"] == 0


# --- when a feed is due -----------------------------------------------------------


def test_a_feed_nobody_has_checked_is_due(conn):
    """Which is what makes a fresh subscription and a restart behave the same:
    at boot, everything overdue is due, and never-checked is overdue."""
    feeds.subscribe(conn, FEED_URL)

    assert [f["url"] for f in feeds.due_feeds(conn, now=0.0)] == [FEED_URL]


def test_a_feed_checked_within_its_interval_is_not_due(conn):
    feed_id = feeds.subscribe(conn, FEED_URL)
    _poll(conn, feed_id, playlist(), now=1000.0)

    assert feeds.due_feeds(conn, now=1000.0 + feeds.DEFAULT_INTERVAL_SECONDS - 1) == []
    assert len(feeds.due_feeds(conn, now=1000.0 + feeds.DEFAULT_INTERVAL_SECONDS)) == 1


def test_a_closed_laptop_comes_back_to_an_overdue_feed(conn):
    """The reason due-ness is a date and not a countdown: nothing was awake to
    count, and a week away must not mean a week of missed episodes."""
    feed_id = feeds.subscribe(conn, FEED_URL)
    _poll(conn, feed_id, playlist(), now=1000.0)

    a_week_later = 1000.0 + 7 * 86400
    assert len(feeds.due_feeds(conn, now=a_week_later)) == 1


def test_a_paused_feed_is_never_due(conn):
    feed_id = feeds.subscribe(conn, FEED_URL)
    feeds.set_paused(conn, feed_id, True)

    assert feeds.due_feeds(conn, now=1e12) == []


# --- polling ----------------------------------------------------------------------


def test_a_poll_queues_one_job_per_new_episode(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, title="The Feed", entries=[entry(1)])

    report = _poll(conn, feed_id, playlist(entry(1), entry(2), entry(3)))

    assert report["new"] == 2
    queued = _queued(conn)
    assert [p["url"] for p in queued] == [
        "https://cdn.test/ep2.mp3",
        "https://cdn.test/ep3.mp3",
    ]
    assert queued[0][url_stage.ENTRY_KEY] == {"title": "Episode 2", "source_id": "Generic:g2"}
    assert queued[0][url_stage.SOURCE_KEY]["url"] == FEED_URL
    # What tells the register stage nobody asked for this one by hand, so a
    # download that turns out to be known content is not transcribed twice.
    assert queued[0][url_stage.FEED_KEY] == feed_id


def test_an_episode_already_in_the_library_is_not_queued_again(conn):
    """`known_sources` asks the media rows and the live jobs. An episode in the
    trash counts as known too: deleting it was a decision."""
    feed_id = feeds.subscribe(conn, FEED_URL)

    def knows_two(conn, entries):
        return [
            "library" if e.get("source_id") == "Generic:g2" else None for e in entries
        ]

    report = _poll(conn, feed_id, playlist(entry(1), entry(2)), known=knows_two)

    assert report["new"] == 1
    assert [p[url_stage.ENTRY_KEY]["source_id"] for p in _queued(conn)] == ["Generic:g1"]


def test_polling_twice_does_not_queue_the_same_episode_twice(conn):
    feed_id = feeds.subscribe(conn, FEED_URL)
    _poll(conn, feed_id, playlist(entry(1)), now=1000.0)

    report = _poll(conn, feed_id, playlist(entry(1)), now=2000.0)

    assert report["new"] == 0
    assert len(_queued(conn)) == 1


def test_a_poll_stops_at_the_cap_and_says_so(conn, monkeypatch):
    """A feed that rewrote its guids looks entirely new. Without a ceiling the
    next unattended poll starts a night of downloads and transcriptions."""
    monkeypatch.setattr(feeds, "MAX_NEW_PER_POLL", 3)
    feed_id = feeds.subscribe(conn, FEED_URL)

    report = _poll(conn, feed_id, playlist(*(entry(i) for i in range(10))))

    assert report["new"] == 10 and report["capped"] is True
    assert len(_queued(conn)) == 3
    assert "queued 3" in _feed(conn, feed_id)["last_result"]


def test_the_rest_come_on_the_next_poll_rather_than_being_forgotten(conn, monkeypatch):
    monkeypatch.setattr(feeds, "MAX_NEW_PER_POLL", 3)
    feed_id = feeds.subscribe(conn, FEED_URL)
    entries = [entry(i) for i in range(6)]

    _poll(conn, feed_id, playlist(*entries), now=1000.0)
    _poll(conn, feed_id, playlist(*entries), now=2000.0)

    assert len(_queued(conn)) == 6


def test_an_entry_with_no_source_id_is_skipped_rather_than_guessed_at(conn):
    """It would be queued again on every poll for ever."""
    feed_id = feeds.subscribe(conn, FEED_URL)
    anonymous = {"url": "https://cdn.test/x.mp3", "title": "No id", "source_id": ""}

    report = _poll(conn, feed_id, playlist(anonymous, entry(1)))

    assert report["new"] == 1
    assert [p[url_stage.ENTRY_KEY]["source_id"] for p in _queued(conn)] == ["Generic:g1"]


def test_nothing_new_is_recorded_as_a_successful_check(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, entries=[entry(1)])

    _poll(conn, feed_id, playlist(entry(1)), now=1000.0)

    row = _feed(conn, feed_id)
    assert row["last_result"] == "nothing new"
    assert (row["checked_at"], row["failures"]) == (1000.0, 0)


# --- when a feed misbehaves --------------------------------------------------------


def test_a_probe_that_raises_is_recorded_and_the_feed_is_not_retried_at_once(conn):
    """Every exit records checked_at, failures included: a host that is down
    must not be hammered, and the next attempt is one interval away like any
    other."""
    feed_id = feeds.subscribe(conn, FEED_URL)

    report = _poll(conn, feed_id, None, raises=urls.UrlError("no such host"), now=1000.0)

    row = _feed(conn, feed_id)
    assert report["queued"] == []
    assert row["failures"] == 1
    assert "no such host" in row["last_result"]
    assert row["checked_at"] == 1000.0
    assert feeds.due_feeds(conn, now=1001.0) == []


def test_consecutive_failures_add_up_and_a_good_check_clears_them(conn):
    feed_id = feeds.subscribe(conn, FEED_URL)
    _poll(conn, feed_id, None, raises=urls.UrlError("down"), now=1000.0)
    _poll(conn, feed_id, None, raises=urls.UrlError("down"), now=2000.0)
    assert _feed(conn, feed_id)["failures"] == 2

    _poll(conn, feed_id, playlist(), now=3000.0)

    assert _feed(conn, feed_id)["failures"] == 0


def test_a_url_that_answers_as_one_item_is_not_a_feed(conn):
    """Somebody pasted a single video. Saying so beats queueing it as if the
    subscription had worked."""
    feed_id = feeds.subscribe(conn, FEED_URL)
    single = urls.UrlInfo(
        kind="single", title="One video", duration=1.0, uploader="", webpage_url=FEED_URL
    )

    report = _poll(conn, feed_id, single)

    assert report["queued"] == []
    assert "not a feed" in _feed(conn, feed_id)["last_result"]


def test_unsubscribing_forgets_the_feed_and_keeps_the_jobs_it_made(conn):
    feed_id = feeds.subscribe(conn, FEED_URL)
    _poll(conn, feed_id, playlist(entry(1)))

    feeds.unsubscribe(conn, feed_id)

    assert feeds.all_feeds(conn) == []
    assert len(_queued(conn)) == 1
