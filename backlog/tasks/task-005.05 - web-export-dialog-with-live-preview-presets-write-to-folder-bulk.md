---
id: TASK-005.05
title: 'web: export dialog with live preview, presets, write-to-folder, bulk'
status: Done
assignee: []
created_date: '2026-09-02 14:12'
updated_date: '2026-09-02 19:09'
labels: []
dependencies: []
parent_task_id: TASK-005
ordinal: 35000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 Task 5. GET /media/{id}/export dialog, POST preview (first 10 cues + violation summary), POST export (download single or ZIP; write-to-folder under fsbrowse roots), bulk action=export, preset CRUD in settings; enable the Export entries in the rail and the library menu.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Preview shows 10 cues and the violation summary
- [x] #2 Single format downloads with the right filename; three formats yield a ZIP
- [x] #3 Write-to-folder refuses paths outside the roots
- [x] #4 Bulk export over two media writes all files
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Export dialog with live preview and violation summary, presets, download or ZIP, write-to-folder under the fsbrowse roots, bulk export. Review CRITs fixed: ffmpeg no longer transcodes for non-HTML exports (055924d) and a cp1252 failure is a 400 naming the character instead of a 500 (b8a470d), plus size cap checked while building (f93613a) and write failures reported per file (a22a34b). Commit b54c132 lineage.
<!-- SECTION:FINAL_SUMMARY:END -->
