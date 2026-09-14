---
id: TASK-053.07
title: The player gets a structure ribbon
status: To Do
assignee: []
created_date: '2026-09-14 21:08'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 98000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 7 of TASK-053. The player is a 52px strip with no link between the playhead and the shape of the recording. A ribbon carrying chapter ticks where chapters exist, speaker bands, the playhead and search hits makes an hour legible at a glance and turns chapters from an answer you read into a way to move. Explicitly NOT a waveform: nothing stores peaks and decoding a 64-minute file in the browser costs minutes of CPU, so promising one would be a lie. Geometry must come from the timing attributes, never from measured boxes - content-visibility:auto makes off-screen reads unreliable.
<!-- SECTION:DESCRIPTION:END -->
