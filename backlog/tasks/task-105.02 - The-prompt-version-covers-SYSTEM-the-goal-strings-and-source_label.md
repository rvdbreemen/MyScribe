---
id: TASK-105.02
title: 'The prompt version covers SYSTEM, the goal strings and source_label'
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - llm
dependencies: []
parent_task_id: TASK-105
ordinal: 192000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
PROMPT_VERSION's digest test hashes only prompts/*.md, so changing SYSTEM reuses cached answers; its docstring still says version 1.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A test fails when SYSTEM or a goal string changes without a version bump, red first
- [ ] #2 The docstring matches the version
<!-- AC:END -->
