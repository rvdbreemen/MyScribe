---
id: TASK-106.01
title: A long title does not push the library and search tables off the page
status: Done
assignee: []
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 08:36'
labels:
  - ui
  - bug
dependencies: []
parent_task_id: TASK-106
ordinal: 195000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Measured: one long title made the library 1829 px wide in a 1440 px window; Duration, Mode and Status left the card. app.css:402 makes every cell nowrap and td.title has no cap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A real render at 1440 px with a 150-character title has no horizontal overflow (screenshot); the title wraps or ellipsises
- [x] #2 The 390 px library shows status and duration for each row, not only the title
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured 2026-10-06, headless Chrome (CDP), synthetic library on port 4299, before = same run with the template/CSS changes stashed: library with a 150-char title at 1440: page 1829 px -> 1440 (search 1813 -> 1440); the title wraps (td.title white-space normal, overflow-wrap break-word). At 390 the library keeps Title, Duration, Status and the menu; Category, Labels, Uploaded and Mode hide below 600 px. Screenshots home-desktop-light / home-phone-light in the session scratchpad. First pass used overflow-wrap:anywhere and the 390 shot showed 'getranscribeer|d' broken mid-word - changed to break-word, reshot. Also th.mode no longer picks up the icon font size.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Long titles wrap instead of widening the library; the phone library shows title, duration, status and menu. Measured before/after.
<!-- SECTION:FINAL_SUMMARY:END -->
