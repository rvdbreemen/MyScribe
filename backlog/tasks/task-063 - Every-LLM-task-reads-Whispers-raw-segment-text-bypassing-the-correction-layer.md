---
id: TASK-063
title: >-
  Every LLM task reads Whisper's raw segment text, bypassing the correction
  layer
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 15:06'
updated_date: '2026-09-16 16:31'
labels:
  - review-2026-09-16
  - architecture
  - needs-decision
dependencies: []
priority: medium
type: bug
ordinal: 108000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). chunking.timestamped_line builds every line a model sees from segment['text'] (scribe/llm/chunking.py:85), which is Whisper's raw text. The glossary correction stage writes its result to the word rows, and the transcript view, the DOCX and the SRT all read words - so a name the correct stage fixed is right everywhere the user looks and wrong in everything the LLM is asked about. The verifier established this needs no manual retype: correct.py runs as a standard stage on every transcription (scribe/stages/__init__.py:36) and finalize auto-queues the speakers job straight after it, so the default path already sends uncorrected text. It also established the auto-apply cannot clobber a human-typed name (llm/tasks.py:2052-2066), which bounds the harm.

NEEDS A DECISION, NOT A BUGFIX. ADR-003 governs this ground: words are canonical, every grouping is derived at render time, and its Must Not forbids rewriting segment text. Its Exceptions clause allows a cache of derived output, but only keyed by (run id, rules version) and invalidated on edit. Deriving the LLM's lines from word rows is therefore an architecture choice with an ADR around it, and belongs in /adr-kit:adr with Robert accepting it - not in a quiet bugfix. Related: TASK-063's sibling finding that clean_reading is a stored derivation keyed by run alone and never invalidated (scribe/db.py:390), which the same decision should settle.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A decision is recorded: either an ADR that says how the LLM layer derives its text, or an accepted note that the raw segment text is deliberate
- [x] #2 If the decision is to derive from words, timestamped_line reads the corrected text and a test pins a corrected name reaching the model
- [x] #3 The chunking size estimate still matches what is actually sent
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Decision: derive every LLM line from the run's words, read through the correction layer (TranscriptDoc.words already is), with Whisper's segments as the boundaries only. This is what ADR-003 prescribes (words canonical, groupings derived at render time); it adds no table and no cache, so it needs no new ADR - the decision is recorded here and in the commit.
2. Red: tests in tests/test_llm_chunking.py - a glossary correction on a stored run reaches transcript_text and the raw name does not; every word lands on exactly one line (a word in the silence between segments, one after the last segment); chunk.tokens still equals estimate_tokens(chunk.text) over corrected rows.
3. Green: chunking.segment_words partitions doc.words over doc.segments in one pass; segment_texts joins each partition with render.join_text and falls back to segment.text only when the run has no word rows; timestamped_line takes the text; segment_speakers counts over the same partition; chat_tool reads transcript_lines instead of timestamped_line per segment.
4. Run test_llm_chunking, test_llm_speakers, test_llm_tasks, test_llm_chat_tool sequentially; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decision: derive every LLM line from the run's words (already read through the correction layer by exports.doc), with Whisper's segments as boundaries only. No new ADR: ADR-003 already says groupings are derived from words at render time, and this adds no table and no cache. Red: test_a_line_says_what_the_corrected_words_say_not_what_whisper_wrote and test_every_word_reaches_exactly_one_line_even_outside_every_segment failed (Marvin still reached the model after a correction to Marvyn; the gap word was dropped). Green after chunking.segment_words/segment_texts and chat_tool reading transcript_lines: test_llm_chunking 32 passed, test_llm_speakers 43, test_llm_tasks 120, test_llm_chat 27. Observation, not fixed here: the chat tool still finds excerpts through segment_fts, which indexes Whisper's raw text, so a corrected name typed in a question may not match the FTS hit; the excerpt it renders is corrected.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
chunking.timestamped_line now takes the text; segment_texts derives it from the words inside each segment (render.join_text, corrections included) and falls back to segment.text only for a run without word rows; segment_speakers counts over the same partition; chat_tool renders transcript_lines. Verified: a stored glossary correction reaches transcript_text and the raw name does not, every word lands on exactly one line, chunk.tokens still equals estimate_tokens(chunk.text) - 222 tests green across the four LLM files.
<!-- SECTION:FINAL_SUMMARY:END -->
