---
id: TASK-005.03
title: 'exports: SRT, VTT, TXT, CSV, JSON, Markdown writers with golden files'
status: Done
assignee: []
created_date: '2026-09-02 14:12'
updated_date: '2026-09-02 19:09'
labels: []
dependencies: []
parent_task_id: TASK-005
ordinal: 33000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 Task 3. Pure writers over TranscriptDoc: SRT (comma timestamps, NAME: prefix), VTT (<v> tags), TXT four layouts, CSV utf-8-sig with documented columns, canonical JSON schema_version 1, Markdown with optional front matter and timestamp links; encodings and CRLF honoured.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 One golden file per writer matches byte-for-byte
- [x] #2 VTT has voice tags only with speakers on; TXT monologue has no names
- [x] #3 CSV parses back to the cue count; JSON round-trips
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Six writers with byte-compared golden files, encodings and CRLF honoured. Later corrected by 3d58af2: the speaker name is written where a run opens, not on every cue - found by exporting real audio, with the golden diff showing exactly the two redundant prefixes removed. Commit 420267a.
<!-- SECTION:FINAL_SUMMARY:END -->
