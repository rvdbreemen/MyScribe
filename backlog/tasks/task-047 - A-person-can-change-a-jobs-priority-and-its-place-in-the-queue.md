---
id: TASK-047
title: A person can change a job's priority and its place in the queue
status: To Do
assignee: []
created_date: '2026-09-13 20:59'
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
