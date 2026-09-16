---
id: TASK-076
title: >-
  inherit_speaker_names carries the name a person typed but drops the colour
  they picked
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:20'
updated_date: '2026-09-16 17:26'
labels:
  - review-2026-09-16
  - pipeline
dependencies: []
priority: low
type: bug
ordinal: 121000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low - the shipped page has no colour control, so the colour reaches speaker_label only through the rename route's optional color field, but the route takes it and the transcript view paints with it). finalize.inherit_speaker_names copies a label to the new run after a re-transcription - display_name, source, llm_output_id, confidence - and reads and writes no color, so a speaker who had one comes back to the new run without it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 After a re-transcription that inherits a label, the new run's label carries the old run's colour
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_stage_finalize - a label with a colour on the old run reaches the new run with that colour.
2. Green: inherit_speaker_names selects and inserts color beside the other columns.
3. Run test_stage_finalize and test_web_transcript one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_an_inherited_label_keeps_the_colour_it_had failed (color None on the new run). Green after color joins the SELECT and the INSERT in inherit_speaker_names: test_stage_finalize 25, test_pipeline_e2e 54 (one stalled run on the Windows socketpair issue, green on the solo rerun).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
inherit_speaker_names copies the colour with the rest of the label. Verified red-to-green by one test, 79 tests across two files.
<!-- SECTION:FINAL_SUMMARY:END -->
