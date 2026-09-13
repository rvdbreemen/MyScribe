---
id: TASK-039
title: >-
  The speaker prompt asks for a first name, so asking again shortens a stored
  full name
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 20:43'
updated_date: '2026-09-13 10:00'
labels:
  - speakers
  - llm
dependencies:
  - TASK-037
ordinal: 80000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-11 after TASK-037 (a re-transcription always asks again). The 25 passes queued that evening (jobs 284-308, openrouter/auto, 404,494 prompt + 19,534 completion tokens, 0 failures, 0 private) changed 15 of 54 named clusters on the 25 recordings. Most of those changes (12 clusters, 6 recordings) replaced a full name with a first name: media 15 Laura Scherling -> Laura, Rachel Tubbs -> Rachel, Josh Bressers -> Josh; 19 Marc Boorshtein -> Marc; 28 Josh Bressers -> Josh, Emily Fox -> Emily; 46 Josh Bressers -> Josh (two clusters), Brian Proffitt -> Brian; 55 Melanie Ensign -> Melanie; 58 Josh Bressers -> Josh, Mark Loveless -> Mark. The other way on media 29: Rob -> Rob Malda, Josh -> Josh Bressers. Media 20 gained a name for its unnamed cluster (Cyberpunk Librarian). 0 names gone and 0 role words written (is_role_word, 63a3c91). None of these 25 runs, old or new, held a name a person typed, so this batch did not exercise the human-name guard; its tests do.

Cause, in the primary source: scribe/llm/prompts/speakers.md:40 asks for "the actual first name (or title and surname) when the transcript supports it", one or two words. The model complied; the earlier passes happened to answer with full names. Nothing is lost: the full names are still on each recording's previous run (speaker_label of runs 73, 74, 67, 70, 71, 118), and a person can rename in the transcript.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Robert decides which fix: (a) the prompt asks for the full name the transcript gives, measured on the library before and after; (b) apply_speakers keeps a stored name the new one is only the first part of, with a test for the case where the shorter name is the right one; or (c) accept first names
- [ ] #2 The chosen fix has a red/green test and, for (a), a before/after count over the named recordings
- [x] #3 Whether to put the 13 full names back on the current runs is decided with it; the previous runs hold them
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: a prompt test that the rendered speakers prompt asks for the full name the transcript gives, lets a surname with a tussenvoegsel through (no two-word cap) and shows a full name in its example; the digest test goes red on the edit.
2. speakers.md: the name rule and the JSON example ask for the full name as said; a first name only when that is all the transcript says; "never a guess" stays; cap raised to four words, a tussenvoegsel counts as part of the surname.
3. PROMPT_VERSION 2 -> 3 and PROMPTS_DIGEST (the edit changes a question). Checked first: sweep_speaker_passes keys on kind=speakers without a version, so the bump queues nothing by itself; the only version-keyed read is chunk reuse (tasks.py:1289), a one-time re-ask of chunk notes for a kind someone reruns.
4. Suites in halves, commit before any re-ask (runner children import the working tree at spawn).
5. Measure through the transcript endpoint on the 6 shortened recordings (15, 19, 28, 46, 55, 58) and controls 2, 3, 34 (first names only today): matrix of served model and name per cluster, same person across recordings, no invented surname.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Cause refined 2026-09-11 from llm_output (read-only). The 25 new passes were answered by gpt-5.6-luna 24 times and deepseek-v4-flash once. In media 15 luna wrote "Josh" for SPEAKER_03, while its own evidence field quotes "My name is Josh Bressers". Over all stored speakers answers, luna gave a multi-word name 66 of 97 times and deepseek 77 of 96. The same host is "Josh" in media 15, 28, 34, 46 and 58, and "Josh Bressers" in 13, 17, 20, 22, 24, 29, 30, 36, 39, 40, 43, 47, 50, 51, 52, 55, 57, on the same prompt and mostly the same model. So the prompt allows both answers, and which one comes out varies per call; the JSON example ("name": "Sarah") pulls toward the short one. Option (a) chosen by Claude under Robert's "Doe alles" and the goal "los alle bevinden op". AC #1 stays open for Robert: undoing it is one revert commit, and the old answers stay stored under prompt_version 2.

