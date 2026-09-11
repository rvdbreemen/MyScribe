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

import bisect
import sqlite3
import time
from typing import TYPE_CHECKING, Sequence

from scribe import applog, db, jobs, paths

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

    # Read before _commit moves the flag: the names worth carrying over are the
    # ones on the transcript people had open, not on the newest run - which can
    # be a failed attempt that never became current.
    previous = _current_run(ctx.conn, ctx.job["media_id"], run_id)
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

    inherit_speaker_names(ctx.conn, run_id, speakers, previous=previous)
    # Asked even when every name carried over: a new transcript gets a new
    # speaker analysis (Robert, 2026-09-11). The inherited names stand until
    # it answers, and it never writes over one a person typed.
    speaker_job = queue_speaker_pass(
        ctx.conn, ctx.job["media_id"], run_id, speakers, even_if_named=True
    )
    if speaker_job is not None:
        jobs.emit(ctx.conn, ctx.job["id"], "speakers-queued", job_id_queued=speaker_job)

    ctx.report(1.0)


def sweep_speaker_passes(conn: sqlite3.Connection) -> list[int]:
    """Ask who the speakers are for recordings that were never asked.

    TASK-024 queues the pass as a recording finishes, which leaves everything
    transcribed before that feature existed sitting with "Speaker 1" for ever.
    Measured on this library 2026-09-10: sixty runs carried diarized clusters
    and three carried names.

    Four conditions, and each rules out a way this could be wrong.

    * **Clusters exist.** No diarization means nothing to identify; there is no
      question to pay for.
    * **It never ran.** One stored `speakers` answer for this recording is
      enough - even a refused or unhelpful one. Asking again is a decision, not
      a default, and this sweep runs at every start.
    * **Nothing is already queued.** Otherwise every restart before the queue
      drains adds another copy of the same question.
    * **Not private.** A recording pinned private is never sent to an external
      service by something a person did not ask for, and a startup sweep is the
      least deliberate act there is. `queue_speaker_pass` refuses it again on
      the provider check; this simply does not offer it.

    No ceiling on how many it queues, unlike a feed poll: "never asked before"
    bounds itself. It is a one-off catching-up, and the second run finds
    nothing.
    """
    with db.LOCK:
        rows = conn.execute(
            """
            SELECT m.id AS media_id, r.id AS run_id
              FROM media m
              JOIN run r ON r.media_id = m.id AND r.is_current = 1
             WHERE m.trashed_at IS NULL
               AND m.private = 0
               AND EXISTS (SELECT 1 FROM word w
                            WHERE w.run_id = r.id AND w.speaker IS NOT NULL)
               AND NOT EXISTS (SELECT 1 FROM llm_output o
                                WHERE o.media_id = m.id AND o.kind = 'speakers')
               AND NOT EXISTS (SELECT 1 FROM job j
                                WHERE j.media_id = m.id AND j.type = 'llm'
                                  AND j.status IN ('queued', 'running'))
             ORDER BY m.id
            """
        ).fetchall()

    # `m.private = 0` above only reads the recording's own pin; a folder above
    # it pinned private makes it private too (found in review 2026-09-11).
    from scribe.llm import privacy

    queued: list[int] = []
    for row in rows:
        if privacy.is_private(conn, int(row["media_id"])):
            continue
        clusters = [
            str(word["speaker"])
            for word in conn.execute(
                "SELECT DISTINCT speaker FROM word WHERE run_id=? AND speaker IS NOT NULL",
                (row["run_id"],),
            )
        ]
        job_id = queue_speaker_pass(conn, int(row["media_id"]), int(row["run_id"]), clusters)
        if job_id is not None:
            queued.append(job_id)
    if queued:
        applog.log("speakers.sweep", recordings=len(queued), first=queued[0], last=queued[-1])
    return queued


def _current_run(conn: sqlite3.Connection, media_id: int, run_id: int) -> int | None:
    """The run this one is about to replace as the current one, if any."""
    with db.LOCK:
        row = conn.execute(
            "SELECT id FROM run WHERE media_id=? AND is_current=1 AND id<>?", (media_id, run_id)
        ).fetchone()
    return int(row["id"]) if row else None


