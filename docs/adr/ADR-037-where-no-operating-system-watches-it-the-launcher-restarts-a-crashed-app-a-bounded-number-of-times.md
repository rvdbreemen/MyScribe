---
id: "ADR-037"
title: "Where no operating system watches it, the launcher restarts a crashed app a bounded number of times"
status: "Proposed"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-026"
  - "ADR-032"
topics:
  - "background"
  - "restart"
aliases:
  - "restart after a crash"
components:
  - "packaging.launcher.myscribe_launcher"
symbols:
  - "os_supervisor"
  - "RestartBudget"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-037 Where no operating system watches it, the launcher restarts a crashed app a bounded number of times

## Status

Proposed, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: "TASK-112.10: the launcher-side restart"
    changed_via: adr-kit lifecycle
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: "TASK-112.10: the launcher-side restart"
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-026 accepts that a crash is not restarted on Windows: a Run value has no
supervisor. The systemd user unit restarts exit 70 (five starts in five
minutes, TASK-112.06); the LaunchAgent restarts every non-zero exit
(TASK-112.05). Exit 70 is the launcher's `APP_DIED`, the app died under it; a
stop and a configuration problem exit 0 (TASK-112.02, ADR-032). On 2026-10-24
Robert chose for TASK-112.08 that the launcher restarts its app itself, a
bounded number of times. Where must it, so that one crash is one restart?

## Decision Drivers

* A crash comes back on Windows without a scheduled task (ADR-026).
* One crash, one restart; never after a stop or a configuration problem.

## Considered Options

* **A.** Restart only when no MyScribe OS entry started the launcher, known
  by name: `XPC_SERVICE_NAME` equal to the label, or a cgroup named
  `myscribe.service`. Under either, exit 70 at once, as today.
* **B.** As A, known by `INVOCATION_ID` or `XPC_SERVICE_NAME` being set.
* **C.** Restart everywhere; the unit and the LaunchAgent stop restarting.

## Decision Outcome

Chosen option: **A**. It restarts on Windows and under the XDG autostart
file, where nothing else does, and keeps both OS supervisors as built. B can
switch the restart off for a start by hand: a macOS Terminal shell is said to
carry `XPC_SERVICE_NAME` (`0`), and a shell under a terminal server running
as a systemd user service can inherit `INVOCATION_ID` (neither checked here).
C loses the OS restart of a launcher that crashes itself and undoes
TASK-112.05 and TASK-112.06.

The limit is 3 restarts in any 10 minutes, each after 5 s (the unit's
`RestartSec=5`); a restart that never answers `/health` is spent like a
crash. Past it the launcher says one sentence, keeps it for the next start by
hand after a login start (TASK-112.07), and exits 70. A running job is not
requeued, only the stop route does that (ADR-028, ADR-032): the restarted
app's reconcile marks it interrupted if its runner is gone, and a runner that
outlived the crash writes its own verdict (ADR-013). The sentence says both.

### Confirmation

On Windows, a launcher from source with a scratch home on port 4530: kill
the app's python, `/health` answers with a new pid; after the fourth kill in
ten minutes the sentence is in `launcher.log` and nothing answers.

## Decision Contract

### Must

* Restart only after a non-zero exit of the app, in the headless door.
* Exit 70 at once under MyScribe's own unit or LaunchAgent (`os_supervisor`).
* Count every restart against `RestartBudget`, also one that never answers.
* Stop through `Launch.stop` on Ctrl+C or SIGTERM, also during a restart.

### Must Not

* Restart after an exit of 0, Ctrl+C, SIGTERM, Quit or `--stop`.
* Tell a supervisor by `INVOCATION_ID` or by `XPC_SERVICE_NAME` alone.
* Re-run the sync or the first-run sitting for a restart.

### Exceptions

* The window door does not restart: its pump lets go of the instance lock
  when the app ends, a restart would race the next start for it, and a
  person sits at that window. Its own task if wanted.

### Verification

* `tests/test_launcher.py`, the TASK-112.10 block: supervisor names, the
  budget, restart, give-up, no restart after 0, Ctrl+C, SIGTERM, supervised.
* `tests/test_autostart.py::test_the_launcher_knows_the_unit_and_the_job_by_the_names_written_here`.

## Consequences

### Positive

* A crash comes back on Windows, no admin rights; Linux, macOS unchanged.

### Negative

* A launcher that crashes itself is still not restarted on Windows.
* The launchd half rests on launchd putting the label in `XPC_SERVICE_NAME`,
  unseen on a Mac here. If not, each launcher launchd starts restarts its app
  up to 3 times first: more starts, no new failure.

## Open Questions

* [ ] Is 3 restarts in 10 minutes the budget Robert wants?

## Related Decisions

* ADR-026: answers its Negative "a crash is not restarted on Windows".
* ADR-032, ADR-028: deliberate stops and requeues. ADR-013: reconcile.

## References

* `packaging/launcher/myscribe_launcher.py`: `run_headless`, `os_supervisor`.
* `scribe/autostart.py`: `SystemdUserUnit.text`, `LaunchAgent.plist`.
* systemd.exec(5) on `INVOCATION_ID`; launchd.plist(5) on `KeepAlive`.
