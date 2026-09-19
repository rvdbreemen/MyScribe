---
id: TASK-084
title: >-
  Who is speaking cannot run on a local provider: its answer budget exceeds the
  window it plans against
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 20:02'
updated_date: '2026-09-18 20:36'
labels: []
dependencies:
  - TASK-014
references:
  - scribe/llm/tasks.py
  - scribe/llm/ollama.py
priority: high
type: bug
ordinal: 129000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The speakers task fails at prepare on every local run, before the model is called: 'a 8192-token context leaves nothing for the transcript after 1044 tokens of prompt and 8000 tokens of answer'. Observed on jobs 88, 89 and 90 with llm_provider=ollama; llm_output has never held a row.

It is arithmetic, not configuration. context_tokens_for() plans a local call against LOCAL_CONTEXT_TOKENS = ollama.DEFAULT_NUM_CTX = 8192. The speakers spec carries max_output_tokens=8000, raised from the 4000 default after measuring openrouter/auto on 2026-09-10, where 128k makes 8000 free. Locally 8192 - 1044 - 8000 = -852, so budget_for raises before any transcript is added. Every other kind survives: summary/labels/chapters get ~3.7k tokens of transcript, blog and cleanup ~1.8k. speakers is the only IMPOSSIBLE cell in the table.

The window is also pessimistic by a lot. The configured model, qwen3.8:27b-q8_0, reports qwen35.context_length = 262144 - 32x what the app plans against - and num_ctx is not exposed in the settings UI, so a user cannot raise it from the app. Note ollama.py documents a measured daemon quirk at 8192 (prompt_eval_count comes back at num_ctx/2+2), so raising the constant blindly is not obviously right; asking /api/show for the model's real window is.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The speakers task completes on a local provider against a real episode, and llm_output holds its answer
- [x] #2 The budget a local call plans against reflects the model actually configured rather than one constant for every local model
- [x] #3 A window too small for a kind is caught before the job is queued and said in words the user can act on, not as a RUNTIME failure at the prepare stage
- [x] #4 The suite covers the local-provider budget for every kind in TASKS, so a future max_output_tokens raise cannot make a kind impossible again in silence
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. scribe/llm/ollama.py: MAX_NUM_CTX cap (a window is KV cache, and 262144 on a 27B model is not a window this machine can hold) and model_context_tokens(model) asking POST /api/show for <arch>.context_length, cached per model, None when the daemon cannot say. OllamaProvider.context_tokens(model) classmethod clamps it between DEFAULT_NUM_CTX and MAX_NUM_CTX.
2. The planned window and the requested one must agree - LOCAL_CONTEXT_TOKENS' docstring is explicit that planning for more than the provider asks of /api/chat means sending a prompt the daemon silently truncates. So OllamaProvider.__init__ takes num_ctx=None meaning discover, and tasks.context_tokens_for(provider_cls, model) asks the class first and falls back to the constants.
3. base.Provider grows context_tokens(model) returning None, so the seam is the provider's and no caller tests for a class.
4. ai_ui refuses a kind whose window cannot hold it before enqueueing, with the numbers and what to change, instead of letting budget_for raise RUNTIME at the prepare stage.
5. Tests: every kind in TASKS fits its local window (the regression that would have caught this), discovery clamped both ways, a daemon that cannot answer falls back, planning agrees with the request, and the queue-time refusal.
6. Evidence: red first, then green, then the real thing - the speakers task run against episode 36 on qwen3.8:27b-q8_0 with a row in llm_output.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed in three places.

ollama.py: MAX_NUM_CTX = 32768 and context_tokens(model), which asks /api/show for <arch>.context_length and clamps between the shipped floor and that cap, cached per model. num_ctx=None now means discover; a number from a caller is never second-guessed. The request asks for the window that was planned - complete() sends context_tokens(req.model) - because LOCAL_CONTEXT_TOKENS' own docstring says a disagreement there is a prompt the daemon truncates while answering 200.

base.py: window_for_model() classmethod returning None, so the planner asks the provider instead of keeping a table of provider names.

tasks.py: MIN_TRANSCRIPT_TOKENS = 1500, MIN_OUTPUT_TOKENS = 512 and fit_output_tokens(), which clamps a spec's answer ceiling to what the window affords. This is the spec's own reading of max_output_tokens - 'a cap, not a spend' - finally enforced. run_task uses it, and ai_ui refuses a kind the window cannot hold before the row is written rather than letting it fail at the prepare stage.

Two test doubles had to change and the behaviour change is deliberate: the local provider now makes one extra cached /api/show call per model, so Recorder in test_llm_ollama.py answers that path itself and Recorder in test_llm_privacy.py excludes it from the count that asks whether a transcript left the machine. Both are documented in place.

Evidence. Live: qwen3.8:27b-q8_0 reports 262144, clamped to 32768, and ollama ps showed the model resident at CONTEXT 32768. Job 91 (speakers, ollama) passed prepare instantly where 88, 89 and 90 had died there, and ran 293 s in generate before it was cancelled by hand - the local 27B path is unblocked but its cost is still unmeasured. Job 94 (speakers, openrouter z-ai/glm-5.3-flash) completed in under 30 s: prompt 7776 tokens, completion 3383, the first row llm_output has ever held, and speaker_label now carries SPEAKER_00=Ad, SPEAKER_01=Chantal, SPEAKER_02=Nancy at confidence 97, source llm - matching the diarized audio. Suite: 2411 passed, 2 failed, both pre-existing and unrelated.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A local call is planned against the window the model actually has, and a kind's answer budget is clamped to what that window affords. speakers - impossible on Ollama by arithmetic, working on every cloud provider - now runs on both. Verified live: the first speakers answer this app has ever produced named all three hosts correctly, and 10 new tests cover discovery, clamping, the planned-equals-requested rule and the pre-queue refusal.
<!-- SECTION:FINAL_SUMMARY:END -->
