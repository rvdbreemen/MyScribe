---
id: TASK-107.06
title: >-
  Turning video on for a watched folder does not sweep in the videos it was
  leaving alone
status: Done
assignee:
  - '@claude'
created_date: '2026-10-06 09:28'
updated_date: '2026-10-06 10:05'
labels:
  - watch
  - bug
dependencies: []
parent_task_id: TASK-107
priority: high
ordinal: 207000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by Hermes (Jim's agent) in the third macOS walk on 0.8.4, 2026-10-05; confirmed in code 2026-10-06. leave_existing() in scribe/ingest/watching.py writes a watch_existing row only for paths takes() accepts (line 434: if not takes(folder, path): skip). A folder added audio-only therefore counts its videos as 'ignored' but does not record them as left alone. After 'Take video too', reconcile at the next start tests takes() (now true for video) and _left_alone() (no row, so false) and queues every existing video. Hermes measured 2 videos -> 2 jobs after a restart in a scratch folder; on his ~/Downloads that would be 761 video jobs: the flood 0.8.4 removed from 'add', moved to 'restart'. Suggested fix (Hermes): register every media file in leave_existing, audio and video, independent of takes(); include_video then governs only what arrives, and the existing files stay left alone until 'Transcribe them'.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A folder added audio-only, then switched to video, queues no existing video after a restart - test red today, green after
- [x] #2 'Transcribe them' still takes the existing files, now including videos only when video is on for that folder
- [x] #3 The add sentence keeps its counts (186 audio left alone, 761 video ignored in Hermes' walk)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Confirmed in code before fixing: leave_existing skipped every path takes() refused before writing its watch_existing row (watching.py:434 on 0.8.4). Red (b8e0803 tests on the old code): test_video_switched_on_later_leaves_the_videos_that_were_already_there - reconcile after video on returned 1 (the old video), expected 0; test_transcribing_what_was_there_takes_only_what_the_folder_takes - same, after 'Transcribe them'. Fix: leave_existing records every media file (counts still by takes(), so the add sentence keeps '186 audio left alone, 761 video ignored'); existing_left and take_existing filter the stored keys through takes() with the folder's current include_video, so an audio-only folder's 'Transcribe them' takes only audio and keeps the videos left alone. test_the_count_left_alone_is_what_transcribe_them_would_take guards the row count (green before and after). Green: test_ingest_watching 101 passed (two earlier runs stalled - socketpair; third run with --durations finished in 45 s), test_web_settings 55, test_setup_plan 183.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Old videos in an audio-only watched folder stay left alone when video is switched on later; 'Transcribe them' and the left-alone count follow what the folder takes. Red then green.
<!-- SECTION:FINAL_SUMMARY:END -->