Implemented 2026-09-11 as 3c4fe0d. speakers.md now asks for the full name as the transcript gives it: first name + surname, or title + surname; a first name only when that is all the transcript says; still "never a guess at a name or a surname". The cap is four words because a tussenvoegsel is part of the surname, and the JSON example is "Sarah Chen". PROMPT_VERSION 2 -> 3, PROMPTS_DIGEST updated. Red: test_the_speakers_prompt_asks_for_the_full_name_the_transcript_gives failed on the old rule; test_editing_a_template_means_bumping_the_prompt_version failed on the edit until the bump. Green: 1235 + 838 passed (Windows, halves).

Measured through the transcript endpoint after the commit: jobs 309-317, 0 failed, 121,064 prompt + 5,365 completion tokens, all 9 answered by openai/gpt-5.6-luna under both versions, so the model is held constant. Recordings: the 6 shortened (15, 19, 28, 46, 55, 58) and 3 controls where the stored names were first names only (2, 3, 34). Multi-word names: v2 3 of 22 -> v3 17 of 22.
- All 12 shortened names came back through the model: Laura Scherling, Rachel Tubbs, Josh Bressers (media 15, 28, 46 x2, 58), Marc Boorshtein, Emily Fox, Brian Proffitt, Melanie Ensign, Mark Loveless. Media 34 gained two: Lars -> Lars Wirzenius, Josh -> Josh Bressers.
- The 5 that stayed one word are the controls: Robert and Lukas (media 2), Danny, Ad and Nancy (media 3). Their transcripts say only a first name, so no surname was invented.
- Media 3 SPEAKER_01 "Ad" came back at confidence 88; it was left, and the stored "Ad" stays.
- Media 19 "Josh Brusses" is the ASR spelling in its own transcript ("My name is Josh Brusses"), unchanged from v2. This is not a new defect.
- Library-wide, "Josh" alone is gone from every current run. 18 single-word names remain on 111 named clusters: 3 typed by a person (media 4) and 15 LLM names that are first names or handles (Wirefall, Hash, Bitterman, Gibson...).

AC #3: nothing was written by hand; the names came back through the new prompt. (The AC says 13; the recount is 12 on 6 recordings.)

Not done, blocked: 22 re-transcriptions from the 43-batch (jobs 206-248; media 4, 10, 11, 12, 14, 16, 18, 21, 23, 26, 27, 35, 37, 38, 42, 44, 45, 48, 49, 54, 56, 59; all open, all fully named, 4 and 59 with names a person typed) were never asked on their current run. Robert's "Altijd opnieuw" would give each a pass. Queueing them was denied by the permission classifier (bulk external send), so it is Robert's call: about 22 x 13k prompt tokens.

Robert decided 2026-09-13: keep the prompt as it was - option (c), accept first names. The full-name prompt is reverted in 0d7d8a8, so speakers.md asks for "the actual first name (or title and surname)" again and PROMPT_VERSION is back to 2. Suites after the revert: 1234 + 851 passed (one test fewer: the one that pinned the full-name wording went with the revert).

AC #2 is unchecked again, because no fix ships: the red/green test it asked for was part of what was reverted. What was measured stands on the record above - with this prompt, on one model, the same host comes back as "Josh" in five episodes and "Josh Bressers" in seventeen, so which one a pass returns varies per call.

The names are not rolled back. The 9 passes of 2026-09-12 wrote the full names to the current runs, and they stay there; nothing re-asks by itself. What follows from this decision: every future re-transcription queues a pass under this prompt (TASK-037), so a full name can come back shortened - including on the nine recordings being re-transcribed now for TASK-036. That is the accepted consequence, not a defect to file again.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Asking who is speaking again (TASK-037) shortened 12 full names to first names on 6 recordings, because the prompt asks for "the actual first name (or title and surname)" and which of the two a model returns varies per call - the same host is "Josh" in five episodes and "Josh Bressers" in seventeen. Option (a) was built and measured (multi-word names 3 of 22 -> 17 of 22 on the same model, all 12 names back, no invented surnames, no role words), and Robert chose to keep the old prompt instead: reverted in 0d7d8a8. The names the measurement restored stay on the current runs; a future pass may shorten them again, which is the accepted behaviour rather than an open bug.
<!-- SECTION:FINAL_SUMMARY:END -->
