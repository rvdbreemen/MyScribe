---
id: TASK-005.02
title: 'exports: subtitle segmentation engine with compliance report'
status: Done
assignee: []
created_date: '2026-09-02 14:12'
updated_date: '2026-09-02 19:09'
labels: []
dependencies: []
parent_task_id: TASK-005
ordinal: 32000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 Task 2. cues.build(doc, options) -> CueSet with deterministic cues: speaker change is a hard break, prefer sentence end then comma then largest silence, DP line balancing under cpl, min_duration extension, cue_gap, cps and cpl violations reported.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Speaker change always starts a new cue; no cue over cpl*max_lines
- [x] #2 Balanced lines preferred over greedy
- [x] #3 Violations reported for an unsplittable long word and for excessive cps
- [x] #4 Deterministic across runs
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
cues.build: speaker change is a hard break, sentence-then-comma-then-silence break preference, DP line balancing, min_duration extension, cue_gap, cps and cpl violations reported, deterministic. Follow-up 97d3fc7 stopped a cue starting before its predecessor ended. Commit 00f6c02.
<!-- SECTION:FINAL_SUMMARY:END -->
