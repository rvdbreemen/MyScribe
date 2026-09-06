---
id: TASK-003.03
title: 'prepare stage: ffmpeg normalize with -progress'
status: Done
assignee: []
created_date: '2026-09-02 02:32'
updated_date: '2026-09-02 07:19'
labels: []
dependencies: []
parent_task_id: TASK-003
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 Task 3. to_wav (mono 16k pcm_s16le) parsing out_time_us from -progress pipe:1 into a 0..1 fraction; adds RunnerContext.state as shared scratch between stages.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Output is 16kHz mono PCM verified by ffprobe
- [x] #2 on_progress called at least twice, non-decreasing, ending >=0.9
- [x] #3 Corrupt input raises with stderr captured
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
prepare stage: ffmpeg to mono 16 kHz pcm_s16le with progress parsed from -progress pipe:1, RunnerContext.state added and tested across stages. Verifier caught an untested progress path and a work-dir leak on failed conversion; both fixed (6ef2192, b6bc513). Commit 951d43a.
<!-- SECTION:FINAL_SUMMARY:END -->
