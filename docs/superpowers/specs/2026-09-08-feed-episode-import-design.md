# Feed and channel import: list the episodes, pick one or many

Status: revision 2, 2026-09-08, written in an autonomous session. Revision 1
went through a four-lens critique (product, ADR/security, correctness,
testing) with every finding verified against the code; 47 held and are
folded in below. The choices are stated with their reasons so they can be
overturned one at a time.

Scope: the "From a link" door of the transcribe dialog grows an episode
list, the link field's preview trigger changes from `change, keyup changed`
to `input changed` (§3.6 says why), and a link dropped on the dialog fills
the link field. Nothing else in the dialog changes.

## 0. What is asked, and what is already there

The ask: drop or paste a podcast RSS feed or a YouTube channel, see its
episodes, and import one or many of them. YouTube goes through yt-dlp.
"Drop" is read literally: a link dragged from an address bar or a feed
icon onto the dialog is caught (§3.6); pasting works as it does today.

What Phase 6 already built (TASK-007.01, TASK-007.02, commits c5305da and
a1c3618):

* `urls.probe` asks a URL what it is; a playlist comes back flat as
  `{id, title, duration, url}` entries. `urls.download` fetches one link's
  audio without re-encoding.
* An `ingest_url` job downloads one link in a runner child (ADR-001) and
  queues a `transcribe` job on the result. A playlist fans out into one
  `ingest_url` job per entry, capped at `MAX_FAN_OUT = 500`.
* The dialog previews a pasted link (title, uploader, duration, or an entry
  count with the words "all N") in a worker thread with a 10 s budget.

What was measured on 2026-09-08 against yt-dlp 2026.08.19, live:

| Input | `urls.probe` today | Time |
| --- | --- | --- |
| Planet Money RSS (`feeds.npr.org/510289/podcast.xml`) | playlist, 355 entries, each with title, duration, timestamp | 4.2 s |
| The Daily RSS (`feeds.simplecast.com/54nAGcIl`) | playlist, 2970 entries | 13.9 s |
| `youtube.com/@Computerphile` (no tab) | playlist "Computerphile - Videos", 920 entries; yt-dlp picks the Videos tab itself | 22.6 s |
| `youtube.com/@Computerphile`, `playlistend=5` | 5 entries | 2.3 s |
| One feed enclosure on its own (`…/default.mp3?…`) | single, title `default.mp3_ywr3ahjkcgo_6c70…`, no duration | 5.6 s |

And, offline, against the installed yt-dlp's own code:

* `GenericIE._extract_rss` turns a feed into a playlist whose entries are
  `url_transparent` with `title`, `timestamp` (from `pubDate`), `duration`
  (from `itunes:duration`), **no `id`**, and a `url` that is the enclosure
  with the item's `<guid>` smuggled into the fragment:
  `…/default.mp3?d=1&e=0#__youtubedl_smuggle=%7B%22force_videoid%22%3A+%22guid-0%22%7D`.
  An item with a `<link>` but no enclosure is listed under its page URL;
  an item with neither is skipped before yt-dlp returns.
* With `playlistend=N`, yt-dlp returns N entries and still reports
  `playlist_count` as the feed's full length (a feed is a list; a
  paginated channel reports no count).
* Starlette 1.6.0's `Request.form()` refuses more than 1000 fields, for
  multipart and url-encoded bodies alike, with its own sentence.
* The vendored htmx 2.0.10 applies `hx-params` *after* merging `hx-vals`.

Three conclusions follow:

1. **RSS already parses.** No feed parser is needed and none will be
   added. The design spec of 2026-09-01 listed "RSS ingest" under
   Deferred; this document un-defers it.
2. **A feed's episodes lose their names on the way down.** The fan-out
   child carries only the entry's URL, and a bare enclosure probes as its
   CDN filename. Every Planet Money episode would land in the library as
   `default.mp3_…`. The entry's title has to travel with the job.
3. **Big sources outrun the preview.** The Daily and a 900-video channel
   both pass the 10 s budget, so today the dialog says "took too long" for
   exactly the inputs this feature is about, and pressing Fetch on them
   fails at the 500 cap with no way to take fewer.

## 1. The user's view

In the URL panel, a link that turns out to be a feed, a channel or a
playlist shows its episodes under the field. The row button "Fetch and
transcribe" disappears while a list is on screen: there is one import
action, and it honours the ticks.

```
Planet Money · 355 episodes · at most 500 per import
[ filter by title or date…        ]   All shown · None      3 selected
┌──────────────────────────────────────────────────────────────────────┐
│ ☑ Trump drinks Venezuela's milkshake        2026-09-04 · 25:27       │
│ ☐ Love in the time of Palantir              2026-09-02 · 25:47       │
│ ☐ Dolly Parton's "9 to 5" (bonus)           2026-08-30 · 22:54  in library │
│ ☐ The Indicator crossover                   2026-08-28 · 09:12  queued │
│ ☐ An episode whose recording was trashed    2026-08-21 · 31:02  in trash │
│ …                                                                     │
└──────────────────────────────────────────────────────────────────────┘
[ Import 3 selected ]
```

