---
id: "ADR-007"
title: "The application log is observation: nothing reads it to decide anything"
status: "Accepted"
date: "2026-09-06"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "logging"
  - "observability"
  - "process-model"
  - "windows-file-locking"
aliases:
  - "applog"
  - "app.log"
  - "application log"
  - "log page"
  - "runner stderr"
components:
  - "scribe/applog.py"
  - "scribe/web/logs_ui.py"
symbols:
  - "applog.log"
  - "applog.tail"
  - "applog._append"
  - "supervisor._report_exit"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-007 The application log is observation: nothing reads it to decide anything

## Status

Accepted, 2026-09-06.

## Status History

```yaml
status_history:
  - date: 2026-09-06
    status: Proposed
    changed_by: Claude (session 2026-09-06)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-06
    status: Accepted
    changed_by: Robert van den Breemen
    reason: "Accepted by the user in session 2026-09-06 (explicit: 'Accept ADR-007'); the one open question answered by the user the same day"
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

A recording that did not transcribe could not be diagnosed from the job
events: they live in SQLite per job and are blind before a job exists (the
recorder's start, chunks and finish; the upload; the enqueue), between the
supervisor and the runner (which pid, what exit code), and when the runner
child dies before its first stage - it was spawned with no stderr, so the one
sentence explaining a DLL (dynamic-link library) that would not load went
nowhere.

`scribe/applog.py` answers that with one JSON-Lines file, `logs/app.log`,
written by the web process, the supervisor thread and every runner child, and
shown live on `/logs`. Building it produced three facts about this platform
that are easy to "fix" back into failures, and one boundary question with
ADR-002, which says SQLite in WAL (write-ahead logging) mode is the only coordination between the
processes and `db.LOCK` the only lock guarding the connection:

* `os.open(O_APPEND)` + one `os.write` per line is **not** an atomic append on
  Windows. The C runtime implements `_O_APPEND` as a seek followed by a write,
  and two writers of a thousand lines each produced spliced lines. A test
  caught it.
* Windows byte-range locks are **mandatory**. A lock on byte 0 of `app.log`
  itself made every *read* of the file fail with "Permission denied" while a
  writer held it, and `msvcrt`'s blocking mode waits in one-second steps for up
  to ten seconds. The `web_ai` suite went from 29 s to over 200 s; moving the
  lock to a separate `app.log.lock` taken with 2 ms non-blocking retries
  brought it back to 28.7 s.
* A module-global writer name is wrong in a process with threads: the
  supervisor is a thread inside the web process (ADR-001), and naming it
  globally renamed every web request's line to `supervisor`. The name is
  thread-local with a process default.

Is a cross-process file lock, and a `threading.Lock()` in `applog`, a second
coordination mechanism in the sense ADR-002 forbids?

## Decision Drivers

* A person debugging a failed recording needs one file with the process
  boundary in it, readable in an editor and on a page, without cross-referencing
  SQLite.
* The log must never take the app down with it, block a request thread, or
  make another process wait: a missing line is the smaller failure.
* ADR-002's reason - no queues, no brokers, no second source of truth for job
  state - must keep holding.
* The empirical findings above are one careless refactor away from recurring;
  a docstring is not where the next maintainer looks before "simplifying" a
  lock.

## Considered Options

* Write the log through SQLite (a `log` table) so ADR-002's one mechanism
  covers it.
* Write the log through Python's `logging` with a `FileHandler`.
* A JSON-Lines file with a byte-lock on a separate lock file, taken with a
  bounded non-blocking wait, and a rule that nothing reads it to decide
  anything.

## Decision Outcome

Chosen option: **the JSON-Lines file with a bounded lock and the
observation rule**, because a log that lives in the database is unreadable
the moment the database is the problem, `logging.FileHandler` is `open(path,
"a")` and splices on Windows for the same reason, and the file lock is not
coordination: no process changes its behaviour because of anything in the
log or the lock. ADR-002 is untouched by letter (its Must was narrowed at
acceptance to the lock guarding the database connection) and by spirit (the
log is written, never consulted).

### Confirmation

`tests/test_applog.py` - two processes write a thousand lines each and every
line parses back whole; rotation at the cap keeps one previous file; the
writer name is per thread; the catch-up read is capped. `tests/test_web_logs.py`
- the page and the tail. `tests/test_supervisor.py` - a runner that dies before
its first stage leaves its stderr in the log by name.

## Decision Contract

### Must

* Every line is one write of a complete JSON document ending in `\n`, made
  while holding the byte lock on `app.log.lock` - a file nothing reads.
* The lock wait is bounded (`LOCK_WAIT_S`) and non-blocking; on expiry the
  writer writes anyway. A rare spliced line is accepted over a stalled process.
* `applog.log` never raises. A failure to write is reported on stderr once and
  swallowed.
* The writer name in `proc` is thread-local with a process-wide default; a
  thread that shares a process with request threads names only itself.
* Retention is a cap: `MAX_BYTES`, one previous file. The file is renamed,
  never truncated.
* Field names containing key, token, secret, password or authorization are
  redacted; URL query strings and cookie-file paths are redacted by shape.

### Must Not

* Nothing in `scribe/` may read `app.log` to make a decision: not the
  supervisor, not the runner, not a route. The only readers are `/logs` and a
  human.
* The lock must not be taken on `app.log` itself, and must not be a blocking
  `LK_LOCK`.
* The log must not be a second record of job state. Transitions may appear
  in both places; progress (percentages, ETA (estimated time of arrival)
  samples) appears only in the event log.

### Exceptions

* `tests/` may read the file to assert on it.

### Verification

* `tests/test_applog.py::test_two_processes_appending_at_once_interleave_nothing`
* `tests/test_applog.py::test_the_supervisor_thread_names_itself_without_renaming_the_web_process`
* `tests/test_applog.py::test_writers_hold_the_cross_process_mutex_while_deciding_to_rotate`
* Source anchor: `scribe/applog.py::_append`

## Consequences

### Positive

* A runner that dies on import is diagnosable from the Log page, by name.
* The log cannot stall a request or a runner, on any platform.
* The findings about Windows appends and mandatory locks are recorded where a
  maintainer will meet them before changing the code.

### Negative

* A second file to think about under `logs/`, plus one stderr file per
  runner that wrote anything; both are capped or swept (32 MB + one; seven
  days).
* Under sustained contention a line may splice; the page shows it as
  `unparseable` rather than hiding it.

## Pros and Cons of the Options

### A `log` table in SQLite

* Good, because one mechanism and one lock.
* Bad, because the log is needed most when the database is locked, migrating
  or wedged - the cases it would then be unable to report.
* Bad, because a runner that dies before opening its connection writes nothing.

### `logging.FileHandler`

* Good, because it is the standard library.
* Bad, because it is `open(path, "a")`: the seek-then-write that splices on
  Windows, with no cross-process lock at all.

### JSON Lines with a separate lock file (chosen)

* Good, because every line is whole, every process can write, and a text
  editor can read it.
* Bad, because the lock discipline is subtle and platform-specific - which is
  why this record exists.

## Open Questions

- [x] Should `runner-<job>.stderr` files be folded into the app log entirely (tail only) rather than kept for seven days? — **Answered 2026-09-06 by Robert van den Breemen:** Keep them: runner-<job>.stderr stays under logs/ for seven days (supervisor.sweep_stderr at startup) and the 4 KB tail rides on runner.exited in app.log. A crash the log only quotes the tail of can be read in full for a week; a runner that wrote megabytes of warnings before the real error would otherwise be unreadable. Decided by the user, 2026-09-06.

## Related Decisions

* ADR-001 - the supervisor is a thread in the web process, which is why the
  writer name is per thread.
* ADR-002 - SQLite is the only coordination; this record is what keeps the
  log on the observation side of that line.

## References

* `scribe/applog.py` module docstring and `_append`, with the measurements.
* Commit `bb92a15` (the log) and the review of 2026-09-06 (CR-001, CR-002,
  CR-011, CR-020, CR-025).
* Microsoft C runtime, `_open` with `_O_APPEND`: "positions the file pointer
  at the end of the file before every write operation" - a seek, then a write.

## Enforcement

```json
{
  "forbid_pattern": [
    {"pattern": "msvcrt\\.locking\\([^)]*LK_LOCK\\b", "path_glob": "scribe/**", "message": "The app log's lock must be non-blocking with a bounded wait (ADR-007)."},
    {"pattern": "applog\\.tail\\(|applog\\.path\\(\\)\\.read", "path_glob": "scribe/[!w]*/**", "message": "Nothing under scribe/ reads app.log to decide anything; only scribe/web/logs_ui.py reads it (ADR-007)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
