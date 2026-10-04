---
id: TASK-102.03
title: Stopping the launcher stops the app it started
status: To Do
assignee: []
created_date: '2026-10-04 05:27'
labels:
  - installer
dependencies: []
parent_task_id: TASK-102
ordinal: 179000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After pkill MyScribe the child python -m scribe kept serving on 4242. Quit does the right thing; a SIGTERM to the launcher does not, because the app runs in its own session. Report section 7. A SIGKILL or crash cannot be caught by the launcher and stays out of scope; say so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A SIGTERM (and Ctrl+C) to the headless and the windowed launcher stops the app's process group, shown by a test that starts a real child and signals
- [ ] #2 Windows behaviour is unchanged and said so
<!-- AC:END -->
