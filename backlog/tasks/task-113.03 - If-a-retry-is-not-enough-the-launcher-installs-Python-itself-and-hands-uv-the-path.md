---
id: TASK-113.03
title: >-
  If a retry is not enough: the launcher installs Python itself and hands uv the
  path
status: Done
assignee: []
created_date: 2026-10-19 18:15
updated_date: '2026-10-25T07:56:00Z'
labels:
  - packaging
  - windows
dependencies:
  - TASK-113.01
references:
  - >-
    docs/adr/ADR-027-myscribe-keeps-uv-managed-python-and-retries-a-failed-minor-version-link-instead-of-shipping-its-own-python.md
  - packaging/tools.json
parent_task_id: TASK-113
priority: low
ordinal: 225000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Option B of ADR-027. The launcher downloads python-build-standalone for 3.12 with a pinned sha256, the way tools.json already pins uv and ffmpeg, unpacks it outside the uv python dir, and runs `uv sync --python <that python.exe>`. uv then has no download and makes no junction (the link is only made in the download path). Only to be picked up when ADR-027 is decided that way, for instance because the reporter of issue 16 still fails after the retry of the first subtask.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ADR-027 is decided for option B, or this task is closed with the reason
- [ ] #2 A fresh install on Windows creates no junction under the home, shown with `dir /AL` in the notes
- [ ] #3 An update that changes the pinned Python rebuilds the env, and a test asserts the stamp covers the Python pin
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): close as not needed (same reason as TASK-113); no own Python download to pin and maintain. A new 448 report becomes a new task.

2026-10-25: closed with the reason (AC1). The cause of #16 was Inno Setup's RedirectionGuard (TASK-116, fixed in 0.9.1); ADR-027 records it in a dated note (PR #94, merged); the #16 comment is posted (issuecomment-6394984503). AC2 and AC3 belong to option B (MyScribe installs its own Python), which Robert did not choose; they are left unticked as not applicable, not as met.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Not needed: the retry's premise was a 448 of unknown cause; the cause was RedirectionGuard, fixed in 0.9.1. No own-Python download to pin and maintain. ADR-027 carries the note (PR #94).
<!-- SECTION:FINAL_SUMMARY:END -->
