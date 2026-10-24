---
id: "ADR-032"
title: "Every shutdown the app lives through ends the running runner, and only a requested stop requeues"
status: "Proposed"
date: "2026-10-25"
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
  - "scribe.web.stopping"
symbols:
  - "Supervisor.stop"
  - "STOP_JOIN_SECONDS"
  - "SHUTDOWN_GRACE_SECONDS"
  - "UPLOAD_FINISH_SECONDS"
  - "StoppingServer"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-032 Every shutdown the app lives through ends the running runner, and only a requested stop requeues

## Status

Proposed, 2026-10-25.

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
    reason: "ADR-032 amends ADR-028: every shutdown ends the runner, only the route requeues"
    changed_via: adr-kit lifecycle
  - date: 2026-10-24
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: ADR-032 relies on ADR-001's reconcile for a shutdown nobody asked for
    changed_via: adr-kit lifecycle
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: Renumbered from ADR-031 (TASK-124 holds ADR-031); upload exemption, the 3 s grace and the lifespan stop order recorded as agent additions Robert approved on 2026-10-25
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
slow fake job ran to `done` (2026-10-24). A single uvicorn timeout that cut
the stream also cut an upload still on its way in, a recording the user then
has to send again. The launcher's hard stop SIGKILLs the app's group
`APP_SIGTERM_WAIT` after SIGTERM, which never reaches the runner's session.
That wait outlasts the app's whole shutdown, so a SIGKILL means the lifespan
did not finish.

## Decision Drivers

* No runner outlives the app on any OS (TASK-112.09, criterion 3), so a next start never finds the card taken (ADR-001, ADR-013).
* A stop nobody asked for is not a requeue (ADR-028's Must Not).

## Considered Options

* **A.** The app ends the runner's tree on every shutdown it lives through; only the stop route requeues.
* **B.** Every shutdown the app lives through also requeues.
* **C.** The runner ends itself when its parent goes (Linux `PR_SET_PDEATHSIG`, a parent poll elsewhere).

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-10-24. It
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
* The lifespan's end stops the supervisor before the phone door, the feeds and the watcher (agent addition, approved by Robert on 2026-10-25).
* The app's server cancels open reads, a job page's event stream among them, `SHUTDOWN_GRACE_SECONDS` (3 s) after a stop begins (agent addition, approved by Robert on 2026-10-25).
* An upload in flight finishes, up to `UPLOAD_FINISH_SECONDS` (30 s) after the stop began, and is cut there: `POST /api/media`, `/transcribe/upload`, `/record/{session}/chunk` and `/finish`, and the phone door's POST (agent addition, approved by Robert on 2026-10-25).
* The phone door, closed inside the app's stop, gives its upload what is left of the app's deadline and at least its own 2 s grace.
* The launcher's `APP_SIGTERM_WAIT` (75 s) and `STOP_WAIT` (80 s) outlast the app's whole stop (62 s at worst).
* The hard stop says "marked interrupted" only where the app lived through its SIGTERM, or on Windows.

### Must Not

* Requeue on a stop that did not come through the stop route.

### Exceptions

* An app that ends without its lifespan can leave a runner on any OS: a crash, a kill of the app's process alone, or uvicorn's forced exit on a second Ctrl+C. The Windows hard stop ends the tree; the POSIX one SIGKILLs after `APP_SIGTERM_WAIT` and then says the job can keep running.

### Verification

* `tests/test_app_signal_stop.py`, `tests/test_stopping.py`, `tests/test_phone_door.py::test_an_upload_in_flight_when_the_door_closes_is_allowed_to_finish` and `::test_an_upload_still_going_at_a_passed_deadline_is_cut_after_the_grace`; `tests/test_app.py::test_the_supervisor_stops_before_the_watchers`; `tests/test_supervisor.py::test_a_stop_nobody_asked_for_ends_the_runner_and_leaves_the_job_to_reconcile`.
* `tests/test_launcher.py::test_the_hard_stops_sentence_says_what_this_os_does_with_the_running_job`, `::test_the_hard_stop_says_whether_it_had_to_kill_the_app`, `::test_the_posix_hard_stop_waits_out_the_apps_whole_shutdown_before_it_kills` and `::test_the_hard_stop_leaves_no_runner_and_says_the_job_is_interrupted`.

## Consequences

### Positive

* A service manager's stop, a logout or a Ctrl+C no longer leaves a runner holding the card, and an open job page no longer holds a stop for the rest of the job.

### Negative

* A job ended this way is `interrupted` and waits for a Retry; a read still open 3 s into a stop is cut off, and an upload still sending 30 s into it too.
* A stop with an upload in flight takes up to 30 s longer, and the hard stop waits 75 s instead of 50 s before it SIGKILLs an app that hangs.
* The `--stop` hard stop that ends a launcher cannot see whether that launcher had to SIGKILL the app, so on macOS and Linux it gives the cautious sentence.

## Open Questions

- [x] Which open requests does a stop cut off after its 3 s grace? — **Answered 2026-10-25 by Robert van den Breemen (chose "exempt uploads" in session 2026-10-25; recorded by Claude):** reads such as a job page's event stream are cut after the 3 s grace; an upload in flight finishes, up to UPLOAD_FINISH_SECONDS (30 s) after the stop began.
- [x] What does the app do with the runner on a shutdown that did not come through the stop route? — **Answered 2026-10-24 by Robert van den Breemen (chose it in session 2026-10-24; recorded by Claude):** the app ends the runner's tree on any shutdown, without requeueing.

## Related Decisions

* ADR-028: amended - its Negative "an orphaned runner that runs on elsewhere" no longer holds; its Must Not on requeueing is unchanged.
* ADR-001: unchanged - `reconcile()` still turns the left row into `interrupted`.

## References

* Backlog TASK-112.09, implementation note of 2026-10-24.
* `scribe/supervisor.py` (`Supervisor.stop`), `scribe/app.py` (lifespan), `scribe/__main__.py`, `scribe/web/stopping.py`, `scribe/web/phone_door.py`, `packaging/launcher/myscribe_launcher.py` (`AppProcess.stop`, `end_tree`, `ended`).
