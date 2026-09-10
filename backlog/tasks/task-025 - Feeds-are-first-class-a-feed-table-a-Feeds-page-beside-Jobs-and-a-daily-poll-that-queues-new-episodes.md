---
id: TASK-025
title: >-
  Feeds are first-class: a feed table, a Feeds page beside Jobs, and a daily
  poll that queues new episodes
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-10 04:41'
updated_date: '2026-09-10 16:50'
labels: []
dependencies: []
type: feature
ordinal: 66000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-021 shipped one-shot feed import: paste an RSS feed or a YouTube channel, see its episodes, tick some, import. Robert then asked (2026-09-09) that a feed stay monitored once you start with it - checked daily or at startup - and that feeds exist beside jobs as something you can see and manage, a YouTube channel included. ADR-008 was revised for exactly this on 2026-09-09 (commit 674625c); it is the design of record and its first shape forbade what this task builds, so read the current version, not a memory of it. The decisions it records: a feed is a row (schema v11) with its URL, the title yt-dlp gave it, the poll interval, checked_at, the last result and a per-feed switch; a YouTube channel or playlist is the same row as an RSS feed because it is the same probe and the same job; the poller is its own daemon thread shaped like scribe/ingest/watching.py's Watcher (own connection, _survive around every step, reconcile before the loop, started from the lifespan at scribe/app.py:225-229) and explicitly NOT inside supervisor._loop, whose tick is jobs.claim_next and nothing else (scribe/supervisor.py:193-208); polling is decided by a stored checked_at against the feed's interval rather than by a sleep, so a closed lid and a restart lose nothing and 'check at startup' needs no second mechanism; new means unknown to ingest_ui.known_sources, which already compares media.source_id, the fragment-stripped media.source_url and live jobs over library and trash alike; subscribing imports no back catalogue and one poll queues at most MAX_NEW_PER_POLL. Two answers from Robert that the ADR should be read alongside: subscribing is a checkbox in the import dialog, ticked by default, so one episode can be plucked from a feed without binding yourself to it; and a new episode found by a poll is downloaded AND transcribed, bounded by the per-poll cap and the per-feed switch. The reason the cap matters is measured: url_stage.register enqueues a transcribe job unconditionally (:201) and media.ingest_path's deduped flag is reported but never acted on (media.py:276 and :325), so anything a poll lets through costs GPU. The Feeds page follows the jobs board's shape - one module under scribe/web/, one NAV entry in scribe/web/__init__.py, a page template and a self-polling fragment - and reuses the macros in scribe/templates/_macros.html.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A feed is a row (schema v11) carrying its URL, title, poll interval, checked_at, last result and a per-feed switch; an RSS feed and a YouTube channel are the same kind of row
- [ ] #2 The import dialog offers 'keep following this feed', ticked by default, and subscribing queues nothing by itself: the episodes present at that moment are recorded as known
- [ ] #3 A FeedWatcher thread polls the feeds that are overdue by their own interval, with its own connection and every step wrapped so one bad feed cannot end the loop; nothing periodic is added to supervisor._loop
- [ ] #4 A restart polls whatever is overdue, so 'check at startup' and 'check daily' are the same mechanism
- [ ] #5 An episode already in the library, in the trash or already queued is never queued again; one poll queues at most MAX_NEW_PER_POLL and says so in the feed's last result
- [ ] #6 A new episode found by a poll is downloaded and transcribed, unless the feed's switch says otherwise
- [ ] #7 A Feeds page beside Jobs lists each feed with its last check and what it found, and offers pause, poll now and unsubscribe; a feed whose probe keeps failing says so there
- [ ] #8 Tests cover due selection with a frozen clock, the no-back-catalogue rule, the per-poll cap, a poll that finds nothing, a poll whose probe raises, and the page's actions; a real run against a live feed is in the notes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Page, watcher and checkbox green: 202 passed over test_feeds, test_web_feeds, test_web_url_dialog, test_db, test_app and test_web_scaffold.

The Feeds page is the jobs board's shape - one module, one NAV entry, a page and a self-polling fragment - but it polls every 60s rather than every 2s: a feed changes on the hour, and asking every two seconds about something that moves once a day is only noise. It shows the three things a subscription owes a reader, and each is a way one becomes a surprise when missing: when it last looked and what it found, when it stopped answering, and a way out. The feed's URL is never an href - it came over the network from a stranger, the same rule the episode list follows.

'Check now' bypasses the schedule but NOT the cap. The reason for the ceiling does not become a good idea because somebody is watching this time.

TWO WRONG ASSUMPTIONS THE TESTS CAUGHT, both about contracts I had not read.

render() is render(request, name, **ctx), not (request, name, ctx). Eleven tests failed at once, which is what a wrong call signature looks like.

known_sources(conn, entries) takes the entry DICTS and returns one state per entry - 'queued', 'library', 'trash' or None - not a set of ids. I had written poll() against a signature I imagined. Fixing it made the code better than my version: the poll now calls exactly the function the episode list calls, with the same argument, which is what stops the marks a person sees and the poller's judgement from drifting apart. That was ADR-008's intent and I had quietly broken it while thinking I was following it.

The checkbox is ticked by default and subscribing queues nothing: the entries on screen are recorded as seen, so only what appears after today is new. Unticking it imports without following, which is how one episode gets plucked from a feed without binding yourself to it.
<!-- SECTION:NOTES:END -->
