---
id: TASK-063
title: >-
  Every LLM task reads Whisper's raw segment text, bypassing the correction
  layer
status: To Do
assignee: []
created_date: '2026-09-16 15:06'
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
- [ ] #1 A decision is recorded: either an ADR that says how the LLM layer derives its text, or an accepted note that the raw segment text is deliberate
- [ ] #2 If the decision is to derive from words, timestamped_line reads the corrected text and a test pins a corrected name reaching the model
- [ ] #3 The chunking size estimate still matches what is actually sent
<!-- AC:END -->
