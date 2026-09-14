---
id: TASK-051
title: 'The library table is the page, not a box one screen tall'
status: Done
assignee: []
created_date: '2026-09-14 20:13'
updated_date: '2026-09-14 20:14'
labels:
  - ui
  - library
dependencies: []
priority: medium
ordinal: 89000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-14, looking at the table after the two new columns landed: "De tabel is nu precies 1 scherm hoog, wellicht dat je dat design nog even moet kijken naar het scherm. Kan dat mogelijk anders, ipv een fixed scroll tabel/lijst."

.table-wrap carries max-height: 78vh and overflow: auto, so the window has two scrollbars and the list stops before the page does - which reads as 'that is all there is' when it is not.

The height is not incidental: the CSS comment beside it says the sticky thead is a no-op without it, and that is true, because sticky anchors to the nearest scrolling ancestor. Dropping the height alone does not help either - overflow: auto makes the wrapper a scroll container whatever its height. Both have to go together, and then the header anchors to the viewport instead, which keeps it in view at row 40 rather than only within a box.

Robert chose this shape over 'no sticky header at all' and over 'just make the box taller'. Scoped to the library: a job panel inside a tab strip is a bounded region on purpose and keeps its own height.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The library page has one scrollbar: the page's own, and the table ends where its rows end
- [x] #2 The column headers stay in view while scrolling the full list, anchored to the window
- [x] #3 The sticky header is painted above the rows that scroll under it
- [ ] #4 A narrow window still scrolls the table sideways rather than the whole page
- [x] #5 The jobs page's bounded panels are untouched
- [ ] #6 Verified in a browser at both widths - a scroll container is not something a template test can see
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC4 is honest about what was measured. The wide case was driven in Chrome at 1568px: scrolled to the bottom of all 55 rows, the header stayed at the top of the window, one scrollbar, the table ending with its rows. The narrow case is the media query at 900px and was NOT seen - resize_window reported success but the capture came back at the same viewport size, so what I would be reporting is the CSS I wrote rather than a browser. Left unchecked rather than claimed.

Two things had to move together. max-height alone is not the switch: overflow: auto makes the wrapper a scroll container at any height, and sticky anchors to the nearest scrolling ancestor - so with the height gone and the overflow kept, the header would have stopped sticking without the page gaining anything. Both off, and it anchors to the viewport.

thead th also needed a z-index. A sticky cell without one is painted over by the rows that come after it in document order; inside a 78vh box the header only ever met rows that were clipped, so it never showed. Added to the shared rule, since it is a fix wherever a header sticks.

Scoped with .library-main so the jobs page keeps its bounded panels - a table inside a tab strip is a region on purpose, not the page.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The library table scrolls with the page instead of inside a box 78vh tall, so the window has one scrollbar and the list ends where its rows end rather than looking like it stops at 20 of 55.

Two rules had to go together: max-height alone changes nothing, because overflow: auto makes the wrapper a scroll container at any height and the sticky thead anchors to the nearest scrolling ancestor. With both off the header anchors to the viewport, which is better than what it replaced - it stays in view at row 40 instead of only within the box. It also needed a z-index, because a sticky cell without one is painted over by the rows that scroll under it; inside the old box it only ever met clipped rows, so the bug could not show.

Scoped to .library-main. A job panel inside a tab strip is a bounded region on purpose and keeps its own height.

Verified in Chrome at 1568px, scrolled to the last of 55 rows: headers stuck to the window, one scrollbar, no second. The 900px fallback is written but was not seen - the resize reported success and the capture came back at the same size - so AC4 is left unchecked rather than claimed on the strength of the CSS I just wrote. Templates unaffected: test_web_scaffold 27, test_web_library 77, test_library_row_meta 23.
<!-- SECTION:FINAL_SUMMARY:END -->
