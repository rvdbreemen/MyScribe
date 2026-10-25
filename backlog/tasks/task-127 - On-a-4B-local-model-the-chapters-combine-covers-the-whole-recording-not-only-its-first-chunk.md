---
id: TASK-127
title: >-
  On a 4B local model the chapters combine covers the whole recording, not only
  its first chunk
status: To Do
assignee: []
created_date: '2026-10-25T21:10:00Z'
labels:
  - llm
  - ollama
dependencies: []
priority: low
ordinal: 243000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up of TASK-126 (Robert, 2026-10-25). On qwen3.5:4b the chapters combine on a long recording ignores the notes of the later chunks. Media 61 (2h28m Dutch), TASK-126 job 430 at ab378fa on a library copy: the notes of all 3 chunks reach each chunk's end (last stamps 57:12, 1:53:26, 2:24:28), yet all 11 chapters start by 54:40 and their titles match chunk 0's notes one for one. Evidence: session scratchpad run-126 (ev-chapters-430.txt, notes-chapters-430.txt). Wanted: chapters that cover the whole recording on a 4B model, or a measured, documented limit (e.g. 9b covers it).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A red test or a measured run shows chapters drawn from one chunk only
- [ ] #2 Chapters on media 61 with qwen3.5:4b (or the chosen alternative) start in every chunk of the recording, shown live on a library copy
- [ ] #3 If not reachable on 4B, the limit is measured against 9b and documented where a local model is chosen
<!-- AC:END -->
