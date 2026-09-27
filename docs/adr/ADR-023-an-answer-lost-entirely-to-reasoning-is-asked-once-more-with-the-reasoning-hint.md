---
id: "ADR-023"
title: "An answer lost entirely to reasoning is asked once more with the reasoning hint"
status: "Accepted"
date: "2026-09-27"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-020"
topics:
  - "llm"
  - "reasoning"
  - "speakers"
aliases:
  - "reasoning retry"
  - "empty answer retry"
  - "lost to reasoning"
components:
  - "scribe.llm.openai_like"
symbols:
  - "_ask_again_with_the_hint"
  - "_lost_to_reasoning"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-023 An answer lost entirely to reasoning is asked once more with the reasoning hint

## Status

Accepted, 2026-09-27.

## Status History

```yaml
status_history:
  - date: 2026-09-27
    status: Proposed
    changed_by: Claude (agent, session 2026-09-27)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-27
    status: Proposed
    changed_by: Claude (agent, session 2026-09-27)
    reason: Related to ADR-020
    changed_via: adr-kit lifecycle
  - date: 2026-09-27
    status: Accepted
    changed_by: "User: Robert van den Breemen (via Claude, session 2026-09-27)"
    reason: Robert accepted it on 2026-09-27 when the ADR-020 check blocked the 0.7.2 commit
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-020 gives `speakers` and `labels` no reasoning hint until an A/B on a
database copy, because they run unattended and `speakers` writes names. On
2026-09-27 jobs 418 and 419 ran the speakers pass on media 61 (2h28m, 22,875
words, five clusters) through `openrouter/auto`. Both came back with no text:
`finish_reason='length'`, `completion_tokens=8000`, `reasoning_tokens=8000`
and `8003`, upstream BaseTen, no hint asked. The whole cap went into thinking,
nothing was stored, and the recording kept unnamed speakers. The cap had
already been doubled from 4,000 for the same failure (2026-09-10).

## Decision Drivers

* An empty answer is the worst outcome: the call is paid and nothing is kept.
* The first request of every kind must stay byte-identical to before (ADR-020).
* A retry must never become a loop or repeat the call that just failed.

## Considered Options

* **A.** Ask once more with the reasoning hint when a call that asked none came back empty because of reasoning.
* **B.** Only raise the cap further.
* **C.** Give `speakers` the hint on every call, as `cleanup` has.

## Decision Outcome

Chosen option: **A, together with a higher cap for `speakers` (16,000 output
tokens, from 8,000)**, decided by Robert on 2026-09-27. The cap gives a model
that needs to think more room to answer; the retry catches a model that
thinks past any cap. Option C stays ADR-020's A/B: the hint is used only on a
call that otherwise gave nothing, so names from a hinted call replace no
names, never names from an unhinted one.

### Confirmation

`tests/test_llm_providers.py` pins each rule below, and nine mutants of it on
a copy were each caught (TASK-099). Re-running the speakers pass for media 61
is the live check.

## Decision Contract

### Must

* Retry only when the request carried no hint and the answer has no text, `finish_reason` `length`, and reported reasoning tokens above zero.
* Retry exactly once, with `reasoning_off` set, and store the answer with `hint_sent` true.
* When the retry's hint is refused, or its answer is empty too, fail with a message that names what both attempts spent.

### Must Not

* Change the first request of any kind.
* Drop the hint on the retry: without it the retry is the failed call again.
* Retry an answer that has text, one cut off without reported reasoning, or an error envelope.

### Exceptions

* The Ollama provider is outside this record; its thinking is measured in characters and it has not failed this way.

### Verification

* `tests/test_llm_providers.py::test_an_answer_lost_to_reasoning_is_asked_once_more_with_the_hint`
* `tests/test_llm_providers.py::test_a_retry_whose_hint_is_refused_stops_without_a_third_call`
* `tests/test_llm_tasks.py::test_the_speakers_pass_may_answer_in_16000_tokens_against_a_cloud_window`

## Consequences

### Positive

* A long recording gets its speakers named instead of failing twice.
* ADR-020's rule for the first call stands, and every row still says whether the hint was sent.

### Negative

* A heavy reasoner can cost up to the cap plus one hinted call. The hinted call is short when the hint is honoured (ADR-020, case 1).
* Names written after a retry come from a model told not to reason; the A/B that ADR-020 asks for is still owed.

## Pros and Cons of the Options

### A

* Good, because it turns an empty, paid call into an answer at the cost of one short call.
* Bad, because the names then come from a hinted call, which has not been A/B-tested for `speakers`.

### B

* Good, because it changes nothing about how the model is asked.
* Bad, because nobody knows how much is enough: 8,000 was not, and a model can think past any cap.

### C

* Good, because it bounds every call.
* Bad, because it is ADR-020's open A/B, decided without a measurement.

## Open Questions

- [x] Does Robert accept this record, and with it the retry for `speakers` and `labels` before ADR-020's A/B? — **Answered 2026-09-27 by User: Robert van den Breemen (via Claude, session 2026-09-27):** Yes. Accepted by Robert on 2026-09-27 in the session, when the ADR-020 check blocked the 0.7.2 commit: the retry with the hint applies to speakers and labels too, and ADR-020's A/B stays owed for the hint on every call.

## Related Decisions

* ADR-020 (refined: the first call follows it unchanged; this record adds what happens after an empty answer).

## References

* `scribe/llm/openai_like.py` (`OpenAILikeProvider._ask_again_with_the_hint`, `_lost_to_reasoning`).
* `scribe/llm/tasks.py` (`TASKS["speakers"].max_output_tokens`).
* TASK-099; jobs 418 and 419 in the library of 2026-09-27.
