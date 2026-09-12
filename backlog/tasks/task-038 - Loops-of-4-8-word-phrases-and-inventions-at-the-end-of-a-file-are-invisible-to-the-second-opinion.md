---
id: TASK-038
title: >-
  Loops of 4-8 word phrases and inventions at the end of a file are invisible to
  the second opinion
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 17:33'
updated_date: '2026-09-12 01:18'
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
- [x] #1 A read-only sweep lists every stretch a widened detector would flag (phrases up to 8 words; repeats under 5; the last seconds of a file), with timings, fallback temperature and the old run's text
- [x] #2 A false-positive review like TASK-032's re-decodes a sample of the hits, and the rule it supports (or none) is recorded before any code changes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured 2026-09-11 during TASK-035 (scratchpad splice_replay.py, real model, media 34, stretch 800.58-802.63): the 20 s opinion fell back to T0.4 and said 'A, B, B, B, B, B, B, B.' - seven B with median 0.06 s a word - and second_opinion.verdict() judged it clean, because its loop check only counts repeats squeezed under 0.05 s a word. In production (run 99) the 7 s opinion won on sureness; had the B opinion been surer, a loop of 'ah' would have been replaced by a loop of 'B'. The verdict's loop test belongs in this task's sweep.

AC1 swept 2026-09-12, read-only, over all 55 current runs of 55 non-trashed media (400,549 words, 31,710 segments, 37.0 h). Scripts: scratchpad t038_sweep.py -> t038_sweep.json, t038_hits_compact.tsv (481 rows), t038_sweep_out.txt. A second agent re-derived the numbers with its own detector and its own word-to-segment mapping and confirmed them (t038_skeptic.py / t038_skeptic.json); where it differed, its own query was at fault, except b_extra_packed, where the count depends on how a repeat is parsed (222 with this parse, 205 leftmost).

Detector: n-word phrases n=1..8, tokens through second_opinion._token, repeated consecutively >= 2 times, left-maximal and primitive. 6,624 families; 6,081 of them are unpacked short repeats in T0 segments ("the the", "I think I think") and stay in the census only. 543 families meet the listing rules, 481 hits after merging overlaps:
- a_phrase (n >= 4): 158 hits in 43 media, 42 packed. a_short_5plus (n <= 3, 5+ copies): 35 in 19 media.
- b_extra_packed: 222 in 48 media (207 of them x2, 158 of those n=1 x2). b_collapsed (all copies under 0.05 s a word, which is strictly what MIN_REPEATS hides): 51 in 27 media. b_fallback: 10 in 5 media.
- c_end (last 10 s of the file): 5 in 5 media.
All five cases named in this task are recovered with their quoted timings (m44 2261.79 "go find whatever you" x4, m38 2065.54 x3 at T0.4, m59 "Bye-bye ... Everybody throne" at T1.0, m49 "Thank you." in 0.02 s, m18 "That worked." x4).

What the shipped detector sees: find_flags flags 1 of the 481 - media 20 "no" x5, whose own opinion kept the original. So a widened rule would be new work on 480 stretches, not a tightening of an existing one.

Three findings worth the task:
1. The end of a file is a weak case as written. The last 10 s is not worse than the rest on packing (1.03% vs 1.79%) or on low probability (0.51% vs 0.39%); the only elevated signal is fallback temperature, and all four such segments are one recording (media 59). The rule fires 5 times in 37 h, of which 2 are the known inventions. The rest of the last window is not worse either (13.7 hits per 10k words against 11.5 elsewhere).
2. Packing and low probability travel together: 38 of 42 packed n>=4 phrases have an extra copy with p <= 0.02, against 10 of 116 unpacked. But the presumed-real control ("That worked.") shows both too, so neither separates invented from real on its own. rate_ratio does not separate at all (packed p50 1.6, unpacked 1.21, wide overlap) - treat it as a null.
3. Old-run identity is not evidence at T0: 455 of 467 T0 hits are identical to the previous run, which is what a deterministic decode on the same audio and hotwords gives (hotwords identical in 54 of 54 media). The information sits in the 14 hits inside fallback segments, of which 6 differ.
Second-opinion records: 16 stored, 12 on current runs. The widened test flags none of the 15 replaced ones and does flag the one kept record (media 20 "no" x5 and "wait" x5) - i.e. two false positives if the timing test were simply dropped at 5+ copies. The "A, B, B, B, B, B, B, B." opinion cannot be seen by the sweep because only winning opinions are stored; a synthetic check with the shipped verdict() reproduces the blind spot (seven "b" at 0.06 s a word judged clean, widened test flags it), and the stored words around media 34 at 782-796 s describe a program printing "a string of A" and "a string of B letters", so that opinion may have been literal. Listening would settle it.

Beyond AC1, found and not in scope: 4 stretches where a 9-11 word phrase is said twice back to back (m17 733.1, m21 1609.94, m27 73.45, m47 2281.76 "how am i going to blag my way out of this"), which a detector capped at 8 words cannot see.

