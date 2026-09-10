---
id: TASK-029
title: >-
  Cleanup spends its output budget on reasoning: decide how the app talks to
  reasoning models
status: To Do
assignee: []
created_date: '2026-09-10 20:50'
labels:
  - llm
  - bug
dependencies: []
references:
  - scribe/llm/tasks.py
  - scribe/llm/openai_like.py
  - scribe/llm/ollama.py
priority: medium
ordinal: 70000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The cleanup kind fails with 'no message content (finish_reason=length)' on real transcripts. Measured 2026-09-10 on media 1 (WHYcast ep29, 5 min, clean Dutch, 837 words, one chunk) by sending the exact request the app builds straight to OpenRouter: openrouter/auto served deepseek/deepseek-v4-flash-0731 twice; both answers were good cleanings (97% and 90% of the words, finish 'stop') but spent 22,515 and 21,512 reasoning tokens of 23,918 and 22,589 completion tokens, in 63-84 s, straight through max_tokens=6000. With reasoning {enabled:false} the same model answered in 8.5 s with 1,171 tokens (0 reasoning) and 94% of the words, keeping the [m:ss] and speaker labels. With reasoning {effort:low} it took 7.9 s and 1,417 tokens but dropped the timestamps and labels - a reason not to reach for effort:low. A model that counts reasoning against the cap (gpt-5.6-luna, served for media 12's chunks) hits 'length' instead: media 12 chunk 0 succeeded with 4,946 of 6,000 tokens, leaving ~1,000 for thinking, and the later chunks did not. The earlier conclusion recorded at the concat-budget comment in tasks.py ('the model runs away on this prompt whatever it is given') rested on media 7, whose transcript is a 112-word 'La, la' loop, and on these reasoning exhaustions; it is corrected there. The prompt works. What is open is a design decision: a per-kind reasoning hint in TaskSpec mapped per provider (OpenRouter reasoning.enabled, OpenAI reasoning_effort, Ollama think), and/or cutting concat chunks to a fraction of the output cap so reasoning has room. Only one provider and one model are measured; auto-routing does not reliably hand out the model that failed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A decision is recorded on how cleanup (and other mechanical kinds) limit reasoning, per provider
- [ ] #2 Cleanup of media 1 and media 12 through run_task succeeds against openrouter/auto and the local Ollama default, each run's served model, reasoning tokens and ratio reported
- [ ] #3 A model that ignores a reasoning hint still cannot exhaust a chunk's cap on reasoning alone (headroom measured, not assumed)
<!-- AC:END -->
