---
id: "ADR-010"
title: "Reasoning is a per-kind hint; an ignored hint is bounded only where the endpoint enforces the cap"
status: "Proposed"
date: "2026-09-11"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "llm"
  - "reasoning-models"
  - "output-budget"
  - "provenance"
aliases:
  - "reasoning hint"
  - "reasoning_off"
  - "think false"
  - "reasoning effort none"
  - "finish_reason length"
  - "cap not enforced"
  - "cleanup chunk size"
components:
  - "scribe.llm.base"
  - "scribe.llm.openai_like"
  - "scribe.llm.ollama"
  - "scribe.llm.tasks"
symbols:
  - "ChatRequest.reasoning_off"
  - "Provider.reasoning_off_body"
  - "ChatResponse.hint_sent"
  - "TaskSpec.reasoning_off"
  - "reasoning_record"
  - "CONCAT_CHUNK_TOKENS"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-010 Reasoning is a per-kind hint; an ignored hint is bounded only where the endpoint enforces the cap

## Status

Proposed, 2026-09-11. Written with the TASK-029 implementation; a human
accepts it. The step-0 headroom measurement it names is not taken yet (Open
Questions).

## Status History

```yaml
status_history:
  - date: 2026-09-11
    status: Proposed
    changed_by: Claude Opus 5 (agent, session 2026-09-11)
    reason: Initial proposal
    changed_via: adr-kit
```

## Context and Problem Statement

A reasoning model thinks in hidden tokens before it writes its answer, and
most endpoints count those tokens against the same output cap
(`max_tokens`) as the answer. MyScribe's cleanup kind rewrites a transcript
chunk by chunk, so its answer is as long as its input, and it failed with
"no message content (finish_reason=length)" on real recordings. Measured on
2026-09-10 and 2026-09-11 (TASK-029):

* Media 1 (837 Dutch words, one chunk) through `openrouter/auto`:
  deepseek-v4-flash-0731 spent 22,515 and 21,512 reasoning tokens of 23,918
  and 22,589 completion tokens, 63-84 s, and came back 'stop' - past the
  6,000 cap. With OpenRouter's `reasoning: {enabled: false}`: 1,171 tokens,
  0 of them reasoning, 8.5 s, 94% of the words kept, stamps and speaker
  labels intact. With `effort: low`: 1,417 tokens, but the `[m:ss]` stamps
  and speaker labels were dropped.
* Media 12's chunk 0 succeeded on gpt-5.6-luna with 4,946 of 6,000 tokens;
  later chunks ended 'length'. The speakers kind hit the same failure in the
  startup sweep (job 154, cap 8,000).
* Rows 6, 17 and 19 spent 5,651 (cap 4,000), 12,767 and 21,401 (cap 8,000),
  all deepseek, all 'stop': some upstreams do not enforce the cap at all.
* `openrouter/auto` has no `reasoning` object in `/models`, so whether the
  model it picks has mandatory reasoning, or what its default effort is,
  cannot be read before the call. All 28 deepseek-v4-flash-0731 upstreams
  list `max_tokens` in `supported_parameters`, so enforcement cannot be read
  there either.

The question is how the app talks to reasoning models, per provider, and what
bounds the spend when a model ignores what it is asked.

## Decision Drivers

* No caller may branch on the provider (the rule `scribe/llm/base.py` states
  at its top): the provider-specific part has to live in the provider classes.
* A mechanical kind should not pay for reasoning it does not need.
* An answer cut off at the cap must never be stored as if it were whole.
* What a call spent, and whether the cap held, has to be visible on the row,
  because the failure modes above were invisible until measured by hand.
* The pattern has to work when the model is chosen per request by a router.

## Considered Options

* **A. A per-kind hint, mapped per provider; send, detect, bound.**
* B. A reasoning effort ladder (`enabled:false`, then `minimal`, then drop).
* C. Raise the cloud cleanup cap (for example to 32,000).
* D. `provider.require_parameters: true` on OpenRouter.
* E. Do nothing: rely on TASK-026's length gate alone.

## Decision Outcome

