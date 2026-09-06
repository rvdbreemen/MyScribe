---
id: TASK-006.01
title: 'llm: provider seam with OpenAI and OpenRouter'
status: Done
assignee: []
created_date: '2026-09-02 16:32'
updated_date: '2026-09-03 07:34'
labels: []
dependencies: []
parent_task_id: TASK-006
ordinal: 37000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 Task 1. base.py Provider ABC with ChatRequest/ChatResponse and an error taxonomy (AuthError, RateLimited, ModelNotFound, ContextTooLong, BadResponse, Unreachable); openai_like.py one implementation for both, differing in base_url/env/default model; retry only on RateLimited and Unreachable. Plan: docs/superpowers/plans/2026-09-02-phase5-llm.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 401 maps to AuthError with no retry; 429 retries exactly 3 times
- [x] #2 A 200 carrying an error envelope or empty choices is BadResponse
- [x] #3 available() explains why a provider is not ready
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
OpenRouter route verified live on 2026-09-02 against the real API (key from the machine-wide OPENROUTER_TOKEN): GET /models returns 423 models across the openai/, anthropic/ and google/ namespaces plus :free ids; openai/gpt-4o-mini answered in 1.8s. anthropic/claude-3.5-haiku returns HTTP 404 'No endpoints found' while anthropic/claude-haiku-4.5 exists - a plausible model id is not a valid one, so the settings dropdown is populated from models() rather than free text. Default: openai/gpt-5.6-luna (1.05M context, $0.20/$1.20 per M). Key resolution: setting table, then OPENROUTER_TOKEN, then OPENROUTER_API_KEY.

Verified 2026-09-03 by tests/test_llm_providers.py, run with the five other phase 5 suites: 217 passed in 13.26s. AC1 by test_http_401_is_an_auth_error_and_is_never_retried and test_http_429_is_rate_limited_and_retried_exactly_three_times. AC2 by test_a_200_carrying_an_error_envelope_is_a_bad_response and test_a_200_with_no_choices_is_a_bad_response. AC3 by test_available_is_false_with_a_readable_reason_when_no_key_is_found and test_available_is_true_and_names_the_source_without_the_value, which also pins that the reason names the key's source and never its value. Every request runs through an httpx2.MockTransport handed to a real openai.OpenAI client, so the SDK's own status-to-exception mapping is what is being tested rather than a reimplementation of it.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Provider seam with OpenAI and OpenRouter, sharing one OpenAI-shaped transport that differs only in base URL, default model and attribution headers. Verified by tests/test_llm_providers.py on a fake socket: 401 is AuthError and is never retried, 429 retries exactly three times, a 200 carrying an error envelope or no choices is BadResponse, and available() explains itself without echoing the key.
<!-- SECTION:FINAL_SUMMARY:END -->
