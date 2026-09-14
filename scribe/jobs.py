"""Job queue: enqueue, claim, finish (first-verdict-wins), events, ETA.

Every write is serialized in-process by db.LOCK (the one RLock, defined
in db.py). Cross-process safety comes from the SQL itself: BEGIN
IMMEDIATE claims, the status CHECK constraint, and finish() only
touching rows still in a non-terminal status — the first verdict wins,
whoever delivers it (runner, supervisor safety net, or cancel route).
"""

import json
import sqlite3
import statistics
import time
from typing import Any, Mapping, Sequence

from scribe import applog, db

# Statuses a job must be in before a retry makes sense: the terminal ones
# that are not `done`. Read by the JSON API and the jobs board alike.
RETRYABLE_STATUSES: tuple[str, ...] = ("failed", "cancelled", "interrupted")

# Back of the queue: greater than every job now queued. Only queued rows are
# ever ordered, so the counter need not outlive the queue - which is what keeps
# this a bounded index read instead of a scan over every job ever run.
_NEXT_SEQ = "(SELECT COALESCE(MAX(queue_seq), 0) + 1 FROM job WHERE status='queued')"

# Front of this row's own level; correlated on the outer `job` row.
_FRONT_SEQ = (
    "(SELECT COALESCE(MIN(q.queue_seq), 0) - 1 FROM job q"
    " WHERE q.status='queued' AND q.priority = job.priority)"
)

MOVES: tuple[str, ...] = ("front", "back")

PRIORITY_MIN, PRIORITY_MAX = -1000, 1000
"""A band, not a vocabulary. What a person picks from is the board's ladder;
this only stops a typo or a local client from writing a number that sorts above
everything for good."""


def enqueue(
    conn: sqlite3.Connection,
    type_: str,
    media_id: int | None = None,
    params: dict | None = None,
    priority: int = 0,
    retry_of: int | None = None,
) -> int:
    """Insert a queued job; returns the new job id."""
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO job(type, media_id, params_json, priority, retry_of, created_at,"
            f" queue_seq) VALUES (?, ?, ?, ?, ?, ?, {_NEXT_SEQ})",
            (type_, media_id, json.dumps(params or {}), priority, retry_of, time.time()),
        )
        conn.commit()
        job_id = cur.lastrowid
    applog.log("job.enqueued", job=job_id, type=type_, media=media_id, params=params or {},
               retry_of=retry_of)
    return job_id


def enqueue_many(
    conn: sqlite3.Connection,
    type_: str,
    params_list: Sequence[dict],
    priority: int = 0,
) -> list[int]:
    """Insert N queued jobs in one transaction: all of them, or none.

    A feed import queues one job per ticked episode, and five hundred single
    `enqueue` calls are five hundred commits and five hundred log lines on the
    event loop - measured 2026-09-08 at ~917 ms against ~17 ms for one
    transaction, the difference being the log lines. A failure halfway
    through single calls would also leave half the episodes queued with no
    answer to the browser. Rolled back on any exception, `KeyboardInterrupt`
    included, the way `claim_next` guards its own transaction.

    One log line carries the count and the first and last id, which are
    contiguous under one write transaction, rather than the id list, which
    `applog` would cut at fifty anyway.
    """
    params_list = list(params_list)
    if not params_list:
        return []
    ids: list[int] = []
    with db.LOCK:
        try:
            # One read for the batch, then base + i. The subquery per row would
            # add ~84 ms to the 17 ms this function's docstring measures, and
            # nothing can interleave: the loop runs in one write transaction.
            base = int(conn.execute(f"SELECT {_NEXT_SEQ}").fetchone()[0])
            for offset, params in enumerate(params_list):
                cur = conn.execute(
                    "INSERT INTO job(type, media_id, params_json, priority, retry_of, created_at,"
                    " queue_seq) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (type_, None, json.dumps(params), priority, None, time.time(), base + offset),
                )
                ids.append(cur.lastrowid)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    applog.log("job.enqueued_many", type=type_, count=len(ids), first=ids[0], last=ids[-1])
    return ids


