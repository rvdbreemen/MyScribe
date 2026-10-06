---
id: TASK-107.05
title: 'A failed browser open is said, and the docs explain the busy-port proof'
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 21:54'
labels:
  - installer
  - docs
dependencies: []
parent_task_id: TASK-107
ordinal: 206000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
webbrowser.open returned True and opened nothing (default browser not running); nothing in the window or log. The clone proof cannot finish while a MyScribe holds 4242 (correct, exit 1) and the doc does not say so; the terminal sitting asks for an OpenAI key after Ollama was chosen.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The window and launcher.log give the URL to open when a browser open cannot be confirmed
- [x] #2 docs/macos-acceptance.md says the proof needs 4242 free; the OpenAI question is skipped once another provider is chosen, or the reason is recorded
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Browser: open_in_browser() says the address every time - 'Opening MyScribe in your browser. If no browser window appears, go to <url>' when webbrowser.open returns True (which is no confirmation: on the Mac it returned True and opened nothing), 'No browser could be opened. Go to <url>' when it returns False or raises. Used in both places the launcher opened the browser; on a second launch the line now comes before 'done', the line the window closes on. Red then green (test_launcher.py, 2 tests); launcher tests 181 passed, 2 skipped (one earlier run had the two known flaky running_instance failures, gone on rerun). OpenAI question: the console ask() ignored shown_if, so it asked for the OpenAI key after Ollama was chosen; it now skips a question whose condition does not hold, as the Tk window does (ADR-015). Red: the key was asked ('sk-asked-anyway' in the answers); green: test_setup_plan + test_setup 197 passed. Docs: docs/macos-acceptance.md says the proof needs 4242 free.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A browser that does not open is no longer silent: the window and launcher.log always give the address. The terminal sitting no longer asks for a provider's key after another provider was chosen (shown_if honoured, as in the window). The macOS acceptance list says the install proof needs port 4242 free.
<!-- SECTION:FINAL_SUMMARY:END -->
