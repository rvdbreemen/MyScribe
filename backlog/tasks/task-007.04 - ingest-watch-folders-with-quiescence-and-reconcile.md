---
id: TASK-007.04
title: 'ingest: watch folders with quiescence and reconcile'
status: Done
assignee: []
created_date: '2026-09-02 16:34'
updated_date: '2026-09-03 13:20'
labels: []
dependencies: []
parent_task_id: TASK-007
ordinal: 47000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 6 Task 4. watching.Watcher over enabled watch_folder rows, 5s quiescence on size and mtime, startup reconcile by hash, extension and partial-suffix filters, settings CRUD validated against fsbrowse roots, per-folder default options.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A file still growing is not ingested; a settled file is ingested once
- [x] #2 reconcile picks up a file created while the watcher was down
- [x] #3 A folder outside the allowed roots is refused
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_ingest_watching.py (111 passed together with the glossary suite) and by a real run. AC1 by test_a_file_that_is_still_being_copied_is_not_ingested_until_it_settles and test_a_file_that_disappears_before_it_settles_is_forgotten. AC2 by test_reconcile_picks_up_a_file_created_while_the_watcher_was_down, with test_reconcile_does_not_hash_a_file_that_is_still_being_copied and test_reconcile_is_idempotent guarding the two ways a startup sweep goes wrong. AC3 by test_a_folder_outside_the_allowed_roots_is_refused, read in full: it posts a path outside the configured root and asserts 403, an error naming Settings, and an empty folder table - nothing stored. Two neighbours matter as much: test_a_watch_folder_that_is_not_a_directory_is_refused covers a file, a missing path, whitespace and a relative path with 400, and test_the_apps_own_data_directory_cannot_be_watched stops the app watching its own store, which would be an ingest loop. Real run on an isolated instance (SCRIBE_DATA_DIR in the scratchpad, port 4299): a folder was added through POST /settings/watch, vergadering.wav was copied into it, and job #3 appeared by itself and finished - media 2, 18.0s, 43 words, 1 speaker. Nobody touched a transcribe button.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Watch folders with quiescence gating and a startup reconcile (commits 78766a9, a127246, 6c05e35, 0be446a, 3a53935). Verified by tests/test_ingest_watching.py and by dropping a file into a watched folder on a live instance: it was transcribed automatically, 43 words with a speaker label, no button pressed. A folder outside the allowed roots is refused with 403 and stored nowhere; the app cannot watch its own data directory.
<!-- SECTION:FINAL_SUMMARY:END -->
