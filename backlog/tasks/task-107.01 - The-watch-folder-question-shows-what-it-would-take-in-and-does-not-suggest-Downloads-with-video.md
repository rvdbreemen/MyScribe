---
id: TASK-107.01
title: >-
  The watch-folder question shows what it would take in, and does not suggest
  Downloads with video
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 07:10'
labels:
  - installer
  - bug
dependencies: []
parent_task_id: TASK-107
ordinal: 202000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The question came prefilled with ~/Downloads and the probe.MEDIA_EXTENSIONS filter (audio plus 16 video extensions); yes queued 884 files and 882 transcription jobs at tier max (558 of them .ts).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Before yes, the question says how many files the folder holds and would queue; tested
- [ ] #2 Downloads is not offered as a default, and the filter (audio only, or audio and video) is decided with Robert
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. probe: split MEDIA_EXTENSIONS into AUDIO_EXTENSIONS and VIDEO_EXTENSIONS (union unchanged).
2. db v18: watch_folder.include_video INTEGER NOT NULL DEFAULT 1 (existing rows keep video); add_folder takes include_video, default False.
3. watching: one rule, takes(folder, path) = is_candidate and (folder.include_video or audio suffix), applied where a file meets its folder (pump, reconcile).
4. watching.preview(path, include_video): the same walk and rule, counts per extension: what would be queued and what would be skipped.
5. Settings: an 'also video files' checkbox (off) on the add form; the answer says what was found and queued.
6. Setup engine: a yes-no question 'also video' shown only after a watch folder is given (default no); the engine's apply goes through add_watched with that flag; a --preview-watch PATH command prints the count as JSON; the Tk summary shows it before Finish; the terminal sitting shows it before applying.
7. The Browse dialog opens at the home folder, not wherever macOS last was.
Red first for 2-4, then the UI parts; a real run on a scratch folder with mixed files at the end.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Robert, 2026-10-05: a new watch folder takes audio only; video is switched on per folder; existing watch folders keep taking video (nothing changes under anyone). Code finding: the setup question has no default and the app suggests no path anywhere; the Browse button's askdirectory has no initialdir, and on macOS that dialog opens in Downloads or the last folder - the likely way Downloads got in (unconfirmed; Hermes's report may say). Adding a folder reconciles it at once, which is what turned 884 files into 882 jobs.

Core, red then green: tests/test_ingest_watching.py 5 new tests (audio-only reconcile: 3 taken instead of 1 before; a video noticed and pumped was ingested before; add_folder had no include_video; v17->v18 had no column; no preview) -> 89 passed. Schema v18: watch_folder.include_video DEFAULT 1 for existing rows; add_folder defaults to False. watching.takes() is applied in pump and reconcile after folder_for; preview() walks with the same _walk and rule. Neighbouring files green: test_db 39, test_fsbrowse 14, test_web_settings 55, test_doctor 56, test_library 15, test_setup_library 22, test_setup_prove 43.
<!-- SECTION:NOTES:END -->
