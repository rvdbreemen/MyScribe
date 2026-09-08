---
id: TASK-021
title: >-
  Feed and channel import: list the episodes of an RSS feed or YouTube channel
  and import one or many
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-08 17:34'
updated_date: '2026-09-08 18:09'
labels: []
dependencies: []
references:
  - docs/superpowers/specs/2026-09-08-feed-episode-import-design.md
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Pasting a podcast RSS feed, a YouTube channel or a playlist into the transcribe dialog's link field shows its episodes as a list with checkboxes (title, date, duration, and a marker for episodes already in the library or queued). The user filters, ticks one or many, and presses Import: one ingest_url job per episode, downloaded by yt-dlp in a runner child (ADR-001), transcribed with the dialog's options. Why: today a feed URL either previews as a bare count or fans out into every episode at once (capped at 500), and a feed episode lands in the library named after its CDN filename (measured 2026-09-08: default.mp3_ywr3ahjkcgo_...) because the entry title does not travel with the job. RSS was listed as deferred in the 2026-09-01 design spec; this un-defers it. Design: docs/superpowers/specs/2026-09-08-feed-episode-import-design.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A pasted podcast RSS feed lists its episodes with title, date and duration; a YouTube channel URL (with or without a tab) lists its videos
- [ ] #2 Ticking one or many episodes and pressing Import queues one ingest_url job per ticked episode carrying the dialog's options, folder and cookies file; zero ticked and more than the cap are refused with a readable message and nothing queued
- [ ] #3 A feed episode arrives in the library under the feed's title for it, not the CDN filename, and the jobs board names each ingest_url job after its episode
- [ ] #4 Episodes already in the library or already queued are marked as such when the feed is listed again
- [ ] #5 A source that outruns the keystroke preview can still be listed on request with a longer budget, and a listing is bounded in count and time
- [ ] #6 Every error row of the design's section 5 has a test; no test reaches the network; the whole suite passes; a real run on an isolated instance imports one feed episode and one channel video end to end
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Spec revision 2 (docs/superpowers/specs/2026-09-08-feed-episode-import-design.md) after a four-lens critique; plan at docs/superpowers/plans/2026-09-08-feed-episode-import.md
2. urls.py: probe(limit) -> playlistend, UrlInfo.total/truncated, entries carry timestamp and source_id, download(title_hint)
3. db v10 (media.source_url, media.source_id), ingest_path writes and backfills them on dedupe, jobs.enqueue_many
4. url_stage: fan-out carries entry{title,source_id} and source{url,title}; register prefers the entry title, writes provenance, hotwords fall back to the source title
5. jobs_ui: an ingest_url job is named after its episode
6. ingest_ui: one listing per URL (leader/follower futures), patient budget, known_sources (queued > library > trash), parse_entry, add_url with three cases behind request.form(max_fields=...)
7. templates/CSS/JS: episode list with filter/All shown/None/count/cap, retry button, hx-trigger input changed + hx-sync, row button hidden while a list shows, link drop handler
8. docs: spec amendment, README line, ADR-008 (Proposed), ingest_ui docstring correction
9. evidence: suite in halves, scripted real run on 4299 (Planet Money, Computerphile, The Daily, a local 2600-item feed), recorded in the task
<!-- SECTION:PLAN:END -->
