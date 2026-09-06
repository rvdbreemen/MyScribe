---
id: TASK-002.03
title: 'jobs.py: enqueue, atomic claim, first-verdict-wins, events, ETA'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-02 02:27'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 3. enqueue/claim_next (BEGIN IMMEDIATE + UPDATE RETURNING, gpu and cpu-prework modes)/finish (first verdict wins)/request_cancel/emit+events_after/set_stage/record_stage_perf+eta_seconds/queue_position, exact signatures in the plan.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 8 racing threads on 4 queued jobs yield exactly 4 claims
- [x] #2 Second finish() returns False and keeps first verdict
- [x] #3 Cancel on queued job finishes it cancelled immediately
- [x] #4 Event seq strictly increasing per job under concurrent emit
- [x] #5 eta_seconds returns median-based estimate with seeded history, None without
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
jobs.py: atomic claim via BEGIN IMMEDIATE + UPDATE RETURNING, first-verdict-wins finish, event log with per-job seq, stage_perf median ETA, queue_position. Verified: 8-thread race yields exactly 4 claims. Commit 372d587.
<!-- SECTION:FINAL_SUMMARY:END -->
