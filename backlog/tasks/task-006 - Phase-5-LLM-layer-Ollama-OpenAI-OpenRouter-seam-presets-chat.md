---
id: TASK-006
title: 'Phase 5: LLM layer (Ollama/OpenAI/OpenRouter seam + presets + chat)'
status: Done
assignee: []
created_date: '2026-09-01 20:11'
updated_date: '2026-09-03 07:42'
labels: []
dependencies:
  - TASK-003
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One chat() seam: OpenAI SDK for OpenAI+OpenRouter, native /api/chat for Ollama (num_ctx, keep_alive). Commercial default, private-mode pin forcing Ollama with visible leaves-your-machine indicator. Six preset outputs with pydantic schemas, map-reduce chunking as resumable jobs, chat-with-transcript with timestamp citations, GPU semaphore. Robustness: no model-name carryover on fallback, 200-OK error envelope detection. Spec section 6.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Summary generated via each of the three providers in a mocked test, one real smoke per configured provider
- [x] #2 Private-pinned file never sends text to a non-Ollama provider (test proves refusal)
- [x] #3 Chat answer cites timestamps that seek the player
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Closed 2026-09-03 with all seven subtasks Done. AC1 has two halves and both were run today. The mocked half was missing and is now tests/test_llm_task_providers.py (commit 7a08a9c, 6 passed): a summary runs through the real OpenAIProvider, OpenRouter and OllamaProvider classes on an httpx2.MockTransport, storing a schema-valid row and carrying the transcript into the posted body. The provider suites covered each transport alone and the task suite covered planning against a fake Provider; neither covered the join, which is where a provider's own response shape breaks a preset. Proved by mutation rather than by a first-try pass: reading 'thinking' instead of 'content' in ollama.py fails exactly the two ollama cases and leaves the four cloud ones green, and reading 'role' instead of 'content' in openai_like.py fails exactly the four cloud cases and leaves ollama green - both applied to a scratch copy, never to the repository. The live half is tests/test_llm_live.py: 7 passed, 0 skipped, 132s, with ollama, openai and openrouter all answering for real. AC2 by tests/test_llm_privacy.py (19 passed), refusal before the transport is touched. AC3 by test_llm_chat.py plus test_web_ai.py::test_a_citation_renders_as_a_link_that_seeks_the_player. Whole phase 5 suite run: 217 passed in 13.26s across the six offline LLM files, plus 83 passed in tests/test_web_ai.py.

Spot-checked the assertions rather than trusting the test names, since a name is a claim and not evidence. Read in full: test_llm_tasks.py:637 (interrupts after two chunk rows are stored, resumes, and asserts len(resumed_calls) == len(chunk_rows) - 2 + 1, so the two already-bought chunks were reused and only the missing ones plus the reduce were paid for); test_llm_chat.py:247 (segment_ids == [3, 4, 5] - the hit with one neighbour either side in time order, the far end of the recording absent, and a GAP_MARKER so the model is told something is missing); test_web_ai.py:380 (a stored summary appears in the GET /media/{id} body); test_web_ai.py:1044 (the private default is posted through /settings/llm and read back through media.private_default, with test_a_recording_added_while_the_default_is_on_opens_with_the_cloud_disabled proving the stored default reaches a new recording). All four assert what their names claim.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Phase 5 complete: one chat() seam over OpenAI, OpenRouter and Ollama, six preset outputs as resumable map-reduce jobs, chat with seeking citations, and a fail-closed private-mode pin. Verified offline (217 passed across six LLM suites, 83 in the AI web suite) and live (7 passed, 0 skipped, all three providers answering). The one real gap found while closing out - no mocked summary through the real provider classes - was filled by tests/test_llm_task_providers.py and proved to bite by mutating each provider's answer extraction in a scratch copy.
<!-- SECTION:FINAL_SUMMARY:END -->