def claim_next(conn: sqlite3.Connection, mode: str = "gpu") -> dict | None:
    """Claim the next queued job (priority DESC, then FIFO by `queue_seq`).

    mode="gpu" (default): atomically flips the job to running via
    BEGIN IMMEDIATE + UPDATE ... RETURNING, so exactly one claimer wins
    even across processes.

    mode="cpu-prework": returns the next queued job whose CPU prework
    is still pending WITHOUT touching its status; the caller reports
    completion via mark_prework_done().
    """
    if mode == "cpu-prework":
        with db.LOCK:
            row = conn.execute(
                "SELECT * FROM job WHERE status='queued' AND cpu_prework_done=0"
                " ORDER BY priority DESC, queue_seq, id LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None
    if mode != "gpu":
        raise ValueError(f"unknown claim mode: {mode!r}")

    with db.LOCK:
        if conn.in_transaction:  # never nest inside a stray open transaction
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "UPDATE job SET status='running', started_at=?, pid=NULL"
                " WHERE id=(SELECT id FROM job WHERE status='queued'"
                "           ORDER BY priority DESC, queue_seq, id LIMIT 1)"
                " RETURNING *",
                (time.time(),),
            ).fetchone()
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return dict(row) if row is not None else None


def mark_prework_done(conn: sqlite3.Connection, job_id: int) -> None:
    """Record that the CPU prework stages for this job have completed."""
    with db.LOCK:
        conn.execute("UPDATE job SET cpu_prework_done=1 WHERE id=?", (job_id,))
        conn.commit()


