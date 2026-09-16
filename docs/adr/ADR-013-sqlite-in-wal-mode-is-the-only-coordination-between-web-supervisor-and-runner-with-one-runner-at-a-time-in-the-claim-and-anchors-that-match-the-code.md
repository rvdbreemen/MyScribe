---
id: "ADR-013"
title: "SQLite in WAL mode is the only coordination between web, supervisor and runner, with one runner at a time in the claim and anchors that match the code"
status: "Proposed"
date: "2026-09-16"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "storage"
  - "job-orchestration"
  - "concurrency"
  - "enforcement"
aliases:
  - "job queue"
  - "first verdict wins"
  - "atomic claim"
  - "one runner at a time"
  - "no message broker"
  - "anchored import rules"
components:
  - "scribe.db"
  - "scribe.jobs"
symbols:
  - "claim_next"
  - "finish"
  - "db.LOCK"
  - "reconcile"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-013 SQLite in WAL mode is the only coordination between web, supervisor and runner, with one runner at a time in the claim and anchors that match the code

## Status

Proposed, 2026-09-16.

## Status History

```yaml
status_history:
  - date: 2026-09-16
    status: Proposed
    changed_by: Claude Fable 5.1 (agent)
    reason: Initial proposal
    changed_via: adr-kit
```

## Context and Problem Statement

Three parties touch job state, all of them through SQL against one SQLite
file in WAL (write-ahead log) mode: the web process (enqueue, cancel, read),
the supervisor thread inside it (claim, safety-net verdicts), and each runner
child process (stage progress, events, its own verdict). A runner child shares
no memory with the web process (ADR-001). Something has to make "claim exactly
one queued job", "run at most one job at a time" and "write the terminal
verdict exactly once" safe when two of them race.

### Why this record exists

ADR-009 (Accepted 2026-09-13) made this decision and is not changed here. The
whole-codebase review of 2026-09-16 found three places where the record no
longer describes the code, and the kit's guide forbids rewriting an Accepted
record, so the corrections arrive as a successor that stands on its own.

* **The claim SQL drifted twice.** ADR-009 quotes the claim as
  `order by priority desc, id`. The FIFO key has been `queue_seq` since
  schema v16 (TASK-047): a person can move a job, and `id` cannot move. And
  since TASK-070 the claim is refused while any job is running -
  `AND NOT EXISTS (SELECT 1 FROM job WHERE status='running')` inside the same
  statement - because ADR-001's "at most one GPU runner" was the supervisor's
  habit of running its loop in sequence, not a rule SQLite enforced: a second
  app instance on the same data, or a runner that outlived a stopped app,
  claimed beside the running job and put two children on one card.
* **Anchors moved.** `claim_next` is at `scribe/jobs.py:108-158`, `finish` at
  `:166-185`, the SQLite assert at `scribe/db.py:536`, WAL and `busy_timeout`
  at `:542-547`. ADR-009's Must names `scribe/web/transcript.py:648-654` as the
  one permitted lock over a resource other than the database; that lock is now
  `playback._proxy_lock` (`scribe/playback.py:274-281`), one per proxy path,
  and `transcript.py` holds no lock at all.
* **A carried defect.** ADR-009 kept ADR-002's `threading.RLock()` rule with
  the glob `scribe/[!d]*.py` and said so in Consequences: adr-kit's judge does
  not support a character class, so no top-level module under `scribe/` was
  guarded. The rule is restated below without the class.

## Decision Drivers

* A claim, a verdict and "one at a time" must each be one atomic statement,
  so a new call site cannot forget the rule and two processes cannot race it.
* No second service to install, start or monitor for one user on one machine.
* The declarative rules must match the files they name, or they guard nothing
  while looking active.

## Considered Options

* SQLite in WAL mode with atomic SQL, one-at-a-time inside the claim
  statement, and anchored `forbid_import` rules (ADR-009 restated).
* A cross-process lock file or pid file for "one runner at a time".
* A message broker (Redis/RQ, Celery).
* Leave ADR-009 as it is.

## Decision Outcome

Chosen option: **SQLite WAL with atomic SQL, one-at-a-time in the claim**,
because every hard coordination problem is then one statement with a test.

The statements, as the code has them:

