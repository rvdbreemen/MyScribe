---
id: TASK-006.03
title: 'llm: segment-boundary chunking with map-reduce planning'
status: Done
assignee: []
created_date: '2026-09-02 16:32'
updated_date: '2026-09-03 07:35'
labels: []
dependencies: []
parent_task_id: TASK-006
ordinal: 39000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 Task 3. estimate_tokens (pessimistic), plan() splitting only on segment boundaries with N segments of overlap and timestamped chunk text, needs_chunking, oversized single segments recorded rather than raised.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every chunk starts at a segment start and stays under budget
- [x] #2 Overlap repeats exactly N segments; ranges cover the whole media
- [x] #3 A segment larger than the budget is marked oversized, not an exception
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_llm_chunking.py, part of the 217-passed phase 5 run. AC1 by test_a_doc_under_budget_is_one_chunk_holding_the_whole_transcript and test_a_doc_over_budget_splits_into_chunks_that_each_fit. AC2 by test_overlap_repeats_exactly_the_last_n_segments (parametrised over overlap), test_time_ranges_are_contiguous_and_cover_the_whole_transcript, test_every_segment_reaches_at_least_one_chunk and test_with_no_overlap_the_chunks_still_leave_no_segment_out - coverage is asserted from both directions, so neither a gap nor a silently dropped tail can pass. AC3 by test_a_segment_bigger_than_the_budget_is_its_own_oversized_chunk: marked, not raised, because one long segment must not fail a whole recording. test_an_overlap_wider_than_a_chunk_still_advances pins that the loop cannot stall.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Segment-boundary chunking with map-reduce planning. Verified by tests/test_llm_chunking.py: every chunk starts at a segment start and stays under budget, overlap repeats exactly N segments, the ranges cover the whole recording with no segment dropped, and an oversized segment becomes its own marked chunk rather than an exception.
<!-- SECTION:FINAL_SUMMARY:END -->
