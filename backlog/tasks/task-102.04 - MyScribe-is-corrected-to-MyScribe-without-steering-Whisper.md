---
id: TASK-102.04
title: 'MyScribe is corrected to MyScribe, without steering Whisper'
status: To Do
assignee: []
created_date: '2026-10-04 05:27'
labels:
  - transcription
dependencies: []
parent_task_id: TASK-102
ordinal: 180000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The app's own name came back as MiScribe; the glossary was empty. Robert, 2026-10-04: a built-in term for the correction pass only, not for the hotwords, so a recording that never says the name is not biased towards it. Report section 3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A transcript word MiScribe (and My Scribe) gets a correction row to MyScribe through the existing correction layer (ADR-003), with no glossary row needed, red first
- [ ] #2 compose_hotwords does not contain MyScribe unless a user's own glossary has it; a test says so
<!-- AC:END -->