Nothing here was listened to or re-decoded: every "reads as real speech" is a reading of the text. AC2 is the re-decode of a stratified sample of 13 picks plus 2 reserves (stored in t038_sweep.json "sample"), with t038_redecode_ac2.py written and its pure helpers self-tested (15 of 15 classify correctly against the stored words), but not yet run - it waits for the GPU, which is decoding TASK-036 AC2 now.

One more thing the review turned up, for whoever builds the rule: production bounds a stretch with _group_by_segments, which counts a segment as touched only when segment.start < flag.end. A zero-duration word (media 20 at 1866.26, "hold. hold.") opens the next segment exactly at that instant, so _stored_span ends at the first copy and the flagged copy falls outside [r0, r1). A widened detector wired straight into second_opinion would re-decode a stretch without the copy it flagged, and would then always read as "disappeared". Traced in the code, not run.

AC2 re-decoded 2026-09-12 on the GPU (scratchpad t038_redecode_ac2.py --with-reserve --opinions, output t038_ac2.json; large-v3-turbo, cuda/float16). Each pick was decoded the way the shipped second opinion decodes: the same stretch from a clip starting 20 s and 7 s before it, production decode options, collect_segments bounded at the stretch. 15 picks, both production opinions agreeing on every one:

- 11 disappear: the repeat is gone, said once. Among them the 4-8 word phrases (media 44 "go find whatever you" x4, media 44's 8-gram, media 19 x3, media 13, media 11), the packed short repeats (media 11 "that book in" x2, media 20 "hold." x2), the fallback cases (media 38 "they're going to" x3 at T0.4, media 15) and both end-of-file inventions (media 59 "Bye-bye Bye-bye Everybody throne", gone at T0 and with 30 s of earlier audio appended too; media 49 "Thank you." in 0.02 s).
- 2 change: media 44 "you" x6 (fewer copies) and media 18 "That worked." x4 - the stretch this task listed as presumed real, which turns out to be partly decoder noise.
- 2 reproduce, and both are the controls that were picked as presumed-real speech: media 47 "240,000 dozen eggs an hour" said twice, and media 14 "until" (a word with p 0.04 at the end of a file).

The rule the evidence supports, for whoever builds it:
1. Widen loops() to phrases of 4-8 words with 2 or more copies, keeping the timing test: every sampled 4-8 word phrase whose extra copies were packed disappeared on a second decode, and the one that was not packed and read as real speech reproduced.
2. Keep the timing test for short repeats instead of lowering MIN_REPEATS on its own: the only 5+ short repeat in the library is media 20 "no" x5, which is real - it reproduced here too, in both decodes, and the shipped opinion already kept it.
3. Treat the end of a file as a temperature and probability question, not a position: the two inventions there sit in a T1.0 segment and in words packed to 0.02 s, while an ordinary word with a low probability reproduced. The last 10 s is not otherwise worse than the rest of the library (1.03 % packed against 1.79 %).
4. Fix the bounding first (noted above, media 20 at 1866.26): a widened detector wired into second_opinion would re-decode a stretch without the copy it flagged, and would then always read "disappeared".

Also replayed: the 6 stored second-opinion records of current runs. The 5 replaced ones come back clean, and the kept one (media 20) still says "no" 4 times, so TASK-032's judgement holds on a fresh decode. The "A, B, B, B" opinion (media 34 at 800.58) came back as "cool." and "And that" this time, so it did not reproduce; the stored words around it describe a program printing strings of A and B, so it may well have been literal. Someone has to listen to settle that one.

Correction to the note above about media 34 at 800.58. The replay used exactly the span TASK-035 replayed (800.58-802.63, run 99's record), so the two are measuring the same thing. Its 7 s opinion came back "And that" at T0, both in TASK-035's replay and here - the same two words run 99 stored, and the words the seam echo in TASK-035 is built around. The 20 s opinion fell back to T0.4 both times and sampled differently: "A, B, B, B, B, B, B, B." in the earlier replay, "cool." here. So "A, B, B, B" is one draw of a fallback, not a stable reading, and it is evidence about sampling at T>0 rather than about letters. The surrounding words do describe a two-threaded program writing "a string of A" and "a string of B letters", so a literal opinion was possible - but nothing here rests on it, and the sweep cannot see opinions anyway because only winning ones are stored.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Measured, not built - which is what this task asked for. A widened detector (phrases of 1-8 words, repeats under MIN_REPEATS, the end of a file) was run read-only over all 55 current runs (400,549 words, 37 h) and lists 481 stretches, of which the shipped detector sees exactly 1. A stratified sample of 15 was re-decoded on the GPU the way the second opinion decodes: 11 disappeared, 2 changed, and the only 2 that reproduced were the controls picked as real speech. The rule the numbers support is recorded in the notes: widen loops() to 4-8 word phrases while keeping the timing test, leave MIN_REPEATS alone for short repeats, treat the end of a file by temperature and probability rather than position, and fix the stretch bounding first (a zero-duration word can put the flagged copy outside the re-decoded span). Building it is new work and needs Robert to say so.
<!-- SECTION:FINAL_SUMMARY:END -->
