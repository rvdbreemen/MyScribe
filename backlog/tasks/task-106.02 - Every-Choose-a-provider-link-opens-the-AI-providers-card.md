---
id: TASK-106.02
title: Every Choose a provider link opens the AI providers card
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 07:54'
labels:
  - ui
  - bug
dependencies: []
parent_task_id: TASK-106
ordinal: 196000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
SETTINGS_ANCHOR and two templates link /settings#llm-providers, but the page reads only ?section=, so they open the Defaults card. Verified in code.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The links use ?section=llm and a test follows one to the providers card, red first
- [x] #2 No other settings link changes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_every_choose_a_provider_link_opens_the_providers_card - /settings#llm-providers opened Defaults (ss-llm not checked). Green: SETTINGS_ANCHOR, _ai_region.html, chat.html and the llm_stage refusal sentence now use /settings?section=llm#llm-providers. Three test files pinned the old (broken) link; updated deliberately. test_web_settings 56, test_web_ai 146, test_web_library 86, test_llm_tasks 160.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every 'choose a provider' link (AI panel, chat, the stage's refusal sentence) now opens Settings on the AI providers card. Red then green.
<!-- SECTION:FINAL_SUMMARY:END -->
