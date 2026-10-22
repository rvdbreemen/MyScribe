---
id: TASK-112.01
title: A login item starts MyScribe headless, and an older login item is rewritten
status: In Progress
assignee:
  - '@claude'
created_date: 2026-10-19 05:51
updated_date: '2026-10-22T00:29:00Z'
labels:
  - packaging
  - autostart
dependencies: []
references:
  - >-
    docs/adr/ADR-026-myscribe-runs-in-the-background-as-a-per-user-login-item-started-headless-not-as-an-operating-system-service.md
  - docs/reports/2026-10-19-background-server-research.md
parent_task_id: TASK-112
ordinal: 214000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Today `start_command` (scribe/autostart.py:308-318) registers `--at-login --no-browser`, and the launcher still opens its Tk window (myscribe_launcher.py:2957). With `--headless` added no window and no console appear. Items written by earlier versions carry the old command and must be brought up to date. Waits for ADR-026.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The command registered on Windows, macOS and Linux carries `--headless`, and a test per OS asserts it
- [x] #2 At start, a login item whose command differs from the current one is rewritten; one the user switched off stays off
- [x] #3 Settings says that MyScribe then runs without a window and how to open it (the Start menu entry or localhost:4242)
- [ ] #4 Robert logs out and in on Windows: no window, /health answers; switched off, out and in: nothing answers
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. start_command: the three launcher returns (Windows, Linux, macOS open -a ... --args) gain --headless. Clone doors unchanged: start.ps1/start.sh forward their args to python -m scribe, not to a launcher, and already start detached and hidden.
2. autostart.refresh(mechanism, command, environ): rewrite a registered entry to the current command. Guards, each tested: off stays off (read() is None, incl. XDG Hidden=true); only when MYSCRIBE_LAUNCHER is set; only when the registered command already names that same launcher path (never retarget, so a clone or second install cannot take the entry); compare rendered strings, write only on a difference.
3. Lifespan calls refresh when the supervisor runs; OSError/ValueError logged via applog, never fatal; a rewrite is logged with old and new command.
4. Settings card: one sentence that MyScribe started at login runs without a window, and how to open it, worded per OS.
5. Red first: update expectations in test_autostart, test_setup_plan, test_web_settings; new refresh tests; card test. Then green.
6. AC #4 is a real logout/login walk by Robert on a build with this change; written into the notes, not simulated against his home or port 4242.
Checked: launcher children (run_streaming, AppProcess) already get CREATE_NO_WINDOW, so the headless door flashes no console.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence (2026-10-20):
- Red first: test_autostart.py 8 failed, 19 passed (release commands without --headless; refresh() did not exist). Settings card test red without the template line ("without a window" not in card), green with. Windows case test red without casefold, green with.
- Green: test_autostart.py 33 passed (incl. the rewrite against a scratch HKCU key, a plist and a desktop file, each keeping its neighbour); test_setup_plan.py 183; test_web_settings.py 57; test_app.py 17; test_applog.py 14.
- Guards in refresh(): off stays off (missing, or XDG Hidden=true); only with MYSCRIBE_LAUNCHER set (a clone cannot point the release entry at start.ps1); only an entry naming this same launcher, case-insensitive on Windows; written only when the rendered command differs. Lifespan calls it only with the supervisor on; OSError/ValueError is a warn log line, a rewrite an info line with the new command.
- Roberts real Run value today (read only, not touched): C:\Users\rvdbr\AppData\Local\Programs\MyScribe\MyScribe.exe --at-login --no-browser. The first start of a build with this change rewrites it.
- Checked by reading: the launcher children (run_streaming, AppProcess) already get CREATE_NO_WINDOW, and the Windows build is --windowed, so --headless shows no console.
- Consequence to know: after an update the re-sync at login now runs with no window; its progress is only in launcher.log.
- AC #4 open: Roberts walk on an installed build with this change. 1) Start MyScribe once by hand, check the Run value now ends in --headless. 2) Log out and in: no window, Get-Process MyScribe shows it, http://localhost:4242/health answers. 3) Switch it off in Settings, log out and in: nothing answers on 4242.

2026-10-22: 0.9.0 is the release to walk on for AC #4 (log out and in on Windows: no window, /health answers; switched off: nothing answers). The release PR is release-0.9.0; status and AC left as they are until Robert's walk.
<!-- SECTION:NOTES:END -->
