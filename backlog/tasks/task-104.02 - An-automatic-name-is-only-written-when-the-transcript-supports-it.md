---
id: TASK-104.02
title: An automatic name is only written when the transcript supports it
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - llm
dependencies: []
parent_task_id: TASK-104
ordinal: 188000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Confidence is the model's own claim; nothing checks that the name occurs in the transcript before apply_speakers writes it unattended.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A failing test: a name at confidence 95 whose words occur nowhere in the transcript is offered as a suggestion, not written, red first
- [ ] #2 A name that does occur is still written; human names stay untouched
<!-- AC:END -->
