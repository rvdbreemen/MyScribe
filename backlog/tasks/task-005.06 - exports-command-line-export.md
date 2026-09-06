---
id: TASK-005.06
title: 'exports: command-line export'
status: Done
assignee: []
created_date: '2026-09-02 14:12'
updated_date: '2026-09-02 19:09'
labels: []
dependencies: []
parent_task_id: TASK-005
ordinal: 36000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 4 Task 6. python -m scribe.export <id|--all|--folder ID> --format ... --preset ... --out DIR with the same options as flags.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Single media export writes the file; --all skips media without a transcript with a message
- [x] #2 Bad format exits 2 with usage
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
python -m scribe.export with the same options as flags, --all and --folder, --preset, --out. Verified with a real export to a temp directory.
<!-- SECTION:FINAL_SUMMARY:END -->
