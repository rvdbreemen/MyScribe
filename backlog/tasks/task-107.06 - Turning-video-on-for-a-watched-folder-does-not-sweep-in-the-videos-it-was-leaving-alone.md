---
id: TASK-107.06
title: >-
  Turning video on for a watched folder does not sweep in the videos it was
  leaving alone
status: To Do
assignee: []
created_date: '2026-10-06 09:28'
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
- [ ] #1 A folder added audio-only, then switched to video, queues no existing video after a restart - test red today, green after
- [ ] #2 'Transcribe them' still takes the existing files, now including videos only when video is on for that folder
- [ ] #3 The add sentence keeps its counts (186 audio left alone, 761 video ignored in Hermes' walk)
<!-- AC:END -->
