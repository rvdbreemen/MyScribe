---
id: TASK-104
title: >-
  Speaker naming: never invent a speaker, check the name, and say why nothing
  was named
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 14:43'
labels:
  - transcription
  - llm
dependencies: []
priority: high
ordinal: 186000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
From the review of 2026-10-05 (prompt reviewer and flow check). The speaker pass runs automatically after every transcription (finalize queues it), but it can write a name for a cluster that does not exist, writes a name above 90 without checking it against the transcript, and is silent when no provider is chosen.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Speaker naming no longer invents speakers (only clusters the run's words carry), writes a name unattended only when the recording supports it (a word of the name in transcript, title or file name), and says when nothing could be named because no AI provider is chosen. Three subtasks, each red then green; two older tests that pinned the old behaviour moved deliberately.
<!-- SECTION:FINAL_SUMMARY:END -->
