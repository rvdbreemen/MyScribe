---
id: TASK-004.05
title: 'Jobs dashboard with stage stepper, adaptive polling and SSE detail'
status: Done
assignee: []
created_date: '2026-09-02 07:28'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 27000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 5. /jobs page and /jobs/fragment with the poll interval rendered into the fragment (2s active, 15s idle), stage stepper with progress bar, queue position and ETA, cancel/retry, /jobs/{id} detail with error card and stderr disclosure, /api/jobs/{id}/stream SSE with Last-Event-ID resume, heartbeat and terminal end event.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Fragment interval is every 2s with active jobs and every 15s without
- [x] #2 Detail page shows the error card with error_code for a failed job
- [x] #3 Stream honours Last-Event-ID and ends with event end for a terminal job
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Jobs dashboard: stage stepper, poll interval rendered into the fragment (2s/15s), ETA, cancel/retry, detail with error card, SSE stream with Last-Event-ID resume, heartbeat and terminal end. Commit ded2232.
<!-- SECTION:FINAL_SUMMARY:END -->
