---
id: TASK-035
title: >-
  The look-ahead writes one stretch twice at a window seam, and so does the
  second-opinion splice
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-11 17:07'
updated_date: '2026-09-11 18:19'
labels:
  - bug
  - transcribe
dependencies: []
ordinal: 76000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-11 by the verification of the 43 re-transcriptions (jobs 206-248), recounted from the database the same day (scratchpad overlaps.py, read-only, every current run). One decode never writes two consecutive words that overlap in time; the current runs hold 31 such pairs in 23 media, and every one sits at a window cut or a second-opinion splice edge.

- 18 are one word written twice at a window cut: window k keeps a word it heard in its look-ahead (it starts before the cut, ends past it, midpoint before the limit - collect_segments) and window k+1 says it again. Media 17 "opportunities when when they are presented": when 595.82-596.72, again 596.30-596.74. 16 in the 43 new runs, 2 in runs 70-71 (media 46, 55). In all 18 the two copies end within 0.02 s of each other; the left copy starts earlier, stretched over the pause before it.
- 1 is two words written twice at a second-opinion splice (TASK-032): media 34 run 99, "Yeah. And that And that was quite impressive." The opinion said "And that" for the looped stretch (800.58-802.63), with "that" stretched to 803.36, and the stored "And that" (802.66) follows. The run before (40) says it once.
- 12 overlap two different words: 8 at window cuts (media 11 twice, 20, 21, 24, 29, 48, 52; e.g. media 11 "you" 595.64-596.66 against "Yeah," from 596.40) and 4 at splice edges by 0.02-0.04 s (media 14, 17, 24, 34). Out of scope: which of two different words was said needs a listen.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 At a window seam, a left copy that overlaps the right side in time and repeats its opening word(s) is dropped; the media 17 and media 34 shapes are unit tests, red first
- [ ] #2 A straddling word the next window does not repeat (the Revspace case) is kept, and a genuine repeat with no time overlap is kept
- [ ] #3 Word and segment indices stay contiguous and each segment text stays the join of its words after a drop
- [ ] #4 The 25 media the fix repairs are re-transcribed - the 17 with an echo (2, 3, 13, 17, 22, 24, 30, 34, 36, 39, 46, 47, 50, 51, 52, 55, 58) and the rest of runs 67-76, which never got the second opinion (15, 19, 20, 28, 29, 40, 43, 57) - and afterwards none of their current runs writes a word twice with overlapping times; the remaining overlaps of two different words are counted in the notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. A pure helper (scribe/stages/seams.py): echo(left, right) -> how many of left's last words (up to 2) are right's first words said again, only when the tokens match and the left copy ends after the right side starts; remove_words() re-says each segment that lost words; renumber() moved from second_opinion.
2. transcribe_audio: remember where each window's words begin; after the last window drop each echo's left copy, renumber; before the second opinion.
3. second_opinion._second_opinion: drop the opinion's copy at either splice edge (the stored neighbours stay untouched).
4. Red tests first (media 17 and 34 shapes, Revspace, "no, no" with a gap, invariants), then green.
5. Real run on a library copy: re-transcribe media 17 and 34, count overlaps before/after.
6. Suite halves on Windows, then Linux (WSL). adr judge before commit.
7. Re-transcribe the 25 in the live library (non-private only), re-count overlaps, verify names kept.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-11: scribe/stages/seams.py (echo, remove_words, renumber); transcribe._drop_echoes drops the left copy at each cut before the second opinion; second_opinion drops the opinion's copy at either splice edge and records it as "echo".

Red first: 5 failed, 28 passed (tests/test_stage_transcribe_windows.py x3: media 17 shape, two-word echo, echo-only segment; tests/test_stage_transcribe_second_opinion.py x2: media 34 shape and its mirror at the stretch start); tests/test_stage_seams.py failed to import. Green: 43 passed. Mutation on a scratch copy: 9 of 9 mutants caught, each by the tests about that piece (no overlap check, punctuation-only words, one-word echoes only, window drops the right copy, window seam never runs, splice tail/head unchecked, edge not moved, no renumber).

Real run on a library copy (old code = HEAD worktree, new = working tree, same job): media 17 old reproduces live run 86 exactly - when[595.82-596.72] when[596.30-596.74] - and new writes it once: opportunities[595.20-595.82] when[596.30-596.74] they; 6193 -> 6192 words, 0 same-word overlaps, indices contiguous. The one overlap left is the 0.02 s splice at 733.94 (two/and, different words, by design). Media 34: neither old nor new code looped at 800.58 this time (window 1 has temperature fallbacks), so the splice path was not hit by the full run; both came out 'Yeah. And that was quite impressive.' with 0 overlaps. Replayed instead with the real model on the real audio (scratchpad splice_replay.py: run 99's leads, tail, limit, hotwords): the 7 s opinion at T0 reproduces run 99's 'And[800.54-801.12] that[801.12-803.36]', echo with the stored words after = 2, transcript reads 'Yeah. And that was quite impressive.' Copies, wav and worktree deleted afterwards.

Adversarial review 2026-09-11 (workflow, 5 lenses, each finding verified by a skeptic): 10 confirmed, 4 refuted. Fixed, each red first (5 failed, 45 passed -> 50 passed): (1) echo() proved an n-word echo with left[-1] against right[0] - a one-word overlap or two different words - so 'no, no, no' lost a real 'no' and 'I think, I think' lost a phrase; the gate is now left[-1] against its twin right[n-1]. On all 31 overlaps in the library the rule gives the same answer as before (18 same -> 1, 12 other -> 0, media 34 -> 2). (2) At a window cut the left copy could reach back past the previous cut when a window held one word; it is now looked for only in the window before the cut. (3) At a splice the head check trusted stored words that the adjacent stretch, worked next, replaced - the word ended in neither copy (regression of this change); the head now only counts stored words from the end of the stretch before. (4) Pre-existing in TASK-032, fixed here because it is the same lines: opinion segment keys could collide between two stretches. Test gaps closed: echoes at two cuts, the > / >= boundary, an edge test that tells one edge from both, segment invariants on the head path, the 18 = 16 + 2 count in a comment. Mutation on a copy: 17 of 17 mutants caught. Refuted: tag collision as an observable defect (it was, once the invariants were tested - fixed anyway), a tail echo split by r1 (upstream, not this code), verdict/_sureness counting echo words (no wrong output), gate looseness (no failure scenario). Found and left: a stored edge word can be lost when the opinion's copy falls past r1 (predates this change; noted in TASK-032).
<!-- SECTION:NOTES:END -->
