---
id: TASK-030
title: >-
  Whisper hallucinates at every 600 s window cut: let each decode hear past its
  cut
status: In Progress
assignee: []
created_date: '2026-09-10 21:02'
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
- [ ] #1 Every window but the last is decoded with LOOKAHEAD_SECONDS (30 s, at most half a window) of the next window appended; words are owned by their midpoint; the segment generator is not consumed past the first segment that starts after the cut
- [ ] #2 Windows stay contiguous and add up to the file; memory stays bounded by one window plus the look-ahead
- [ ] #3 The 7 looping episodes are re-transcribed through the app and none has a run of 8 or more identical words
- [ ] #4 Suite passes in halves; -m gpu pipeline and diarize tests pass
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. iter_windows reads the look-ahead into the carry. 2. collect_segments takes a limit. 3. transcribe_audio decodes window + look-ahead. 4. Tests: look-ahead contents, limit and midpoint ownership, decoder hears past the cut. 5. Correct the real-clip test's metric (difflib autojunk) with the numbers. 6. Suite + gpu. 7. Re-transcribe 28, 29, 40, 43, 46, 55, 57 (+ 7) through the app; count loops.
<!-- SECTION:PLAN:END -->
