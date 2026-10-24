---
id: "ADR-031"
title: "Every shutdown the app lives through ends the running runner, and only a requested stop requeues"
status: "Proposed"
date: "2026-10-24"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-001"
  - "ADR-028"
topics:
  - "shutdown"
  - "jobs"
  - "background"
aliases:
  - "orphan runner"
  - "SIGTERM to the app"
  - "runner outlives the app"
components:
  - "scribe.supervisor"
symbols:
  - "Supervisor.stop"
  - "STOP_JOIN_SECONDS"
  - "SHUTDOWN_GRACE_SECONDS"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-031 Every shutdown the app lives through ends the running runner, and only a requested stop requeues

## Status

Proposed, 2026-10-24.

## Status History

```yaml
status_history:
  - date: 2026-10-24
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-24
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: "ADR-031 amends ADR-028: every shutdown ends the runner, only the route requeues"
    changed_via: adr-kit lifecycle
  - date: 2026-10-24
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: ADR-031 relies on ADR-001's reconcile for a shutdown nobody asked for
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-028 requeues only on the stop route, which TASK-112.09 gives Quit, Ctrl+C
and a SIGTERM to the launcher. Other shutdowns skip it: a SIGTERM straight to
the app, Ctrl+C or Ctrl+Break in a terminal running `python -m scribe`, and
the launcher's hard stop when the route does not answer in time.

On those, `Supervisor.stop` left the runner alone. On macOS and Linux it
leads a session of its own and ran on as an orphan holding the GPU (graphics
processing unit): in WSL (Windows Subsystem for Linux) it wrote `done` 13 s
after the app had gone (2026-10-21). On Windows, Ctrl+Break to the app left
it running too (`tests/test_app_signal_stop.py`, red on 2026-10-24).

An open job page's event stream ends only when the job does, and uvicorn's
graceful shutdown waited for it before the lifespan ran: after Ctrl+Break the
slow fake job ran to `done` (2026-10-24). The launcher's hard stop SIGKILLs
the app's group 15 s (`STOP_TIMEOUT`) after SIGTERM, which never reaches the
runner's session.

## Decision Drivers

* No runner outlives the app on any OS (TASK-112.09, criterion 3), so a next start never finds the card taken (ADR-001, ADR-013).
* A stop nobody asked for is not a requeue (ADR-028's Must Not).

## Considered Options

* **A.** The app ends the runner's tree on every shutdown it lives through; only the stop route requeues.
* **B.** Every shutdown the app lives through also requeues.
* **C.** The runner ends itself when its parent goes (Linux `PR_SET_PDEATHSIG`, a parent poll elsewhere).

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-10-24 (his option b). It
reuses ADR-028's kill path and changes nothing about which stops requeue. B
would requeue after a logout or a service manager's stop, which ADR-028 rules
out. C is per-OS code in every runner, and macOS has no parent-death signal.

### Confirmation

A real `python -m scribe` running the slow fake job gets SIGTERM (POSIX) or Ctrl+Break (Windows), with and
without its job page open: the runner goes with the app, and the row stays `running` until reconcile.

## Decision Contract

### Must

* `Supervisor.stop` ends the running runner's tree and waits for it on every stop, and joins for at least `STOP_JOIN_SECONDS`.
* A stop without the route leaves the row `running`, for the next start's `reconcile()` to mark it `interrupted`, or `cancelled` when a cancel was asked.
* The lifespan's end stops the supervisor before the phone door, the feeds and the watcher.
* The app's server cancels open requests `SHUTDOWN_GRACE_SECONDS` (3 s) after a stop begins, so no request holds off the lifespan.
* The hard stop says "marked interrupted" only where the app lived through its SIGTERM, or on Windows.

### Must Not

* Requeue on a stop that did not come through the stop route.

### Exceptions

* An app that ends without its lifespan can still leave a runner on macOS and Linux: a SIGKILL, a crash, the hard stop's SIGKILL after `STOP_TIMEOUT`, or uvicorn's forced exit on a second Ctrl+C. The hard stop's sentence then says the job can keep running.

### Verification

* `tests/test_app_signal_stop.py`; `tests/test_app.py::test_the_supervisor_stops_before_the_watchers`; `tests/test_supervisor.py::test_a_stop_nobody_asked_for_ends_the_runner_and_leaves_the_job_to_reconcile`.
* `tests/test_launcher.py::test_the_hard_stops_sentence_says_what_this_os_does_with_the_running_job` and `::test_the_hard_stop_says_whether_it_had_to_kill_the_app`.

## Consequences

### Positive

* A service manager's stop, a logout or a Ctrl+C no longer leaves a runner holding the card, and an open job page no longer holds a stop for the rest of the job.

### Negative

* A job ended this way is `interrupted` and waits for a Retry; a request still in flight 3 s into a stop is cut off, an upload included.
* The `--stop` hard stop that ends a launcher cannot see whether that launcher had to SIGKILL the app, so on macOS and Linux it gives the cautious sentence.

## Open Questions

- [x] What does the app do with the runner on a shutdown that did not come through the stop route? — **Answered 2026-10-24 by User: Robert van den Breemen:** it ends the runner's tree without requeueing (option b); recorded by Claude from the session.

## Related Decisions

* ADR-028: amended - its Negative "an orphaned runner that runs on elsewhere" no longer holds; its Must Not on requeueing is unchanged.
* ADR-001: unchanged - `reconcile()` still turns the left row into `interrupted`.

## References

* Backlog TASK-112.09, implementation note of 2026-10-24.
* `scribe/supervisor.py` (`Supervisor.stop`), `scribe/app.py` (lifespan), `scribe/__main__.py`, `packaging/launcher/myscribe_launcher.py` (`AppProcess.stop`, `end_tree`, `ended`).
