"""The last stage: make the run real, or make it nobody's problem.

Everything before this wrote what it found. This stage decides whether any of
it counts. Four things happen, three of them inside one transaction:

* the speakers `attribute` worked out are written onto the word rows;
* every other run of this media stops being `is_current` and this one starts;
* the measured xRT goes on the run;
* and the search index is checked against the segments before any of that is
  committed.

The check is the reason the transaction exists. A run that is `is_current` is
the run the library opens and the run search claims to cover, so a transcript
whose segments never reached FTS must not become it - the user would get a
transcript that search silently cannot find, which is worse than a job that
failed loudly. Rolling back leaves the previous current run in place and the
words unlabelled, and the job fails with the counts in its error detail.

**Counting the index means counting the index, not the segments.** `segment_fts`
is an external-content table (`content='segment'`), so
`SELECT count(*) FROM segment_fts` reads the `segment` table through it and
returns the number this check is supposed to be comparing against - it agrees
with itself even when the index is provably missing rows. Measured, not
assumed: dropping the insert trigger and adding a segment leaves
`count(*) FROM segment_fts` at 3 and the index holding 2. The index's own row
count lives in the `segment_fts_docsize` shadow table, one row per indexed
document keyed by the segment's rowid, and that is what is read here.

The work directory goes last and on its own. By then the transcript is
committed and safe, so a wav Windows will not let go of is a stray temporary
file, not a reason to make somebody re-run forty minutes of GPU time; the
outcome is recorded in the stage's event instead of raised.
"""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING, Sequence

from scribe import db, jobs, paths

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext


class IndexOutOfStep(Exception):
    """The search index does not hold a row for every segment of this run."""


# --- how fast was it ------------------------------------------------------------


def xrt(media_duration: float, wall_seconds: float) -> float | None:
    """Times faster than realtime: 15.0 means a five-minute file took twenty seconds.

    None rather than 0 when either number is missing, because "we did not
    measure this" and "this run was infinitely slow" are different facts and
    the ETA reads the column afterwards.
    """
    if media_duration <= 0 or wall_seconds <= 0:
        return None
    return media_duration / wall_seconds


# --- did the index keep up ------------------------------------------------------


def indexed_segment_count(conn: sqlite3.Connection, run_id: int) -> int:
    """How many of this run's segments the FTS index actually holds.

    Reads `segment_fts_docsize` - FTS5's own per-document shadow table - rather
    than `segment_fts`, for the reason spelled out in the module docstring: the
    external-content table answers from `segment`, so asking it would be asking
    the question of itself.
    """
    with db.LOCK:
        row = conn.execute(
            "SELECT COUNT(*) FROM segment_fts_docsize"
            " WHERE id IN (SELECT id FROM segment WHERE run_id=?)",
            (run_id,),
        ).fetchone()
    return int(row[0])


# --- the stage ------------------------------------------------------------------


def run(ctx: "RunnerContext") -> None:
    """The stage: commit this run as the current one, or refuse it."""
    run_id = ctx.state.get("run_id")
    if not run_id:
        raise RuntimeError(
            "finalize needs the run the transcribe stage created, "
            "but ctx.state['run_id'] is empty"
        )

    words = ctx.state.get("words") or []
    duration = _media_duration(ctx)
    measured = xrt(duration, _wall_seconds(ctx))

    n_segments = _commit(ctx, run_id, words, measured)

    removed = _remove_work_dir(ctx.job["id"])

    speakers = sorted({word.get("speaker") for word in words if word.get("speaker")})
    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "finalize",
        run_id=run_id,
        n_words=len(words),
        n_segments=n_segments,
        n_speakers=len(speakers),
        speakers=speakers,
        duration=duration,
        xrt=measured,
        work_dir_removed=removed,
    )
    ctx.report(1.0)


def _commit(
    ctx: "RunnerContext", run_id: int, words: Sequence[dict], measured: float | None
) -> int:
    """The one transaction; returns this run's segment count.

    Order matters: the index check comes after the writes and before the
    commit, so a run whose segments never reached FTS takes its speakers, its
    `is_current` and its xRT down with it.
    """
    media_id = ctx.job["media_id"]
    labelled = [
        (word["speaker"], run_id, word["idx"])
        for word in words
        if word.get("speaker") is not None
    ]

    with db.LOCK:
        try:
            # Only the words that got a speaker: the column already defaults to
            # NULL, so writing the others back would be tens of thousands of
            # statements to change nothing. Safe because finalize meets each run
            # exactly once - a retry is a new job with a new run row.
            ctx.conn.executemany(
                "UPDATE word SET speaker=? WHERE run_id=? AND idx=?", labelled
            )
            # This media's other runs, never anybody else's: two runs of two
            # different files are both current, which is the whole point.
            ctx.conn.execute(
                "UPDATE run SET is_current=0 WHERE media_id=? AND id<>?",
                (media_id, run_id),
            )
            ctx.conn.execute(
                "UPDATE run SET is_current=1, xrt=? WHERE id=?", (measured, run_id)
            )

            n_segments = ctx.conn.execute(
                "SELECT COUNT(*) FROM segment WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            n_indexed = indexed_segment_count(ctx.conn, run_id)
            if n_indexed != n_segments:
                raise IndexOutOfStep(
                    f"run {run_id} has {n_segments} segments but the search "
                    f"index holds {n_indexed}; refusing to make a transcript "
                    "current that search cannot find"
                )
            ctx.conn.commit()
        except BaseException:
            ctx.conn.rollback()
            raise
    return int(n_segments)


def _wall_seconds(ctx: "RunnerContext") -> float:
    """How long this job has been running, read fresh from its row.

    `started_at` is stamped by the claim, before the child process exists, so
    it covers the whole pipeline rather than the part of it this stage can see.
    It stops a hair short of the truth - the runner still has finalize's own
    timing and the verdict to write - and that is worth far less than a number
    a user can compare against the file they gave us.

    A job nobody claimed has no `started_at` (a stage driven by hand, a test);
    `created_at` is the honest second best, and 0 means the run gets no xRT.
    """
    with db.LOCK:
        row = ctx.conn.execute(
            "SELECT started_at, created_at FROM job WHERE id=?", (ctx.job["id"],)
        ).fetchone()
    if row is None:
        return 0.0
    started = row["started_at"] or row["created_at"]
    if not started:
        return 0.0
    return max(0.0, time.time() - float(started))


def _media_duration(ctx: "RunnerContext") -> float:
    """Seconds of media as probe recorded them; 0 when nobody knows."""
    if not ctx.job.get("media_id"):
        return 0.0
    with db.LOCK:
        row = ctx.conn.execute(
            "SELECT duration FROM media WHERE id=?", (ctx.job["media_id"],)
        ).fetchone()
    return float(row["duration"]) if row is not None and row["duration"] else 0.0


def _remove_work_dir(job_id: int) -> bool:
    """Delete this job's scratch; returns whether it is actually gone.

    Never raises. The transcript is committed by the time this runs, so a file
    another process still has open is litter for a later sweep, not a failed
    job. `job_work_dir` is resolved here rather than imported so a test that
    moves WORK_DIR moves this too.
    """
    return paths.remove_job_work_dir(job_id)
