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


def test_subscribing_fetches_the_newest_episode_and_remembers_only_that(conn):
    """Subscribing is still not a request for the archive - following The Daily
    must not queue its 2970 back episodes - but it is no longer silence either:
    the newest episode arrives so a person can hear whether the feed is any
    good, and the rest waits for the answer to the question on the Feeds page.

    Before TASK-044 this recorded both entries as seen and queued nothing; the
    old behaviour is what `answered=True` now means, and the tests below that
    poll an established feed use it."""
    feed_id = feeds.subscribe(conn, FEED_URL, title="The Feed", entries=[entry(1), entry(2)])

    assert len(_queued(conn)) == 1
    seen = {
        row["source_id"]
        for row in conn.execute("SELECT source_id FROM feed_seen WHERE feed_id=?", (feed_id,))
    }
    assert seen == {"Generic:g1"}  # entry(2) stays unseen, so an answer can still fetch it
    assert feeds.is_asking(conn, feed_id) is True


def test_subscribing_twice_follows_one_feed_not_two(conn):
    """A person who imports from the same feed again meant to follow it, not
    to follow it twice - and two rows would race each other into queueing every
    new episode twice."""
    first = feeds.subscribe(conn, FEED_URL, answered=True, title="The Feed", entries=[entry(1)])
    second = feeds.subscribe(conn, FEED_URL, answered=True, title="Renamed", entries=[entry(2)])

    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM feed").fetchone()[0] == 1
    assert _feed(conn, first)["title"] == "Renamed"


def test_subscribing_again_unpauses(conn):
    """Importing from a feed you had paused is a clear statement about wanting
    it followed."""
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    feeds.set_paused(conn, feed_id, True)

    feeds.subscribe(conn, FEED_URL, answered=True)

    assert _feed(conn, feed_id)["paused"] == 0


# --- when a feed is due -----------------------------------------------------------


def test_a_feed_nobody_has_checked_is_due(conn):
    """Which is what makes a fresh subscription and a restart behave the same:
    at boot, everything overdue is due, and never-checked is overdue."""
    feeds.subscribe(conn, FEED_URL, answered=True)

    assert [f["url"] for f in feeds.due_feeds(conn, now=0.0)] == [FEED_URL]


