import threading

import pytest

from scribe import db, jobs


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


# --- enqueue -----------------------------------------------------------------


def test_enqueue_inserts_queued_job_with_params(conn):
    job_id = jobs.enqueue(conn, "fake", params={"scenario": "ok"}, priority=3)
    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "queued"
    assert row["type"] == "fake"
    assert row["priority"] == 3
    assert row["params_json"] == '{"scenario": "ok"}'
    assert row["created_at"] > 0
    assert row["retry_of"] is None


def test_enqueue_records_retry_of(conn):
    first = jobs.enqueue(conn, "fake")
    second = jobs.enqueue(conn, "fake", retry_of=first)
    row = conn.execute("SELECT retry_of FROM job WHERE id=?", (second,)).fetchone()
    assert row["retry_of"] == first


# --- claim_next --------------------------------------------------------------


def test_claim_next_empty_queue_returns_none(conn):
    assert jobs.claim_next(conn) is None


def test_claim_next_flips_status_and_orders_by_priority_then_id(conn):
    low = jobs.enqueue(conn, "fake", priority=0)
    high = jobs.enqueue(conn, "fake", priority=5)
    low2 = jobs.enqueue(conn, "fake", priority=0)

    first = jobs.claim_next(conn)
    assert first["id"] == high
    assert first["status"] == "running"
    assert first["started_at"] is not None
    jobs.finish(conn, high, "done")

    assert jobs.claim_next(conn)["id"] == low
    jobs.finish(conn, low, "done")
    assert jobs.claim_next(conn)["id"] == low2
    jobs.finish(conn, low2, "done")
    assert jobs.claim_next(conn) is None

    statuses = {
        row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM job")
    }
    assert statuses == {low: "done", high: "done", low2: "done"}


def test_claim_next_refuses_while_a_job_is_running(conn):
    """One runner at a time is SQLite's rule, not the supervisor's habit.

    ADR-001 promises at most one GPU runner and delivered it per supervisor
    only, by running its loop in sequence; a second app instance on the same
    data, or a runner that outlived a stopped app, claimed beside it and two
    children loaded Whisper on one card (TASK-070). The claim is now refused
    inside the same statement while any job is running, so it cannot race.
    """
    first = jobs.enqueue(conn, "fake")
    second = jobs.enqueue(conn, "fake")

    assert jobs.claim_next(conn)["id"] == first
    assert jobs.claim_next(conn) is None  # not "nothing queued": something is running
    assert _row(conn, second)["status"] == "queued"

    jobs.finish(conn, first, "done")
    assert jobs.claim_next(conn)["id"] == second


def _row(conn, job_id):
    return conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()


def test_claim_race_exactly_one_winner_per_job(conn):
    """Eight claimers released through one barrier onto the queue: one wins
    and seven get None, because the claim is one statement under BEGIN
    IMMEDIATE and is refused while a job runs. Finish the winner and the next
    round has one winner again, until every row has been claimed exactly once
    and nothing is claimed twice."""
    queued = [jobs.enqueue(conn, "fake") for _ in range(4)]
    claimed: list[int] = []
    lock = threading.Lock()

    def round_of_claims() -> list[int]:
        wins: list[int] = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            row = jobs.claim_next(conn)
            if row is not None:
                with lock:
                    wins.append(row["id"])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return wins

    for _ in queued:
        wins = round_of_claims()
        assert len(wins) == 1, wins  # one runner at a time, however many ask
        claimed.extend(wins)
        jobs.finish(conn, wins[0], "done")

    assert sorted(claimed) == sorted(queued)  # every row once, no duplicates
    assert round_of_claims() == []


def test_claim_cpu_prework_returns_row_without_flipping_status(conn):
    job_id = jobs.enqueue(conn, "fake")
    row = jobs.claim_next(conn, mode="cpu-prework")
    assert row["id"] == job_id
    assert (
        conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()[
            "status"
        ]
        == "queued"
    )

    jobs.mark_prework_done(conn, job_id)
    assert (
        conn.execute(
            "SELECT cpu_prework_done FROM job WHERE id=?", (job_id,)
        ).fetchone()["cpu_prework_done"]
        == 1
    )
    # nothing left needing prework
    assert jobs.claim_next(conn, mode="cpu-prework") is None
    # but the gpu claim still sees it
    assert jobs.claim_next(conn)["id"] == job_id


