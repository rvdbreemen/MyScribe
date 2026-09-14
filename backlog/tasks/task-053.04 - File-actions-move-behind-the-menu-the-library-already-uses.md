---
id: TASK-053.04
title: 'The AI menu moves to the top, with tabs for the cards'
status: Done
assignee: []
created_date: '2026-09-14 21:08'
updated_date: '2026-09-14 23:11'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 95000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Steps 4 and 5 of TASK-053 became one shape when Robert saw the first three land: "Het moet wel heel eenvoudig benaderbaar zijn, de kern is en blijft het transcript. Wellicht is het handiger om een menu bovenaan het scherm te hebben, met de verschillende opties. En een soort van tabbladen of iets dergelijks om snel te schakelen tussen de verschillende kaarten."

So: a toolbar at the top of the page carrying the nine AI options, a tab strip under it with one tab per kind, and one answer panel below that - bounded in height and collapsible, scrolling inside itself. The transcript begins at the same place on every visit however long the answers grow. Robert chose bounded over full-height explicitly, and chose to move ONLY the AI buttons: Take away, File and Speakers stay in the right-hand column.

Two constraints the shape has to keep.

The refresh rule: #ai-outputs must stay OUTSIDE #transcript-panel, which swaps its own outerHTML, or a rename destroys an answer being written. Moving the region ABOVE the panel keeps it a sibling, so the rule survives - and the privacy pin, which lives in the AI panel today and targets #transcript-panel, should target the new region instead so pinning stops rebuilding the whole transcript.

And every card keeps polling. A tab that is not selected is hidden, not absent: hx-trigger=every 2s only fires for an element in the DOM, so all nine stay rendered and all but one are hidden. Which means the trap: scrollIntoView and show: are no-ops inside a hidden container, so pressing a button must activate its tab AT CLICK TIME, before the request goes out.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The menu of what to ask sits above the transcript, not in the rail
- [x] #2 A tab per kind, and the tab strip says which hold an answer or are working
- [x] #3 Every card stays in the document so it keeps polling; only one is shown
- [x] #4 Asking switches to its own tab before the request leaves
- [x] #5 The answer panel is bounded and rests closed, so the transcript starts in the same place
- [x] #6 An answer being written still survives a rename
- [x] #7 Red then green, and seen in a browser
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured in Chrome on a snapshot of the real library, 801px window:

  closed   region 236px, transcript begins at y=375
  open     region 548px, transcript begins at y=687
  toggle   closes it again, tab and open-state both remembered per recording

Clicking a tab switches AND opens - a tab that opens nothing looks broken. The arrows walk the strip with roving tabindex.

One mistake worth recording, because it cost twenty minutes and it is the same one as the library columns. The page rendered with no tab selected and every slot hidden while panel_context clearly returned selected='labels'. The cause was an older app instance still holding port 4299: my restart failed to bind and said nothing, so the served ai_ui.py was the pre-edit one. Templates reload from disk, Python does not. Check what is LISTENING before believing a screenshot.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The nine AI options are a menu above the transcript, with a tab per kind and one bounded panel below them. The answer appears where the button that asked for it is, which is the fault this rebuild exists to remove.

Three properties carry it. The region sits above #transcript-panel and outside it, so a rename still cannot swap away an answer being written - asserted as 'a sibling, earlier' rather than 'follows', because the old test was the latter and that is the assertion somebody fixes instead of thinking about. Every card stays in the document and all but one are hidden, because hx-trigger only fires for an element that is in it - a card removed to save markup would stop polling and its answer would never arrive. And an Ask button switches to its own tab before the request leaves, because a swap into a hidden container is invisible: the same bug as the thirty thousand pixels, one layer up.

The pin now answers with the region rather than the whole transcript panel: pinning changes which providers are offered, not a single word.

Verified: 12 new tests in tests/test_web_transcript.py, and a browser run - closed the region is 236px with the transcript at y=375; open it is 548px; a tab switches and opens; the toggle closes it; both are remembered per recording. Suites green: transcript 73, ai 97, scaffold 27, exports 49.
<!-- SECTION:FINAL_SUMMARY:END -->
