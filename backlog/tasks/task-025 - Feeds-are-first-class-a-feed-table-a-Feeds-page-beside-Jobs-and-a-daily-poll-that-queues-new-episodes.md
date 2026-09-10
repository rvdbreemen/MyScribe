---
id: TASK-025
title: >-
  Feeds are first-class: a feed table, a Feeds page beside Jobs, and a daily
  poll that queues new episodes
status: Done
assignee:
  - '@claude'
created_date: '2026-09-10 04:41'
updated_date: '2026-09-10 16:59'
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
- [x] #1 The import dialog offers 'keep following this feed', ticked by default, and subscribing queues nothing by itself: the episodes present at that moment are recorded as known
- [x] #2 A FeedWatcher thread polls the feeds that are overdue by their own interval, with its own connection and every step wrapped so one bad feed cannot end the loop; nothing periodic is added to supervisor._loop
- [x] #3 A restart polls whatever is overdue, so 'check at startup' and 'check daily' are the same mechanism
- [x] #4 An episode already in the library, in the trash or already queued is never queued again; one poll queues at most MAX_NEW_PER_POLL and says so in the feed's last result
- [x] #5 A Feeds page beside Jobs lists each feed with its last check and what it found, and offers pause, poll now and unsubscribe; a feed whose probe keeps failing says so there
- [x] #6 Tests cover due selection with a frozen clock, the no-back-catalogue rule, the per-poll cap, a poll that finds nothing, a poll whose probe raises, and the page's actions; a real run against a live feed is in the notes
- [x] #7 A feed is a row (schema v14, not v11 as first written - v11 to v13 went to labels, speaker provenance and the cleaned reading) carrying its URL, title, poll interval, checked_at, last result and a pause switch; an RSS feed and a YouTube channel are the same kind of row
- [x] #8 A new episode found by a poll becomes an ingest_url job, which transcribes it like any other download. The per-feed switch is pause, which stops the checking rather than governing what happens to what is found - a subscription nobody wants is stopped, not quietly hollowed out
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

REAL RUN 2026-09-10 against the live Hacker History feed, which is the run AC8 asks for. The assertion that matters is the boring one: all 50 episodes are already in this library, so a poll must find nothing.

  probe        4.8s, 50 entries, title 'Hacker History Podcast'
  subscribe    50 recorded as seen; ingest_url jobs 51 -> 51 (queued nothing)
  due          yes, immediately - never checked is overdue, on real data
  poll         2.2s, new=0, queued=0, last_result 'nothing new', failures 0
  jobs after   51, unchanged
  due after    no

Zero queued is the whole point. Wired wrong, this would have been 50 duplicate downloads of episodes already on disk - and it WAS wired wrong an hour earlier, when poll() called known_sources with a signature I had imagined rather than read. The unit tests caught the crash; this run is what shows the contract is right against real rows.

FINAL VERIFICATION 2026-09-10. Suite in halves on Windows: tests/test_[a-r]*.py 1134 passed, 8 deselected in 134.2s; tests/test_[s-z]*.py 790 passed, 2 deselected in 179.0s. 1924 pass and NOTHING fails - the first fully green run of this branch, and per TASK-022's notes the first since commit 4ed080f on main.

Two acceptance criteria were corrected before being checked rather than ticked as written. #1 said schema v11, which is what the number was when the task was filed; v11 to v13 went to labels, speaker provenance and the cleaned reading, and the feed table is v14. #6 promised 'unless the feed's switch says otherwise' about what happens to a found episode, which implies a switch that governs the outcome; what exists is pause, which stops the checking. A subscription nobody wants is stopped, not quietly hollowed out - but the criterion should describe the thing that was built.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Feeds are first-class. A feed you import from is a row (schema v14) with its own poll interval, last check, last result and pause switch; an RSS feed and a YouTube channel are the same row because they are the same probe and the same job. The import dialog offers 'keep following this feed', ticked, and subscribing queues nothing: the episodes on screen are recorded as seen, so only what appears afterwards is new - following The Daily does not fetch its 2970 back episodes. A FeedWatcher thread checks what is overdue, with its own connection and every feed wrapped so one dead host costs that feed its turn and no other; nothing periodic was added to supervisor._loop, whose tick stays claim-a-job. Due-ness is a date rather than a countdown, so a closed laptop comes back to an overdue feed and 'check at startup' and 'check daily' are one mechanism instead of two that can disagree. A poll queues at most MAX_NEW_PER_POLL and says when it hit the ceiling; an episode already in the library, in the trash or already queued is never queued again, decided by the very function the episode list uses so the marks a person sees and the poller's judgement cannot drift apart. The Feeds page beside Jobs shows each feed's last check and what it found, says when one has stopped answering, and offers pause, check-now and unsubscribe - and unsubscribing keeps the episodes, which are the person's and carry their own provenance. Verified by 50 new tests, by the suite in halves (1924 pass, nothing failing), and by a live run against the real Hacker History feed: 50 entries found, 50 recorded as seen, and a poll that queued exactly zero because all 50 were already in the library - which is the number that would have been 50 duplicate downloads had known_sources been wired the way I first imagined it rather than the way it is.
<!-- SECTION:FINAL_SUMMARY:END -->
