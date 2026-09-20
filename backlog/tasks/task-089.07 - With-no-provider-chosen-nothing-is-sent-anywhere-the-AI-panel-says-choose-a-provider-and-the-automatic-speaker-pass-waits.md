---
id: TASK-089.07
title: >-
  With no provider chosen nothing is sent anywhere: the AI panel says 'choose a
  provider' and the automatic speaker pass waits
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - llm
  - security
  - ui
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 144000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 (brief: R1, W3). Today a missing llm_provider row falls through to OpenRouter. DEFAULT_PROVIDER is OpenRouterProvider.name (scribe/llm/tasks.py:456), and default_provider() returns it whenever the stored name is missing or no longer registered (scribe/llm/__init__.py:164-175). Three more fall-throughs sit in the stage itself: `ctx.params.get("provider") or tasks.DEFAULT_PROVIDER` at scribe/stages/llm_stage.py:94, :271 and :345.

What a user hits: they press 'Skip for now', or install from a clone and are never asked. With no key, every AI action fails for lack of a credential for a cloud provider they never chose, while the dialog they skipped had Ollama preselected (packaging/launcher/myscribe_launcher.py:558). With an OpenRouter key anywhere on the machine - scribe/llm/base.py:196-203 records that this machine has one machine-wide under HKLM - every transcript not pinned private can go to OpenRouter without anybody having chosen it.

The largest trigger is not a click. sweep_speaker_passes (scribe/stages/finalize.py:155) runs at every app start from the lifespan (scribe/app.py:261), over the whole back catalogue, and its docstring says 'No ceiling on how many it queues'. It goes through queue_speaker_pass (:384), which asks llm.default_provider(conn) at :437. Point MyScribe at an existing library, skip the provider question, and the first start queues one cloud job for every diarized recording that was never asked. That is the path TASK-089.19 opens, so this lands first.

scribe/setup.py:13-14 already argues the decision: 'sending a private recording to a cloud model is not a default anybody should inherit'. Once every question can be skipped, the skip path is the default most people get. It reverses the recorded spec decision 'Defaults: commercial providers (user decision)' (docs/superpowers/specs/2026-09-01-myscribe-design.md:219), so the spec and the CHANGELOG each get a line. Recordings pinned private are protected either way, and that rule does not move.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red first: with no llm_provider row, default_provider(conn) answers 'openrouter' today. After the change it answers 'no provider'. A stored name that is no longer registered does the same without raising, and a transcript page with such a row still renders 200.
- [ ] #2 sweep_speaker_passes, red first, on a scratch database with N diarized, never-asked, non-private recordings and no llm_provider row: the app's lifespan queues N llm jobs for openrouter today. After the change it queues none, and the test counts the job rows. The run is on a scratch or copied library, never the live one.
- [ ] #3 The pass waits; it is not lost. Once a provider is saved, the same recordings are queued no later than the next app start, and a test shows it. The notes say whether saving the provider also triggers the sweep.
- [ ] #4 Each of the 8 default_provider() call sites handles 'no provider', with one test per site: scribe/stages/finalize.py:437; scribe/web/ai_ui.py:650, :837, :953, :1016, :1181 and :1202; scribe/web/library.py:994, which goes through the alias at ai_ui.py:143.
- [ ] #5 Each of the three fallbacks at scribe/stages/llm_stage.py:94, :271 and :345 refuses a job that names no provider, with a sentence, instead of sending it to OpenRouter. One test per fallback. A grep shows no remaining fall-through to tasks.DEFAULT_PROVIDER.
- [ ] #6 POST /media/{id}/ai/{kind} with no row and no provider in the form answers with a 'choose a provider' card that links to Settings > AI providers. No job row is created - the test counts them - and nothing is sent. A provider picked in the panel for that one request still works: that is somebody choosing.
- [ ] #7 A machine whose row exists behaves exactly as before. The existing tests for a stored provider and for the private pin stay green, unchanged.
- [ ] #8 docs/superpowers/specs/2026-09-01-myscribe-design.md:219 gains a line saying 'Defaults: commercial providers' was reversed by Robert on 2026-09-20, and why. CHANGELOG.md gains a line under [Unreleased]. Both diffs are shown.
- [ ] #9 The sweep test is shown to bite: on a COPY of the repo the fall-through is put back, and the red output is shown.
- [ ] #10 What the panel shows, not only what the POST answers. Red first: on GET of the transcript page with no llm_provider row, the provider select (scribe/templates/_ai_region.html:102) has no provider preselected, and the panel shows the 'choose a provider' sentence with the link to Settings > AI providers. Today ai_context takes default_provider(conn) as the chosen one (scribe/web/ai_ui.py:650-652), so the page renders OpenRouter as selected on a machine where nobody chose it. One test reads the rendered HTML.
<!-- AC:END -->
