---
id: TASK-004.08
title: Settings page and python -m scribe polish
status: Done
assignee: []
created_date: '2026-09-02 07:28'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 30000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 8. /settings with live CPU doctor, stored GPU doctor result, model inventory from the HF cache, media disk usage, defaults form persisted to setting; doctor job type running gpu_smoke; python -m scribe opens the browser unless --no-browser.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Saving defaults persists to setting and the page shows them
- [x] #2 The doctor job type stores its result in setting doctor_last
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Settings: live CPU doctor, GPU doctor as a runner job (ADR-001), model inventory, defaults form; python -m scribe opens the browser. Commit 77af69b.
<!-- SECTION:FINAL_SUMMARY:END -->
