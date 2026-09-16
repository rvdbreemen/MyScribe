---
id: "ADR-014"
title: "The application log is observation: nothing reads it to decide anything, with an enforcement rule that can match"
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
  - "logging"
  - "observability"
  - "process-model"
  - "enforcement"
aliases:
  - "applog"
  - "app.log"
  - "application log"
  - "log page"
  - "the one reader"
components:
  - "scribe/applog.py"
  - "scribe/web/logs_ui.py"
symbols:
  - "applog.log"
  - "applog.tail"
  - "applog._append"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-014 The application log is observation: nothing reads it to decide anything, with an enforcement rule that can match

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

`scribe/applog.py` is one JSON-Lines file, `logs/app.log`, written by the web
process, the supervisor thread and every runner child, and shown live on
`/logs`. ADR-007 (Accepted 2026-09-06) decided that the log is observation:
nothing under `scribe/` reads it to decide anything; the only readers are the
Log page and a human. That decision is unchanged here.

### Why this record exists

ADR-007 tried to make its reader rule machine-checkable with a
`forbid_pattern` over `path_glob: "scribe/[!w]*/**"`, meant to cover every
package under `scribe/` except `scribe/web/`. The whole-codebase review of
2026-09-16 found that this glob matches no file in the repository: adr-kit's
judge escapes the character class (the same defect ADR-009 recorded for the
`RLock` rule), and even as a shell glob `scribe/[!w]*/**` reaches no
top-level module - `scribe/supervisor.py`, the very file the rule's scenario
names, is outside it. The rule looked active and guarded nothing. The kit's
guide forbids rewriting an Accepted record, so the working rule arrives as a
successor.

The facts ADR-007 recorded about this platform still hold and are carried:
`os.open(O_APPEND)` plus one write is not an atomic append on Windows (the C
runtime seeks, then writes); Windows byte-range locks are mandatory, so the
lock lives on a separate `app.log.lock` taken with short non-blocking tries;
the writer name is thread-local because the supervisor is a thread in the web
process (ADR-001).

## Decision Drivers

* A person debugging a failed recording needs one file with the process
  boundary in it, readable in an editor and on a page.
* The log must never take the app down, block a request thread, or make
  another process wait.
* The log must not become a second record of job state (SQLite stays the
  only coordination, ADR-009/ADR-013).
* A declarative rule must match the files it is about, or it is a tripwire
  nobody can trip.

## Considered Options

* Keep ADR-007's decision and write the reader rule as one pattern over
  `scribe/**`, with the one legitimate reader marked on its own lines.
* List every package except `scribe/web/` by name in the glob, and leave the
  web package to the LLM pass.
* Drop the declarative rule and rely on the LLM pass.
* Leave ADR-007 as it is.

## Decision Outcome

Chosen option: **one pattern over `scribe/**`, with the one reader marked**.
`applog.tail(` and `applog.path().read` are forbidden everywhere under
`scribe/` unless the line carries the marker `the one reader (ADR-014)`; the
three reading lines in `scribe/web/logs_ui.py` carry it. A new reader has to
write the marker to pass, which is a visible act in a diff, and the LLM pass
and review see it. The rule reaches every file, top-level modules included,
because `scribe/**` needs no character class.

The log itself stays what ADR-007 made it: one write of a complete JSON
document per line under the byte lock on `app.log.lock` with a bounded
non-blocking wait (`LOCK_WAIT_S`, `scribe/applog.py:216`), a size cap with
one previous file (`MAX_BYTES`, `:66`), a thread-local writer name, secrets
redacted by field name and by shape, and `applog.log` never raising.

### Confirmation

`tests/test_applog.py` - two processes write a thousand lines each and every
line parses back whole; rotation at the cap keeps one previous file; the
writer name is per thread; the catch-up read is capped. `tests/test_web_logs.py`
- the page and the tail. The declarative rule is confirmed by
`adr-judge --dry-run-enforcement ADR-014` on a probe diff that adds
`applog.tail(` to `scribe/supervisor.py` (flagged) and the marked line in
`scribe/web/logs_ui.py` (not flagged); see Verification.

## Decision Contract

### Must

* Every line is one write of a complete JSON document ending in `\n`, made
  while holding the byte lock on `app.log.lock` - a file nothing reads.
