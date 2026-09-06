---
id: TASK-006.06
title: 'web: AI panel, chat and provider settings with a visible privacy boundary'
status: Done
assignee: []
created_date: '2026-09-02 16:32'
updated_date: '2026-09-03 07:32'
labels: []
dependencies: []
parent_task_id: TASK-006
ordinal: 42000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 Task 6. Rail AI section enqueuing llm jobs with a polling pending panel, markdown rendered through an allowlist (never |safe on model output), provider/model select with a leaves-your-machine marker and a lock for private media, private toggles for media and folders, chat panel with seek links, settings provider table with availability and a test button.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Model output containing script tags is escaped in the rendered HTML
- [x] #2 A private media disables cloud options and the POST returns 403 when forced
- [x] #3 Chat answers render [m:ss] as seek links
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Settings gains a per-provider key field (masked, shows which source answered, storable locally in the setting table with the plaintext trade stated in the UI) and a model dropdown populated from the provider's live models() list.

Verified 2026-09-03 by running tests/test_web_ai.py: 83 passed in 22.58s. AC1 by test_a_free_text_answer_containing_a_script_tag_is_escaped_in_the_html, test_a_structured_answer_containing_a_script_tag_is_escaped_too and test_an_answer_containing_a_script_tag_is_escaped_on_the_chat_page. AC2 by test_a_private_medias_panel_disables_every_cloud_option_and_keeps_the_local_one, test_a_private_media_refuses_a_forced_cloud_provider_with_403_and_no_job and test_a_media_inside_a_private_folder_is_refused_the_same_way. AC3 by test_a_citation_renders_as_a_link_that_seeks_the_player and test_stored_chapters_render_as_links_that_seek. The description's two extra items are covered too: the stored-output panel by test_a_stored_output_shows_on_the_transcript_page_itself, and the per-provider test button end to end - _settings_llm.html:112 posts to /settings/llm/{name}/test, settings.py:504 calls ai_ui.queue_provider_test - exercised over HTTP at test_web_ai.py:926-1037 including a 404 for an unknown provider.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AI panel, chat and provider settings with a visible privacy boundary. Verified by tests/test_web_ai.py (83 passed): model output is escaped, a private media disables cloud options and answers 403 when forced, citations render as seek links, and the provider test button runs as a job through POST /settings/llm/{name}/test.
<!-- SECTION:FINAL_SUMMARY:END -->
