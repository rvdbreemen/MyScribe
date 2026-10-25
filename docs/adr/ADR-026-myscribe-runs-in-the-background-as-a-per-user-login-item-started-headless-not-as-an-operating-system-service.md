---
id: "ADR-026"
title: "MyScribe runs in the background as a per-user login item started headless, not as an operating-system service"
status: "Accepted"
date: "2026-10-19"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-038"
topics:
  - "autostart"
  - "packaging"
  - "background"
aliases:
  - "start at login"
  - "login item"
  - "background server"
  - "headless start"
  - "run as a service"
components:
  - "scribe.autostart"
  - "packaging/launcher/myscribe_launcher.py"
symbols:
  - "start_command"
  - "run_headless"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-026 MyScribe runs in the background as a per-user login item started headless, not as an operating-system service

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
    reason: Robert chose option A with a systemd user unit on Linux, 2026-10-19
    changed_via: adr-kit lifecycle
  - date: 2026-10-25
    status: Accepted
    changed_by: Claude (agent, session 2026-10-25)
    reason: the tray shows in the headless login door ADR-026 starts (TASK-112.11)
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Robert asked on 2026-10-19 that MyScribe on a laptop or MacBook start by
itself when the machine starts, as a server, instead of a window from the
installer that has to stay open.

What exists, read on `main` at 3b573b0:

* A per-user login item, switched in Settings (TASK-089.21) and offered by the
  installer with default No (TASK-089.22). Windows: the value `MyScribe` under
  HKCU (HKEY_CURRENT_USER) `Software\Microsoft\Windows\CurrentVersion\Run`
  (`scribe/autostart.py:50`). macOS: a plist in `~/Library/LaunchAgents`
  (`:147`). Linux: `~/.config/autostart/myscribe.desktop` (`:55`).
* It starts `<launcher> --at-login --no-browser`, on macOS through
  `/usr/bin/open -a <bundle> --args ...` (`scribe/autostart.py:308-318`).
* `--at-login` only skips the first-run sitting. The launcher still opens its
  Tk window, because `windowed = not quiet and not args.headless and
  tkinter_present()` (`packaging/launcher/myscribe_launcher.py:2957`). That
  window is the one Robert wants gone.
* `--headless` already gives a start with no window and no console: the app
  child runs with `CREATE_NO_WINDOW` (`:62`) and logs to `<home>/logs/app.log`
  (`:1261`).
* `run_headless` returns 0 also when the app child dies
  (`:2200-2221`), so a supervising OS mechanism cannot tell a crash from a
  clean stop.
* The installer runs without administrator rights
  (`PrivilegesRequired=lowest`, `packaging/windows/myscribe.iss:22`, ADR-011),
  and the library, `.env` and the pointer file (ADR-019) live in the user's
  profile.
* No real login has started the item on any OS yet: TASK-089.21 criterion 6
  is open.

The question is which mechanism runs MyScribe in the background.

Not decided here, each with its own task: how the server is stopped (a stop
route and `--stop`), whether it restarts after a crash, and whether a tray
icon shows it. They depend on this decision, not the other way round.

## Decision Drivers

* No window to keep open, and nothing to do after logging in.
* No administrator rights, the same as the install (ADR-011).
* The server opens the same library a start by hand opens (ADR-019).
* The GPU (graphics processing unit) stays usable to the runner (ADR-001).
* What is registered is visible and removable by the user, per OS.

## Considered Options

* **A.** Keep the per-user login item and start the launcher headless:
  `--headless --at-login --no-browser`. Windows keeps the Run value; macOS's
  LaunchAgent starts the binary inside the bundle instead of `open -a`;
  Linux gets a systemd user unit, with the XDG (X Desktop Group) autostart
  file as the fallback where no systemd user session runs.
* **B.** An operating-system service: a Windows service (WinSW, Servy or
  pywin32), a macOS LaunchDaemon, a Linux system unit.
* **C.** A tray application that owns the server, the way Ollama does
  (Startup shortcut on Windows, `SMAppService` agent on macOS).
* **D.** Do nothing: the login item keeps opening the window.

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-10-19, with a systemd user
unit on Linux and the XDG autostart file as fallback. A is the only option
that needs no administrator rights and no new dependency, starts in the
user's own session with the user's own library, and builds on the mechanism
TASK-089.21 already ships and tests.

### Confirmation

