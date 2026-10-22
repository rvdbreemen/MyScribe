---
id: TASK-123
title: >-
  AI jobs on openrouter/auto fail on reasoning models that spend the whole
  output budget, and the errors say the wrong thing
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T21:10:00Z'
updated_date: '2026-10-22T23:09:00Z'
labels:
  - llm
  - bug
dependencies: []
priority: high
ordinal: 237000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-10-22, jobs 423-428 on media 61 (read-only from his job and job_event rows). Every job had provider openrouter, model openrouter/auto: OpenRouter's Auto Router picks a model per request, which is why the upstreams differ per failure (Venice, BaseTen, Fireworks) and why 424 and 425 succeeded on the same recording. 423 speakers: finish_reason=length, completion 16000 of which reasoning 12842, no content. 426 cleanup (22 chunks, 3000-token budget each): chunk 1 answered, chunk 2 length with completion 6000 = reasoning 6000, reasoning hint refused. 428 minutes: completion 4000 = reasoning 4000, asked again with the hint, refused. 427 blog: finish_reason=length, completion 6000, visible reply 4292 chars cut off mid-string, so not valid JSON - but the error says 'the model did not answer with a JSON object', which reads as a model that ignored the format; json.loads also stops at a literal control character at char 700 (a raw newline inside a string). Causes: (1) an auto-routed reasoning model burns the max_tokens budget on hidden reasoning; (2) the messages hide that: a truncated answer is reported as non-JSON, and the model that actually answered is never recorded. Options to decide: record the answering model (OpenRouter returns it) in llm-reply; say 'cut off at the token limit' when finish_reason=length; parse JSON with strict=False; for openrouter/auto send OpenRouter's reasoning controls or warn in Settings that Auto picks models per request; raise max_output_tokens for reasoning models.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A truncated answer (finish_reason=length) is reported as cut off at the token limit, with the model that answered
- [ ] #2 Robert chose how openrouter/auto is handled (reasoning controls, a warning, or not offered)
- [ ] #3 A rerun of a failed kind on media 61 with the chosen handling succeeds, shown with its job events
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Code (branch fix/llm-cutoff-and-model): _refuse_a_cut_off_answer for schema kinds in the single call and the combine ('ran out of room ... N-token cap'); _spent, openai_like _spend and the no-content message name model=; llm-reply event adds model, reasoning_tokens, upstream; repair_json strict=False and '(json: <error>)'; Ollama _refuse_a_prompt_that_cannot_fit and _answer compare against context_tokens(model) instead of num_ctx (red first: 'window asked for is 8192' for a 9000-word prompt with a measured 32768 window). Tests red then green: 5 in test_llm_tasks, 1 in test_llm_providers, 1 in test_llm_live_log, 1 in test_llm_ollama; all test_llm*/test_web_ai*: 794 passed. Not yet: the live gemma4:12b run on a copy of the library (after the installer test frees the GPU), and Robert's choice for openrouter/auto.

2026-10-23 live runs on a copy of Robert's library (media 61, 2h28m), evidence in scratchpad llmcopy/evidence-jobs-*.txt; the copy is deleted. gemma4:12b without the retry (jobs 429-431): all three empty, budget spent on thinking (4000/12530 chars, 4000/2778, 3119/8652). A/B on synthetic text: no think key 41.1 s, 750 tokens; think false 7.8 s, 148 tokens, same JSON -> ADR-029 (Robert: retry with think:false; accepted). gemma4 with the retry took 34 min for one note on a GPU shared with qwen3-embedding:4b and qwen3.5:4b; Robert switched to qwen3.5:4b. qwen3.5:4b (jobs 435-437): blog and speakers report 'reasoning hint sent' although neither sends the hint first (TaskSpec.reasoning_off False) - the ADR-029 retry fired live and answered with text. CORRECTION: item 5 of this branch (Ollama guards against context_tokens) is reverted. With it, minutes job 435 ended 'done' on truncated notes: two notes of 87,180 and 89,374 chars both read prompt_eval_count 16386 = 32768/2+2, the truncation signature. The old comparison against 8192 refuses such prompts, which is honest; the real fix is TASK-124. Blog 436 then ran out of window (32,016 + 752 = 32,768), speakers 437 copied the transcript into its notes (a 4B model's quality, not a code defect).
<!-- SECTION:NOTES:END -->
