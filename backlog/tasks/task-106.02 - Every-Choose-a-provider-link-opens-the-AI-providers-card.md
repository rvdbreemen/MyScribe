---
id: TASK-106.02
title: Every Choose a provider link opens the AI providers card
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - ui
  - bug
dependencies: []
parent_task_id: TASK-106
ordinal: 196000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
SETTINGS_ANCHOR and two templates link /settings#llm-providers, but the page reads only ?section=, so they open the Defaults card. Verified in code.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The links use ?section=llm and a test follows one to the providers card, red first
- [ ] #2 No other settings link changes
<!-- AC:END -->
