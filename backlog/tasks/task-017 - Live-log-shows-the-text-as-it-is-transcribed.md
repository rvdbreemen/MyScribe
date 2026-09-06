---
id: TASK-017
title: Live log shows the text as it is transcribed
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 19:22'
updated_date: '2026-09-06 20:29'
labels: []
dependencies: []
ordinal: 58000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert expects (2026-09-06) the job page's live log to show the transcription text while the transcribe stage runs. The stage only reports a progress fraction; segment text never leaves the runner until finalize. Emit a 'log' job event per segment (or per few seconds, batched, to keep job_event small) carrying the segment's time and text; the job page's log renders events as lines, so the text appears live and stays readable afterwards. Keep the payload short: the words table is the transcript, the event is a glimpse.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 While a transcribe job runs, the job page's log shows each segment's text within a few seconds of it being decoded
- [x] #2 job_event rows for one hour of audio stay under a few hundred (batching), and the events tab does not drown in them
- [x] #3 Nothing reads these events to decide anything (ADR-007 spirit); finalize still builds from words
- [x] #4 A real run with a measured delay between decode and display is in the notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. collect_segments/transcribe_audio take on_segment. 2. LiveText batcher in run(): flush on 12 segments, 8 s wall clock, and at the end; one 'log' job event per batch with at/until/text. 3. jobs_ui.event_summary renders log+text as [m:ss] text; detail_context adds 'listed' (non-log) for the events table; app.js describe() does the same for the stream. 4. Stage tests with a fake clock; web test for pre vs table.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Real run 2026-09-06, job 31 (WHYcast ep29 5-min clip, large-v3-turbo, cuda): first text 31.3 s after the transcribe stage began (model load), further lines at +34.5 s, +39.8 s, … ; 5 log rows for 300 s of audio (~60/hour). Screenshot taken of the running page: text visible under the stepper while diarize ran. Tests: test_stage_transcribe.py + test_web_jobs.py 100 passed. Commit follows 2f3ec3b.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The transcribe stage emits batched 'log' events with the decoded text; the job page shows them as [m:ss] lines live and afterwards, and keeps them out of the events table. Verified with three tests and a measured live run (first text after 31 s, then every 3-5 s).
<!-- SECTION:FINAL_SUMMARY:END -->
