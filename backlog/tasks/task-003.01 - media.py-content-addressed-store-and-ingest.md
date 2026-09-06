---
id: TASK-003.01
title: 'media.py: content-addressed store and ingest'
status: Done
assignee: []
created_date: '2026-09-02 02:32'
updated_date: '2026-09-02 07:19'
labels: []
dependencies: []
parent_task_id: TASK-003
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 Task 1. hash_file (streamed sha256), store_path_for, ingest_path (dedupe on sha256, os.link with copy2 fallback), ingest_stream via .incoming temp. Plan: docs/superpowers/plans/2026-09-02-phase2-pipeline.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 hash_file matches hashlib over a >2MiB file
- [x] #2 Identical bytes under different names produce one media row, second reports deduped
- [x] #3 Hardlink ingest leaves the source in place; copy2 fallback works when os.link raises
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
media.py: streamed sha256 (tracemalloc peak 2 MiB over a 64 MiB file), two-char sharded store path, hardlink ingest with copy2 fallback, sha256 dedupe returning the existing row untouched, ingest_stream via .incoming with cleanup on a broken stream. Verified independently: 16 tests, 12/13 injected mutations caught. Commit 41acc97.
<!-- SECTION:FINAL_SUMMARY:END -->