```sql
-- claim: exactly one caller can win this, and nobody wins while a job runs.
-- begin immediate takes the write lock up front, which is why two claimers
-- cannot both read the same queued row first; the not-exists clause is in
-- the same statement, so it cannot race either.
begin immediate;
update job set status='running', started_at=?, pid=null
 where id=(select id from job where status='queued'
            order by priority desc, queue_seq, id limit 1)
   and not exists (select 1 from job where status='running')
 returning *;

-- verdict: the first writer wins, later writers change no row
update job set status=?, finished_at=?, error_code=?, error_detail=?
 where id=? and status in ('running','queued');
```

A refused claim would hold the queue for ever once the running row's runner
had died, so the supervisor loop calls `reconcile()` whenever a claim comes
back empty (`scribe/supervisor.py:343`): a running row whose pid is dead, or
whose pid belongs to a process younger than the job, is flipped to
`interrupted`; a live one holds the queue, which is the point. With one
instance and no orphan nothing changes.

Inside the web process `db.LOCK`, one `threading.RLock` defined in
`scribe.db` (`scribe/db.py:17`) and imported everywhere else, guards every
database access, including the shared connection opened with
`check_same_thread=False` (`scribe/db.py:542`). The supervisor, watch-folder
and feed threads open their own connections (`scribe/supervisor.py:334`,
`scribe/ingest/watching.py:782`, `scribe/ingest/feeds.py:641`) under the same
lock discipline. A lock over some other resource is fine -
`playback._proxy_lock` keeps one per proxy path so two audio requests cannot
start the same transcode - as long as it never also takes `db.LOCK`.

"No broker" is checked by three `forbid_import` rules anchored to the import
statement, as ADR-009 established. The `threading.RLock()` rule is one
pattern over `scribe/**` that exempts the single defining line by regex
rather than by a glob the engine cannot read.

### Confirmation

`tests/test_jobs.py` (`test_claim_next_refuses_while_a_job_is_running`,
`test_claim_race_exactly_one_winner_per_job` - one winner per round of eight
claimers, `test_finish_first_verdict_wins`), `tests/test_supervisor.py`
(`test_a_dead_orphan_from_a_previous_life_does_not_hold_the_queue`,
`test_a_live_orphan_from_a_previous_life_holds_the_queue_until_it_ends`) and
`tests/test_db.py` (`test_wal_and_fk_on`,
`test_job_status_check_constraint_rejects_bogus`). On 2026-09-16 the sixteen
test files that call `claim_next` passed, one file at a time (670 tests).
The declarative rules were confirmed on 2026-09-16 with
`adr-judge --dry-run-enforcement ADR-013` over a scratch copy marked Accepted:
a probe diff adding `threading.RLock()` to `scribe/supervisor.py` and
`import redis` to `scribe/db.py` was flagged twice, and the re-added line
`LOCK = threading.RLock()` in `scribe/db.py` was not.

## Decision Contract

### Must

* Job claims use `begin immediate` plus `update ... returning`, ordered by
  `priority desc, queue_seq, id`, with `not exists (... status='running')`
  in the same statement (`scribe/jobs.py:142-150`).
* Terminal verdicts are written with a status guard so the first one wins
  (`scribe/jobs.py:178-182`).
* The supervisor reconciles running rows whenever a claim comes back empty,
  and at startup before accepting work (`scribe/supervisor.py:179`, `:343`).
* `db.LOCK` (`scribe/db.py:17`) is the only lock guarding database access in
  the web process, and every database access there holds it, whichever
  connection it uses. A lock around a different resource is fine
  (`scribe/playback.py:274-281`), but such a lock must never also take
  `db.LOCK`, because two lock orders is how a deadlock is built.
* `job.status` stays constrained by a `check` to exactly `queued, running,
  done, failed, cancelled, interrupted` (`scribe/db.py:50`).
* A declarative rule in this record's Enforcement block that forbids a module
  matches its import statement, not the module's name anywhere on a line.

### Must Not

* Introduce a second queue, a message broker, a lock file or a pid file for
  job state or for "one runner at a time".
* Import `redis`, `celery` or `rq` anywhere under `scribe/`.
* Store media bytes or transcript artifacts in the database. The one BLOB
  column is `speaker_embedding.embedding` (`scribe/db.py:46`).

### Exceptions

* None.

### Verification

* `tests/test_jobs.py::test_claim_next_refuses_while_a_job_is_running`,
  `::test_claim_race_exactly_one_winner_per_job`,
  `::test_finish_first_verdict_wins`,
  `::test_request_cancel_on_queued_cancels_instantly`,
  `::test_emit_monotonic_seq_under_concurrent_emit`.
