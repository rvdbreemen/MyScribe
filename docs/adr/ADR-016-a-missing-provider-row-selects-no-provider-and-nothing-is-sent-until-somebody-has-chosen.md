---
id: "ADR-016"
title: "A missing provider row selects no provider, and nothing is sent until somebody has chosen"
status: "Accepted"
date: "2026-09-21"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-015"
  - "ADR-017"
topics:
  - "llm"
  - "privacy"
aliases:
  - "no provider row"
  - "llm_provider"
components:
  - "scribe.llm"
  - "scribe.stages.finalize"
  - "scribe.stages.llm_stage"
  - "scribe.web.ai_ui"
symbols:
  - "default_provider"
  - "sweep_speaker_passes"
  - "queue_speaker_pass"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-016 A missing provider row selects no provider, and nothing is sent until somebody has chosen

## Status

Accepted, 2026-09-21.

## Status History

```yaml
status_history:
  - date: 2026-09-20
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-21
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-015
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Proposed
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-017
    changed_via: adr-kit lifecycle
  - date: 2026-09-21
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert on 2026-09-21, asked for in the session and given as 'Accept ADR 016' after the packet was put to him: what it reverses (the recorded 'Defaults: commercial providers'), what it costs (eleven places, the speaker pass of TASK-024 waiting, jobs the fall-through already queued keeping openrouter in their parameters), and what it leaves alone (a machine whose row exists). That yes also confirms the two things the packet flagged as the agent's and not his: the definition of 'somebody has chosen' - the row, or a request somebody made that names a provider - and the one Enforcement tripwire over scribe/, which leaves today's four fall-through lines unflagged because the judge reads only added lines. Nothing is built yet; TASK-089.07 owes the evidence, red first."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Which provider a request opens with is one settings row, `llm_provider`.
When the row is missing the code picks OpenRouter. Read on 2026-09-20:

* **The fall-through.** `DEFAULT_PROVIDER` is OpenRouter's name
  (`scribe/llm/tasks.py:456`); `default_provider()` returns it for a missing
  row or an unregistered name (`scribe/llm/__init__.py:164-175`). Eight call
  sites ask it: `scribe/stages/finalize.py:437`, `scribe/web/ai_ui.py:650`,
  `:837`, `:953`, `:1016`, `:1181`, `:1202`, `scribe/web/library.py:994`; the
  runner repeats it at `scribe/stages/llm_stage.py:94`, `:271`, `:345`.
* **The widest path is not a button.** `sweep_speaker_passes` runs at every
  app start (`scribe/app.py:261`) over every diarized recording never asked
  before, "No ceiling on how many it queues" (`scribe/stages/finalize.py:177`):
  60 runs with clusters and 3 with names in Robert's library on 2026-09-10
  (`:160-161`); every transcription queues the same pass (`:137-139`).
* **A key is found without anybody typing one.** The resolver reads the
  settings row, the environment, then the Windows registry, machine-wide
  hive included (`scribe/llm/base.py:194-222`, `:260-266`), and text then
  goes to `https://openrouter.ai/api/v1` (`scribe/llm/openai_like.py:469`).

This was "Defaults: commercial providers (user decision)"
(`docs/superpowers/specs/2026-09-01-myscribe-design.md:219`), safe while
choosing was something a person did. ADR-015 makes every question skippable
and TASK-089.19 adopts a whole back catalogue, so the skip is the default
most installs get, and it sends to a cloud nobody chose.

## Decision Drivers

* The automatic paths outweigh the buttons: the sweep acts with nobody there.
* A machine whose row exists must not notice the change.

## Considered Options

* A missing row selects no provider: the panel asks, the automatic pass waits.
* Keep the fall-through and say so in words: in setup, Settings, the readme.
* Default to the local provider, Ollama, instead of to none.
* Ask at first use: the first AI (artificial intelligence) action asks.

## Decision Outcome

Chosen option: **a missing row selects no provider**, decided by Robert on
2026-09-20 (R1 in `docs/superpowers/specs/2026-09-20-installer-decisions.md`;
W3 there names the sweep). The reason as this record puts it: only then are
"nobody answered" and "nothing was sent" the same fact on every path.

