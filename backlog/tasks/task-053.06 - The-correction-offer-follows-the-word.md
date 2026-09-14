---
id: TASK-053.06
title: The correction offer follows the word
status: Done
assignee: []
created_date: '2026-09-14 21:08'
updated_date: '2026-09-14 23:32'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 97000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 6 of TASK-053. Correct a word at 58 minutes and 'Saved. 14 other words say X - replace them too?' renders at the top of the main column, up to 25,000px away, and the swap drops focus entirely. The offer to fix the same mistake everywhere is the feature's whole payoff and it is invisible to the person who just triggered it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The offer names the word it is about, so it can be moved to it
- [x] #2 It is placed beside that word rather than at the top of the main column
- [x] #3 Focus follows it, because the swap drops focus to the document
- [x] #4 It is still rendered where it works with scripting off
- [x] #5 It is moved by index, never by measuring a box
- [x] #6 Red then green
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Correcting a word at 58 minutes reported the result at the top of the main column - up to 25,000px away on a 64-minute recording - and the swap dropped focus to the document. The offer to fix the same mistake everywhere is the feature's whole payoff, and it was being shown to a screen nobody was looking at.

The offer already carried the index of its word; it just never said so in the markup. It does now, and app.js moves it next to that word after the swap and puts focus on its button.

By index and not by geometry, deliberately: content-visibility: auto on .para makes off-screen boxes unreliable, so anything positioned by measurement would work on screen and fail for the paragraph you actually corrected - which is always the one off screen. The test asserts getBoundingClientRect does not appear in that function.

The server still renders it in the main column, because that is where it works with scripting off: visible and reachable, just further from the word.

Verified: 3 new tests in tests/test_web_transcript.py (76 in the file). One of them needed a word the seeded transcript repeats - ' the', seven times - because correcting a unique word produces no offer at all, which is correct and was briefly mistaken for a failure.
<!-- SECTION:FINAL_SUMMARY:END -->
