---
id: TASK-032
title: >-
  The decoder's own failure signals are stored as transcript: repeats with
  collapsed timing, and segments that failed every temperature
status: To Do
assignee: []
created_date: '2026-09-10 23:18'
labels:
  - bug
  - transcribe
dependencies: []
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Diagnosed 2026-09-11 (items "La x112 in media 7" and "mid-window repeats like I do x6"). Two mechanisms, told apart by their signature. (A) All temperature fallbacks fail the compression check (> 2.4) and faster-whisper keeps the best attempt by avg_logprob - which a loop wins (media 7 segment 0: compression 26.24, temperature 1.0, avg_logprob -0.051). (B) A repeat inside a long temperature-0 segment that never trips the threshold because the rest of the segment dilutes it (media 14/17/19: compression 1.8-2.03, temperature 0). Both leave words with ~0 s duration. Re-decoding each site 6 ways (stage params; clip started elsewhere; condition_on_previous_text off; no_repeat_ngram_size 3): media 14 "we were" x8 -> 1-2x (a real stutter, amplified); 17 "wouldn't really" x7 -> 1x in 6/6; 19 "I do" x6 -> "I do security work. I do (my) daddy work. Nice." in 6/6 - words lost; 7 "La" x112 -> 4-24x "la" then "Oké, dit is een test. En deze test moet aantonen dat de recording functie goed werkt" - a sentence lost. Media 7 is not deterministic: the same audio and params decoded to 112x (compression 26.24, as stored) or 16x depending on the sampling draw at temperature 1.0. no_repeat_ngram_size=3 is NOT the fix: it mangles text ("lala", dropped words). Library scan of current runs: 29 collapsed repeats (>=3x, word duration median < 0.05 s) in 22 media, 34 segments over 12 runs failed every temperature; only the 4 sites above are verified by re-decoding, several others are plausibly real ("bye" x4 at an episode end, "um" x3). Many hits sit on window cuts of pre-lookahead runs (37 @1191 s, 51 @1195 s, 39 @1190 s, 34 @595 s) and go away with the re-transcription TASK-030 enables. What remains after that: media 7, 14, 17, 19, about a second each. Scripts: scratchpad repeat_detail.py, repeat_redecode.py, collapsed_scan.py (session 2026-09-11).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A decision is recorded on whether to repair (e.g. a second-opinion re-decode of a flagged span with fresh context) or only flag these spans, with the trade-off against deleting words that were really said
- [ ] #2 Whatever is chosen, media 7, 14, 17 and 19 are re-checked against the six-decode evidence above
- [ ] #3 Any detector is measured on the whole library for false positives before it changes a transcript
<!-- AC:END -->
