"""The glossary post-pass: what the decoder heard, spelled the way it is spelled.

Hotwords bias the decoder but do not command it, so a name can still come out
as "Vermulen" after being asked for as "Vermeulen". This stage is the second
attempt: it slides the glossary over the finished words and writes down where
they disagree.

**It writes down, it does not overwrite** (ADR-003). Every correction is a
`word_correction` row keyed to `(run_id, word_idx)`; `word.text` keeps what
Whisper produced. `scribe.glossary` holds the rules and the SQL; this module
is only the two ways the pass gets run:

* **as the last step before finalize** in the transcribe pipeline, on the run
  the transcribe stage left in `ctx.state`, so a fresh transcript arrives with
  its corrections already applied;
* **as a job type of its own**, which is what the settings page queues - one
  `correct` job per transcribed media - when the glossary changes. That path
  has no `ctx.state`, so it finds the media's current run itself.

Re-running is not additive: `glossary.store` replaces the whole layer, so a
term deleted from the glossary this morning has its corrections gone by this
afternoon's re-run. That is the property that makes the "re-run corrections on
the whole library" button safe to press twice.

Nothing here loads a model (ADR-001). It runs in a runner child because it is
a stage, not because it needs one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from scribe import db, glossary, jobs

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

JOB_TYPE = "correct"
"""Registered in `scribe.runner.STAGES` as a job type in its own right, and
its `run` is also the `correct` step of the transcribe pipeline."""


def run(ctx: "RunnerContext") -> None:
    """Rebuild this run's correction layer from the glossary as it is now."""
    run_id = _run_id(ctx)
    terms = glossary.terms(ctx.conn)
    words = glossary.words_of(ctx.conn, run_id)

    corrections = glossary.corrections_for(words, terms)
    ctx.report(0.9)
    written = glossary.store(ctx.conn, run_id, corrections)

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "correct",
        run_id=run_id,
        n_terms=len(terms),
        n_words=len(words),
        n_corrections=written,
        rules=sorted({fix.rule for fix in corrections}),
    )
    ctx.report(1.0)


def _run_id(ctx: "RunnerContext") -> int:
    """The run to correct: the one this job just produced, else the media's
    current one.

    The fallback is the whole point of the standalone job type - a re-run
    started from the settings page has no pipeline behind it, only a media id -
    and it deliberately refuses rather than inventing a run: a media with no
    transcript has nothing to correct, and saying so is more useful than a job
    that succeeds having done nothing.
    """
    from_pipeline = ctx.state.get("run_id")
    if from_pipeline:
        return int(from_pipeline)

    media_id = ctx.job.get("media_id")
    if not media_id:
        raise RuntimeError(
            "a correct job needs either a run from the pipeline or a media to "
            "find one for; this one has neither"
        )
    with db.LOCK:
        row = ctx.conn.execute(
            "SELECT id FROM run WHERE media_id=? AND is_current=1 ORDER BY id DESC LIMIT 1",
            (media_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(
            f"media {media_id} has no current run; there is nothing to correct yet"
        )
    return int(row["id"])


STAGES: list[tuple[str, Callable]] = [("correct", run)]
