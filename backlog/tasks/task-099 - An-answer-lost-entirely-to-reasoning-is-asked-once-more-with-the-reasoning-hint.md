---
id: TASK-099
title: >-
  An answer lost entirely to reasoning is asked once more with the reasoning
  hint
status: Done
assignee:
  - '@claude'
created_date: '2026-09-27 11:24'
updated_date: '2026-09-27 20:19'
labels:
  - llm
  - bug
dependencies: []
priority: high
ordinal: 173000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Jobs 418 and 419 (2026-09-27): the speakers pass on media 61 (Audio Terrein sessie, 2h28m, 22,875 words, 5 clusters) failed twice with 'openrouter answered with no message content (finish_reason=length, completion_tokens=8000, reasoning_tokens=8000/8003, upstream=BaseTen, no reasoning hint asked)'. openrouter/auto routed to a reasoning model that spent the whole 8,000-token cap thinking; speakers carries no reasoning hint by ADR-020 until an A/B. The recording keeps its transcript but its speakers stay unnamed. Decided with Robert: automatic recovery - when a call that asked no hint comes back with no text, finish_reason length and reported reasoning tokens, ask once more with the hint. This refines ADR-020 (speakers/labels get the hint only on a call that otherwise gave nothing), so a Proposed ADR records it for Robert to accept.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A call that asked no reasoning hint and came back with no text, finish_reason length and reported reasoning tokens is asked exactly once more with the hint; its answer is returned with hint_sent True
- [x] #2 No second call when the first had text, ended otherwise, reported no reasoning tokens, or already carried the hint; the first request is byte-identical to before
- [x] #3 If the retry is empty too, or its hint is refused, the error names both attempts and no third call is made
- [x] #4 A Proposed ADR refines ADR-020 with this rule; red first for each behaviour, mutants on a copy, the provider and task test files green
- [x] #5 The speakers pass for media 61 is re-run on the live library and its speakers get names, or the failure says what both attempts spent
- [x] #6 The speakers pass may answer in up to 16,000 output tokens (was 8,000), decided with Robert on 2026-09-27; the transcript budget per call still leaves room for the transcript
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red first: tests/test_llm_providers.py 4 failed, 57 passed (the retry tests), then 61 passed; the cap test failed at 8000 == 16000, then passed. LLM files green: test_llm_tasks 160, providers 61, chat 27, ollama 55, labels 19, chunking 32, privacy 19, cleaning_gate 31, test_web_ai 141. Mutants on a copy, 9 of 9 killed: never-retry, retry-a-hinted-call-too, finish-reason-ignored, reasoning-count-ignored, text-ignored, retry-drops-a-refused-hint, answer-not-marked-hinted, second-empty-not-named, cap-stays-8000. ADR-023 Proposed, related to ADR-020 via adr relate; adr-lint strict passes. Released as 0.7.2 with the documentation change.

Live check on 0.7.2 (installed over 0.7.1 on this laptop, 2026-09-27 22:14): POST /api/jobs/419/retry queued job 424 with the same params (openrouter/auto, speakers, run 156). Done in 77.4 s: llm_output 182, served_model z-ai/glm-5.3-flash at BaseTen, reasoning_tokens 11,218, completion_tokens 12,399, finish_reason stop, calls 1, hint_sent null. So the 16,000 cap answered it; the hint retry did not fire this time (it would have at 8,000). speaker_label for run 156: SPEAKER_00 Christel (93), SPEAKER_01 Rox (92); the other three clusters are not named by the model.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A cloud answer lost entirely to reasoning is asked once more with the reasoning hint (never a third time, first request unchanged), and the speakers pass may answer in 16,000 tokens. ADR-023 accepted by Robert, refining ADR-020. Verified: red first, LLM test files green, 9 of 9 mutants killed, CI green on three OSes (run 36346005628), released as 0.7.2, and on the live library the speakers pass that failed twice now named two of five speakers in one call (12,399 tokens).
<!-- SECTION:FINAL_SUMMARY:END -->
