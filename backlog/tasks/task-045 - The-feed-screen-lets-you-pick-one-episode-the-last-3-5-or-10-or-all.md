---
id: TASK-045
title: 'The feed screen lets you pick: one episode, the last 3, 5 or 10, or all'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 02:16'
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
- [x] #1 The feed screen offers: one episode (the ticked one), the last 3, the last 5, the last 10, and all - with the count each choice would queue shown before it is made
- [x] #2 "The last N" counts from the newest episode the feed lists, and stops at what the feed has when it holds fewer
- [x] #3 The choice queues at the priority TASK-046 sets, and the queued count is reported back on the page
- [x] #4 A choice above MAX_FAN_OUT is refused with the number, as a pasted playlist already is
- [x] #5 Red then green: a test per choice over a feed of 12 episodes, asserting which jobs were queued and in which order
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-14 on the Feeds page, which is where TASK-044 AC3 says the question has to live. feeds.backfill_offer prices each choice against the listing recorded at subscribe time, minus the episode that already came - so "the last 3" reads "+2" and "everything" on a long archive reads what it is. A choice that would fetch nothing is not offered. feeds.backfill takes "3", "5", "10", "all" or "nothing": it probes, takes from the top of the listing, skips what is already seen, refuses above MAX_FAN_OUT with both numbers the way a pasted playlist is refused, queues at BULK_PRIORITY, marks seen and closes the question. Whatever the answer, the question closes - somebody who said "nothing more" is not asked again tomorrow.

The route is POST /feeds/{id}/backfill (form-encoded, htmx-swapped, 400 for a choice not on offer), and the question renders as a block in the feed row rather than a flash, because a flash is gone with the tab.

Red then green: tests/test_feed_backfill.py, 10 tests, and four more in tests/test_web_feeds.py that prove the question is on the page with its counts, that answering queues what was chosen and closes it, and that a choice not on offer is a 400 that leaves the question open.

Two things changed after the review.

An answer marked everything it queued as seen and closed the question, both unconditionally - so on a disk below TASK-043's floor one press of 'everything' queued 39 downloads that all failed DISK_LOW, marked 39 episodes seen, and never asked again. feeds.backfill checks the floor before it queues, marks or answers anything; the question stays open and the same button works once there is room. 'Nothing more' is deliberately still answerable on a full disk: it queues nothing, and refusing it would trap a person who wants the question gone precisely because the disk is full.

The count beside each choice comes from feed.backfill_total, which for the follow route arrives from a browser. It is capped at url_stage.MAX_FAN_OUT, because AC1 is 'the count each choice would queue' and a count the app would then refuse is not that.

And the durable half of an answer was unpinned: deleting the _mark_seen from backfill left all 58 feed tests green while the next poll re-queued the whole catalogue the person had just answered for. One test now asserts feed_seen after an answer and that the following poll queues nothing.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The Feeds page asks a new feed's question as a row rather than a flash, and offers one episode, the last 3, 5 or 10, or all - each choice showing what it would fetch before it is clicked, because 'everything' on a 2970-episode archive should look like what it is. A choice that would fetch nothing is not offered; 'the last N' stops at what the feed holds; a choice above MAX_FAN_OUT is refused with both numbers, the way a pasted playlist already is. Whatever the answer, the question closes: somebody who said 'nothing more' is not asked again tomorrow.

Everything an answer queues runs at url_stage.BULK_PRIORITY, and the queued count comes back on the page. Below TASK-043's disk floor the answer is refused before anything is queued, marked or closed - a 507 that leaves the question open - because marking-as-seen is what would have made a refusal permanent.

Verified: feeds.backfill_offer / feeds.backfill and POST /feeds/{id}/backfill; tests/test_feed_backfill.py, 14 tests, plus 5 in tests/test_web_feeds.py for the route. Mutants killed on a copy: the answer forgetting to mark what it queued as seen, and a choice queueing at the default priority. Suite green: 1331 passed, 1 skipped, and 861 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