* The list appears in the same preview swap that shows a single video's
  title today, as soon as the probe answers. Of the three live inputs
  measured above, one (Planet Money) answers inside the 10 s budget.
* When the probe times out, the panel says so and offers **"List the
  episodes anyway"**. The button attaches to the listing that is still
  running for that URL (§3.4) and waits up to 60 s; while it waits it is
  disabled and reads "Listing… this can take a minute". A second timeout
  offers no second retry.
* Each row shows the date when the source gives one (a feed does;
  YouTube's flat listing does not) and the duration when known, in the
  order the source lists them. Nothing is sorted here.
* An episode already in the library is marked `in library`; one whose
  only recording sits in the trash is marked `in trash` (restore it from
  the Trash view rather than re-importing, which would transcribe onto
  the trashed row); one with an `ingest_url` job queued or running is
  marked `queued`. None is disabled: re-importing is the user's call.
  Matching is by the listed URL (without yt-dlp's fragment) or by the
  extractor-scoped id yt-dlp reports (§3.3), so a channel video added by
  hand as `youtu.be/ID` is marked too. A recording that arrived through
  another door (an upload, a watch folder) is marked from its first
  re-import on, because the re-import fills in the provenance it lacked.
* The filter narrows the list by title or date as it is typed; **All
  shown** ticks what the filter left visible, so "filter on 2025, All
  shown, Import" is three actions.
* The count line reads "3 selected"; past the cap it reads "920 selected -
  at most 500 in one go" and the Import button is disabled. The server's
  400 with the count is what a post without scripting sees.
* **Import N selected** queues one `ingest_url` job per ticked episode,
  with the dialog's options, folder and cookies file, and closes the
  dialog like every other submit. Nothing ticked is a 400 that says so.
  The supervisor runs jobs one at a time, so N imported episodes all
  download before the first one transcribes.
* Enter in the link field or the cookies field still presses the (hidden)
  row button, which posts to the same route, which honours the ticks.
  Enter in the filter or on a checkbox presses nothing.
* A link dropped anywhere on the dialog's sources block switches to the
  link tab, fills the field and starts the preview. A dropped text that
  is not http(s) is ignored.

Not in scope, deliberately: subscribing to a feed and polling it; a
folder created from the feed's name; storing the episode's publication
date on the media row (it is shown in the list only); a per-episode
preview; a backfill of provenance for recordings imported through the
link door before this version (they are marked from their first re-import
on, see above). The footer's "Upload and transcribe" stays the dialog's
only primary button on every tab, as today; on the link tab it answers
400 "choose at least one file to upload". Known, and a dialog change
outside this ask.

## 2. Architecture

```
browser ── POST /transcribe/url/preview ──► ingest_ui.url_preview
   │        (url, patient?)                    │ probe in a slot, 10 s or 60 s,
   │                                           │ playlistend = MAX_LISTED;
   │                                           │ one listing per URL at a time
   │  ◄──── _url_panel.html: the list ─────────┘ marks from media.source_url /
   │        (checkbox value = JSON entry)        source_id and queued jobs
   │
   └── POST /transcribe/url ─────────────► ingest_ui.add_url, three cases:
            (url, entry*, feed_url,             entries → validate all, then
             feed_title, options,                 jobs.enqueue_many × N
             folder_id, cookies_file)           feed_url, no entry → 400
                                                neither → one job on the link
                                                ▼
                       ingest_url job (runner child, ADR-001)
                       params: url, options, folder_id, from_playlist,
                               entry{title, source_id}, source{url, title},
                               cookies_file?
                         fetch: urls.download(url, title_hint=entry.title)
                         register: media.ingest_path(title=entry.title,
                                   source_url=url without fragment,
                                   source_id=…) → transcribe job
```

The web process lists; a runner child fetches. That is the split ADR-001
already draws and the preview carve-out `ingest_ui.py` already documents:
a listing is `extract_info(download=False)`, the same call the preview
makes. The request is bounded by a budget, the concurrency by a slot
count held inside the thread, and the work of one listing by
`MAX_LISTED`; the abandoned thread itself is bounded by none of these and
runs until its listing is done (§6 says what that means).

The selection lives in the browser's form between the two requests, not
in a table: there is nothing to keep in sync and nothing to sweep. A
`feed` table is what a *subscription* would need, and that is not this
feature.

## 3. Components

### 3.1 `scribe/ingest/urls.py`

* `probe_opts(cookies_file=None, *, limit=None)` gains `limit` →
  `playlistend`. `playlist_items` stays None, or yt-dlp ignores
  `playlistend`. A channel is paginated by yt-dlp at roughly 40 entries a
  second (measured above), so the count is what bounds a channel's time.
  A feed is one document; the limit only trims what is returned.
* `probe(url, *, cookies_file=None, limit=None, ydl=None)`: `limit` goes
  to `probe_opts` when no `ydl` is given, and is used either way to
  compute `UrlInfo.truncated`.
* `UrlInfo.total: int | None` is yt-dlp's `playlist_count` when it
  reports one (it does for any feed; a paginated channel reports none).
  `UrlInfo.truncated` is `total > returned` when the total is known, else
  `limit is not None and returned >= limit` (the count is the only signal
  for a channel; a channel of exactly `limit` videos reads as "the first
  N", which is the honest answer since nothing can tell the two apart).
  `returned` is the raw count yt-dlp handed back, before `_entries` drops
  URL-less items.
* `_entries` yields `{id, title, duration, url, timestamp, source_id}`:
  * `url` is the entry's URL **verbatim**, fragment and all. yt-dlp
    smuggles a feed's `<guid>` into the enclosure URL to set its own id,
    and other extractors smuggle referers and headers the same way; the
    child fetches exactly what the listing said.
  * `timestamp` is epoch seconds from `timestamp`, else
    `release_timestamp`, else None. No `upload_date` fallback: no flat
    source delivers one (verified in `_tab.py` and `_extract_rss`).
  * `source_id` is the extractor-scoped id: `f"{ie_key}:{id}"` when the
    entry carries both (YouTube: `Youtube:kVXp6UNVPTo`), else
    `f"Generic:{guid}"` when the URL carries a smuggled `force_videoid`
    (a feed item with a guid), else None. Read with yt-dlp's own
    `unsmuggle_url`, imported lazily like `_is_unsupported` does.
* `download(..., title_hint=None)`: the rename after the download scrubs
  `title_hint or info["title"]` through `_rename_to_title`; an empty hint
  means "not given"; a hint that scrubs to nothing gets `FALLBACK_STEM`,
  exactly as a title does today. `DownloadedMedia.title` stays the site's
  title; the media row's title is `register`'s decision. The on-disk name
  becomes `orig_name`, which is user-visible (the row tooltip and the
  export header line): a feed episode's "original name" is its title, not
  the CDN's `default.mp3`. Intended.

### 3.2 `scribe/stages/url_stage.py`

* `_fan_out` gives each child `entry={title, source_id}` from the listed
  entry and `source={url: ctx.params["url"], title: playlist.title}` -
  the validated pasted URL `add_url` wrote, read before the spread
  overwrites `url` with the entry's, so that Fetch-all and Import-selected
  write the same `source.url` for the same feed. `playlist.webpage_url` is
  deliberately not used: it is a stranger's URL.
* `register` names the media after `params["entry"]["title"]` when that is
  non-empty, else after the download. A feed names its episodes; a CDN
  names its files. YouTube gives the same title both ways.
* `register` passes `source_url=urldefrag(url).url` (never a fragment:
  the smuggle goes, and so does a typed `#t=42`) and
  `source_id=entry.source_id or (f"{extractor_key}:{id}" when the
  download's extractor is not `Generic`)`. The generic extractor's id for
  a bare file is its filename, which would mark the wrong episode.
* Hotwords are read from the effective title and, when the download
  reports no uploader, the source's title - so "Planet Money" biases the
  decoder for a feed the way a channel name does for a video. A plain
  video with an uploader produces byte-identical params to today
  (`tests/test_ingest_urls.py:544-548` pins it).

### 3.3 `scribe/media.py` and `scribe/db.py`

* Migration v10: `ALTER TABLE media ADD COLUMN source_url TEXT;` and
  `ALTER TABLE media ADD COLUMN source_id TEXT;`. Rows that predate v10
  have neither. No index: measured, the lookup costs 4-11 ms at 1k-10k
  rows against a probe of 4-22 s.
* `ingest_path(..., source_url=None, source_id=None)` writes both on
  insert. On the dedupe path, title, folder and the privacy pin stay as
  the first arrival chose; **a missing `source_url` / `source_id` is
  filled in, and a set one is kept** (`UPDATE … WHERE id=? AND source_url
  IS NULL`, so two fan-out children deduping the same bytes at once are
  harmless). Without this, a recording that arrived through another door
  or before v10 would never be marked, however often it is re-imported.
  The same bytes first fetched at another URL keep that URL and show
  unmarked in a feed that lists a new one: the URL match's known edge,
  accepted rather than a second table (the id match covers a feed whose
  CDN moved but whose guids stayed).
* `jobs.enqueue_many(conn, type_, params_list, priority=0) -> list[int]`:
  N queued rows in one transaction, all or none, one log line with the
  count and the first and last id (contiguous under one write
  transaction). Measured: 500 rows in ~17 ms against ~917 ms for 500
  single `enqueue` calls, the difference being the 500 log lines.

### 3.4 `scribe/web/ingest_ui.py`

**Constants.** `MAX_LISTED = 2500` (the listing's `playlistend`; at 40
entries a second a channel of 2500 lists in about a minute, the patient
budget; The Daily's 2970 is not covered, and that is the trade - it also
caps the fragment at about 1-1.5 MB, which §7 measures).
`PATIENT_TIMEOUT_SECONDS = 60`. `FORM_FIELD_CEILING = MAX_LISTED + 64`
(the panel renders at most `MAX_LISTED` `entry` checkboxes plus the
dialog's own fields; Starlette's default 1000 would refuse "All shown,
Import" on any feed over ~985 entries with its own sentence before this
route can say "at most 500"). `MAX_ENTRY_BYTES = 4096`. `MAX_FEED_TITLE =
120` (the flash). Titles are cut at `library.MAX_NAME` (300), the cap the
rename route already enforces.

**The preview.** `url_preview` reads an optional `patient` field.
`probe_or_none(url, *, patient=False)` chooses `PATIENT_TIMEOUT_SECONDS
if patient else PREVIEW_TIMEOUT_SECONDS` *inside its body*, never as a
default argument: the tests monkeypatch both module globals. Both budgets
probe with `limit=MAX_LISTED`.

**One listing per URL.** `probe_in_slot` keeps `_inflight: dict[str,
Future]` under a `threading.Lock` beside `_preview_slots`. Under the lock:
a URL already in flight makes this thread a *follower* of that future
(it holds no slot: it does no network work); otherwise the thread takes a
slot (or raises `PreviewBusy`), registers a future and becomes the
*leader*. The leader runs `urls.probe`, sets the result or the exception
(followers get the same answer or the same `UrlError`), and in `finally`
removes the entry and releases the slot. The patient click after a
timeout therefore attaches to the 10 s probe's thread, which is still
listing, and usually answers within seconds of it finishing; a second
`extract_info` of the same URL is never started. Keyed on the stripped
URL text as posted, no normalisation: a differently spelled URL is a
second probe.

`preview_view(info, *, url, states, cap)` gains `entries` (each `{title,
duration, timestamp, state, value}` where `value` is the JSON string
`{"url", "title", "source_id"}` produced by `json.dumps` and passed to
the template as a plain `str` - never `|tojson`, which returns Markup and
breaks a double-quoted attribute at the first quote in a title),
`truncated`, `total`, `feed_url` (the pasted, checked `text` from
`url_preview` - `info.webpage_url` stays out of the view, as the existing
docstring requires), `feed_title` and `cap` (`url_stage.MAX_FAN_OUT`).
The noun is "episodes" for every playlist kind.

`known_sources(conn, entries) -> list[str | None]`, one state per entry:
`"queued"` when the entry's defragged URL or `source_id` matches a queued
or running `ingest_url` job's `params.url` (defragged) or
`params.entry.source_id`; else `"library"` when it matches a media row
with `trashed_at IS NULL`; else `"trash"` when it matches only trashed
rows; else None. Precedence queued > library > trash: the transient fact
that stops a duplicate download wins, and "in library" reappears on its
own once the job finishes. One `SELECT source_url, source_id, trashed_at
FROM media WHERE source_url IN (…) OR source_id IN (…)` - at most 5000
parameters, well under the 32766 that SQLite ≥ 3.35 (`db.py:288`)
guarantees - plus one read of the live jobs' params. Runs under
`db.LOCK` (ADR-002).

**The url route, three cases.** `add_url` reads the form with
`await request.form(max_fields=FORM_FIELD_CEILING)`; the ticked episodes
with `form.getlist("entry")` (string values only) - not through
`_fields`, which keeps the last value per name, right for the
hidden-0/checkbox-1 option pairs and wrong for a checkbox list; the
options, folder and cookies file through `_fields` as today. Then:

1. **Entries present.** More than `url_stage.MAX_FAN_OUT` (read at
   request time) → 400 with the count before any entry is parsed. Then
   every entry through `parse_entry(raw, index)`: `len(raw) <=
   MAX_ENTRY_BYTES`; `json.loads(raw, parse_constant=_refuse)` so
   NaN/Infinity never parse; a dict; `url` a string, then through
   `_web_url` (not a bare `ensure_http_url`: `UrlError` is a
   `RuntimeError` and would be a 500); `title` text cut at
   `library.MAX_NAME`; `source_id` a string or None; anything else
   (`ValueError`, `TypeError`, `RecursionError`) → 400 "episode N is not
   something this app can read". `feed_url` through `_web_url`,
   `feed_title` cut at `MAX_FEED_TITLE`. **All entries are checked before
   any job is enqueued**, then `jobs.enqueue_many` in the threadpool, one
   job per entry with `{url, folder_id, options, from_playlist: True,
   entry: {title, source_id}, source: {url: feed_url, title: feed_title},
   cookies_file?}`. Flash: "Fetching N episodes of <feed title>. Each is a
   job of its own; the transcription is queued behind each download."
2. **`feed_url` present, no entries** (the list was rendered, nothing
   ticked) → 400 "tick at least one episode - All shown takes everything
   the filter left".
3. **Neither** → exactly today's body: one job on the link as pasted.
   Covers a single video, a paste without scripting, and a feed whose
   listing timed out; the child's fan-out stays as the fallback, cap and
   all.

The existing `add_url` tests post neither field and stay green unchanged;
`tests/test_web_url_dialog.py:194-207` pins that case's params as exactly
`{url, folder_id, options}`.

### 3.5 `scribe/web/jobs_ui.py`

* `job_view` names an `ingest_url` job after `params.entry.title`, else
  `params.url`, instead of "ingest_url job". Twenty jobs from one feed
  were twenty rows with the same name. A failed child with no media row
  keeps its name in history. The title is a stranger's text and goes
  through the same autoescape as every `{{ job.title }}`.

### 3.6 Templates, CSS, JS

* `transcribe_dialog.html`: the link field's trigger becomes
  `hx-trigger="input changed delay:800ms"`. Today's `change, keyup
  changed` fires `change` on blur - the first click into the list - and
  the swap replaces the list, ticks and filter text and all, after a
  second probe. `input` fires for typing, keyboard paste, context-menu
  paste and drop, and `changed` dedupes them (measured: one request in
  every path, none on blur). The header comment says why. The field and
  the retry button carry `hx-sync="closest [data-panel]:replace"` - on
  those two elements only, not the form, where a keystroke would abort an
  in-flight upload - so a keystroke during a patient listing aborts it in
  the browser (the thread is abandoned server-side as today) and the new
  link previews; without it the first of two answers wins and the second
  is dropped.
* `_url_panel.html`, playlist branch: the header line ("<title> · N
  episodes · at most 500 per import", "the first N of T episodes" when
  truncated with a known total, "the first N episodes" without one), the
  toolbar, the scroll box, the count line and the Import button. Each
  row is `<li><label class="episode" data-search="…">` with `<input
  type="checkbox" name="entry" value="{{ e.value }}">`, the title, the
  meta (`e.timestamp|localtime('%Y-%m-%d')` and `e.duration|clock`, the
  " · " only between two present values, a missing value simply
  omitted) and the state tag. `data-search` is the lower-cased title plus
  the same `localtime('%Y-%m-%d')` the visible date uses, so the two
  cannot disagree. Hidden inputs `feed_url` and `feed_title` sit in the
  panel, so they are swapped away with the list whenever the link
  changes. The filter input carries no `name`: the server never sees it.
  **Every button the panel renders is `type="button"`** - All shown,
  None, the patient retry - because the panel is swapped into the
  dialog's form, where an untyped button is a submit that Enter presses
  and that closes the dialog on the 200 the preview always returns (the
  rule `_fs_panel.html` and `_recorder.html` already state). The one
  submit is `<button type="submit" formaction="/transcribe/url"
  hx-post="/transcribe/url" hx-params="not files">Import N selected</button>`,
  the row button's attributes, so it works with scripting off, leaves
  chosen files out of the post and closes the dialog on success. The
  count line carries `data-max="{{ cap }}"`.
* `_url_panel.html`, error branch: when the message is the slow hint and
  the request was not patient, the retry: `<button type="button"
  hx-post="/transcribe/url/preview" hx-params="url,patient"
  hx-vals='{"patient":"1"}' hx-target="#url-preview" hx-swap="outerHTML"
  hx-disabled-elt="this" hx-sync="closest [data-panel]:replace">List the
  episodes anyway</button>` with an `hx-indicator` line. `hx-params` must
  name `patient`: htmx 2.0.10 filters after merging `hx-vals`, so an
  allow-list of `url` alone drops it. `patient` cannot ride on the
  field's own trigger because the field posts `hx-params="url"`.
* `app.css`: `.episodes` (max-height, scroll), `.episode` rows,
  `.ep-state` tags, the toolbar; and, in the house `:has()` form, hide
  the row button while a list is showing: `[data-panel="url"]:has(.episodes)
  [data-url-submit] { display: none; }`. The markup is untouched, so the
  structural tests that count `data-submit-row` and `data-url-submit`
  still hold, and Enter's `.click()` on the hidden button still fires.
* `app.js`, inside `wireTranscribeDialog`, as delegated listeners on
  `document` like every handler there (the Node harness fires on
  `document` and propagates nothing):
  * one `input` listener serves the filter (`[data-episode-filter]`:
    `row.hidden = needle !== '' && row.getAttribute('data-search')
    .indexOf(needle) === -1` over `panel.querySelectorAll('.episode')`)
    and the checkboxes (recount). Not `change`: the existing `change`
    handler matches a descendant selector the stub throws on. Rows are
    walked with `forEach` (a NodeList has no `filter`); visibility is the
    `hidden` property, never a `[hidden]` or `:checked` selector; ticks
    are `Boolean(box.checked)`; the count is `textContent` on
    `[data-episodes-count]`, and past `data-max` it reads "N selected -
    at most M in one go" and the Import button (`[data-episodes-submit]`)
    is disabled.
  * one `click` listener for `[data-episodes-all]` (ticks rows with
    `hidden` false) and `[data-episodes-none]`.
  * the drop handler: files on the dropzone as today; otherwise
    `dataTransfer.getData('text/uri-list')`'s first non-comment line,
    else `getData('text/plain').trim()`, accepted only when it starts
    with `http://` or `https://` (the server's `ensure_http_url` still
    guards); then `#src-url` checked, the field filled, an `input` event
    dispatched on it so the trigger posts the preview, and the field
    focused. `dragover` inside `[data-sources]` is prevented so the
    browser does not navigate.

### 3.7 Docs

* `docs/superpowers/specs/2026-09-01-myscribe-design.md` §9: an amendment
  paragraph dated 2026-09-08 moving RSS ingest out of Deferred.
* `README.md`: one line under the link door.
* ADR-008 (Proposed): feeds and channels are read by yt-dlp and chosen in
  the browser; the web process lists, a runner child fetches; provenance
  is two columns on the media row, compared and never rendered as a link.

## 4. Data flow of one import

1. The user drops `https://feeds.npr.org/510289/podcast.xml` on the
   dialog. The link tab opens, the field fills, and 800 ms later it posts
   to the preview. `probe_in_slot` becomes the leader for that URL and
   runs yt-dlp with `extract_flat="in_playlist"`, `playlistend=2500`; 4 s
   later a 355-entry playlist is back, `total=355`, not truncated.
   `known_sources` finds two of the entries on media rows and one in a
   queued job. The panel renders 355 rows; the row button hides.
2. The user types "Palantir" in the filter (1 row stays), presses All
   shown, then Import 1 selected. htmx posts the form minus the files: one
   `entry` value, `feed_url`, `feed_title`, the options, the folder, the
   cookies field.
3. `add_url` sees entries, validates the one, enqueues one `ingest_url`
   job with `params = {url, folder_id, options, from_playlist: true,
   entry: {title, source_id: "Generic:<guid>"}, source: {url, title}}`,
   saves the options as defaults, answers with the library rows and
   `jobs-changed`. The dialog closes.
4. The supervisor spawns a runner child. `fetch` probes the enclosure URL
   (single), downloads `download.mp3`, renames it to the scrubbed entry
   title. `register` ingests it with the entry's title, `source_url`
   (defragged) and `source_id`, queues `transcribe` with the options and
   hotwords `["Love", "Palantir", "Planet", "Money"]` (the enclosure
   reports no uploader, so the source title fills in).
5. Pasting the same feed again shows that episode as `in library`.

## 5. Error handling

| Situation | Where | What the user sees |
| --- | --- | --- |
| Probe past 10 s | preview | the slow hint plus "List the episodes anyway" |
| Patient probe past 60 s | preview | the slow hint, no second retry; the thread runs to the end of its listing - a feed until its one document is read, a channel until `MAX_LISTED` entries are paged (about a minute on YouTube at the measured rate; longer on a slow host) |
| Editing the link during a patient listing | browser | the listing is aborted in the browser (`hx-sync`), its thread abandoned as today, and the new link previews |
| All four slots busy | either | `HINT_BUSY`, unchanged; a follower holds no slot |
| Channel with more than 2500 videos | either | "the first 2500 episodes" in the header |
| Feed longer than 2500 | either | "the first 2500 of T episodes" |
| More than 500 ticked, scripting on | panel | Import disabled, the count line says so |
| More than 500 ticked, scripting off | url route | 400 "… at most 500 in one go", nothing queued |
| Nothing ticked with a list on screen | url route | 400 "tick at least one episode …", nothing queued |
| An entry value that is too long, not JSON, not a dict, has a non-string or non-http(s) URL, or NaN | url route | 400 naming the episode; no job queued, including the good ones beside it |
| A `feed_url` that is not http(s) | url route | 400, nothing queued |
| Enclosure 403 / gone at fetch time | runner child | the job fails with `DOWNLOAD_FAILED` / `UNAVAILABLE` as today, one row per episode, named after the episode |
| Feed item with neither an enclosure nor a `<link>` | yt-dlp | never listed; skipped before `_entries` sees it |
| Feed item with a `<link>` but no enclosure | probe, then child | listed under its page URL; the child probes that page and fetches what yt-dlp finds or fails with `UNSUPPORTED_URL` / `DOWNLOAD_FAILED` |
| A dropped text that is not http(s) | browser | ignored; field and tab unchanged |

## 6. Security

* Every `entry` value is a stranger's JSON echoed back by the browser. It
  is parsed under the rules of §3.4, not evaluated; its URL passes the
  same `_web_url` as a typed link (so `file://`, `javascript:` and a bare
  search string are refused before yt-dlp sees them); its title is text
  on a job row and in a template, escaped there, and scrubbed by
  `_rename_to_title` before it is a filename. A browser-supplied entry URL
  reaches the filesystem only through the fixed download stem and that
  scrub. A selection can fetch nothing a typed link could not.
* `feed_url` is a URL field like `url` and gets the same check on the way
  back; it is stored on the job row as `source.url`.
* The preview and the url route are POSTs behind `guard.SameOriginPosts`.
  The preview is the one that opens a socket to an address of someone
  else's choosing, and that is exactly why it is not a GET.
* The patient budget is a longer wait, not more work: the slot count and
  abandon-on-cancel are unchanged, and the patient request attaches to
  the listing already running. `playlistend` caps an abandoned channel
  listing at `MAX_LISTED` entries - a ceiling on work, which is a ceiling
  on time only at the site's paging rate; a feed is one document and the
  cap does not shorten it. yt-dlp's `SOCKET_TIMEOUT` ends a thread whose
  host has gone silent; a host that keeps answering keeps the thread and
  its slot until the listing is complete. That is why the slot is counted
  inside the thread and why a listing is capped. `ingest_ui.py`'s
  docstring, which today says the socket timeout "ends it shortly after",
  is corrected to say this.
* `media.source_url` and `source_id` are read only by `known_sources`; no
  template or export selects them (`media_rows` names its columns,
  `MEDIA_FIELDS` allowlists, the transcript header reads `orig_name`).
  The URL value itself is not hidden: it appears as text on a job's
  Parameters tab, in `/api/jobs`, and on the log page, exactly as `url`
  does today. **Always escaped, never an `href`**: autoescaping quotes an
  attribute correctly and does nothing about a `javascript:` scheme.

## 7. Testing

No test reaches the network. yt-dlp is `FakeYdl` at the `urls.build_ydl`
seam, as in every existing URL test. The feed tests run on a new
`feed_info()` fixture derived from the installed yt-dlp rather than
hand-mirrored: a small RSS string (channel title; items with title, guid,
pubDate, itunes:duration and an enclosure; one item with a `<link>` and no
enclosure; one with neither; one title carrying `&`, `"`, `<` and `café`)
through `GenericIE()._extract_rss(url, None, ET.fromstring(xml))`,
`playlist_count` set to the item count as yt-dlp would. `playlist_info`
stays for the YouTube shape. Pins that must stay green unchanged:
`tests/test_web_url_dialog.py:194-207` (`add_url`'s params for a plain
link are exactly `{url, folder_id, options}`) and
`tests/test_ingest_urls.py:544-548` (a plain video's transcribe params
and hotword list are byte-identical).

* `tests/test_ingest_urls.py`: `probe_opts(limit=5)` sets `playlistend`
  and leaves `playlist_items` None; yt-dlp honours `playlistend` on a
  list-shaped playlist (a real `YoutubeDL.process_ie_result` over a
  synthetic 6-entry playlist with `probe_opts(limit=5)` returns 5 and
  reports `playlist_count` 6 - offline, primary source, in the style of
  the `yes_playlist` tests); `truncated` follows the reported total and
  falls back to the limit when there is none (four cases); entries carry
  a timestamp from `timestamp` or `release_timestamp` and None otherwise;
  a feed entry's `url` keeps its smuggle fragment and its `source_id` is
  `Generic:<guid>`, a YouTube entry's is `Youtube:<id>`, an entry with
  neither has None; `download(title_hint=…)` names the file and its
  `.info.json` sidecar after the hint, a traversing hint cannot escape
  the work dir, a hint that scrubs to nothing gets `FALLBACK_STEM`, an
  empty hint falls back to the site's title; a fan-out child carries
  `entry` and `source` with `source.url` equal to the parent's
  `params["url"]`; `register` prefers the entry title over a CDN
  filename, writes a `source_url` without `#` and the entry's
  `source_id`, and falls back to the download's extractor-scoped id only
  when the extractor is not Generic; hotwords include the source title
  when the download has no uploader.
* `tests/test_db.py` / `tests/test_media.py`: v10 migrates a v9 library
  (copied from the v8 test's shape); `ingest_path` stores both columns; a
  deduped arrival fills an empty `source_url`/`source_id` and does not
  overwrite a set one (both halves); `jobs.enqueue_many` inserts N rows
  with consecutive ids in one transaction and leaves nothing when the
  third insert fails.
* `tests/test_web_url_dialog.py`:
  * template: the link field's `hx-trigger` specs contain no `change`
    (parse the specs; `changed` contains `change` as a substring) and the
    field carries `hx-sync`; the playlist panel's Import button is
    `type="submit"` with `formaction="/transcribe/url"` and `hx-params="not
    files"`; All shown, None and the retry are `type="button"`; the retry
    carries `hx-params="url,patient"`, `hx-vals` with `patient`,
    `hx-target="#url-preview"`, `hx-sync`; the filter input has no name.
  * preview: one row per entry whose checkbox value html-unescapes and
    `json.loads` back to `{url, title, source_id}` for a title with `"`
    and `<`; rows in the library, in the trash and queued are marked, a
    media row with `trashed_at` set reads `in trash`; the header says
    "N episodes", "the first N of T episodes" and "the first N episodes";
    the fake receives `playlistend == MAX_LISTED`; a row shows its date
    (noon-UTC epoch, so the local date is the same in every zone) and
    duration, copes without either, and leaks neither "None" nor "1970";
    the slow hint carries the retry with the attributes above; a patient
    preview waits past the short budget (same `BlockingYdl`, released
    after 0.6 s: the short budget gave up on it and the patient one did
    not); a patient preview that still times out offers no second retry;
    the slot refusal holds for patient too; a cross-site patient post is
    refused; a patient preview of a URL whose 10 s probe is still running
    makes exactly one `build_ydl` call and answers with that listing; a
    follower holds no slot; the "3 videos" assertion becomes "3
    episodes" and "all 3" goes (a deliberate wording change; the PR shows
    the rendered fragment before and after).
  * url route: N ≥ 2 entries become N jobs whose `params.url` are the
    posted URLs in posted order (count catches a last-value read, order a
    set-based one), each with nested options, `from_playlist`, `entry`
    and `source`; exactly the cap's worth are all queued and one more is
    refused with both numbers (`MAX_FAN_OUT` patched to 3); `MAX_LISTED`
    entries posted as one form are refused with "at most 500" and not
    Starlette's "Too many fields"; one bad entry between two good ones
    queues none of them, parametrised over not-JSON, a list, a string,
    null, `{}`, `file://`, `ytsearch:`, `javascript:`, NaN, a 100 KB
    nested value, an oversize value, a non-string url; a 400-character
    title is stored at 300; `feed_url` that is not http(s) is a 400; a
    post with `feed_url` and no entry is a 400 and queues nothing; a post
    with neither still queues one job on the link; the cookies file
    travels to every child; the flash names the feed and the count; a
    post with `url` set to one address and `feed_url` to another queues
    per-entry jobs with `source.url` equal to `feed_url` and no job on
    `url`.
  * never-an-href: a template grep for `href=` bound to `source_url`,
    `webpage_url`, `feed_url`, `params.url`, `source.url` or `entry.url`
    finds nothing (with the guard that at least one template renders one
    of those names somewhere); a job whose `source.url` is
    `javascript:alert(1)` renders it on the details page as text and not
    as an `href`.
  * Node harness: the filter matches the date so "2025" selects that
    year's episodes and hides the rest (titles without digits, so a match
    can only come from the date); All shown ticks only visible rows; the
    count follows the ticks and reads the warning past `data-max` with
    Import disabled; the patient retry (`type="button"`) does not close
    the dialog and the Import submit does; a dropped `text/uri-list`
    switches the tab, fills the field, dispatches `input` and prevents
    default, and a dropped non-http text does neither; Enter in the link
    field with a list present still presses the row button. The stub
    gains `dispatchEvent`, an `Event` constructor and `checked: false` on
    inputs.
* `tests/test_web_jobs.py`: an `ingest_url` job is listed under its
  episode title from queue to history, a bare one under its URL, and a
  failed child with no media row keeps its name; an episode title that is
  markup is escaped on the board.

**Evidence for the working agreement**, a scripted run recorded the way
TASK-007's AC1 was: an isolated instance on port 4299 with
`SCRIBE_DATA_DIR` in the scratchpad, posts from a script (the guard admits
a request with no `Sec-Fetch-Site` and no `Origin`: that is the user at
the keyboard). Recorded:

1. The yt-dlp the run used, from `urls.installed_version()`, with its
   release date.
2. Preview wall-clock and the fragment's header line for Planet Money
   without `patient` ("355 episodes"); Computerphile and The Daily first
   without (the slow hint and the retry control), then with `patient=1`
   (Computerphile: "920 episodes" - under the limit, and a channel
   reports no total; The Daily: "the first 2500 of 2970 episodes"). These
   reach NPR, YouTube and Simplecast. The fragment's size in bytes for the
   largest listing.
3. The trim with no third party: a local `http.server` serving a
   generated feed of 2600 enclosures, previewed with `patient=1`, must say
   "the first 2500 of 2600 episodes" - the one check the suite cannot
   make, because `FakeYdl` returns its dict untrimmed.
4. The `queued` mark deterministically: on a `--no-supervisor` instance,
   post one Planet Money entry, preview again, and record that row's
   state; the job waits (only the supervisor claims) and survives the
   restart (`reconcile` touches only `running` rows).
5. Restart with the supervisor and record `SELECT id, type, status,
   error_code FROM job` for the `ingest_url` and `transcribe` rows;
   `SELECT title, orig_name, source_url, source_id FROM media` for the
   episode, with `orig_name` the scrubbed entry title and not
   `default.mp3…`; the transcribe job's `extra_hotwords` beside §4's
   predicted list, saying whether it matched; the word count; a third
   preview showing `in library` for that row. Then one Computerphile
   video the same way.

## 8. Open questions

None that block. Judgment calls stated for the record: `MAX_LISTED` at
2500 and `PATIENT_TIMEOUT_SECONDS` at 60 are numbers chosen from the
measurements above and easy to move; "queued" winning over "in library"
in the marks; and the id-plus-URL match rather than a canonical-page
column, which is a follow-up if hand-typed share links turn out to
matter.
