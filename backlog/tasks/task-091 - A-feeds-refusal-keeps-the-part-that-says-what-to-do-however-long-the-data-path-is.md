---
id: TASK-091
title: >-
  A feed's refusal keeps the part that says what to do, however long the data
  path is
status: To Do
assignee: []
created_date: '2026-09-20 21:41'
labels:
  - feeds
  - ui
dependencies: []
references:
  - scribe/ingest/feeds.py
  - scribe/doctor.py
  - tests/test_feed_first_episode.py
priority: low
type: bug
ordinal: 163000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found on 2026-09-20 by running the suite with the data directory pointed at a long scratch path (TASK-090's fence).

`scribe/ingest/feeds.py:458` and `:464` store a poll's outcome as `result[:200]`. The disk-floor refusal reads "only 1.0 GB free at <path>, and this app keeps 10 GB clear on the drive your recordings land on. Free up space and retry; nothing was downloaded." (scribe/doctor.py:99-104). The path sits in the middle, so a long one pushes the actionable half over the 200-character edge.

Measured: from a data directory of 142 characters the amount the app needs is gone, and the Feeds page shows only "not checked: only 1.0 GB free at <path>". Robert's own paths are far shorter - 51 characters for the clone, 42 for a release install - so this is not his machine today. A user who keeps recordings in a deeply nested or cloud-synced folder can reach it, and Windows allows 260.

It surfaced as a failing test rather than as a report: tests/test_feed_first_episode.py:174 asserts the floor is in `last_result`, and it fails when the data directory path is long. The test is right; the message is what gives.

Not urgent, and not caused by the work it was found during.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red first: tests/test_feed_first_episode.py:150 is run with a data directory whose path is over 141 characters and fails on its `last_result` assertion, and that output is kept.
- [ ] #2 A refusal that is too long keeps what a person acts on - how much is free, how much is needed, and that nothing was downloaded - and gives up the middle of the path instead, or the whole path.
- [ ] #3 The 200-character store is still respected, and a test pins what a refusal looks like at the longest path Windows allows.
- [ ] #4 The same test passes with a short path and with a long one, and both runs are shown.
<!-- AC:END -->
