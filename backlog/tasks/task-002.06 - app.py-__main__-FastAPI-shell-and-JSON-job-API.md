---
id: TASK-002.06
title: 'app.py + __main__: FastAPI shell and JSON job API'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-02 02:27'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 6. GET /health, GET /api/jobs with queue_position and eta, GET /api/jobs/{id} with events, POST cancel, POST retry (new job, retry_of), GET events tail with after=. Lifespan: ensure_dirs, migrate, reconcile, Supervisor.start unless --no-supervisor. Binds 127.0.0.1:4242.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 TestClient: health ok; enqueued fake job listed; cancel and retry behave; events tail respects after=
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app.py FastAPI shell: health, job list with queue position and ETA, job detail with events, cancel, retry, events tail; lifespan does ensure_dirs, migrate, reconcile, supervisor start. Verified with TestClient. Commit 3c310d4.
<!-- SECTION:FINAL_SUMMARY:END -->
