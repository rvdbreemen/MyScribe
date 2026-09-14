---
id: TASK-047
title: A person can change a job's priority and its place in the queue
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 02:17'
labels:
  - jobs
  - ux
dependencies: []
ordinal: 85000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-13: "Zorg ervoor dat je de prioriteit van jobs en / of volgorde van de queue kan beinvloeden." Plus, asked and confirmed the same day: at equal priority the queue is FIFO - oldest first, new work joins the back.

Today priority is decided when a job is enqueued and never again: jobs.claim_next orders by priority DESC, then id, and nothing in the UI can move a job. So a 3-hour recording queued behind a feed import waits, and the only lever is to let it wait.

FIFO at equal priority is already what claim_next does. It is written down here because the change makes it a promise rather than an accident: raising a job must not reorder its equals, and lowering one must put it behind the work already queued at that level.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A queued job's priority can be changed from the jobs board, and the change takes effect on the next claim without restarting anything
- [x] #2 A job can be moved to the front or the back of its own priority level, which is the "order" half of the ask
- [x] #3 At equal priority the queue stays FIFO: oldest first. A test claims jobs across two priority levels and asserts the exact order, including that a job whose priority was just raised does not jump ahead of equals queued before it
- [x] #4 A running job is not affected, and the change is rejected rather than silently ignored for anything but a queued job
- [x] #5 The change is visible: the board shows the priority, and the event log records who moved what (ADR-007: observation only)
- [x] #6 Red then green, and a real run through the app: queue a feed import, raise one recording above it, and show the claim order that follows
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-14. Schema v16 gives the queue an order key of its own, job.queue_seq, because id cannot move: it is the rowid, the target of job_event.job_id and job.retry_of, the /jobs/{id} URL and the runner child argv. The claim index is rebuilt as (status, priority DESC, queue_seq) and still covers the query - EXPLAIN QUERY PLAN says SEARCH job USING COVERING INDEX idx_job_claim, no temp b-tree. The backfill is queue_seq = id, so an existing queue keeps exactly the order it had.

Three properties, written into the migration comment because a reader who does not know the second will file it as a bug: enqueue puts a job behind everything queued (FIFO, as before); a priority change puts a job at the BACK of its new level, so raising the oldest row in the table does not put it in front of jobs queued before it; and front/back within a level is a separate action, which makes "run this next" two deliberate clicks.

jobs.set_priority and jobs.move_in_queue are one statement each with the status guard inside the UPDATE - the supervisor can claim a job between the board reading the row and the write landing, and SQLite is the only coordination there is (ADR-009). Both refuse anything but a queued job (False, not a silent no-op), raise ValueError outside the band or for an unknown move, and record what happened as a job event plus an applog line; nothing reads either to decide a claim (ADR-007). enqueue_many reads the base sequence once and binds base + i, so a 500-episode batch does not pay 500 subqueries against a docstring that measures 17 ms.

The board shows the priority and four controls per queued row (raise, lower, front, back), as buttons rather than a dropdown: the fragment swaps its own outerHTML every two seconds while the queue is busy, and a select a person has open would be yanked mid-choice. tests/seed.py allocates queue_seq the same way, or a seeded queued job would sit at 0 and jump the queue.

Red then green: tests/test_queue_order.py (11 tests, 10 red first) and five in tests/test_web_jobs.py. The FIFO case Robert named is pinned twice: raising the oldest job puts it behind its new equals, and lowering puts it behind the work already there.

AC6, the real run, done through the app rather than the library: .venv/Scripts/python -m scribe --port 4299 --no-supervisor --no-browser against a throwaway SCRIBE_DATA_DIR, driven over HTTP only.

  1. a ticked feed import of 8 episodes -> jobs 1-8 at priority -10
  2. three recordings pasted by hand    -> jobs 10, 11, 12 at priority 0
     board: #1 job 9 (10), #2 job 10, #3 job 11, #4 job 12, then the feed at -10

  3. POST /api/jobs/12/priority priority=10 -> 200, position 2
  4. POST /api/jobs/10/priority priority=10 -> 200, position 3

Job 10 is the OLDEST of the three and still lands behind job 12, which was raised first: that is Robert's FIFO rule, proved through HTTP rather than argued. POST /api/jobs/{id}/move where=sideways is a 400 naming the two moves that exist.

The claim order was then taken with jobs.claim_next against the database the app had just written, supervisor off - deliberately, because a live supervisor would have started real downloads. Board order [9, 12, 10, 11, 1, 2, 3, 4, 5, 6, 7, 8]; claimed order identical.

Two test holes the mutants found, both closed. queue_position is the number a person reads and nothing compared it to the claim order: reverted to the old id ordering, all 77 job tests stayed green while the board showed the job that runs first as #3. And AC5 has two halves - replacing move_in_queue's 'moved' event with 'pass' left everything green, so a move could have shipped leaving no trace at all.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Priority used to be decided at enqueue and never again, so a three-hour recording queued behind a feed import waited and the only lever was to let it wait. Two things move a job now: its priority (what class of work this is) and its place within that class, from the jobs board and from /api/jobs/{id}/priority and /move.

They are separate on purpose, and the rule that makes them agree is that a priority change always lands a job at the BACK of its new level. Without it, raising the oldest job in the table would put it in front of everything there - exactly the 'does not jump ahead of equals queued before it' Robert asked for. Order is a queue_seq column (schema v16, backfilled from id, with the claim index rebuilt on (status, priority DESC, queue_seq)), because id alone cannot express a move. A running job is refused rather than silently ignored, and the guard is inside the UPDATE: the supervisor can claim a job between the board reading it and the write landing, and SQLite is the only coordination there is (ADR-009). Both changes emit an event - observation only, ADR-007; the order lives in the column.

Verified: tests/test_queue_order.py, 13 tests, 10 red first, plus 5 in tests/test_web_jobs.py. Real run through the app on port 4299: raising the newest recording then the oldest put the oldest behind its new equal (positions 2 then 3), an unknown move was a 400, and the order the board showed - [9, 12, 10, 11, 1..8] - is exactly what jobs.claim_next then took. Mutants killed on a copy: queue_position reverted to id ordering, and the 'moved' event removed. Suite green: 1331 passed, 1 skipped, and 861 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
