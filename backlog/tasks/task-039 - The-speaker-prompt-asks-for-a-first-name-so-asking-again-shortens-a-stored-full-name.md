---
id: TASK-039
title: >-
  The speaker prompt asks for a first name, so asking again shortens a stored
  full name
status: To Do
assignee: []
created_date: '2026-09-11 20:43'
updated_date: '2026-09-11 20:49'
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
- [ ] #1 Robert decides which fix: (a) the prompt asks for the full name the transcript gives, measured on the library before and after; (b) apply_speakers keeps a stored name the new one is only the first part of, with a test for the case where the shorter name is the right one; or (c) accept first names
- [ ] #2 The chosen fix has a red/green test and, for (a), a before/after count over the named recordings
- [ ] #3 Whether to put the 13 full names back on the current runs is decided with it; the previous runs hold them
<!-- AC:END -->
