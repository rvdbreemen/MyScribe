---
id: TASK-007
title: 'Phase 6: Ingest extras (URL import, mic recording, watch folders, glossary)'
status: Done
assignee: []
created_date: '2026-09-01 20:11'
updated_date: '2026-09-03 13:21'
labels: []
dependencies:
  - TASK-004
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
yt-dlp URL import (bestaudio, playlist fan-out, info.json to hotwords), browser mic recording (getUserMedia DSP off, MediaRecorder 5s chunks appended server-side, remux), watchdog watch folders with quiescence gating and startup reconcile, glossary decode-side (hotwords slot composition) plus post-pass (rapidfuzz + phonetics as reversible derived layer). Spec sections 3-4.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A YouTube URL becomes a finished transcript
- [x] #2 A two-minute mic recording survives a mid-recording page reload (chunks persisted)
- [x] #3 A file dropped in a watch folder is transcribed automatically
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Closed 2026-09-03 with all five subtasks Done. Whole suite run in halves (the whole-suite stall is documented in pytest.ini): tests/test_[a-r]*.py -> 913 passed, 8 deselected in 84.56s; tests/test_[s-z]*.py -> 545 passed, 2 deselected in 121.83s. 1458 passed, 0 failed. The globs were checked for exhaustiveness first (32 + 16 = 48 = every tests/test_*.py) after a quoted glob silently matched nothing and still exited 0.

The three criteria here are end-to-end, so they were run rather than argued. An isolated instance (SCRIBE_DATA_DIR in the scratchpad, port 4299, the app on 4242 untouched) with the supervisor and watcher live:

AC1 - a URL becomes a finished transcript. The link was served from a local http.server rather than YouTube, so the whole path runs (scheme guard, probe, download, ingest, register, transcribe) without reaching a third party. POST /transcribe/url -> job #1 ingest_url done at stage register -> job #2 transcribe done at stage finalize -> media 1, 30.0s, 75 words, 1 speaker, opening 'Almost 80 Orga members showed up...'.

AC2 - a recording survives a mid-recording reload. POST /record/start, two chunks posted, then a full page load on a new connection, then a third chunk with the same token: it answered index 2 rather than restarting at 0, and 000000.webm, 000001.webm and 000002.webm were all on disk. Finish queued job #4, which completed at finalize. It produced 0 words, and that is the fixture rather than the pipeline: chunk.webm is a ~0.33s fragment, so the recording is one second with no recognisable speech. The pipeline finished it cleanly instead of failing, which is the right answer to a silent recording.

AC3 - a file dropped in a watch folder is transcribed automatically. vergadering.wav was copied into the watched folder; job #3 appeared without anyone pressing anything and finished - media 2, 18.0s, 43 words, 1 speaker.

Also run today: python -m scribe.doctor exits 0 with every check green, including gpu-smoke (large-v3-turbo, 75 words from 30s, load 10.7s) which still carries its warmup-dominated caveat, the note that exists because that number once poisoned the ETA medians.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Phase 6 complete: URL import with playlist fan-out, browser microphone recording, watch folders with quiescence gating, and a reversible glossary correction layer. Verified by 1458 passing tests and by a real run on an isolated live instance: a URL became a 75-word transcript, a file dropped in a watched folder became a 43-word transcript with nobody pressing a button, and a recording continued at chunk index 2 across a page reload with all three chunks on disk.
<!-- SECTION:FINAL_SUMMARY:END -->
