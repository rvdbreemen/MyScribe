---
id: "ADR-030"
title: "An OpenRouter call that asks no reasoning hint carries a reasoning limit of two fifths of its cap"
status: "Accepted"
date: "2026-10-24"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-020"
  - "ADR-035"
topics:
  - "openrouter"
  - "reasoning"
aliases:
  - "reasoning.max_tokens"
components:
  - "scribe/llm/openai_like.py"
symbols:
  - "reasoning_cap_for"
  - "_names_reasoning"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-030 An OpenRouter call that asks no reasoning hint carries a reasoning limit of two fifths of its cap

## Status

Accepted, 2026-10-24.

## Status History

```yaml
status_history:
  - date: 2026-10-24
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-24
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: ADR-030 amends one ADR-020 Must for OpenRouter's reasoning limit
    changed_via: adr-kit lifecycle
  - date: 2026-10-24
    status: Accepted
    changed_by: Robert van den Breemen (said yes in session 2026-10-24; recorded by Claude)
    reason: Robert accepted ADR-030 in the session of 2026-10-24, with ADR-020's conflicting Must, the cleanup exclusion and the AC3 rerun in front of him
    changed_via: adr-kit lifecycle
  - date: 2026-10-25
    status: Accepted
    changed_by: Claude (agent, session 2026-10-25)
    reason: ADR-035 scopes the combine hint to local models (TASK-124)
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-020 has a Must: "a request without the hint carries none of `reasoning`,
`reasoning_effort` or `think`". On 2026-10-22 three jobs on media 61 (2h28m),
all `openrouter/auto`, came back with no text (TASK-123). Job 423, speakers:
12,842 of 16,000 completion tokens were reasoning. Job 426, cleanup: the hint
was refused, and the call without it spent 6,000 of 6,000. Job 428, minutes:
4,000 of 4,000, and the hint retry of ADR-023 was refused. The router picks a
model per request, so no single model setting fixes this.

OpenRouter documents `reasoning.max_tokens` (read 2026-10-24, not measured).
It "directly specifies the maximum number of tokens to use for reasoning". On
effort-only models it "will be used to determine the effort level"; "low" is
about 20% of the cap, "medium" about 50%. Providers "will ignore unknown
parameters" unless `require_parameters` is set.

## Decision Drivers

* An empty answer spends the time and keeps nothing.
* ADR-020's Must Not forbids `effort: low` for cleanup, the kind that must keep `[m:ss]` stamps and speaker labels.
* A call that succeeded before the limit must not fail because of it.

## Considered Options

* **A.** Send `reasoning.max_tokens` on every OpenRouter call without the hint, cleanup included after a refused hint. It would bound job 426 too.
* **B.** As A, but never on cleanup: a hinted request carries no limit, also after its hint is dropped.
* **C.** Keep ADR-020 whole and raise the caps.

## Decision Outcome

Chosen option: **B**, decided by Robert on 2026-10-24. Two fifths lies
between "low" and "medium", so an effort-only model may map it to `low`,
which ADR-020 forbids for cleanup. C costs more on every call, and the router
can still pick a model that spends it all.

This amends one ADR-020 Must and leaves its text unchanged: an OpenRouter
request without the hint carries the limit. The limit's 400 retry reads the
wording when no `param` came back; ADR-020's hint retry never does.

### Confirmation

Unit tests pin the rules below; no live call was made. TASK-123's AC3, a
rerun of jobs 423 and 428 on a copy of the library, decides whether it works.

## Decision Contract

### Must

* An OpenRouter request without the hint carries `reasoning: {max_tokens: N}` with N = cap * 2 // 5, alone in the object, when N is at least 1,024.
* A request with the hint never carries the limit: not on the first call, and not after a refused hint is dropped.
* A 400 that names `reasoning` while only the limit is on the wire is retried once without it; `param` decides when present, the wording otherwise; a context-length 400 is not retried.
* `ChatResponse.reasoning_cap` and the row's `params_json` record the limit the answering call carried, None when none.

### Must Not

* Send the limit beside the hint or beside `effort`.
* Send the limit to any provider but OpenRouter.
* Drop the limit on a 400 that does not name `reasoning`, or more than once.

### Exceptions

* A cap whose share is under 1,024 sends no limit: OpenRouter raises an Anthropic budget to 1,024 and wants the cap strictly above it.

### Verification

* `tests/test_llm_providers.py::test_openrouter_bounds_reasoning_to_a_share_of_the_answer_budget`, `::test_a_share_below_openrouters_floor_is_not_sent`, `::test_openai_sends_no_reasoning_limit`, `::test_the_hint_goes_alone_never_beside_the_limit`.
* `tests/test_llm_providers.py::test_a_refused_hint_falls_back_to_no_reasoning_field_not_to_the_limit`, `::test_a_400_naming_reasoning_with_only_the_limit_on_the_wire_is_retried_once_without_it`, `::test_a_400_that_names_no_reasoning_field_with_only_the_limit_on_the_wire_is_not_retried`, `::test_a_limit_refused_twice_is_two_calls_and_a_refusal`.
* `tests/test_llm_task_providers.py::test_a_cleanup_whose_hint_openrouter_refuses_goes_again_with_no_reasoning_limit`.
* `tests/test_llm_providers.py::test_a_context_length_400_naming_reasoning_with_the_limit_on_is_context_too_long_after_one_call`; `tests/test_llm_tasks.py::test_the_row_and_a_cut_off_refusal_name_the_reasoning_limit_sent`.

## Consequences

### Positive

* Speakers, minutes and the other unhinted kinds ask the router's model to leave room for the answer, and each row shows the limit that went out.

### Negative

* Cleanup is not helped: a job like 426, its hint refused, is still unbounded.
* A model whose thinking is off by default may now think within the limit; OpenRouter infers `enabled` from `max_tokens`. Not measured.
* A 400 that mentions reasoning for another reason costs one more unbilled 400. Gemini 3 maps the budget to a level, and an upstream that ignores it is not bounded.

## Open Questions

- [x] Does AC3's live rerun of jobs 423 and 428 on a library copy answer with the limit on the wire? — **Answered 2026-10-24 by Claude (agent, session 2026-10-24):** Yes, 2026-10-24 on a copy of media 61 with this branch: speakers (job 430) reasoning_cap 6400, reasoning_tokens 2717, finish stop; minutes (job 431) reasoning_cap 1600, reasoning_tokens 712, finish stop; both valid JSON. Auto routed both to openai/gpt-6-luna on Azure, not the Venice, BaseTen or Fireworks upstreams of jobs 423 and 428, so whether those honour the limit is not shown.

## Related Decisions

* ADR-020: amended - its Must "a request without the hint carries none of `reasoning`, ..." no longer holds for OpenRouter's limit; the rest holds.
* ADR-023: unchanged - an answer lost despite the limit is asked again with the hint, without the limit.

## References

* TASK-123; jobs 423, 426 and 428 on media 61 (2026-10-22).
* https://openrouter.ai/docs/use-cases/reasoning-tokens and https://openrouter.ai/docs/guides/routing/provider-selection, read 2026-10-24.
* `scribe/llm/openai_like.py`: `reasoning_cap_for`, `OpenRouterProvider._reasoning_limit`, `_refused_knob`, `_names_reasoning`.