# --- finish: first verdict wins ----------------------------------------------


def test_finish_first_verdict_wins(conn):
    job_id = jobs.enqueue(conn, "fake")
    jobs.claim_next(conn)

    assert jobs.finish(conn, job_id, "done") is True
    assert jobs.finish(conn, job_id, "failed", error_code="RUNTIME") is False

    row = conn.execute(
        "SELECT status, error_code, finished_at FROM job WHERE id=?", (job_id,)
    ).fetchone()
    assert row["status"] == "done"
    assert row["error_code"] is None
    assert row["finished_at"] is not None


def test_finish_failed_records_error(conn):
    job_id = jobs.enqueue(conn, "fake")
    jobs.claim_next(conn)
    assert jobs.finish(conn, job_id, "failed", "RUNTIME", "boom") is True
    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert (row["status"], row["error_code"], row["error_detail"]) == (
        "failed",
        "RUNTIME",
        "boom",
    )


# --- cancel ------------------------------------------------------------------


def test_request_cancel_on_queued_cancels_instantly(conn):
    job_id = jobs.enqueue(conn, "fake")
    jobs.request_cancel(conn, job_id)
    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "cancelled"
    assert row["cancel_requested"] == 1
    assert row["finished_at"] is not None


def test_request_cancel_on_running_sets_flag_only(conn):
    job_id = jobs.enqueue(conn, "fake")
    jobs.claim_next(conn)
    jobs.request_cancel(conn, job_id)
    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "running"  # runner/supervisor delivers the verdict
    assert row["cancel_requested"] == 1


# --- events ------------------------------------------------------------------


def test_emit_and_events_after_round_trip(conn):
    job_id = jobs.enqueue(conn, "fake")
    seq1 = jobs.emit(conn, job_id, "stage", name="probe")
    seq2 = jobs.emit(conn, job_id, "progress", pct=0.5)
    assert (seq1, seq2) == (1, 2)

    events = jobs.events_after(conn, job_id, 0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[0]["kind"] == "stage"
    assert events[0]["payload"] == {"name": "probe"}
    assert events[1]["payload"] == {"pct": 0.5}

    assert [e["seq"] for e in jobs.events_after(conn, job_id, 1)] == [2]
    assert jobs.events_after(conn, job_id, 2) == []


def test_emit_seq_is_per_job(conn):
    a = jobs.enqueue(conn, "fake")
    b = jobs.enqueue(conn, "fake")
    assert jobs.emit(conn, a, "x") == 1
    assert jobs.emit(conn, b, "x") == 1
    assert jobs.emit(conn, a, "x") == 2


def test_emit_monotonic_seq_under_concurrent_emit(conn):
    job_id = jobs.enqueue(conn, "fake")
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        for _ in range(25):
            jobs.emit(conn, job_id, "tick")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = [e["seq"] for e in jobs.events_after(conn, job_id, 0)]
    assert seqs == list(range(1, 101))


# --- stage + perf ------------------------------------------------------------


def test_set_stage_updates_row(conn):
    job_id = jobs.enqueue(conn, "transcribe")
    jobs.set_stage(conn, job_id, "probe", 0.25)
    row = conn.execute(
        "SELECT stage, stage_progress FROM job WHERE id=?", (job_id,)
    ).fetchone()
    assert (row["stage"], row["stage_progress"]) == ("probe", 0.25)


def test_eta_seconds_none_without_history(conn):
    assert jobs.eta_seconds(conn, "transcribe", "large-v3-turbo", 60.0) is None


def test_eta_seconds_median_ratio_over_last_five(conn):
    # oldest row has an absurd ratio; the last-5 window must exclude it
    jobs.record_stage_perf(conn, "transcribe", "large-v3-turbo", 10.0, 1000.0)
    for ratio in (0.1, 0.2, 0.2, 0.3, 0.4):
        jobs.record_stage_perf(conn, "transcribe", "large-v3-turbo", 100.0, ratio * 100.0)

    eta = jobs.eta_seconds(conn, "transcribe", "large-v3-turbo", 60.0)
    assert eta == pytest.approx(0.2 * 60.0)  # median ratio 0.2 x duration

    # a different model has no history
    assert jobs.eta_seconds(conn, "transcribe", "tiny", 60.0) is None


# --- queue_position ----------------------------------------------------------


def _media_with_duration(conn, duration):
    cur = conn.execute(
        "INSERT INTO media(sha256, store_path, orig_name, title, size_bytes, created_at, duration)"
        " VALUES (?, 'p', 'o.wav', 'o', 1, 1.0, ?)",
        ("b" * 64, duration),
    )
    conn.commit()
    return cur.lastrowid


def test_retryable_statuses_are_the_three_terminal_ones_a_retry_makes_sense_for():
    assert jobs.RETRYABLE_STATUSES == ("failed", "cancelled", "interrupted")


def test_job_eta_is_none_without_a_stage_a_media_a_duration_or_history(conn):
    """The ETA of a job row under a perf key, or None for every reason the
    number cannot be known - the JSON API and the jobs board both ask."""
    job_id = jobs.enqueue(conn, "transcribe")  # no media at all
    row = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    assert jobs.job_eta(conn, row, "large-v3-turbo") is None

    silent = _media_with_duration(conn, None)
    job_id = jobs.enqueue(conn, "transcribe", media_id=silent)
    jobs.set_stage(conn, job_id, "transcribe", 0.2)
    row = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    assert jobs.job_eta(conn, row, "large-v3-turbo") is None  # duration unknown

    row["stage"] = None
    assert jobs.job_eta(conn, row, "large-v3-turbo") is None  # no stage yet


def test_job_eta_scales_the_stage_history_by_the_media_duration(conn):
    media_id = _media_with_duration(conn, 600.0)
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_id)
    jobs.set_stage(conn, job_id, "transcribe", 0.2)
    row = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    assert jobs.job_eta(conn, row, "large-v3-turbo") is None  # no history yet

    jobs.record_stage_perf(conn, "transcribe", "large-v3-turbo", 100.0, 50.0)

    assert jobs.job_eta(conn, row, "large-v3-turbo") == pytest.approx(300.0)
    assert jobs.job_eta(conn, row, "large-v3") is None  # history is per model


