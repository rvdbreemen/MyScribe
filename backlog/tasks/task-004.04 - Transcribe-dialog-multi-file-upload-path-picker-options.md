---
id: TASK-004.04
title: 'Transcribe dialog: multi-file upload, path picker, options'
status: Done
assignee: []
created_date: '2026-09-02 07:28'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 26000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 4. TranscribeOptions pydantic model shared with POST /api/media (tier -> model, translate -> task), GET /transcribe fragment with language list and defaults, POST /transcribe/upload (multipart, many files), POST /transcribe/path via hardlink ingest, fsbrowse with allowed roots and traversal rejection.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Uploading two files creates two media rows and two queued jobs with the mapped params
- [x] #2 Same bytes twice dedupe to one media row but still enqueue
- [x] #3 Path outside allowed roots is refused with 403
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Transcribe dialog: TranscribeOptions shared with POST /api/media, multi-file upload, path picker with fsbrowse roots and traversal/junction refusal (0b444f7), options persisted as defaults. Commits cddf924, 4129b23.
<!-- SECTION:FINAL_SUMMARY:END -->
