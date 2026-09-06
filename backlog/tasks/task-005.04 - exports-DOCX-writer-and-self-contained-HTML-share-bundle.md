---
id: TASK-005.04
title: 'exports: DOCX writer and self-contained HTML share bundle'
status: Done
assignee: []
created_date: '2026-09-02 14:12'
updated_date: '2026-09-02 19:09'
labels: []
dependencies: []
parent_task_id: TASK-005
ordinal: 34000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 Task 4. python-docx document with speaker bold runs and hand-rolled hyperlink timestamps; HTML bundle with inline CSS, the app's transcript markup, data: audio under 25 MB or a relative sidecar above, and a 60-line inline player script; no external references.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 DOCX opens, one paragraph per rendered paragraph, hyperlink per timestamp
- [x] #2 HTML bundle has no http(s) references and seeks on click
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
python-docx document with bold speaker runs and hand-rolled hyperlink timestamps; self-contained HTML bundle with inline CSS/JS, data: audio under the cap and a relative sidecar above, no external references. Commit ac2f46f.
<!-- SECTION:FINAL_SUMMARY:END -->
