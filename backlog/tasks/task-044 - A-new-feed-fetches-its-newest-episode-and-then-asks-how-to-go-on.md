---
id: TASK-044
title: A new feed fetches its newest episode and then asks how to go on
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 00:14'
labels:
  - feeds
  - ux
dependencies: []
priority: high
ordinal: 82000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-13: "Een feed wordt niet volledig gedownload, eerst de laatste aflevering, daarna altijd een vraag hoe verder."

Today adding a feed can queue a whole back catalogue at once: the dialog ticks what it lists (up to MAX_LISTED) and the watcher queues up to MAX_NEW_PER_POLL per poll. A person who pastes a podcast they have never heard gets 500 downloads, a night of GPU and a disk they did not agree to spend.

The rule: a feed that is new fetches one episode - the newest - and then asks. Nothing else is queued until a person answers. The answer is TASK-045 s dialog (one episode, the last 3, 5 or 10, or all).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Subscribing to a feed queues exactly one ingest_url job, for the newest episode by the feed order the probe returns
- [ ] #2 The rest of the feed is not queued and not marked seen, so answering "all" later still fetches them
- [ ] #3 After that episode is queued, the person is asked how to continue - visible on the feed screen, not only as a one-off toast that a closed tab loses
- [ ] #4 A feed that was already answered does not ask again; the watcher keeps following it as it does today
- [ ] #5 Red then green: a test that subscribing queues one job and leaves the rest unseen, and one that the answer queues what it promised
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-14. Schema v15 adds feed.backfill_answered_at (NULL = still asking) and feed.backfill_total (what the listing held, so the question can price each choice without probing again). The migration stamps every existing feed as answered with its own created_at: they were subscribed under the old rule, which recorded the whole listing as seen, so their question genuinely is closed - without that, upgrading would stop every feed in the library.

feeds.subscribe now queues exactly newest_first(entries)[0] at the default priority, marks only that one seen, and leaves the question open; feeds.is_asking / asking / answer read and close it. newest_first is deliberately not a sort: urls._entries keeps the document order, timestamp is optional and a flat YouTube listing has none, so sorting would reorder half the sources this app accepts. The gate against polling sits in poll() rather than due_feeds(), because the Feeds page check-now button bypasses due_feeds entirely and a new feed is due the instant it exists.

The dialog path passes answered=True: a person who ticked episodes has answered the question.

Red then green: tests/test_feed_first_episode.py (12 tests, 10 red first). One golden moved with its reason written into the test: test_subscribing_queues_nothing_and_remembers_what_was_there is now test_subscribing_fetches_the_newest_episode_and_remembers_only_that. Twenty tests in test_feeds.py and one in test_web_feeds.py and one in test_params_door.py now say answered=True, because they poll an established feed - which is exactly what that flag means.
<!-- SECTION:NOTES:END -->
