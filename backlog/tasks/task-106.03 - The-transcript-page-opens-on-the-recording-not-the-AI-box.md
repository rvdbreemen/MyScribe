---
id: TASK-106.03
title: 'The transcript page opens on the recording, not the AI box'
status: Done
assignee: []
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 09:44'
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
- [x] #1 The back link and title come first and the AI tasks appear once; screenshot before and after
- [x] #2 Nothing in the AI panel loses a function
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured 2026-10-06, headless Chrome (CDP), synthetic library on port 4299, before = same run with the template/CSS changes stashed: title and back link moved above the AI region into _transcript_title.html (outside #transcript-panel, sent out of band on every panel refresh so a rename updates it). At 1440: h1 at y=406 below the AI box -> title at y=66, AI region at 236. At 390: h1 y=705 -> title y=92. Tests: test_the_page_opens_on_the_title... and test_a_rename_of_the_recording_updates_the_title_at_the_top, red with templates stashed, green after. OPEN: AC1's 'the AI tasks appear once' - the ask buttons and the answer tabs are still two rows; merging them changes what a click does (send vs view), question put to Robert.

Second half, Robert's choice 2026-10-06 ('Tab klikken = vragen', over 'only tabs + Ask' and 'keep two rows'): the row of eight Ask buttons is gone. A card of a never-asked task carries an Ask button (data-ask-now, type=submit form=ai-ask with formaction, so it still posts without scripting; hx-include=#ai-ask for provider/model/prompt); app.js presses it when that task's tab is clicked. An answered card has 'Ask again' and no data-ask-now, so a tab click on an answer only shows it. Custom keeps its ask beside the question box. Duplicate clicks are absorbed server-side (_already_queued). Real browser run (headless Chrome, port 4299, no provider chosen): one click on the Chapters tab -> exactly one POST /media/1/ai/chapters, the card shows the refusal sentence, tab selected, panel open, no .ai-menu in the page (scratchpad tabs-after.png). Tests: test_each_task_is_named_once..., test_an_answered_card_offers_ask_again..., test_a_tab_presses_the_ask_button... - red with the changes stashed, green after. Two test_web_ai tests sliced the region up to its first </section>, which is now a card; widened deliberately to the transcript panel.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The transcript page opens on title and back link; each AI task is named once, and a never-asked task's tab asks it. Measured and clicked in a real browser.
<!-- SECTION:FINAL_SUMMARY:END -->
