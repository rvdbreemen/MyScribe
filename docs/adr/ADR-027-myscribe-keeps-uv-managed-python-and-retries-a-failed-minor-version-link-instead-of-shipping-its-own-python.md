---
id: "ADR-027"
title: "MyScribe keeps uv-managed Python and retries a failed minor version link instead of shipping its own Python"
status: "Accepted"
date: "2026-10-19"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "packaging"
  - "windows"
  - "first-run"
aliases:
  - "os error 448"
  - "minor version link"
  - "untrusted mount point"
  - "uv python junction"
  - "bundled python"
components:
  - "packaging/launcher/myscribe_launcher.py"
symbols:
  - "sync"
  - "sync_command"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-027 MyScribe keeps uv-managed Python and retries a failed minor version link instead of shipping its own Python

## Status

Accepted, 2026-10-19.

## Status History

```yaml
status_history:
  - date: 2026-10-19
    status: Proposed
    changed_by: Claude (agent, session 2026-10-19)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-19
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: Robert chose option A, 2026-10-19
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

GitHub issue #16 (2026-10-16): on Windows, a default install stops at the
first start with:

```text
error: Failed to create Python minor version link directory
  Caused by: The path cannot be traversed because it contains an untrusted mount point. (os error 448)
uv sync failed with exit code 2
```

What is known, read in uv's source at the bundled 0.12.13
(`packaging/tools.json:8`):

* After downloading a Python, uv always makes a junction
  `cpython-3.12-windows-x86_64-none` next to the patch folder
  (`crates/uv-python/src/installation.rs:368`, `managed.rs:796`). On Windows
  that is always a junction (`crates/uv-fs/src/lib.rs:263-295`). There is no
  setting to skip it.
* The junction is made only on the download path. A venv uses it only when it
  already exists, and falls back to the patch folder when it does not
  (`crates/uv-virtualenv/src/virtualenv.rs:214-222`).
* The same error is reported upstream as astral-sh/uv#19616 (open). There it
  went away by moving uv's folder from `AppData\Roaming` to `AppData\Local`.
  MyScribe's default is already under `%LOCALAPPDATA%\MyScribe`
  (`myscribe_launcher.py:75`), so that fix does not apply here.
* What causes 448 on the reporter's machine is not known. Error 448 is
  `ERROR_UNTRUSTED_MOUNT_POINT`; #19616 blames OneDrive's file filter. That is
  one report, not a confirmed cause.

A simulation on 2026-10-19, in the scratchpad with the bundled uv 0.12.13 and
the launcher's flags. It used a one-line project and a plain file planted where
the junction goes:

* The top-level error was the same (`Failed to create Python minor version
  link directory`). The cause line differed: os error 267, not 448.
* `cpython-3.12.14-windows-x86_64-none\python.exe` was complete on disk.
* A second `uv sync` exited 0, plain or with `--python <patch>\python.exe`.
  So did a second sync with an empty folder left at the junction path.

The question is how the first start should survive this failure.

## Decision Drivers

* A first start on Windows must not fail on a step the app does not need.
* Stay inside ADR-011: the launcher plus uv plus the lock, no admin rights.
* Nothing new to pin, download or keep up to date unless it is needed.

## Considered Options

* **A.** Keep uv-managed Python. When the sync fails on the minor version link
  alone, the launcher retries once with `--python` naming the installed patch
  interpreter.
* **B.** The launcher downloads python-build-standalone itself, with a pinned
  sha256 the way `tools.json` pins uv and ffmpeg. It unpacks it outside uv's
  python folder and runs `uv sync --python <that python.exe>`, so uv
  downloads nothing and makes no junction.
* **C.** Do nothing: the user starts MyScribe again, which the simulation
  says may already work.

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-10-19. It is a few lines in
`sync`, adds nothing to pin, and the simulation shows it works for every
failure on that step that leaves the interpreter in place. B stays the
fallback, if the reporter still fails after A.

### Confirmation

TASK-113.01 tests the retry with a red-then-green unit test, then re-runs the
scratch simulation through the launcher. A real os error 448 has not been
tested. It counts as verified only when the reporter of #16, or a machine that
shows 448, gets through a first start.

## Decision Contract

For the recommended option.

### Must

* Retry only when the uv output holds `Failed to create Python minor version
  link directory`, and only once.
* The retry names the installed interpreter with `--python`; it never falls
  back to a system Python.

### Must Not

* Retry any other sync failure.
* Turn on a Python from `PATH` (`--python-preference only-managed` stays).

### Exceptions

* None.

### Verification

* The TASK-113.01 tests and the simulation transcript in its notes.

## Consequences

### Positive

* A machine that hit #16 gets through the first start with no action of its
  own, if the simulation reflects its case.
* Nothing new to download, pin or update.

### Negative

* Whether the retry beats a real 448 is unknown. If 448 also blocks reading
  the patch folder, A does nothing. Mitigation: B is written down as
  TASK-113.03, and the reply on #16 asks the reporter whether a second start
  works.
* It depends on uv's error text. Mitigation: a test pins the line, and a uv
  bump that changes it turns the test red.

## Pros and Cons of the Options

### A

* Good, because it is small and stays with the mechanism ADR-011 verified.
* Bad, because it works around uv rather than avoiding the step.

### B

* Good, because uv never makes a junction at all.
* Bad, because the launcher then owns a second download: its own pin, its
  checksum, and an update rule for Python patches. That is code ADR-011 left to
  uv.

### C

* Good, because nothing changes.
* Bad, because a first start that fails, with advice to check the internet, is
  where the reporter gave up.

## Open Questions

- [x] Which option: A, B or C? — **Answered 2026-10-19 by User: Robert van den Breemen:** A: retry the sync once with --python naming the installed interpreter; B stays the fallback in TASK-113.03.

## Related Decisions

* ADR-011 (the launcher installs the locked environment with uv): the
  mechanism this keeps.
* ADR-012 (one uv lockfile): the lock the retry syncs, unchanged.

## References

* <https://github.com/rvdbreemen/MyScribe/issues/16>
* <https://github.com/astral-sh/uv/issues/19616>
* uv 0.12.13 source: `crates/uv-python/src/installation.rs:340-370`,
  `crates/uv-python/src/managed.rs:536-541, 796-818`,
  `crates/uv-fs/src/lib.rs:263-295`,
  `crates/uv-virtualenv/src/virtualenv.rs:214-222`.
* `packaging/launcher/myscribe_launcher.py:367-410, 645-659, 1567-1573`.
* Backlog TASK-113 and subtasks .01 to .03.

## More Information

Note added 2026-10-24, after acceptance. It changes no decision above.

* The cause of #16 is found (TASK-116). Inno Setup 6.7.1 turns on Windows
  RedirectionGuard for Setup, and the MyScribe that Setup starts inherits it.
  Windows then refuses uv's Python junction with os error 448. 0.9.1 fixes it
  with `RedirectionGuard=no` (`packaging/windows/myscribe.iss`, pinned by
  `tests/test_installer_redirectionguard.py`).
* Reproduced 2026-10-24 with uv 0.12.13 and the launcher's sync flags in a
  shell with RedirectionGuard enforced: a fresh Python folder gives #16's
  exact line, `Failed to create Python minor version link directory ... (os
  error 448)`. The same run without the guard exits 0.
* The retry of option A stays as a cheap guard against other failures on the
  minor version link.
* Option B, the launcher installing its own Python (TASK-113.03), is not
  planned.
