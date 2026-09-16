---
id: TASK-072
title: >-
  Feed and playlist entry URLs are fetched with a scheme check only, on the
  watcher's own timer
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 16:56'
updated_date: '2026-09-16 16:59'
labels:
  - review-2026-09-16
  - security
  - ingest
dependencies: []
priority: low
type: bug
ordinal: 117000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low after correction - the unattended path is capped at MAX_NEW_PER_POLL = 25). urls.ensure_http_url accepts any http(s) address, and every ingest_url job goes through it. For an address a person pasted that is right: the app listens on 127.0.0.1 only and a person who pastes their NAS's address chose it. For an address a feed or playlist author wrote down it is not: a followed feed is polled unattended on its own timer, and an enclosure at http://127.0.0.1:11434/api/tags or http://192.168.1.1/admin would be fetched by this app, on this machine, on the author's behalf. The entries a person ticks in the dialog carry the author's addresses too - the list shows titles, not URLs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An ingest_url job marked from_playlist refuses a URL whose host is this machine or the local network (loopback, private, link-local, unspecified, .local/.localhost names) before yt-dlp is asked anything, with UNSUPPORTED_URL and a reason a person can read
- [x] #2 A URL a person pasted themselves is not filtered: ensure_http_url still accepts a loopback or private address
- [x] #3 The check judges the written address only and says so; a name is not resolved
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: tests in test_ingest_urls.py - ensure_public_http_url refuses loopback, private, link-local, unspecified and .local/.localhost hosts and passes public ones; ensure_http_url still accepts a pasted private address; url_stage.fetch refuses a from_playlist job pointing home before yt-dlp is asked, and probes the same address when a person pasted it.
2. Green: urls.is_local_host (ipaddress.is_global for a literal, localhost/.localhost/.local by name, no resolution) and urls.ensure_public_http_url; url_stage.fetch applies it when ctx.params['from_playlist'].
3. Run test_ingest_urls, test_ingest_feeds, test_web_url_dialog one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: 13 of the 15 new tests failed (urls has no ensure_public_http_url; the from_playlist job reached the fake yt-dlp); the two 'a person may paste a private address' tests passed before the change, as intended. Green after urls.is_local_host (ipaddress.is_global for a literal, localhost/.localhost/.local by name, no resolution), urls.ensure_public_http_url and the from_playlist guard in url_stage.fetch: test_ingest_urls 101, test_web_url_dialog 90, test_web_feeds 18, test_feeds 18, test_feed_follow 13, test_feed_first_episode 13, test_feed_backfill 13. Limit stated in the docstring: a name that resolves to a private address only at fetch time (DNS rebinding) is out of this guard's reach.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A URL a feed or playlist author wrote down (from_playlist jobs) is refused before yt-dlp sees it when its host is this machine or the local network, with UNSUPPORTED_URL and a readable reason; a URL a person pastes keeps the scheme check only. Verified red-to-green by 15 tests and 266 tests across seven files.
<!-- SECTION:FINAL_SUMMARY:END -->
