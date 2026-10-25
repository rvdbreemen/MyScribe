---
id: "ADR-034"
title: "Ollama answers are constrained by the kind's JSON schema"
status: "Proposed"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "ollama"
  - "structured-output"
  - "json-schema"
aliases:
  - "ollama format"
  - "timestamps as text"
components:
  - "scribe/llm/ollama.py"
  - "scribe/llm/tasks.py"
symbols:
  - "OllamaProvider._body"
  - "Provider.schema_clock_as_text"
  - "CLOCK_TEXT"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-034 Ollama answers are constrained by the kind's JSON schema

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
```

## Context and Problem Statement

Ollama was asked for a schema kind's JSON in words only. Job 431 (minutes,
media 61, `qwen3.5:4b`, library copy, 2026-10-24) failed: the combine wrote
`"evidence_ts": 2:16:29` unquoted. Cloud providers already get the schema as
`response_format`; Ollama's `/api/chat` takes one as `format`. Robert decided
on 2026-10-25 that Ollama gets the kind's schema through `format`.

Measured on Ollama 0.40.8 with `qwen3.5:4b`, on a four-line minutes prompt
with a stamp at `[2:16:10]`:

* No `format`, thinking off: `"evidence_ts": 2:16:10`, not JSON.
* Strict schema as `format`, thinking off: valid JSON, `"evidence_ts": 2.0`.
  The grammar allowed `2` as a number, forbade the `:`, and the model closed
  the number: a moment two hours off that nothing would flag.
* `number` or text: `2.0`. Text or null: `null`. Text bound by the pattern
  `[0-9]+(:[0-5][0-9]){1,2}`: `"2:16:10"`, read as 8170 s; written with
  `\d`, the pattern let `"{2:16:10}"` through (2 of 2).
* That text or null: `null` with thinking off (3 of 3); `"2:16:10"` with it on
  (1 run; another spent all 8000 tokens thinking).
* `prompt_eval_count` did not change with `format` (500 thinking, 502 not).

In the v0.40.8 source (`llm/llama_server.go`, lines 1713-1738) a thinking
model's schema becomes a grammar applied after the thinking closes; `/api/ps`
reports `runner: llamacpp`, so that path is the one used.

## Decision Drivers

* A local answer that is not JSON costs the whole job: minutes of GPU time.
* A wrong timestamp is worse than a missing one: a reader trusts it.
* The cloud schema, validated live on OpenRouter, moves only when measured.

## Considered Options

* **A.** Send the strict schema as `format` unchanged.
* **B.** Send it as `format`, with every timestamp field as pattern-bound
  text (or null where the field is nullable) instead of `number`.
* **C.** Keep asking in words, and repair unquoted clocks in `repair_json`.

## Decision Outcome

Chosen option: **B**. A cuts a copied `2:16:10` to `2.0`; C fixes one
malformation and leaves the rest to chance; B keeps a copied clock whole and
invents digits only where there is no time to copy (see Consequences).

`Provider.schema_clock_as_text` is True for Ollama and False for the cloud
providers, whose request stays byte-identical. Timestamp fields are found by
a mark on `Seconds` and `OptionalSeconds` (`CLOCK_FIELD`), which
`strict_json_schema` always removes before sending.

### Confirmation

The tests below pin body and schema. Live on media 61 (library copy, round 6)
the blog combine with `format` ended done (job 434); minutes failed in its
notes, which carry no `format`, so its combine is backed by the probes only.

## Decision Contract

### Must

* Send `format` as the bare JSON Schema whenever `ChatRequest.json_schema` is
  set, and keep it on the ADR-029 retry.
* Give a provider with `schema_clock_as_text` every timestamp field as text
  bound by `CLOCK_TEXT`, plus null exactly where the field is nullable.
* Write the pattern with `[0-9]`.

### Must Not

* Offer a `number` type for a timestamp to such a provider.
* Send `format` on a note or a part, which are prose.
* Change the schema a cloud provider receives.

### Exceptions

* None.

### Verification

* `tests/test_llm_ollama.py::test_the_kinds_schema_goes_out_as_format_so_the_daemon_enforces_it`
* `tests/test_llm_ollama.py::test_the_schema_rides_along_when_an_answer_lost_to_thinking_is_asked_again`
* `tests/test_llm_tasks.py::test_a_grammar_provider_is_never_offered_a_number_for_a_timestamp`
* `tests/test_llm_tasks.py::test_the_cloud_schema_is_unchanged_by_the_clock_marker`
* `tests/test_llm_tasks.py::test_the_combine_carries_the_text_timestamps_and_the_notes_no_schema`

## Consequences

* Good: a local schema answer is valid JSON; a timestamp is time-shaped or null.
* Bad: the grammar forces digits, not the copied moment. Asked to copy
  `{hello world}`, the 4B wrote `"1234567890"` (2 of 2, review probe of
  2026-10-25), so a required `chapters.start` can get an unchecked made-up time.
* Bad: thinking off, the 4B wrote null for a time; minutes and action items think.
* Neutral: a daemon without structured outputs ignores `format`, as before.

## Related Decisions

* ADR-029 (its retry keeps the schema); ADR-031 (`format` costs no prompt tokens).

## References

* TASK-124; job 431; probes `run-124/r6/formatprobe*.out`, `rev5-probe/probe.out`.
* Ollama v0.40.8 `llm/llama_server.go` (format and `ThinkingClose` handling);
  Ollama `docs/api.md`, `format` on `POST /api/chat`.