On Windows Robert switches start-at-login on, logs out and in: no window
appears and `curl -s http://127.0.0.1:4242/health` answers. Off, out and in:
nothing answers. The same walk on a Mac and a Linux desktop, when one is
available (TASK-089.21 criterion 6).

## Decision Contract

For the recommended option.

### Must

* Every login item starts the launcher with `--headless`, so no window and no
  console appear.
* Register per user only: no administrator rights, no machine-wide key, folder
  or unit.
* A login item written by an earlier version is rewritten to the current
  command at the next start.
* On Linux, an `XDG_DATA_HOME` the user has set is written into the unit, so
  the background server opens the same library as a start by hand.

### Must Not

* Install or require an operating-system service, daemon or system unit.
* Point a login item at the environment's python instead of the launcher, so
  that a start after an update still re-syncs (TASK-089.21 criterion 4).
* Register a macOS path under `AppTranslocation`, which is gone at the next
  login.

### Exceptions

* None.

### Verification

* `scribe/autostart.py` tests assert the registered command carries
  `--headless` on each OS.
* The login walk under Confirmation.

## Consequences

### Positive

* The window disappears with a one-flag change to the existing command.
* No admin prompt, no new dependency, no second profile to keep in sync.
* Each OS shows the item where its users look: Task Manager's Startup apps,
  Login Items, `systemctl --user status myscribe`.

### Negative

* There is no visible sign that MyScribe runs, and no way to stop it but the
  OS, until the stop task lands. Mitigation: that task is the first follow-up.
* A crash is not restarted on Windows, where a Run value has no supervisor.
  Accepted: the GPU work runs in runner children (ADR-001), so the long-lived
  process is the web server.
* An uninstall or update must stop a running server first; today neither
  does. Mitigation: its own task.
* macOS and Linux have no uninstaller, so a deleted app can leave a login item
  that fails at each login. Mitigation: `docs/installation.md` says to switch
  it off first.

## Pros and Cons of the Options

### A

* Good, because it extends a shipped, tested mechanism with no new rights.
* Bad, because nothing restarts a crashed server on Windows.

### B

* Good, because the OS supervises and restarts it, before anyone logs in.
* Bad, because it needs administrator rights, runs under another account with
  another profile, and a Windows service runs in session 0, where CUDA on a
  consumer card is in doubt: NVIDIA forum posts say it cannot, Ollama's
  documentation offers a service anyway. Not settled, and not needed to
  reject B.

### C

* Good, because the user sees that it runs and can stop it from the tray.
* Bad, because pystray, the obvious library, last released 0.19.5 on
  2023-09-17 and is LGPL-3.0 (GNU Lesser General Public License); in the
  launcher it breaks ADR-011's standard-library-only rule.

### D

* Good, because nothing changes.
* Bad, because the window Robert wants gone stays.

## Open Questions

- [x] Which option: A, B, C or D? — **Answered 2026-10-19 by User: Robert van den Breemen:** A: a per-user login item started headless.
- [x] On Linux, a systemd user unit with the XDG autostart file as fallback, or the XDG file alone as today? — **Answered 2026-10-19 by User: Robert van den Breemen:** A systemd user unit where a systemd user session runs, with the XDG autostart file as fallback.

## Related Decisions

* ADR-001 (one web process, a runner per job): why the long-lived process holds no GPU context.
* ADR-011 (a launcher that installs with uv, no admin): the rights this must stay within.
* ADR-019 (the pointer file): the library the background server must open.

## References

* `docs/reports/2026-10-19-background-server-research.md`: the research of 2026-10-19, in Dutch, with sources and a configuration per OS.
* Backlog TASK-089.21 (the login item), TASK-089.22 (the installer question).
* `scribe/autostart.py:50-318`, `packaging/launcher/myscribe_launcher.py:2200-2221, 2957`, `packaging/windows/myscribe.iss:22, 53`.
* Microsoft, Run and RunOnce registry keys: <https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys>
* Microsoft, interactive services and session 0: <https://learn.microsoft.com/en-us/windows/win32/services/interactive-services>
* launchd.plist(5): <https://keith.github.io/xcode-man-pages/launchd.plist.5.html>
* Apple, SMAppService: <https://developer.apple.com/documentation/servicemanagement/smappservice>
* systemd, loginctl and user units: <https://www.freedesktop.org/software/systemd/man/latest/loginctl.html>

Enforcement: none machine-checkable; the `scribe/autostart.py` tests of TASK-112.01 assert the `--headless` rule.