def test_queue_position_counts_same_or_higher_priority(conn):
    j1 = jobs.enqueue(conn, "fake", priority=0)
    j2 = jobs.enqueue(conn, "fake", priority=5)
    j3 = jobs.enqueue(conn, "fake", priority=0)

    assert jobs.queue_position(conn, j2) == 1  # highest priority first
    assert jobs.queue_position(conn, j1) == 2
    assert jobs.queue_position(conn, j3) == 3


def test_queue_position_none_when_not_queued(conn):
    job_id = jobs.enqueue(conn, "fake")
    jobs.claim_next(conn)
    assert jobs.queue_position(conn, job_id) is None
    assert jobs.queue_position(conn, 999) is None


# --- many jobs in one transaction (TASK-021) --------------------------------


def test_enqueue_many_inserts_every_row_with_consecutive_ids(conn):
    import json

    ids = jobs.enqueue_many(conn, "ingest_url", [{"url": f"https://x.test/{i}"} for i in range(5)])

    assert ids == list(range(ids[0], ids[0] + 5))
    rows = conn.execute("SELECT id, type, status, params_json FROM job ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == ids
    assert [r["status"] for r in rows] == ["queued"] * 5
    assert [r["type"] for r in rows] == ["ingest_url"] * 5
    assert json.loads(rows[3]["params_json"]) == {"url": "https://x.test/3"}


def test_enqueue_many_is_all_or_nothing(conn):
    """A feed import queues one job per ticked episode; a failure halfway
    through must not leave half of them queued with no answer."""
    import sqlite3

    class Flaky:
        """The connection, with its third INSERT INTO job blowing up."""

        def __init__(self, real):
            self._real = real
            self.inserts = 0

        def execute(self, sql, *args):
            if sql.lstrip().startswith("INSERT INTO job"):
                self.inserts += 1
                if self.inserts == 3:
                    raise sqlite3.OperationalError("disk I/O error")
            return self._real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    with pytest.raises(sqlite3.OperationalError):
        jobs.enqueue_many(Flaky(conn), "ingest_url", [{"url": f"https://x.test/{i}"} for i in range(5)])

    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
    assert not conn.in_transaction


def test_enqueue_many_with_nothing_to_enqueue_inserts_nothing(conn):
    assert jobs.enqueue_many(conn, "ingest_url", []) == []
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
