---
id: TASK-112.09
title: >-
  Window Quit, Ctrl+C and SIGTERM stop MyScribe through the stop route, so every
  stop requeues and no runner outlives the app
status: Done
assignee: []
created_date: 2026-10-21 18:03
updated_date: '2026-10-25T12:05:00Z'
labels:
  - autostart
  - packaging
dependencies:
  - TASK-112.03
references:
  - >-
    docs/adr/ADR-028-a-deliberate-stop-puts-the-running-job-back-at-the-front-of-the-queue.md
parent_task_id: TASK-112
ordinal: 226000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-028 (answered by Robert on 2026-10-21): every stop goes through the stop route. Today only POST /api/shutdown, the Settings button and MyScribe --stop requeue the running job. Window Quit and a POSIX SIGTERM end the app through AppProcess.stop: on Windows taskkill ends the runner and reconcile marks its job interrupted; on macOS and Linux the runner has a session of its own and runs on as an orphan (measured in WSL on 2026-10-21: it wrote done 13 s after the app had gone), holding the GPU with no app to show it. Ctrl+C on a headless launcher takes the same path.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Window Quit and a SIGTERM to the launcher call the stop route first and wait for the app's pid, with AppProcess.stop as the fallback after a timeout
- [x] #2 A job running at that moment is queued again at the front, on Windows, macOS and Linux; a test covers each door
- [x] #3 On macOS and Linux no runner outlives the app after a Quit; shown in WSL or on a Mac with a slow fake job
- [x] #4 The window and the console say what happens to the running job before and after the stop
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): POSIX orphan runner on the hard fallback: option b, the app ends the runner tree on any shutdown (amendment to ADR-028). The macOS LaunchAgent gets an ExitTimeOut longer than the longest stop.

2026-10-25 Robert's decision (asked with options in the session): wording '...unless it finishes first' (option a); INGEST_FINISH 20 s; a phone file that lands while Close/timeout shuts the door is named and the library reloads. ADR-032 accepted.

2026-10-25 PR #99 merged; CI green on macOS, Ubuntu and Windows after a test fix (test_env patched uvicorn.Server, which ADR-032's StoppingServer subclass no longer reaches; CI found it, the builders had run only touched files). AC2 macOS: test_quit_requeues_a_running_job_and_leaves_no_runner has no skip and ran on the CI macOS runner (4225 passed there); Windows and WSL real runs in earlier notes. Real run 2026-10-25 (Windows, port 4440, fake stages): an upload in flight when /api/shutdown answered got 201 8.5 s later with its job queued, 0 claims after the stop began. ADR-032 accepted by Robert. Not run: a real Mac desktop, the frozen launcher, a real Ctrl+C keypress.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Quit, Ctrl+C and a stop signal to the launcher stop MyScribe through the stop route: a running job goes back to the front (unless it finishes first), no runner outlives the app on any OS, uploads in flight may finish, and no new job is claimed once a stop begins. ADR-032. PR #99.
<!-- SECTION:FINAL_SUMMARY:END -->
