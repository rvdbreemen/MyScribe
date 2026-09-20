---
id: TASK-089.11
title: >-
  The first-run sitting appears when it should: a finished old setup gets the
  new questions once, and a skipped question does not nag
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - packaging
  - ux
dependencies:
  - TASK-089.09
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 148000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The gate that decides whether the sitting appears at all was never defined. Today it is 'setup.json exists': setup.done() (scribe/setup.py:60-61) and the launcher's setup_needed (packaging/launcher/myscribe_launcher.py:462-463). The stamp holds {provider, tier, hf_token, fetched} (setup.py:143-154) and no question ids.

If the gate stays as it is, everybody who already finished the old setup keeps their stamp and never sees the API-key question or the Ollama question. That includes the people who pressed Save on the preselected 'Ollama, on this machine' radio without having Ollama (myscribe_launcher.py:558) - the group this work is for.

If the gate becomes 'does --plan have open questions', every start pays for a Python child. A reader measured `--status` at about 4.6 s on Robert's machine on 2026-09-20; that was not re-run when this task was written. A question somebody skipped on purpose would then also come back on every start.

Robert's requirements (brief: M4). Reading the gate is cheap: the stamp only, and no child process. Criterion 2 adds one number out of the payload to that read, because the stamp alone cannot say whether this version has questions it never covered. People who finished the old setup are asked the NEW questions once. A question deliberately skipped is recorded as skipped and does not nag, and it stays reachable through `--setup`, the Setup button and Settings. The button does not exist yet: it is built in TASK-089.15, and its route is tested there. The launcher's own docstring already says that closing the window is '"ask me next time", not "never"' (packaging/launcher/myscribe_launcher.py:527-528, committed on 2026-09-19), and that stays. TASK-040.06 records four things Robert chose on 2026-09-18; this wording is not among them, so it is cited from the code and not as his decision.

Today 'Skip for now' destroys the window and writes nothing (myscribe_launcher.py:590). The same dialog returns at every start until Save is pressed once, and no single question can be skipped on its own.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The stamp carries the contract number, when the sitting ended, and per question id its state: answered, skipped, not needed because the answer was found, or still open after a failure. Booleans and source names only; a SENTINEL test finds no secret in it.
- [ ] #2 Deciding whether to show the sitting reads setup.json and one number out of the payload, as data, and nothing else. The number is the contract number, raised whenever a question is added. It is a number and not a list of question ids, so that the launcher learns nothing about questions; the design spec and ADR-015 say the same. How it ships - a one-number JSON file under app/scribe/ (the spec proposes scribe/setup_contract.json), or a constant read as text the way app_version reads the version (packaging/launcher/myscribe_launcher.py:119-126) - and its name are settled here and written into the notes. A test asserts that no child process is started, and the time it takes on Robert's machine is reported next to the 4.6 s it replaces.
- [ ] #3 Red first: an old-format stamp {provider, tier, hf_token, fetched} makes the sitting appear once. It shows only the questions that stamp never covered and that are still open, keeps the old answers as answered, and does not reset the provider or the tier.
- [ ] #4 A question that was skipped is not asked again at the next start. It is asked again by `--setup`, and its answer can be given later by the route its answer_later line names: Settings for every question that has a counterpart there, and the stated exceptions where it has none - the location (TASK-089.14: MYSCRIBE_HOME, `--home` or the pointer file) and the library (TASK-089.19: `--setup` and the Setup button only, with its reason). One test per route. The Setup-button route is not tested here: no such button exists today - the window has three, 'Open MyScribe', 'Open data folder' and 'Quit' (packaging/launcher/myscribe_launcher.py:613-623) - and TASK-089.15, which creates it, depends on this task. Its test is TASK-089.15 criterion 8.
- [ ] #5 'Ask me next time' on the whole sitting writes no stamp, so the sitting returns at the next start, and its label says so. Skip on one question records that question as skipped. The two are different controls, and a test covers both.
- [ ] #6 When a later version adds a question, a machine with a current stamp is asked that one question once. The launcher notices by comparing the stamp with the contract number of criterion 2; it starts no child to find out, and only when they differ does it pay for one `--plan`. A test adds a fake question id and shows it.
- [ ] #7 A state that became open again after it was answered - a token removed, weights deleted - does not reopen the sitting by itself. `--plan` and the doctor report it. The notes record that this is deliberate: the gate is about what was asked, `--plan` is about the machine now.
- [ ] #8 The launcher reads the stamp as JSON and the contract number of criterion 2 as data, and imports nothing from the app (ADR-011's Must: 'Keep the launcher stdlib-only'). tests/test_launcher.py:378 is updated rather than deleted, and its new name says what it pins.
<!-- AC:END -->
