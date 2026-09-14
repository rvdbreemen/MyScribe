---
id: TASK-053
title: 'Rebuild the recording view: answers beside the words, a page you can read'
status: To Do
assignee: []
created_date: '2026-09-14 21:07'
labels:
  - ui
  - transcript
dependencies: []
priority: high
ordinal: 91000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-14, on /media/20: "On the bottom of the screen you have some useful things, however it's at the bottom. On the right you have some things you can do with a recording."

Measured on media 20 (1h04m, 12,108 words, viewport 801px): the page is 31,645px - 39.5 screens - and the transcript is 94% of it. #ai-outputs begins at y=30,069. Press Summary in the rail and the answer arrives 37 screens below the button.

That address is the price of a correct decision, documented where it was made (_transcript_panel.html:221): the AI answers sit outside #transcript-panel so a rename cannot swap away an answer being written. #transcript-panel swaps its own outerHTML. The rebuild must keep that property and still put the answer where the eye is.

Robert chose, after reading the plan: answers dock BESIDE the words rather than above them; the page stays a DOCUMENT (one scroll, so Ctrl+F, print and save-page-as keep working) rather than becoming a two-pane application shell; and the file actions move behind the popover menu the library page already ships. He asked for all four improvements and the whole rearrangement.

Plan, with the wireframes and the full evaluation: https://claude.ai/artifact/Lg1uwK5s1edQPcN4Sjw1Kq

Seven steps, and the order does work: step 3 produces the page height that decides whether step 5 is worth its 150 lines.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 An AI answer is visible without scrolling after the button that asked for it
- [ ] #2 An answer being written survives a rename, a move, a word correction and every other panel refresh
- [ ] #3 The page keeps one scroll: Ctrl+F reaches the whole recording and it still prints
- [ ] #4 Every follow-along word is lit, measured in a foreground browser rather than inferred
- [ ] #5 Each step ends with something that can be looked at, and the page height is re-measured after step 3
<!-- AC:END -->
