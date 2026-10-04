---
id: TASK-103
title: >-
  A glossary term whose first part is a common word doubles it: My Scribe
  becomes My MyScribe
status: To Do
assignee: []
created_date: '2026-10-04 07:07'
labels:
  - transcription
  - bug
dependencies: []
priority: low
ordinal: 185000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found while building TASK-102.04 (measured 2026-10-04): corrections_for with a user term 'MyScribe' on the words 'My Scribe' corrects only the one-word window 'Scribe' (fuzz.ratio 85.7 against 'MyScribe', at the threshold) and leaves 'My', so the transcript reads 'My MyScribe'. 'Mi Scribe' goes right (both words replaced), 'My Scribe' does not, which points at how overlapping windows are chosen, not at the threshold alone. Any user term whose first part is an ordinary word can hit this.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A failing test shows 'My Scribe' with the user term 'MyScribe' corrected to one 'MyScribe' (the two-word window wins), red first
- [ ] #2 The existing glossary tests (the 'met Marieke' guard against deleting words included) stay green
<!-- AC:END -->
