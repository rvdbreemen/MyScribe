---
id: TASK-004.03
title: 'Library page: sidebar, table, FTS search, folders, file actions, bulk'
status: Done
assignee: []
created_date: '2026-09-02 07:28'
updated_date: '2026-09-02 13:46'
labels: []
dependencies: []
parent_task_id: TASK-004
ordinal: 25000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 3 Task 3. GET / with views recent/uncategorized/trash and folder filter, HX-Request fragment, /search over segment_fts with snippet and deep links, folders CRUD with non-empty guard, rename/move/trash/restore/purge, bulk actions, Range-capable download.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Trashed media hidden from / and shown under view=trash
- [x] #2 Search hit links to /media/{id}#t=<segment start>
- [x] #3 Download answers 206 with Content-Length 10 to Range bytes=0-9
- [x] #4 Folder delete refuses when non-empty unless force=1
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Library: views, folder tree via WITH RECURSIVE, FTS search with snippets and #t= deep links, rename/move/trash/restore/purge, bulk actions, Range download. Review fixes b54c132 -> 1d505f7; later hardening: purge refuses running jobs (c163068), purge removes proxy (741a38f). Commit b54c132.
<!-- SECTION:FINAL_SUMMARY:END -->