Chosen option: **A**. `TaskSpec.reasoning_off` becomes
`ChatRequest.reasoning_off` on every call a kind makes, and each provider
class turns it into its own wire field (`Provider.reasoning_off_body`):
OpenRouter `reasoning: {enabled: false}` (sent through the `extra_body` of
the OpenAI SDK (software development kit)), OpenAI `reasoning_effort:
"none"`, Ollama `think: false`. What bounds a model that ignores the hint
depends on the endpoint, and the decision says so rather than promising more:

```text
                         hint honoured?
                   yes /              \ no (refused and dropped, or ignored)
                      /                \
         case 1: the hint           endpoint enforces max_tokens?
         is the bound             yes /                   \ no
                                     /                     \
                    case 2: reasoning + answer       case 3: only the hint
                    stay under the cap; a heavy      bounds it; the call can
                    reasoner ends 'length' and is    return 'stop' past the
                    refused before it is stored      cap, and the row says so
```

1. **Hint honoured: the hint is the bound.** Measured at 0 reasoning tokens:
   deepseek-v4-flash-0731 via `openrouter/auto` (3 calls, upstream Wafer
   where recorded); openai/gpt-5.6-luna via OpenRouter (upstream Azure; 107
   reasoning tokens on the same puzzle without the hint); gpt-5.6 on
   api.openai.com; qwen3.5:4b (0 thinking characters - re-measured through
   the working tree's `OllamaProvider` on 2026-09-11: 107 tokens and a
   correct cleaning with the hint; all 600 tokens spent thinking and no
   answer without it).
2. **Hint refused or ignored, and the endpoint enforces the cap:** reasoning
   plus answer stay under `max_output_tokens`. A light reasoner fits in the
   chunk reserve (`CONCAT_CHUNK_TOKENS`); a heavy one ends 'length', which
   every cleanup path refuses before storing; the spend is at most the cap
   per chunk, and the first failed chunk ends the run.
3. **Hint refused or ignored, and the endpoint does not enforce the cap:**
   the hint is the only bound. The call returns 'stop' past the cap and the
   cleaning gate still judges the text. Seen without a hint (rows 6, 17, 19
   and the 2026-09-10 calls); never seen with the hint. Every row records
   `hint_sent`, `reasoning_tokens`, `upstream`, `hint_ignored` and
   `cap_not_enforced`, so this case shows up when it happens.

**A refused hint.** Any HTTP (Hypertext Transfer Protocol) 400 - the
status a server gives a request it will not serve - while the hint is on the
wire gets one more call without it, except the temperature refusal (checked first, by
`exc.param`) and a context-length refusal. The rule is structural - it never
matches vendor wording - because the three refusals measured share none:
OpenRouter's "Reasoning is mandatory for this endpoint and cannot be
disabled." (google/gemini-3.5-flash-lite), gpt-4o-mini's "Unrecognized request
argument supplied: reasoning_effort" (the OpenAI provider's default model),
and gpt-5 / gpt-6-astra's `unsupported_value`. Each knob is dropped at most
once, so a call is at most three requests. After the drop the model reasons at
its own default and case 2 or 3 applies; the row says `hint_sent=false`.

**Per kind.** `cleanup`: hint on. `speakers` and `labels`: no hint yet - they
are queued without a person (finalize, the startup sweep) and `speakers`
writes names unattended, so they get the same mappings only after an A/B on a
database copy. Every other kind, chat and the provider self-test: no hint, and
byte-identical requests to before. `effort: low` or `minimal`: used nowhere.

**Chunks.** A cleanup chunk is at most `CONCAT_CHUNK_TOKENS` = 3,000
estimated tokens, its own constant so a later cap change buys reasoning room
rather than bigger chunks. Derived: a part at the gate ceiling is about 1.07 x
its chunk, so a 5,999-token chunk needs about 6,420 tokens from a 6,000 cap,
while a 3,000 chunk needs about 3,210 and leaves about 2,790 for reasoning.
This ships only if step 0 passes (Open Questions); otherwise sizing stays
`min(budget, cap)` and this record says chunk size buys no headroom.

### Confirmation

* The suite (Verification below), run in halves on 2026-09-11: 1,174 and 811
  passed.
* `scripts/task029_headroom.py` (step 0) and `scripts/task029_acceptance.py`
  (AC2), both run with `--fake` on 2026-09-11; their live runs are pending.

## Decision Contract

### Must

