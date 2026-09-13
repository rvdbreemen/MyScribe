---
id: "ADR-009"
title: "SQLite in WAL mode is the only coordination between web, supervisor and runner, restated with anchored import rules"
status: "Accepted"
date: "2026-09-13"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes:
  - "ADR-002"
superseded_by: null
topics:
  - "storage"
  - "job-orchestration"
  - "concurrency"
  - "enforcement"
aliases:
  - "job queue"
  - "first verdict wins"
  - "atomic claim"
  - "no message broker"
  - "anchored import rules"
  - "forbid_import false positive"
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

# ADR-009 SQLite in WAL mode is the only coordination between web, supervisor and runner, restated with anchored import rules

## Status

Accepted, 2026-09-13.
full with one change to the Enforcement block. The supersede itself is
human-gated and has not been run; the pending steps are under Related
Decisions.

## Status History

```yaml
status_history:
  - date: 2026-09-11
    status: Proposed
    changed_by: Claude Opus 5 (agent, session 2026-09-11)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-13
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: Robert asked for it in the session of 2026-09-13 (TASK-033)
    changed_via: adr-kit lifecycle
  - date: 2026-09-13
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: ADR-009 restates it with anchored forbid_import patterns (TASK-033)
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Three parties touch job state, all of them through SQL (structured query
language) against one SQLite file in WAL (write-ahead log) mode: the web
process (enqueue, cancel, read), the supervisor thread inside it (claim,
safety-net verdicts), and each runner child process (stage progress, events,
its own verdict). A runner child shares no memory with the web process
(ADR-001). Something has to make "claim exactly one queued job" and "write
the terminal verdict exactly once" safe when two of them race.

The user chose SQLite as the database on 2026-09-01. ADR-002 records that
WHYcast-transcribe runs the same shape in production; that was not
re-checked for this record.

### Why this record exists

ADR-002 (architecture decision record 002, Accepted 2026-09-03) made this
decision, and the decision does not change here. What changes is how one of
its rules is checked by machine. The kit's guide forbids rewriting an
Accepted record (`.adr-kit/ADR-guide.md`: "Never rewrite an Accepted ADR"),
so the fix arrives as a successor, and a successor has to stand on its own
once its predecessor is history.

**The false positive.** ADR-002's Enforcement block forbids the broker
libraries with three `forbid_import` rules whose patterns are the bare words
`redis`, `celery` and `rq`, over `scribe/**`. adr-kit defines `forbid_import`
as "same engine as `forbid_pattern`; the separate name documents intent"
(adr-kit 0.57.0, `templates/adr-kit-guide.md:144`). In code that is one loop
over both kinds: each pattern is compiled with Python's `re` and searched in
every added diff line, leading `+` stripped (`bin/adr-judge`,
`apply_rules_to_diff` and `parse_diff`). Nothing ties the rule to an import
statement unless the pattern does, and the kit's own example is anchored:
`^#include\s+<ArduinoJson\.h>`. A bare word matches wherever it appears on a
line, prose included, and a match blocks the commit.

**The reproduction.** On 2026-09-11, adr-judge 0.57.0, declarative pass only
(`ADR_KIT_NO_LLM=1`, `--dry-run-enforcement ADR-002`), on this diff to a new
`scribe/x.py`:

```text
+"""Re-transcribing should not rediscover names already known."""
+# the queue is ordered, first come first served
+import redis
```

Both the docstring (line 1) and the import (line 3) are reported as ADR-002
violations.

**The anchored pattern.** `^\s*(?:import|from)\s+redis\b`, and the same for
`celery` and `rq`. `^\s*` admits an import indented inside a function;
`(?:import|from)\s+` requires the statement keyword; `\b` ends the match at
the module name, so `redis.asyncio` and `celery.app` match while
`redistools`, `celery_like` and `rqlite` do not. On the three-line diff above
it reports line 3 only.

A wider probe, 18 added lines in `scribe/x.py`, was judged by the same engine
against each record's block. ADR-009 was judged on a scratch copy of
`docs/adr` with its status set to Accepted in that copy only, because the
judge skips records that are not Accepted. Two lines that neither block flags
(a comment and a `def`) are left out of the table:

| Added line | ADR-002 bare words | ADR-009 anchored |
| --- | --- | --- |
| `"""... rediscover names ..."""` | flagged | not flagged |
| `# redistribute the words ...` | flagged | not flagged |
| `import rqlite` | flagged | not flagged |
| `import redistools` | flagged | not flagged |
| `from celery_like import thing` | flagged | not flagged |
| `irq_count = 0` | flagged | not flagged |
| `pool = "celery"` | flagged | not flagged |
| `import redis` and `import redis as r` | flagged | flagged |
| `from rq import Queue` | flagged | flagged |
| `import celery.app` | flagged | flagged |
| `from redis.asyncio import Redis` | flagged | flagged |
| `    import redis` (inside a function) | flagged | flagged |
| `    from celery import Celery` (inside a function) | flagged | flagged |
| `import os, redis` | flagged | missed |
| `mod = importlib.import_module("redis")` | flagged | missed |

Bare words: 16 findings, 7 of them on lines that import nothing. Anchored: 7
findings, all real imports, and 2 misses, which Consequences names.

**What else changed.**

* ADR-002's Verification names
  `tests/test_jobs.py::test_claim_is_atomic_under_concurrent_threads`, which
  is not in the suite. The race test is
  `test_claim_race_exactly_one_winner_per_job` (`tests/test_jobs.py:64`).
* Line anchors are the code's as of 2026-09-11. ADR-002's acceptance note put
  the claim at `jobs.py:66-72`; it is now `scribe/jobs.py:107-123`.
* Since ADR-002 the web process holds more than one connection: the
  watch-folder thread and the feed thread open their own
  (`scribe/ingest/watching.py:782`, `scribe/ingest/feeds.py:390`, ADR-008)
  and take `db.LOCK` around their statements (for example
  `scribe/ingest/watching.py:230`, `scribe/ingest/feeds.py:83`). The lock
  rule below is worded for that. Every call site was not re-audited for this
  record.
* Both `forbid_pattern` rules for `threading.RLock()` are carried byte for
  byte, messages included. A defect in one of them, found while writing this
  record, is under Consequences.

## Decision Drivers

* No broker, no Redis, no second service for a single-user localhost app.
* Cross-process safety must come from something that cannot be forgotten at a
  call site.
* One file to back up.
* A machine check on "no broker" has to fire on imports and nothing else. A
  rule that blocks a commit over the word "rediscover" gets overridden, and a
  routine override stops meaning anything.

## Considered Options

How the three parties coordinate (ADR-002's question, unchanged):

* **SQLite in write-ahead log mode; claims and verdicts expressed as single
  atomic statements.**
* An in-process queue plus sockets or pipes between processes.
* Redis with RQ (Redis Queue), or Celery.

How "no broker" is checked by machine (the question this record adds):

* **`forbid_import` rules anchored to the import statement.**
* Keep ADR-002's bare words (do nothing).
* Word-bounded patterns such as `\bredis\b`, not anchored.
* Drop the declarative rules and rely on the LLM (large language model) judge
  pass.
* Edit ADR-002's Enforcement block in place.

## Decision Outcome

Chosen option: **SQLite WAL with atomic SQL** (ADR-002's decision,
unchanged), with **"no broker" checked by `forbid_import` rules anchored to
the import statement**, because the two hard coordination problems are each
one statement, and an anchored rule fires on imports alone.

Claiming a job is one statement, and so is finishing one:

```sql
-- claim: exactly one caller can win this.
-- begin immediate takes the write lock up front, which is why
-- two claimers cannot both read the same queued row first.
begin immediate;
update job set status='running', started_at=?, pid=null
 where id=(select id from job where status='queued'
            order by priority desc, id limit 1)
 returning *;

-- verdict: the first writer wins, later writers change no row
update job set status=?, finished_at=?, error_code=?, error_detail=?
 where id=? and status in ('running','queued');
```

`begin immediate` "causes the database connection to start a new write
immediately, without waiting for a write statement", and it fails with
`SQLITE_BUSY` while another connection holds a write transaction
(sqlite.org, `lang_transaction.html`). That is what makes the claim
exclusive across processes.
`tests/test_jobs.py::test_claim_race_exactly_one_winner_per_job` releases
eight threads through a barrier onto four queued jobs and asserts exactly
four distinct claims. Those threads share one connection, so the test proves
in-process exclusivity; the cross-process half rests on the SQLite behaviour
just quoted. The `returning` clause needs SQLite 3.35.0 or newer
(sqlite.org, `lang_returning.html`), which `db.connect` asserts
(`scribe/db.py:462-464`). Because
the verdict only touches a row that is still running or queued
(`scribe/jobs.py:148`; `finish` returns `rowcount == 1` at `:152`), the
runner, the supervisor's safety net and the cancel route can each write one
without coordinating.

Inside the web process `db.LOCK`, one `threading.RLock` defined in
`scribe.db` (`scribe/db.py:17`) and imported everywhere else, guards every
database access, including the shared `sqlite3.Connection` opened with
`check_same_thread=False` (`scribe/db.py:468`). It is never re-created,
because two locks over one connection protect nothing. This is a rule about
the database, not about locking in general: code that guards some other
resource may have its own lock, as long as it never takes `db.LOCK` as well.

"No broker" is checked by three `forbid_import` rules, one per library, each
`^\s*(?:import|from)\s+<name>\b` over `scribe/**`. The `threading.RLock()`
rules are ADR-002's, unchanged. The LLM judge pass stays on for this record:
the block has no `llm_judge` key, and the key defaults to true in adr-kit's
enforcement schema. It reads intent where the patterns cannot.

### Confirmation

`tests/test_jobs.py` (the race, first verdict wins, cancel on a queued job,
event sequence under concurrent emit) and `tests/test_db.py` (WAL mode and
foreign keys, the status constraint). The six tests named under Verification
passed on 2026-09-11: `6 passed, 56 deselected in 4.19s`. The enforcement
change is confirmed by the before-and-after judge runs in Context.

## Decision Contract

### Must

* Job claims use `begin immediate` plus `update ... returning`
  (`scribe/jobs.py:110-116`).
* Terminal verdicts are written with a status guard so the first one wins
  (`scribe/jobs.py:148`).
* `db.LOCK` (the one module-level lock in `scribe.db`) is the only lock
  guarding database access in the web process, and every database access
  there holds it, whichever connection it uses. A lock around a different
  resource is fine (`scribe/web/transcript.py:648-654` keeps one per proxy
  path so two audio requests cannot start the same transcode), but such a
  lock must never also take `db.LOCK`, because two lock orders is how a
  deadlock is built.
* `job.status` stays constrained by a `check` (a rule SQLite enforces on
  every write) to exactly `queued, running, done, failed, cancelled,
  interrupted` (`scribe/db.py:50`).
* A declarative rule in this record's Enforcement block that forbids a module
  matches its import statement, not the module's name anywhere on a line.

### Must Not

* Introduce a second queue or a message broker for job state.
* Import `redis`, `celery` or `rq` anywhere under `scribe/`.
* Store media bytes or transcript artifacts in the database. The one BLOB
  (binary large object) column is `speaker_embedding.embedding`
  (`scribe/db.py:44-46`).

### Exceptions

* None.

### Verification

* `tests/test_jobs.py::test_claim_race_exactly_one_winner_per_job`,
  `::test_finish_first_verdict_wins`,
  `::test_request_cancel_on_queued_cancels_instantly`,
  `::test_emit_monotonic_seq_under_concurrent_emit`.
* `tests/test_db.py::test_wal_and_fk_on`,
  `::test_job_status_check_constraint_rejects_bogus`.
* `ADR_KIT_NO_LLM=1 adr-judge --dry-run-enforcement ADR-009` on a diff like
  the probe flags the import statements and none of the prose or look-alike
  lines (table in Context). The judge applies this record only once it is
  Accepted.
* `grep -rnE "^\s*(import|from)\s+(redis|celery|rq)\b" scribe/` returns
  nothing on 2026-09-11: the rules are a tripwire, not a cleanup.

## Consequences

### Positive

* Correctness lives in SQL, so a new call site cannot forget it.
* The database plus the media directory is the whole backup.
* The "no broker" tripwire fires on imports only. On the probe it flagged 0
  of the 7 lines that import nothing, against 7 of 7 for ADR-002's bare
  words, so a docstring saying "rediscover" no longer blocks a commit.

### Negative

* SQLite has one writer at a time; long transactions in the runner would
  stall the UI (user interface). Mitigated by `busy_timeout=5000`
  (`scribe/db.py:473`) and by keeping runner writes short: progress writes
  are throttled to one per 0.4 s (`REPORT_MIN_INTERVAL`,
  `scribe/runner.py:33`).
* Anchoring narrows what the declarative rule sees. It misses a forbidden
  module imported after another on one line (`import os, redis`), a dynamic
  import (`importlib.import_module("redis")`, `__import__`), and any client
  whose module name is not exactly one of the three (a module named
  `redis_lock`, say). That is the trade: no prose false positives, for a
  small evasion surface that the LLM pass and review cover. The LLM pass
  degrades to advisory when no backend is reachable, so it is a second line,
  not a guarantee.
* A line that begins with `import redis` inside a docstring (a code example)
  is still flagged. Rare, and under `scribe/` arguably wanted.
* Carried defect: the first `threading.RLock()` rule matches no real file. Its
  glob `scribe/[!d]*.py` relies on a character class, which adr-kit 0.57.0's
  `glob_to_regex` does not support: it escapes the `[` and produces
  `^scribe/\[!d\][^/]*\.py$`. In the probe, `threading.RLock()` added to
  `scribe/jobs.py` and to `scribe/doctor.py` went unflagged, while the same
  line in `scribe/web/probe_lock.py` was caught by the second rule
  (`scribe/*/**`). Top-level modules under `scribe/` are therefore not
  declaratively guarded against a second lock. Even as a shell glob,
  `[!d]*.py` would exempt `doctor.py` along with `db.py`. Both rules are
  carried byte for byte, their messages still naming ADR-002, because this
  record's scope is the import rules; fixing the glob is a separate change.

## Pros and Cons of the Options

### SQLite WAL with atomic SQL

* Good, because both hard operations are single statements with tests.
* Good, because there is nothing else to run.
* Bad, because a single writer is a ceiling, irrelevant at one GPU (graphics
  processing unit) job at a time.

### In-process queue plus sockets/pipes

* Good, because it avoids disk writes for progress.
* Bad, because state dies with the process and reconciliation has nothing to
  read on restart.

### Redis/RQ or Celery

* Good, because it scales to many workers.
* Bad, because it adds a service to install, start and monitor for one user
  on one machine, and RQ's worker needs `os.fork`, which Windows does not
  have.

### Anchored forbid_import rules

* Good, because they match the statement, not the word: on the probe, 7 of
  the 9 lines that load a broker library, and 0 of the 7 that do not.
* Good, because only the patterns and messages change; engine, key and glob
  stay as ADR-002 had them.
* Bad, because a narrower pattern has gaps (see Consequences).

### Keep the bare words (do nothing)

* Good, because it needs no new record.
* Bad, because a prose line under `scribe/` blocks the commit, and the way
  through is an `ADR_KIT_OVERRIDE`. An override that is routine stops
  meaning anything.

### Word-bounded patterns (`\bredis\b`)

* Good, because they stop `rediscover`, `redistools`, `rqlite` and
  `irq_count`.
* Bad, because they still flag a comment or string that names a library:
  `# no redis here, see the rq docs` and `pool = "celery"` both match
  (checked with Python's `re` on 2026-09-11).

### LLM judge pass alone

* Good, because it reads intent and would catch a dynamic import.
* Bad, because it is not deterministic and degrades to advisory when the
  backend is unavailable (`.adr-kit/ADR-guide.md`, "Judgment"); the kit
  treats deterministic enforcement as the blocking floor.

### Edit ADR-002's Enforcement block in place

* Good, because it is the smallest diff.
* Bad, because an Accepted record is not rewritten (`.adr-kit/ADR-guide.md`).
  The kit permits reference bookkeeping on one, and the Enforcement block is
  the decision's own surface, not bookkeeping. This record leaves ADR-002
  untouched, bookkeeping included (see Related Decisions).

## Open Questions

None.

## Related Decisions

* ADR-002 (Accepted): the record this one restates and is proposed to
  supersede. The link is in prose only, as ADR-008 links ADR-002. adr-kit
  0.57.0 has no "proposed to supersede" relation, and `adr relate` is not
  used because it writes into ADR-002 as well: a `related` key and a
  status_history entry on both sides (`bin/adr`, `command_relate`). The
  index dates a record from its last status_history entry
  (`bin/adr_catalog.py:336-338`), so ADR-002 would read as re-accepted on
  the day of the link, and `--remove` appends a second entry rather than
  deleting the first. The real edge is written by `adr supersede`, which
  sets ADR-002's `superseded_by` and this record's `supersedes` together,
  and that is human-gated (`.adr-kit/ADR-guide.md`, "Human-gated actions").
  **The supersede is pending a human.** Suggested order:
  1. `adr accept ADR-009`, with the human's own `--confirm`.
  2. `adr supersede ADR-002 --by ADR-009`.

  Accepting before superseding leaves no moment in which neither record is
  enforced, because the judge applies only Accepted records. Until then
  ADR-002 stays Accepted, and its bare-word rules are the ones the
  pre-commit hook applies.
* ADR-001 (the processes this coordinates).
* ADR-003 (what the database stores for transcripts).
* ADR-008 (the feed thread's own connection works under this record's lock
  discipline).

## References

* `scribe/db.py:17` (`LOCK`), `:44-50` (the one BLOB column; the
  `job.status` check), `:462-464` (the SQLite 3.35 assert), `:468-473`
  (`check_same_thread=False`, WAL, `busy_timeout=5000`).
* `scribe/jobs.py:86-123` (`claim_next`), `:133-152` (`finish`).
* `scribe/runner.py:33`, `scribe/web/transcript.py:648-654`,
  `scribe/ingest/watching.py:230` and `:782`, `scribe/ingest/feeds.py:83`
  and `:390`.
* `tests/test_jobs.py:64`, `:114`, `:144`, `:189`; `tests/test_db.py:331`,
  `:336`.
* `docs/superpowers/specs/2026-09-01-myscribe-design.md` section 2 (data
  model).
* adr-kit 0.57.0: `templates/adr-kit-guide.md:129-144` (Enforcement rules
  and the anchored `forbid_import` example); `bin/adr-judge` `parse_diff`,
  `apply_rules_to_diff` and `glob_to_regex` (the engine and the glob
  translation), and its main loop (only Accepted records are judged);
  `bin/adr` `command_relate` (a `related` key and a status_history entry
  on both sides, `--remove` included) and `command_supersede`
  (`superseded_by` and `supersedes` written together);
  `bin/adr_catalog.py:336-338` (the index takes a record's date from its
  last status_history entry).
* `.adr-kit/ADR-guide.md` (never rewrite an Accepted ADR; human-gated
  actions; judgment).
* SQLite, transactions: <https://www.sqlite.org/lang_transaction.html>
  (`begin immediate`; read 2026-09-11).
* SQLite, the returning clause: <https://www.sqlite.org/lang_returning.html>
  (supported since 3.35.0, 2021-03-12; read 2026-09-11).
* SQLite, write-ahead logging: <https://www.sqlite.org/wal.html>.

## Enforcement

```json
{
  "forbid_import": [
    {"pattern": "^\\s*(?:import|from)\\s+redis\\b", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-009)."},
    {"pattern": "^\\s*(?:import|from)\\s+celery\\b", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-009)."},
    {"pattern": "^\\s*(?:import|from)\\s+rq\\b", "path_glob": "scribe/**", "message": "SQLite WAL is the only job coordination (ADR-009)."}
  ],
  "forbid_pattern": [
    {"pattern": "threading\\.RLock\\(\\)", "path_glob": "scribe/[!d]*.py", "message": "db.LOCK is the only lock; import it, do not create another (ADR-002)."},
    {"pattern": "threading\\.RLock\\(\\)", "path_glob": "scribe/*/**", "message": "db.LOCK is the only lock; import it, do not create another (ADR-002)."}
  ],
  "require_pattern": []
}
```