Somebody has chosen when the row names a registered provider, or when a
request somebody made names one: the panel's select for that one request, or
job parameters that such a request wrote (the agent's wording of R1 and
TASK-089.07 criterion #6, for Robert to confirm at acceptance).

### Confirmation

Nothing exists yet. TASK-089.07 owes the evidence, red first: criteria #1-#10,
of which #2 and #9 are the sweep counted as job rows on a scratch library and
shown to bite on a copy of the repository; #7 keeps a machine with a row green.

## Decision Contract

### Must

* `default_provider()` answers "no provider" for a missing row or unregistered
  name, without raising; every caller in Context, and any added, handles it.
* The panel says "choose a provider" with a link to Settings > AI providers
  and preselects nothing; the Settings line says the same.
* `queue_speaker_pass`, `sweep_speaker_passes` and the bulk action queue nothing
  without a provider; the sweep marks nothing asked, so the next start catches up.
* A job that names no provider ends in the runner with a sentence; no request.
* Only an answer somebody gave writes the row: in Settings, or in a setup
  sitting (ADR-015) to a question whose text says it chooses the provider.
* The spec line quoted in Context and `CHANGELOG.md` each gain a line saying
  the default was reversed, by whom and why (criterion #8).

### Must Not

* Fall back to any provider, cloud or local, when none was named.
* Read a found key, in the environment or the registry, as a choice.
* Change what a machine with a row does, or loosen the private pin
  (`scribe/llm/privacy.py:99-120` already refuses every non-local provider).

### Exceptions

* None: a provider named by a request somebody made is a choice, not an exception.

### Verification

* `grep -rnE --include=*.py "(or|else) tasks\.DEFAULT_PROVIDER" scribe/`
  returns four lines on 2026-09-20 (`scribe/llm/__init__.py:175` and the
  three runner lines) and none after TASK-089.07.
* `adr-judge --dry-run-enforcement ADR-016`, as run under Enforcement.

## Consequences

### Positive

* An install that skipped every question, or asked none, sends nothing, and
  adopting a library (TASK-089.19) is safe before anybody has chosen.
* The panel stops rendering OpenRouter as selected where nobody chose it.

### Negative

* A recorded user decision is reversed in two documents; whoever relied on
  the fall-through has to choose once, and the speaker pass of TASK-024 waits.
* Jobs the fall-through queued before this change keep `openrouter` in their
  parameters and are not recalled; the definition above does not cover them.

## Pros and Cons of the Options

### A missing row selects no provider (chosen)

* Good, because nothing is sent unless somebody chose, the sweep included.
* Bad, because it touches eleven places and holds back the speaker pass.

### Keep the fall-through and say so in words

* Good, because no code changes and a machine with a key keeps working.
* Bad, because the sweep reads no sentence: it runs at start, before anybody
  can read one, and a clone or a headless start shows none at all.

### Default to the local provider

* Good, because nothing leaves the machine.
* Bad, because without Ollama the sweep queues a job per recording against a
  daemon that is not there (by reading; nobody ran it), and with it a choice
  nobody made loads the transcription card (`scribe/llm/ollama.py:221-225`).

### Ask at first use

* Good, because the choice is made where it is needed.
* Bad, because the sweep and the pass after a transcription have no first
  use: nobody is at the screen, so they need this rule anyway.

## Open Questions

None.

## Related Decisions

* ADR-015 (the first-run engine): a skip writes nothing, which is only honest
  when nothing written means nothing sent; Robert split this record off (G1).
* ADR-017 (third-party software): its install offer writes the row on a yes
  only because that question's text says so (Must above); a no leaves none.

## References

* `docs/superpowers/specs/2026-09-20-installer-decisions.md` (R1, W3, G1);
  `docs/superpowers/specs/2026-09-20-installer-design.md`, sections 3.3, 3.5.
* Backlog TASK-089.07, TASK-089.19, TASK-024.

## Enforcement

One tripwire, not a proof; TASK-089.07's tests are the proof. The judge reads
added lines only, comments included (adr-kit 0.57.0): today's four lines stay
unflagged; moving one, or quoting the idiom in a comment, needs
`ADR_KIT_OVERRIDE`; another spelling, or a front-end outside `scribe/`
(ADR-015's), passes the pattern. No `require_pattern`: it reads whole files
and fails every commit until the code lands. Dry run on 2026-09-20, scratch
copy marked Accepted: two added lines flagged, a deleted one and five passed.

```json
{
  "forbid_pattern": [
    {"pattern": "\\b(?:or|else)\\s+(?:tasks\\.)?DEFAULT_PROVIDER\\b", "path_glob": "scribe/**", "message": "No provider named means no provider: refuse with a sentence, never fall back to DEFAULT_PROVIDER (ADR-016)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
