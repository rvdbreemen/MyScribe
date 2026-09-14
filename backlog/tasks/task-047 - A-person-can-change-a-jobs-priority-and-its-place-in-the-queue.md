---
id: TASK-047
title: A person can change a job's priority and its place in the queue
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 00:14'
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
- [ ] #1 A queued job's priority can be changed from the jobs board, and the change takes effect on the next claim without restarting anything
- [ ] #2 A job can be moved to the front or the back of its own priority level, which is the "order" half of the ask
- [ ] #3 At equal priority the queue stays FIFO: oldest first. A test claims jobs across two priority levels and asserts the exact order, including that a job whose priority was just raised does not jump ahead of equals queued before it
- [ ] #4 A running job is not affected, and the change is rejected rather than silently ignored for anything but a queued job
- [ ] #5 The change is visible: the board shows the priority, and the event log records who moved what (ADR-007: observation only)
- [ ] #6 Red then green, and a real run through the app: queue a feed import, raise one recording above it, and show the claim order that follows
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-14. Schema v16 gives the queue an order key of its own, job.queue_seq, because id cannot move: it is the rowid, the target of job_event.job_id and job.retry_of, the /jobs/{id} URL and the runner child argv. The claim index is rebuilt as (status, priority DESC, queue_seq) and still covers the query - EXPLAIN QUERY PLAN says SEARCH job USING COVERING INDEX idx_job_claim, no temp b-tree. The backfill is queue_seq = id, so an existing queue keeps exactly the order it had.

Three properties, written into the migration comment because a reader who does not know the second will file it as a bug: enqueue puts a job behind everything queued (FIFO, as before); a priority change puts a job at the BACK of its new level, so raising the oldest row in the table does not put it in front of jobs queued before it; and front/back within a level is a separate action, which makes "run this next" two deliberate clicks.

jobs.set_priority and jobs.move_in_queue are one statement each with the status guard inside the UPDATE - the supervisor can claim a job between the board reading the row and the write landing, and SQLite is the only coordination there is (ADR-009). Both refuse anything but a queued job (False, not a silent no-op), raise ValueError outside the band or for an unknown move, and record what happened as a job event plus an applog line; nothing reads either to decide a claim (ADR-007). enqueue_many reads the base sequence once and binds base + i, so a 500-episode batch does not pay 500 subqueries against a docstring that measures 17 ms.

The board shows the priority and four controls per queued row (raise, lower, front, back), as buttons rather than a dropdown: the fragment swaps its own outerHTML every two seconds while the queue is busy, and a select a person has open would be yanked mid-choice. tests/seed.py allocates queue_seq the same way, or a seeded queued job would sit at 0 and jump the queue.

Red then green: tests/test_queue_order.py (11 tests, 10 red first) and five in tests/test_web_jobs.py. The FIFO case Robert named is pinned twice: raising the oldest job puts it behind its new equals, and lowering puts it behind the work already there.
<!-- SECTION:NOTES:END -->
