---
id: TASK-112.10
title: >-
  A crashed MyScribe server is restarted by the launcher a bounded number of
  times
status: Done
assignee: []
created_date: '2026-10-25T12:06:00Z'
updated_date: '2026-10-25T17:46:00Z'
labels:
  - packaging
  - autostart
dependencies: []
parent_task_id: TASK-112
priority: medium
ordinal: 240000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert's decision of 2026-10-24 for TASK-112.08: a crashed server is restarted by the launcher itself, a bounded number of times, instead of an OS supervisor. The Windows Run value stays (ADR-026). The launcher already tells a crash (exit 70, APP_DIED) from a deliberate stop or a config problem (exit 0); a crash of the launcher itself is not covered. On Linux the systemd unit (TASK-112.06) and on macOS the LaunchAgent (TASK-112.05) already restart on exit 70, so this must not double-restart there: the launcher restarts only where no OS supervisor watches it, or the OS mechanism is told not to. Needs its own ADR because ADR-026 assumes the OS supervises.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A Proposed ADR records the launcher-side restart and how it composes with systemd and launchd restarts
- [x] #2 After an app crash (exit 70) the launcher restarts it up to N times in a window, then gives up with a sentence; a deliberate stop or config problem is never restarted; tests cover each
- [x] #3 No double restart where systemd or launchd already restarts on exit 70
- [x] #4 A real run on Windows: kill the app process, it comes back, /health answers
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-25 Robert's decision (asked with options in the session): ADR-037 accepted with a restart budget of 5 in 5 minutes (same as the systemd unit).

2026-10-25 PR #103 merged (merge 0530109), CI green on three OSes. ADR-037 accepted with Robert's budget 5 restarts in 300 s, 5 s apart. Restarts only after exit 70; never after a deliberate stop or a config problem; not under systemd (INVOCATION_ID) or launchd, which restart themselves; not with the window open. Real run on Windows (port 4530, scratch home): kills 1-5 came back with a new pid, the first answered /health 11.8 s after the kill; after kill 6 the launcher exited 70 at 107.0 s with 'after 5 restarts in 5 minutes', and the login note says the same. A job whose app was killed alone ran on to done; after a whole-tree kill it ended interrupted (reconcile). Seam found at merge: when the loop gives up after a start at login, the TASK-120 Windows box is not shown (the next start by hand says it) - follow-up fix queued.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A crashed MyScribe app is restarted by its launcher, at most 5 times in 5 minutes, where no OS supervisor does it (ADR-037). Shown live on Windows. PR #103.
<!-- SECTION:FINAL_SUMMARY:END -->
