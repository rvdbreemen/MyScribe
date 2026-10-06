---
id: TASK-107.03
title: A second setup window does not look like a hang
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 20:29'
labels:
  - installer
dependencies: []
parent_task_id: TASK-107
ordinal: 204000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When the speaker token turns out gated, a second window (Everything else was saved / Retry / Continue without) waits while the main window stays on Saving your answers...
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The main window's status says a choice is waiting, or the choice appears in the main window; tested
- [x] #2 A real run shows it, with a screenshot
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Red: in the window harness, a sequence that calls retry() leaves the main window's status and log saying a choice is waiting in another window. Green: run_window's retry() reports that sentence through the event queue before it opens the failure dialog (the worker never touches Tk, TASK-101), and the dialog is made transient to the main window and lifted so it is in front. Real run: the source launcher's real Tk window on Windows, screenshot.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red (behaviour, after only adding CHOICE_WAITING): the window log held only 'Saving your answers...'. First fix put the sentence on the event queue - and the harness test went green while a real Tk run on Windows still read 'Preparing...' with the dialog open: pump ran requests (the dialog, which runs inside the tick) before draining events. The test now records the main status at the moment the dialog opens (red: ['Preparing...']); pump drains events first. Green: test_launcher_sitting + test_launcher 179 passed, 2 skipped. Real Tk (Windows, python.org 3.12/Tcl 8.6), read from Tk itself because a screenshot crop missed the windows under display scaling: main status 'A choice is waiting in the Set up MyScribe window: Retry, or Continue without.'; dialog 'Set up MyScribe' viewable and top of the stacking order (lift + focus_force).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
While the failure dialog (Retry / Continue without) waits, the main window now says a choice is waiting in the Set up window, and the dialog is lifted to the front; found and fixed on the way: the window drained its messages only after a dialog closed. Red then green, and a real Tk run shows it.
<!-- SECTION:FINAL_SUMMARY:END -->
