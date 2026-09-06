---
id: TASK-005
title: 'Phase 4: Exports (8 formats, presets, preview, bulk)'
status: Done
assignee: []
created_date: '2026-09-01 20:11'
updated_date: '2026-09-02 19:09'
labels: []
dependencies:
  - TASK-003
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Pure exporters over words + speaker labels: SRT, VTT with voice tags, TXT 4 layouts, CSV utf-8-sig, DOCX, canonical JSON, Markdown, self-contained HTML share bundle. Subtitle segmentation engine (CPL, max lines, reading speed, sentence-aware balancing, compliance report). Advanced Export dialog with live preview, presets, write-to-folder, bulk over selection, thin CLI. Spec section 5.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Golden-file tests for all 8 formats
- [x] #2 Preview endpoint returns first 10 rendered cues for chosen options
- [x] #3 Bulk export writes a folder selection to disk with presets
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Phase 4 complete: 6/6 subtasks Done, 874 tests pass (469 + 405 in two halves). Review verdict FIX-FIRST with both CRITs closed test-first across 12 fix commits; adr-lint --strict clean. One design bug I found myself by exporting real audio (speaker name repeated on every cue) is fixed in 3d58af2 with 4 new tests and a golden diff.
<!-- SECTION:FINAL_SUMMARY:END -->
