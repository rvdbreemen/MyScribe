---
id: "ADR-028"
title: "A deliberate stop puts the running job back at the front of the queue"
status: "Accepted"
date: "2026-10-21"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-032"
topics:
  - "jobs"
  - "shutdown"
  - "background"
aliases:
  - "stop MyScribe"
  - "stop route"
  - "requeue on stop"
  - "shutdown route"
components:
  - "scribe.supervisor"
  - "scribe.jobs"
symbols:
  - "Supervisor.stop"
  - "requeue_running"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-028 A deliberate stop puts the running job back at the front of the queue

## Status

Accepted, 2026-10-21.

## Status History

```yaml
status_history:
  - date: 2026-10-21
    status: Proposed
    changed_by: Claude (agent, session 2026-10-21)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-21
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Robert accepted the text of PR #27 in an interactive session on 2026-10-21"
    changed_via: adr-kit lifecycle
  - date: 2026-10-24
    status: Accepted
    changed_by: Claude (agent, session 2026-10-24)
    reason: "ADR-032 amends ADR-028: every shutdown ends the runner, only the route requeues"
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-026 runs MyScribe headless at login, so there is no window to close.
TASK-112.03 adds a stop: a loopback-only route, `MyScribe --stop` and a Stop
button. Something must happen to a job that is running when it lands.

What happens today, read on `main` at 0331d47:

* `Supervisor.stop` sets its stop event and leaves a running runner child
  alone (`scribe/supervisor.py:453`, `:609`).
* On Windows the launcher then ends the app's whole tree with taskkill
  (`packaging/launcher/myscribe_launcher.py`, `AppProcess.stop`). The runner
  dies without a verdict, its row stays `running`, and the next start's
  `reconcile()` makes it `interrupted` (`scribe/supervisor.py:309`). The jobs
  board then says "Retry starts it over"; nothing runs it by itself, so a
  recording a watch folder took in waits until somebody sees it.
* On macOS and Linux the launcher signals only the app's process group, and
  the runner has a session of its own (`Supervisor._spawn`). Measured in WSL
  on 2026-10-21: after SIGTERM to the app's group the app was gone and the
  runner ran on, an orphan, and wrote `done` 13 s later. A real transcription
  would hold the GPU (graphics processing unit) with no app to show it, and
  the next start would wait for it.

## Decision Drivers

* A stop is something the user asked for, not a failure, and must not cost
  them a recording.
* A stop must stop now: an installer or uninstaller (TASK-112.04) cannot wait
  an hour for a transcription.
* At most one runner at a time (ADR-001, ADR-013): a job may only go back to
  the queue once its runner is gone.

## Considered Options

* **A.** Stop now; the running job goes back to the front of the queue and
  starts over at the next start.
* **B.** Stop now; the job becomes `interrupted`, as today, and waits for a
  Retry.
* **C.** Wait until the running job is done, then stop.
* **D.** Two actions: "stop after this job" and "stop now".

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-10-21. It is the only option
that both stops at once and loses no job: the work already done is redone,
and nobody has to notice an `interrupted` row and press Retry.

### Confirmation

A test stops the app through the route while a fake runner runs: the runner
is gone, and the row is `queued` ahead of the other queued jobs, with its
priority kept.

## Decision Contract

### Must

* The stop route makes the supervisor end the runner's process tree, wait for
  it, and only then put the row back to `queued` at the front of its
  priority, with `started_at`, `pid`, `stage` and `stage_progress` cleared.
* A row whose cancel was requested before the stop becomes `cancelled`, not
  `queued`.
* The UI's Stop action says that the running job starts over at the next
  start.

### Must Not

* Requeue a row while its runner can still write to it.
* Requeue after a crash, a hard kill or a reboot: those still go through
  `reconcile()` to `interrupted` (ADR-001).

### Exceptions

* The hard-stop fallback of `--stop`, used when the route does not finish in
  time, ends the process tree: that job becomes `interrupted`, and `--stop`
  says so.

### Verification

* `tests/test_supervisor.py` and `tests/test_app.py` tests of TASK-112.03.

## Consequences

### Positive

* A stop at night loses no recording: it runs again at the next start.
* The same job id carries on, so an LLM (large language model) job keeps the
  chunks it paid for (`scribe/stages/llm_stage.py:36`).

### Negative

* The work done before the stop is redone: up to a whole transcription.
* Only the stop route requeues. Window Quit, Ctrl+C and a SIGTERM still
  behave as today: `interrupted` on Windows, an orphaned runner that runs on
  elsewhere. Mitigation: TASK-112.09 sends them through the stop route too.

## Pros and Cons of the Options

### A

* Good, because nothing is lost and nothing waits for the user.
* Bad, because the work in progress is redone.

### B

* Good, because nothing runs again unasked.
* Bad, because a recording from a watch folder waits for somebody to notice.

### C

* Good, because no work is redone.
* Bad, because a stop can take an hour, and an installer cannot wait for it.

### D

* Good, because the user picks per stop.
* Bad, because it is two actions to explain and test where one serves.

## Open Questions

- [x] What does a stop do to a running job? — **Answered 2026-10-21 by User: Robert van den Breemen:** it goes back to the queue (option A).
- [x] Should window Quit, Ctrl+C and a SIGTERM to the launcher go through the stop route too, so they requeue on every OS and no runner outlives the app on macOS and Linux? — **Answered 2026-10-21 by User: Robert van den Breemen:** Yes: they go through the stop route too, as follow-up TASK-112.09, so every stop requeues on every OS and no runner outlives the app on macOS and Linux.

## Related Decisions

* ADR-001 (one web process, a runner per job): `reconcile()` keeps handling every stop nobody asked for.
* ADR-013 (SQLite is the only coordination): the requeue is one guarded UPDATE, after the runner is gone.
* ADR-026 (background login item): why a stop needs a door of its own.

## References

* Backlog TASK-112.03; `docs/reports/2026-10-19-background-server-research.md`, section 3.
* `scribe/supervisor.py:270` (`_kill_tree`), `:309` (`reconcile`), `:453` (`stop`), `:609` (`_watch`).
* `scribe/jobs.py:28` (`_FRONT_SEQ`), `scribe/templates/_job_panel.html:59` (the "Retry starts it over" banner).
