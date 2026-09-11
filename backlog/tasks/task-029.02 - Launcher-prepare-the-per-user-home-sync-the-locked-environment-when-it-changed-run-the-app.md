---
id: TASK-029.02
title: >-
  Launcher: prepare the per-user home, sync the locked environment when it
  changed, run the app
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-11 21:18'
labels:
  - packaging
dependencies:
  - TASK-029.01
parent_task_id: TASK-029
ordinal: 72000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-008: the executable a user double-clicks. It runs before any Python environment exists, so it is stdlib-only and frozen per OS; it must show progress through a first sync of about 3 GB on Windows, keep the app from being started twice, and give the user a way to quit - today the app has no quit path at all.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 First run creates the environment in the per-user home with the bundled uv and a uv-managed Python, showing progress; later runs skip the sync while the shipped uv.lock is unchanged and re-sync when it changed
- [ ] #2 The app runs from that environment with SCRIBE_DATA_DIR and .env in the per-user home and the bundled ffmpeg first on PATH; the browser opens when it is up
- [ ] #3 A second launch while MyScribe is running opens the browser instead of starting another server
- [ ] #4 Quit stops the server and any runner child it started
- [ ] #5 tests/test_launcher.py covers the paths, the sync decision and the single-instance rule without network access
<!-- AC:END -->
