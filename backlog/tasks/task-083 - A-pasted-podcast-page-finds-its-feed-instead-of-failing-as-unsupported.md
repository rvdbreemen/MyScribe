---
id: TASK-083
title: A pasted podcast page finds its feed instead of failing as unsupported
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 19:35'
updated_date: '2026-09-18 19:47'
labels: []
dependencies:
  - TASK-072
references:
  - 'https://whycast.podcast.audio/@whycast'
  - scribe/ingest/urls.py
  - >-
    docs/adr/ADR-008-a-feed-or-channel-is-a-subscription-polled-on-a-schedule-each-new-episode-is-one-ingest-url-job.md
ordinal: 128000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Pasting a podcast's home page into Transcribe from URL dead-ends. https://whycast.podcast.audio/@whycast is a Castopod profile page: yt-dlp has no extractor for it, so probe raises UnsupportedUrl and the user sees 'Unsupported URL' with no way forward. The page itself carries the answer in its head - <link rel="alternate" type="application/rss+xml" href=".../feed.xml"> - and that feed URL works today (47 episodes listed by yt-dlp's generic RSS reading). Pasting the page rather than the feed is the obvious user move, and every podcast host publishes that link tag. The discovered href is author-controlled text, not something the user typed, so it is the TASK-072 case: it must pass the public-host guard before anything fetches it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 probe() retries once against the feed announced by a page's <link rel=alternate type=application/rss+xml> when yt-dlp raises UnsupportedUrl, and returns that feed's listing
- [x] #2 A page with no feed link, or whose feed also fails, still raises UnsupportedUrl carrying the original reason - discovery never masks the real error
- [x] #3 A discovered href is resolved against the page URL and passes ensure_public_http_url, so a page advertising http://127.0.0.1:11434/feed.xml is refused (TASK-072)
- [x] #4 The fetch is bounded: html content-types only, a byte cap, the module's socket timeout, and no second discovery hop
- [x] #5 Discovery is off the happy path: a URL yt-dlp supports costs no extra request
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. urls.py: add discover_feed(page_url, *, fetch=None) -> str | None. Fetch with urllib under SOCKET_TIMEOUT, accept only html content-types, read at most MAX_DISCOVERY_BYTES, parse with html.parser (stdlib, no new dependency) for <link rel=alternate type=application/rss+xml|atom+xml>, urljoin the href against the page URL, and return it only if ensure_public_http_url accepts it.
2. probe(): wrap the _extract call. On UnsupportedUrl only, call discover_feed once; on a hit, re-probe the feed URL with a fresh ydl and discovery disabled so there is no second hop. On a miss, or if the feed probe fails too, raise the original UnsupportedUrl with its reason intact.
3. Thread a discover flag through probe so the recursive call cannot recurse, and so callers that must not touch the network can turn it off.
4. Tests in tests/test_ingest_urls.py, with a FakeYdl that answers per URL and a fetch double: the happy path, no link tag, feed also unsupported, a private-host href refused, a bounded fetch, and no extra request on a supported URL.
5. Evidence: red first, then green, then a real run against https://whycast.podcast.audio/@whycast through the running app.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented in scribe/ingest/urls.py: discover_feed() reads the page once, parses <link rel=alternate> with html.parser (no new dependency), urljoins the href and puts it through ensure_public_http_url; probe() calls it only on UnsupportedUrl and re-probes with discover=False so there is no second hop. RSS is preferred over Atom because enclosures live in RSS.

Two things the real run taught, neither visible from the tests:

1. The first live run found no feed on a page that has one. A Castopod instance content-negotiates /@show: with no Accept header urllib gets HTTP 404 application/json (body ""), with Accept: text/html it gets 200 and 318 KB of HTML carrying the link tag. Verified with two curls. fetch_page now sends an Accept header, pinned by test_the_page_read_asks_for_html_and_keeps_its_own_budget.

2. ADR-001 lets the web process touch the network only for the dialog's preview, on a 10 s budget that an abandoned thread holds for as long as SOCKET_TIMEOUT (15 s). Discovery is a second read inside that same budget, so borrowing SOCKET_TIMEOUT would let one preview hold a socket for 30 s. DISCOVERY_TIMEOUT = 5 keeps the total inside what that exception was written for.

Evidence - live network, not mocks:
  discover_feed('https://whycast.podcast.audio/@whycast') -> https://whycast.podcast.audio/@whycast/feed.xml (0.79 s)
  probe(page, limit=5) -> kind=playlist title='WHYcast' entries=5 total=47 truncated=True (3.57 s), named 'Ep 46 - Episode 46' by TASK-041's naming
  a page advertising http://127.0.0.1:11434/api/tags -> None, refused before any fetch
Suite: 2385 passed, 5 skipped, 2 failed. Both failures pre-date this task and are unrelated: test_app version mismatch (scribe/__init__.py 0.2.1 vs pyproject 0.3.1) and test_supervisor reconcile (macOS process start time, TASK-065's own scenario). 9 new tests went red before the code and green after.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A podcast page that yt-dlp has no extractor for is now read once for the feed it announces, and that feed is probed instead. https://whycast.podcast.audio/@whycast went from 'Unsupported URL' to a 47-episode listing. The discovered href is the page author's text, so it is resolved against the page URL and must pass ensure_public_http_url (TASK-072); discovery runs only after UnsupportedUrl, never on the happy path, once, on its own 5 s budget (ADR-001), and never masks the original error. Verified by 10 new tests red-then-green plus a live run against the real host.
<!-- SECTION:FINAL_SUMMARY:END -->
