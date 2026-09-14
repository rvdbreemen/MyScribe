---
id: TASK-044
title: A new feed fetches its newest episode and then asks how to go on
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 02:16'
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
- [x] #1 Subscribing to a feed queues exactly one ingest_url job, for the newest episode by the feed order the probe returns
- [x] #2 The rest of the feed is not queued and not marked seen, so answering "all" later still fetches them
- [x] #3 After that episode is queued, the person is asked how to continue - visible on the feed screen, not only as a one-off toast that a closed tab loses
- [x] #4 A feed that was already answered does not ask again; the watcher keeps following it as it does today
- [x] #5 Red then green: a test that subscribing queues one job and leaves the rest unseen, and one that the answer queues what it promised
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented 2026-09-14. Schema v15 adds feed.backfill_answered_at (NULL = still asking) and feed.backfill_total (what the listing held, so the question can price each choice without probing again). The migration stamps every existing feed as answered with its own created_at: they were subscribed under the old rule, which recorded the whole listing as seen, so their question genuinely is closed - without that, upgrading would stop every feed in the library.

feeds.subscribe now queues exactly newest_first(entries)[0] at the default priority, marks only that one seen, and leaves the question open; feeds.is_asking / asking / answer read and close it. newest_first is deliberately not a sort: urls._entries keeps the document order, timestamp is optional and a flat YouTube listing has none, so sorting would reorder half the sources this app accepts. The gate against polling sits in poll() rather than due_feeds(), because the Feeds page check-now button bypasses due_feeds entirely and a new feed is due the instant it exists.

The dialog path passes answered=True: a person who ticked episodes has answered the question.

Red then green: tests/test_feed_first_episode.py (12 tests, 10 red first). One golden moved with its reason written into the test: test_subscribing_queues_nothing_and_remembers_what_was_there is now test_subscribing_fetches_the_newest_episode_and_remembers_only_that. Twenty tests in test_feeds.py and one in test_web_feeds.py and one in test_params_door.py now say answered=True, because they poll an established feed - which is exactly what that flag means.

The adversarial review found the thing that mattered here: the question existed in the library and the app could not produce it. The only production caller of feeds.subscribe was ingest_ui._add_episodes, which always passed answered=True - correctly, because ticking episodes IS an answer - and a listing with nothing ticked was a 400. So AC1 and AC3 were pinned by tests that called feeds.subscribe directly, and no route reached them. The proof: deleting 'answered=True' from the route left 170 tests green while the app queued a duplicate download and then never polled the feed again.

'Follow, decide later' is the missing route (scribe/web/ingest_ui.py _follow_feed, POST /transcribe/url with follow_only=1). It posts the newest entry and the length of the listing rather than the listing itself - subscribe uses exactly those two for an unanswered feed, and 2970 JSON blobs have no business travelling through a form to say there are 2970. The length is capped at url_stage.MAX_FAN_OUT, because it arrives from a browser and becomes the count shown beside 'everything'.

So AC1 holds per route, and the two routes differ on purpose: Follow queues exactly one episode and asks (AC1, AC3); Import queues what was ticked and subscribes already-answered, which is AC4's case. Both are tested through HTTP.

Also closed: a refused poll now records checked_at (without it the watcher handed the same asking feed back on every tick, because NULL means due), and the reason a feed is not polled while asking is asserted rather than implied.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Adding a feed used to record its whole listing as seen and queue nothing, so the only way to hear anything was to tick episodes - and ticking 'all' on a podcast nobody had heard was five hundred downloads. A new feed now queues exactly one episode, the newest in the order the probe returned, marks only that one seen, and leaves the question open on the Feeds page.

Reachable from the app through 'Follow, decide later' (POST /transcribe/url with follow_only=1), which is the route this task describes. The other button is unchanged and is AC4's case: a person who ticked episodes has answered the question by answering it, so the listing is recorded as seen and nothing extra is queued.

That first episode runs at the normal priority - somebody is standing there waiting to hear whether the feed is any good - and everything else a feed queues runs at url_stage.BULK_PRIORITY (TASK-046). While the question is open the watcher leaves the feed alone, and the refused poll still records checked_at so it does not come back every tick.

Verified: schema v15 (feed.backfill_answered_at, feed.backfill_total); tests/test_feed_first_episode.py, 13 tests, and tests/test_feed_follow.py, 13 tests through the real HTTP route. Mutants killed on a copy: the follow dispatch removed (8 red), answered=True removed from the ticked import (1 red), the asking-poll recording nothing (1 red). Suite green: 1331 passed, 1 skipped, and 861 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
