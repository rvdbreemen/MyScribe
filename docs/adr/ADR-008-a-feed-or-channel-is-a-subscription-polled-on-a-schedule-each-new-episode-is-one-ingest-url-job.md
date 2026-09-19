---
id: "ADR-008"
title: "A feed or channel is a subscription polled on a schedule; each new episode is one ingest_url job"
status: "Accepted"
date: "2026-09-19"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "ingest"
  - "feeds"
  - "subscriptions"
  - "scheduling"
  - "yt-dlp"
  - "provenance"
aliases:
  - "feed import"
  - "feed subscription"
  - "feed polling"
  - "feeds page"
  - "episode list"
  - "channel import"
  - "RSS import"
  - "podcast import"
components:
  - "scribe.ingest.urls"
  - "scribe.ingest.feeds"
  - "scribe.web.ingest_ui"
  - "scribe.web.feeds_ui"
  - "scribe.stages.url_stage"
  - "scribe.app"
symbols:
  - "probe_in_slot"
  - "known_sources"
  - "add_url"
  - "enqueue_many"
  - "source_id_for"
  - "FeedWatcher"
  - "due_feeds"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-008 A feed or channel is a subscription polled on a schedule; each new episode is one ingest_url job

## Status

Accepted, 2026-09-19.
first shape.

## Status History

