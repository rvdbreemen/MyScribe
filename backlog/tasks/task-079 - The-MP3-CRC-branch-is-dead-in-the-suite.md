---
id: TASK-079
title: The MP3 CRC branch is dead in the suite
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:34'
updated_date: '2026-09-16 17:40'
labels:
  - review-2026-09-16
  - tests
dependencies: []
priority: low
type: bug
ordinal: 124000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low - a test gap, no user-facing defect). playback.mp3_seeks_exactly reads the Info/Xing tag after the frame header, the optional 16-bit CRC and the side information; the CRC term (playback.py:152, two bytes when the protection bit is 0) is never exercised, because tests/mp3_headers.frame only writes frames with the protection bit set. Drop the CRC term and the suite stays green, while a CRC-protected CBR MP3 (LAME -p, some podcast encoders) would have its tag read two bytes early and be sent through a proxy it does not need. The review's second claim - that the side-info table mirrored in the helper lets a wrong table survive - was refuted by the verifier.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 tests/mp3_headers.frame can write a CRC-protected frame, with the tag where a decoder reads it past the CRC
- [x] #2 A test pins that a CRC-protected Info frame seeks exactly and that a tag at the CRC-less offset in a CRC-protected frame is not read as one, so dropping the CRC term fails the suite
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red evidence is by mutation: with the CRC term at playback.py:152 removed, the two new tests fail and the rest of test_playback stays green - which is the gap. The tests also pass against the code as it is.
2. tests/mp3_headers.frame gains crc=; test_playback gains a CRC-protected Info/Xing test and a tag-at-the-CRC-less-offset test.
3. Run test_playback; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence by mutation: test_playback 40 passed as is; with playback.py:152 changed to crc = 0 exactly the two new tests failed (2 failed, 38 passed); the file was restored (git checkout) and the suite is 40 passed again.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
tests/mp3_headers.frame takes crc=, clearing the protection bit and moving the tag past the two CRC bytes; two tests pin a CRC-protected Info frame seeking exactly and a tag at the CRC-less offset not being read. Verified by mutation: removing the CRC term fails exactly those two.
<!-- SECTION:FINAL_SUMMARY:END -->