* Each provider class declares the hint's wire form in `reasoning_off_body`;
  a request without the hint carries none of `reasoning`, `reasoning_effort`
  or `think`.
* A 400 while the hint is on the wire is retried once without it, unless it
  is a temperature refusal or a context-length refusal; the decision reads
  what was sent, never the vendor's wording.
* `ChatResponse.hint_sent` records what the answering call carried: None not
  asked, True sent, False refused and dropped. Unreported counts are None,
  never 0.
* Every `llm_output` row a call produces records `hint_sent`,
  `reasoning_tokens`, `reasoning_chars`, `upstream`, `hint_ignored` and
  `cap_not_enforced` in `params_json`, judged against the cap that call was
  asked with.
* A concat kind's answer that ended 'length' is refused before it is stored,
  whether it is one call or one of many.
* A stored part is reused only when its `segment_ids` are the chunk's.

### Must Not

* Branch on the provider's name in a caller to decide the hint.
* Use `effort: low` or `minimal` for a kind that must keep `[m:ss]` stamps and
  speaker labels.
* Count OpenRouter's `message.reasoning` trace as reasoning spent: luna spent
  107 reasoning tokens with no trace.
* Merge a provider's `reasoning_off_body` into a body by reference.

### Exceptions

* `speakers` and `labels` stay at the provider default until their own A/B.
* Ollama's hint is never dropped: its daemon refuses only a truthy `think`.

### Verification

* `tests/test_llm_providers.py::test_each_cloud_provider_says_no_reasoning_its_own_way`,
  `::test_a_refused_hint_is_retried_once_without_it`,
  `::test_a_context_length_400_with_the_hint_on_is_context_too_long_after_one_call`,
  `::test_a_temperature_refusal_then_a_hint_refusal_is_three_calls`,
  `::test_the_answer_carries_reasoning_tokens_and_upstream`.
* `tests/test_llm_ollama.py::test_the_hint_is_think_false_and_its_absence_sends_no_think`.
* `tests/test_llm_tasks.py::test_the_hint_follows_the_kind`,
  `::test_every_row_records_what_was_sent_and_what_reasoning_cost`.
* `tests/test_llm_task_providers.py::test_cleanup_reaches_each_real_provider_with_its_own_no_reasoning_field`.
* `tests/test_llm_speakers.py::test_a_one_call_cleanup_cut_off_at_the_cap_is_refused_and_nothing_is_stored`,
  `::test_a_stored_part_cut_on_other_boundaries_is_asked_again`,
  `::test_a_cleanup_chunk_is_cut_to_the_concat_limit_not_the_answer_cap`.

## Consequences

### Positive

* Cleanup on the models measured costs about a twentieth of the tokens
  (1,171 against 23,918 on media 1) and an eighth to a tenth of the time
  (8.5 s against 63-84 s).
* A dropped hint and an ignored hint are told apart on the row, and case 3 -
  an endpoint that does not enforce the cap - is named per row instead of
  being found by hand.
* A cut-off cleaning can no longer reach the gate or the reading (row 15:
  12,000 of 12,000 tokens, stored).
* A failed call's error names what it spent (completion and reasoning tokens,
  upstream, hint state), so the jobs board carries the receipt.

### Negative

* Case 3 has no app-side bound on spend. Mitigation: never seen with the
  hint; the row flags make it visible when it happens.
* The step-0 measurement counts only upstreams that pass the enforcement
  check, so it says nothing about case 3. Mitigation: stated here and in the
  script's output ('cap not enforced - not counted').
* More parts make the per-part 0.55/1.15 cleaning gate judge shorter spans:
  media 12 goes from 4 parts to 7. Mitigation: the acceptance script prints
  the per-part ratios next to the overall one.
* A refusal unrelated to the hint costs one extra 400. Its latency is not
  measured.
* Three extra calls on media 12 when the hint is honoured, about +1,180
  estimated prompt tokens (about 6%).
* `openrouter/auto` has served only deepseek-v4-flash-0731 and gpt-5.6-luna
  for cleanup on this account; the evidence is about those two models, not
  the router.

## Pros and Cons of the Options

### A. Per-kind hint, mapped per provider

* Good, because it is measured to work on every model auto has served for
  cleanup, and a refusal costs one free 400.
