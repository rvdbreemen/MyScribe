---
id: TASK-106.01
title: A long title does not push the library and search tables off the page
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - ui
  - bug
dependencies: []
parent_task_id: TASK-106
ordinal: 195000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Measured: one long title made the library 1829 px wide in a 1440 px window; Duration, Mode and Status left the card. app.css:402 makes every cell nowrap and td.title has no cap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A real render at 1440 px with a 150-character title has no horizontal overflow (screenshot); the title wraps or ellipsises
- [ ] #2 The 390 px library shows status and duration for each row, not only the title
<!-- AC:END -->
