"""Following a feed from the dialog, without ticking anything (TASK-044).

The question TASK-044 describes - "the newest episode is on its way, how much
of the rest do you want?" - was reachable from `feeds.subscribe` and from
nowhere else. The only route that subscribed was the episode dialog, and it
always passed `answered=True`, because a person who ticks episodes has said
what they want. So the state existed, the tests exercised it, and the app
could not produce it.

"Follow, decide later" is the missing half: it posts the feed, the newest
entry and how long the listing is, queues that one episode at the normal
priority, and leaves the question open on the Feeds page.

The other half of this file is the rule the adversarial review found missing:
every path that queues feed work marks what it queued as seen, so queueing
into a disk below TASK-043's floor does not delay those episodes - it loses
them. Each such path refuses first.
"""

from __future__ import annotations

import json
import shutil

import pytest

from scribe import doctor, jobs
from scribe.ingest import feeds
from scribe.stages import url_stage

from tests.test_web_url_dialog import (  # noqa: F401  (fixtures and helpers)
    FEED_URL,
    HX,
    _entries,
    _entry,
    _post_episodes,
    client,
    conn,
    data_dir,
    db_path,
    roots,
)


def _follow(client, *, first=None, total=4, feed_url=FEED_URL, **extra):
    """What the "Follow, decide later" button posts: no `entry` values."""
    data = {
        "url": feed_url,
        "feed_url": feed_url,
        "feed_title": "The Hitchhiker Lectures",
        "feed_first": first if first is not None else _entry(
            "https://example.test/ep0.mp3", "Episode 0", "Generic:g0"
        ),
        "feed_total": str(total),
        "follow_only": "1",
        **extra,
    }
    return client.post("/transcribe/url", data=data, headers=HX)


def _feed(conn):
    return dict(conn.execute("SELECT * FROM feed ORDER BY id").fetchone())


def _queued(conn):
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, priority, params_json FROM job WHERE type=? ORDER BY id",
            (url_stage.JOB_TYPE,),
        )
    ]


def _seen(conn, feed_id):
    return sorted(
        row["source_id"]
        for row in conn.execute("SELECT source_id FROM feed_seen WHERE feed_id=?", (feed_id,))
    )


def _short_disk(monkeypatch, free_gb):
    """A volume below the floor, recording which path was asked about."""
    asked: list = []

    def usage(path):
        asked.append(path)
        return shutil._ntuple_diskusage(
            total=1000 * 2**30, used=0, free=int(free_gb * 2**30)
        )

    monkeypatch.setattr(shutil, "disk_usage", usage)
    return asked


# --- the button ----------------------------------------------------------------------


def test_following_queues_the_newest_episode_and_nothing_else(client, conn):  # noqa: F811
    """TASK-044 AC1, through the app rather than through the library."""
    resp = _follow(client, total=40)

    assert resp.status_code == 200
    queued = _queued(conn)
    assert len(queued) == 1
    assert json.loads(queued[0]["params_json"])["url"] == "https://example.test/ep0.mp3"


def test_that_one_episode_runs_at_the_normal_priority(client, conn):  # noqa: F811
    """TASK-046 AC2: the one exception to "feed work runs last"."""
    _follow(client, total=40)

    assert _queued(conn)[0]["priority"] == 0


def test_the_rest_of_the_feed_stays_unseen(client, conn):  # noqa: F811
    """TASK-044 AC2: answering "everything" later still fetches them."""
    _follow(client, total=40)

    assert _seen(conn, _feed(conn)["id"]) == ["Generic:g0"]


def test_the_question_is_open_and_says_how_big_the_feed_is(client, conn):  # noqa: F811
    """TASK-044 AC3 and TASK-045 AC1: the count comes from the form, because
    the listing itself does not travel back - 2970 episodes in a POST to say
    "there are 2970" is the thing this avoids."""
    _follow(client, total=40)

    feed = _feed(conn)
    assert feeds.is_asking(conn, feed["id"]) is True
    assert feed["backfill_total"] == 40
    assert [(o["choice"], o["count"]) for o in feeds.backfill_offer(feed)] == [
        ("3", 2), ("5", 4), ("10", 9), ("all", 39),
    ]


