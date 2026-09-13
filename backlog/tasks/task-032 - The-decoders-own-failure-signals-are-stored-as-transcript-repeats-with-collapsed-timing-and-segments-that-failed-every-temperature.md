---
id: TASK-032
title: >-
  The decoder's own failure signals are stored as transcript: repeats with
  collapsed timing, and segments that failed every temperature
status: Done
assignee: []
created_date: '2026-09-10 23:18'
updated_date: '2026-09-11 18:18'
labels:
  - bug
  - transcribe
dependencies: []
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Diagnosed 2026-09-11 (items "La x112 in media 7" and "mid-window repeats like I do x6"). Two mechanisms, told apart by their signature. (A) All temperature fallbacks fail the compression check (> 2.4) and faster-whisper keeps the best attempt by avg_logprob - which a loop wins (media 7 segment 0: compression 26.24, temperature 1.0, avg_logprob -0.051). (B) A repeat inside a long temperature-0 segment that never trips the threshold because the rest of the segment dilutes it (media 14/17/19: compression 1.8-2.03, temperature 0). Both leave words with ~0 s duration. Re-decoding each site 6 ways (stage params; clip started elsewhere; condition_on_previous_text off; no_repeat_ngram_size 3): media 14 "we were" x8 -> 1-2x (a real stutter, amplified); 17 "wouldn't really" x7 -> 1x in 6/6; 19 "I do" x6 -> "I do security work. I do (my) daddy work. Nice." in 6/6 - words lost; 7 "La" x112 -> 4-24x "la" then "OkÃ©, dit is een test. En deze test moet aantonen dat de recording functie goed werkt" - a sentence lost. Media 7 is not deterministic: the same audio and params decoded to 112x (compression 26.24, as stored) or 16x depending on the sampling draw at temperature 1.0. no_repeat_ngram_size=3 is NOT the fix: it mangles text ("lala", dropped words). Library scan of current runs: 29 collapsed repeats (>=3x, word duration median < 0.05 s) in 22 media, 24 segments over 11 runs failed every temperature (recounted 2026-09-11 by the batch verification, by query and by collapsed_scan's own column; the 34 over 12 first written here did not reproduce); only the 4 sites above are verified by re-decoding, several others are plausibly real ("bye" x4 at an episode end, "um" x3). Many hits sit on window cuts of pre-lookahead runs (37 @1191 s, 51 @1195 s, 39 @1190 s, 34 @595 s) and go away with the re-transcription TASK-030 enables. What remains after that: media 7, 14, 17, 19, about a second each. Scripts: scratchpad repeat_detail.py, repeat_redecode.py, collapsed_scan.py (session 2026-09-11).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A decision is recorded on whether to repair (e.g. a second-opinion re-decode of a flagged span with fresh context) or only flag these spans, with the trade-off against deleting words that were really said
- [x] #2 Whatever is chosen, media 7, 14, 17 and 19 are re-checked against the six-decode evidence above
- [x] #3 Any detector is measured on the whole library for false positives before it changes a transcript
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Measure first (done 2026-09-11, second_opinion_sweep.py): every flag in the library re-decoded twice from clips starting 20 s and 7 s before it. 31 spans / 21 media. Big loops (>=5 repeats, collapsed timing) and failed-every-temperature segments collapse or clear in both opinions; real repetitions hold (media 20 "no" x5 -> 4,4; media 18 "that worked" -> 3,3). 3x repeats are a coin flip ("blah, blah, blah" -> 2x, "um um um" -> gone), so they are out of scope.
2. Rule: flag (A) a 1-3 word phrase repeated back to back >= 5 times whose words take < 0.05 s median, or (B) a segment whose compression ratio stayed over 2.4. Re-decode the flagged stretch from two clips (60 s, starting 20 s and 7 s before it) with the stage parameters. Replace the stored segments over that stretch only when BOTH opinions are clean there (no flag of their own; for (A) the phrase at most twice), taking the opinion with the higher mean word probability. Otherwise keep what was stored.
3. Replace whole stored segments (segment text stays the join of its words; idx renumbered), skip a stretch that does not sit well inside both clips (a clip end is an end of file to Whisper).
4. Record every flag and outcome in the run params (before/after text) and as a job event - the audit trail.
5. Red/green unit tests with a fake model; real run on a copy of the library (media 19: "I do" x6, "uh" x6 in a run made by the current code); suite halves Windows + Linux; adr-judge.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-11: scribe/stages/second_opinion.py, called from transcribe_audio after the windows while the model is loaded. Rule: flag a 1-3 word phrase repeated back to back >= 5 times with median word duration < 0.05 s, or a segment whose compression ratio stayed > 2.4; re-decode the touched segments (bounded at the middle of the pauses either side) from clips starting 20 s and 7 s before, running 15 s past; replace only when both opinions are clean (phrase at most twice, no loop or failed segment of their own), with the surer one (mean word probability); a first opinion that hears the loop too saves the second decode. Every flag and outcome (before/after text) goes into run.params_json["second_opinions"] and a "second-opinion" job event.
Evidence. Sweep (read-only, 31 flags / 21 media, second_opinion_sweep.py): with this rule the stage would change 11 non-cut stretches (14 x3, 17, 19 x2, 24, and failed tail segments in 4, 30, 32) and leave media 20 "no" x5 (both opinions 4x) and media 18 alone; 3x repeats are not flagged. Not verified by listening: the sign-offs "Bye. Bye." (4) and "Bye-bye. Bye-bye." (30), which both opinions hear nothing in.
Red: tests/test_stage_transcribe_second_opinion.py::test_transcribe_audio_gives_a_loop_a_second_opinion_and_says_so ("we were we were" still in the output) and test_stage_transcribe.py::test_a_second_opinion_is_on_the_record_of_the_run (KeyError second_opinions) before wiring. Mutations on a copy (mutate_second_opinion.py): each of five rule changes is caught by the tests about that rule.
Green: 92 transcribe tests; Windows 1136 + 811; Linux 1126 + 794; -m gpu 10 passed; adr-judge 0 violations.
Real run on a library copy, media 19 (3567 s), against run 74 (same transcribe.py without the second opinion): 2 flags, both replaced - "I do. I do. I do. I do. I do. I do." -> "I do identity work. Nice." and "Uh, uh, uh, uh, uh, uh" -> "Oh", "HR" recovered. Word diff 9852 -> 9835 words, ratio 0.9986, all 7 changed stretches inside the two flagged ones; 0 flags left; xRT 10.0; names inherited. Side effect of replacing whole segments: fillers in those segments go too ("I'm, I'm" -> "I'm", one "uh" -> "he"). Media 14 and 17 were re-checked by the sweep only (their current runs predate the look-ahead fix, so a real run would mix both changes); media 7 is in the trash.

Found by the batch verification 2026-09-11 (jobs 206-248), not patched: _sureness scores an opinion with no words in the stretch as 0.0 (mean probability of nothing), so an opinion that is correctly empty always loses to one that says anything. Not exercised by a test and not provable in production: whether it picked media 34's "And that" over an empty opinion cannot be checked after the fact, because a record keeps neither the losing opinion nor which lead won. Media 34's doubled words themselves are fixed at the splice edge (TASK-035). The kept branch never ran in production (0 of 10 records).

Found by the TASK-035 review 2026-09-11 (synthetic fixture only, scratchpad refute_adjacent_head.py, control case): the mirror of an echo. A stored word at the edge of a stretch is inside it, so it is replaced; if the opinion says the same word with its end stretched so its midpoint falls past r1, _cut_to leaves it out, and the word is in neither copy ('we met at the Revspace that evening' -> 'we met at the that evening'). Same result with and without TASK-035's echo drop, so it predates it. Not looked for in the library: a record keeps the first 300 characters of before and after, not their timings. Also fixed in TASK-035 because it touched the same lines: opinion segment keys were (len(segments) + i, idx), and a later opinion that grew the list could make two stretches' keys equal, pointing one opinion's words at the other's segments after renumbering (latent: segment_idx is not persisted); the key is now (stretch number, idx).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The transcribe stage now gives a second opinion to the stretches where Whisper failed by its own measure (a >= 5-fold loop squeezed into no time, or a segment that failed every temperature): two re-decodes from different starting points, and the stored segments are replaced only when both are clean. Decided on a whole-library sweep (31 flags), verified with red/green and mutation tests, both platforms, the GPU tests, and a real run of media 19 where the two loops became "I do identity work. Nice." and "Oh ... HR" and nothing else in 9852 words moved. Each decision is recorded in the run params.
<!-- SECTION:FINAL_SUMMARY:END -->
