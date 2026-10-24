---
id: "ADR-029"
title: "An Ollama answer lost entirely to thinking is asked once more with think false"
status: "Accepted"
date: "2026-10-23"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-020"
  - "ADR-023"
  - "ADR-031"
topics:
  - "ollama"
  - "reasoning"
  - "thinking"
aliases:
  - "think false retry"
  - "answer lost to thinking"
components:
  - "scribe/llm/ollama.py"
symbols:
  - "OllamaProvider.complete"
  - "_lost_to_thinking"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-029 An Ollama answer lost entirely to thinking is asked once more with think false

## Status

Accepted, 2026-10-23.

## Status History

```yaml
status_history:
  - date: 2026-10-23
    status: Proposed
    changed_by: Claude (agent, session 2026-10-23)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-23
    status: Proposed
    changed_by: Claude (agent, session 2026-10-23)
    reason: Related to ADR-023
    changed_via: adr-kit lifecycle
  - date: 2026-10-23
    status: Proposed
    changed_by: Claude (agent, session 2026-10-23)
    reason: Related to ADR-020
    changed_via: adr-kit lifecycle
  - date: 2026-10-23
    status: Accepted
    changed_by: "User: Robert van den Breemen (via Claude, session 2026-10-23)"
    reason: Robert chose option A (retry with think:false) on 2026-10-23 and answered 'Ja, accepteer ADR-029'
    changed_via: adr-kit lifecycle
  - date: 2026-10-24
    status: Accepted
    changed_by: Claude (agent, session 2026-10-24)
    reason: ADR-031 bounds the Ollama window that ADR-029's retry also runs in
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-023 asks a cloud answer that was lost entirely to reasoning once more with
the reasoning hint, and excepts Ollama because "it has not failed this way".
On 2026-10-23 it did. On a copy of Robert's library, jobs 429-431 ran minutes,
blog and speakers on media 61 (2h28m) with the local `gemma4:12b`. Each came
back with empty content, `done_reason='length'`, and the whole budget in
`thinking`: 4000 tokens (12,530 characters), 4000 tokens (2,778 characters)
and 3119 tokens (8,652 characters). Nothing was stored. Robert's rule is that
AI work runs on a local Ollama model first, so local had to work.

An A/B on synthetic text, same prompt, `num_predict` 1500: without a `think`
key (what MyScribe sends today) 41.1 s, 750 tokens, 2008 characters of
thinking; with `think: false` 7.8 s, 148 tokens, no thinking, the same JSON.

## Decision Drivers

* An empty answer is the worst outcome: the time is spent and nothing is kept.
* The first request of every kind stays byte-identical (ADR-020).
* A retry must not become a loop or repeat the call that just failed.
* Ollama's hint (`think: false`) is never refused, unlike some cloud upstreams.

## Considered Options

* **A.** Ask once more with `think: false` when a request without the hint came back empty because all of it went into thinking - ADR-023's rule, applied to Ollama.
* **B.** Send `think: false` on every Ollama request.
* **C.** Send it on every request except `speakers`, which writes names.

## Decision Outcome

Chosen option: **A**, decided by Robert on 2026-10-23 with the three options
and the A/B in front of him. It is the smallest change, keeps ADR-020's first
request, and makes the provider consistent with ADR-023. B and C would save
the first attempt's thinking time, at the cost of reopening ADR-020's A/B rule.

### Confirmation

`tests/test_llm_ollama.py` pins the rules below. The live check is the same
three jobs on the library copy with the retry in place.

## Decision Contract

### Must

* Retry only when the request carried no hint and the answer has empty content, `done_reason` `length`, and non-empty `thinking`.
* Retry exactly once, with `reasoning_off` set (`think: false`), and record `hint_sent` true on the answer.
* When the retry gives no answer either, fail with a message that names what the first attempt spent and that the retry was made.

### Must Not

* Change the first request of any kind.
* Retry an answer that has text, one cut off without thinking, one that finished of its own accord, or a request that already carried the hint.

### Exceptions

* None.

### Verification

* `tests/test_llm_ollama.py::test_an_answer_lost_to_thinking_is_asked_once_more_with_think_false`
* `tests/test_llm_ollama.py::test_a_retry_that_is_empty_too_fails_naming_both_attempts_and_stops`
* `tests/test_llm_ollama.py::test_no_retry_for_an_answer_with_text_a_hinted_request_or_no_thinking`

## Consequences

* Good: a local thinking model that runs out of budget now answers instead of failing, and `hint_sent` shows when it did.
* Bad: the first attempt's thinking time is still spent - minutes to two minutes per job on a long recording - before the retry.
* Neutral: a model that thinks past any budget even with `think: false` still fails, now saying both attempts were made.

## Related Decisions

* ADR-023: amended - its Ollama exception no longer holds; this record is the Ollama half of the same rule.
* ADR-020: the first request of each kind stays as it was.

## References

* TASK-123; jobs 429-431 on a copy of Robert's library (2026-10-23); `scribe/llm/ollama.py` `OllamaProvider.complete`, `_lost_to_thinking`.
