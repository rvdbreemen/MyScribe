---
id: TASK-016
title: 'OpenAI test fails: gpt-5.6 refuses temperature 0.0'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 19:20'
updated_date: '2026-09-06 19:26'
labels: []
dependencies: []
ordinal: 57000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The provider self-test against OpenAI with gpt-5.6 answered 400: "Unsupported value: 'temperature' does not support 0.0 with this model. Only the default (1) value is supported." (settings page, 2026-09-06 18:23). The OpenAI-compatible client sends temperature=0.0 on every request; newer OpenAI models reject any value but the default. Omit temperature for providers/models that refuse it, or retry once without it on that specific 400.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A self-test against gpt-5.6 answers instead of failing on temperature
- [x] #2 Ollama and OpenRouter requests still send the temperature they sent before, or the change is deliberate and noted
- [x] #3 A test covers the retry-without-temperature path with a fake 400
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. In OpenAILikeProvider.complete, catch a BadRequestError whose message names 'temperature' as unsupported and repeat the call once with the temperature key dropped; every other 400 maps as before. 2. Test with the fake transport: first answer is the live 400 text, second a completion; assert two calls, the second body without temperature. 3. Probe OpenAI gpt-5.6 from the settings page.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live probe with the .env key: gpt-5.6 answered 'ok' in 4.6s (was 400 before). tests/test_llm_providers.py + test_llm_selftest.py: 48 passed. Ollama and OpenRouter bodies untouched: only a 400 naming temperature triggers the retry. Commit follows 0d411f3.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
OpenAILikeProvider.complete retries once without temperature when a 400 names it as unsupported; other providers and other 400s are unchanged. Verified by two fake-transport tests and a live probe of gpt-5.6.
<!-- SECTION:FINAL_SUMMARY:END -->
