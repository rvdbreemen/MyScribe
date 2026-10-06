---
id: TASK-105.02
title: 'The prompt version covers SYSTEM, the goal strings and source_label'
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 20:34'
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
- [x] #1 A test fails when SYSTEM or a goal string changes without a version bump, red first
- [x] #2 The docstring matches the version
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The digest test now hashes prompt_material(): every template, SYSTEM, JSON_SYSTEM, each kind's goal and two source_label lines; recorded for version 2 without a bump (the material is what version 2 already asked). Proof it bites, in memory only (no file mutated): unchanged -> old and new test pass; SYSTEM + ' Answer in Dutch.' -> old test passes (the gap), new FAILS; goal:summary changed -> old passes, new FAILS. Docstrings: the digest's said version 1, now 2; PROMPT_VERSION's names everything that counts. test_llm_tasks 160 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Changing SYSTEM, a goal or the source line without bumping PROMPT_VERSION now fails a test; docstrings match version 2.
<!-- SECTION:FINAL_SUMMARY:END -->
