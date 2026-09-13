---
id: TASK-043
title: 'A download stops before it fills the disk: refuse below 4 GB free'
status: To Do
assignee: []
created_date: '2026-09-13 20:59'
labels:
  - ingest
  - reliability
dependencies: []
priority: high
ordinal: 81000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-13: "Downloaden moet stoppen als het filesysteem minder dan 4GB vrij heeft."

Today the only thing that measures free space is the doctor (scribe/doctor.py check_disk_space, DISK_FLOOR_GB = 10), and it is advice on a page nobody reads mid-import. A feed fan-out queues up to MAX_FAN_OUT = 500 jobs; each downloads into the job work directory and then prepares a wav beside it, so a run can walk a disk down to nothing while every job reports success until the one that cannot write.

The floor to enforce is 4 GB free, measured where the data actually lands (paths.DATA_DIR, the same probe the doctor uses).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Before a download starts, free space at the data directory is measured; below 4 GB the job fails with a message naming the free space and the floor, and nothing is downloaded
- [ ] #2 The check runs per job rather than once per batch, so a fan-out of 500 stops when the disk runs low rather than when the queue empties
- [ ] #3 A queued job that cannot run for this reason is distinguishable on the jobs board from a download that failed on the network
- [ ] #4 Red then green: a test with a stubbed disk_usage proves the refusal and the message, and one proves a normal download still runs at 4 GB + 1 byte
- [ ] #5 The doctor and this guard name the same number in one place, so they cannot drift apart
<!-- AC:END -->
