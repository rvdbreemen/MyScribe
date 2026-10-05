---
id: TASK-107.02
title: The install guide's Gatekeeper steps match macOS 26
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - docs
  - macos
dependencies: []
parent_task_id: TASK-107
ordinal: 203000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On macOS 26 the refusal dialog has no Open Anyway; it is in System Settings > Privacy & Security after the first attempt, with Touch ID or a password. Control-click > Open gives the same refusal. Until approved the app is killed (exit 137).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 docs/installation.md and the dmg note describe the macOS 26 route
- [ ] #2 The older route stays for earlier macOS, labelled as such
<!-- AC:END -->
