---
id: TASK-073
title: >-
  Supervisor.stop() drops the thread handle even when the join times out, unlike
  its sibling class
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:01'
updated_date: '2026-09-16 17:02'
labels:
  - review-2026-09-16
  - concurrency
  - supervisor
dependencies: []
priority: low
type: bug
ordinal: 118000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low - a latent robustness gap). Supervisor.stop() sets the stop event, joins with a timeout, and sets self._thread = None whether or not the join returned because the thread ended. The loop cannot check the stop event while _watch is inside a kill - terminate, then up to two waits of _KILL_WAIT_SECONDS - so a shutdown that lands mid-cancel returns from stop() with the loop still running, and nothing remembers it: start() would begin a second loop in the same process beside the first, and the app's lifespan closes the database next. watching.Watcher.stop() (scribe/ingest/watching.py:748) already handles the same case - a join that runs out of time is said on stderr and the thread is kept, so start() refuses a second loop and a later stop() can try again.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A stop() whose join runs out of time keeps the thread handle, says so on stderr, and start() does not begin a second loop while the first is alive
- [x] #2 A stop() that joins in time drops the handle as before, and a later start() runs a fresh loop
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_supervisor.py - a stuck thread handed to stop(timeout=0.05) is kept, stderr says 'still running', start() does not replace it; a clean stop drops the handle and start() runs a fresh loop.
2. Green: Supervisor.stop() mirrors watching.Watcher.stop(): join, and if the thread is still alive say so on stderr and keep the handle; drop it only when the join returned because the loop ended.
3. Run test_supervisor and test_app one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_a_stop_that_could_not_join_says_so_and_keeps_the_thread failed ('a thread that never stopped was forgotten'); the clean-stop test passed before the change and pins that path. Green after stop() mirrors watching.Watcher.stop(): test_supervisor 16, test_app 17.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Supervisor.stop() joins with the timeout and, when the thread is still alive, says so on stderr and keeps the handle so start() cannot begin a second loop beside it; a join that returns because the loop ended drops the handle as before. Verified red-to-green by two tests, 33 tests across two files.
<!-- SECTION:FINAL_SUMMARY:END -->
