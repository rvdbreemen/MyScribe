---
id: "ADR-004"
title: "large-v3-turbo is the default model and translate substitutes large-v3"
status: "Accepted"
date: "2026-09-03"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "asr"
  - "models"
  - "performance"
aliases:
  - "turbo"
  - "model tiers"
  - "Turbo and Maximaal"
components:
  - "scribe.stages.transcribe"
  - "scribe.doctor"
symbols:
  - "DEFAULT_MODEL"
  - "resolve_model"
  - "perf_model_for"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-004 large-v3-turbo is the default model and translate substitutes large-v3

## Status

Accepted, 2026-09-03.

## Status History

```yaml
status_history:
  - date: 2026-09-02
    status: Proposed
    changed_by: Claude Fable 5.1 (agent)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-03
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert in an interactive session on 2026-09-03. Verified: the model name is spelled once (transcribe.py:60), resolve_model does the turbo/translate substitution and four tests pin the table, and writer and reader of stage_perf share perf_model_for (runner.py:194, app.py:60, jobs_ui.py:184). The stage_perf key rule was widened after the grill found llm_stage.py:107 deliberately overriding it - the rule is that writer and reader agree, not that one function supplies the value. Live stage_perf confirms the separation: transcribe stages under large-v3-turbo, the language model job's generate under openai/gpt-5.6-luna."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

A three-tier menu - roughly base, small and large - is the usual shape for
this choice. On this hardware the small models are dominated: they are not
meaningfully faster in wall-clock terms once model load is paid, and their
Dutch accuracy is far worse. Measured on the RTX 3080 Laptop on 2026-09-02:

* large-v3-turbo, 5 minutes of Dutch speech, word timestamps and voice
  activity detection: **14.5x realtime**, about 6 GB of VRAM (video memory),
  10 s to load.
* large-v3 (WHYcast, 22 minutes): ~2.9x realtime, ~10 GB VRAM.

large-v3-turbo was fine-tuned on transcription only; it degrades silently on
`task="translate"`. The user chose turbo as the default on 2026-09-01.

## Decision Drivers

* Speed headroom on one 16 GB (gigabyte) card that must also fit pyannote
  and, later, a local LLM (large language model).
* A translate request must not silently produce worse output.
* One place decides which model runs, so timing history and ETAs agree.

## Considered Options

* Default large-v3-turbo; large-v3 selectable ("Maximaal") and automatically
  substituted when `task == "translate"`.
* Default large-v3 everywhere.
* Three tiers (base / small / large), the usual shape.

## Decision Outcome

Chosen option: **turbo default with large-v3 substitution for translate**.
`transcribe.resolve_model(requested, task)` is the single decision point; it
returns the model plus a human-readable substitution note that is stored on
the run and shown in the UI (user interface). `transcribe.perf_model_for(params)` derives the
`stage_perf` key from the same function, so the runner's timing rows and the
job API (application programming interface) lookups can never disagree — they did, once, and every ETA past
`prepare` was None until the key was unified.

### Confirmation

`tests/test_stage_transcribe.py` covers the substitution table (turbo +
translate → large-v3 with the note; turbo + transcribe unchanged; large-v3 +
translate unchanged; empty → default). `tests/test_app.py` and
`tests/test_runner.py` pin that a default job files and reads `stage_perf`
under the same key.

## Decision Contract

### Must

* `DEFAULT_MODEL == "large-v3-turbo"`, defined once in `scribe.stages.transcribe`.
* Any model whose name contains `turbo` is substituted by `large-v3` when
  `task == "translate"`, and the substitution is recorded on the run.
* `stage_perf` writes and ETA (estimated time of arrival) reads use the same
  key: `perf_model_for(params)` by default, or `ctx.state["model"]` when a
  stage sets one. The LLM stage must set one - its model is not a Whisper
  tier, and reading it through these rules is right by accident today and
  wrong the moment a job leaves the model to the provider's default
  (`scribe/stages/llm_stage.py:107`). The rule is that writer and reader
  agree, not that one function supplies the value.

### Must Not

* Expose tiny/base/small as user-facing quality tiers.
* Spell the default model name anywhere except `transcribe.DEFAULT_MODEL`.

### Exceptions

* `small` may be used as a hidden CPU (processor-only) fallback or preview
  engine.

### Verification

* `tests/test_stage_transcribe.py` (resolve_model table),
  `tests/test_runner.py::test_stage_perf_for_a_default_job_is_filed_under_the_resolved_model`,
  `tests/test_app.py::test_eta_for_a_default_job_uses_the_model_the_runner_files_under`.
* `grep -rn "large-v3-turbo" scribe/` matches only `scribe/stages/transcribe.py`.

## Consequences

### Positive

* One hour of audio in ~4 minutes, with room left on the card.
* Translate never silently degrades.
* Timings stay separable per job type. Observed in `stage_perf` on
  2026-09-03: transcribe stages filed under `large-v3-turbo`, the language
  model job's `generate` under `openai/gpt-5.6-luna`. `prepare` appears under
  both - two job types spend that name on unrelated work - and they do not
  collide only because `eta_seconds` filters on stage and model together.
  Worth knowing before a third job type reuses a stage name.

### Negative

* Turbo costs one to two WER (word error rate) points against large-v3. Mitigated by the Maximaal
  tier being one click away and re-runs being cheap (ADR-003).
* Turbo is a separate ~1.6 GB download. The doctor performs it on first run.

## Pros and Cons of the Options

### Turbo default + substitution

* Good, because it is the fastest model that keeps Whisper's 99 languages and
  word timestamps.
* Bad, because two models to cache instead of one.

### large-v3 default

* Good, because best accuracy always.
* Bad, because ~10 GB VRAM leaves no room for pyannote plus a local LLM, and
  it is ~4x slower for a difference most Dutch speech does not show.

### Three tiers

* Bad, because base/small are dominated on this GPU: a choice with no good answer.

## Open Questions

- [x] Does the stage_perf key rule survive job types that are not transcription? — **Answered 2026-09-03 by User: Robert van den Breemen:** It survives, once the rule says what it means. Robert chose this on 2026-09-03. A stage may override the key by setting ctx.state['model'], and the LLM stage must: perf_model_for reads the job's model param through the Whisper tier rules, which returns an OpenRouter model id unchanged today but falls back to large-v3-turbo the moment a job omits the model - filing LLM timings as Whisper timings, the same pollution the doctor smoke caused before it was moved to its own stage name. The rule is that the writer and the ETA reader agree on one key, not that one function must supply it.

## Related Decisions

* ADR-006 (the stack that runs the model).

## References

* `requirements-gpu.txt` header (measurements), `scribe/stages/transcribe.py`.
* Research note in `docs/superpowers/specs/2026-09-01-myscribe-design.md` §3.

## Enforcement

```json
{
  "forbid_import": [],
  "forbid_pattern": [
    {"pattern": "\"large-v3-turbo\"", "path_glob": "scribe/app.py", "message": "The default model is spelled once, in transcribe.DEFAULT_MODEL (ADR-004)."},
    {"pattern": "\"large-v3-turbo\"", "path_glob": "scribe/doctor.py", "message": "The default model is spelled once, in transcribe.DEFAULT_MODEL (ADR-004)."},
    {"pattern": "\"large-v3-turbo\"", "path_glob": "scribe/runner.py", "message": "The default model is spelled once, in transcribe.DEFAULT_MODEL (ADR-004)."},
    {"pattern": "\"large-v3-turbo\"", "path_glob": "scribe/web/**", "message": "The default model is spelled once, in transcribe.DEFAULT_MODEL (ADR-004)."},
    {"pattern": "eta_seconds\\([^)]*params\\.get\\(\"model\"\\)", "path_glob": "scribe/**", "message": "ETA lookups use transcribe.perf_model_for(params), the same key the runner writes (ADR-004)."}
  ],
  "require_pattern": []
}
```
