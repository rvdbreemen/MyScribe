---
id: TASK-102.01
title: 'The macOS app says its own version in Finder, not 0.0.0'
status: To Do
assignee: []
created_date: '2026-10-04 05:27'
labels:
  - macos
  - installer
dependencies: []
parent_task_id: TASK-102
ordinal: 177000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CFBundleShortVersionString is 0.0.0 in the shipped bundle (0.6.0 and 0.8.0), while the window and --version say the real version; Finder's Get Info shows 0.0.0. Report section 1.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The frozen MyScribe.app's Info.plist carries scribe.__version__ as CFBundleShortVersionString and CFBundleVersion, covered by a test of the step that writes it
- [ ] #2 The bundle still verifies with codesign --verify --deep --strict after the change (the release's macOS build and smoke stay green)
<!-- AC:END -->
