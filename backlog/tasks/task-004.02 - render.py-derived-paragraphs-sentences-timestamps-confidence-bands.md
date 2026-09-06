---
id: TASK-004.02
title: 'render.py: derived paragraphs, sentences, timestamps, confidence bands'
status: Done
assignee: []
created_date: '2026-09-02 07:27'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 24000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 2. Pure functions over word rows: paragraphs (speaker change, gap >= 2s, ~700 chars), sentences on terminal punctuation, format_ts clock/srt, speaker_display, confidence_band, join_text without double spaces.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Paragraph splits on speaker change, on a 2.5s gap, not on 1.9s, and after max_chars
- [x] #2 Sentence split ignores 3.14 and splits on period plus space
- [x] #3 format_ts and confidence band boundaries as specified
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
render.py pure view models: paragraphs (speaker/gap/max_chars), sentences, format_ts, confidence bands, speaker_display, join_text. Commit 3afe796.
<!-- SECTION:FINAL_SUMMARY:END -->
