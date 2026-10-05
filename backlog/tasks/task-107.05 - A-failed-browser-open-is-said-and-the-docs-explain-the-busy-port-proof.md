---
id: TASK-107.05
title: 'A failed browser open is said, and the docs explain the busy-port proof'
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - installer
  - docs
dependencies: []
parent_task_id: TASK-107
ordinal: 206000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
webbrowser.open returned True and opened nothing (default browser not running); nothing in the window or log. The clone proof cannot finish while a MyScribe holds 4242 (correct, exit 1) and the doc does not say so; the terminal sitting asks for an OpenAI key after Ollama was chosen.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The window and launcher.log give the URL to open when a browser open cannot be confirmed
- [ ] #2 docs/macos-acceptance.md says the proof needs 4242 free; the OpenAI question is skipped once another provider is chosen, or the reason is recorded
<!-- AC:END -->
