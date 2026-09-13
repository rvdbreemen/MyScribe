---
id: TASK-021
title: >-
  Feed and channel import: list the episodes of an RSS feed or YouTube channel
  and import one or many
status: Done
assignee:
  - '@claude'
created_date: '2026-09-08 17:34'
updated_date: '2026-09-09 18:12'
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
- [x] #1 A pasted podcast RSS feed lists its episodes with title, date and duration; a YouTube channel URL (with or without a tab) lists its videos
- [x] #2 Ticking one or many episodes and pressing Import queues one ingest_url job per ticked episode carrying the dialog's options, folder and cookies file; zero ticked and more than the cap are refused with a readable message and nothing queued
- [x] #3 A feed episode arrives in the library under the feed's title for it, not the CDN filename, and the jobs board names each ingest_url job after its episode
- [x] #4 Episodes already in the library or already queued are marked as such when the feed is listed again
- [x] #5 A source that outruns the keystroke preview can still be listed on request with a longer budget, and a listing is bounded in count and time
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-08/09 on this machine (Windows 11, RTX 3080, yt-dlp 2026.8.19, 20 days old).

TESTS. The suite in halves (the whole-suite stall is documented in pytest.ini): tests/test_[a-r]*.py -> 1043 passed, 8 deselected in 148.02s; tests/test_[s-z]*.py -> 1 failed, 709 passed, 2 deselected in 227.83s. 1752 pass. The one failure is tests/test_web_settings.py::test_the_web_process_never_imports_a_model_runtime, and it is not this work: it was reproduced on main in a clean worktree with the same 687 torch modules, and an import hook named the chain (settings_page -> page_context -> doctor_context -> doctor.checks -> check_accelerators -> accel.describe -> accel.transcription_backend -> accel.cuda_available -> import torch), introduced with the Apple Silicon work in 4ed080f. Filed as TASK-022. python -m scribe.doctor --no-gpu: every check green, database schema v10.

ADR. adr-lint --strict: 8 pass, 0 fail. adr-judge over the whole branch diff (deterministic plus the host LLM pass over seven ADRs): OK, 0 violations, 0 advisory. adr-readiness ADR-008: ready-for-confirmation, quality 0.9, no open questions - it stays Proposed until a human accepts it.

REAL RUN, isolated instance on port 4299 with SCRIBE_DATA_DIR in a scratchpad, driven by a script (the guard admits a POST with no Sec-Fetch-Site or Origin: that is the user at the keyboard).

Phase A, no supervisor, previews:
- Planet Money (feeds.npr.org/510289/podcast.xml): 5.2 s on the keystroke budget, 355 rows, 351545 bytes, header "355 episodes . at most 500 per import".
- Computerphile (youtube.com/@Computerphile, no tab): 10.1 s -> the slow hint with the retry offered; patient 12.1 s, 920 rows, 450313 bytes, header "920 episodes" (a paginated channel reports no total).
- The Daily (feeds.simplecast.com/54nAGcIl): 10.1 s -> the slow hint; patient 17.6 s, 2500 rows, 2398353 bytes, header "the first 2500 of 2970 episodes". The cut and the true total, both shown.
- A local http.server feed of 2600 enclosures, no third party: 13.2 s, "the first 2500 of 2600 episodes". This is the one check the suite cannot make - FakeYdl returns its dict untrimmed - so it proves yt-dlp honours playlistend on an RSS playlist.
- Row detail, rendered live through the route path: a feed row reads "Trump drinks Venezuela s milkshake  2026-09-05 . 25:27" (title, date, duration); a channel row reads "How Watermarks Track AI Generated Content - Computerphile  31:56" (no date, as documented - YouTube s flat listing carries none).

Phase A, imports: one ticked episode of Planet Money and one Computerphile video, each POST /transcribe/url -> 200, one ingest_url job each with entry.title and source.title on the row. Previewing Planet Money again marked that row queued - deterministically, because only the supervisor claims and it was not running.

