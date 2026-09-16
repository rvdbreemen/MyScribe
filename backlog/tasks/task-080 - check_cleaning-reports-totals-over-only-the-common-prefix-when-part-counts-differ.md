---
id: TASK-080
title: >-
  check_cleaning reports totals over only the common prefix when part counts
  differ
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:41'
updated_date: '2026-09-16 17:42'
labels:
  - review-2026-09-16
  - tests
  - llm
dependencies: []
priority: low
type: bug
ordinal: 125000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low - the refusal itself is right, its numbers are not). check_cleaning pairs source and cleaned parts with zip, so when the cleaning came back in a different number of parts the words_in and words_out it reports - the numbers _keep_verdict stores on the answer and the transcript page quotes with the refusal - cover only the common prefix, and the per-part ratios and the copy check compare parts that do not belong together (the mismatch test's own docstring says comparing them pairwise would compare the wrong things). The test at tests/test_llm_cleaning_gate.py:68 asserts only that the reason string mentions part(s), so the wrong totals were never noticed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 When the part counts differ, words_in and words_out count every part on both sides, no part is compared with a part that is not its own, and the reasons name the count mismatch and nothing built on a wrong pairing
- [x] #2 The mismatch test pins the totals, the empty per-part list and the exact reasons
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: the mismatch test pins words_in 1000, words_out 900, chunks [] and reasons == [the count reason] for source [500, 500] and cleaned [900]; today it gets words_in 500, one paired chunk and extra reasons built on that pairing.
2. Green: check_cleaning totals over every part on both sides; when the counts differ the per-part list is empty, so no ratio, copy or per-part reason rests on a wrong pairing.
3. Run test_llm_cleaning_gate and test_llm_tasks one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: the sharpened mismatch test failed with two extra reasons built on the wrong pairing ('the whole reading is 180% of the words' and 'part 0 is 180%') and words_in 500. Green after totals over every part and an empty per-part list when the counts differ: test_llm_cleaning_gate 25, test_llm_tasks 120.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
check_cleaning counts words_in/words_out over every part on both sides and pairs parts only when the counts match; a count mismatch is refused with that reason alone. Verified red-to-green by the sharpened mismatch test, 145 tests across two files.
<!-- SECTION:FINAL_SUMMARY:END -->
