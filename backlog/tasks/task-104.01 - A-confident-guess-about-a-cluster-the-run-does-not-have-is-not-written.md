---
id: TASK-104.01
title: A confident guess about a cluster the run does not have is not written
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - llm
  - bug
dependencies: []
parent_task_id: TASK-104
ordinal: 187000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
apply_speakers (scribe/llm/tasks.py) writes any cluster the model names, so a confident SPEAKER_07 becomes a speaker with no words (run_speakers lists clusters that only have a label too). Verified in code 2026-10-05; no test covers it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A failing test: a guess at confidence 95 for a cluster with no words in the run writes no speaker_label row and no phantom speaker appears, red first
- [ ] #2 Guesses for real clusters behave as before (test_llm_speakers.py green)
<!-- AC:END -->
