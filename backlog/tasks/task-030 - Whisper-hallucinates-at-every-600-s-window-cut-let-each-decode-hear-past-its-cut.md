---
id: TASK-030
title: >-
  Whisper hallucinates at every 600 s window cut: let each decode hear past its
  cut
status: Done
assignee: []
created_date: '2026-09-10 21:02'
updated_date: '2026-09-10 22:18'
labels:
  - transcription
  - bug
dependencies: []
references:
  - scribe/stages/transcribe.py
  - tests/test_stage_transcribe_windows.py
priority: high
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
7 of 50 Hacker History episodes carry a run of ~100 identical words (um/uh/I, 96-112x, squeezed into ~0.3 s) and every one sits just before a multiple of 600 s: media 46 at 598.7 s, 28/29/40/43/55 at 1189-1198 s, 57 at 1792 s; media 7 (a 32 s test recording) starts with 112x 'La,'. WINDOW_SECONDS is 600: the transcribe stage decodes the file in 600 s windows cut at the quietest 100 ms of the last 5 s, and each cut is, to Whisper, the end of the file - where it hallucinates filler loops, 'Thank you.' and the hotword prompt read back (media 40 and 46 print 'The, Hacker, History, Podcast' after the loop). Measured 2026-09-10 by decoding the stage's exact windows again (scratchpad window_loops.py): as the stage decodes, end-of-window garbage 5 of 5 runs over media 46 window 0 and media 28 window 1, a different shape each run (temperature-fallback sampling); with 30 s of the next window appended and everything past the cut dropped, clean 5 of 5, the three repeats identical. hallucination_silence_threshold=2 made media 28 produce a 106x 'um' loop; condition_on_previous_text=False was clean but changes punctuation and disfluencies across the whole transcript.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every window but the last is decoded with LOOKAHEAD_SECONDS (30 s, at most half a window) of the next window appended; words are owned by their midpoint; the segment generator is not consumed past the first segment that starts after the cut
- [x] #2 Windows stay contiguous and add up to the file; memory stays bounded by one window plus the look-ahead
- [x] #3 The 7 looping episodes are re-transcribed through the app and none has a run of 8 or more identical words
- [x] #4 Suite passes in halves; -m gpu pipeline and diarize tests pass
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. iter_windows reads the look-ahead into the carry. 2. collect_segments takes a limit. 3. transcribe_audio decodes window + look-ahead. 4. Tests: look-ahead contents, limit and midpoint ownership, decoder hears past the cut. 5. Correct the real-clip test's metric (difflib autojunk) with the numbers. 6. Suite + gpu. 7. Re-transcribe 28, 29, 40, 43, 46, 55, 57 (+ 7) through the app; count loops.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-10: implemented and committed (4b2c771). Evidence so far: stage windows re-decoded - as-is 5/5 end-of-window garbage, +30 s lookahead 5/5 clean. Suite 1136 + 793, -m gpu 3 passed. Baseline census before re-transcription: 8 of 57 current transcripts carry a run of 8+ identical words (media 7 is a 32 s single-window recording with no cut, so this fix does not touch it). Re-transcription of 28, 29, 40, 43, 46, 55, 57 queued through the app's bulk route (jobs 185-191).

Verified 2026-09-11. AC1/AC2: tests test_each_window_carries_the_audio_just_past_its_cut, test_collect_segments_stops_at_the_cut_without_asking_for_more (incl. a word the cut runs through, each way, and the generator not drained past the cut), test_the_decoder_hears_past_the_cut_and_what_it_says_there_is_dropped, test_windows_are_contiguous_and_cover_the_whole_file, test_windows_never_hold_more_than_one_window_of_samples (lookahead <= half a window). AC3: re-transcribed through the app's bulk route (jobs 185-191, 196): 28, 29, 40, 43, 46, 55, 57 all loop-free (longest repeat 3-5); library census 8 of 57 -> 1 of 59 (media 7 remains: 32 s, one window, no cut - not this bug). The recovered text is real speech, e.g. media 46 at 599 s now reads 'the bulk of my, um, journalist, tech journalist experience was at Jupiter' where it read a repeated sentence and 96x 'uh'. Mean xrt old 8.06 -> new 8.76 (noisy, no slowdown measurable). AC4: Windows 1136 + 793 passed, -m gpu 3 passed; Linux (WSL Ubuntu, Python 3.12.3, torch 2.10.0) 1126 + 776 passed. One runner death on the first media 29 attempt (job 186, STATUS_THREADPOOL_HANDLE_EXCEPTION, 59 s into decoding, at 84-90% commit charge while my WSL page cache held 13 GB); the retry (job 196) with 8 GB free succeeded - memory pressure is the likely cause, not proven.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Each 600 s window is now decoded with 30 s of the next window appended and only what was said before the cut is kept (by word midpoint), so a cut no longer sounds like the end of the file to Whisper. That removed the ~100-word filler loops and hotword read-backs at every cut: 7 affected episodes re-transcribed through the app are loop-free and recover real speech; suites green on Windows and Linux.
<!-- SECTION:FINAL_SUMMARY:END -->
