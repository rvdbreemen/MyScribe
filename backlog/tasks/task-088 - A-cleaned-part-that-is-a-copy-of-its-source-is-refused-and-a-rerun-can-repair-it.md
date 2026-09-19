---
id: TASK-088
title: >-
  A cleaned part that is a copy of its source is refused, and a rerun can repair
  it
status: To Do
assignee: []
created_date: '2026-09-19 20:09'
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
- [ ] #1 A part that is byte-identical to its source is refused when that source has a line continuing the speaker before it, proven by a test that fails on today's code
- [ ] #2 A part that is honestly unchanged - one line, or alternating speakers - still passes; the refusal keys on the continuing line in the source, not on sameness alone
- [ ] #3 A rerun on the same provider, model and prompt version does not reuse the stored parts of a reading that was refused, so the refusal can be repaired without emptying the cache
- [ ] #4 tests/test_llm_cleaning_gate.py:145 is updated rather than deleted, and its new name says what it now pins
<!-- AC:END -->
