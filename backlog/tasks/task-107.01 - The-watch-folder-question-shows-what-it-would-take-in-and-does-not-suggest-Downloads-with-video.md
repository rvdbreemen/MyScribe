---
id: TASK-107.01
title: >-
  The watch-folder question shows what it would take in, and does not suggest
  Downloads with video
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - installer
  - bug
dependencies: []
parent_task_id: TASK-107
ordinal: 202000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The question came prefilled with ~/Downloads and the probe.MEDIA_EXTENSIONS filter (audio plus 16 video extensions); yes queued 884 files and 882 transcription jobs at tier max (558 of them .ts).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Before yes, the question says how many files the folder holds and would queue; tested
- [ ] #2 Downloads is not offered as a default, and the filter (audio only, or audio and video) is decided with Robert
<!-- AC:END -->
