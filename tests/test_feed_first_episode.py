"""A new feed fetches its newest episode and then asks (TASK-044, TASK-046).

Adding a feed used to record the whole listing as seen and queue nothing; the
dialog's ticked episodes were the only way to get anything, and ticking "all"
on a podcast nobody had heard was five hundred downloads and a night of GPU.

The rule now: a feed that is new queues exactly one episode - the newest, in
the order the probe returned - marks only that one seen, and leaves a question
on the Feeds page until a person answers it. Nothing else is marked, so
answering "everything" later still fetches the rest.

That one episode runs at the normal priority (a person is standing there
waiting to hear whether the feed is any good). Everything else a feed queues
runs at `url_stage.BULK_PRIORITY`, which is the floor the app itself uses.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from scribe import db, jobs
from scribe.ingest import feeds
from scribe.stages import url_stage

from tests.test_feeds import (  # noqa: F401  (fixtures and helpers)
    conn,
    entry,
    playlist,
)


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


# --- a new feed ---------------------------------------------------------------


def test_a_new_feed_queues_its_newest_episode_and_nothing_else(conn):  # noqa: F811
    entries = [entry(n) for n in range(4)]

    feed_id = feeds.subscribe(conn, "https://feed.test/rss", title="A feed", entries=entries)

    queued = _queued(conn)
    assert len(queued) == 1
    params = json.loads(queued[0]["params_json"])
    assert params["url"] == entries[0]["url"]  # the first the probe returned
    assert params[url_stage.FEED_KEY] == feed_id


def test_the_rest_of_the_feed_stays_unseen_so_a_later_answer_still_fetches_it(conn):  # noqa: F811
    entries = [entry(n) for n in range(4)]

    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=entries)

    assert _seen(conn, feed_id) == [entries[0]["source_id"]]


def test_the_first_episode_runs_at_the_normal_priority(conn):  # noqa: F811
    """TASK-046: the one exception to "feed work runs last". Somebody is
    waiting to hear whether this feed is any good."""
    feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0), entry(1)])

    assert _queued(conn)[0]["priority"] == 0


def test_a_new_feed_is_asking_until_it_is_answered(conn):  # noqa: F811
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])

    assert feeds.is_asking(conn, feed_id) is True
    assert [f["id"] for f in feeds.asking(conn)] == [feed_id]


def test_answering_closes_the_question_for_good(conn):  # noqa: F811
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])

    feeds.answer(conn, feed_id)

    assert feeds.is_asking(conn, feed_id) is False
    assert feeds.asking(conn) == []


def test_importing_the_same_feed_again_does_not_reopen_the_question(conn):  # noqa: F811
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])
    feeds.answer(conn, feed_id)

    feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0), entry(1)])

    assert feeds.is_asking(conn, feed_id) is False
    assert len(_queued(conn)) == 1  # and it queued nothing a second time


def test_a_feed_imported_with_its_episodes_ticked_is_already_answered(conn):  # noqa: F811
    """The dialog's own path: a person who ticked episodes has answered the
    question by answering it. Everything listed is seen, nothing is queued
    here - the ticked jobs are queued by the route."""
    entries = [entry(n) for n in range(3)]

    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=entries, answered=True)

    assert feeds.is_asking(conn, feed_id) is False
    assert _seen(conn, feed_id) == sorted(e["source_id"] for e in entries)
    assert _queued(conn) == []


def test_a_feed_that_is_still_asking_is_not_polled(conn, monkeypatch):  # noqa: F811
    """The watcher reaches a new feed within five minutes. Until the question
    is answered, a poll would queue the back catalogue the question is about."""
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0), entry(1)])
    feed = dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())
    probes = []

    def probe(url, **kw):
        probes.append(url)
        return playlist(*[entry(n) for n in range(3)])

    out = feeds.poll(conn, feed, probe=probe, known_sources=lambda c, e: [None] * len(e), options={})

    assert out["queued"] == []
    assert "asking" in (out.get("result") or out.get("error") or "")
    assert len(_queued(conn)) == 1  # still only the first episode


def test_an_answered_feed_polls_as_before(conn):  # noqa: F811
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])
    feeds.answer(conn, feed_id)
    feed = dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())

    out = feeds.poll(
        conn,
        feed,
        probe=lambda url, **kw: playlist(*[entry(n) for n in range(3)]),
        known_sources=lambda c, e: [None] * len(e),
        options={},
    )

    assert len(out["queued"]) == 2  # entry 1 and 2; entry 0 was already seen


# --- the floor (TASK-046) ------------------------------------------------------


def test_everything_a_feed_queues_after_the_first_episode_runs_last(conn):  # noqa: F811
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])
    feeds.answer(conn, feed_id)
    feed = dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())

    feeds.poll(
        conn,
        feed,
        probe=lambda url, **kw: playlist(*[entry(n) for n in range(3)]),
        known_sources=lambda c, e: [None] * len(e),
        options={},
    )

    later = [job for job in _queued(conn)[1:]]
    assert later and all(job["priority"] == url_stage.BULK_PRIORITY for job in later)


def test_no_code_path_enqueues_below_the_floor(conn):  # noqa: F811
    """TASK-046 AC6: the rule survives a fourth caller.

    An AST walk rather than a runtime guard, because the thing to prevent is
    someone *writing* a lower number, and a runtime guard would only catch the
    paths a test happens to exercise.
    """
    root = pathlib.Path("scribe")
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in {"enqueue", "enqueue_many"}:
                continue
            for arg in [*node.args, *(kw.value for kw in node.keywords if kw.arg == "priority")]:
                value = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                    value = arg.value
                elif isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub) and isinstance(arg.operand, ast.Constant):
                    value = -arg.operand.value
                if value is not None and value < url_stage.BULK_PRIORITY:
                    offenders.append(f"{path}:{node.lineno} enqueues at {value}")
    assert offenders == [], offenders


def test_the_floor_is_named_once(conn):  # noqa: F811
    """Every feed path reads url_stage.BULK_PRIORITY rather than a copy of the
    number, so moving the constant moves all of them."""
    for path in (pathlib.Path("scribe/ingest/feeds.py"), pathlib.Path("scribe/web/ingest_ui.py")):
        source = path.read_text(encoding="utf-8")
        assert "BULK_PRIORITY" in source, path
        assert "priority=-10" not in source.replace(" ", ""), path
