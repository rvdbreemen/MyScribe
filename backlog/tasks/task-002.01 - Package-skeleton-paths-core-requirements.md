---
id: TASK-002.01
title: 'Package skeleton, paths, core requirements'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-05 20:50'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 1. Venv at .venv, install fastapi/uvicorn/jinja2/pytest/httpx, scribe package with paths.py (DATA_DIR, DB_PATH, MEDIA_DIR, LOGS_DIR, ensure_dirs), pytest.ini.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 test_paths.py passes: dirs created under SCRIBE_DATA_DIR override
- [x] #2 requirements.txt frozen and committed
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
scribe package + paths.py with SCRIBE_DATA_DIR override; venv pinned in requirements.txt. Verified: test_paths.py passes, commit 7e1c440.
<!-- SECTION:FINAL_SUMMARY:END -->
