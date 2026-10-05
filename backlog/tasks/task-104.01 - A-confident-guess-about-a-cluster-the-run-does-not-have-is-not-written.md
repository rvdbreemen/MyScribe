---
id: TASK-104.01
title: A confident guess about a cluster the run does not have is not written
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 14:26'
labels:
  - llm
  - bug
dependencies: []
parent_task_id: TASK-104
ordinal: 187000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
apply_speakers (scribe/llm/tasks.py) writes any cluster the model names, so a confident SPEAKER_07 becomes a speaker with no words (run_speakers lists clusters that only have a label too). Verified in code 2026-10-05; no test covers it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A failing test: a guess at confidence 95 for a cluster with no words in the run writes no speaker_label row and no phantom speaker appears, red first
- [x] #2 Guesses for real clusters behave as before (test_llm_speakers.py green)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Red: a confident guess for a cluster with no words in the run writes nothing and no phantom speaker shows. Green: apply_speakers keeps only clusters that carry words in the run. The existing threshold test wrote 'Sure' for SPEAKER_02, a cluster the seeded run does not have - it pinned this bug; it is rewritten on the run's real clusters (diff shown).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: SPEAKER_07 at 95 was written ({'SPEAKER_07': ('Marvin','llm',95.0)}). Green: apply_speakers keeps only clusters with words in the run. The existing threshold test wrote 'Sure' for SPEAKER_02, which the seeded run does not have - it pinned this bug; rewritten as a parametrised test on SPEAKER_01 at 89.9 / 90 / 90.1 (written only at 90.1). test_llm_speakers 51, test_stage_finalize 35, test_llm_tasks 160, test_pipeline_e2e 54, test_web_ai 146, test_web_transcript 107 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A speaker pass no longer writes a name for a cluster the run's words do not carry, so no phantom speaker appears. Red then green; the old threshold test that pinned the bug moved onto a real cluster.
<!-- SECTION:FINAL_SUMMARY:END -->
