---
id: TASK-102.08
title: The sync-only door says what it did and leaves a record
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:36'
labels:
  - installer
dependencies: []
parent_task_id: TASK-102
ordinal: 184000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On an up-to-date home, MyScribe --sync-only prints nothing and exits 0; when a sync is due it prints only uv's line; neither writes launcher.log. Report section 7.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A sync-only run prints one line saying the environment is up to date, or that it synced, and appends what it said to <home>/logs/launcher.log, red first
- [x] #2 The doctor and smoke doors are unchanged
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Red: two tests in tests/test_launcher.py on the headless-machine fixture: a sync-only run that syncs prints a closing line and finds it in <home>/logs/launcher.log; a second run on an up-to-date environment prints that it is up to date and logs that. Green: the quiet door reports through an InstallLog tee (print + launcher.log) and says one closing line per outcome.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red (behaviour, after adding only the two constants): the sync run printed 'Downloading torch / Installed 3 packages' and no closing line; the up-to-date run printed only the location note; no launcher.log either way. Green: the two new tests plus the existing sync-only, enough-room, smoke and doctor tests in tests/test_launcher.py, 9 passed. The doctor and smoke doors print exactly as before; only sync-only tees to launcher.log and says SYNCED / UP_TO_DATE.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
MyScribe --sync-only now ends on one line - the speech engine is installed and up to date, or was already up to date - and writes what it said, uv's lines included, to <home>/logs/launcher.log. Red then green in tests/test_launcher.py; doctor and smoke unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
