---
id: TASK-004.07
title: Speaker rename and word-range reassignment
status: Done
assignee: []
created_date: '2026-09-02 07:28'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 29000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 7. POST rename upserting speaker_label for the current run and re-rendering all headings; POST reassign setting speaker and edited_by_user=1 on an idx range, new labels created on the fly; range validation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rename updates every heading of that cluster and does not duplicate on repeat
- [x] #2 Reassign flips speaker and edited_by_user only within the range
- [x] #3 Out-of-range reassignment returns 400
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Speaker rename (speaker_label upsert, all headings re-rendered) and word-range reassignment with edited_by_user, new labels on the fly, range validation. Commit d4e4511.
<!-- SECTION:FINAL_SUMMARY:END -->
