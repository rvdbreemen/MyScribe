"""Answering a new feed's question (TASK-045, and TASK-044's AC3).

A feed that was just added has fetched one episode and asks how much of the
rest it should fetch. The question lives on the Feeds page - not in a flash a
closed tab loses - and each choice says what it would fetch before it is
clicked, because "everything" on a 2970-episode archive should look like what
it is.

Whatever is chosen, the question closes: somebody who said "nothing more" is
not asked again tomorrow.
"""

from __future__ import annotations

import pytest

from scribe import db, jobs
from scribe.ingest import feeds
from scribe.stages import url_stage

from tests.test_feeds import (  # noqa: F401  (fixtures and helpers)
    FEED_URL,
    conn,
    entry,
    playlist,
)


def _feed(conn, feed_id):
    return dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())


def _new_feed(conn, count=12):
    entries = [entry(n) for n in range(count)]
    feed_id = feeds.subscribe(conn, FEED_URL, title="The Feed", entries=entries)
    return feed_id, entries


def _answer(conn, feed_id, choice, entries):
    return feeds.backfill(
        conn,
        _feed(conn, feed_id),
        choice,
        probe=lambda url, **kw: playlist(*entries),
        known_sources=lambda c, e: [None] * len(e),
        options={},
    )


def _queued_urls(conn):
    import json

    return [
        json.loads(row["params_json"])["url"]
        for row in conn.execute(
            "SELECT params_json FROM job WHERE type=? ORDER BY id", (url_stage.JOB_TYPE,)
        )
    ]


# --- what the question offers --------------------------------------------------


def test_each_choice_says_what_it_would_fetch(conn):  # noqa: F811
    """AC1: the count before the click. One episode is already on its way, so
    "the last 3" is two more, and "everything" is the other eleven."""
    feed_id, _ = _new_feed(conn, count=12)

    offer = feeds.backfill_offer(_feed(conn, feed_id))

    assert [(o["choice"], o["count"]) for o in offer] == [
        ("3", 2), ("5", 4), ("10", 9), ("all", 11),
    ]


def test_a_short_feed_does_not_offer_more_than_it_has(conn):  # noqa: F811
    """AC2: "the last N" stops at what the feed holds, and a choice that would
    fetch nothing is not offered at all."""
    feed_id, _ = _new_feed(conn, count=4)

    offer = feeds.backfill_offer(_feed(conn, feed_id))

    assert [(o["choice"], o["count"]) for o in offer] == [("3", 2), ("5", 3), ("10", 3), ("all", 3)]


def test_a_feed_of_one_asks_nothing(conn):  # noqa: F811
    feed_id, _ = _new_feed(conn, count=1)

    assert feeds.backfill_offer(_feed(conn, feed_id)) == []


# --- answering -----------------------------------------------------------------


def test_the_last_three_fetches_the_two_that_are_left(conn):  # noqa: F811
    """AC2: counted from the newest the feed lists, and the one already
    fetched is not fetched twice."""
    feed_id, entries = _new_feed(conn, count=12)

    out = _answer(conn, feed_id, "3", entries)

    assert len(out["queued"]) == 2
    assert _queued_urls(conn) == [e["url"] for e in entries[:3]]


def test_everything_fetches_the_whole_listing(conn):  # noqa: F811
    feed_id, entries = _new_feed(conn, count=12)

    out = _answer(conn, feed_id, "all", entries)

    assert len(out["queued"]) == 11
    assert _queued_urls(conn) == [e["url"] for e in entries]


def test_nothing_more_queues_nothing_and_still_closes_the_question(conn):  # noqa: F811
    feed_id, entries = _new_feed(conn, count=12)

    out = _answer(conn, feed_id, "nothing", entries)

    assert out["queued"] == []
    assert len(_queued_urls(conn)) == 1  # the first episode, from subscribing
    assert feeds.is_asking(conn, feed_id) is False


def test_an_answer_closes_the_question_whatever_it_was(conn):  # noqa: F811
    feed_id, entries = _new_feed(conn, count=12)

    _answer(conn, feed_id, "5", entries)

    assert feeds.is_asking(conn, feed_id) is False


def test_what_it_fetches_runs_at_the_bulk_priority(conn):  # noqa: F811
    """AC3, and TASK-046: only the first episode was worth the normal
    priority; a back catalogue waits behind hand-started work."""
    feed_id, entries = _new_feed(conn, count=12)

    _answer(conn, feed_id, "all", entries)

    rows = list(conn.execute("SELECT priority FROM job ORDER BY id"))
    assert rows[0]["priority"] == 0
    assert {row["priority"] for row in rows[1:]} == {url_stage.BULK_PRIORITY}


def test_a_choice_that_is_not_on_offer_is_refused(conn):  # noqa: F811
    feed_id, entries = _new_feed(conn, count=12)

    with pytest.raises(ValueError):
        _answer(conn, feed_id, "7", entries)

    assert feeds.is_asking(conn, feed_id) is True  # and the question stays open


def test_a_choice_above_the_fan_out_ceiling_is_refused_with_both_numbers(conn, monkeypatch):  # noqa: F811
    """AC4: the same refusal a pasted playlist already gets."""
    feed_id, entries = _new_feed(conn, count=12)
    monkeypatch.setattr(url_stage, "MAX_FAN_OUT", 3)

    with pytest.raises(ValueError) as caught:
        _answer(conn, feed_id, "all", entries)

    assert "11" in str(caught.value) and "3" in str(caught.value)
    assert len(_queued_urls(conn)) == 1  # nothing was queued
