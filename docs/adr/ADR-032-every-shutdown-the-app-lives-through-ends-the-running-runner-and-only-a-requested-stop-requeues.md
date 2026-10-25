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
  - "scribe.shutdown"
symbols:
  - "Supervisor.stop"
  - "STOP_JOIN_SECONDS"
  - "SHUTDOWN_GRACE_SECONDS"
  - "UPLOAD_FINISH_SECONDS"
  - "StoppingServer"
  - "Supervisor.stop_claiming"
  - "INGEST_FINISH_SECONDS"
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
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: "Review: only the 3 s grace and exempt uploads were Robert's; the 30 s bound, the lifespan order, the door's own cut and the 75/80 s waits are agent additions awaiting approval; worst stop 72 s, not 62 s; /transcribe/path is an upload"
    changed_via: adr-kit lifecycle
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: "Robert 2026-10-25: 30 s and 75/80 s kept; no claim once a stop begins; one threaded store-and-enqueue call, database closed only when none runs; every door Close lets an upload finish"
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-028 requeues only on the stop route, which TASK-112.09 gives Quit, Ctrl+C and a SIGTERM to the launcher. Other shutdowns skip it: a SIGTERM straight to the app, Ctrl+C or Ctrl+Break in a terminal running `python -m scribe`, and the launcher's hard stop when the route does not answer in time. On those, `Supervisor.stop` left the runner alone. On macOS and Linux it leads a session of its own and ran on as an orphan holding the GPU (graphics processing unit): in WSL (Windows Subsystem for Linux) it wrote `done` 13 s after the app had gone (2026-10-21). On Windows, Ctrl+Break to the app left it running too (`tests/test_app_signal_stop.py`, red on 2026-10-24). An open job page's event stream held uvicorn's graceful shutdown until its job ended, and a single uvicorn timeout that cut it also cut an upload on its way in. A cut upload's worker thread runs on after its request is cancelled, and uvicorn runs the lifespan's end at once: the database closed under it, and a job queued after the `await` never was (red in `tests/test_stop_cuts_ingest.py`, 2026-10-25). The launcher's hard stop SIGKILLs the app's group `APP_SIGTERM_WAIT` after SIGTERM, which never reaches the runner's session.

## Decision Drivers

