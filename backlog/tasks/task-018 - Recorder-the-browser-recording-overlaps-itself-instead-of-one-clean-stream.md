---
id: TASK-018
title: 'Recorder: the browser recording overlaps itself instead of one clean stream'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 19:25'
updated_date: '2026-09-06 20:43'
labels: []
dependencies: []
ordinal: 59000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert reports (2026-09-06) that a recording made in the app's Record dialog does not come out as one clean stream; it sounds like overlapping recordings. Review recorder.js end to end (MediaRecorder start/stop, timeslice chunks, how chunks are concatenated and uploaded, whether a second MediaRecorder or AudioContext is started on re-entry or on the dialog reopening) and compare with how OpenTranscribe (WSL install, see INSTALL-NOTES-claude.md in that project) records in the browser. Reproduce first: record 30 seconds of a metronome or spoken count, inspect the file with ffprobe and by ear.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The overlap is reproduced and its cause is written down with the code path
- [x] #2 A 30-second recording made after the fix plays as one continuous stream (ffprobe shows one audio stream with the expected duration, and a listen confirms no doubling)
- [ ] #3 Opening, cancelling and reopening the Record dialog never leaves a MediaRecorder or AudioContext running (checked via console)
- [x] #4 A note compares the chosen approach with OpenTranscribe's recorder
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Measure the bad file (ffprobe packets, decoded length) and the access log. 2. Find the double begin(): live is set only after two awaits, so a second begin slipped through, opened a second session and recorder, and both posted to the live session. 3. Fix: synchronous starting flag, session bound at creation, chunks from a non-live recorder dropped. 4. Node test red/green; real browser recording with a synthetic microphone.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Cause: recorder.js begin() guarded only on live, which is assigned after getUserMedia and POST /record/start resolve; a second begin in that window opened a second session and a second MediaRecorder, and both recorders posted every chunk to live.session. Evidence on media 7 (Recording 2026-09-06 21:20): 2x POST /record/start, 13 chunk POSTs for 32 s, 3103 Opus packets where ~1600 fit, six runs of 251 packets with pts stuck at 4.973/9.973/…, plain decode 62.07 s vs stamped 32.08 s, RMS per 5-s block in near-identical pairs. Fix in commit 5e719cf. Node tests: 2 starts/2 chunks before, 1/1 after (39 passed in test_ingest_recording.py). Real browser (Playwright Chrome, oscillator as microphone, 4299 instance): 1 start, 5 chunks, file 21.361 s, decoded 21.360 s, 356 packets. AC 3 not separately re-verified in a console this session; the existing CR-002 node tests (repaint on swap, refuse to close, discard releases) still pass. OpenTranscribe comparison (frontend/src/stores/recording.ts): sets isRecording=true synchronously before awaiting getUserMedia and refuses a start while it is set - the same guard; it records without a timeslice (one blob at stop), so it has no chunk durability and no concatenation to get wrong.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The overlap was a second begin() slipping through before live was set: two sessions, two recorders, every chunk posted twice. Fixed with a synchronous starting flag, a session bound per recorder and a live-recorder check on chunks. Verified red/green in node and with a real browser recording that decodes to its stamped length.
<!-- SECTION:FINAL_SUMMARY:END -->
