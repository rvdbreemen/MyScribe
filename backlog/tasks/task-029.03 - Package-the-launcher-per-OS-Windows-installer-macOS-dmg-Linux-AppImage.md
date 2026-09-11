---
id: TASK-029.03
title: 'Package the launcher per OS: Windows installer, macOS dmg, Linux AppImage'
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-11 21:18'
labels:
  - packaging
dependencies:
  - TASK-029.02
parent_task_id: TASK-029
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-008: each OS needs a native artifact carrying the frozen launcher, the app source, uv.lock, a pinned and checksum-verified uv binary and an LGPL ffmpeg/ffprobe, small enough for a GitHub release asset. Builds must run unattended on GitHub runners.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One build script per OS produces the artifact from a clean checkout on its GitHub runner
- [ ] #2 Each artifact is well under 2 GiB and contains the launcher, app source, uv.lock, the pinned uv and ffmpeg plus ffprobe
- [ ] #3 uv and ffmpeg downloads are pinned by version and verified by SHA-256 before they are packaged
- [ ] #4 The macOS build produces a working artifact locally on the M2 and it launches the app
<!-- AC:END -->