def test_a_feed_checked_within_its_interval_is_not_due(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    _poll(conn, feed_id, playlist(), now=1000.0)

    assert feeds.due_feeds(conn, now=1000.0 + feeds.DEFAULT_INTERVAL_SECONDS - 1) == []
    assert len(feeds.due_feeds(conn, now=1000.0 + feeds.DEFAULT_INTERVAL_SECONDS)) == 1


def test_a_closed_laptop_comes_back_to_an_overdue_feed(conn):
    """The reason due-ness is a date and not a countdown: nothing was awake to
    count, and a week away must not mean a week of missed episodes."""
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    _poll(conn, feed_id, playlist(), now=1000.0)

    a_week_later = 1000.0 + 7 * 86400
    assert len(feeds.due_feeds(conn, now=a_week_later)) == 1


def test_a_paused_feed_is_never_due(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    feeds.set_paused(conn, feed_id, True)

    assert feeds.due_feeds(conn, now=1e12) == []


# --- polling ----------------------------------------------------------------------


def test_a_poll_queues_one_job_per_new_episode(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True, title="The Feed", entries=[entry(1)])

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
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)

    def knows_two(conn, entries):
        return [
            "library" if e.get("source_id") == "Generic:g2" else None for e in entries
        ]

    report = _poll(conn, feed_id, playlist(entry(1), entry(2)), known=knows_two)

    assert report["new"] == 1
    assert [p[url_stage.ENTRY_KEY]["source_id"] for p in _queued(conn)] == ["Generic:g1"]


def test_polling_twice_does_not_queue_the_same_episode_twice(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    _poll(conn, feed_id, playlist(entry(1)), now=1000.0)

    report = _poll(conn, feed_id, playlist(entry(1)), now=2000.0)

    assert report["new"] == 0
    assert len(_queued(conn)) == 1


def test_a_poll_stops_at_the_cap_and_says_so(conn, monkeypatch):
    """A feed that rewrote its guids looks entirely new. Without a ceiling the
    next unattended poll starts a night of downloads and transcriptions."""
    monkeypatch.setattr(feeds, "MAX_NEW_PER_POLL", 3)
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)

    report = _poll(conn, feed_id, playlist(*(entry(i) for i in range(10))))

    assert report["new"] == 10 and report["capped"] is True
    assert len(_queued(conn)) == 3
    assert "queued 3" in _feed(conn, feed_id)["last_result"]


def test_the_rest_come_on_the_next_poll_rather_than_being_forgotten(conn, monkeypatch):
    monkeypatch.setattr(feeds, "MAX_NEW_PER_POLL", 3)
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    entries = [entry(i) for i in range(6)]

    _poll(conn, feed_id, playlist(*entries), now=1000.0)
    _poll(conn, feed_id, playlist(*entries), now=2000.0)

    assert len(_queued(conn)) == 6


def test_an_entry_with_no_source_id_is_skipped_rather_than_guessed_at(conn):
    """It would be queued again on every poll for ever."""
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    anonymous = {"url": "https://cdn.test/x.mp3", "title": "No id", "source_id": ""}

    report = _poll(conn, feed_id, playlist(anonymous, entry(1)))

    assert report["new"] == 1
    assert [p[url_stage.ENTRY_KEY]["source_id"] for p in _queued(conn)] == ["Generic:g1"]


def test_nothing_new_is_recorded_as_a_successful_check(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True, entries=[entry(1)])

    _poll(conn, feed_id, playlist(entry(1)), now=1000.0)

    row = _feed(conn, feed_id)
    assert row["last_result"] == "nothing new"
    assert (row["checked_at"], row["failures"]) == (1000.0, 0)


# --- when a feed misbehaves --------------------------------------------------------


def test_a_probe_that_raises_is_recorded_and_the_feed_is_not_retried_at_once(conn):
    """Every exit records checked_at, failures included: a host that is down
    must not be hammered, and the next attempt is one interval away like any
    other."""
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)

    report = _poll(conn, feed_id, None, raises=urls.UrlError("no such host"), now=1000.0)

    row = _feed(conn, feed_id)
    assert report["queued"] == []
    assert row["failures"] == 1
    assert "no such host" in row["last_result"]
    assert row["checked_at"] == 1000.0
    assert feeds.due_feeds(conn, now=1001.0) == []


