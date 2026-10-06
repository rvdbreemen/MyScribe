---
id: TASK-106.04
title: 'Phone widths: header, jobs, feeds and settings fit 390 px'
status: Done
assignee: []
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 08:36'
labels:
  - ui
dependencies: []
parent_task_id: TASK-106
ordinal: 198000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Measured at 390 px: feeds 748 px wide (unwrapped empty-state cell, no table-wrap); jobs breaks a word mid-way and cuts the history; the nav stacks beside the logo; most pages are 404 px.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Screenshots at 390 px of library, transcript, jobs, feeds and settings show no horizontal overflow
- [x] #2 Desktop screenshots keep their structure
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured 2026-10-06, headless Chrome (CDP), synthetic library on port 4299, before = same run with the template/CSS changes stashed: page width at 390: library 404->390, search 404->390, jobs 404->390, feeds 748->390, settings 405->390, transcript 404->390; topbar 122->74 px (nav wraps to its own line under the logo). Feeds table in a .table-wrap, cells wrap. Jobs summary cells break-word with a 9rem floor instead of 'anywhere'. Desktop widths unchanged (1440). No OVERFLOW-X in any of the 15 screenshots (5 pages x desktop light/dark + phone).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every page fits 390 px; the nav wraps under the logo; feeds no longer 748 px wide.
<!-- SECTION:FINAL_SUMMARY:END -->
