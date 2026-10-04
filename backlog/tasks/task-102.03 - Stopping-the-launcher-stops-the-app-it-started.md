---
id: TASK-102.03
title: Stopping the launcher stops the app it started
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:41'
labels:
  - installer
dependencies: []
parent_task_id: TASK-102
ordinal: 179000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After pkill MyScribe the child python -m scribe kept serving on 4242. Quit does the right thing; a SIGTERM to the launcher does not, because the app runs in its own session. Report section 7. A SIGKILL or crash cannot be caught by the launcher and stays out of scope; say so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A SIGTERM (and Ctrl+C) to the headless and the windowed launcher stops the app's process group, shown by a test that starts a real child and signals
- [x] #2 Windows behaviour is unchanged and said so
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Cause: run_headless catches KeyboardInterrupt (SIGINT) only; pkill sends SIGTERM, Python's default ends the launcher without launch.stop(), and the app in its own session keeps serving. Red: (1) a portable test runs run_headless with a fake launch and raises SIGTERM from a timer; the loop must end at once and stop() be called (a sentinel handler keeps pytest alive without the fix); (2) a POSIX-only test runs a real launcher-side script with a real child in its own session, sends the script SIGTERM, and expects the child gone. Green: run_headless and run_window turn SIGTERM into the same path as Ctrl+C / Quit, and restore the old handler after. SIGKILL and a crash stay out of reach; Windows' taskkill path unchanged.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: headless test, the SIGTERM went to the handler that was there before (the launcher installed none); window test, the same. Green: tests/test_launcher.py + test_launcher_sitting.py 178 passed, 2 skipped on Windows. The POSIX real-process test (a launcher-side script, a real child in its own session, SIGTERM to the script, the child must be gone) runs on Linux in WSL: 5 passed with the sync-only and SIGTERM tests. Bites: in the WSL copy with stop_on_sigterm removed, 'DID NOT RAISE ProcessLookupError' - the child survived. Windows: no change to AppProcess.stop's taskkill path; SIGKILL and crashes stay out of reach, said in the docstring.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A SIGTERM to the launcher (what pkill sends) now ends it through launch.stop(), which takes the app's process group with it: run_headless turns it into the Ctrl+C path, run_window into Quit, and the old handler is put back. Red then green in both launcher test files; a real-process test on Linux bites when the fix is removed. SIGKILL or a crash still leave the app running, as documented.
<!-- SECTION:FINAL_SUMMARY:END -->