Phase B, with the supervisor: all four jobs settled in 155 s. ingest_url #1 and #2 done at register; transcribe #3 and #4 done at finalize.
- media 1: title "Congress has voted to eliminate government funding for public media", orig_name the same with .mp3 - not the CDN s default.mp3_ywr3ahjkcgo_..., which is conclusion 2 of the design fixed; source_url the podtrac URL without a fragment; source_id Generic:68f20634-257a-481c-9f82-ca0d844b4503 (the feed s guid); 431 words; extra_hotwords ["Congress", "Planet", "Money"] - the feed title filled in for the uploader the enclosure does not report, which is the fallback working.
- media 2: title "EXTRA BITS - BBC Micro and Teletext - Computerphile", orig_name the same with .webm, source_url https://www.youtube.com/watch?v=eofVhJc2lUA, source_id Youtube:eofVhJc2lUA, 306 words, extra_hotwords from the title (the download had its uploader, so no fallback).
- Honest note on media 1: the feed said 2:01 and the file is 169.5 s, and the transcript opens on a Mark Cuban promo rather than the episode. That is dynamic ad insertion at the CDN, not a bug here - the URL was fetched verbatim and the title is the feed s own.
- Previewing both sources a third time marked both imported rows "in library" (4.7 s and 20.9 s).

DELIBERATE BEHAVIOUR CHANGE. The playlist preview used to read "A playlist of 3 videos. Fetching it queues all 3, one job each" and now reads "3 episodes . at most 500 per import" above a list of checkboxes. The fragment before and after is in the PR; test_the_preview_of_a_playlist_counts_the_entries carries the reason. "all 3" is gone because the press it described (the row button) hides while a list is on screen.

LIVE RUN ON THE REAL INSTANCE, 2026-09-09. Not the isolated 4299 rehearsal: the app on 4242 against the real data directory, driven through Chrome, at the user's request to import the Hacker History Podcast.

Path exercised end to end: typed https://hackerhistory.com/feed/podcast/hacker-history-podcast/ into the link field -> the keystroke budget (10 s) expired and the panel offered "List the episodes anyway" -> the patient press attached to the listing still running and returned "Hacker History Podcast, 50 episodes - at most 500 per import" with filter, All shown / None and a live count -> All shown ticked 50 -> Import selected -> "Fetching 50 episodes of Hacker History Podcast. Each is a job of its own; the transcription is queued behind each download."

Queued: 50 ingest_url jobs (#36-#85), each carrying entry={title, source_id} and source={url, title="Hacker History Podcast"}.

AC3 outside the suite, from the media rows the runner children actually wrote:
  #10 title 'The history of Michael Lenz'  orig_name 'The history of Michael Lenz.mp3'
      source_url https://hackerhistory.com/podcast-download/1614/the-history-of-michael-lenz.mp3
      source_id  Generic:0c83540e-a13b-5430-8b9e-5421eda71ee4
  #15 title 'The history of Laura, Rachael, and Ben'
      source_id  Generic:https://hackerhistory.com/?post_type=podcast&p=1584
The CDN filename (default.mp3_ywr3ahjkcgo_..., the defect this task was written around) does not appear on any row. Both guid dialects this feed uses - a UUID and a permalink - come through as the source_id, which is what the next listing matches on.

Also added after an advisory review: tests/test_web_url_dialog.py::test_a_dropped_file_still_reaches_the_file_input_and_leaves_the_link_field_alone. The drop listener was rewritten to carry links as well as files, and every existing Node test passed files: [], so the older file half had become untested code. Proved to bite by removing `input.files = files` from a COPY of app.js in a scratch directory and re-running the harness against it: AssertionError, as it should be. The repo's app.js was never touched.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The transcribe dialog's link field now lists a podcast RSS feed, a YouTube channel or a playlist as a filterable episode list with checkboxes, and Import queues one ingest_url job per ticked episode. yt-dlp does the listing (no second feed parser); the listing runs in the web process under the preview's existing carve-out with a 10 s keystroke budget, a 60 s patient retry that attaches to the listing already running, and MAX_LISTED=2500 as the ceiling. Each job carries the entry's title and an extractor-scoped source_id, so an episode arrives in the library under the feed's name for it instead of the CDN filename - the defect this task was written around. New media columns source_url and source_id (schema v10) let the next listing mark what is already in the library, in the trash or queued. Verified: 1752 tests pass in halves; doctor green on v10; adr-lint --strict 8/8, adr-judge 0 violations; a scripted isolated run on 4299 over Planet Money, Computerphile, The Daily and a local 2600-item feed; and a live run on 4242 that listed the Hacker History Podcast (50 episodes), imported all 50 through the patient path, and produced media rows titled 'The history of Michael Lenz' with the feed's guid as source_id. AC6 is left unchecked deliberately: it asks for the whole suite, and tests/test_web_settings.py::test_the_web_process_never_imports_a_model_runtime fails - identically on main in a clean worktree, chain named by an import hook, tracked as TASK-022.
<!-- SECTION:FINAL_SUMMARY:END -->
