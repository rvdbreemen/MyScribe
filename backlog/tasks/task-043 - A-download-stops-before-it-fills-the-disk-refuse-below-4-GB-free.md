---
id: TASK-043
title: 'A download stops before it fills the disk, at the doctor''s own floor'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 02:15'
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
- [x] #1 Before a download starts, free space at the data directory is measured against scribe.doctor.DISK_FLOOR_GB - the doctor and the guard read one constant, so they cannot drift apart
- [x] #2 Below the floor the job fails with a message naming the free space and the floor, and nothing is downloaded
- [x] #3 The check runs per job rather than once per batch, so a fan-out of 500 stops when the disk runs low rather than when the queue empties
- [x] #4 A queued job that cannot run for this reason is distinguishable on the jobs board from a download that failed on the network
- [x] #5 Red then green: a test with a stubbed disk_usage proves the refusal and the message, and one proves a normal download still runs one byte above the floor
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Robert chose 2026-09-13: keep the doctor's 10 GB as the shared number. So the guard refuses a download below 10 GB free - stricter than the 4 GB first named, and deliberately so: one constant, and the stricter reading wins. If the floor ever moves, it moves for the advice and the stop together, in scribe/doctor.py.

Implemented 2026-09-14. scribe/doctor.py gains the refusal (NotEnoughDisk), the probe (disk_probe_path), the measurement (free_disk_gb) and the guard (require_disk_headroom); check_disk_space is rewritten onto the same two helpers. url_stage.fetch calls the guard first, before the probe and therefore before any network, and runner maps NotEnoughDisk to the error code DISK_LOW - next to DISK_FULL, which is a write that already hit a full volume and leaves half a file behind.

Red then green: tests/test_disk_floor.py, 7 tests, 6 of them failing first. One of them caught a real drift path that was not in the plan: check_disk_space bound the floor as a default argument, so it was frozen at import and moving DISK_FLOOR_GB would have moved the guard and left the advice behind - the two numbers this task exists to prevent. It resolves the floor on the call now.

tests/conftest.py gains an autouse fixture that fakes a roomy disk. Without it the whole url-import suite would quietly depend on the free space of the machine TEMP volume - green here, red in the WSL copy - and the failure would read DISK_LOW, pointing at the code instead of at the disk.

Reviewed adversarially after the fact (four read-only lenses, 2026-09-14) and two things changed.

The refusal's message promised "10 GB clear for media and models" while measuring only paths.DATA_DIR. On this machine the Hugging Face cache is on C: and the data directory on D:, and a first-run model download never passes through url_stage.fetch, so the promise was larger than the guard. It names the drive it measured now, and the doctor's fix_hint says the same thing.

The floor also collided with the feed paths, which was the sharper problem and is why TASK-044/045 changed with it: a feed marks an episode seen the moment it queues it, so queueing into a disk below the floor did not delay a back catalogue, it lost one. Every path that queues feed work checks the floor first now - feeds.poll, feeds.backfill, the episode dialog and the follow button - so nothing is queued, nothing is marked, and a feed's question stays open. Over HTTP that is a 507 carrying the same sentence.

Two test holes the mutants found, both closed: every fake was 'lambda _path: ...', so no test could see which volume was probed (a mutant pointing the probe at C:/Windows passed 7 of 7); and the floor was never tested at exactly DISK_FLOOR_GB, so both '<' -> '<=' and '>=' -> '>' survived. tests/test_disk_floor.py is now 9 tests and kills all three.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A download now refuses below scribe.doctor.DISK_FLOOR_GB free at paths.DATA_DIR - the doctor's own constant, read at call time by both the advice and the refusal, so the two numbers Robert did not want cannot appear. Robert chose the doctor's 10 GB over the 4 GB first named: one number, and the stricter reading wins.

url_stage.fetch calls the guard before the probe, so it runs per job and no byte is fetched; runner maps NotEnoughDisk to DISK_LOW, beside DISK_FULL, so the board tells 'nothing was attempted' from 'a write hit a full volume'. Every feed path checks it too, because a feed marks an episode seen when it queues it: without that, a low disk would lose a back catalogue rather than delay it. The web answer is 507 with the sentence that names the free space and the floor.

Verified: tests/test_disk_floor.py, 9 tests, 6 red first - one of them caught check_disk_space binding the floor as a default argument, frozen at import, which is exactly the drift this task exists to prevent. Three mutants applied to a copy of the tree, each killed by the named test: the probe pointed at another volume, '<' loosened to '<=', and '>=' tightened to '>'. Suite green: 1331 passed, 1 skipped, and 861 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
