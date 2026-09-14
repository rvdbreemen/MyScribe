---
id: TASK-053.05
title: The answer docks beside the words
status: To Do
assignee: []
created_date: '2026-09-14 21:08'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 96000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 5 of TASK-053, and the one Robert's complaint is about. #ai-outputs keeps its place in the DOM - that is what preserves the rule that an answer being written survives a panel swap - and gains a fixed position painting it into the band beside the transcript. Needs a dock control and a width watcher so it degrades to full width when there is no room. Chosen over moving the answers above the words because a blog post allows 6000 output tokens and a clean transcript is as long as the input, so 'above' would open every later visit on screens of answer.
<!-- SECTION:DESCRIPTION:END -->
