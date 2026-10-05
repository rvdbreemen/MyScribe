---
id: TASK-104.02
title: An automatic name is only written when the transcript supports it
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 14:26'
labels:
  - llm
dependencies: []
parent_task_id: TASK-104
ordinal: 188000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Confidence is the model's own claim; nothing checks that the name occurs in the transcript before apply_speakers writes it unattended.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A failing test: a name at confidence 95 whose words occur nowhere in the transcript is offered as a suggestion, not written, red first
- [x] #2 A name that does occur is still written; human names stay untouched
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Rule: a name is written unattended only when at least one of its words (2+ letters, case-folded) occurs in the transcript, the title or the file name - a name said aloud, or one the title gives. Otherwise it stays a suggestion. Red first; existing tests that wrote 'Arthur' for a transcript and title without it are moved onto a title that names him (diff shown).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: 'Zaphod Beeblebrox' at 97, in neither transcript nor title, was written. Green: name_is_supported - at least one word of the name (2+ letters, case-folded) in the run's words, the title or the file name stem; otherwise the cluster is 'left', i.e. a suggestion. Said aloud ('Marvin says') and given by the title ('Interview with Arthur Dent') are written (tests). Human names untouched (existing tests). The media fixture's title now names Arthur and Zaphod, the two names the older tests write; their intent is unchanged.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
An automatic speaker name is written only when the recording supports it - one of its words in the transcript, the title or the file name; otherwise it stays a suggestion in the panel. Red then green.
<!-- SECTION:FINAL_SUMMARY:END -->
