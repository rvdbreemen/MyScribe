---
id: TASK-102.08
title: The sync-only door says what it did and leaves a record
status: To Do
assignee: []
created_date: '2026-10-04 05:27'
labels:
  - installer
dependencies: []
parent_task_id: TASK-102
ordinal: 184000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On an up-to-date home, MyScribe --sync-only prints nothing and exits 0; when a sync is due it prints only uv's line; neither writes launcher.log. Report section 7.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A sync-only run prints one line saying the environment is up to date, or that it synced, and appends what it said to <home>/logs/launcher.log, red first
- [ ] #2 The doctor and smoke doors are unchanged
<!-- AC:END -->
