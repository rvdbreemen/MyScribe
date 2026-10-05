---
id: TASK-105.01
title: Every prompt tells the model which language to answer in
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - llm
dependencies: []
parent_task_id: TASK-105
ordinal: 191000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
SYSTEM and all templates are English and never mention run.language; a Dutch recording likely gets English summaries (not measured).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 SYSTEM (or each template) carries the run's language and a test pins it, red first
- [ ] #2 A real run on a Dutch clip with a local model shows a Dutch summary, output quoted in the notes
<!-- AC:END -->
