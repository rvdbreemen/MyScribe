---
id: TASK-106.03
title: 'The transcript page opens on the recording, not the AI box'
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 08:36'
labels:
  - ui
dependencies: []
parent_task_id: TASK-106
ordinal: 197000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Measured: the page starts with the privacy strip and two rows of the same AI tasks (buttons and tabs); the title and back link sit below them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The back link and title come first and the AI tasks appear once; screenshot before and after
- [x] #2 Nothing in the AI panel loses a function
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured 2026-10-06, headless Chrome (CDP), synthetic library on port 4299, before = same run with the template/CSS changes stashed: title and back link moved above the AI region into _transcript_title.html (outside #transcript-panel, sent out of band on every panel refresh so a rename updates it). At 1440: h1 at y=406 below the AI box -> title at y=66, AI region at 236. At 390: h1 y=705 -> title y=92. Tests: test_the_page_opens_on_the_title... and test_a_rename_of_the_recording_updates_the_title_at_the_top, red with templates stashed, green after. OPEN: AC1's 'the AI tasks appear once' - the ask buttons and the answer tabs are still two rows; merging them changes what a click does (send vs view), question put to Robert.
<!-- SECTION:NOTES:END -->
