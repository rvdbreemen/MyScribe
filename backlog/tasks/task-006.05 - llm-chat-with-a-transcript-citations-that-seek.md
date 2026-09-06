---
id: TASK-006.05
title: 'llm: chat with a transcript, citations that seek'
status: Done
assignee: []
created_date: '2026-09-02 16:32'
updated_date: '2026-09-03 07:35'
labels: []
dependencies: []
parent_task_id: TASK-006
ordinal: 41000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 Task 5. chat_tool.context_for (whole transcript when it fits, else FTS5 retrieval with neighbours in time order), ask() returning text plus parsed citations, parse_citations dropping timestamps past the media duration; chat_message table (schema v5); runs as an llm job under the same privacy rules.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Short transcript passed whole; long one retrieves with neighbours in time order
- [x] #2 Citations past the duration are dropped
- [x] #3 A private media refuses a cloud provider here too
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_llm_chat.py, part of the 217-passed phase 5 run. AC1 by test_a_transcript_that_fits_is_passed_whole and test_a_long_transcript_is_retrieved_around_the_question, with test_retrieval_is_in_time_order_however_the_hits_ranked pinning that retrieval hands the model time order rather than rank order, and test_retrieval_reads_this_recordings_current_run_and_nothing_else pinning that it cannot read a neighbouring recording. AC2 by test_parse_citations_drops_a_timestamp_past_the_end and test_an_answer_citing_past_the_end_stores_only_what_can_be_seeked - a citation that cannot seek is not stored, so the player never gets a link that goes nowhere. AC3 by test_a_private_media_refuses_a_cloud_provider_here_too; the rendered seek link is covered on the web side by test_web_ai.py::test_a_citation_renders_as_a_link_that_seeks_the_player.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Chat with a transcript, citing timestamps that seek. Verified by tests/test_llm_chat.py: a short transcript is passed whole and a long one is retrieved around the question in time order from the current run only, citations past the recording's end are dropped rather than stored, and a private recording refuses a cloud provider on this path too.
<!-- SECTION:FINAL_SUMMARY:END -->
