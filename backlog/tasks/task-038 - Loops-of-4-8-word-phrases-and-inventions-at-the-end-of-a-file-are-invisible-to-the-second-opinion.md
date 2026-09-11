---
id: TASK-038
title: >-
  Loops of 4-8 word phrases and inventions at the end of a file are invisible to
  the second opinion
status: To Do
assignee: []
created_date: '2026-09-11 17:33'
updated_date: '2026-09-11 17:51'
labels:
  - transcribe
  - investigation
dependencies: []
ordinal: 79000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-11 by the verification of the 43 re-transcriptions. Pre-existing; neither caused nor fixed by the batch. Measure before building.

- Phrases longer than 3 words: loops(), loop_scan and collapsed_scan all stop at 3-word phrases. A 4-8 word phrase echoed 2-4 times with the extra copies squeezed into ~0 s passes all three. Packed stretches in the 43 went from 46 in 24 media to 26 in 15 (scratchpad batch43_packed.py); per the verification 25 of the 26 carry the same text as the old run, and several read as real speech with packed timing - which is why collapsed timing alone is not evidence of invented words. Example: media 44 run 107 at 2263.93 s, "go find whatever you go find whatever you go find whatever you go find whatever you want".
- Repeats under MIN_REPEATS: media 38 run 103 at 2066.4 s, "they're going to, they're going to, they're going to have a lot of stuff" - 3x, compression 1.96, in the last window (no look-ahead), in temperature-fallback segments (old T0.6, new T0.4), so it varies per run.
- The end of a file has no look-ahead by design: media 59 run 119 ends after "Keep hacking" with "Bye -bye Bye -bye Everybody throne" (T1.0, words 0.00-0.08 s, p0.00); media 49 keeps a "Thank you." squeezed into 0.02 s 4 s before the end; media 18's "That worked." x4 is identical in both runs and was judged real.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A read-only sweep lists every stretch a widened detector would flag (phrases up to 8 words; repeats under 5; the last seconds of a file), with timings, fallback temperature and the old run's text
- [ ] #2 A false-positive review like TASK-032's re-decodes a sample of the hits, and the rule it supports (or none) is recorded before any code changes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured 2026-09-11 during TASK-035 (scratchpad splice_replay.py, real model, media 34, stretch 800.58-802.63): the 20 s opinion fell back to T0.4 and said 'A, B, B, B, B, B, B, B.' - seven B with median 0.06 s a word - and second_opinion.verdict() judged it clean, because its loop check only counts repeats squeezed under 0.05 s a word. In production (run 99) the 7 s opinion won on sureness; had the B opinion been surer, a loop of 'ah' would have been replaced by a loop of 'B'. The verdict's loop test belongs in this task's sweep.
<!-- SECTION:NOTES:END -->
