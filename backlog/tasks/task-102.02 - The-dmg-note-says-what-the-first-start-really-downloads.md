---
id: TASK-102.02
title: The dmg note says what the first start really downloads
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:32'
labels:
  - macos
  - installer
  - docs
dependencies: []
parent_task_id: TASK-102
ordinal: 178000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Open me first.txt says about 1 GB; measured on a Mac: 2.7 GB for the environment plus 1.5 GB of weights with the first transcription. Report section 2.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The note states the measured sizes and where they were measured, with a test pinning the text to its source
- [x] #2 Nothing else in the note changes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Red: a test on packaging/build_release.py's dmg note expects the measured sizes (2.7 GB environment, 1.5 GB models, where and when measured) and no 'about 1 GB'. Green: the note moves to a module constant DMG_NOTE used by dmg(), with the measured numbers.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: tests/test_build_release.py, AttributeError: no DMG_NOTE. Green: 2 passed (plus the existing build_release smoke-log test). The note moved verbatim to the constant DMG_NOTE except the size sentence.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The dmg's Open me first note now gives the sizes a Mac measured on 2026-10-03 (2.7 GB on disk for the speech engine, 1.5 GB for the models) instead of the unmeasured 'about 1 GB'; the text is a constant pinned by a test, the signing instructions unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
