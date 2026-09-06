---
id: TASK-003.02
title: 'probe stage: ffprobe as the media gate'
status: Done
assignee: []
created_date: '2026-09-02 02:32'
updated_date: '2026-09-02 07:19'
labels: []
dependencies: []
parent_task_id: TASK-003
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 Task 2. probe_media returning duration/format/streams/chapters/tags, NotMediaError when no audio stream, stage writes media.duration, registers STAGES['transcribe'] and the FFMPEG_DECODE error code.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 clip30.wav probes to ~30.0s with one audio stream
- [x] #2 A text file raises NotMediaError
- [x] #3 Stage run persists duration on the media row
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
probe stage: ffprobe JSON as the gate, NotMediaError mapped to FFMPEG_DECODE, duration written onto the media row, STAGES['transcribe'] registered. Verified by an independent agent with its own runs. Commit dd235a1.
<!-- SECTION:FINAL_SUMMARY:END -->
