---
id: "ADR-008"
title: "A feed or channel is listed in the web process and chosen in the browser; each chosen episode is one ingest_url job"
status: "Proposed"
date: "2026-09-08"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "ingest"
  - "feeds"
  - "yt-dlp"
  - "provenance"
aliases:
  - "feed import"
  - "episode list"
  - "channel import"
  - "RSS import"
  - "podcast import"
components:
  - "scribe.ingest.urls"
  - "scribe.web.ingest_ui"
  - "scribe.stages.url_stage"
symbols:
  - "probe_in_slot"
  - "known_sources"
  - "add_url"
  - "enqueue_many"
  - "source_id_for"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-008 A feed or channel is listed in the web process and chosen in the browser; each chosen episode is one ingest_url job

## Status

Proposed, 2026-09-08.

## Status History

```yaml
status_history:
  - date: 2026-09-08
    status: Proposed
    changed_by: Claude Fable 5.1 (agent, session 2026-09-08)
    reason: Initial proposal
    changed_via: adr-kit
```

## Context and Problem Statement

The ask (TASK-021): paste or drop a podcast RSS feed or a YouTube channel,
see its episodes, and import one or many. YouTube goes through yt-dlp.

Phase 6 already fetched a link in a runner child (ADR-001), fanned a
playlist out into one `ingest_url` job per entry (capped at 500), and
previewed a pasted link in the web process under a documented carve-out: a
worker thread with a 10 s budget, a slot count held inside the thread, and
abandon-on-cancel. Measured live on 2026-09-08 against yt-dlp 2026.08.19:

* yt-dlp's generic extractor already reads a podcast feed
  (`GenericIE._extract_rss`) into a flat playlist: Planet Money, 355
  entries with title, duration and timestamp, in 4.2 s; The Daily, 2970
  entries, in 13.9 s. A bare YouTube channel URL resolves to its Videos
  tab: Computerphile, 920 entries, in 22.6 s.
* A feed entry fetched on its own has no usable name: a bare enclosure
  probes as `default.mp3_ywr3ahjkcgo_6c70…` with no uploader. Today's
  fan-out carries only the entry's URL, so every episode of a feed would
  land in the library under the CDN's filename.
* yt-dlp smuggles a feed item's `<guid>` into the enclosure URL's fragment
  (`#__youtubedl_smuggle=…`) to set its own id; other extractors smuggle
  referers and headers the same way.
* Starlette 1.6.0 refuses a form of more than 1000 fields with its own
  sentence, for multipart and url-encoded bodies alike.

Three questions follow. Who parses a feed? Where does the selection live
between "list" and "import"? How does the next listing know what is already
here?

## Decision Drivers

* ADR-001: the web process lists, a runner child fetches. No second
  long-lived worker, no model in the web process.
* No second parser: yt-dlp already reads feeds and every video site, and
  it is pinned, doctor-checked and expected to age. Two parsers would age
  separately.
* Nothing to keep in sync: a selection that lives in a table has to be
  swept; one that lives in the browser's form does not.
* A person comes back to a feed. "In library" and "queued" are what make
  "import one or many, over time" easy, and they need provenance the media
  row does not have today.
* Every URL and title here is a stranger's text, posted back by a browser.

## Considered Options

* **yt-dlp lists in the web process under the existing carve-out; the
  selection lives in the browser's form; provenance is two columns on the
  media row.**
* A feed parser (`feedparser`) plus an HTTP download of the enclosure,
  beside yt-dlp for video sites.
* A `feed` table: a listing job writes the episodes, the page polls, the
  selection is rows.
* Do nothing: paste the feed, get every episode through the fan-out, or a
  failure past 500.

## Decision Outcome

Chosen option: **yt-dlp lists, the browser holds the selection, the media
row remembers where it came from**, because it adds no parser, no table and
no worker, and each of its three parts is the smallest thing that answers
its question:

* The listing is the preview's `extract_info(download=False)` with
  `playlistend=MAX_LISTED` (2500), bounded as before by a budget (10 s on a
  keystroke, 60 s on the explicit "List the episodes anyway"), a slot count
  held inside the thread, and the abandoned thread. Listings are
  **coalesced per URL**: the patient press attaches to the probe still
  running for that URL rather than starting a second one.
