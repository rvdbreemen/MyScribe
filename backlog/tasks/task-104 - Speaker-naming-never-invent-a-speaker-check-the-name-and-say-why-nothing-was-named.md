---
id: TASK-104
title: >-
  Speaker naming: never invent a speaker, check the name, and say why nothing
  was named
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
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
