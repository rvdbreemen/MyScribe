---
id: TASK-106.06
title: Live regions and polling keep the user's place
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - ui
  - a11y
dependencies: []
parent_task_id: TASK-106
ordinal: 200000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Code review, not yet rendered: chat swaps its own form while polling and wipes a typed question; jobs polling may steal focus; AI answers sit in a replaced aria-live region; the cleaned reading is a pre that does not wrap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Chat keeps a typed question through a poll and the cleaned text wraps, each with a test or a recorded browser run
- [ ] #2 AI answers are announced from a stable live region
<!-- AC:END -->