def inherit_speaker_names(
    conn: sqlite3.Connection, run_id: int, clusters: Sequence[str], *, previous: int | None
) -> list[str]:
    """Carry the previous run's speaker names onto this one, when they still fit.

    `previous` is the run that was current until this one - named by the
    caller, because by the time the names are carried the flag has moved, and
    the newest other run can be a failed attempt with none (found in review
    2026-09-11: a person's name was lost that way). None for a first run.

    A re-transcription should not throw away names that were already right,
    and certainly not one a person typed. It does not save the speaker pass -
    finalize asks again anyway (Robert, 2026-09-11) - but the names stand from
    the moment the run is current, and one the new pass is not sure about
    stays, since the pass only writes a name it is confident of.

    The condition is the voice behind each label, not the text. WHYcast
    compares a transcript fingerprint because its names live in a file beside
    the transcript; here they hang off cluster labels, and a label is only a
    number diarization handed out. Copying a name onto a label that is now
    somebody else's voice would put a real person's name on the wrong words,
    which is worse than asking again.

    So a name carries over when its label covers the same words in both runs,
    judged per label (`_voices_kept`): most of the old run's words under it
    are under it again, and most of the new run's words under it were under
    it before. The first catches a renumbering - the set is still
    {SPEAKER_00, SPEAKER_01} but each is now the other voice. The second
    catches a merge - two old clusters became one new one, which kept all of
    one name's words and is still not that person.

    Until 2026-09-11 the condition was "the named labels are exactly the new
    cluster set". That let a renumbering through, and it dropped every name
    on a run where the pass had named some clusters and not others: 12 of the
    53 named recordings then, among them media 3's Danny and Nancy. Measured
    the same day, 11 re-transcribed recordings, 26 labels: the lowest share
    was 98.9 % (media 46), so on the pipeline as it stands the voice check
    refuses nothing; it is there for the day a pipeline change renumbers them.

    Labels this run no longer has, and labels whose voice moved, get nothing,
    and the pass names them - it never overwrites a name a person typed.

    Returns the clusters that were named.
    """
    if not clusters or previous is None:
        return []

    with db.LOCK:
        old = {
            str(row["cluster_label"]): row
            for row in conn.execute(
                "SELECT cluster_label, display_name, source, llm_output_id, confidence"
                " FROM speaker_label WHERE run_id=?",
                (previous,),
            )
        }
        kept = _voices_kept(conn, previous, run_id)
        old = {
            cluster: row
            for cluster, row in old.items()
            if cluster in clusters and kept.get(cluster, 0.0) >= VOICE_KEPT
        }
        if not old:
            return []

        for cluster, row in old.items():
            conn.execute(
                "INSERT OR IGNORE INTO speaker_label(run_id, cluster_label, display_name,"
                " source, llm_output_id, confidence) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    cluster,
                    row["display_name"],
                    row["source"],
                    row["llm_output_id"],
                    row["confidence"],
                ),
            )
        conn.commit()
    return sorted(old)


# A name is inherited only if its label covers the same words in both runs to
# at least this share, both ways (`_voices_kept`). Measured labels that did not
# move scored 0.989-1.000; two traded labels score near 0. The margin is for
# re-transcription moving word boundaries, not for doubt about which regime a
# label is in.
VOICE_KEPT = 0.8

# How far apart two runs' words may be and still count as the same moment.
# Beyond it the new run heard nothing there, and the word is not evidence
# either way.
_SAME_MOMENT_SECONDS = 1.0


def _voices_kept(conn: sqlite3.Connection, old_run: int, new_run: int) -> dict[str, float]:
    """Per label, how much of it is the same voice in both runs.

    The smaller of two shares. Of the old run's words under the label, how
    many the new run puts under it too - that catches a label that moved to
    another voice. And of the new run's words under it, how many the old run
    had there - that catches a label that swallowed somebody else's: two old
    clusters merged into one new one keep all of one name's words and still
    are not that person. A label missing from either run is absent, which
    `inherit_speaker_names` reads as 0. Caller holds `db.LOCK`.
    """
    old = _labelled_words(conn, old_run)
    new = _labelled_words(conn, new_run)
    there = _same_label_share(old, new)
    back = _same_label_share(new, old)
    return {label: min(share, back[label]) for label, share in there.items() if label in back}


