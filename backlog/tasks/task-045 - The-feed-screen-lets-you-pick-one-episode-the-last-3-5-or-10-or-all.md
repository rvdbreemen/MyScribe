---
id: TASK-045
title: 'The feed screen lets you pick: one episode, the last 3, 5 or 10, or all'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 00:14'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-14 on the Feeds page, which is where TASK-044 AC3 says the question has to live. feeds.backfill_offer prices each choice against the listing recorded at subscribe time, minus the episode that already came - so "the last 3" reads "+2" and "everything" on a long archive reads what it is. A choice that would fetch nothing is not offered. feeds.backfill takes "3", "5", "10", "all" or "nothing": it probes, takes from the top of the listing, skips what is already seen, refuses above MAX_FAN_OUT with both numbers the way a pasted playlist is refused, queues at BULK_PRIORITY, marks seen and closes the question. Whatever the answer, the question closes - somebody who said "nothing more" is not asked again tomorrow.

The route is POST /feeds/{id}/backfill (form-encoded, htmx-swapped, 400 for a choice not on offer), and the question renders as a block in the feed row rather than a flash, because a flash is gone with the tab.

Red then green: tests/test_feed_backfill.py, 10 tests, and four more in tests/test_web_feeds.py that prove the question is on the page with its counts, that answering queues what was chosen and closes it, and that a choice not on offer is a 400 that leaves the question open.
<!-- SECTION:NOTES:END -->
