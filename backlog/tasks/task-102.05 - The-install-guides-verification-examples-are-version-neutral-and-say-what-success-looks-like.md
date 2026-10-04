---
id: TASK-102.05
title: >-
  The install guide's verification examples are version-neutral and say what
  success looks like
status: To Do
assignee: []
created_date: '2026-10-04 05:27'
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
- [ ] #1 Both examples use a placeholder version, and the attestation example says that no output with exit 0 means verified, and how to see more (--format json)
- [ ] #2 No other part of the guide changes
<!-- AC:END -->
