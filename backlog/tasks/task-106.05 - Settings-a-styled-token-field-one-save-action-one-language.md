---
id: TASK-106.05
title: 'Settings: a styled token field, one save action, one language'
status: Done
assignee: []
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 08:36'
labels:
  - ui
dependencies: []
parent_task_id: TASK-106
ordinal: 199000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Measured: the Hugging Face token field is unstyled under the first Save with its own Save token; Maximaal is Dutch in an English UI; the .settings max-width is overridden (hint lines about 1250 px).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Screenshot of the transcription settings with a styled token field and a readable line length
- [x] #2 UI text is English throughout, checked by a test over the templates
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured 2026-10-06, headless Chrome (CDP), synthetic library on port 4299, before = same run with the template/CSS changes stashed: token field '2px inset, radius 0' -> '1px solid, radius 6px' (input[type=password] joins the shared input rule), in its own bordered row; hint lines capped at 75ch. 'Maximaal' renamed 'Maximum' in 4 templates, jobs_ui, settings, transcribe_dialog (the name in ADR-004 stays as history; _macros notes the old name). Red: tests/test_ui_language.py found Maximaal in _macros, _settings_defaults, _settings_watch, _transcribe_options and jobs_ui; green after. Three tests that pinned 'Maximaal' updated with it. Not done: merging the token's own Save into the page's Save - the token posts to its own endpoint and clears separately; left as is, not in the criteria.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Styled token field, readable hint width, and an English-only UI with a template test; Maximaal is now Maximum.
<!-- SECTION:FINAL_SUMMARY:END -->
