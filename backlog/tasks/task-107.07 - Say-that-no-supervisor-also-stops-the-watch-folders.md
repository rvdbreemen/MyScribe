---
id: TASK-107.07
title: Say that --no-supervisor also stops the watch folders
status: To Do
assignee: []
created_date: '2026-10-06 09:28'
labels:
  - watch
  - docs
dependencies: []
parent_task_id: TASK-107
priority: low
ordinal: 208000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Hermes, 2026-10-05: with --no-supervisor the start log reads supervisor false, watcher false, and a watch folder silently does nothing. Probably intended (the watcher lives with the supervisor), but nowhere visible: neither --help nor the Watch folders card says so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 --help and the Watch folders card say that watching needs the supervisor, or the watcher runs without it
<!-- AC:END -->
