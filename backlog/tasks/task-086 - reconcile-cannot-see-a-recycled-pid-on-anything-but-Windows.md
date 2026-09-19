---
id: TASK-086
title: reconcile cannot see a recycled pid on anything but Windows
status: Done
assignee:
  - '@claude'
created_date: '2026-09-19 07:05'
updated_date: '2026-09-19 07:05'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 133000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-065 gave reconcile a second question - did the process holding this row's pid start *after* the job did? - and implemented the answer for Windows only. process_started_at returns None everywhere else, every caller reads None as 'no evidence', and the guard therefore never fires on macOS or Linux. tests/test_supervisor.py::test_reconcile_flips_a_job_whose_pid_belongs_to_a_younger_process has been red on this Mac since it was written, and on the ubuntu and macos CI runners as well.

The premise was wrong rather than the code: Linux has /proc/<pid>/stat field 22 against btime in /proc/stat, and macOS has ps -o lstart=. Neither needs a dependency.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 process_started_at answers on macOS and on Linux, and its answer for this process is within a day of now
- [x] #2 A job whose pid belongs to a process younger than the job is flipped to interrupted on every platform, not only Windows
- [x] #3 An unreadable answer - no ps, a refusal, an unexpected format, an unreadable /proc - is None and never an exception, because reconcile runs at startup
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Linux reads /proc/<pid>/stat field 22 and adds btime from /proc/stat, counting fields from the last ')' because a process may call itself '(evil) 1 2 3'. macOS runs ps -o lstart= under LC_ALL=C - the format is the locale's otherwise - and parses the absolute stamp rather than subtracting etime from a clock that may have moved.

Verified on this M2: process_started_at(os.getpid()) returned a time 0.8 s in the past, the TASK-065 test passes, and the whole suite is green for the first time in this session - 2474 passed, 0 failed, where it had been 2 failed all day.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
process_started_at answers on macOS and Linux as well as Windows, so reconcile's pid-recycling guard works everywhere instead of nowhere-but-Windows. The premise that POSIX could not answer without a dependency was simply wrong: /proc/<pid>/stat on Linux, ps -o lstart= on macOS.
<!-- SECTION:FINAL_SUMMARY:END -->