def _labelled_words(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT start, end, speaker FROM word WHERE run_id=? AND speaker IS NOT NULL"
        " ORDER BY start",
        (run_id,),
    ).fetchall()


def _same_label_share(words: list[sqlite3.Row], other: list[sqlite3.Row]) -> dict[str, float]:
    """Per label in `words`, the share whose nearest word in `other` has it too.

    Nearest to the word's midpoint; a word with nothing in `other` within
    `_SAME_MOMENT_SECONDS` is left out - the other run heard nothing there,
    which says nothing about who spoke.
    """
    if not other:
        return {}
    starts = [float(word["start"]) for word in other]

    def gap(word: sqlite3.Row, mid: float) -> float:
        return max(float(word["start"]) - mid, mid - float(word["end"]), 0.0)

    same: dict[str, int] = {}
    seen: dict[str, int] = {}
    for word in words:
        mid = (float(word["start"]) + float(word["end"])) / 2
        i = bisect.bisect_right(starts, mid)
        near = min((other[j] for j in (i - 1, i) if 0 <= j < len(other)), key=lambda w: gap(w, mid))
        if gap(near, mid) > _SAME_MOMENT_SECONDS:
            continue
        label = str(word["speaker"])
        seen[label] = seen.get(label, 0) + 1
        same[label] = same.get(label, 0) + (str(near["speaker"]) == label)
    return {label: same[label] / seen[label] for label in seen}


def queue_speaker_pass(
    conn: sqlite3.Connection,
    media_id: int,
    run_id: int,
    clusters: Sequence[str],
    *,
    even_if_named: bool = False,
) -> int | None:
    """Ask who the speakers are, as soon as there are speakers to ask about.

    TASK-024. Diarization produces clusters and stops; naming them used to be a
    person opening the transcript and pressing a button, which is why 60 runs in
    this library carried clusters and three carried names. This is what makes it
    happen by itself, and it belongs here rather than in the diarize stage
    because `attribute` runs in between: by finalize the words carry their
    cluster, so the transcript the pass reads is the finished one.

    Nothing is queued when there is nothing to ask - no diarization means no
    clusters means no question. A run whose every cluster has a name is left
    alone too, unless `even_if_named`: the catch-up sweep asks only where the
    speakers were never assigned, and finalize asks again after every
    transcription, re-transcriptions included (Robert, 2026-09-11 - TASK-037).

    A private recording is not sent to an external provider, and the pass is
    simply not queued. That is the same rule a bulk action follows, for a
    sharper reason: sending private words out is a decision a person takes
    knowing which recording it is, and a pipeline step is the least conscious
    act there is. On a local provider nothing leaves the machine and the pass
    runs like any other.

    Returns the job id, or None when it queued nothing.
    """
    if not clusters:
        return None

    with db.LOCK:
        named = {
            str(row["cluster_label"])
            for row in conn.execute(
                "SELECT cluster_label FROM speaker_label WHERE run_id=?", (run_id,)
            )
        }
    if named >= set(clusters) and not even_if_named:
        # Every cluster already has a name - typed by somebody, or an answer
        # the catch-up is not the one to question.
        return None

    # Imported here: these pull the provider registry in, and a stage that runs
    # in every transcribe job should not pay for that at import time.
    from scribe import llm
    from scribe.llm import privacy
    from scribe.stages import llm_stage

    provider_name = llm.default_provider(conn)
    if not llm.provider_class(provider_name).is_local and privacy.is_private(conn, media_id):
        return None

    return jobs.enqueue(
        conn,
        llm_stage.JOB_TYPE,
        media_id=media_id,
        params={
            "media_id": media_id,
            "kind": "speakers",
            "provider": provider_name,
            "model": llm.default_model(conn, provider_name),
            "run_id": run_id,
        },
    )


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