* The lock wait is bounded (`LOCK_WAIT_S`) and non-blocking; on expiry the
  writer writes anyway. A rare spliced line is accepted over a stalled process.
* `applog.log` never raises. A failure to write is reported on stderr once and
  swallowed.
* The writer name in `proc` is thread-local with a process-wide default.
* Retention is a cap: `MAX_BYTES`, one previous file. The file is renamed,
  never truncated.
* Field names containing key, token, secret, password or authorization are
  redacted; URL query strings and cookie-file paths are redacted by shape.
* Every line under `scribe/` that reads the log carries the marker
  `the one reader (ADR-014)`, and only `scribe/web/logs_ui.py` carries it.

### Must Not

* Nothing in `scribe/` may read `app.log` to make a decision: not the
  supervisor, not the runner, not a route. The only readers are `/logs` and a
  human.
* The lock must not be taken on `app.log` itself, and must not be a blocking
  `LK_LOCK`.
* The log must not be a second record of job state. Transitions may appear
  in both places; progress appears only in the event log.

### Exceptions

* `tests/` may read the file to assert on it (the rule's glob is `scribe/**`,
  so tests are outside it).

### Verification

* `tests/test_applog.py::test_two_processes_appending_at_once_interleave_nothing`
* `tests/test_applog.py::test_the_supervisor_thread_names_itself_without_renaming_the_web_process`
* `tests/test_applog.py::test_writers_hold_the_cross_process_mutex_while_deciding_to_rotate`
* `grep -rn "applog\.tail(" scribe/` lists `scribe/web/logs_ui.py` lines only,
  each carrying the marker (2026-09-16).
* Source anchor: `scribe/applog.py::_append`, `scribe/web/logs_ui.py`.

## Consequences

### Positive

* A runner that dies on import is diagnosable from the Log page, by name.
* The log cannot stall a request or a runner, on any platform.
* The reader rule now fires on the file its own scenario names.

### Negative

* The marker is a convention: a reader who writes it in bad faith passes the
  declarative rule. The LLM pass and review are what catch that, and the
  marker makes it visible in the diff.
* A second file to think about under `logs/`, plus one stderr file per
  runner that wrote anything; both are capped or swept.

## Pros and Cons of the Options

### One pattern over `scribe/**` with a marked reader (chosen)

* Good, because it reaches every file, and the exemption is on the line, in
  the diff, rather than in a glob nobody can verify.
* Bad, because it is a convention, not a proof.

### List every package except `scribe/web/`

* Good, because no marker is needed.
* Bad, because a new package is unguarded until someone remembers the list,
  and `scribe/web/` - where a route could read the log - is left to the LLM
  pass entirely.

### LLM pass alone

* Bad, because it degrades to advisory when no backend is reachable; the kit
  treats deterministic enforcement as the floor.

### Leave ADR-007 as it is

* Bad, because the rule matches nothing and says otherwise.

## Open Questions

None.

## Related Decisions

* ADR-007 (Accepted): the record this one restates and is proposed to
  supersede. Suggested order: `adr accept ADR-014`, then
  `adr supersede ADR-007 --by ADR-014`.
* ADR-001 - the supervisor is a thread in the web process, which is why the
  writer name is per thread.
* ADR-009 / ADR-013 - SQLite is the only coordination; this record keeps the
  log on the observation side of that line.

## References

* `scribe/applog.py` (`_append` at `:174`, `tail` at `:281`, `LOCK_WAIT_S`
  at `:216`, `MAX_BYTES` at `:66`).
* `scribe/web/logs_ui.py` - the one reader.
* ADR-009, Consequences, on the judge and character classes in globs.

## Enforcement

```json
{
  "forbid_pattern": [
    {"pattern": "msvcrt\\.locking\\([^)]*LK_LOCK\\b", "path_glob": "scribe/**", "message": "The app log's lock must be non-blocking with a bounded wait (ADR-014)."},
    {"pattern": "^(?!.*the one reader \\(ADR-014\\)).*(applog\\.tail\\(|applog\\.path\\(\\)\\.read)", "path_glob": "scribe/**", "message": "Nothing under scribe/ reads app.log to decide anything; only scribe/web/logs_ui.py reads it, and says so on the line (ADR-014)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
