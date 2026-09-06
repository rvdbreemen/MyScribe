---
id: TASK-006.07
title: 'llm: live provider smoke tests behind the gpu marker'
status: Done
assignee: []
created_date: '2026-09-02 16:32'
updated_date: '2026-09-03 07:33'
labels: []
dependencies: []
parent_task_id: TASK-006
ordinal: 43000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 Task 7. One gpu-marked test per configured provider sending a two-sentence transcript, skipping with a clear reason when unavailable, recording model and elapsed time to stage_perf under stage llm; plus a num_ctx honesty test for Ollama.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each live test either passes or skips with the reason
- [x] #2 Ollama either honours the requested num_ctx or raises ContextTooLong, never truncates silently
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by running tests/test_llm_live.py against the live daemons: 7 passed, 0 skipped, in 132.00s. All three configured providers answered for real - the parametrised test_a_configured_provider_summarises_a_two_sentence_transcript collected and passed as [ollama], [openai] and [openrouter] - so AC1's 'passes or skips with the reason' resolved to passing for every one. AC2 by the three num_ctx honesty tests: test_ollama_reads_the_whole_prompt_into_the_window_it_asked_for, test_ollama_refuses_a_prompt_that_overflowed_the_window_it_asked_for and test_ollama_refuses_an_overflowing_prompt_at_the_window_the_app_ships_with. The measurement is recorded in the module docstring: a 200 with prompt_eval_count exactly num_ctx/2+2 is how a silent truncation shows itself, and word_floor is what sees it.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Live provider smoke tests behind the marker. Verified by tests/test_llm_live.py: 7 passed, 0 skipped, 132s, with ollama, openai and openrouter all answering live. Ollama either honours the requested num_ctx or raises ContextTooLong; the truncation it would otherwise hide is caught by comparing prompt_eval_count against the window.
<!-- SECTION:FINAL_SUMMARY:END -->
