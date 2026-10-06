---
id: TASK-106.06
title: Live regions and polling keep the user's place
status: Done
assignee: []
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 08:36'
labels:
  - ui
  - a11y
dependencies: []
parent_task_id: TASK-106
ordinal: 200000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Code review, not yet rendered: chat swaps its own form while polling and wipes a typed question; jobs polling may steal focus; AI answers sit in a replaced aria-live region; the cleaned reading is a pre that does not wrap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Chat keeps a typed question through a poll and the cleaned text wraps, each with a test or a recorded browser run
- [x] #2 AI answers are announced from a stable live region
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Chat: the poll now lives on #chat-answers (log + Thinking line), the form sits outside it, so a typed question and an open provider list survive a poll (test_the_chat_poll_leaves_the_question_form_alone). Considered hx-preserve on the textarea first; rejected because it would also keep a SENT question in the box. AI answers: aria-live moved from the polled section to the .ai-slot around it, which no poll replaces; test_web_transcript's live-region test changed deliberately (it pinned aria-live on the section). Jobs: every board button has a unique id so htmx restores focus after the 2 s swap (test_every_button_on_the_board_has_an_id...). .clean-text: white-space pre -> pre-wrap, page font (measured computed style). All three tests red with the template changes stashed, green with them.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Chat keeps a typed question through polls, AI answers announce from a stable live region, board focus survives polls, cleaned text wraps.
<!-- SECTION:FINAL_SUMMARY:END -->
