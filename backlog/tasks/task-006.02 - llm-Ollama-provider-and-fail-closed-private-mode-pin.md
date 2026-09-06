---
id: TASK-006.02
title: 'llm: Ollama provider and fail-closed private-mode pin'
status: Done
assignee: []
created_date: '2026-09-02 16:32'
updated_date: '2026-09-03 07:34'
labels: []
dependencies: []
parent_task_id: TASK-006
ordinal: 38000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 Task 2. ollama.py native /api/chat with explicit num_ctx and keep_alive, /api/tags for models, Unreachable vs ModelNotFound; privacy.py is_private (media or any ancestor folder) and assert_allowed raising PrivacyRefused before a request is built; schema v3 adds media.private and folder.private.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A private media refuses every cloud provider and allows Ollama
- [x] #2 The refusal happens before the transport is touched
- [x] #3 num_ctx appears in the posted body; a missing model is ModelNotFound
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_llm_ollama.py and tests/test_llm_privacy.py (19 passed on its own; both included in the 217-passed phase 5 run). AC1 by test_a_private_media_refuses_every_cloud_provider and test_a_private_media_still_reaches_a_local_provider, with the folder tree covered by test_a_media_in_a_nested_private_folder_is_private and the cycle guard by test_a_private_folder_in_a_cycle_is_still_found. AC2 by test_the_refusal_happens_before_the_transport_is_ever_touched - fail-closed means the refusal cannot depend on a request being built, and test_an_unknown_media_is_an_error_rather_than_permission_to_send pins the direction the unknown case falls. AC3 by test_the_posted_body_asks_for_the_context_window_and_the_budget_it_needs (body['options']['num_ctx'] == 8192) and test_a_model_that_is_not_pulled_is_model_not_found_carrying_the_id.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ollama provider spoken to natively at /api/chat, plus the fail-closed private-mode pin. Verified by tests/test_llm_ollama.py and tests/test_llm_privacy.py: a private recording refuses every cloud provider and still reaches Ollama, the refusal happens before any transport is touched, num_ctx reaches the posted body, and an unpulled model is ModelNotFound carrying its id.
<!-- SECTION:FINAL_SUMMARY:END -->
