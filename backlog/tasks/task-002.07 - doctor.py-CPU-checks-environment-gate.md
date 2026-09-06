---
id: TASK-002.07
title: 'doctor.py CPU checks: environment gate'
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-02 02:27'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 7. checks() returning Check(name, ok, detail, fix_hint): python>=3.12, sqlite>=3.35 with FTS5, ffmpeg+ffprobe on PATH, data dir writable, free-disk floor (default 10GB), DB migrates. CLI table output, exit 0 iff non-optional all pass. GPU checks registered optional until next task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 checks() all-ok on dev machine for CPU set
- [x] #2 Disk floor check fails when floor above free space (monkeypatched)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
doctor.py: 7 required CPU checks each carrying its remedy; FTS5 probed by creating a vtab, not assumed. Verified: 10 tests pass and python -m scribe.doctor exits 0 on the target machine with all required checks green. Commit 0d98c16.
<!-- SECTION:FINAL_SUMMARY:END -->
