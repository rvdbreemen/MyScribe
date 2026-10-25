---
id: TASK-112.07
title: 'One MyScribe per user: a lock file, and a busy port that somebody sees'
status: Done
assignee: []
created_date: 2026-10-19 05:51
updated_date: '2026-10-25T11:20:00Z'
labels:
  - packaging
  - autostart
dependencies: []
references:
  - docs/reports/2026-10-19-background-server-research.md
parent_task_id: TASK-112
ordinal: 220000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Launch.prepare probes /health and then binds the port (myscribe_launcher.py:1536-1553), which leaves a race between a login item and a start by hand. A busy port in a headless start is only a log line nobody reads.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An exclusive lock in the home is taken before the health probe; a second start that finds it opens the browser and exits 0
- [x] #2 A test starts two launchers at once and only one serves
- [x] #3 A busy port in a headless start is written to launcher.log and shown at the next start by hand
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): lock and login note beside the home, as built; the uninstaller also removes both beside the default home. A windowed second start during an install shows 'already starting' without opening the browser, as built.

2026-10-25 PR #93 merged, CI green on three OSes after the two timing-dependent tests were made deterministic (cause: a Tk StringVar finaliser on a worker thread stalled 3.3 s; fixed in the tests, production unchanged; 20/20 loops on Windows and WSL Tk 9.0). AC1 as Robert accepted it (2026-10-24): the lock is beside the home (<home>.lock), not in it; a headless second start waits and opens the browser once MyScribe serves, a windowed one shows 'already starting' without opening the browser. Evidence: two real launchers on 4330 -> one start, the other 'already starting' exit 0; test_two_launchers_started_at_once_start_one_app (2 starts on main, 1 now); a held port at --at-login -> launcher.log line and the note, shown at the next start by hand and cleared. Uninstaller removes both files beside the default home (text test + ISCC compile only; a real uninstall not run).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
One MyScribe per user: a non-blocking lock beside the home before any probe or sync, a second start that waits or says so, and a login start that fails leaves a note the next start shows. PR #93. Not run: a real uninstall, macOS flock.
<!-- SECTION:FINAL_SUMMARY:END -->