def finish(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> bool:
    """Deliver a terminal verdict; the first one wins.

    Only a job still queued or running accepts a verdict; a second call
    is refused (returns False) and the row keeps the first verdict.
    """
    with db.LOCK:
        cur = conn.execute(
            "UPDATE job SET status=?, finished_at=?, error_code=?, error_detail=?"
            " WHERE id=? AND status IN ('running','queued')",
            (status, time.time(), error_code, error_detail, job_id),
        )
        conn.commit()
        return cur.rowcount == 1


def request_cancel(conn: sqlite3.Connection, job_id: int) -> None:
    """Set the cooperative cancel flag; a still-queued job dies instantly."""
    with db.LOCK:
        conn.execute("UPDATE job SET cancel_requested=1 WHERE id=?", (job_id,))
        conn.commit()
        row = conn.execute(
            "SELECT status FROM job WHERE id=?", (job_id,)
        ).fetchone()
        if row is not None and row["status"] == "queued":
            finish(conn, job_id, "cancelled")


def set_priority(conn: sqlite3.Connection, job_id: int, priority: int) -> bool:
    """Move a queued job to another priority level, at the back of it.

    At the back, deliberately (TASK-047): a job whose priority was just raised
    must not jump ahead of the jobs already queued at that level, and the
    oldest row in the table is exactly the case a plain id ordering gets
    wrong. "Run this next" is priority plus `move_in_queue(…, "front")` - two
    actions, because they answer two different questions.

    Returns False for anything but a queued job. That refusal is a race as
    much as a rule: the supervisor can claim the job between the board reading
    the row and this statement landing, so the guard lives inside the UPDATE
    the way `finish` does it - SQLite is the only coordination there is
    (ADR-009).
    """
    if not PRIORITY_MIN <= priority <= PRIORITY_MAX:
        raise ValueError(
            f"priority {priority} is outside [{PRIORITY_MIN}, {PRIORITY_MAX}]"
        )
    with db.LOCK:
        before = conn.execute("SELECT priority FROM job WHERE id=?", (job_id,)).fetchone()
        row = conn.execute(
            f"UPDATE job SET priority=?, queue_seq={_NEXT_SEQ}"
            " WHERE id=? AND status='queued' RETURNING priority, queue_seq",
            (priority, job_id),
        ).fetchone()
        conn.commit()
    if row is None:
        return False
    was = int(before["priority"]) if before is not None else 0
    emit(conn, job_id, "priority", was=was, now=priority, position=queue_position(conn, job_id))
    applog.log("job.priority", job=job_id, was=was, now=priority)
    return True


def move_in_queue(conn: sqlite3.Connection, job_id: int, where: str) -> bool:
    """Put a queued job at the front or the back of its own priority level.

    Its own level, never the whole queue: a bulk import moved to the front is
    still bulk, and a person who wants it ahead of everything says so by
    raising its priority. Returns False for anything but a queued job, for the
    reason `set_priority` gives.
    """
    if where not in MOVES:
        raise ValueError(f"{where!r} is not one of {', '.join(MOVES)}")
    seq = _FRONT_SEQ if where == "front" else _NEXT_SEQ
    with db.LOCK:
        row = conn.execute(
            f"UPDATE job SET queue_seq={seq}"
            " WHERE id=? AND status='queued' RETURNING queue_seq",
            (job_id,),
        ).fetchone()
        conn.commit()
    if row is None:
        return False
    emit(conn, job_id, "moved", where=where, position=queue_position(conn, job_id))
    applog.log("job.moved", job=job_id, where=where)
    return True


def emit(conn: sqlite3.Connection, job_id: int, kind: str, **payload: Any) -> int:
    """Append a job event; returns its per-job monotonically increasing seq."""
    with db.LOCK:
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM job_event WHERE job_id=?",
            (job_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO job_event(job_id, seq, ts, kind, payload_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (job_id, seq, time.time(), kind, json.dumps(payload)),
        )
        conn.commit()
        return seq


def events_after(
    conn: sqlite3.Connection, job_id: int, after_seq: int
) -> list[dict]:
    """Events with seq > after_seq, in order, payload decoded."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT * FROM job_event WHERE job_id=? AND seq>? ORDER BY seq",
            (job_id, after_seq),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "job_id": row["job_id"],
            "seq": row["seq"],
            "ts": row["ts"],
            "kind": row["kind"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def set_stage(
    conn: sqlite3.Connection, job_id: int, stage: str, progress: float
) -> None:
    """Record the job's current stage and 0..1 progress within it."""
    with db.LOCK:
        conn.execute(
            "UPDATE job SET stage=?, stage_progress=? WHERE id=?",
            (stage, progress, job_id),
        )
        conn.commit()


def record_stage_perf(
    conn: sqlite3.Connection,
    stage: str,
    model: str | None,
    media_duration: float,
    wall_seconds: float,
) -> None:
    """Log one completed stage's wall time for ETA calibration."""
    with db.LOCK:
        conn.execute(
            "INSERT INTO stage_perf(stage, model, media_duration, wall_seconds, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (stage, model, media_duration, wall_seconds, time.time()),
        )
        conn.commit()


def eta_seconds(
    conn: sqlite3.Connection,
    stage: str,
    model: str | None,
    media_duration: float,
) -> float | None:
    """Estimated wall seconds for a stage on this machine.

    Rolling median of wall/duration over the last 5 recorded runs of
    (stage, model), scaled by media_duration. None without history.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT media_duration, wall_seconds FROM stage_perf"
            " WHERE stage=? AND model IS ? ORDER BY id DESC LIMIT 5",
            (stage, model),
        ).fetchall()
    ratios = [
        row["wall_seconds"] / row["media_duration"]
        for row in rows
        if row["media_duration"] > 0
    ]
    if not ratios:
        return None
    return statistics.median(ratios) * media_duration


def job_eta(conn: sqlite3.Connection, row: Mapping[str, Any], model: str | None) -> float | None:
    """ETA for a job row's current stage, or None when it cannot be known.

    ``model`` is the `stage_perf` key the runner files timings under -
    `transcribe.perf_model_for(params)` (ADR-004); it is the caller's to
    resolve, because the stage module that defines it imports this one. None
    without a stage, a media, a known duration, or history for the pair.
    """
    if not row["stage"] or not row["media_id"]:
        return None
    with db.LOCK:
        media = conn.execute(
            "SELECT duration FROM media WHERE id=?", (row["media_id"],)
        ).fetchone()
    if media is None or not media["duration"]:
        return None
    return eta_seconds(conn, row["stage"], model, float(media["duration"]))


def queue_position(conn: sqlite3.Connection, job_id: int) -> int | None:
    """1-based claim-order position of a queued job; None if not queued."""
    with db.LOCK:
        row = conn.execute(
            "SELECT status, priority, queue_seq FROM job WHERE id=?", (job_id,)
        ).fetchone()
        if row is None or row["status"] != "queued":
            return None
        # Named, not ?1/?2: Python 3.12.0-3.12.3 (Ubuntu 24.04's python3)
        # warns that numbered placeholders bound from a tuple will break in
        # 3.14. They do not - later 3.12s and 3.14 take them - but a dict is
        # quiet everywhere.
        ahead = conn.execute(
            "SELECT COUNT(*) FROM job WHERE status='queued'"
            " AND (priority > :priority OR (priority = :priority AND queue_seq < :seq))",
            {"priority": row["priority"], "seq": row["queue_seq"]},
        ).fetchone()[0]
    return ahead + 1


def active_count(conn: sqlite3.Connection) -> int:
    """How many jobs are running or queued - the number that decides whether
    a page watching the board should poll fast or idle."""
    with db.LOCK:
        return conn.execute(
            "SELECT COUNT(*) FROM job WHERE status IN ('running', 'queued')"
        ).fetchone()[0]
