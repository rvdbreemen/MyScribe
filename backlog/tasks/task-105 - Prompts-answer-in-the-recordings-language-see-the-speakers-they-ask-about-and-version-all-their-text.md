---
id: TASK-105
title: >-
  Prompts answer in the recording's language, see the speakers they ask about,
  and version all their text
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 21:05'
labels:
  - llm
dependencies: []
priority: medium
ordinal: 190000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
From the prompt review of 2026-10-05: structurally complete (StrictUndefined, schemas match parsers), but no prompt names the answer language, action items and minutes ask for owners the model cannot see, long-recording notes may drop speaker labels, and PROMPT_VERSION hashes only prompts/*.md.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Prompts name the transcript's language (gemma4:12b: English -> Dutch measured), the prompt version covers SYSTEM, goals and the source line (shown to bite), and the owner kinds see speaker names. PROMPT_VERSION 3.
<!-- SECTION:FINAL_SUMMARY:END -->
