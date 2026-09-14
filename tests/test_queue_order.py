"""Changing a job's priority and its place, with FIFO intact (TASK-047).

Priority was decided at enqueue and never again, so a three-hour recording
queued behind a feed import waited, and the only lever was to let it wait.

Two things move a job now: its priority (what class of work this is) and its
place within that class. They are separate on purpose, and the rule that makes
them agree is that a priority change always lands a job at the *back* of its
new level. Without that, raising the oldest job in the table would put it in
front of everything at that level - which is exactly the "does not jump ahead
of equals queued before it" that Robert asked for.

FIFO at equal priority is what `claim_next` already did. It is pinned here
because a control that can reorder the queue is the thing most likely to break
it by accident.
"""

from __future__ import annotations

import pytest

from scribe import db, jobs


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "queue.db")
    db.migrate(c)
    yield c
    c.close()


def _claim_all(conn) -> list[int]:
    order = []
    while (job := jobs.claim_next(conn)) is not None:
        order.append(job["id"])
        jobs.finish(conn, job["id"], "done")
    return order


def _row(conn, job_id):
    return dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())


# --- what the queue promises ---------------------------------------------------


def test_a_new_job_queues_behind_everything_already_queued(conn):
    """The allocator's invariant, and the shape matters: `> every queued row`,
    not `> 0`. A column with DEFAULT 0 means an INSERT that forgets it lands at
    the *front*, and only this assertion catches that."""
    first = jobs.enqueue(conn, "transcribe", params={})
    second = jobs.enqueue(conn, "transcribe", params={})

    assert _row(conn, second)["queue_seq"] > _row(conn, first)["queue_seq"]
    assert _claim_all(conn) == [first, second]


def test_a_batch_keeps_the_order_it_was_given(conn):
    """A feed import of 47 episodes stays in feed order among itself."""
    ids = jobs.enqueue_many(conn, "ingest_url", [{"n": n} for n in range(5)])

    seqs = [_row(conn, job_id)["queue_seq"] for job_id in ids]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert _claim_all(conn) == ids


def test_priority_comes_first_then_the_oldest(conn):
    low = jobs.enqueue(conn, "ingest_url", params={}, priority=-10)
    normal_first = jobs.enqueue(conn, "transcribe", params={})
    normal_second = jobs.enqueue(conn, "transcribe", params={})

    assert _claim_all(conn) == [normal_first, normal_second, low]


# --- moving a job --------------------------------------------------------------


def test_raising_a_job_puts_it_behind_the_equals_already_at_that_level(conn):
    """The case a plain id ordering gets wrong: the raised job is the *oldest*
    row in the table, so ordering by id would run it first."""
    old = jobs.enqueue(conn, "transcribe", params={})           # id 1, priority 0
    high = jobs.enqueue(conn, "transcribe", params={}, priority=5)

    assert jobs.set_priority(conn, old, 5) is True

    assert _claim_all(conn) == [high, old]


def test_lowering_a_job_puts_it_behind_the_work_already_queued_there(conn):
    bulk = jobs.enqueue(conn, "ingest_url", params={}, priority=-10)
    normal = jobs.enqueue(conn, "transcribe", params={})

    jobs.set_priority(conn, normal, -10)

    assert _claim_all(conn) == [bulk, normal]


def test_a_job_moves_to_the_front_and_to_the_back_of_its_own_level(conn):
    a = jobs.enqueue(conn, "transcribe", params={})
    b = jobs.enqueue(conn, "transcribe", params={})
    c = jobs.enqueue(conn, "transcribe", params={})
    other = jobs.enqueue(conn, "ingest_url", params={}, priority=-10)

    assert jobs.move_in_queue(conn, c, "front") is True
    assert jobs.move_in_queue(conn, a, "back") is True

    # c, b, a within their level; the other level is untouched and still last.
    assert _claim_all(conn) == [c, b, a, other]


def test_moving_to_the_front_does_not_cross_a_priority_level(conn):
    bulk = jobs.enqueue(conn, "ingest_url", params={}, priority=-10)
    normal = jobs.enqueue(conn, "transcribe", params={})

    jobs.move_in_queue(conn, bulk, "front")

    assert _claim_all(conn) == [normal, bulk]  # front of its own level, not of the queue


def test_only_a_queued_job_can_be_moved(conn):
    """A refusal rather than a silent no-op, and the guard is in the UPDATE:
    the supervisor can claim a job between the board reading it and the write
    landing, and SQLite is the only coordination there is (ADR-009)."""
    job_id = jobs.enqueue(conn, "transcribe", params={})
    claimed = jobs.claim_next(conn)
    assert claimed["id"] == job_id
    before = _row(conn, job_id)

    assert jobs.set_priority(conn, job_id, 5) is False
    assert jobs.move_in_queue(conn, job_id, "front") is False

    after = _row(conn, job_id)
    assert (after["priority"], after["queue_seq"]) == (before["priority"], before["queue_seq"])


def test_an_unknown_move_and_an_absurd_priority_are_refused_before_the_write(conn):
    job_id = jobs.enqueue(conn, "transcribe", params={})
    before = _row(conn, job_id)

    with pytest.raises(ValueError):
        jobs.move_in_queue(conn, job_id, "sideways")
    with pytest.raises(ValueError):
        jobs.set_priority(conn, job_id, 10_000)

    after = _row(conn, job_id)
    assert (after["priority"], after["queue_seq"]) == (before["priority"], before["queue_seq"])


def test_the_change_is_recorded_where_a_person_can_see_it(conn):
    """ADR-007: the event is observation. Nothing reads it to decide a claim -
    the order lives in the column."""
    job_id = jobs.enqueue(conn, "transcribe", params={})

    jobs.set_priority(conn, job_id, 5)

    kinds = [e["kind"] for e in jobs.events_after(conn, job_id, 0)]
    assert "priority" in kinds
    payload = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "priority"][0]["payload"]
    assert payload["was"] == 0 and payload["now"] == 5


def test_a_recording_can_be_pulled_in_front_of_a_feed_import(conn):
    """The report this task came from: two recordings behind 47 episodes."""
    episodes = jobs.enqueue_many(conn, "ingest_url", [{"n": n} for n in range(5)], priority=-10)
    recording = jobs.enqueue(conn, "transcribe", params={}, priority=-10)

    jobs.set_priority(conn, recording, 0)

    assert _claim_all(conn) == [recording, *episodes]
