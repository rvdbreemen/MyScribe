---
id: TASK-105.03
title: 'Action items and minutes can name owners, and chunk notes keep speaker labels'
status: To Do
assignee: []
created_date: '2026-10-05 06:56'
labels:
  - llm
dependencies: []
parent_task_id: TASK-105
ordinal: 193000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
action_items.md and minutes.md ask for an owner by name or speaker label but run with_speakers=False; map_chunk.md does not ask to keep SPEAKER_XX labels, so a long recording's speaker pass may lose them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 These kinds receive speaker display names, or the wording stops asking for labels; tested
- [ ] #2 map_chunk asks to keep each line's speaker label; a test with notes that drop labels shows the effect
<!-- AC:END -->
