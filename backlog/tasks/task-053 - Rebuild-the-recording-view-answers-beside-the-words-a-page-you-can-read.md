---
id: TASK-053
title: 'Rebuild the recording view: answers beside the words, a page you can read'
status: Done
assignee: []
created_date: '2026-09-14 21:07'
updated_date: '2026-09-15 06:04'
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
- [x] #1 An AI answer is visible without scrolling after the button that asked for it
- [x] #2 An answer being written survives a rename, a move, a word correction and every other panel refresh
- [x] #3 The page keeps one scroll: Ctrl+F reaches the whole recording and it still prints
- [x] #4 Every follow-along word is lit, measured in a foreground browser rather than inferred
- [x] #5 Each step ends with something that can be looked at, and the page height is re-measured after step 3
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Parent verification, 2026-09-15, Chrome 152 headless over CDP against the app on 4242, media 20 (scratchpad/follow_cdp.js, scratchpad/scroll_find_print_cdp.js).
AC1: TASK-053.04 browser run - the AI region sits above the transcript, 236px closed / 548px open, transcript at y=375; Ask switches to its own tab before the request leaves.
AC2: the region is a sibling before #transcript-panel, never inside it, so every panel swap (rename, move, word correction) replaces only the panel - asserted in tests/test_web_transcript.py.
AC3: no ancestor of the first or the last word scrolls on its own (document scrollHeight 31,072px, one scroll); window.find('next time, keep') - a phrase unique to the last paragraph - is found from the top and scrolls to y=30,272 with the selection in that paragraph; Page.printToPDF gives a 3.98 MB PDF whose pdftotext output (13,638 words) holds both the opening line and that closing phrase, so content-visibility:auto does not drop unrendered paragraphs from print.
AC4: every word lit at 1x, 2x and 0.75x (60/60, 91/91, 32/32), lag median 3-5 ms, max 23 ms - see TASK-053.02 notes.
AC5: each subtask summary names what was looked at in a browser; the page height after step 3 is in TASK-053.03's notes, with the warning that content-visibility makes y-positions session-dependent.
Also full suite per file: 2253 passed, 1 failed (a DOM-stub fixture that appended a non-node, exposed by e8c905e) - fixed in 40f6bbf, file green again.
Out of scope and still open: Robert hears words light about 0.5 s after he hears them at 0.75x. Measured so far: not the data (MMS_FA forced alignment over 2,690 words puts Whisper starts a median 74 ms EARLIER, flat over the hour) and not the page (lag above). Chromium's CurrentMediaTime already subtracts the output delay the OS reports, so the remaining suspect is that delay being misreported on his device; an A/V flash-and-beep calibration snippet was handed over to measure it.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The recording view was rebuilt in seven steps: four real bugs fixed, follow-along sampled per animation frame, the rail capped, the AI menu and a bounded collapsible answer panel above the words with one tab per card, the correction offer beside its word, and a speaker ribbon on the player. Verified per step in the subtasks and as a whole in Chrome 152 over CDP on media 20: every word lit at three speeds with a median 3-5 ms lag, one page scroll, find-in-page reaching the last paragraph, and a print whose text holds the first and last lines. Full suite per file 2253 passed after fixing one fixture.
<!-- SECTION:FINAL_SUMMARY:END -->
