---
id: TASK-088
title: >-
  A cleaned part that is a copy of its source is refused, and a rerun can repair
  it
status: Done
assignee: []
created_date: '2026-09-19 20:09'
updated_date: '2026-09-24 04:50'
labels:
  - llm
  - cleaning
dependencies: []
ordinal: 136000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-010 decided on 2026-09-19 that a part which comes back byte-identical while its source carries a line continuing the speaker before it must be refused: it skipped the grouping cleanup.md asked for. No existing gate sees this, because the per-part ratio gates count lost words and a copy loses none.

The decision explicitly includes the reuse path. stored_chunk does not ask whether a reading was published, so a rerun on the same provider, model and prompt version pulls the stored parts back in, copy included, and is refused again. Refusing without fixing that leaves a reading nobody can repair short of emptying the cache.

The shape to build from is the 1-of-11 part of qwen3.5:4b's media 12 reading. tests/test_llm_cleaning_gate.py:145 (test_a_cleaning_with_some_parts_unchanged_is_still_published) pins today's answer and has to move.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A part that is byte-identical to its source is refused when that source has a line continuing the speaker before it, proven by a test that fails on today's code
- [x] #2 A part that is honestly unchanged - one line, or alternating speakers - still passes; the refusal keys on the continuing line in the source, not on sameness alone
- [x] #3 A rerun on the same provider, model and prompt version does not reuse the stored parts of a reading that was refused, so the refusal can be repaired without emptying the cache
- [x] #4 tests/test_llm_cleaning_gate.py:145 is updated rather than deleted, and its new name says what it now pins
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. check_cleaning: a part that came back unchanged (whitespace aside, the existing notion) is refused when its source has a line whose SPEAKER_xx label equals the label of the line before it. Reason names the part and the count of continuing lines. The total-copy rule stays (ADR-010 Must). Unlabelled lines never count as continuing: the transcript does not say who spoke.
2. Red first for #1: rename and flip tests/test_llm_cleaning_gate.py::test_a_cleaning_with_some_parts_unchanged_is_still_published to the measured 1-of-11 shape, now refused.
3. Guards for #2: a copied one-line part and a copied alternating-speakers part, each next to a changed part, still pass; unlabelled consecutive lines copied still pass; a stamp past an hour is parsed.
4. #3: stored_chunk does not reuse a part named in chunk_output_ids of a final row whose gate says published False. End-to-end red test: run 1 copies a part and is refused, run 2 on the same provider, model and prompt version asks that part again and publishes.
5. Grep every test importing scribe.llm.tasks for reuse-after-refusal assumptions; run those files.
6. Mutants on a copy: rule keyed on sameness alone (#2 red), part rule dropped (#1 red), reuse exclusion dropped (#3 red).
7. Update the check_cleaning docstring; list stale ADR-010 passages in the notes (ADR is Accepted, not edited).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Build agent (gate lane, worktree MyScribe-wt-gate), 2026-09-23. Not committed, no criterion ticked.

What changed
- scribe/llm/tasks.py, block "TASK-088: a copied part that skipped the grouping": `_SOURCE_LINE` and `continuing_lines(source)` count a part's stamped lines whose SPEAKER_xx label equals the label of the stamped line before it. In `check_cleaning`'s per-part loop, a part with `unchanged` True and at least one continuing line adds the reason "part N came back as it went in, though C of its L lines continue the speaker before them: the grouping cleanup asks for was skipped". The whole-reading copy rule stays as it was (ADR-010 Must). The per-part dict gets no new key, so test_the_verdict_carries_the_numbers_a_refusal_has_to_quote is untouched.
- scribe/llm/tasks.py, block "TASK-088: a refused reading's parts are asked again": `refused_part_ids(conn, plan)` collects the ids in `chunk_output_ids` of every final row in the same media, run and kind whose params `gate.published` is False. `stored_chunk` returns None for such a row, so the part is asked again. Only the newest row is looked at, as before; no hunt for older rows.
- `check_cleaning` docstring: the "open question for Robert" passage is replaced by the decided rule.

Criteria and proof (EVIDENCE = the 088 folder next to this plan)
- #1: tests/test_llm_cleaning_gate.py::test_one_copied_part_that_skipped_the_grouping_refuses_the_reading and ::test_a_continuing_line_past_the_first_hour_is_seen. Red on the old code: red-test_llm_cleaning_gate.txt ("assert not True", 3 failed, 28 passed). Green: green-test_llm_cleaning_gate.txt (31 passed).
- #2: ::test_a_copied_one_line_part_still_passes, ::test_a_copied_part_of_alternating_speakers_still_passes, ::test_a_copied_part_without_speaker_labels_still_passes. Each copied part sits next to a changed part, otherwise the whole-reading rule refuses it and the guard proves nothing. They pass on old and new code; mut-sameness-alone.txt shows all three red when the rule keys on sameness alone.
- #3: ::test_a_rerun_asks_again_for_the_parts_of_a_refused_reading (run 1 copies part 0 and is refused; run 2, same provider, model and prompt version, asks every part again and publishes). Red with the part rule in and the reuse fix out: red-3-reuse-after-part-rule.txt ("the copied part was reused, not asked again"). Guard on the other side: ::test_a_rerun_still_reuses_the_parts_of_a_published_reading (zero calls on a rerun of a published reading).
- #4: the test at line 145 is renamed from test_a_cleaning_with_some_parts_unchanged_is_still_published to test_one_copied_part_that_skipped_the_grouping_refuses_the_reading. Same inputs; its assertion flips from ok to refused and now pins the exact reason. That is the deliberate behaviour change ADR-010 decided, not a loosening.

Mutants (copy in EVIDENCE/mut, one per run, `grep -rn MUTANT scribe tests packaging` in the worktree finds nothing)
- mut-sameness-alone.txt: 3 failed (the three #2 guards).
- mut-no-part-rule.txt: 3 failed (#1 tests and the #3 rerun test).
- mut-no-reuse-exclusion.txt: 1 failed (#3 rerun test).
- mut-exclude-every-part.txt: 1 failed (published-reading reuse guard).
- mut-unlabelled-continues.txt: 1 failed (unlabelled guard).
- mut-no-hour-stamps.txt: 1 failed (hour-stamp test). A first attempt put the MUTANT marker inside the regex string and broke it; it was rerun on the whole line, and the file holds the rerun.

Other test files importing scribe.llm.tasks or the web AI pages, one process each, all green (green-<file>.txt): test_db 39, test_llm_chat 27, test_llm_headroom_script 9, test_llm_labels 19, test_llm_live 7 deselected (live), test_llm_live_log 10, test_llm_ollama 55, test_llm_speakers 45, test_llm_task_providers 9, test_llm_tasks 159, test_setup 15, test_setup_plan 144, test_setup_prove 43, test_web_ai 141, test_web_library 86, test_web_transcript 106.

Deviations and judgement calls, for the orchestrator to see
- "Byte-identical" in #1 is read as the existing `unchanged` (words equal, whitespace aside). The only copy ever measured differed from its source by 44 added line breaks, so a byte compare would miss it.
- Unlabelled lines never count as continuing: an undiarized run does not say the speaker stayed. So a copy of an undiarized multi-line part still passes. Pinned by test_a_copied_part_without_speaker_labels_still_passes; flip that test if you want the other reading.
- #3 is applied as its text says: every part of a refused reading is asked again, whatever the refusal was for, including the older ratio refusals. Before, a ratio refusal was equally unrepairable by a rerun. Cost: one call per part of the refused reading on the next run. No existing test assumed reuse after a refusal (all files above green).
- A reading published before this rule, such as qwen3.5:4b's media 12 reading, has no refusal on record. Its first rerun reuses its parts and is refused by the part rule; the second rerun asks them again. Stated in the refused_part_ids docstring.

Not done, and who can
- ADR-010 is Accepted, so it was not edited. Stale passages: "A cleaning that is a copy" (the paragraph ending "Whether one such part should refuse the reading is open (Open Questions)"), the Consequences bullet "The copy rule catches only a reading that is a copy throughout ... Whether a copied part should refuse the reading is open", and the Must list, which names only the whole-reading rule and "A stored part is reused only when its segment_ids are the chunk's" without the refused-reading exclusion. A superseding or amending record is the orchestrator's or Robert's call.
- No live run against a model: the fake provider stands in. A real check is a rerun of media 12 on qwen3.5:4b think:false on a library copy, expecting a refusal naming part 0, then a second rerun that asks every part again.

Addendum. The evidence folder named above is C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/d0ea7837-cd8e-4a70-958e-8536dd2e4652/scratchpad/build/gate/088 (scratch, not in the repo).
UI look: the transcript page joins the gate's reasons as they are, and nothing in scribe/web or the templates matches on a reason's wording. New test tests/test_web_transcript.py::test_a_part_copied_without_its_grouping_says_so_on_the_page stores the reason check_cleaning itself writes and finds it in the page's status line through TestClient. Proof: green-page-part-rule-reason.txt and green-test_web_transcript.txt (107 passed).

Verified 2026-09-23 by the orchestrator in MyScribe-wt-gate, fenced, one file per process: test_llm_cleaning_gate 31, test_feed_first_episode 17, test_web_feeds 19, test_web_transcript 107, test_doctor 64, test_llm_tasks 159, test_ingest_urls 112, test_stage_prepare 27, all passed; grep MUTANT over scribe tests packaging install.py finds nothing. Recorded for Robert, not changed here: 'byte-identical' was read as identical apart from whitespace, because the one measured copy differed from its source only by 44 added line breaks; a copied part of an undiarized source passes, because unlabelled lines never count as continuing (pinned by a test, one line to flip); every part of any refused reading is asked again on a rerun, ratio refusals included, as the criterion's words say; an old published copy (qwen3.5:4b, media 12) takes two reruns to repair. ADR-010 is Accepted and was not edited, but three passages now describe the old behaviour: 'A cleaning that is a copy', the copy-rule Consequences bullet, and the Must list - updating an Accepted record is Robert's step. Nothing ran against a live model.

2026-09-24: ADR-010 is superseded by ADR-020, accepted by Robert the same day. The decision is unchanged; ADR-020 describes this task's rule as built (the copy section, the Must and Verification lists, the Consequences bullet), where ADR-010 still called it open.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A cleaned part that is a copy of its source is refused when the source has a line continuing the speaker before it, and an honest copy (one line, alternating speakers) still passes; a refused reading's parts are no longer reused, so a rerun repairs it without emptying the cache. The line-145 test was renamed and flipped, not deleted. Verified red first, six mutants, 16 dependent files green, and a TestClient check that the transcript page shows the new reason.
<!-- SECTION:FINAL_SUMMARY:END -->
