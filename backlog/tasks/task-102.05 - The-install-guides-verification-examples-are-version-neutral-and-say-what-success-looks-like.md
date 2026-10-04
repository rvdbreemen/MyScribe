---
id: TASK-102.05
title: >-
  The install guide's verification examples are version-neutral and say what
  success looks like
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:30'
labels:
  - docs
dependencies: []
parent_task_id: TASK-102
ordinal: 181000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
docs/installation.md shows MyScribe-0.7.2 in both verification examples, and gh attestation verify prints nothing at all on success (gh 2.88.1), so a reader cannot tell success from a no-op. Report sections 1 and 7.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Both examples use a placeholder version, and the attestation example says that no output with exit 0 means verified, and how to see more (--format json)
- [x] #2 No other part of the guide changes
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
docs/installation.md: both verification examples use MyScribe-<version>, with 0.8.2 as the example, and say that gh attestation verify prints nothing and exits 0 when the file verifies, and that --format json shows what it checked. Nothing else in the guide's verification section changed. Checked: no 0.7.2 left in the guide; the tests that read the guide pass.
<!-- SECTION:FINAL_SUMMARY:END -->
