---
id: TASK-043
title: 'A download stops before it fills the disk, at the doctor''s own floor'
status: To Do
assignee: []
created_date: '2026-09-13 20:59'
updated_date: '2026-09-13 21:29'
labels:
  - ingest
  - reliability
dependencies: []
priority: high
ordinal: 81000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-13: "Downloaden moet stoppen als het filesysteem minder dan 4GB vrij heeft." Then, the same day: "task 043 hetzelfde gebruiken als doctor" - one number, shared with the doctor, not a second floor beside it.

Today the only thing that measures free space is scribe/doctor.py check_disk_space, with DISK_FLOOR_GB = 10, and it is advice on a page nobody reads mid-import. A feed fan-out queues up to MAX_FAN_OUT = 500 jobs; each downloads into the job work directory and then prepares a wav beside it, so a run can walk a disk down to nothing while every job reports success until the one that cannot write.

So the guard reuses scribe.doctor.DISK_FLOOR_GB rather than naming its own number, measured where the data lands (paths.DATA_DIR, the doctor's own probe). That makes the stop 10 GB today, not the 4 GB first named: one constant means the stricter of the two wins, and moving it to 4 is then a single edit that moves the advice and the stop together. Worth a moment before building: at 10 GB the guard refuses a download that a 4 GB floor would have allowed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Before a download starts, free space at the data directory is measured against scribe.doctor.DISK_FLOOR_GB - the doctor and the guard read one constant, so they cannot drift apart
- [ ] #2 Below the floor the job fails with a message naming the free space and the floor, and nothing is downloaded
- [ ] #3 The check runs per job rather than once per batch, so a fan-out of 500 stops when the disk runs low rather than when the queue empties
- [ ] #4 A queued job that cannot run for this reason is distinguishable on the jobs board from a download that failed on the network
- [ ] #5 Red then green: a test with a stubbed disk_usage proves the refusal and the message, and one proves a normal download still runs one byte above the floor
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Robert chose 2026-09-13: keep the doctor's 10 GB as the shared number. So the guard refuses a download below 10 GB free - stricter than the 4 GB first named, and deliberately so: one constant, and the stricter reading wins. If the floor ever moves, it moves for the advice and the stop together, in scribe/doctor.py.
<!-- SECTION:NOTES:END -->