* Good, because it keeps the provider seam: one field on the request.
* Bad, because a model that ignores it on a non-enforcing endpoint is bounded
  by nothing the app controls.

### B. Effort ladder

* Good, because a mandatory reasoner would reason less than at its default.
* Bad, because `effort: low` dropped the stamps and speaker labels on this
  very kind, and it adds a third retry state for a case never seen under auto.

### C. Raise the cloud cleanup cap (option not taken; offered to Robert)

* Good, because a heavy reasoner on an enforcing endpoint would succeed, at
  about 25k tokens per chunk - inferred from media 1, not measured on
  3,000-token chunks - where today it fails loudly at no more than 6,000.
* Bad, because a runaway can spend up to the new cap (row 15 spent all
  12,000 of its cap), it needs separate local and cloud caps (`budget_for`
  raises on the 8,192 local window), and it does nothing for models whose
  reasoning budget is a share of the cap.

### D. `provider.require_parameters: true`

* Good, because one call showed auto accepts it (deepseek via Wafer, 0
  reasoning).
* Bad, because 127 of the 130 non-reasoning models do not list `reasoning`
  and would leave the pool, and it cannot exclude non-enforcing upstreams.

### E. Do nothing

* Good, because TASK-026's gate already refuses cut-off parts in chunked runs.
* Bad, because cleanup keeps failing on real recordings, a one-call cleanup
  went round the gate (row 15), and the spend stays invisible.

## Open Questions

- [ ] Step 0 (`scripts/task029_headroom.py --model openai/gpt-5.6-luna` and
  `--model google/gemini-3.5-flash-lite`, 3 repeats, no hint): does every
  counted chunk end 'stop' with content and spend at most half its reserve?
  If luna fails, `CONCAT_CHUNK_TOKENS` is removed and this record says chunk
  size buys no headroom. It spends money and waits for Robert's go-ahead.
- [ ] TASK-029 AC3 as written cannot hold (locally `num_predict` lives inside
  `num_ctx` 8,192; on cloud deepseek at its default effort spent 21.5-22.5k
  reasoning tokens on a 1,557-token chunk). Proposed wording, for Robert: "A
  model that ignores the hint either finishes each chunk inside its cap with
  measured headroom, or fails before anything is stored. On an endpoint that
  does not enforce the cap only the hint bounds reasoning, and each row
  records which happened."
- [ ] Should the cloud cleanup cap be raised (option C) so heavy reasoners are
  covered?

## Related Decisions

* ADR-002 / ADR-009 (SQLite in WAL (write-ahead log) mode is the only
  coordination): the
  reasoning numbers go into `llm_output.params_json` under `db.LOCK`, with no
  new table or column.
* ADR-007 (the application log is observation): the per-row flags are data a
  person reads; nothing decides anything from them.

## References

* TASK-029 (backlog) and the design it was built from.
* `scribe/llm/base.py:57` (`ChatRequest.reasoning_off`), `:92`
  (`ChatResponse.hint_sent`), `:302` (`Provider.reasoning_off_body`).
* `scribe/llm/openai_like.py:220` (`complete`, the drop loop), `:250`
  (`_refused_knob`), `:409` (`_refuses_context`), `:442` and `:474` (the two
  wire forms).
* `scribe/llm/ollama.py:177` (`think: false`), `:385` (thinking characters).
* `scribe/llm/tasks.py:316` (`TaskSpec.reasoning_off`), `:436` (cleanup on),
  `:527` (`CONCAT_CHUNK_TOKENS`), `:1006` (the chunk limit), `:1227`
  (`stored_chunk`), `:1286` (`reasoning_record`), `:1705`
  (`_refuse_a_cut_off_part`).
* `scripts/task029_headroom.py`, `scripts/task029_acceptance.py`.

## Enforcement

```json
{
  "forbid_pattern": [
    {"pattern": "[\"']effort[\"']\\s*:\\s*[\"'](?:low|minimal)[\"']", "path_glob": "scribe/**", "message": "effort low/minimal dropped the [m:ss] stamps and speaker labels on cleanup (ADR-010)."},
    {"pattern": "reasoning_effort\\s*=\\s*[\"'](?:low|minimal)[\"']", "path_glob": "scribe/**", "message": "effort low/minimal dropped the [m:ss] stamps and speaker labels on cleanup (ADR-010)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
