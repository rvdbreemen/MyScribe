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
import inspect
import json
import pathlib
import shutil

import pytest

from scribe import db, doctor, jobs
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
    # And it records the check. A feed is due when checked_at is NULL, so a
    # refused poll that writes nothing hands the same feed back on every tick
    # for as long as the question is open, instead of going quiet for an
    # interval. The refused poll is still a poll.
    checked = conn.execute("SELECT checked_at FROM feed WHERE id=?", (feed_id,)).fetchone()
    assert checked["checked_at"] is not None
    assert [f["id"] for f in feeds.due_feeds(conn, now=checked["checked_at"] + 1)] == []


def _dir_of_length(base: pathlib.Path, length: int) -> pathlib.Path:
    """A directory under `base` whose full path is `length` characters long,
    or one level deeper than `base` when `base` is already that long."""
    pad = length - len(str(base)) - 1
    return base / ("d" * max(pad, 1))


@pytest.mark.parametrize("data_dir", ["short", "long"])
def test_a_poll_below_the_disk_floor_queues_nothing_and_says_so(  # noqa: F811
    conn, monkeypatch, tmp_path, data_dir
):
    """An episode is marked seen the moment a poll queues it, so queueing into
    a disk that will refuse every download does not delay the catalogue - it
    loses it, and the Feeds page would say "3 new" while all three died. The
    probe is not even opened, and the entries stay unseen for next time.

    And the stored refusal keeps what a person acts on, however long the data
    directory's path is (TASK-091). 'long' is 150 characters: from 142 the
    old `result[:200]` cut the amount needed and "nothing was downloaded"
    away. 'short' is the library conftest fences the test into."""
    if data_dir == "long":
        from scribe import paths

        library = _dir_of_length(tmp_path, 150)
        library.mkdir(parents=True)
        monkeypatch.setattr(paths, "DATA_DIR", library)
        assert len(str(doctor.disk_probe_path())) > 141
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])
    feeds.answer(conn, feed_id)
    feed = dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: shutil._ntuple_diskusage(total=1000 * 2**30, used=0, free=2**30),
    )

    def probe(url, **kw):
        raise AssertionError("the feed was probed on a disk that cannot hold the result")

    out = feeds.poll(conn, feed, probe=probe, known_sources=lambda c, e: [], options={})

    assert out["queued"] == [] and str(doctor.DISK_FLOOR_GB) in out["error"]
    assert len(_queued(conn)) == 1  # the first episode, and nothing new
    assert _seen(conn, feed_id) == [entry(0)["source_id"]]
    row = conn.execute(
        "SELECT last_result, failures FROM feed WHERE id=?", (feed_id,)
    ).fetchone()
    assert str(doctor.DISK_FLOOR_GB) in row["last_result"], (
        "the Feeds page must not call this a healthy check"
    )
    assert row["failures"] == 1  # counted as a failure, so the backoff applies
    # TASK-091: what a person acts on survives the store, whatever the path.
    stored = row["last_result"]
    assert len(stored) <= 200, len(stored)
    assert "only 1.0 GB free" in stored, stored
    assert f"keeps {doctor.DISK_FLOOR_GB} GB clear" in stored, stored
    assert stored.endswith("nothing was downloaded."), stored


