---
id: TASK-013
title: 'Export dialog: fewer fields in view, grouped by what the chosen formats need'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 19:16'
updated_date: '2026-09-06 20:57'
labels: []
dependencies: []
ordinal: 54000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert asked (2026-09-06) to improve the UX of the Export dialog. Today it shows eight format checkboxes and then six fieldsets of every option for every format at once (timestamps, speakers, subtitles, text, file), so a person exporting one SRT scrolls past Markdown and CSV settings. The dialog should show the options that matter for the ticked formats and keep the rest reachable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Options irrelevant to the ticked formats are hidden or collapsed, and appear when a format that uses them is ticked
- [x] #2 Preset and format choice stay above the fold at 900px height
- [x] #3 The preview pane remains live and every existing export test passes
- [x] #4 Before and after screenshots in the task notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Map each option group to the writers that read it (grep over scribe/exports). 2. data-for on each fieldset, body wrapped, legend suffix; a show-all tick under the formats. 3. CSS: one :has() selector per format sets custom properties; children read them. 4. Test the groups; measure in the browser.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Before: Robert's screenshot in the task description (all six groups open). After (Playwright Chrome on 4299, /media/1/export): SRT alone -> Timestamps and Text folded (23 px each), Subtitles/Speakers/File/Destination open; ticking md opens Timestamps and Text; show-all opens everything; preset row top 164 px, formats bottom 383 px. Preview still live (hx-sync replace aborts are htmx's own, pre-existing). Tests: test_web_exports.py 49 passed. Commit above.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Option groups fold to their legend unless a ticked format reads them, with a show-all tick as the way back; CSS-only via :has(). Verified by the dialog test and browser measurements.
<!-- SECTION:FINAL_SUMMARY:END -->
