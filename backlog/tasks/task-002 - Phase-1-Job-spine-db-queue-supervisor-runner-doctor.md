---
id: TASK-002
title: 'Phase 1: Job spine (db, queue, supervisor, runner, doctor)'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-05 10:25'
labels: []
dependencies: []
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the spine from the approved Phase 1 plan: pinned venv, SQLite schema v1 (words-canonical), atomic job queue with first-verdict-wins, supervisor thread spawning one runner child per job, startup reconciliation, JSON job API, and a doctor that ends with a verified 30s GPU transcription. Plan of record: docs/superpowers/plans/2026-09-01-phase1-spine.md; spec: docs/superpowers/specs/2026-09-01-myscribe-design.md.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All eight subtasks Done
- [x] #2 python -m pytest -q green on the dev machine
- [x] #3 python -m scribe.doctor all green including GPU smoke, xRT recorded in stage_perf
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Phase 1 spine complete: 8 of 8 subtasks Done. Verification: 86 tests pass (python -m pytest -q, 16.4s) and python -m scribe.doctor exits 0 on the target machine with all nine checks green including the GPU smoke. Commits 7e1c440..85e693e. Measured on this hardware: large-v3-turbo at 14.5x realtime, model load ~10s warm, ~6 GB VRAM.
<!-- SECTION:FINAL_SUMMARY:END -->
