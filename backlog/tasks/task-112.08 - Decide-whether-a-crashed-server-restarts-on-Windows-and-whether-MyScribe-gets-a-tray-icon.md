---
id: TASK-112.08
title: >-
  Decide whether a crashed server restarts on Windows, and whether MyScribe gets
  a tray icon
status: Done
assignee: []
created_date: 2026-10-19 05:51
updated_date: '2026-10-25T17:46:00Z'
labels:
  - autostart
dependencies: []
references:
  - docs/reports/2026-10-19-background-server-research.md
parent_task_id: TASK-112
priority: low
ordinal: 221000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-026 leaves two choices open. Restart after a crash on Windows means a per-user scheduled task instead of the Run value, with defaults that must all be overridden (battery, a 72-hour limit, below-normal priority) and unverified rights for a standard user. A tray icon makes running and stopping visible, but pystray 0.19.5 (2023, LGPL-3.0) breaks the standard-library-only rule of ADR-011 for the launcher. Each choice that goes ahead gets its own ADR.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Robert has answered both, with the consequences of each option in front of him
- [x] #2 Each yes has a Proposed ADR and a task; each no is recorded in this task
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): restart: option C, the launcher restarts its app child a bounded number of times (Run value stays; own ADR because ADR-026 assumes the OS supervises). Tray: option B, pystray in the frozen launcher on all three OSes (ADR amending ADR-011's stdlib-only rule; licence LGPL-3.0, GNOME needs AppIndicator).

2026-10-25: both answered (restart C, tray B). Tasks created: TASK-112.10 (launcher-side restart, its own ADR) and TASK-112.11 (tray via pystray, ADR amending ADR-011). AC2 completes when both ADRs are Proposed.

2026-10-25: both yeses have their ADR and task: ADR-037 / TASK-112.10 (merged), ADR-038 / TASK-112.11 (PR #106).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Decided: a crashed server is restarted by the launcher (TASK-112.10, ADR-037) and MyScribe gets a pystray tray icon (TASK-112.11, ADR-038).
<!-- SECTION:FINAL_SUMMARY:END -->
