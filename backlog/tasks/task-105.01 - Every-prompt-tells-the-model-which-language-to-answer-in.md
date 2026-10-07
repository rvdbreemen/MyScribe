---
id: TASK-105.01
title: Every prompt tells the model which language to answer in
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 20:57'
labels:
  - llm
dependencies: []
parent_task_id: TASK-105
ordinal: 191000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
SYSTEM and all templates are English and never mention run.language; a Dutch recording likely gets English summaries (not measured).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 SYSTEM (or each template) carries the run's language and a test pins it, red first
- [x] #2 A real run on a Dutch clip with a local model shows a Dutch summary, output quoted in the notes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
SYSTEM gains LANGUAGE_SYSTEM when the run's language is known: 'The transcript is in {name}: write your answer in {name}, whatever language these instructions are in.' answer_language(run) gives English for a translate run, the run's language otherwise, None when unknown (then nothing is added). TaskPlan.language carries it to all three system_for calls. PROMPT_VERSION 2 -> 3; the widened digest test (105.02) failed on this change until the bump was recorded - the guard doing its job. Red: test_a_dutch_recording_is_answered_in_dutch and test_a_translated_run_is_answered_in_english failed (no language in the system prompt); test_an_unknown_language_adds_nothing passed before and after. Green: test_llm_tasks 163, chunking 32, chat 27, web_ai 147, speakers 51, finalize 39, db 39, library 15, web_transcript 112. Real run, local Ollama, the same Dutch synthetic transcript (scratch copy of the layout library, not an audio clip), summary kind, reasoning switched off for the measurement: gemma4:12b without the line -> English ('This recording is an interview regarding the book...'), with it -> Dutch ('Deze opname is een interview over het boek...'). qwen3.5:4b answered in Dutch both times, so the review's guess holds for some models, not all. Outputs: scratchpad lang-run.txt, lang-run-gemma.txt. With reasoning ON (the summary default) both models thought past 4000 and qwen also past 16000 output tokens with no answer on this repetitive transcript - recorded as its own task.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Prompts name the transcript's language; measured: gemma4:12b went from an English to a Dutch summary of a Dutch transcript. PROMPT_VERSION is 3.
<!-- SECTION:FINAL_SUMMARY:END -->
