---
id: "ADR-002"
title: "SQLite in WAL mode is the only coordination between web, supervisor and runner"
status: "Accepted"
date: "2026-09-03"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "storage"
  - "job-orchestration"
  - "concurrency"
aliases:
  - "job queue"
  - "first verdict wins"
  - "atomic claim"
components:
  - "scribe.db"
  - "scribe.jobs"
symbols:
  - "claim_next"
  - "finish"
  - "db.LOCK"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-002 SQLite in WAL mode is the only coordination between web, supervisor and runner

## Status

Accepted, 2026-09-03.

## Status History

```yaml
status_history:
  - date: 2026-09-02
    status: Proposed
    changed_by: Claude Fable 5.1 (agent)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-03
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert in an interactive session on 2026-09-03. Every claim verified against the code: the atomic claim at jobs.py:66-72, the first-verdict guard returning rowcount==1, the SQLite 3.35 assert, the status CHECK constraint, and speaker_embedding as the only BLOB. The lock rule was narrowed from 'the only lock' to 'the only lock guarding the database connection' after the grill found the proxy transcode locks in scribe/web/transcript.py."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Three parties touch job state - all of them through SQL (structured query
language) against one SQLite file in WAL (write-ahead log) mode: the web process (enqueue, cancel, read), the
supervisor thread (claim, safety-net verdicts), and each runner child (stage
progress, events, its own verdict). They are separate processes with no shared
memory. Something has to make "claim exactly one queued job" and "write the
terminal verdict exactly once" safe when two of them race.

The user chose SQLite as the database on 2026-09-01. WHYcast-transcribe runs
the same shape in production and its pattern is proven.

## Decision Drivers

* No broker, no Redis, no second service for a single-user localhost app.
* Cross-process safety must come from something that cannot be forgotten at a
  call site.
* One file to back up.

## Considered Options

* SQLite in write-ahead log mode; claims and verdicts expressed as single
  atomic statements.
* An in-process queue plus sockets or pipes between processes.
* Redis with RQ (Redis Queue), or Celery.

## Decision Outcome

Chosen option: **SQLite WAL with atomic SQL**, because the two hard problems
are each one statement:

Claiming a job is one statement, and so is finishing one:

```sql
-- claim: exactly one caller can win this.
-- begin immediate takes the write lock up front, which is why
-- two claimers cannot both read the same queued row first.
begin immediate;
update job set status='running', started_at=?
 where id=(select id from job where status='queued'
            order by priority desc, id limit 1)
 returning *;

-- verdict: the first writer wins, later writers change no row
update job set status=?, finished_at=?, error_code=?, error_detail=?
 where id=? and status in ('running','queued');
```

Eight racing threads on four queued jobs yield exactly four claims
(`tests/test_jobs.py`). The claim needs SQLite 3.35 or newer for the
returning clause, which `db.connect` asserts. Because the verdict only
touches a row that is still running or queued, the runner, the supervisor's
safety net and the cancel route can all write one without coordinating.

Inside the web process a single `sqlite3.Connection`
(`check_same_thread=False`) is shared behind one `threading.RLock` defined in
`scribe.db` and imported everywhere else — never re-created, because two locks
over one connection protect nothing. This is a rule about the database, not
about locking in general: code that guards some other shared resource may
have its own lock, as long as it never takes `db.LOCK` as well.

### Confirmation

`tests/test_jobs.py` (race test, first-verdict-wins, cancel-on-queued, event
seq under concurrent emit) and `tests/test_db.py` (write-ahead log mode, foreign keys, the status
constraint).

## Decision Contract

### Must

* Job claims use `begin immediate` plus `update … returning`.
* Terminal verdicts are written with a status guard so the first one wins.
* db.LOCK (the one module-level lock in `scribe.db`) is the only lock
  guarding the database connection, and every
  database access in the web process holds it. A lock around a different
  resource is fine - `scribe/web/transcript.py` keeps one per proxy path so
  two audio requests cannot start the same transcode - but such a lock must
  never also take `db.LOCK`, because two lock orders is how a deadlock is
  built.
* `job.status` stays constrained by a `check` (a rule SQLite enforces on
  every write) to exactly `queued, running, done, failed, cancelled, interrupted`.

### Must Not

* Introduce a second queue or a message broker for job state.
* Store media bytes or transcript artifacts in the database.

### Exceptions

* None.

### Verification

* `tests/test_jobs.py::test_claim_is_atomic_under_concurrent_threads` (name as
  in the suite), `tests/test_jobs.py` first-verdict tests,
  `tests/test_db.py::test_wal_and_fk_on`.

## Consequences

### Positive

* Correctness lives in SQL, so a new call site cannot forget it.
* The database plus the media directory is the whole backup.

### Negative

* SQLite has one writer at a time; long transactions in the runner would
  stall the UI (user interface). Mitigated by `busy_timeout=5000` and by keeping runner writes
  short (progress writes are throttled to one per 0.4 s).

## Pros and Cons of the Options

### SQLite WAL with atomic SQL

* Good, because both hard operations are single statements with tests.
* Good, because there is nothing else to run.
* Bad, because a single writer is a ceiling — irrelevant at one GPU (graphics
  processing unit) job at a time.

### In-process queue plus sockets/pipes

* Good, because it avoids disk writes for progress.
* Bad, because state dies with the process and reconciliation has nothing to
  read on restart.

### Redis/RQ or Celery

* Good, because it scales to many workers.
* Bad, because it adds a service to install, start and monitor for one user
  on one machine — and RQ needs `os.fork`, which Windows does not have.

## Open Questions

* None.

## Related Decisions

* ADR-001 (the processes this coordinates).
* ADR-003 (what the database stores for transcripts).

## References

* `scribe/db.py`, `scribe/jobs.py`.
* `docs/superpowers/specs/2026-09-01-myscribe-design.md` §2.

## Enforcement

```json
{
  "forbid_import": [
    {"pattern": "redis", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-002)."},
    {"pattern": "celery", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-002)."},
    {"pattern": "rq", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-002)."}
  ],
  "forbid_pattern": [
    {"pattern": "threading\\.RLock\\(\\)", "path_glob": "scribe/[!d]*.py", "message": "db.LOCK is the only lock; import it, do not create another (ADR-002)."},
    {"pattern": "threading\\.RLock\\(\\)", "path_glob": "scribe/*/**", "message": "db.LOCK is the only lock; import it, do not create another (ADR-002)."}
  ],
  "require_pattern": []
}
```
