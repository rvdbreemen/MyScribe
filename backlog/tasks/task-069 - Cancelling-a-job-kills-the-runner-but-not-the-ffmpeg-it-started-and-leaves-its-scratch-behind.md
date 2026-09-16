---
id: TASK-069
title: >-
  Cancelling a job kills the runner but not the ffmpeg it started, and leaves
  its scratch behind
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 16:05'
updated_date: '2026-09-16 16:19'
labels:
  - review-2026-09-16
  - concurrency
dependencies: []
priority: medium
type: bug
ordinal: 114000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). _spawn starts the runner with CREATE_NO_WINDOW only (scribe/supervisor.py:216-230), no process group. When a cancel outlives kill_grace the supervisor does proc.terminate() then proc.kill() on the runner alone (:308-313). A runner blocked inside prepare's ffmpeg never sees the flag, so the runner dies but its ffmpeg grandchild keeps converting a two-hour file, and the runner's own finally that removes the job's work directory (runner.py:290-294) never runs, so the scratch stays for ever. The shipped launcher already solves exactly this for the app as a whole: CREATE_NEW_PROCESS_GROUP plus taskkill /T /F on Windows and killpg on POSIX (packaging/launcher/myscribe_launcher.py).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 After a cancel that needs the kill, a grandchild the runner started is dead too
- [x] #2 The job's work directory is gone after that kill
- [x] #3 A runner that honours the cancel flag itself is unaffected: same verdict, same timing
- [x] #4 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_supervisor.py: nep-runner die zelf een slapend kind start en diens pid wegschrijft; annuleren; eisen dat het kleinkind dood is en de kladmap weg.
2. Fix in _spawn: CREATE_NEW_PROCESS_GROUP op Windows, start_new_session op POSIX - dezelfde vlaggen als de launcher.
3. Fix in de kill-tak: taskkill /T /F op Windows, killpg op POSIX, daarna paths.remove_job_work_dir.
4. Draai tests/test_supervisor.py, daarna tests/test_app.py en tests/test_jobs.py, na elkaar.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/supervisor.py, gespiegeld aan packaging/launcher/myscribe_launcher.py: _spawn zet CREATE_NEW_PROCESS_GROUP (Windows) of start_new_session (POSIX); de nieuwe _kill_tree doet taskkill /T /F /PID op Windows en killpg SIGTERM-dan-SIGKILL op POSIX; daarna ruimt de supervisor paths.remove_job_work_dir(job_id) op, want de finally in de runner die dat normaal doet draait niet na een kill.

Rood: 'the grandchild outlived the cancel' - het kleinkind leefde nog na de annulering.
Groen: gerichte test 1 passed; tests/test_supervisor.py 12 passed (de bestaande annuleertest houdt zijn timing binnen grace + 2 s); tests/test_app.py 17 passed; tests/test_jobs.py 24 passed. Een eerdere gechainde run hing op test_app.py, maar alleen draaide die in 5 s: de intermitterende socketpair-stall uit pytest.ini, niet deze wijziging.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Annuleren eindigt nu de hele procesboom van een taak - de runner en de ffmpeg die hij startte - en ruimt de kladmap op die de runner zelf niet meer kan opruimen. Zelfde patroon als de launcher voor de app als geheel. Geverifieerd met een test die eerst rood was op een overlevend kleinkind en daarna groen, plus 12 + 17 + 24 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
