---
id: TASK-107.01
title: >-
  The watch-folder question shows what it would take in, and does not suggest
  Downloads with video
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 18:15'
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
- [x] #1 Before yes, the question says how many files the folder holds and would queue; tested
- [x] #2 Downloads is not offered as a default, and the filter (audio only, or audio and video) is decided with Robert
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

Robert, 2026-10-05, second decision: a new watch folder takes only what arrives after it is added (the question says 'watch for new recordings'); what it held is recorded in watch_existing (path key, size, mtime) and left alone until it changes or somebody presses 'Transcribe them'. Folders from before v18 keep taking everything. Found on the way: a folder added to a running app was only walked at the next start (reconcile ran at startup only), so 'picked up shortly' was not true either; Watcher.request_reconcile now walks on the next tick when asked.
Red then green (tests/test_ingest_watching.py): left alone at add, taken when changed, 'Transcribe them' takes them, old rows unchanged, audio-only filter, video switch on an existing folder (red: flag stayed 0), the button offering the right switch (red: the context row had no include_video key, so an audio-only folder showed 'Audio only'), singular wording. tests/test_setup_plan.py: the sitting test that showed the existing file taken at start now shows it left alone and a later file taken (deliberate change, diff in the commit); a success note only when something was left alone. Totals: test_ingest_watching + test_web_settings + test_setup_plan 334 passed; test_setup 15, test_setup_library 22, test_launcher_sitting 71, test_db 39.
Real run (app on 4299, --no-supervisor, scratch library and a folder with 3 audio and 4 video files): adding it answered 'Watching that folder for new recordings. 3 audio files already in it were left alone; Transcribe them below if you want them too. 4 video files ignored: video is off for this folder.'; 'Transcribe them' answered '3 audio files already in it will be transcribed' and the watch_existing rows went to 0; media and jobs stayed 0 (no watcher in that mode). The screenshot showed a long path pushing the switch and the button out of the table; the watch table now wraps (app.css), re-shot and visible. The Browse button opens at the home folder (initialdir), not where macOS last was.

Released in 0.8.4 (tag v0.8.4 -> 2774c2a, release.yml run 37351054122 all success; Windows exe sha256sum -c OK, attestation -> refs/tags/v0.8.4).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A watch folder now watches for new recordings: audio only unless video is switched on (per folder, at add time or later on its row), and what it already held is counted, said, and left alone until it changes or somebody presses 'Transcribe them'. Existing folders keep their behaviour (schema v18 defaults). The setup question says so; its Browse opens at home. Red-then-green tests across watching, settings and the setup sitting; a real run on a scratch folder with the answers and screenshots quoted.
<!-- SECTION:FINAL_SUMMARY:END -->
