---
id: "ADR-039"
title: "A login start that cannot serve shows one notice on Windows"
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
topics:
  - "autostart"
  - "launcher"
aliases:
  - "login box"
  - "failed login start"
components:
  - "packaging/launcher/myscribe_launcher.py"
symbols:
  - "show_login_box"
  - "LOGIN_BOX_SECONDS"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-039 A login start that cannot serve shows one notice on Windows

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
    reason: ADR-039 amends ADR-026's no-window Must for a failed login start
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-026's Must says a login start shows "no window and no console". On
2026-10-22 (TASK-115) Windows had reserved 4184-4283, so binding 4242 failed
with WSAEACCES (10013); that login start left one line in `launcher.log`. The
note beside the home (TASK-112.07) says it at the next start by hand, not
before. What does a person see when `--at-login --headless` cannot serve? The
login item and the installer's restart after an update both start that way.

## Decision Drivers

* A login start that fails says so to a person, not only to a log.
* The login is never held up: no wait without an end, no lock kept.
* No new dependency in the launcher, which uses the standard library only (ADR-011).

## Considered Options

* **C.** The note at the next start, also on the Settings card (dismissable,
  cleared after a clean start), and on Windows a notice at once.
* **Note and card only**, so ADR-026 stays untouched.
* **Do nothing**: the log line and the note at the next start.

## Decision Outcome

Chosen option: **C**, decided by Robert on 2026-10-24 and kept on 2026-10-25.
On Windows the Run value has no supervisor that shows a failure. This amends
ADR-026's no-window Must for one case: a launcher started with `--at-login
--headless` that ends without serving shows one message box.

### Confirmation

Such a start on a refused port shows the box within seconds, ends at the
timeout with exit 0 and leaves no box; the next start and Settings say it.

## Decision Contract

### Must

* Show it on Windows only, for an `--at-login` start that ends without serving.
* Show it after the instance lock is let go, so a start by hand is not held up.
* Wait at most `LOGIN_BOX_SECONDS` (600 s); the box ends with the process.
* Use user32's `MessageBoxW` through `ctypes`, on a daemon thread; skip it when
  `MYSCRIBE_NO_LOGIN_BOX` is set, as the test suite does.

### Must Not

* Show the box for a start by hand, a start that serves, or on macOS or Linux.
* Let a box that cannot be shown fail the start, or add a package for it.

### Exceptions

* None beyond the one case this ADR makes in ADR-026's no-window Must.

### Verification

* The TASK-120 box tests in `tests/test_launcher.py`.

## Consequences

### Positive

* A failed login start is seen when it happens, with the way out in the sentence.

### Negative

* On Windows a window can appear at login, which ADR-026 ruled out; only on failure.
* A box nobody closes is gone after 10 minutes; the note and the card keep it.

## Evidence

From the builders of TASK-120 on 2026-10-25, on Robert's Windows 11 machine:

* Red first: no `show_login_box`, no `LOGIN_PROBLEM_VARIABLE`. 13 of 13 mutants
  killed: a box under the lock, by hand, without a timeout (hung), off Windows.
* A PyInstaller build with a 5 s box: seen at 1.80 s, returned after 5.00 s,
  exit 0 at 6.84 s, gone 0.5 s later. A box never closed: `waited=600.02s`.
* A login start from source, port 63500 refused and nobody answering, 45 s
  box: seen at 2.9 s, exit 0 at 47.0 s, no box left.

## Open Questions

- [x] How is a failed headless start at login shown to a person? — **Answered 2026-10-24 by Robert van den Breemen (decided in session 2026-10-24; recorded by Claude):** Option C: a failure record shown at the next start and on the Settings card (dismissable, cleared after a clean start), plus an immediate notice at login on Windows.
- [x] Does the Windows notice stay, given ADR-026's no-window Must? — **Answered 2026-10-25 by Robert van den Breemen (decided in session 2026-10-25; recorded by Claude):** Yes: the Windows login notice stays, recorded in ADR-039 as an amendment of ADR-026's no-window Must for a failed login start.

## Related Decisions

* ADR-026 (a per-user login item started headless): the Must this amends.
* ADR-011 (a launcher on the standard library): why the box is `ctypes`.

## References

* Backlog TASK-120 (both decisions), TASK-115; `show_login_box` in `packaging/launcher/myscribe_launcher.py`.
* Microsoft, MessageBoxW: <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-messageboxw>