```yaml
status_history:
  - date: 2026-09-08
    status: Proposed
    changed_by: Claude Fable 5.1 (agent, session 2026-09-08)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-09
    status: Proposed
    changed_by: Claude Opus 5 (agent, session 2026-09-09)
    reason: Revised at the user's instruction; the first shape forbade the feed table, subscription and poller the user then asked for. Listing and import are unchanged; the prohibition becomes a bounded subscription. Edited at the source because the record was never accepted.
    changed_via: source edit
  - date: 2026-09-19
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert on 2026-09-19. The decision carries no open questions and is built: FeedWatcher is its own daemon thread in scribe/ingest/feeds.py, started from the lifespan in scribe/app.py beside the supervisor and the watcher, the Feeds page is scribe/web/feeds_ui.py, and tests/test_feeds.py and tests/test_web_feeds.py cover the due selection, the back-catalogue rule, the per-poll cap and the page. A decision this far into production should not read as a proposal."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

The first ask (TASK-021): paste or drop a podcast RSS feed or a YouTube
channel, see its episodes, and import one or many. That shipped, and the
first live run on 2026-09-09 imported 50 episodes of the Hacker History
Podcast through the real UI.

The ask then grew: a feed has to stay monitored once you start with it,
checked daily or at startup, and feeds have to exist beside jobs as
something you can see and manage - a YouTube channel included. A one-shot
import is no longer the whole feature.

Measured live on 2026-09-08 against yt-dlp 2026.08.19, and unchanged by this
revision:

* yt-dlp's generic extractor already reads a podcast feed
  (`GenericIE._extract_rss`) into a flat playlist: Planet Money, 355
  entries with title, duration and timestamp, in 4.2 s; The Daily, 2970
  entries, in 13.9 s. A bare YouTube channel URL resolves to its Videos
  tab: Computerphile, 920 entries, in 22.6 s.
* A feed entry fetched on its own has no usable name: a bare enclosure
  probes as `default.mp3_ywr3ahjkcgo_6c70...` with no uploader.
* yt-dlp smuggles a feed item's guid into the enclosure URL's fragment to
  set its own id.
* Starlette 1.6.0 refuses a form of more than 1000 fields.

Three facts about this codebase decide where a poller may live, all read on
2026-09-09:

* `supervisor._loop` (`scribe/supervisor.py:193-208`) does exactly one thing
  per tick: `jobs.claim_next`, or a 1 s wait when the queue is empty. It owns
  no timers and no sweeps.
* `watching.Watcher._loop` (`scribe/ingest/watching.py:774-799`) is the house
  pattern for periodic work: its own daemon thread, its own `db.connect`
  (never `app.state.conn`, because its long pass must not hold the jobs board
  hostage), one `reconcile` before the loop, then a `pump` per tick, every
  step wrapped in `_survive`. Started from the lifespan at
  `scribe/app.py:225-229` beside the supervisor.
* Startup-only work already exists in that same lifespan: `reconcile`,
  `sweep_stderr`, `recording.sweep` (`scribe/app.py:216-218`). None of it is
  on a timer.

And two facts make an unattended poller sharper than a person clicking
Import:

* `url_stage.register` enqueues a transcribe job unconditionally
  (`scribe/stages/url_stage.py:201`).
* `media.ingest_path` reports `deduped` but nothing acts on it: the flag is
  set at `scribe/media.py:276` and `:325`, and read only to be logged
  (`url_stage.py:219`, `web/transcribe_dialog.py:245`).

So anything a poll lets through is downloaded *and* transcribed. A person
re-importing by hand sees the "in library" and "queued" markers first; a
poller at 03:00 sees nothing and asks nobody.

Four questions follow. Where does the schedule live? What counts as new?
How much may one poll start? And what does a person see and control?

## Decision Drivers

* ADR-001: the web process lists, a runner child fetches. No model in the
  web process; a poller must not become a second job engine.
* No second parser: yt-dlp already reads feeds and every video site.
* A subscription outlives the dialog that created it, so it is a row.
* A schedule that lives in a `sleep` is lost to a closed laptop and to every
  restart. This is a desktop app, not a server: it is off more than it is on.
* An unattended import spends the GPU. Every unbounded edge - the back
  catalogue, a feed that rewrites its guids - must be bounded before it runs
  unwatched.
* A person comes back to a feed. Seeing what it did, and turning it off, is
  the difference between a subscription and a surprise.
* Every URL and title here is a stranger's text.

## Considered Options

Where the schedule lives:

* **Its own daemon thread, modelled on `watching.Watcher`, waking often and
  asking which feeds are due.**
* Periodic work added to the supervisor loop.
* A `feed_poll` job the supervisor claims, which re-queues itself.
* The operating system's scheduler (Task Scheduler, cron, launchd).
* Startup only, with no timer at all.

What the selection and the record are:

* **A `feed` table plus the existing `media.source_id` provenance.**
* No table: re-list on demand and diff against the library each time.

## Decision Outcome

Chosen: **a feed is a row, polled by its own thread on a due-date, and every
new episode is one `ingest_url` job**, on top of the listing and import
decisions this ADR already recorded and which do not change.

* **Listing and import are unchanged.** yt-dlp lists under the preview's
  carve-out (a 10 s keystroke budget, 60 s on the explicit "list them
  anyway", one listing per URL, `MAX_LISTED` as the ceiling); the browser's
  form holds the selection; each ticked episode becomes one `ingest_url` job
  carrying the listing's title and extractor-scoped id, and `register` writes
  `media.source_url` and `media.source_id`.
* **A subscription is a `feed` row** (schema v11): the URL, the title yt-dlp
  gave it, the poll interval, `checked_at`, the last result, and a per-feed
  switch for what a new episode does. A YouTube channel or playlist is the
  same row as an RSS feed, because it is the same probe and the same job.
* **The poller is its own daemon thread**, `FeedWatcher`, shaped exactly like
  `watching.Watcher`: its own connection, `_survive` around every step,
  started and stopped from the lifespan beside the supervisor and the
  watcher. It is **not** in the supervisor loop, whose tick is claim-only,
  and it is not a self-requeueing job.
* **Due, not timed.** The thread wakes on a short interval and asks which
  feeds are overdue by their own interval (`checked_at` older than now minus
  the interval). A restart or a closed lid loses nothing, and "check on each
  startup" needs no separate mechanism: at boot, everything overdue is due.
* **New means unknown to `known_sources`** - the function the listing already
  uses: `media.source_id`, then the fragment-stripped `media.source_url`,
  then live jobs, over library and trash alike. An episode in the trash
  counts as known, because deleting it was a decision.
* **Bounded.** Subscribing imports no back catalogue: a feed starts knowing
  the episodes present when it was subscribed. One poll queues at most
  `MAX_NEW_PER_POLL`, and says in the feed's last result when it hit that.
* **Visible.** A Feeds page beside Jobs, the same shape as the jobs board:
  one module under `scribe/web/`, one `NAV` entry, a page template and a
  self-polling fragment. It lists each feed with its last check, what that
  check found, and controls to pause, poll now, or unsubscribe.

### Confirmation

`tests/test_ingest_urls.py` (the listing's limit and total, the feed-shaped
fixture derived from yt-dlp's own extractor, the fan-out carrying entry and
source, register naming and provenance), `tests/test_web_url_dialog.py` (the
list, the marks, the coalesced and patient probe, the three cases of the url
route), a new `tests/test_feeds.py` (due selection with a frozen clock, the
back-catalogue rule, the per-poll cap, a poll that finds nothing, a poll
whose probe fails), `tests/test_web_feeds.py` (the page, pause, poll now,
unsubscribe), and the live run recorded in the task.

## Decision Contract

### Must

* A feed, a channel or a playlist is listed by yt-dlp through
  `urls.probe(limit=...)`; no other feed or HTML parser is added to `scribe`.
* An interactive listing runs only under `ingest_ui.probe_in_slot`.
* An entry's URL is fetched verbatim by the runner child; anything that
  compares or stores it strips the fragment first.
* Every posted entry passes `parse_entry` before any job is queued, and the
  jobs of one import are inserted by `jobs.enqueue_many` in one transaction.
* The media row's `source_url` and `source_id` are compared by
  `known_sources` and never rendered as an `href`.
* The listing's title names the media row; the download's title is the
  fallback.
* Periodic feed work runs in `FeedWatcher`'s own thread with its own
  connection, every step wrapped so one bad feed cannot end the loop.
* A feed's next poll is decided by its stored `checked_at` and interval, not
  by elapsed process time.
* Nothing is queued for an episode `known_sources` already knows, and one
  poll queues at most `MAX_NEW_PER_POLL` jobs.
* Subscribing queues nothing by itself: the episodes present at subscription
  time are recorded as known.

### Must Not

* Add periodic work to `supervisor._loop`, or express a schedule as a job
  that re-queues itself.
* Poll a feed the person paused, or keep polling one whose probe has failed
  past the recorded limit without saying so on the Feeds page.
* Render the checkbox value with `|tojson`; it is `json.dumps` output
  autoescaped as an attribute.
* Read the ticked entries through `_fields`, which keeps the last value per
  name.
* Let a feed's own text - its title, an episode title - reach a template
  unescaped, or a shell in any form.

### Exceptions

* The fan-out inside the runner child (`url_stage._fan_out`) still queues
  its children one `jobs.enqueue` at a time: it runs where the cost falls
  on nobody.
* A person pressing "poll now" on the Feeds page bypasses the due check -
  that is the point of the button - but not the per-poll cap.

### Verification

* `tests/test_ingest_urls.py::test_a_feed_entry_keeps_its_smuggled_url_and_gets_the_guid_as_its_source_id`,
  `::test_register_names_a_feed_episode_after_the_feed_not_the_cdn`,
  `::test_yt_dlp_honours_playlistend_on_a_flat_playlist_and_still_reports_the_total`.
* `tests/test_web_url_dialog.py::test_a_patient_preview_attaches_to_the_listing_already_running`,
  `::test_one_bad_entry_among_good_ones_queues_none_of_them`,
  `::test_no_template_binds_an_href_to_a_stranger_s_url`.
* `tests/test_feeds.py::test_a_new_subscription_queues_nothing`,
  `::test_only_overdue_feeds_are_polled`,
  `::test_an_episode_already_in_the_library_or_the_trash_is_not_queued_again`,
  `::test_a_poll_queues_at_most_the_cap_and_says_so`,
  `::test_a_feed_whose_probe_raises_does_not_end_the_loop`.
* `grep -rn "feedparser" scribe/` returns nothing.

## Consequences

### Positive

* One dependency, one fetch path, one queue. A feed episode is a job like a
  video is, with a name and a source.
* The schedule survives what a desktop app actually does: sleep, restart,
  and days switched off. Startup checking is the same code as daily
  checking.
* "In library", "in trash" and "queued" are one query over two columns, and
  the same function decides what a poll may queue - so the marks a person
  sees and the poller's judgement cannot drift apart.
* A second page of the jobs board's shape is cheap: a module, a NAV entry,
  and two templates that reuse the existing macros.

### Negative

* A third long-lived thread in the web process (supervisor, watcher,
  feeds). Each owns a connection; ADR-002's lock discipline carries the
  weight.
* A feed longer than `MAX_LISTED` lists its newest 2500 only. The Daily is
  the measured case.
* An abandoned listing runs to the end of its work. What is bounded is the
  slot, not the seconds.
* Matching is by URL and extractor id, not by a canonical page. A feed that
  rewrites its guids looks new; the per-poll cap is what keeps that from
  becoming 2500 transcriptions overnight, and the Feeds page is where it
  shows.
* A poll spends the GPU without anyone present. The per-feed switch and the
  cap bound it; nothing prevents a person from subscribing to more than the
  machine can chew.
* The url route reads up to `MAX_LISTED + 64` form fields, above Starlette's
  default of 1000.

## Pros and Cons of the Options

### Its own thread, waking often, polling what is due

* Good, because it copies a pattern already running in this process, with
  its failure handling and its connection discipline proven by watch folders.
* Good, because the due-date is in the database, so no schedule is lost.
* Bad, because it is a third thread, and because a short wake interval does
  a little work forever to notice a daily event.

### Periodic work in the supervisor loop

* Good, because it adds no thread.
* Bad, because that loop's tick is claim-a-job and nothing else; feed I/O in
  it would delay claiming, and a slow feed would stall the queue. ADR-001
  keeps that loop thin on purpose.

### A self-requeueing feed_poll job

* Good, because it reuses the queue, the jobs board and cancellation.
* Bad, because a schedule expressed as a job that re-queues itself has no
  resting state: it competes with real work for the single claim slot, and
  a cancelled or failed link breaks the chain silently.

### The operating system's scheduler

* Good, because the OS already solves waking a sleeping machine.
* Bad, because it is three implementations, it needs install-time
  privileges, and it is invisible from inside the app - the opposite of the
  Feeds page the user asked for.

### Startup only

* Good, because it is nearly free and needs no thread.
* Bad, because a machine left on for a week never checks. The ask was daily
  or at startup; due-dates give both.

### No table, diff on demand

* Good, because nothing to migrate or sweep.
* Bad, because there is nowhere to put a pause switch, a last result or a
  poll interval, and nothing to show on a Feeds page. A subscription with no
  record is not a subscription.

## Open Questions

None.

## Related Decisions

* ADR-001 (the web process lists under the preview carve-out; a runner
  child fetches; the supervisor loop stays thin).
* ADR-002 (`known_sources`, `enqueue_many` and the poller's own connection
  under SQLite's lock discipline).

## References

* `docs/superpowers/specs/2026-09-08-feed-episode-import-design.md`
  (revision 2, with the measurements and the critique they answer).
* `scribe/web/ingest_ui.py` (`probe_in_slot`, `known_sources`, `add_url`,
  `parse_entry`), `scribe/ingest/urls.py` (`probe`, `source_id_for`),
  `scribe/stages/url_stage.py` (`_fan_out`, `register`), `scribe/db.py`
  (v10, v11).
* `scribe/supervisor.py:193-208` (the claim-only tick),
  `scribe/ingest/watching.py:774-799` (the periodic-work pattern),
  `scribe/app.py:216-229` (lifespan startup work and thread wiring).
* yt-dlp 2026.08.19, `yt_dlp/extractor/generic.py` `_extract_rss` and
  `yt_dlp/utils/_utils.py` `smuggle_url`.
* Starlette 1.6.0, `starlette/requests.py` `Request.form(max_fields=...)`.

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
