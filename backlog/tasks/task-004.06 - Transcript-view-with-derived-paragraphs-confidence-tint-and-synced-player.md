---
id: TASK-004.06
title: 'Transcript view with derived paragraphs, confidence tint and synced player'
status: Done
assignee: []
created_date: '2026-09-02 07:28'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 28000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 6. GET /media/{id} rendering render.paragraphs with speaker headings, sentence timestamps, word spans with band classes, timestamp toggle, right rail; /media/{id}/audio serving the original with Range or a cached AAC proxy for non-browser containers with a 50ms duration assertion; app.js player: seek on click, highlight via binary search, resume, speed, #t= deep links, in-page search.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Page has a heading per speaker in order with display names and Speaker N fallback
- [x] #2 Audio route answers 206 on Range and reuses the proxy on a second request for a non-browser container
- [x] #3 Media without a run renders the not-transcribed panel with the job link
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Transcript view: derived paragraphs with speaker headings, sentence timestamps, confidence tint, timestamp toggle, Range audio with cached AAC proxy (race-safe, 634f2bd), synced player in app.js. Commit ff1ce1b.
<!-- SECTION:FINAL_SUMMARY:END -->
