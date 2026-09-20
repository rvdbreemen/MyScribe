---
id: TASK-089.21
title: >-
  Settings can switch 'start MyScribe at login' on and off, and says exactly
  what it registered
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - settings
  - packaging
  - ux
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 158000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
This is the counterpart that has to exist FIRST (brief: U3). scribe/setup.py:21-23 sets the rule: 'an answer given once at the start must never be the only place it can be given.' Nothing offers it today. A search of the tracked files for autostart, login item or LaunchAgents finds only the microphone recorder's own `data-record-autostart` attribute, and nothing in the launcher, the .iss or the README.

It is functional, not cosmetic. Watch folders and feed subscriptions only do work while the app runs: the Watcher and the FeedWatcher are threads the lifespan starts (scribe/app.py:268-281), and ADR-008 polls feeds on a schedule. A user who set up a watch folder in TASK-089.20 and then reboots has a folder nobody watches until they remember to start MyScribe.

For a release, what is registered is the launcher and not the bare app. Only the launcher re-syncs the environment after an update (packaging/launcher/myscribe_launcher.py:433-438); a login item that started the environment's python directly would run a stale environment after the next release.

The mechanism per OS is NOT decided here and none has been tried by anybody: a per-user Run entry or the Startup folder on Windows, a LaunchAgent on macOS, an XDG autostart entry on Linux. Nothing needs administrator rights, and nothing machine-wide is touched.

Needs a real machine: A real log-out and log-in on each OS: Robert on Windows. macOS: Robert, if the Mac of TASK-040.07 (a session on 2026-09-19) is still his to use - not confirmed; otherwise reported as not run. Linux needs a desktop login, which WSL is not, and nobody is named for it. No mechanism has been tried by anybody.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Settings shows one switch, 'Start MyScribe when I log in', with its current state read from the OS and not from a remembered row, so a login item the user removed by hand shows as off.
- [ ] #2 Switching it on says in words what was registered and where, per OS. Switching it off removes exactly that and nothing else. A test on each OS's mechanism covers on, off, and off-when-already-gone.
- [ ] #3 It registers per user only. Nothing needs administrator rights, and nothing under a machine-wide key or folder is written. A test asserts the paths it touches.
- [ ] #4 A release registers the launcher, not the environment's python, so a start at login still re-syncs after an update. A clone registers the clone's own start command, the existing scripts/start.*. That is a choice, stated so that Robert can overturn it, because R3 declines 'a start script or shortcut for a clone'. The login item is what R3's 'start at login' needs; it creates no desktop or Start-menu entry and no new script. On Windows the Startup-folder mechanism would literally be a shortcut file, while a per-user Run entry is not, and that weighs in the choice of mechanism, which is still open. If Robert reads a login item for a clone as the declined item, start-at-login is restricted to releases and the clone's switch says so. The notes say what each door registers.
- [ ] #5 A start at login never opens the first-run sitting: nobody is at the screen, and a modal dialog that holds up the watch folders is the opposite of what the entry is for. The entry carries a flag of its own (the design spec proposes `--at-login`), a sitting that is due waits for the next start somebody makes by hand, and a test shows the app starts without one. What else appears at login is decided and said: no browser tab opens (`--no-browser` exists, myscribe_launcher.py:692), and whether the launcher window shows or starts minimised is recorded in the notes.
- [ ] #6 Needs a real login: on Windows, Robert switches it on, logs out and in, and /health answers without anybody starting MyScribe; then off, log out and in, and it does not. Linux needs a desktop login, which WSL is not; nobody on the project is known to have a Linux desktop, so that box stays unticked and the parent's final summary lists it unless Robert names a machine. macOS needs a real Mac. Robert answers it if the Mac that TASK-040.07 records a session on (2026-09-19) is still his to use; that was not confirmed when these tasks were written, and no Mac was available in the design run. If it is not run, the box stays unticked and the parent's final summary lists it.
- [ ] #7 What an uninstall has to remove is written into the notes, for TASK-089.23 to act on.
<!-- AC:END -->
