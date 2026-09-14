---
id: TASK-053.02
title: Follow-along at sixty samples a second
status: To Do
assignee: []
created_date: '2026-09-14 21:08'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 93000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 2 of TASK-053. Highlighting hangs on timeupdate, which fires about four times a second. wordAt(t) returns the last word started by t, so words that begin between two ticks are never lit. Measured on media 20, run 146, 12,108 words: the median gap between word starts is 0.240s and 52.4% of words begin within 250ms of the previous one. More than half the recording is skipped, systematically. Drive the highlight from requestAnimationFrame while playing and fall back to timeupdate when paused; wordAt and its binary search do not change.
<!-- SECTION:DESCRIPTION:END -->
