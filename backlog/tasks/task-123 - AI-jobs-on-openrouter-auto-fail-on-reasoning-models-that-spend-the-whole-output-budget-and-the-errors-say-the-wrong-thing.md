---
id: TASK-123
title: >-
  AI jobs on openrouter/auto fail on reasoning models that spend the whole
  output budget, and the errors say the wrong thing
status: To Do
assignee: []
created_date: '2026-10-22T21:10:00Z'
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
- [ ] #1 A truncated answer (finish_reason=length) is reported as cut off at the token limit, with the model that answered
- [ ] #2 Robert chose how openrouter/auto is handled (reasoning controls, a warning, or not offered)
- [ ] #3 A rerun of a failed kind on media 61 with the chosen handling succeeds, shown with its job events
<!-- AC:END -->
