---
id: TASK-098
title: 'The first-start setup asks one question per screen, with Back and Next'
status: To Do
assignee: []
created_date: '2026-09-27 08:37'
labels:
  - installer
  - setup
  - ui
dependencies: []
priority: medium
ordinal: 172000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-27, on the 0.7.0 installer: the Set up MyScribe window is one long scrolling form - what was found, the library, which folder, the AI provider, transcription quality, the watch folder, start at login - each with its options, their explanations, a Skip checkbox and a paragraph about skipping. Too long and too many questions at once. He wants one question per screen and a Next button to go to the next one. ADR-015 holds: the setup engine owns the questions and their order (the JSON contract); the launcher's Tk window only renders it, so this is a change to how the window renders, not to which questions exist. The same applies to any other front-end that renders the contract.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The window shows one question per screen, with Back and Next; the last screen shows a short summary of the answers and a Finish that applies them
- [ ] #2 What MyScribe found on this machine is its own first screen, shorter than today, and a question whose answer was found is not shown as a question
- [ ] #3 A question that depends on an earlier answer appears only when it applies (the library folder only after 'I already have one')
- [ ] #4 Each screen fits without scrolling at the window's size on a 1080p display; the skip explanation is one line, with the details behind a link or a smaller text
- [ ] #5 Skip is a button on the screen, not a checkbox, and moves on; nothing is written for a skipped question, as today
- [ ] #6 The engine contract and its tests are unchanged; the launcher's rendering tests cover paging, Back keeping answers, conditional screens and Finish; a real run on this laptop with a screenshot per screen
<!-- AC:END -->