def test_the_feeds_page_shows_the_question_after_following(client, conn):  # noqa: F811
    """AC3's "visible on the feed screen, not a toast a closed tab loses"."""
    _follow(client, total=40)

    body = client.get("/feeds").text
    assert "how much" in body.lower()
    assert "everything" in body.lower()


def test_ticking_episodes_still_answers_the_question_by_answering_it(client, conn):  # noqa: F811
    """The other button, unchanged - and the assertion the mutation review
    found missing: nothing pinned `answered=True` in the route, so deleting it
    left 170 tests green while the app queued a duplicate download and then
    never polled the feed again."""
    _post_episodes(client, _entries(2), follow_feed="1")

    feed = _feed(conn)
    assert feeds.is_asking(conn, feed["id"]) is False
    assert _seen(conn, feed["id"]) == ["Generic:g0", "Generic:g1"]
    assert len(_queued(conn)) == 2  # the two ticked, and no third at priority 0


def test_following_twice_does_not_queue_the_episode_twice(client, conn):  # noqa: F811
    _follow(client, total=40)
    _follow(client, total=40)

    assert len(_queued(conn)) == 1


def test_a_feed_length_that_is_not_a_number_is_refused(client, conn):  # noqa: F811
    resp = _follow(client, total="lots")

    assert resp.status_code == 400
    assert _queued(conn) == []


def test_a_first_entry_that_is_not_a_readable_episode_is_refused(client, conn):  # noqa: F811
    """Same door as a ticked entry: it came back from a browser, not from us."""
    resp = _follow(client, first=json.dumps({"url": "file:///etc/passwd"}))

    assert resp.status_code == 400
    assert _queued(conn) == [] and conn.execute("SELECT COUNT(*) c FROM feed").fetchone()["c"] == 0


# --- the floor, where marking-as-seen makes a refusal permanent ------------------------


def test_following_is_refused_below_the_floor_and_records_nothing(
    client, conn, monkeypatch  # noqa: F811
):
    _short_disk(monkeypatch, doctor.DISK_FLOOR_GB - 1)

    resp = _follow(client, total=40)

    assert resp.status_code == 507
    assert str(doctor.DISK_FLOOR_GB) in resp.json()["detail"]
    assert _queued(conn) == []
    assert conn.execute("SELECT COUNT(*) c FROM feed").fetchone()["c"] == 0


def test_a_ticked_import_is_refused_below_the_floor_before_a_job_is_queued(
    client, conn, monkeypatch  # noqa: F811
):
    """The sharp one: `subscribe(answered=True)` marks the whole listing seen,
    so queueing 40 downloads that all fail DISK_LOW would lose the feed's back
    catalogue rather than delay it."""
    _short_disk(monkeypatch, doctor.DISK_FLOOR_GB - 1)

    resp = _post_episodes(client, _entries(3), follow_feed="1")

    assert resp.status_code == 507
    assert _queued(conn) == []
    assert conn.execute("SELECT COUNT(*) c FROM feed_seen").fetchone()["c"] == 0


def test_the_floor_is_measured_where_the_data_lands(client, monkeypatch):  # noqa: F811
    """The one thing the guard exists to get right, and the one thing no test
    looked at: every fake took `_path` and threw it away, so a regression that
    probed the system temp volume would have shipped green."""
    from scribe import paths

    asked = _short_disk(monkeypatch, doctor.DISK_FLOOR_GB - 1)

    _follow(client, total=40)

    assert asked, "nothing measured the disk at all"
    assert all(str(path).startswith(str(paths.DATA_DIR.parent)) for path in asked), asked