* `tests/test_supervisor.py::test_a_dead_orphan_from_a_previous_life_does_not_hold_the_queue`,
  `::test_a_live_orphan_from_a_previous_life_holds_the_queue_until_it_ends`.
* `tests/test_db.py::test_wal_and_fk_on`,
  `::test_job_status_check_constraint_rejects_bogus`.
* `grep -rnE "^\s*(import|from)\s+(redis|celery|rq)\b" scribe/` returns
  nothing on 2026-09-16.
* `grep -rn "threading.RLock()" scribe/` returns `scribe/db.py:17` only on
  2026-09-16.

## Consequences

### Positive

* Correctness lives in SQL, so a new call site, a second app instance or a
  new life beside an orphan cannot start a second runner.
* The database plus the media directory is the whole backup.
* Every top-level module under `scribe/` is now declaratively guarded
  against a second `RLock`, which ADR-009 admitted it was not.

### Negative

* SQLite has one writer at a time; long transactions in the runner would
  stall the UI. Mitigated by `busy_timeout=5000` (`scribe/db.py:547`) and by
  throttling runner progress writes to one per 0.4 s
  (`REPORT_MIN_INTERVAL`, `scribe/runner.py:33`).
* A live runner left by a previous app life holds the queue until it ends.
  That is the rule working; the reconcile on every empty claim keeps a dead
  one from holding it.
* The anchored import rules still miss `import os, redis` and a dynamic
  import, as ADR-009 said; the LLM pass and review cover that.
* Line anchors drift. This record dates its anchors (2026-09-16); a future
  reader checks the symbol, not the number.

## Pros and Cons of the Options

### SQLite WAL with atomic SQL, one-at-a-time in the claim (chosen)

* Good, because "one runner at a time" is one clause in a statement that
  already exists, with the same cross-process guarantee the claim has.
* Bad, because a refused claim needs the reconcile-on-empty to avoid
  starving on a dead row - one more thing the loop does.

### A lock file or pid file for one runner at a time

* Good, because it is independent of the database.
* Bad, because it is a second coordination mechanism with its own stale-file
  problem, exactly what this decision exists to avoid.

### A message broker

* Bad, because it adds a service for one user on one machine, and RQ's
  worker needs `os.fork`, which Windows does not have.

### Leave ADR-009 as it is

* Bad, because a reader implementing a second claim path from the record
  would order by `id` and forget the not-exists clause, and the RLock rule
  would keep guarding nothing.

## Open Questions

None.

## Related Decisions

* ADR-009 (Accepted): the record this one restates and is proposed to
  supersede. As with ADR-009 and ADR-002, the edge is written by
  `adr supersede`, which is human-gated. Suggested order: `adr accept
  ADR-013`, then `adr supersede ADR-009 --by ADR-013`, so there is no moment
  in which neither record is enforced.
* ADR-001 (the processes this coordinates; "at most one GPU runner" is now
  SQLite's rule).
* ADR-003 (what the database stores for transcripts).
* ADR-008 (the feed thread's own connection works under this lock discipline).

## References

* `scribe/jobs.py:108-158` (`claim_next`), `:166-185` (`finish`).
* `scribe/db.py:17` (`LOCK`), `:46` (the one BLOB column), `:50` (the status
  check), `:536` (the SQLite 3.35 assert), `:542-547` (WAL, `busy_timeout`).
* `scribe/supervisor.py:179` (`reconcile`), `:334`, `:343`.
* `scribe/playback.py:274-281` (`_proxy_lock`), `scribe/runner.py:33`,
  `scribe/ingest/watching.py:782`, `scribe/ingest/feeds.py:641`.
* Backlog TASK-070 (one runner at a time), TASK-047 (`queue_seq`).

## Enforcement

```json
{
  "forbid_import": [
    {"pattern": "^\\s*(?:import|from)\\s+redis\\b", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-013)."},
    {"pattern": "^\\s*(?:import|from)\\s+celery\\b", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-013)."},
    {"pattern": "^\\s*(?:import|from)\\s+rq\\b", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-013)."}
  ],
  "forbid_pattern": [
    {"pattern": "^(?!LOCK = threading\\.RLock\\(\\)\\s*$).*threading\\.RLock\\(\\)", "path_glob": "scribe/**", "message": "db.LOCK is the only RLock; import it, do not create another (ADR-013)."}
  ],
  "require_pattern": []
}
```
