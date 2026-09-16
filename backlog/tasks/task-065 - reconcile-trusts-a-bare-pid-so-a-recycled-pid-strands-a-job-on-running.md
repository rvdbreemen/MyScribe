---
id: TASK-065
title: 'reconcile trusts a bare pid, so a recycled pid strands a job on running'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 15:14'
updated_date: '2026-09-16 15:15'
labels:
  - review-2026-09-16
  - concurrency
dependencies: []
priority: medium
type: bug
ordinal: 110000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). reconcile asks only whether something holds the pid (scribe/supervisor.py:92). Windows hands out pids again after a reboot, so a job interrupted by a power cut can find its pid held by an unrelated process; reconcile then leaves the row on running and the job never reaches a terminal status. The job table already carries started_at, and GetProcessTimes gives a process's start time through ctypes with no new dependency, so a process that started well after the job did can be ruled out as that job's runner.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A running row whose pid belongs to a process younger than the job is flipped to interrupted
- [x] #2 A running row whose pid belongs to a process older than the job is left alone
- [x] #3 Where the start time cannot be read - POSIX, an unreadable handle - the old bare-pid behaviour stands
- [x] #4 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_supervisor.py met os.getpid() op een taak met started_at een uur geleden.
2. Fix: process_started_at() via GetProcessTimes, plus _pid_is_younger_than_job() waarin alleen een ja telt; geen antwoord betekent geen oordeel.
3. Slack van 60 s, want claim en spawn zijn twee statements.
4. Draai tests/test_supervisor.py, tests/test_app.py en tests/test_jobs.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/supervisor.py: process_started_at() vraagt de starttijd van een proces op met GetProcessTimes via ctypes, zonder nieuwe afhankelijkheid, en _pid_is_younger_than_job() vergelijkt die met job.started_at plus 60 s speling (claim en spawn zijn twee statements, geen atomaire).

De regel is bewust asymmetrisch: alleen een ja telt. Geen started_at, een onleesbaar handle of een platform dat het niet kan zeggen (POSIX) levert False op, waarmee het oude gedrag op basis van het kale pid blijft staan. Een onbeantwoordbare vraag mag geen oordeel worden.

Rood: assert 'running' == 'interrupted' - een taak van een uur oud hield het pid van dit net gestarte testproces en bleef op running staan.
Groen: tests/test_supervisor.py 11 passed, tests/test_app.py 17 passed, tests/test_jobs.py 24 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Een hergebruikt pid houdt een dode taak niet langer op running: een proces dat ruim na de taak startte kan haar runner niet zijn. Waar de starttijd onleesbaar is verandert er niets. Geverifieerd met een test die eerst rood was en daarna groen, plus 11 + 17 + 24 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
