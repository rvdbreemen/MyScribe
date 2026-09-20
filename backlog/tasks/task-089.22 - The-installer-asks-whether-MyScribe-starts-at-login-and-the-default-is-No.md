---
id: TASK-089.22
title: 'The installer asks whether MyScribe starts at login, and the default is No'
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - packaging
  - ux
dependencies:
  - TASK-089.11
  - TASK-089.21
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 159000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 as one of three extra questions, with the default No (brief: R3). It is a thin layer over TASK-089.21, which has to exist first (brief: U3): the installer may only ask what Settings can also answer (scribe/setup.py:21-23).

Why it belongs in the sitting: it is the natural follow-up to the watch-folder question of TASK-089.20. A folder that ingests by itself only does so while the app runs, and a first-time user does not know that.

Default No, because starting a program at every login is something a person opts into. A model-loading app that holds VRAM is not something to find running by surprise.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The question is in `--plan` with default No, skippable, and its if_skipped sentence says that watch folders and feeds only work while MyScribe runs.
- [ ] #2 Yes does exactly what the Settings switch of TASK-089.21 does, through the same function. A test shows the registered item is identical.
- [ ] #3 No and skip register nothing. Skip is recorded as skipped (TASK-089.11) and does not return at every start.
- [ ] #4 It is only present when the OS reports no login item for MyScribe. A re-run shows the current state instead of asking.
- [ ] #5 `--non-interactive` and `{}` answers register nothing, and a test asserts that no OS call was made.
<!-- AC:END -->
