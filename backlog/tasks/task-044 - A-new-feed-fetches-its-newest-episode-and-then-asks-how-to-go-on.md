---
id: TASK-044
title: A new feed fetches its newest episode and then asks how to go on
status: To Do
assignee: []
created_date: '2026-09-13 20:59'
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
