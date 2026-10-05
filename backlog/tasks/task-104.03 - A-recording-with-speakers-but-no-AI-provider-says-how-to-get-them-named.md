---
id: TASK-104.03
title: A recording with speakers but no AI provider says how to get them named
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - ui
  - llm
dependencies: []
parent_task_id: TASK-104
ordinal: 189000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
finalize skips the speaker pass silently when no provider is chosen (_speaker_pass_blocked returns empty), so a fresh install shows Speaker 1 and Speaker 2 with no hint that naming exists.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A failing test: a run with clusters and no provider carries a note naming the fix (choose a provider), rendered on the transcript page, red first
- [ ] #2 No note when there are no clusters or the recording is private
<!-- AC:END -->
