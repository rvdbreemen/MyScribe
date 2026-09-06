---
id: TASK-003.05
title: 'attribute: word-level speaker join (port from WHYcast)'
status: Done
assignee: []
created_date: '2026-09-02 02:32'
updated_date: '2026-09-02 02:51'
labels: []
dependencies: []
parent_task_id: TASK-003
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 Task 5. Pure functions, no GPU: turns_from_diarization, speaker_for_interval (max overlap, deterministic ties), attribute_words, fill_unattributed (forward then backward). Built before diarization because a silent bug here mislabels every speaker.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Word inside one turn gets that speaker; straddling word gets the larger overlap
- [x] #2 Gap words inherit previous speaker; leading gap inherits the first known
- [x] #3 Empty turn list leaves speakers None without raising
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ported from WHYcast with the max-overlap algorithm intact, adapted to word dicts. Ties go to the earlier turn and turns are sorted before matching, so results do not depend on upstream ordering. 22 tests pass covering overlap ties, straddling words, gap words carried forward, leading-gap backfill, unsorted turns, zero-length intervals and the no-diarization path. Commit 163d859.
<!-- SECTION:FINAL_SUMMARY:END -->
