---
id: TASK-089.10
title: >-
  An AI action that cannot be answered shows what is missing instead of queueing
  a job that fails
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-21 05:59'
labels:
  - llm
  - web
  - ui
dependencies:
  - TASK-089.06
  - TASK-089.07
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 147000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ai_run checks the kind, the transcript, the private pin, the prompt and the window, and nothing about a key or about Ollama (scribe/web/ai_ui.py:815-870). Its own docstring says the checks in front of the row are 'the ones that cost nothing and would otherwise become a failed job somebody has to read'. A missing key is exactly that, and it is not among them.

So a skipped key, a stopped Ollama or a model that was never pulled becomes an LLM_FAILED job on the board - after three retries in Ollama's case, in a reader's scratch run on 2026-09-20. queue_speaker_pass has the same blind spot for the automatic pass (scribe/stages/finalize.py:437-452).

This is the safety net that makes skipping honest. Every 'decide later' in the sitting ends here, as a card that names the fix, and not as a failed job. TASK-089.07 handles 'no provider chosen'; this handles 'chosen, but cannot answer'.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 POST /media/{id}/ai/{kind} answers with the panel and a card when the chosen provider cannot answer: no key, Ollama not running, or the saved model not pulled. The card names the fix and where to do it. No job row is created, and a test counts the rows. Each of the three is red first.
- [ ] #2 The check uses the model the job would use: the saved row, not the class default (TASK-089.06).
- [ ] #3 The check makes no remote call: key presence and the loopback probe only, bounded the way available() already is. Nothing is loaded in the web process (ADR-001).
- [ ] #4 The automatic speaker-naming pass in finalize skips with a run note when the provider cannot answer, and sweep_speaker_passes queues nothing for it. The note says what to fix.
- [ ] #5 A provider that can answer behaves exactly as before. The existing ai_run tests stay green, unchanged.
- [ ] #6 A request that names no provider at all - no row, nothing in the form - comes back with the same card, naming the same fix (choose a provider in Settings > AI providers) and creating no job row. TASK-089.07 proved the safety half on 2026-09-21 and refuses with those words; what it does not have is the card and a link a person can click, and the card is built here so there is one of it. Red first against the plain 400 that task left behind.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Scope added on 2026-09-21 by the orchestrator, with its reason.

TASK-089.07 built ADR-016's refusal: a request that names no provider creates no job row and sends nothing, which is mutation-proven. What it could not finish is how that refusal reaches the reader. It raises HTTPException(400) whose detail carries the right sentence and the Settings anchor, but app.js renders a detail with textContent (wireHtmxErrors), so the anchor is inert text - no card, no link.

Its criterion #6 asked for the card. This task already builds one, for a provider that is chosen but cannot answer (design spec, section 3.5, lines 900-905, and criterion #1 here). That is a different case, so the card for 'nobody has chosen' was assigned nowhere. Rather than build two cards in two tasks, the card half moved here as a new criterion and TASK-089.07's #6 was narrowed to the safety half, saying so in its own text.

What this task inherits: scribe/web/ai_ui.py:742-754 _chosen_provider raises the 400; the two sentences are NO_PROVIDER_YET (screens with no picker, such as the library's bulk action) and NO_PROVIDER_FOR_REQUEST (the panel and the chat form, which carry a select) at :150-165.
<!-- SECTION:NOTES:END -->