def test_consecutive_failures_add_up_and_a_good_check_clears_them(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    _poll(conn, feed_id, None, raises=urls.UrlError("down"), now=1000.0)
    _poll(conn, feed_id, None, raises=urls.UrlError("down"), now=2000.0)
    assert _feed(conn, feed_id)["failures"] == 2

    _poll(conn, feed_id, playlist(), now=3000.0)

    assert _feed(conn, feed_id)["failures"] == 0


def test_a_url_that_answers_as_one_item_is_not_a_feed(conn):
    """Somebody pasted a single video. Saying so beats queueing it as if the
    subscription had worked."""
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    single = urls.UrlInfo(
        kind="single", title="One video", duration=1.0, uploader="", webpage_url=FEED_URL
    )

    report = _poll(conn, feed_id, single)

    assert report["queued"] == []
    assert "not a feed" in _feed(conn, feed_id)["last_result"]


def test_unsubscribing_forgets_the_feed_and_keeps_the_jobs_it_made(conn):
    feed_id = feeds.subscribe(conn, FEED_URL, answered=True)
    _poll(conn, feed_id, playlist(entry(1)))

    feeds.unsubscribe(conn, feed_id)

    assert feeds.all_feeds(conn) == []
    assert len(_queued(conn)) == 1


# --- the watcher's own manners ---------------------------------------------------


def test_a_tick_spends_its_budget_and_leaves_the_rest_due(conn, monkeypatch):
    """TICK_BUDGET_SECONDS is this wake's share: a feed the budget does not
    reach stays due rather than waiting behind an afternoon of slow ones."""
    import time

    for i in range(3):
        feeds.subscribe(conn, f"https://example.test/f{i}.xml", answered=True)
    monkeypatch.setattr(feeds, "TICK_BUDGET_SECONDS", 0.0)
    watcher = feeds.FeedWatcher("never-opened.db", probe=probing(playlist(entry(1))))

    assert watcher.tick(conn) == 1
    assert len(feeds.due_feeds(conn, now=time.time() + 60.0)) == 2


def test_stop_keeps_a_thread_that_will_not_join(capsys):
    """A failed join is not a dead thread: the reference stays, like
    watching.Watcher.stop, because the two outcomes are not the same."""
    import threading

    watcher = feeds.FeedWatcher("never-opened.db")
    hold = threading.Event()
    thread = threading.Thread(target=hold.wait, daemon=True)
    thread.start()
    watcher._thread = thread

    watcher.stop(timeout=0.01)

    assert watcher._thread is thread
    assert "still running" in capsys.readouterr().err
    hold.set()
    thread.join()


# --- a feed that never answers (GitHub #36) ---------------------------------------

HUNG_URL = "https://example.test/hung.xml"
FINE_URL = "https://example.test/fine.xml"


def _hanging_on(hung_url, release, calls):
    """A probe that does not come back for one feed - a host dripping bytes,
    which no socket timeout ends - and answers at once for every other."""

    def probe(url, **kwargs):
        calls.append(url)
        if url == hung_url:
            release.wait()
        return playlist(entry(1))

    return probe


def _tick_in_background(watcher, conn):
    """`watcher.tick` on a helper thread, with an Event that says it came back.

    The test waits on the Event with a timeout instead of calling tick
    directly: on code without a per-poll bound the tick never returns, and the
    test has to fail on that rather than hang the suite.
    """
    import threading

    done = threading.Event()

    def run():
        try:
            watcher.tick(conn)
        finally:
            done.set()

    helper = threading.Thread(target=run, daemon=True)
    helper.start()
    return helper, done


def test_a_feed_that_never_answers_does_not_starve_the_others(conn, monkeypatch):
    """TICK_BUDGET_SECONDS is looked at between feeds, so a probe that is
    already hung held the one feed thread and every feed behind it stayed due.
    The bound is per poll: the hung feed is recorded as failed, like a probe
    that raised, and the tick goes on to the next."""
    import threading

    monkeypatch.setattr(feeds, "PROBE_TIMEOUT_SECONDS", 0.3, raising=False)
    hung_id = feeds.subscribe(conn, HUNG_URL, answered=True)  # first, so polled first
    fine_id = feeds.subscribe(conn, FINE_URL, answered=True)
    release, calls = threading.Event(), []
    watcher = feeds.FeedWatcher("never-opened.db", probe=_hanging_on(HUNG_URL, release, calls))

    helper, done = _tick_in_background(watcher, conn)
    try:
        assert done.wait(10.0), f"the tick never came back; probed so far: {calls}"
        assert calls == [HUNG_URL, FINE_URL]
        fine = _feed(conn, fine_id)
        assert fine["failures"] == 0 and fine["last_result"] == "1 new"
        hung = _feed(conn, hung_id)
        assert hung["failures"] == 1
        assert "did not answer" in hung["last_result"]
        assert hung_id not in {f["id"] for f in feeds.due_feeds(conn)}
    finally:
        release.set()
        helper.join(10.0)


def test_a_feed_still_hung_from_last_time_is_skipped_not_probed_again(conn, monkeypatch):
    """The abandoned probe thread cannot be stopped from outside, so a feed
    whose last probe is still running is left out of the next tick: one stuck
    thread per feed at most, not one more every wake."""
    import threading

    monkeypatch.setattr(feeds, "PROBE_TIMEOUT_SECONDS", 0.3, raising=False)
    hung_id = feeds.subscribe(conn, HUNG_URL, answered=True)
    release, calls = threading.Event(), []
    watcher = feeds.FeedWatcher("never-opened.db", probe=_hanging_on(HUNG_URL, release, calls))
    helpers = []
    try:
        helper, done = _tick_in_background(watcher, conn)
        helpers.append(helper)
        assert done.wait(10.0), "the tick never came back"
        # Due again while the first probe still hangs - a short interval, or
        # a laptop that slept through the day.
        conn.execute("UPDATE feed SET checked_at=NULL WHERE id=?", (hung_id,))
        conn.commit()

        helper, done = _tick_in_background(watcher, conn)
        helpers.append(helper)
        assert done.wait(10.0), "the second tick never came back"

        assert calls == [HUNG_URL]
        # Recorded as a failure for this due time, so not due again at once:
        # left due and unrecorded, it never reached MAX_FAILURES_REPORTED.
        assert hung_id not in {f["id"] for f in feeds.due_feeds(conn)}
    finally:
        release.set()
        for helper in helpers:
            helper.join(10.0)


def test_a_feed_that_stays_hung_counts_once_per_due_time_until_it_shows_as_failing(
    conn, monkeypatch
):
    """GitHub #36: a skip because the last probe still runs is that due time's
    failure. Left due and unrecorded, the counter stopped at the first timeout
    and the Feeds page said "trying again" for ever about a feed that never
    came back. Counted once per due time, not once per five-minute wake."""
    import threading

    monkeypatch.setattr(feeds, "PROBE_TIMEOUT_SECONDS", 0.3, raising=False)
    hung_id = feeds.subscribe(conn, HUNG_URL, answered=True)
    release, calls = threading.Event(), []
    watcher = feeds.FeedWatcher("never-opened.db", probe=_hanging_on(HUNG_URL, release, calls))
    helpers = []

    def tick():
        helper, done = _tick_in_background(watcher, conn)
        helpers.append(helper)
        assert done.wait(10.0), "the tick never came back"

    try:
        tick()
        assert _feed(conn, hung_id)["failures"] == 1
        for expected in range(2, feeds.MAX_FAILURES_REPORTED + 1):
            conn.execute("UPDATE feed SET checked_at=NULL WHERE id=?", (hung_id,))
            conn.commit()
            tick()
            assert _feed(conn, hung_id)["failures"] == expected

        tick()  # another wake inside the same due time

        hung = _feed(conn, hung_id)
        assert hung["failures"] == feeds.MAX_FAILURES_REPORTED
        assert "did not answer" in hung["last_result"]
        assert calls == [HUNG_URL]
    finally:
        release.set()
        for helper in helpers:
            helper.join(10.0)


def test_an_oldest_first_source_still_has_its_newest_entry_found(conn):
    """GitHub #36: a poll asks for the whole listing. A cap (`playlistend`)
    assumed every source lists newest first; for one that lists oldest first
    it kept the oldest entries and the new one at the bottom was never seen
    again. `new_entries` is a set difference and does not care about order,
    so the listing it is handed must not either."""
    old = [entry(n) for n in range(1, 602)]  # more than the 500 the cap was
    feed_id = feeds.subscribe(conn, FEED_URL, entries=old, answered=True)
    listing = old + [entry(602)]  # oldest first: the new one comes last

    def probe(url, *, limit=None, **kwargs):
        # yt-dlp's `playlistend`: the first `limit` entries in document order.
        return playlist(*(listing if limit is None else listing[:limit]))

    out = feeds.poll(
        conn, _feed(conn, feed_id), probe=probe, known_sources=nothing_known, options=OPTIONS
    )

    assert out["new"] == 1
    assert [p[url_stage.ENTRY_KEY]["source_id"] for p in _queued(conn)] == ["Generic:g602"]
