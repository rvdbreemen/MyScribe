---
id: TASK-006.04
title: 'llm: six preset outputs as resumable map-reduce jobs'
status: Done
assignee: []
created_date: '2026-09-02 16:32'
updated_date: '2026-09-03 07:35'
labels: []
dependencies: []
parent_task_id: TASK-006
ordinal: 40000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 Task 4. tasks.py with six kinds (summary, action_items, chapters, minutes, blog, custom), Jinja prompts with PROMPT_VERSION, pydantic schemas, schema-constrained decoding with a JSON repair pass; llm job type on the runner; schema v4 extends llm_output with run_id, token counts and params.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each kind stores one row keyed by (media, kind, provider, model, prompt_version)
- [x] #2 Re-running adds a new row and leaves the old one
- [x] #3 A resumed job makes only the missing chunk calls
- [x] #4 Fenced JSON is repaired; prose where JSON was required fails with the text preserved
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_llm_tasks.py, part of the 217-passed phase 5 run. AC1 by test_each_kind_stores_one_validated_row_under_its_own_key, parametrised over every kind, which also revalidates the stored content against that kind's schema rather than trusting the row. AC2 by test_rerunning_the_same_task_adds_a_row_and_leaves_the_old_one. AC3 by test_a_resumed_task_only_makes_the_calls_whose_chunk_rows_are_missing, with test_a_resumed_task_ignores_chunk_rows_from_another_run pinning that resumption cannot pick up a neighbouring run's work. AC4 by test_a_fenced_json_answer_is_repaired, test_prose_around_the_object_is_recovered and test_prose_where_json_was_required_fails_with_the_text_preserved - the failure keeps the model's text so a bad answer can be read rather than guessed at. Composition with the real providers is covered separately by tests/test_llm_task_providers.py (commit 7a08a9c).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Six preset outputs as resumable map-reduce jobs. Verified by tests/test_llm_tasks.py: each kind stores one schema-valid row under its own key, a re-run adds a row without touching the old one, a resumed job makes only the missing chunk calls and ignores another run's rows, and fenced or prose-wrapped JSON is repaired while prose where JSON was required fails with the text preserved.
<!-- SECTION:FINAL_SUMMARY:END -->
