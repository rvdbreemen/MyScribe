---
id: TASK-089.25
title: The launcher's setup dialog writes a provider row nobody answered
status: To Do
assignee: []
created_date: '2026-09-21 06:11'
labels:
  - installer
  - llm
dependencies:
  - TASK-089.09
references:
  - packaging/launcher/myscribe_launcher.py
  - >-
    docs/adr/ADR-016-a-missing-provider-row-selects-no-provider-and-nothing-is-sent-until-somebody-has-chosen.md
parent_task_id: TASK-089
priority: medium
type: bug
ordinal: 164000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found on 2026-09-21 while building TASK-089.07, and outside its criteria.

packaging/launcher/myscribe_launcher.py:558 builds the provider radio group with `tk.StringVar(value="ollama")`, so one option is already filled in when the window opens, and `save()` passes `provider` unconditionally. Somebody who presses 'Save and start' without touching the radios has `llm_provider = ollama` written for them.

ADR-016 (Accepted 2026-09-21) says in its Decision Contract: "Only an answer somebody gave writes the row: in Settings, or in a setup sitting (ADR-015) to a question whose text says it chooses the provider." A preselected radio is a default, not an answer, and the heading above it reads "Answers about a transcript", which does not say that it chooses who answers.

This is not a leak. Ollama is local, and ADR-016's own point is that nothing leaves the machine without a choice. What it costs is different: a row that says a choice was made when none was, on a machine that may have no Ollama at all - and TASK-089.06 exists because nothing checks that today. The user then gets failures from a provider they never picked, and TASK-089.07's 'choose a provider' never appears, because there is a row.

It belongs to ADR-015's engine, not to the panel: the launcher is to render questions and never decide (that is the record's Must), so the fix is that a question nobody touched is skipped and writes nothing - which is what ADR-015 calls a skip.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red first: a test drives the launcher's answer-collecting with nothing touched and shows that it passes a provider today; after the change it passes none, and nothing writes llm_provider.
- [ ] #2 A question the person did not answer is skipped and writes nothing, for every question in the sitting, not only the provider - the skip ADR-015 already defines.
- [ ] #3 A person who does pick a provider still gets the row, and the dialog's heading says that picking chooses who answers.
- [ ] #4 tests/test_launcher.py keeps its 26 tests green, and the one that pins what is handed to the app (:404 area, 'the answers are handed to the app not acted on here') moves with the change rather than being deleted.
<!-- AC:END -->