* Each episode is a checkbox whose value is the entry as JSON. `POST
  /transcribe/url` has three cases: ticked entries (every one checked,
  then one `ingest_url` job each in one transaction), a list with nothing
  ticked (a 400), and neither (today's single job on the link). The row
  button hides while a list is on screen, so there is one import action
  and it honours the ticks wherever the press comes from.
* Each child job carries the listing's title and extractor-scoped id, and
  the register stage writes `media.source_url` (the fetched URL without
  its fragment) and `media.source_id` (`Youtube:<id>`, `Generic:<guid>`).
  The next listing compares both against media rows and live jobs.

### Confirmation

`tests/test_ingest_urls.py` (the listing's limit and total, the feed-shaped
fixture derived from yt-dlp's own extractor, the fan-out carrying entry and
source, register naming and provenance), `tests/test_web_url_dialog.py` (the
list, the marks, the coalesced and patient probe, the three cases of the url
route with a bad entry between two good ones, the Node harness), and the
scripted real run recorded in TASK-021.

## Decision Contract

### Must

* A feed, a channel or a playlist is listed by yt-dlp through
  `urls.probe(limit=…)`; no other feed or HTML parser is added to `scribe`.
* The listing runs only under `ingest_ui.probe_in_slot`: a budget, a slot
  taken and released inside the thread, `MAX_LISTED` as the ceiling on one
  listing's work, and one listing per URL at a time.
* An entry's URL is fetched verbatim by the runner child; anything that
  compares or stores it strips the fragment first.
* Every posted entry passes `parse_entry` (size cap, JSON without
  NaN/Infinity, a dict, a string URL through `ensure_http_url`, a title cut
  at `library.MAX_NAME`) before any job is queued, and the jobs of one
  import are inserted by `jobs.enqueue_many` in one transaction.
* The media row's `source_url` and `source_id` are compared by
  `known_sources` and never rendered as an `href`; the same holds for every
  URL that came over the network.
* The listing's title names the media row; the download's title is the
  fallback.

### Must Not

* Add a `feed` table, a subscription, or a poller without superseding this
  ADR.
* Render the checkbox value with `|tojson` (Markup, unescaped quotes); it is
  `json.dumps` output autoescaped as an attribute.
* Read the ticked entries through `_fields`, which keeps the last value per
  name.

### Exceptions

* The fan-out inside the runner child (`url_stage._fan_out`) still queues
  its children one `jobs.enqueue` at a time: it runs where the cost falls
  on nobody, and switching it is tidiness, not this decision.

### Verification

* `tests/test_ingest_urls.py::test_a_feed_entry_keeps_its_smuggled_url_and_gets_the_guid_as_its_source_id`,
  `::test_register_names_a_feed_episode_after_the_feed_not_the_cdn`,
  `::test_yt_dlp_honours_playlistend_on_a_flat_playlist_and_still_reports_the_total`.
* `tests/test_web_url_dialog.py::test_a_patient_preview_attaches_to_the_listing_already_running`,
  `::test_a_follower_holds_no_slot`,
  `::test_one_bad_entry_among_good_ones_queues_none_of_them`,
  `::test_a_whole_listing_posted_at_once_is_refused_by_this_route_not_by_starlette`,
  `::test_no_template_binds_an_href_to_a_stranger_s_url`.
* `grep -rn "feedparser" scribe/` returns nothing.

## Consequences

### Positive

* One dependency, one fetch path, one queue. A feed episode is a job like a
  video is, with a name and a source.
* The listing shares the preview's bounds and its tests; nothing new opens
  a socket.
* "In library", "in trash" and "queued" are one query over two columns, and
  a recording uploaded first and listed later is marked from its first
  re-import on, because the dedupe path fills in a missing source.

### Negative

* A feed longer than `MAX_LISTED` lists its newest 2500 only; the header
  says "the first 2500 of 2970". The Daily is the measured case. Moving the
  number is one constant, argued in `ingest_ui.py` from probe time and
  fragment size (about a megabyte at the cap).
* An abandoned listing runs to the end of its work: a feed until its one
  document is read, a channel until `MAX_LISTED` entries are paged. What is
  bounded is the slot, not the seconds.
* Matching is by URL and extractor id, not by a canonical page. A typed
  `youtu.be/ID` matches a channel listing through the id; a feed whose CDN
  moved matches through the guid; a feed that changed its guids does not.
  A canonical-page column is a follow-up if that turns out to matter.
* The url route reads up to `MAX_LISTED + 64` form fields, above
  Starlette's default of 1000. Behind the same-origin guard on loopback,
  and the count is refused before any entry is parsed.

## Pros and Cons of the Options

### yt-dlp lists, the browser holds the selection, provenance on the row

* Good, because nothing new parses, nothing new polls, nothing new is swept.
* Good, because the listing inherits the preview's three bounds and its
  security tests.
* Bad, because a listing lives only as long as the dialog; closing it means
  listing again (coalescing makes a re-list cheap only while the first one
  is still running).

### feedparser plus an HTTP download

* Good, because a feed parser reads every feed dialect and never pages.
* Bad, because it is a second parser that ages on its own schedule, a second
  download path beside yt-dlp's (with its own progress, retries and errors),
  and it does nothing for a YouTube channel.

### A feed table and a listing job

* Good, because a listing survives the dialog and could become a
  subscription.
* Bad, because it is a table to sweep, a job type, a polling page and a
  second copy of what the site already knows - for a feature (subscribe)
  nobody asked for. ADR-001 would need a supervisor-owned poller.

### Do nothing

* Good, because a feed URL already fans out.
* Bad, because every episode arrives named `default.mp3_…`, and "one or
  many" is not on offer: all, or a failure past 500.

## Open Questions

None.

## Related Decisions

* ADR-001 (the web process lists under the preview carve-out; a runner
  child fetches).
* ADR-002 (`known_sources` and `enqueue_many` run under `db.LOCK`; the
  selection is deliberately not a table).

## References

* `docs/superpowers/specs/2026-09-08-feed-episode-import-design.md`
  (revision 2, with the measurements and the critique they answer).
* `scribe/web/ingest_ui.py` (`probe_in_slot`, `known_sources`, `add_url`,
  `parse_entry`), `scribe/ingest/urls.py` (`probe`, `source_id_for`),
  `scribe/stages/url_stage.py` (`_fan_out`, `register`), `scribe/db.py`
  (v10).
* yt-dlp 2026.08.19, `yt_dlp/extractor/generic.py` `_extract_rss` and
  `yt_dlp/utils/_utils.py` `smuggle_url`.
* Starlette 1.6.0, `starlette/requests.py` `Request.form(max_fields=…)`.

## Enforcement

```json
{
  "forbid_import": [
    {"pattern": "feedparser", "path_glob": "scribe/**", "message": "yt-dlp is the one feed reader (ADR-008)."}
  ],
  "forbid_pattern": [
    {"pattern": "\\|\\s*tojson", "path_glob": "scribe/templates/_url_panel.html", "message": "The checkbox value is json.dumps output autoescaped as an attribute, never |tojson (ADR-008)."}
  ],
  "require_pattern": []
}
```
