---
id: TASK-045
title: 'The feed screen lets you pick: one episode, the last 3, 5 or 10, or all'
status: To Do
assignee: []
created_date: '2026-09-13 20:59'
updated_date: '2026-09-13 21:00'
labels:
  - feeds
  - ux
dependencies:
  - TASK-044
ordinal: 83000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-13: "In het feed scherm moet het mogelijk zijn om te kiezen voor een specifieke aflevering van de feed, of de laatste 3-5-10 afleveringen, of alles."

The dialog today lists the episodes a probe returned and lets a person tick them one by one. That is fine for three and useless for two hundred: the common answers are "just this one", "the last few" and "everything", and only the first is reachable now without 200 clicks.

This is the answer TASK-044 asks for, so the two ship together: the same choice serves a new feed and a feed already followed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The feed screen offers: one episode (the ticked one), the last 3, the last 5, the last 10, and all - with the count each choice would queue shown before it is made
- [ ] #2 "The last N" counts from the newest episode the feed lists, and stops at what the feed has when it holds fewer
- [ ] #3 The choice queues at the priority TASK-046 sets, and the queued count is reported back on the page
- [ ] #4 A choice above MAX_FAN_OUT is refused with the number, as a pasted playlist already is
- [ ] #5 Red then green: a test per choice over a feed of 12 episodes, asserting which jobs were queued and in which order
<!-- AC:END -->