def test_a_disk_floor_refusal_at_the_longest_windows_path_is_pinned(conn, monkeypatch):  # noqa: F811
    """TASK-091 #3. Windows allows a path of 260 characters (MAX_PATH), and the
    Feeds page stores 200. The path gives up its middle - the drive and the
    folder a person recognises stay - and every word after it survives. Not
    created on disk: `disk_probe_path` is replaced, because a directory that
    long cannot hold anything under MAX_PATH."""
    longest = pathlib.PureWindowsPath("C:\\" + "\\".join(["a" * 50] * 4) + "\\" + "b" * 53)
    assert len(str(longest)) == 260
    monkeypatch.setattr(doctor, "disk_probe_path", lambda: longest)
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: shutil._ntuple_diskusage(total=1000 * 2**30, used=0, free=2**30),
    )
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])
    feeds.answer(conn, feed_id)
    feed = dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())

    out = feeds.poll(conn, feed, probe=lambda url, **kw: None, known_sources=lambda c, e: [],
                     options={})

    stored = conn.execute("SELECT last_result FROM feed WHERE id=?", (feed_id,)).fetchone()[0]
    assert stored == (
        # 48 characters are left for the path: its first 23, an ellipsis,
        # its last 24.
        "not checked: only 1.0 GB free at C:\\" + "a" * 20 + "\u2026" + "b" * 24 + ", "
        "and this app keeps 10 GB clear on the drive your recordings land on. "
        "Free up space and retry; nothing was downloaded."
    )
    assert len(stored) == 200
    assert str(longest) in out["error"], "the answer to the button keeps the whole path"


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

    Two things the walk has to get right, and neither was free:

    The root is anchored to this file rather than to the working directory. A
    cwd-relative `Path("scribe")` yields nothing when pytest runs from
    anywhere else, and `assert offenders == []` then passes having read no
    code at all - a guard that goes quiet instead of red is worse than none,
    so `read` is asserted too.

    Only the priority is read, by keyword or by its own position. Scanning
    every positional argument for a negative number reports
    `enqueue(conn, T, -11)` - where -11 is a media id - as a violation, and a
    false alarm sends the next reader hunting something that is not there.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "scribe"
    # jobs.enqueue(conn, type, params=..., priority=...) and
    # jobs.enqueue_many(conn, type, params_list, priority) - the only two,
    # and their signatures are checked below so a reorder cannot fool this.
    slot = {"enqueue": None, "enqueue_many": 3}
    assert [p.name for p in inspect.signature(jobs.enqueue_many).parameters.values()][3] == "priority"
    assert "priority" in inspect.signature(jobs.enqueue).parameters

    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
                and isinstance(node.operand, ast.Constant)):
            return -node.operand.value
        return None

    read, calls, offenders = 0, 0, []
    for path in sorted(root.rglob("*.py")):
        read += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name not in slot:
                continue
            calls += 1
            here = [kw.value for kw in node.keywords if kw.arg == "priority"]
            index = slot[name]
            if index is not None and len(node.args) > index:
                here.append(node.args[index])
            for value in (literal(arg) for arg in here):
                if value is not None and value < url_stage.BULK_PRIORITY:
                    offenders.append(f"{path}:{node.lineno} enqueues at {value}")
    assert read > 10 and calls > 0, f"the walk read {read} files and found {calls} calls"
    assert offenders == [], offenders


def test_the_floor_is_named_once(conn):  # noqa: F811
    """Every feed path reads url_stage.BULK_PRIORITY rather than a copy of the
    number, so moving the constant moves all of them."""
    root = pathlib.Path(__file__).resolve().parents[1]
    for path in (root / "scribe/ingest/feeds.py", root / "scribe/web/ingest_ui.py"):
        source = path.read_text(encoding="utf-8")
        assert "BULK_PRIORITY" in source, path
        assert "priority=-10" not in source.replace(" ", ""), path


def test_a_refusal_that_fits_is_stored_whole(conn, monkeypatch):  # noqa: F811
    """The other side of the 200 characters: a path short enough to fit - 42
    characters, the length of a release install - is stored as it is, with
    nothing shortened. The fixed words leave 48 for the path at "1.0 GB"."""
    install = pathlib.PureWindowsPath("C:\\Users\\guest\\AppData\\Local\\MyScribe\\data")
    assert len(str(install)) == 42
    monkeypatch.setattr(doctor, "disk_probe_path", lambda: install)
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: shutil._ntuple_diskusage(total=1000 * 2**30, used=0, free=2**30),
    )
    feed_id = feeds.subscribe(conn, "https://feed.test/rss", entries=[entry(0)])
    feeds.answer(conn, feed_id)
    feed = dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())

    out = feeds.poll(conn, feed, probe=lambda url, **kw: None, known_sources=lambda c, e: [],
                     options={})

    stored = conn.execute("SELECT last_result FROM feed WHERE id=?", (feed_id,)).fetchone()[0]
    assert stored == out["error"]
    assert stored == (
        f"not checked: only 1.0 GB free at {install}, and this app keeps 10 GB clear on "
        "the drive your recordings land on. Free up space and retry; nothing was downloaded."
    )
    assert "\u2026" not in stored


def test_a_refusal_with_no_room_for_the_path_drops_the_whole_path():
    """TASK-091 #2's other way out: when not even a shortened path fits, the
    path goes and the words a person acts on stay."""
    text = doctor.disk_refusal(1.0, "C:\\" + "a" * 257, limit=140)

    assert text == (
        "only 1.0 GB free, and this app keeps 10 GB clear on the drive your "
        "recordings land on. Free up space and retry; nothing was downloaded."
    )
    assert doctor.disk_refusal(1.0, "/srv/myscribe") == (
        "only 1.0 GB free at /srv/myscribe, and this app keeps 10 GB clear on the drive "
        "your recordings land on. Free up space and retry; nothing was downloaded."
    ), "unlimited, the message is what it always was"
