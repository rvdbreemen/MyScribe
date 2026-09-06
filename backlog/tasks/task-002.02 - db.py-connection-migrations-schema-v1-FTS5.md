---
id: TASK-002.02
title: 'db.py: connection, migrations, schema v1, FTS5'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-05 20:50'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 2. connect() with WAL/FK/busy_timeout/Row, single module RLock, PRAGMA user_version migration ladder, full schema v1 DDL from the plan (media, folder, run, segment, word, speaker_label, speaker_embedding, job with status CHECK, job_event, stage_perf, llm_output, vocab, export_preset, setting, watch_folder, segment_fts external-content with three sync triggers, indexes).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Migration is idempotent and reaches SCHEMA_VERSION
- [x] #2 Bogus job status rejected by CHECK constraint
- [x] #3 FTS triggers keep segment_fts in sync on insert/update/delete
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
db.py with connect (WAL, FK, busy_timeout, Row, check_same_thread=False), module RLock, user_version migration ladder, schema v1 verbatim from the plan. Verified by an independent agent: DDL byte-compared to the plan, live PRAGMA probe, 4 tests pass. Commit f2982d7.
<!-- SECTION:FINAL_SUMMARY:END -->
