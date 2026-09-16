---
id: TASK-074
title: '"Saved ✓" is shown on Settings buttons that saved nothing'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:05'
updated_date: '2026-09-16 17:08'
labels:
  - review-2026-09-16
  - web
  - ui
dependencies: []
priority: low
type: bug
ordinal: 119000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low - wrong feedback, nothing stored wrongly). app.js remembers the submit button a person pressed inside a settings card and, after a successful POST swaps the card, marks its successor 'Saved ✓' for five seconds. Every POST from a settings form gets that mark, including 'Test now' on an AI provider, which queues a test job and saves nothing: the button says Saved while the card beside it says the test is queued. (The review's second example, 'Fetch list', does persist the list - settings.py remembers the models - so it is right to say Saved there.)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A settings form marked data-saves-nothing gets neither 'Saved ✓' nor 'Not saved ✗' on its button, and the 'Test now' form carries that mark on the rendered page
- [x] #2 A form without the mark is marked as before, pinned by the existing DOM tests
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_web_ai - the provider test form carries data-saves-nothing; test_web_settings (node) - a form with data-saves-nothing gets neither Saved nor Not saved after a 200 swap or a 500.
2. Green: app.js records no press for a form with data-saves-nothing and the responseError handler skips such a form; _settings_llm.html marks the Test now form.
3. Run test_web_ai and test_web_settings one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_the_test_form_says_it_saves_nothing (no data-saves-nothing on the rendered form) and test_a_form_that_saves_nothing_gets_neither_mark (the successor button said Saved). Green after app.js skips a data-saves-nothing form in the click handler and the responseError handler, and _settings_llm.html marks the Test now form: test_web_ai 108, test_web_settings 38 (the two existing Saved/Not saved DOM tests still pass and pin AC #2).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A settings form marked data-saves-nothing gets neither 'Saved ✓' nor 'Not saved ✗'; the provider 'Test now' form carries the mark. Verified red-to-green by two tests, 146 tests across two files.
<!-- SECTION:FINAL_SUMMARY:END -->
