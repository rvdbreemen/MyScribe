---
id: "ADR-037"
title: "Where no operating system watches it, the launcher restarts a crashed app a bounded number of times"
status: "Accepted"
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

Accepted, 2026-10-25.

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
    status: Accepted
    changed_by: Robert van den Breemen (said yes in session 2026-10-25; recorded by Claude)
    reason: Robert accepted ADR-037 in the session of 2026-10-25
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
C drops the OS restart of a crashed launcher and undoes TASK-112.05/06.

The limit is 5 restarts in any 5 minutes, each after 5 s: the unit's
`StartLimitBurst=5`, `StartLimitIntervalSec=300` and `RestartSec=5`, Robert's
choice of 2026-10-25 over the 3 in 10 minutes first proposed. A restart
that never answers `/health` is spent like a crash. Past it the launcher says one sentence, keeps it for the next start by
hand after a login start (TASK-112.07), and exits 70. A running job is not
requeued, only the stop route does that (ADR-028, ADR-032): the restarted
app's reconcile marks it interrupted if its runner is gone, and a runner that
outlived the crash writes its own verdict (ADR-013). The sentence says both.

### Confirmation

Windows, launcher from source, scratch home, port 4530: a killed app answers
`/health` again with a new pid; the kill after the last restart in the
budget ends in the sentence and exit 70.

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

* The window door: its pump frees the instance lock when the app ends, so a
  restart would race the next start; a person sits there. Its own task.

### Verification

* The TASK-112.10 block of `tests/test_launcher.py`, and `tests/test_autostart.py::test_the_launcher_knows_the_unit_and_the_job_by_the_names_written_here`
  and `::test_the_launchers_restart_budget_is_the_units_start_limit`.

## Consequences

### Positive

* A crash comes back on Windows, no admin rights; Linux, macOS unchanged.

### Negative

* A launcher that crashes itself is still not restarted on Windows.
* launchd putting the label in `XPC_SERVICE_NAME` is unseen on a Mac here; if
  it does not, each launcher launchd starts first restarts its app 5 times.

## Open Questions

- [x] Is 3 restarts in 10 minutes, 5 s apart, the budget Robert wants? — **Answered 2026-10-25 by Robert van den Breemen (said yes in session 2026-10-25; recorded by Claude):** No: Robert chose 5 restarts in 5 minutes, 5 s apart, the systemd unit's own budget, in the session of 2026-10-25.

## Related Decisions

* ADR-026 (its Negative on Windows crashes); ADR-032, ADR-028, ADR-013.

## References

* `packaging/launcher/myscribe_launcher.py` (`run_headless`, `os_supervisor`); `scribe/autostart.py`.
* systemd.exec(5) on `INVOCATION_ID`; launchd.plist(5) on `KeepAlive`.
