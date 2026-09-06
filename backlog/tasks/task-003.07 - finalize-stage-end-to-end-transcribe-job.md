---
id: TASK-003.07
title: finalize stage + end-to-end transcribe job
status: Done
assignee: []
created_date: '2026-09-02 02:32'
updated_date: '2026-09-02 07:19'
labels: []
dependencies: []
parent_task_id: TASK-003
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 Task 7. finalize in one transaction (speakers onto words, is_current flip, xrt recorded, FTS row count verified, work dir removed); POST /api/media ingest+enqueue; CUDA_OOM/FILE_MISSING/FILE_LOCKED/DISK_FULL error codes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 E2E on tiny/CPU: job done, one current run with xrt>0, >10 words, segment count equals FTS count, MATCH finds the segment, work dir gone
- [x] #2 One real GPU run recorded with its measured xRT
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
finalize stage in one transaction (speakers onto words, is_current flip, xrt, FTS count check, scratch removed) plus POST /api/media and the CUDA_OOM/FILE_MISSING/FILE_LOCKED/DISK_FULL codes. E2E on tiny/CPU passes; GPU e2e with large-v3-turbo + diarization verified twice by an independent agent on the RTX 3080 (1.76x and 1.85x over a 30 s clip - warmup-dominated, a lower bound). Commit 83ef772.
<!-- SECTION:FINAL_SUMMARY:END -->
