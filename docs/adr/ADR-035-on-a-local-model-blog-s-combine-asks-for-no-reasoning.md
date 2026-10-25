---
id: "ADR-035"
title: "On a local model, blog's combine asks for no reasoning"
status: "Proposed"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-020"
  - "ADR-030"
topics:
  - "reasoning"
  - "ollama"
  - "map-reduce"
aliases:
  - "combine reasoning off"
  - "blog combine think false"
components:
  - "scribe/llm/tasks.py"
symbols:
  - "TaskSpec.combine_reasoning_off"
  - "TaskPlan.provider_is_local"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-035 On a local model, blog's combine asks for no reasoning

## Status

Proposed, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: ADR-035 scopes the combine hint to local models (TASK-124)
    changed_via: adr-kit lifecycle
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: ADR-035 scopes the combine hint to local models (TASK-124)
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

A blog post on a long recording is made in two steps: notes per chunk, then
one combine call that writes the post from the notes. On media 61 (2h28m,
Dutch) with `qwen3.5:4b` on Ollama 0.40.8, the combine sometimes spent its
whole 6000-token cap thinking. ADR-020 makes the reasoning hint a per-kind
setting on every call; ADR-030 gives an OpenRouter call without the hint a
reasoning limit of two fifths of its cap. Neither covers one call of a kind.

The A/B (`run-124/r6/ab-summary.txt`, library copies, TASK-124):

* Without the hint, each run on its own notes: job 430 in round 4 done
  (5057 completion tokens); job 430 in round 5 failed at the cap after
  16,866 characters of thinking.
* With `think: false` on the combine: jobs 430-432 in round 6 all done,
  1599-1996 completion tokens, no thinking, 23-31 s each.
* Confound: the three hinted combines shared one set of notes (prompt 2731
  tokens), so this is one input against two, not three independent runs.

Robert decided on 2026-10-25 that the hint applies on Ollama only, the one
provider it was measured on; OpenRouter keeps ADR-030's limit.

## Decision Drivers

* A combine lost to thinking costs every note call before it.
* On OpenRouter the hint replaces ADR-030's limit; that trade is unmeasured.
* One rule, read from the provider class, as `context_tokens_for` does.

## Considered Options

* **A.** Hint on the combine for every provider.
* **B.** Hint on the combine only where `Provider.is_local` is True.
* **C.** No hint; raise the blog cap instead.

## Decision Outcome

Chosen option: **B**. A changes the cloud call without a measurement; C buys
more thinking, which a local window pays for out of the transcript budget.
`TaskSpec.combine_reasoning_off` marks the kind (only `blog`), and
`TaskPlan.provider_is_local`, read from the provider class when the plan is
made, decides whether `_ask` sends it. Today Ollama is the only local
provider, on this machine or on the local network (ADR-024).

### Confirmation

The test below runs the same blog plan against a local and a cloud fake.

## Decision Contract

### Must

* Send `reasoning_off` on a `combine_reasoning_off` kind's combine call only
  when the provider is local.
* Keep the notes and a one-call answer at the provider's default.

### Must Not

* Send the combine hint to a cloud provider.
* Read locality from a provider name instead of `is_local`.

### Exceptions

* A kind with `reasoning_off` (cleanup) sends the hint on every call, on
  every provider, as ADR-020 decides.

### Verification

* `tests/test_llm_tasks.py::test_a_blog_asks_only_its_combine_not_to_reason_and_only_on_a_local_model`

## Consequences

* Good: the measured local failure is addressed, and the cloud request is as
  it was before the combine hint existed.
* Bad: a 4B model writes the post without planning it; its quality was read
  by eye, not scored.
* Bad: a cloud model that thinks too long in the combine is not helped.
* Neutral: another kind gets the hint only with its own measurement.

## Related Decisions

* ADR-020 (reasoning is a per-kind hint); ADR-030 (the OpenRouter reasoning
  limit this keeps); ADR-029 (the retry with `think: false`).

## References

* TASK-124; `run-124/r6/ab-summary.txt`; `scribe/llm/tasks.py` (`_ask`).