* No runner outlives the app on any OS (TASK-112.09, criterion 3), so a next start never finds the card taken (ADR-001, ADR-013), and a stop nobody asked for is not a requeue (ADR-028's Must Not).

## Considered Options

* **A.** The app ends the runner's tree on every shutdown it lives through; only the stop route requeues.
* **B.** Every shutdown the app lives through also requeues.
* **C.** The runner ends itself when its parent goes (Linux `PR_SET_PDEATHSIG`, a parent poll elsewhere).

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-10-24. It reuses ADR-028's kill path and changes nothing about which stops requeue. B would requeue after a logout or a service manager's stop, which ADR-028 rules out. C is per-OS code in every runner, and macOS has no parent-death signal.

### Confirmation

A real `python -m scribe` with the slow fake job gets SIGTERM (POSIX) or Ctrl+Break (Windows), with and without its job page open: the runner goes with the app, and the row stays `running` until reconcile. On Windows an upload in flight at a stop lands with its job, and no job starts after the stop began.

## Decision Contract

### Must

* `Supervisor.stop` ends the running runner's tree and waits for it on every stop, and joins for at least `STOP_JOIN_SECONDS`; a stop without the route leaves the row `running` for the next start's `reconcile()`. The lifespan's end stops the supervisor before the phone door, the feeds and the watcher (agent addition).
* The supervisor claims no new job once a stop begins: the stop route and the server's shutdown call `Supervisor.stop_claiming`, under the lock the claim takes (Robert, 2026-10-25).
* The app's server cancels open reads `SHUTDOWN_GRACE_SECONDS` (3 s) after a stop begins; an upload in flight (`POST /api/media`, `/transcribe/upload`, `/transcribe/path`, `/record/{session}/chunk` and `/finish`, the phone door's POST) finishes, cut at `UPLOAD_FINISH_SECONDS` (30 s) after the stop began (Robert, 2026-10-25).
* Each way a file comes in stores it and queues its job in one worker-thread call, counted in `Ingests`; the lifespan closes the database only once none runs, waiting up to `INGEST_FINISH_SECONDS` (20 s, agent addition) from the start of its end, and leaves it open after that (Robert asked for the one call, 2026-10-25).
* Every close of the phone door (its button, its fifteen minutes, a file, the app's stop) lets an upload in flight finish for `UPLOAD_FINISH_SECONDS`; inside the app's stop it gets what is left of the app's deadline and at least the door's 2 s grace. The panel says *Closing* meanwhile and offers no Open (Robert, 2026-10-25).
* The launcher's `APP_SIGTERM_WAIT` (75 s) and `STOP_WAIT` (80 s) outlast the app's whole stop: 72 s at worst (30 s of uploads, then 20 s, 2 s, 10 s and 10 s of joins, beside which the ingest wait runs), a 3 s POSIX margin (kept by Robert, 2026-10-25). The hard stop says "marked interrupted" only where the app lived through its SIGTERM, or on Windows.

### Must Not

* Requeue on a stop that did not come through the stop route.

### Exceptions

* An app that ends without its lifespan can leave a runner on any OS: a crash, a kill of the app's process alone, or uvicorn's forced exit on a second Ctrl+C. The Windows hard stop ends the tree; the POSIX one SIGKILLs after `APP_SIGTERM_WAIT` and says the job can keep running. A SIGKILL can also cut a file still being stored. Other threaded routes that use the database (exports, previews, the feeds, a ticked feed's episodes) are not counted in `Ingests`; one still running at the close fails on the closed database (by reading, not run).

### Verification

* `tests/test_app_signal_stop.py`, `tests/test_stopping.py`, `tests/test_stop_cuts_ingest.py`, `tests/test_phone_door.py`, `tests/test_web_phone.py::test_while_a_close_waits_for_an_upload_the_panel_says_it_may_still_arrive`, `tests/test_app.py` (the stop route's tests), `tests/test_supervisor.py` (the stop's and `stop_claiming`'s tests), `tests/test_launcher.py::test_the_posix_hard_stop_waits_out_the_apps_whole_shutdown_before_it_kills` and the hard-stop sentence tests.

## Consequences

### Positive

* A service manager's stop, a logout or a Ctrl+C no longer leaves a runner holding the card; an open job page no longer holds a stop; a cut upload never leaves a recording without its job.

### Negative

* A job ended this way is `interrupted` and waits for a Retry; a read still open 3 s into a stop is cut off, and an upload still sending 30 s into it too. A stop or a Close of the door with an upload in flight takes up to 30 s longer, and the hard stop waits 75 s instead of 50 s before it SIGKILLs an app that hangs.
* The `--stop` hard stop that ends a launcher cannot see whether that launcher had to SIGKILL the app, so on macOS and Linux it gives the cautious sentence.

## Open Questions

- [x] Which open requests does a stop cut off after its 3 s grace? — **Answered 2026-10-25 by Robert van den Breemen (chose "exempt uploads" in session 2026-10-25; recorded by Claude):** reads such as a job page's event stream are cut after the 3 s grace; an upload in flight finishes. On 2026-10-25 he kept the 30 s bound and the 75/80 s waits.
- [x] What does the app do with the runner on a shutdown that did not come through the stop route? — **Answered 2026-10-24 by Robert van den Breemen (chose it in session 2026-10-24; recorded by Claude):** the app ends the runner's tree on any shutdown, without requeueing.

## Related Decisions

* ADR-028: amended - its Negative "an orphaned runner that runs on elsewhere" no longer holds; its Must Not on requeueing is unchanged. ADR-001: unchanged - `reconcile()` still turns the left row into `interrupted`.

## References

* Backlog TASK-112.09, implementation note of 2026-10-24; Robert's decisions of 2026-10-25 in the session; `scribe/supervisor.py`, `scribe/app.py` (lifespan), `scribe/shutdown.py`, `scribe/__main__.py`, `scribe/web/stopping.py`, `scribe/web/phone_door.py`, `scribe/web/phone_ui.py`, `packaging/launcher/myscribe_launcher.py`.
