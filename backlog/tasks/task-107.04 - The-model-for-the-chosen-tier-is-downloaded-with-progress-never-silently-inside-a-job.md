---
id: TASK-107.04
title: >-
  The model for the chosen tier is downloaded with progress, never silently
  inside a job
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - installer
  - models
dependencies: []
parent_task_id: TASK-107
ordinal: 205000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Tier max needs mlx-community/whisper-large-v3-mlx (2.9 GB); with the sitting's weights question left open it arrived inside the first job with no progress and no log line (the TASK-089.16 class, one tier over).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A job that must fetch weights reports it with progress and a log line, or the sitting fetches the chosen tier's model; tested
- [ ] #2 The doctor names the missing model for the chosen tier
<!-- AC:END -->
