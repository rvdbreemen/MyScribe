---
id: TASK-005.01
title: 'exports: TranscriptDoc loader, ExportOptions and presets'
status: Done
assignee: []
created_date: '2026-09-02 14:12'
updated_date: '2026-09-02 19:09'
labels: []
dependencies: []
parent_task_id: TASK-005
ordinal: 31000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 Task 1. doc.load(conn, media_id, run_id=None) -> frozen TranscriptDoc (NoTranscript without a current run); ExportOptions pydantic with formats/timestamps/subtitle constraints/encoding/crlf/filename_template; PRESETS netflix/bbc/youtube/podcast/obsidian; filename_for with Win32 scrub; FORMATS registry shape. Plan: docs/superpowers/plans/2026-09-02-phase4-exports.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 load returns idx-ordered words with labels and raises NoTranscript without a run
- [x] #2 Every preset validates; unknown format and cpl<10 rejected
- [x] #3 filename_for scrubs Win32-illegal characters and reserved names
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
doc.load frozen TranscriptDoc (current run by default, NoTranscript otherwise), ExportOptions with 16 validated fields, five presets, filename_for with the Win32 scrub. Verified independently: 49 tests, a sqlite trace proving load issues only SELECTs and total_changes stays 0. Commit 6ce30f4.
<!-- SECTION:FINAL_SUMMARY:END -->
