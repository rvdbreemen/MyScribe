---
id: TASK-007.03
title: 'ingest: browser microphone recording'
status: Done
assignee: []
created_date: '2026-09-02 16:34'
updated_date: '2026-09-03 09:15'
labels: []
dependencies: []
parent_task_id: TASK-007
ordinal: 46000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 6 Task 3. recording.start/append/finalize/sweep with chunks on disk, ffmpeg stream-copy remux to Matroska to fix the missing WebM duration; routes start/chunk/finish/cancel; recorder.js with DSP off, level meter, 5s chunks, pause/resume, beforeunload guard; recording table.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Three appends land as three ordered files; finish yields a file with a real duration
- [x] #2 Cancel removes the directory; sweep clears abandoned sessions
- [x] #3 A session token with a path separator is refused
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_ingest_recording.py: 52 passed with the URL dialog suite (commits 4b44157 and 1b4cd14). AC1 by test_three_appends_land_as_three_ordered_files and test_finalize_gives_the_recording_a_duration_the_chunks_never_had - the second matters because test_the_captured_chunk_carries_no_duration_the_way_a_browser_sends_it pins that a browser's MediaRecorder chunk has no duration of its own, so the duration has to be produced rather than read. AC2 by test_cancel_removes_the_directory_and_says_the_session_is_over, test_sweep_removes_an_abandoned_session_and_keeps_a_fresh_one and test_sweep_leaves_a_directory_that_is_still_being_written_to, so the sweep cannot eat a live recording. AC3 by test_a_session_that_is_not_a_token_never_becomes_a_path, read in full: eleven hostile ids (../../evil, ..\..\evil, a/b, a\b, .., ., empty, whitespace, a non-token string, 200 characters and a non-url-safe 'cafe') are thrown at all four entry points - session_dir, append, finalize and cancel - not only the route, because a route is not the only caller; each raises UnknownSession and nothing is created outside the recording root. Commit 1b4cd14 additionally stops the recorder's name field from renaming an existing upload.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Browser microphone recording that survives a closed tab (commits 4b44157, 1b4cd14). Verified by tests/test_ingest_recording.py: three appends land as three ordered files and finalize gives the recording a real duration the browser's chunks never carried, cancel removes the directory while the sweep clears abandoned sessions without touching one still being written, and a session id that is not a token never becomes a path at any of the four entry points.
<!-- SECTION:FINAL_SUMMARY:END -->
