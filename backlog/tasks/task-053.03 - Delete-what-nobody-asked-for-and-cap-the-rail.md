---
id: TASK-053.03
title: 'Delete what nobody asked for, and cap the rail'
status: To Do
assignee: []
created_date: '2026-09-14 21:08'
updated_date: '2026-09-14 22:36'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 94000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 3 of TASK-053. Nine empty 'Not asked yet.' cards render at the end of every recording page for a reader who never touches AI. The rail is position:sticky with top but no max-height and no overflow, so on a recording with many speakers its own bottom can become unreachable. And eleven unrelated actions silently destroy a typed question, the chosen provider and any open details, because the AI panel is re-rendered from settings on every panel refresh. This step also produces the number that decides step 5: the page height on media 20 after the empty cards are gone.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every kind keeps its card, asked or not - the menu of what this recording could be asked
- [x] #2 The rail cannot put its own bottom out of reach
- [x] #3 A typed question, the chosen provider and the open details survive a panel refresh
- [x] #4 Red then green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The empty-card gate was built and then reverted, on Robert's word: "de 9 antwoordkaarten zijn nog altijd nuttige kaarten". He is right and the plan was wrong: an unasked card says what this recording could be asked and holds the place its answer will land in. The complaint this rebuild came from was the distance to the answer, never the menu. The tests were rewritten to guard the kept behaviour rather than deleted.

Two measurements, one of which is a warning about measuring this page at all.

The rail is capped at the viewport with its own scroll. It measured 1,506px before and 785px after, against a 801px window - so on this recording its bottom was already 700px out of reach, and a recording with more speakers is worse.

And absolute positions on this page are NOT comparable between sessions. Yesterday the transcript measured 29,765px; today, with nothing about it changed, 30,266px - and that 501px is exactly the shift I first read as the answers moving. The cause is contain-intrinsic-size: auto on .para (app.css:1006): the 'auto' keyword makes the browser remember each paragraph's last rendered height, so the page's measured height depends on what that session happened to lay out. Card counts and element heights are sound; y-positions across sessions are not. Worth knowing before step 5 leans on one.
<!-- SECTION:NOTES:END -->
