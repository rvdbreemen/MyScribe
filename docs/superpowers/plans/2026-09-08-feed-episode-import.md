# Feed and Channel Episode Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A podcast RSS feed, a YouTube channel or a playlist pasted (or dropped) into the transcribe dialog lists its episodes; the user ticks one or many and each becomes its own `ingest_url` job, named after the episode and marked as known the next time the feed is listed.

**Architecture:** The web process lists (yt-dlp `extract_info(download=False)` in the preview's existing bounded worker thread, now coalesced per URL and with a patient budget); the browser holds the selection in the form; `POST /transcribe/url` grows two cases (ticked entries → N jobs in one transaction; a list with nothing ticked → 400) in front of today's one; a runner child fetches each episode (ADR-001) with the entry's title and writes provenance (`media.source_url`, `media.source_id`) that the next listing compares.

**Tech Stack:** yt-dlp (already pinned), FastAPI/Starlette, Jinja2, vendored htmx 2.0.10, SQLite (migration v10), the Node DOM harness in `tests/test_web_url_dialog.py` for `app.js`.

**Spec:** `docs/superpowers/specs/2026-09-08-feed-episode-import-design.md` (revision 2). The plan argues from it; executors read both.

## Global Constraints

- ADR-001: the web process never imports a model; downloads run in runner children. The listing is the preview carve-out `ingest_ui.py` documents - bounded by a budget, a slot count held inside the thread, and `MAX_LISTED`.
- ADR-002: every database access in the web process holds `db.LOCK`; `known_sources` and `enqueue_many` do.
- Everything reached over the network is untrusted: entry URLs through `_web_url`, titles escaped in templates and scrubbed before they are filenames, never an `href` bound to a stranger's URL.
- No test reaches the network. yt-dlp is `FakeYdl` at `urls.build_ydl`; feed-shaped fixtures come from `GenericIE()._extract_rss` over a string the test wrote (stdlib `xml.etree` on our own literal is fine; yt-dlp parses real feeds in production).
- The suite: `.venv/Scripts/python -m pytest -q`, in halves when it stalls (`tests/test_[a-r]*.py`, `tests/test_[s-z]*.py`). Never bare `python`.
- Pins that must stay green unchanged: `tests/test_web_url_dialog.py::test_posting_a_url_enqueues_an_ingest_url_job_with_the_options_nested` (params exactly `{url, folder_id, options}` when no entry is posted) and `tests/test_ingest_urls.py::test_a_single_video_is_ingested_and_queued_for_transcription` (a plain video's transcribe params byte-identical).
- Constants and where they live: `urls.py` gains nothing numeric; `url_stage.MAX_FAN_OUT = 500` is read at request time by the route; `ingest_ui.py` gains `MAX_LISTED = 2500`, `PATIENT_TIMEOUT_SECONDS = 60.0`, `FORM_FIELD_CEILING = MAX_LISTED + 64`, `MAX_ENTRY_BYTES = 4096`, `MAX_FEED_TITLE = 120`; titles are cut at `library.MAX_NAME` (300).
- Commit after every task on branch `feat/feed-episode-import`; never on `main`.

## File Structure

```
scribe/
  ingest/urls.py           probe(limit), UrlInfo.total/.truncated, _entries timestamp+source_id, download(title_hint)
  stages/url_stage.py      _fan_out carries entry/source; register names, provenance, hotwords
  media.py                 ingest_path(source_url, source_id) + backfill on dedupe
  db.py                    migration v10 (two columns), SCHEMA_VERSION = 10
  jobs.py                  enqueue_many
  web/ingest_ui.py         constants, coalesced probe_in_slot, probe_or_none(patient), known_sources,
                           parse_entry, preview_view(entries…), add_url three cases
  web/jobs_ui.py           job_view title for ingest_url
  templates/_url_panel.html          list branch, retry button
  templates/transcribe_dialog.html   hx-trigger input changed, hx-sync
  static/app.css           .episodes, .episode, .ep-state, toolbar, hide row button under :has(.episodes)
  static/app.js            filter / All shown / None / count / cap, link drop
docs/
  adr/ADR-008-…md          Proposed
  superpowers/specs/2026-09-01-myscribe-design.md   §9 amendment
README.md                  one line
tests/
  test_ingest_urls.py      feed_info fixture, probe/entries/download/fan-out/register tests
  test_db.py test_media.py test_jobs.py
  test_web_url_dialog.py   preview, url route, markup, never-an-href, node harness
  test_web_jobs.py
```

---

### Task 1: `urls.py` - a bounded listing with a total, entries that carry their identity, a download named by its hint

**Files:**
- Modify: `scribe/ingest/urls.py` (`probe_opts`, `UrlInfo`, `probe`, `download`, `_entries`, `_rename_to_title` call site; new `_as_int`, `_source_id`)
- Test: `tests/test_ingest_urls.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `probe_opts(cookies_file: str | None = None, *, limit: int | None = None) -> dict` - `limit` → `opts["playlistend"]`; `playlist_items` stays `None`.
  - `probe(url, *, cookies_file=None, limit=None, ydl=None) -> UrlInfo`.
  - `UrlInfo` gains `total: int | None = None`, `truncated: bool = False`.
  - `_entries(info) -> list[dict]` entries are `{id, title, duration, url, timestamp, source_id}`; `url` verbatim (fragment kept).
  - `download(url, dest_dir, *, on_progress=None, cookies_file=None, ydl=None, title_hint: str | None = None) -> DownloadedMedia`.
  - `source_id_for(entry: dict) -> str | None` (module-level, also used by `_entries`): `f"{ie_key}:{id}"` when both truthy, else `Generic:<force_videoid>` from a smuggled URL, else None.

- [ ] **Step 1: Add the `feed_info` fixture and the failing tests**

Add to `tests/test_ingest_urls.py`, after `playlist_info`:

```python
import xml.etree.ElementTree as ET  # our own literal below, never a real feed


FEED_URL = "https://feeds.test/podcast.xml"

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<title>The Hitchhiker Lectures</title><description>Mostly harmless.</description>
<item><title>Trump drinks Venezuela's milkshake &amp; "more" &lt;3 café</title><guid>guid-0</guid>
  <pubDate>Fri, 04 Sep 2026 12:00:00 GMT</pubDate><itunes:duration>25:27</itunes:duration>
  <enclosure url="https://cdn.test/ep/default.mp3?d=1&amp;e=0" type="audio/mpeg"/></item>
<item><title>Lecture 1</title><guid>guid-1</guid><pubDate>Wed, 02 Sep 2026 12:00:00 GMT</pubDate>
  <enclosure url="https://cdn.test/ep/default.mp3?d=1&amp;e=1" type="audio/mpeg"/></item>
<item><title>Lecture 2</title><guid>guid-2</guid>
  <enclosure url="https://cdn.test/ep/default.mp3?d=1&amp;e=2" type="audio/mpeg"/></item>
<item><title>A page, not an enclosure</title><guid>guid-3</guid><link>https://example.test/page/3</link></item>
<item><title>Nothing to fetch</title><guid>guid-4</guid></item>
</channel></rss>"""


def feed_info() -> dict:
    """A feed as yt-dlp's own generic extractor reads one - derived, not mirrored.

    `GenericIE._extract_rss` is what turns a real feed into a playlist, so the
    fixture asks it rather than restating its output: if yt-dlp changes the
    shape (the smuggled guid, the timestamp field), this fixture changes with
    it and the tests say so. Four entries come back: the item with neither an
    enclosure nor a link is skipped by yt-dlp itself.
    """
    from yt_dlp.extractor.generic import GenericIE

    info = GenericIE()._extract_rss(FEED_URL, None, ET.fromstring(FEED_XML))
    info["extractor"] = "generic"
    info["extractor_key"] = "Generic"
    info["webpage_url"] = FEED_URL
    info["playlist_count"] = len(info["entries"])  # as YoutubeDL fills it for a list
    return info
```

Then the tests (each is a function; put them in the section they belong to):

```python
def test_probe_opts_limit_becomes_playlistend_and_leaves_playlist_items_alone():
    assert urls.probe_opts(limit=5)["playlistend"] == 5
    assert urls.probe_opts(limit=5)["playlist_items"] is None
    assert urls.probe_opts().get("playlistend") is None


def test_yt_dlp_honours_playlistend_on_a_flat_playlist_and_still_reports_the_total():
    """Asked of yt-dlp itself, offline, in the style of the yes_playlist tests:
    a list-shaped playlist (a feed) cut at 5 still says it held 6."""
    from yt_dlp import YoutubeDL

    info = playlist_info(6)
    info["extractor"], info["extractor_key"] = "generic", "Generic"
    ydl = YoutubeDL({**urls.probe_opts(limit=5)})
    out = ydl.process_ie_result(dict(info), download=False)
    assert len(out["entries"]) == 5
    assert out["playlist_count"] == 6


@pytest.mark.parametrize(
    "count, playlist_count, limit, expected_truncated, expected_total",
    [
        (3, 7, 3, True, 7),        # the total says more was there
        (3, 3, 3, False, 3),       # exactly the limit, and the total agrees: not cut
        (3, None, 3, True, None),  # no total (a channel): the count is the signal
        (3, None, None, False, None),
        (2, None, 3, False, None),
    ],
)
def test_truncated_follows_the_total_and_falls_back_to_the_limit(
    count, playlist_count, limit, expected_truncated, expected_total
):
    info = playlist_info(count)
    if playlist_count is not None:
        info["playlist_count"] = playlist_count
    probed = urls.probe("https://example.test/playlist?list=PL42", limit=limit, ydl=FakeYdl(info))
    assert probed.truncated is expected_truncated
    assert probed.total == expected_total


def test_probe_passes_the_limit_to_the_options_it_builds(monkeypatch):
    seen = {}
    fake = FakeYdl(playlist_info(2))
    monkeypatch.setattr(urls, "build_ydl", lambda opts: (seen.update(opts), fake.configure(opts))[1])
    urls.probe("https://example.test/playlist?list=PL42", limit=42)
    assert seen["playlistend"] == 42


def test_entries_carry_a_timestamp_from_either_field_and_none_otherwise():
    info = playlist_info(3)
    info["entries"][0]["timestamp"] = 1788523200
    info["entries"][1]["release_timestamp"] = 1788350400
    probed = urls.probe("https://example.test/playlist?list=PL42", ydl=FakeYdl(info))
    assert [e["timestamp"] for e in probed.entries] == [1788523200.0, 1788350400.0, None]


def test_a_feed_entry_keeps_its_smuggled_url_and_gets_the_guid_as_its_source_id():
    probed = urls.probe(FEED_URL, ydl=FakeYdl(feed_info()))
    first = probed.entries[0]
    assert first["url"].startswith("https://cdn.test/ep/default.mp3?d=1&e=0#__youtubedl_smuggle=")
    assert first["source_id"] == "Generic:guid-0"
    assert first["title"] == 'Trump drinks Venezuela\'s milkshake & "more" <3 café'
    assert first["timestamp"] == 1788523200.0 and first["duration"] == 1527.0
    assert probed.entries[2]["timestamp"] is None
    assert probed.entries[3]["url"] == "https://example.test/page/3#__youtubedl_smuggle=%7B%22force_videoid%22%3A+%22guid-3%22%7D"
    assert len(probed.entries) == 4 and probed.total == 4 and probed.truncated is False


def test_a_youtube_entry_s_source_id_is_the_extractor_and_the_video_id():
    info = playlist_info(1)
    info["entries"][0]["ie_key"] = "Youtube"
    probed = urls.probe("https://example.test/playlist?list=PL42", ydl=FakeYdl(info))
    assert probed.entries[0]["source_id"] == "Youtube:vid0"


def test_an_entry_with_neither_an_ie_key_nor_a_guid_has_no_source_id():
    probed = urls.probe("https://example.test/playlist?list=PL42", ydl=FakeYdl(playlist_info(1)))
    assert probed.entries[0]["source_id"] is None


def test_download_names_the_file_and_its_sidecar_after_the_hint(tmp_path):
    fake = FakeYdl(single_info(), files=("download.m4a", "download.info.json"))
    got = urls.download("https://example.test/watch?v=abc123", tmp_path, ydl=fake, title_hint="Love in the time of Palantir")
    assert got.path.name == "Love in the time of Palantir.m4a"
    assert (tmp_path / "Love in the time of Palantir.info.json").is_file()
    assert got.title == "Vogon Poetry Slam"  # the hint names the file, not the media


def test_a_traversing_title_hint_cannot_escape_the_work_dir(tmp_path):
    work = tmp_path / "work"
    got = urls.download("https://example.test/watch?v=abc123", work, ydl=FakeYdl(single_info()), title_hint=r"..\..\evil")
    assert got.path.resolve().is_relative_to(work.resolve())
    assert got.path.is_file()


def test_a_hint_that_scrubs_to_nothing_still_names_the_file(tmp_path):
    got = urls.download("https://example.test/watch?v=abc123", tmp_path, ydl=FakeYdl(single_info()), title_hint="...")
    assert got.path.stem == urls.FALLBACK_STEM


def test_an_empty_hint_falls_back_to_the_site_s_title(tmp_path):
    got = urls.download("https://example.test/watch?v=abc123", tmp_path, ydl=FakeYdl(single_info()), title_hint="")
    assert got.path.stem == "Vogon Poetry Slam"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_ingest_urls.py -q -k "playlistend or truncated or timestamp or source_id or hint" `
Expected: FAIL (`TypeError: probe_opts() got an unexpected keyword argument 'limit'`, `probe() got an unexpected keyword 'limit'`, missing keys, `download() got an unexpected keyword 'title_hint'`).

- [ ] **Step 3: Implement**

In `scribe/ingest/urls.py`:

```python
from urllib.parse import urlparse  # already imported

@dataclass(frozen=True)
class UrlInfo:
    ...
    entries: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    total: int | None = None
    """yt-dlp's `playlist_count`: the whole feed's length, whatever `limit` cut
    it to. A feed is a list and always reports one; a paginated channel does
    not, so None there means "unknown", not "zero"."""
    truncated: bool = False
    """Whether `limit` cut the listing short. From `total` when there is one;
    otherwise the count reaching the limit is the only signal, and a source of
    exactly `limit` entries reads as cut, which is the honest answer since
    nothing can tell the two apart."""


def probe_opts(cookies_file: str | None = None, *, limit: int | None = None) -> dict:
    opts = {..., "playlist_items": None, "noplaylist": True}
    if limit is not None:
        # `playlistend` bounds a paginated channel's *work* (yt-dlp stops
        # asking for pages); a feed is one document and is only trimmed. It
        # is honoured only while `playlist_items` is None, which is why that
        # key stays here.
        opts["playlistend"] = int(limit)
    ...


def probe(url, *, cookies_file=None, limit=None, ydl=None) -> UrlInfo:
    url = ensure_http_url(url)
    ydl = ydl if ydl is not None else build_ydl(probe_opts(cookies_file, limit=limit))
    info = _extract(ydl, url, download=False)
    entries = _entries(info)
    returned = len(info.get("entries") or [])   # raw, before URL-less items are dropped
    total = _as_int(info.get("playlist_count"))
    truncated = (total > returned) if total is not None else (limit is not None and returned >= limit)
    return UrlInfo(..., entries=entries, info=info, total=total, truncated=truncated)


def download(url, dest_dir, *, on_progress=None, cookies_file=None, ydl=None, title_hint=None):
    ...
    info = _extract(ydl, url, download=True)
    path = _rename_to_title(_locate(dest, info), title_hint or str(info.get("title") or ""))
    ...


def source_id_for(entry: dict) -> str | None:
    """The extractor-scoped identity of a listed entry, or None.

    `Youtube:kVXp6UNVPTo` for a flat YouTube entry (ie_key plus id);
    `Generic:<guid>` for a feed item, whose guid yt-dlp smuggles into the
    enclosure URL's fragment to set its own id. Scoped by extractor because
    ids are only unique within one. Compared by `known_sources`, stored on
    the media row by `register`; never rendered.
    """
    ie_key, ident = entry.get("ie_key"), entry.get("id")
    if ie_key and ident:
        return f"{ie_key}:{ident}"
    guid = _smuggled_video_id(str(entry.get("url") or ""))
    return f"Generic:{guid}" if guid else None


def _smuggled_video_id(url: str) -> str | None:
    """The `force_videoid` yt-dlp smuggled into ``url``'s fragment, if any.

    yt-dlp's own reader, imported lazily like `_is_unsupported`: this runs in
    `probe`, where yt-dlp is already loaded, and never in a process that has
    not paid for it.
    """
    if "#__youtubedl_smuggle=" not in url:
        return None
    try:
        from yt_dlp.utils import unsmuggle_url
    except ImportError:  # pragma: no cover
        return None
    _bare, data = unsmuggle_url(url, {})
    value = (data or {}).get("force_videoid")
    return str(value) if value else None


def _entries(info):
    ...
        entries.append({
            "id": str(entry.get("id") or ""),
            "title": str(entry.get("title") or ""),
            "duration": _as_float(entry.get("duration")),
            "url": str(url),  # verbatim: the fragment carries what yt-dlp smuggled
            "timestamp": _as_float(entry.get("timestamp")) or _as_float(entry.get("release_timestamp")),
            "source_id": source_id_for(entry),
        })


def _as_int(value) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out >= 0 else None
```

Update the two docstrings that spell the entry shape (`UrlInfo.entries`, `_entries`) to `{id, title, duration, url, timestamp, source_id}`. Careful with `timestamp`: `_as_float(0)` is `0.0` which is falsy - write it as `_as_float(entry.get("timestamp"))`, and only if that is None read `release_timestamp`.

- [ ] **Step 4: Run the file**

Run: `.venv/Scripts/python -m pytest tests/test_ingest_urls.py -q`
Expected: all pass, including the existing ones (`test_probe_reads_a_playlist_as_its_entries` reads keys, not dict equality).

- [ ] **Step 5: Commit**

```bash
git add scribe/ingest/urls.py tests/test_ingest_urls.py
git commit -m "feat(urls): a bounded listing with its total, entries that know their identity, downloads named by a hint"
```

---

### Task 2: Provenance on the media row and N jobs in one transaction

**Files:**
- Modify: `scribe/db.py` (v10 migration, `SCHEMA_VERSION = 10`), `scribe/media.py` (`ingest_path`, `_insert_media`), `scribe/jobs.py` (`enqueue_many`)
- Test: `tests/test_db.py`, `tests/test_media.py`, `tests/test_jobs.py`

**Interfaces:**
- Produces:
  - `media.ingest_path(conn, src, *, title=None, folder_id=None, link=True, source_url=None, source_id=None) -> dict` - the returned dict carries `source_url`/`source_id` as they now are on the row.
  - `jobs.enqueue_many(conn, type_, params_list: Sequence[dict], priority=0) -> list[int]`.

- [ ] **Step 1: Failing tests**

`tests/test_db.py`, copied from the v8 test's shape:

```python
def test_migrate_walks_a_v9_database_up_to_the_provenance_columns(tmp_path):
    """A library from before the feed import is at user_version 9. It gains
    two nullable columns and every row it already holds reads as
    "arrived without a source", which is the truth."""
    path = tmp_path / "v9.db"
    old = db.connect(path)
    for migration in db._MIGRATIONS[:9]:
        old.executescript(migration)
    old.execute("PRAGMA user_version = 9")
    old.commit()
    _seed_media_and_run(old)
    assert "source_url" not in _columns(old, "media")

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert {"source_url", "source_id"} <= set(_columns(old, "media"))
    row = old.execute("SELECT source_url, source_id FROM media").fetchone()
    assert row["source_url"] is None and row["source_id"] is None
    old.close()
```

`tests/test_media.py`, beside `test_ingest_dedupes_identical_content` (use the file's `make_file`/`src_dir` helpers as that test does):

```python
def test_ingest_records_where_a_file_came_from(conn, data_dir, src_dir):
    src = make_file(src_dir / "ep.mp3", b"bytes of ep")
    row = media.ingest_path(conn, src, source_url="https://cdn.test/ep.mp3", source_id="Generic:guid-0")
    stored = conn.execute("SELECT source_url, source_id FROM media WHERE id=?", (row["id"],)).fetchone()
    assert (stored["source_url"], stored["source_id"]) == ("https://cdn.test/ep.mp3", "Generic:guid-0")
    assert (row["source_url"], row["source_id"]) == ("https://cdn.test/ep.mp3", "Generic:guid-0")


def test_a_deduped_arrival_fills_in_a_missing_source_and_keeps_a_set_one(conn, data_dir, src_dir):
    """An upload first, the feed later: the row gains the provenance it lacked.
    The feed first, another URL later: the first source stands."""
    src = make_file(src_dir / "ep.mp3", b"bytes of ep")
    first = media.ingest_path(conn, src)                      # an upload knows no source
    assert first["source_url"] is None
    second = media.ingest_path(conn, src, source_url="https://a.test/1.mp3", source_id="Generic:g1")
    assert second["deduped"] is True and second["id"] == first["id"]
    assert (second["source_url"], second["source_id"]) == ("https://a.test/1.mp3", "Generic:g1")
    third = media.ingest_path(conn, src, source_url="https://b.test/1.mp3", source_id="Generic:g2")
    assert (third["source_url"], third["source_id"]) == ("https://a.test/1.mp3", "Generic:g1")
    stored = conn.execute("SELECT source_url, source_id, COUNT(*) AS n FROM media").fetchone()
    assert (stored["source_url"], stored["source_id"], stored["n"]) == ("https://a.test/1.mp3", "Generic:g1", 1)
```

`tests/test_jobs.py` (use its `conn` fixture):

```python
def test_enqueue_many_inserts_every_row_with_consecutive_ids(conn):
    ids = jobs.enqueue_many(conn, "ingest_url", [{"url": f"https://x.test/{i}"} for i in range(5)])
    assert ids == list(range(ids[0], ids[0] + 5))
    rows = conn.execute("SELECT id, type, status, params_json FROM job ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["queued"] * 5
    assert json.loads(rows[3]["params_json"]) == {"url": "https://x.test/3"}


def test_enqueue_many_is_all_or_nothing(conn, monkeypatch):
    real = conn.execute
    calls = {"n": 0}

    def flaky(sql, *args):
        if sql.startswith("INSERT INTO job"):
            calls["n"] += 1
            if calls["n"] == 3:
                raise sqlite3.OperationalError("disk I/O error")
        return real(sql, *args)

    monkeypatch.setattr(conn, "execute", flaky)  # sqlite3.Connection allows attribute set? if not, wrap conn in a proxy
    with pytest.raises(sqlite3.OperationalError):
        jobs.enqueue_many(conn, "ingest_url", [{"url": f"https://x.test/{i}"} for i in range(5)])
    assert real("SELECT COUNT(*) FROM job").fetchone()[0] == 0


def test_enqueue_many_with_nothing_to_enqueue_inserts_nothing(conn):
    assert jobs.enqueue_many(conn, "ingest_url", []) == []
```

Note: `sqlite3.Connection` does not allow setting attributes. Use a thin proxy class in the test (`class Proxy: def __init__(self, c): self._c = c; def __getattr__(self, n): return getattr(self._c, n)` plus the flaky `execute`), or subclass `sqlite3.Connection` in a helper. Pick the proxy.

- [ ] **Step 2: Run** → FAIL (`no such column: source_url`, `unexpected keyword source_url`, `module 'scribe.jobs' has no attribute 'enqueue_many'`).

- [ ] **Step 3: Implement**

`scribe/db.py`: append to `_MIGRATIONS` (and bump `SCHEMA_VERSION` to 10), with a comment in the style of v3/v9:

```python
_SCHEMA_V10 = """
-- Where a recording came from, when it came over the network (Phase 7, the
-- feed import). Both nullable: an upload, a path, a recording and a watch
-- folder know no source, and rows from before v10 have none either.
-- `source_url` is the fetched URL without its fragment; `source_id` is
-- yt-dlp's extractor-scoped id (`Youtube:<id>`, `Generic:<guid>`). Read only
-- by the dialog's listing, to say "in library"; never rendered as a link.
-- No index: measured 2026-09-08, the lookup costs 4-11 ms at 1k-10k rows
-- against a probe that takes seconds.
ALTER TABLE media ADD COLUMN source_url TEXT;
ALTER TABLE media ADD COLUMN source_id TEXT;
"""
```

`scribe/media.py`:

```python
def ingest_path(conn, src, *, title=None, folder_id=None, link=True, source_url=None, source_id=None) -> dict:
    src = Path(src)
    sha256 = hash_file(src)
    existing = _existing_row(conn, sha256)
    if existing is not None:
        return _fill_source(conn, existing, source_url, source_id)
    ...
    return _insert_media(conn, ..., source_url=source_url, source_id=source_id)


def _fill_source(conn, row: dict, source_url, source_id) -> dict:
    """A known recording gains the provenance it lacked; a set one stands.

    Title, folder and the privacy pin stay as the first arrival chose them,
    and so does a source that is already there. But a row that arrived
    through an upload, a watch folder or a version before v10 has NULL here,
    and NULL is no provenance: without this it would never be marked "in
    library", however often the feed re-imported it. The `IS NULL` guard is
    in the SQL because two fan-out children can dedupe the same bytes at
    once; the second one's update matches no row, which is the right answer.
    """
    updates = {}
    if source_url and not row.get("source_url"):
        updates["source_url"] = source_url
    if source_id and not row.get("source_id"):
        updates["source_id"] = source_id
    if not updates:
        return row
    with db.LOCK:
        for column, value in updates.items():
            conn.execute(f"UPDATE media SET {column}=? WHERE id=? AND {column} IS NULL", (value, row["id"]))
        conn.commit()
        fresh = conn.execute("SELECT source_url, source_id FROM media WHERE id=?", (row["id"],)).fetchone()
    return {**row, "source_url": fresh["source_url"], "source_id": fresh["source_id"]}
```

`_insert_media` gains the two keyword parameters and writes them in the INSERT. `ingest_stream` passes nothing (its callers know no source).

`scribe/jobs.py`:

```python
def enqueue_many(conn, type_, params_list, priority: int = 0) -> list[int]:
    """Insert N queued jobs in one transaction: all of them, or none.

    A feed import queues one job per ticked episode, and five hundred single
    `enqueue` calls are five hundred commits and five hundred log lines on the
    event loop (measured 2026-09-08: ~917 ms against ~17 ms for one
    transaction). A failure halfway through single calls would also leave
    half the episodes queued with no answer to the browser. One log line
    carries the count and the first and last id, which are contiguous under
    one write transaction.
    """
    params_list = list(params_list)
    if not params_list:
        return []
    ids: list[int] = []
    with db.LOCK:
        try:
            for params in params_list:
                cur = conn.execute(
                    "INSERT INTO job(type, media_id, params_json, priority, retry_of, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (type_, None, json.dumps(params), priority, None, time.time()),
                )
                ids.append(cur.lastrowid)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    applog.log("job.enqueued_many", type=type_, count=len(ids), first=ids[0], last=ids[-1])
    return ids
```

- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest tests/test_db.py tests/test_media.py tests/test_jobs.py -q` → PASS.

- [ ] **Step 5: Commit** `feat(media): a recording remembers where it came from; jobs can be queued in one transaction`

---

### Task 3: `url_stage` - the entry's name travels with the job, and register writes provenance

**Files:**
- Modify: `scribe/stages/url_stage.py` (`_fan_out`, `fetch`, `register`, new `ENTRY_KEY = "entry"`, `SOURCE_KEY = "source"`, `_effective_title`, `_source_id`)
- Test: `tests/test_ingest_urls.py`

**Interfaces:**
- Consumes: Task 1's `urls.download(title_hint=)`, `entry["source_id"]`; Task 2's `media.ingest_path(source_url=, source_id=)`.
- Produces: child params `{**parent, "url": entry["url"], "from_playlist": True, "entry": {"title": ..., "source_id": ...}, "source": {"url": parent["url"], "title": playlist.title}}`; media rows with `source_url` (defragged) and `source_id`.

- [ ] **Step 1: Failing tests** (in `tests/test_ingest_urls.py`, using `url_job`, `make_ctx`, `run_stages`, `feed_info`):

```python
def test_a_fan_out_child_carries_the_entry_s_title_and_the_feed_it_came_from(conn, data_dir, monkeypatch):
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(feed_info())))
    job_id = url_job(conn, FEED_URL, options={"model": "large-v3"})
    run_stages(make_ctx(conn, job_id))
    children = [json.loads(r["params_json"]) for r in conn.execute(
        "SELECT params_json FROM job WHERE type=? AND id<>? ORDER BY id", (url_stage.JOB_TYPE, job_id))]
    assert len(children) == 4
    assert children[1]["entry"] == {"title": "Lecture 1", "source_id": "Generic:guid-1"}
    assert children[1]["source"] == {"url": FEED_URL, "title": "The Hitchhiker Lectures"}
    assert children[1]["url"].startswith("https://cdn.test/ep/default.mp3?d=1&e=1#__youtubedl_smuggle=")
    assert children[1]["from_playlist"] is True and children[1]["options"] == {"model": "large-v3"}


def test_register_names_a_feed_episode_after_the_feed_not_the_cdn(conn, data_dir, monkeypatch):
    """A bare enclosure probes as its CDN filename (measured: default.mp3_ywr3…);
    the feed knew the episode's name and the child carries it."""
    fake = FakeYdl(single_info(title="default.mp3_ywr3ahjkcgo_6c70", uploader="", extractor_key="Generic", id="guid-1"),
                   files=("download.mp3", "download.info.json"))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, "https://cdn.test/ep/default.mp3?d=1&e=1#__youtubedl_smuggle=x",
                     from_playlist=True, options={"model": "large-v3"},
                     entry={"title": "Lecture 1", "source_id": "Generic:guid-1"},
                     source={"url": FEED_URL, "title": "The Hitchhiker Lectures"})
    run_stages(make_ctx(conn, job_id))
    row = conn.execute("SELECT * FROM media").fetchone()
    assert row["title"] == "Lecture 1"
    assert row["orig_name"] == "Lecture 1.mp3"
    assert row["source_url"] == "https://cdn.test/ep/default.mp3?d=1&e=1"   # no fragment, ever
    assert row["source_id"] == "Generic:guid-1"
    queued = conn.execute("SELECT params_json FROM job WHERE type='transcribe'").fetchone()
    assert json.loads(queued["params_json"])["extra_hotwords"] == ["Lecture", "The", "Hitchhiker", "Lectures"]


def test_register_takes_the_download_s_extractor_id_when_the_entry_has_none(conn, data_dir, monkeypatch):
    fake = FakeYdl(single_info(extractor_key="Youtube"), files=("download.m4a",))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, "https://youtu.be/abc123#t=42")
    run_stages(make_ctx(conn, job_id))
    row = conn.execute("SELECT source_url, source_id FROM media").fetchone()
    assert row["source_url"] == "https://youtu.be/abc123"
    assert row["source_id"] == "Youtube:abc123"


def test_register_stores_no_id_for_a_generic_download_without_an_entry(conn, data_dir, monkeypatch):
    """The generic extractor's id for a bare file is its filename, which would
    mark the wrong episode; a typed mp3 link is matched by URL only."""
    fake = FakeYdl(single_info(extractor_key="Generic", id="default"), files=("download.mp3",))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, "https://cdn.test/some.mp3")
    run_stages(make_ctx(conn, job_id))
    assert conn.execute("SELECT source_id FROM media").fetchone()["source_id"] is None
```

Check the hotword order in the second test against `hotword_terms`'s rule (capitalised tokens, first occurrence): title "Lecture 1" → `Lecture`; uploader fallback "The Hitchhiker Lectures" → `The`, `Hitchhiker`, `Lectures`. Adjust the expected list if `_WORD` splits differently, and say so in the test.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** in `scribe/stages/url_stage.py`:

```python
from urllib.parse import urldefrag

ENTRY_KEY = "entry"
"""What the listing knew about this one item: ``{"title", "source_id"}``.
Set by `_fan_out` and by the dialog's per-episode jobs; absent on a typed link."""

SOURCE_KEY = "source"
"""The feed or channel an entry came from: ``{"url", "title"}``. The URL is
the one the user pasted and `add_url` checked - never `playlist.webpage_url`,
which is a stranger's - so Fetch-all and Import-selected write the same
source for the same feed."""


def fetch(ctx):
    ...
    entry = ctx.params.get(ENTRY_KEY) or {}
    ctx.state["downloaded"] = urls.download(url, paths.job_work_dir(ctx.job["id"]),
                                            on_progress=ctx.report, cookies_file=cookies_file,
                                            title_hint=str(entry.get("title") or ""))


def register(ctx):
    ...
    downloaded = ctx.state["downloaded"]
    entry = ctx.params.get(ENTRY_KEY) or {}
    source = ctx.params.get(SOURCE_KEY) or {}
    title = str(entry.get("title") or "").strip() or downloaded.title
    url = _url(ctx)
    row = media.ingest_path(ctx.conn, downloaded.path, title=title or None, folder_id=...,
                            source_url=urldefrag(url).url, source_id=_source_id(entry, downloaded.info))
    uploader = downloaded.uploader or str(source.get("title") or "")
    terms = urls.hotword_terms({**downloaded.info, "title": title, "uploader": uploader})
    ...


def _source_id(entry: dict, info: dict) -> str | None:
    """The listing's id when it had one; else the download's, unless it is
    the generic extractor's, whose id for a bare file is the filename."""
    listed = entry.get("source_id")
    if listed:
        return str(listed)
    key, ident = info.get("extractor_key"), info.get("id")
    if key and ident and str(key) != "Generic":
        return f"{key}:{ident}"
    return None


def _fan_out(ctx, playlist):
    ...
    parent_url = _url(ctx)
    queued = [jobs.enqueue(ctx.conn, JOB_TYPE, params={
        **ctx.params, "url": entry["url"], "from_playlist": True,
        ENTRY_KEY: {"title": entry.get("title") or "", "source_id": entry.get("source_id")},
        SOURCE_KEY: {"url": parent_url, "title": playlist.title},
    }) for entry in playlist.entries]
```

`hotword_terms` reads `_uploader(info)` which checks `uploader`, `channel`, `creator`, `uploader_id` in that order - passing `uploader` overrides only when the download had none of those; check the plain-video pin (`test_a_single_video_is_ingested_and_queued_for_transcription`) stays byte-identical: it has an uploader, so nothing changes.

- [ ] **Step 4: Run** `tests/test_ingest_urls.py` → PASS.
- [ ] **Step 5: Commit** `feat(ingest): a feed episode keeps its name and its provenance through the download job`

---

### Task 4: The jobs board names an `ingest_url` job after its episode

**Files:**
- Modify: `scribe/web/jobs_ui.py` (`job_view`; new `_job_title(row, params)`)
- Test: `tests/test_web_jobs.py`

- [ ] **Step 1: Failing tests** (after the doctor-job test; `jobs` and `_section` are already imported there):

```python
def test_an_ingest_url_job_is_named_by_its_episode_from_queue_to_history(client, conn):
    job_id = jobs.enqueue(conn, "ingest_url", params={
        "url": "https://cdn.example/default.mp3", "from_playlist": True,
        "entry": {"title": "Love in the time of Palantir", "source_id": None},
        "source": {"url": "https://feeds.npr.org/510289/podcast.xml", "title": "Planet Money"}})
    bare = jobs.enqueue(conn, "ingest_url", params={"url": "https://youtu.be/dQw4w9WgXcQ"})
    queued = _section(client.get("/jobs").text, "queued")
    assert f'href="/jobs/{job_id}"' in queued and ">Love in the time of Palantir<" in queued
    assert f'href="/jobs/{bare}"' in queued and ">https://youtu.be/dQw4w9WgXcQ<" in queued
    assert "ingest_url job" not in queued
    jobs.claim_next(conn)
    jobs.finish(conn, job_id, "failed", error_code="UNAVAILABLE", error_detail="HTTP Error 403")
    history = _section(client.get("/jobs").text, "history")
    assert ">Love in the time of Palantir<" in history and "UNAVAILABLE" in history
    assert ">Love in the time of Palantir<" in client.get(f"/jobs/{job_id}").text


def test_an_episode_title_that_is_markup_is_escaped_on_the_board(client, conn):
    jobs.enqueue(conn, "ingest_url", params={"url": "https://cdn.example/x.mp3", "entry": {"title": "<b>Zaphod</b>"}})
    body = client.get("/jobs").text
    assert "&lt;b&gt;Zaphod&lt;/b&gt;" in body and "<b>Zaphod</b>" not in body
```

Check `jobs.finish`'s signature in `scribe/jobs.py:92` before writing the call (status positional or keyword; `error_code`, `error_detail` names).

- [ ] **Step 2: Run** → FAIL (`>ingest_url job<`).
- [ ] **Step 3: Implement**:

```python
def _job_title(row: dict, params: dict) -> str:
    """What the board calls a job. A transcribe job is its recording; an
    ingest_url job has no recording yet, so it is its episode's name when the
    listing gave one, else the link itself - twenty jobs from one feed were
    twenty rows called "ingest_url job". A stranger's text either way, and
    escaped like every title."""
    if row.get("media_title"):
        return str(row["media_title"])
    if row["type"] == url_stage.JOB_TYPE:
        entry = params.get(url_stage.ENTRY_KEY) or {}
        title = str(entry.get("title") or "").strip() if isinstance(entry, dict) else ""
        return title or str(params.get("url") or f"{row['type']} job")
    return f"{row['type']} job"
```

`jobs_ui` must not import `url_stage` if that pulls a stage module into the web process… check: `ingest_ui.py` already does `from scribe.stages import url_stage`, so it is fine (a stage module of constants, no model import).

- [ ] **Step 4: Run** `tests/test_web_jobs.py` → PASS.
- [ ] **Step 5: Commit** `feat(jobs-board): an ingest_url job is named after its episode`

---

### Task 5: `ingest_ui` - one listing per URL, a patient budget, the marks, and the url route's three cases

**Files:**
- Modify: `scribe/web/ingest_ui.py`
- Test: `tests/test_web_url_dialog.py` (server-side tests; markup and node tests come in Tasks 6-7)

**Interfaces:**
- Consumes: Task 1 (`urls.probe(limit=)`, `UrlInfo.total/.truncated`, entries with `source_id`), Task 2 (`jobs.enqueue_many`), Task 3 (`url_stage.ENTRY_KEY`, `SOURCE_KEY`, `MAX_FAN_OUT`).
- Produces:
  - `MAX_LISTED`, `PATIENT_TIMEOUT_SECONDS`, `FORM_FIELD_CEILING`, `MAX_ENTRY_BYTES`, `MAX_FEED_TITLE`.
  - `probe_in_slot(url) -> UrlInfo` (coalesced), `probe_or_none(url, *, patient=False)`.
  - `known_sources(conn, entries) -> list[str | None]`.
  - `parse_entry(raw: str, index: int) -> dict` → `{"url", "title", "source_id"}`; raises `HTTPException(400)`.
  - `preview_view(info, *, url, states, cap) -> dict` with `entries: [{title, duration, timestamp, state, value}]`, `truncated`, `total`, `feed_url`, `feed_title`, `cap`, `count` (len entries), plus the existing keys.
  - `add_url` three cases; `EPISODES_FLASH` text.
  - the panel context for the error branch gains `retry: bool` (the slow hint on a non-patient request).

- [ ] **Step 1: Failing tests** (helpers first, in `tests/test_web_url_dialog.py`; `feed_info`, `FEED_URL` imported from `test_ingest_urls`):

```python
from test_ingest_urls import FakeYdl, build_returning, playlist_info, single_info, feed_info, FEED_URL
from scribe import jobs, media

def _entry(url, title="Episode", source_id=None) -> str:
    return json.dumps({"url": url, "title": title, "source_id": source_id})

def _entries(n: int) -> list[str]:
    return [_entry(f"https://example.test/ep{i}.mp3", f"Episode {i}", f"Generic:g{i}") for i in range(n)]

def _post_episodes(client, entries, feed_url=FEED_URL, feed_title="The Hitchhiker Lectures", **extra):
    data = {"url": feed_url, "feed_url": feed_url, "feed_title": feed_title, "entry": entries, **extra}
    return client.post("/transcribe/url", data=data, headers=HX)
```

The tests (names are the contract; bodies follow the file's idiom of `_jobs(conn)` and `_params(job)`):

1. `test_the_playlist_preview_lists_one_row_per_entry_with_a_value_that_round_trips` - `_probing(monkeypatch, FakeYdl(feed_info()))`, `_preview(client, FEED_URL)`; regex every `<input type="checkbox" name="entry" value="…">`, `html.unescape` the value, `json.loads` it; assert 4 rows, `[v["title"] for v in values][0] == 'Trump drinks Venezuela\'s milkshake & "more" <3 café'`, `values[0]["source_id"] == "Generic:guid-0"`, `values[0]["url"].startswith("https://cdn.test/ep/default.mp3?d=1&e=0#")`; `"4 episodes"` in body; `'name="feed_url" value="https://feeds.test/podcast.xml"'` and `'name="feed_title"'` present.
2. `test_the_preview_of_a_playlist_counts_the_entries` - the existing test renamed: `"3 episodes"` in body, `"all 3"` **not** in body, and no `[data-url-submit]`-hiding assertion here (that is CSS).
3. `test_the_preview_marks_what_is_in_the_library_in_the_trash_and_queued` - seed with `data_dir`: `media.ingest_path(conn, file_a, source_url="https://example.test/watch?v=vid0")`; `media.ingest_path(conn, file_b, source_id="Youtube:vid1")` then `UPDATE media SET trashed_at=? WHERE id=?`; `jobs.enqueue(conn, "ingest_url", params={"url": "https://example.test/watch?v=vid2#frag"})`; probe `playlist_info(4)` with `ie_key="Youtube"` on entry 1; assert the rows carry `in library`, `in trash`, `queued`, and the fourth carries none (regex per `<li>`).
4. `test_a_queued_job_wins_over_a_library_row_for_the_same_url` - both seeded for vid0; assert `queued`.
5. `test_known_sources_marks_queued_and_running_jobs_but_no_terminal_one` - direct call: six `ingest_url` jobs, one per status (claim first for running; `jobs.finish` for the terminal four), plus one whose url is `FEED_URL` itself; entries built as `{"url": u, "source_id": None}`; assert `["queued", "queued", None, None, None, None, None]` in the entries' order.
6. `test_the_header_says_the_first_n_when_the_listing_was_cut` - `playlist_info(3)` with `playlist_count=7`, `monkeypatch.setattr(ingest_ui, "MAX_LISTED", 3)`; assert `"the first 3 of 7 episodes"`; without `playlist_count` assert `"the first 3 episodes"`; and `fake.opts["playlistend"] == 3`.
7. `test_the_header_names_the_cap` - `"at most 500 per import"` (read `url_stage.MAX_FAN_OUT` in the assertion).
8. `test_an_episode_row_shows_its_date_and_duration_and_copes_without_either` - entries with `timestamp=calendar.timegm((2026, 9, 4, 12, 0, 0))` and `duration=1527.0`; timestamp only; neither. Assert `"2026-09-04"` and `"25:27"`; for the second no dangling `" · "` right after the date; for the third the title is on the row and neither `"None"` nor `"1970"` appears in that row.
9. `test_a_slow_preview_offers_to_list_the_episodes_anyway` - `BlockingYdl`, `PREVIEW_TIMEOUT_SECONDS` 0.2; assert `HINT_SLOW` and a `<button type="button" … hx-post="/transcribe/url/preview" … hx-params="url,patient" … hx-vals=… patient …>` (regex the button whose `hx-post` is the preview; assert `type="button"`, `hx-target="#url-preview"`, `hx-swap="outerHTML"`, `hx-sync=`).
10. `test_a_patient_preview_waits_past_the_short_budget` - `PREVIEW` 0.2, `PATIENT` 3.0, one `BlockingYdl(release)`: first `_preview` → "took too long"; then `threading.Timer(0.6, release.set).start()`, `t0 = time.monotonic()`, post `{"url": …, "patient": "1"}`; assert the title in the body, no "took too long", `0.2 < elapsed < 3.0`. Note: with coalescing the second request is a *follower* of the first thread; `BlockingYdl` must count `build_ydl` calls (`calls == 1`).
11. `test_a_patient_preview_that_still_times_out_offers_no_second_retry` - `PATIENT` 0.2; assert `HINT_SLOW` and no `patient` control.
12. `test_a_patient_preview_attaches_to_the_listing_already_running` - the counting version of 10: assert `fake_builds == 1` after both requests.
13. `test_a_follower_holds_no_slot` - hold `PREVIEW_WORKERS - 1` slots, start a leader (blocking), then a follower for the same URL (patient, `PATIENT` 0.2 → times out) - it must not answer `HINT_BUSY`, while a probe for a *different* URL does.
14. Parametrise `test_the_preview_refuses_rather_than_starting_an_unbounded_number_of_probes` over `patient in (None, "1")`.
15. Add the patient post to `test_a_page_on_another_site_cannot_make_this_app_fetch_a_url`.
16. `test_ticked_entries_become_one_job_each_in_posted_order` - `_post_episodes(client, _entries(3))`; assert 200; jobs are 3 × `ingest_url` with `params["url"]` in posted order; each has `options`, `from_playlist is True`, `entry == {"title": "Episode i", "source_id": "Generic:gi"}`, `source == {"url": FEED_URL, "title": "The Hitchhiker Lectures"}`; none has `url == FEED_URL`.
17. `test_exactly_the_cap_s_worth_are_all_queued_and_one_more_is_refused_with_both_numbers` - `MAX_FAN_OUT` patched to 3.
18. `test_a_whole_listing_posted_at_once_is_refused_by_this_route_not_by_starlette` - `_post_episodes(client, _entries(ingest_ui.MAX_LISTED))`; assert 400, `"at most"` in detail, `"Too many fields"` not in detail, no jobs.
19. `test_one_bad_entry_among_good_ones_queues_none_of_them` - parametrised over `["not json", "[1]", '"str"', "null", "{}", '{"url": "file:///etc/passwd"}', '{"url": "ytsearch:bread"}', '{"url": "javascript:alert(1)"}', '{"url": 5}', '{"url": "https://x.test/a", "title": NaN}'` (the last as a raw string), `"x" * 5000`, a 100 KB nested `"[" * 50000 + "]" * 50000`]; post `[good, bad, good]`; assert 400, `"episode 2"` in detail, `_jobs(conn) == []`.
20. `test_an_entry_title_is_cut_at_the_library_s_name_limit` - 400-char title stored at `library.MAX_NAME`.
21. `test_a_feed_url_that_is_not_http_is_refused_and_queues_nothing`.
22. `test_a_list_with_nothing_ticked_is_refused_and_queues_nothing` - `feed_url` present, no `entry`; assert 400, `"tick at least one"`, no jobs.
23. `test_a_post_with_neither_still_queues_one_job_on_the_link` - (the existing pin covers it; add an explicit assertion that `set(_params(job)) == {"url", "folder_id", "options"}` - already in the existing test; just make sure the test name in §7 exists).
24. `test_the_cookies_file_travels_to_every_episode_job` - with `roots`.
25. `test_the_flash_names_the_feed_and_the_count` - `"Fetching 3 episodes of The Hitchhiker Lectures"` in the response.
26. `test_a_javascript_source_url_on_a_job_is_text_on_the_details_page_never_an_href` - `jobs.enqueue` with `source.url = "javascript:alert(1)"`, GET `/jobs/{id}` (or the details fragment); assert `'href="javascript:'` not in body and `"javascript:alert(1)"` (escaped or not) is present as text.
27. `test_no_template_binds_an_href_to_a_stranger_s_url` - glob templates; regex `href="\{\{[^}]*(source_url|webpage_url|feed_url|params\.url|source\.url|entry\.url)`; assert none; guard: at least one template mentions `feed_url`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `scribe/web/ingest_ui.py`. Key pieces:

```python
import json, math, threading
from concurrent.futures import Future
from urllib.parse import urldefrag

MAX_LISTED = 2500
PATIENT_TIMEOUT_SECONDS = 60.0
FORM_FIELD_CEILING = MAX_LISTED + 64
MAX_ENTRY_BYTES = 4096
MAX_FEED_TITLE = 120
EPISODES_FLASH = "Fetching {count} episode{s} of {feed}. Each is a job of its own; the transcription is queued behind each download."
HINT_NONE_TICKED = "tick at least one episode - All shown takes everything the filter left"

_inflight: dict[str, Future] = {}
_inflight_lock = threading.Lock()


def probe_in_slot(url: str) -> urls.UrlInfo:
    with _inflight_lock:
        future = _inflight.get(url)
        if future is not None:
            leader = False
        else:
            if not _preview_slots.acquire(blocking=False):
                raise PreviewBusy(HINT_BUSY)
            future = Future()
            _inflight[url] = future
            leader = True
    if not leader:
        return future.result()          # the leader's answer, or its UrlError
    try:
        info = urls.probe(url, limit=MAX_LISTED)
    except BaseException as exc:
        future.set_exception(exc)
        raise
    else:
        future.set_result(info)
        return info
    finally:
        with _inflight_lock:
            _inflight.pop(url, None)
        _preview_slots.release()


async def probe_or_none(url: str, *, patient: bool = False) -> urls.UrlInfo | None:
    budget = PATIENT_TIMEOUT_SECONDS if patient else PREVIEW_TIMEOUT_SECONDS   # read at call time: tests patch both
    with move_on_after(budget):
        return await to_thread.run_sync(probe_in_slot, url, abandon_on_cancel=True)
    return None
```

Watch one subtlety in `probe_in_slot`: `future.set_exception(exc)` inside `except BaseException` then `raise` - fine; but a follower calling `future.result()` on a `BaseException` such as `KeyboardInterrupt` re-raises it in the follower; acceptable.

```python
def _refuse_constant(name):
    raise ValueError(f"{name} is not a number this app accepts")


def parse_entry(raw: str, index: int) -> dict:
    """One ticked episode, as the browser echoed it back: checked, not trusted."""
    def bad(why: str):
        return HTTPException(status_code=400, detail=f"episode {index} is not something this app can read ({why})")
    if not isinstance(raw, str) or len(raw) > MAX_ENTRY_BYTES:
        raise bad("too long")
    try:
        obj = json.loads(raw, parse_constant=_refuse_constant)
    except (ValueError, TypeError, RecursionError):
        raise bad("not JSON") from None
    if not isinstance(obj, dict):
        raise bad("not an object")
    url = obj.get("url")
    if not isinstance(url, str):
        raise bad("no url")
    url = _web_url(url)   # 400 with the scheme sentence
    title = str(obj.get("title") or "")[: library.MAX_NAME]
    source_id = obj.get("source_id")
    source_id = str(source_id) if isinstance(source_id, str) and source_id else None
    return {"url": url, "title": title, "source_id": source_id}


def known_sources(conn, entries: list[dict]) -> list[str | None]:
    urls_ = [urldefrag(str(e.get("url") or "")).url for e in entries]
    ids = [e.get("source_id") for e in entries]
    url_set = {u for u in urls_ if u}
    id_set = {i for i in ids if i}
    by_url: dict[str, str] = {}
    by_id: dict[str, str] = {}
    with db.LOCK:
        if url_set or id_set:
            u_list, i_list = sorted(url_set), sorted(id_set)
            sql = ("SELECT source_url, source_id, trashed_at FROM media WHERE "
                   + " OR ".join(filter(None, [
                       f"source_url IN ({','.join('?' * len(u_list))})" if u_list else "",
                       f"source_id IN ({','.join('?' * len(i_list))})" if i_list else ""])))
            for row in conn.execute(sql, [*u_list, *i_list]):
                state = "library" if row["trashed_at"] is None else "trash"
                for key, table in ((row["source_url"], by_url), (row["source_id"], by_id)):
                    if key and (table.get(key) != "library"):
                        table[key] = state
        live = conn.execute("SELECT params_json FROM job WHERE type=? AND status IN ('queued','running')",
                            (url_stage.JOB_TYPE,)).fetchall()
    queued_urls, queued_ids = set(), set()
    for row in live:
        params = _job_params(row["params_json"])
        queued_urls.add(urldefrag(str(params.get("url") or "")).url)
        entry = params.get(url_stage.ENTRY_KEY) or {}
        if isinstance(entry, dict) and entry.get("source_id"):
            queued_ids.add(str(entry["source_id"]))
    out = []
    for u, i in zip(urls_, ids):
        if (u and u in queued_urls) or (i and i in queued_ids):
            out.append("queued")
        else:
            states = {by_url.get(u), by_id.get(i)} - {None}
            out.append("library" if "library" in states else ("trash" if "trash" in states else None))
    return out
```

`add_url`:

```python
@router.post("/transcribe/url", include_in_schema=False)
async def add_url(request: Request) -> Response:
    conn = request.app.state.conn
    form = await request.form(max_fields=FORM_FIELD_CEILING)
    fields = _fields(form)
    raw_entries = [v for v in form.getlist("entry") if isinstance(v, str)]
    feed_url_given = bool((fields.get("feed_url") or "").strip())

    if raw_entries:
        return await _add_episodes(request, conn, form, fields, raw_entries)
    if feed_url_given:
        raise HTTPException(status_code=400, detail=HINT_NONE_TICKED)
    ... today's body unchanged ...


async def _add_episodes(request, conn, form, fields, raw_entries) -> Response:
    cap = url_stage.MAX_FAN_OUT
    if len(raw_entries) > cap:
        raise HTTPException(status_code=400, detail=f"{len(raw_entries)} episodes ticked, and this app queues at most {cap} in one go; tick fewer")
    entries = [parse_entry(raw, i + 1) for i, raw in enumerate(raw_entries)]
    feed_url = _web_url(fields.get("feed_url"))
    feed_title = str(fields.get("feed_title") or "").strip()[:MAX_FEED_TITLE]
    options = parse_options(fields)
    folder_id = library._folder_id_from(conn, fields.get("folder_id"))
    cookies = _cookies_file(conn, fields.get("cookies_file"))
    params_list = []
    for entry in entries:
        params = {"url": entry["url"], "folder_id": folder_id, url_stage.OPTIONS_KEY: options.to_params(),
                  "from_playlist": True,
                  url_stage.ENTRY_KEY: {"title": entry["title"], "source_id": entry["source_id"]},
                  url_stage.SOURCE_KEY: {"url": feed_url, "title": feed_title}}
        if cookies:
            params["cookies_file"] = cookies
        params_list.append(params)
    await run_in_threadpool(jobs.enqueue_many, conn, url_stage.JOB_TYPE, params_list)
    save_defaults(conn, options)
    flash = EPISODES_FLASH.format(count=len(entries), s="" if len(entries) == 1 else "s", feed=feed_title or "the feed")
    response = library._after_change(request, conn, flash=flash)
    response.headers["HX-Trigger"] = "jobs-changed"
    return response
```

`url_preview`: read `patient = bool((fields.get("patient") or "").strip())`; on a playlist compute `states = known_sources(conn, info.entries)` under the threadpool (`run_in_threadpool`) and render `preview_view(info, url=text, states=states, cap=url_stage.MAX_FAN_OUT)`; the error branch gets `retry = (message == HINT_SLOW and not patient)`.

`preview_view` builds each entry's `value` with `json.dumps({"url": e["url"], "title": e["title"], "source_id": e["source_id"]})`.

Also fix the module docstring's "SOCKET_TIMEOUT ends it shortly after" per spec §6 and document the coalescing and the three cases.

- [ ] **Step 4: Run** `tests/test_web_url_dialog.py -q -k "not node"` (the Node tests come later) → PASS; then `tests/test_ingest_urls.py` again.
- [ ] **Step 5: Commit** `feat(web): the link route imports ticked episodes, and a listing is shared, patient and marked`

---

### Task 6: The panel and the dialog - list, retry, trigger, CSS

**Files:**
- Modify: `scribe/templates/_url_panel.html`, `scribe/templates/transcribe_dialog.html`, `scribe/static/app.css`
- Test: `tests/test_web_url_dialog.py` (markup tests)

- [ ] **Step 1: Failing markup tests**

```python
def test_the_link_field_previews_on_input_never_on_change(client):
    body = client.get("/transcribe", headers=HX).text
    tag = re.search(r'<input[^>]*name="url"[^>]*>', body).group(0)
    trigger = re.search(r'hx-trigger="([^"]*)"', tag).group(1)
    specs = [s.strip().split()[0] for s in trigger.split(",")]
    assert "change" not in specs and "input" in specs
    assert 'hx-sync="closest [data-panel]:replace"' in tag


def test_the_import_button_is_the_row_button_s_twin(client, monkeypatch):
    _probing(monkeypatch, FakeYdl(playlist_info(3)))
    body = _preview(client, "https://example.test/playlist?list=PL42").text
    tag = re.search(r'<button[^>]*data-episodes-submit[^>]*>', body).group(0)
    assert 'type="submit"' in tag and 'formaction="/transcribe/url"' in tag
    assert 'hx-post="/transcribe/url"' in tag and 'hx-params="not files"' in tag
    for name in ("data-episodes-all", "data-episodes-none"):
        other = re.search(rf'<button[^>]*{name}[^>]*>', body).group(0)
        assert 'type="button"' in other
    filter_tag = re.search(r'<input[^>]*data-episode-filter[^>]*>', body).group(0)
    assert " name=" not in filter_tag
    assert 'data-max="500"' in body  # url_stage.MAX_FAN_OUT
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**

`transcribe_dialog.html` line 80-82: `hx-trigger="input changed delay:800ms" hx-sync="closest [data-panel]:replace"`, and a paragraph in the header comment explaining why (spec §3.6).

`_url_panel.html` (whole file; the header comment lists the context and the every-button-is-type-button rule):

```jinja
<div id="url-preview" class="url-preview">
  {% if preview.state == 'ok' %}
  {% if preview.kind == 'playlist' %}
  <div class="episodes-head">
    <p class="url-title"><strong>{{ preview.title or 'Untitled' }}</strong>{% if preview.uploader %} <span class="muted">· {{ preview.uploader }}</span>{% endif %}</p>
    <p class="muted">
      {% if preview.truncated %}the first {{ preview.count }}{% if preview.total %} of {{ preview.total }}{% endif %} episodes{% else %}{{ preview.count }} episode{{ '' if preview.count == 1 else 's' }}{% endif %}
      · at most {{ preview.cap }} per import
    </p>
  </div>
  <div class="episodes-tools" data-episodes>
    <input type="search" data-episode-filter placeholder="filter by title or date…" aria-label="Filter episodes" autocomplete="off">
    <button type="button" data-episodes-all>All shown</button>
    <button type="button" data-episodes-none>None</button>
    <span class="muted" data-episodes-count data-max="{{ preview.cap }}">0 selected</span>
  </div>
  <ul class="episodes">
    {% for e in preview.entries %}
    <li><label class="episode" data-search="{{ (e.title ~ ' ' ~ (e.timestamp|localtime('%Y-%m-%d') if e.timestamp else ''))|trim|lower }}">
      <input type="checkbox" name="entry" value="{{ e.value }}">
      <span class="ep-title">{{ e.title or 'Untitled' }}</span>
      <span class="ep-meta muted">{% if e.timestamp %}{{ e.timestamp|localtime('%Y-%m-%d') }}{% endif %}{% if e.timestamp and e.duration %} · {% endif %}{% if e.duration %}{{ e.duration|clock }}{% endif %}</span>
      {% if e.state == 'library' %}<span class="ep-state">in library</span>{% elif e.state == 'trash' %}<span class="ep-state">in trash</span>{% elif e.state == 'queued' %}<span class="ep-state">queued</span>{% endif %}
    </label></li>
    {% endfor %}
  </ul>
  <input type="hidden" name="feed_url" value="{{ preview.feed_url }}">
  <input type="hidden" name="feed_title" value="{{ preview.feed_title }}">
  <p class="episodes-foot">
    <button type="submit" data-episodes-submit formaction="/transcribe/url" hx-post="/transcribe/url" hx-params="not files">Import selected</button>
  </p>
  {% else %}
  … the single-link branch as today …
  {% endif %}
  {% elif preview.state == 'error' %}
  <p class="url-bad" role="alert">{{ preview.message }}</p>
  {% if preview.retry %}
  <p><button type="button" hx-post="/transcribe/url/preview" hx-params="url,patient" hx-vals='{"patient": "1"}' hx-target="#url-preview" hx-swap="outerHTML" hx-disabled-elt="this" hx-sync="closest [data-panel]:replace" hx-indicator="#url-listing">List the episodes anyway</button>
     <span id="url-listing" class="spinner">Listing… this can take a minute</span></p>
  {% endif %}
  {% else %}
  <p class="muted">{{ preview.message }}</p>
  {% endif %}
</div>
```

`localtime` takes `(epoch, fmt)`, so `e.timestamp|localtime('%Y-%m-%d')` works. Check `.spinner`'s `htmx-request` rule in app.css (line ~297) and reuse it.

`app.css`:

```css
/* The list a feed or channel turns into. The row button hides while a list
   is on screen: one import action, and it honours the ticks. */
[data-panel="url"]:has(.episodes) [data-url-submit] { display: none; }
.episodes-head { margin-bottom: .3rem; }
.episodes-tools { display: flex; gap: .4rem; align-items: center; margin: .3rem 0; }
.episodes-tools input { flex: 1; min-width: 0; }
.episodes { list-style: none; margin: 0; padding: 0; max-height: 18rem; overflow: auto; border: 1px solid var(--line); border-radius: var(--r-sm); }
.episode { display: flex; align-items: center; gap: .5rem; padding: .15rem .5rem; cursor: pointer; }
.episode:hover { background: var(--bg-soft); }
.episode .ep-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.episode .ep-meta { white-space: nowrap; font-variant-numeric: tabular-nums; }
.ep-state { font-size: .75rem; padding: 0 .35rem; border: 1px solid var(--line); border-radius: var(--r-sm); color: var(--fg-muted); white-space: nowrap; }
.episodes-foot { margin: .4rem 0 0; }
[data-episodes-count].over { color: var(--bad); }
```

- [ ] **Step 4: Run** the whole `tests/test_web_url_dialog.py` (minus node) → PASS. Render the 3-entry fragment before and after for the PR (the "3 videos"/"all 3" wording change).
- [ ] **Step 5: Commit** `feat(web): the link panel lists a feed's episodes with a filter, a cap and a patient retry`

---

### Task 7: `app.js` - filter, All shown, None, the count, the cap, a dropped link

**Files:**
- Modify: `scribe/static/app.js` (inside `wireTranscribeDialog`), `tests/test_web_url_dialog.py` (`DOM_STUB` additions, `EPISODES_FIXTURE`, node tests)

- [ ] **Step 1: Stub additions and failing node tests**

In `DOM_STUB`'s `el()`: `if (node.tagName === 'INPUT') { node.type = …; node.checked = false; }`; add `node.dispatchEvent = function (ev) { return fire(node, ev.type, ev); };` and `node.focus = function () { node.focused = (node.focused || 0) + 1; };`; and a global `function Event(type, props) { const ev = event(props); ev.type = type; return ev; }` plus `globalThis.Event = Event`.

`EPISODES_FIXTURE` (appended to `DIALOG_FIXTURE` in the tests that need it):

```javascript
    const sources = form.append(el('div', { 'data-sources': '' }));
    const srcUrl = sources.append(el('input', { type: 'radio', id: 'src-url', name: 'source_tab' }));
    const panel = form.append(el('div', { id: 'url-preview', 'data-panel': 'url' }));
    const tools = panel.append(el('div', { 'data-episodes': '' }));
    const filter = tools.append(el('input', { type: 'search', 'data-episode-filter': '' }));
    const allShown = tools.append(el('button', { type: 'button', 'data-episodes-all': '' }));
    const none = tools.append(el('button', { type: 'button', 'data-episodes-none': '' }));
    const count = tools.append(el('span', { 'data-episodes-count': '', 'data-max': '2' }));
    const list = panel.append(el('ul', { class: 'episodes' }));
    function row(search) {
      const li = list.append(el('li'));
      const label = li.append(el('label', { class: 'episode', 'data-search': search }));
      const box = label.append(el('input', { type: 'checkbox', name: 'entry', value: '{}' }));
      return { label: label, box: box };
    }
    const r1 = row('love in the time of palantir 2025-03-14');
    const r2 = row('the indicator crossover 2026-08-28');
    const r3 = row('vogon poetry slam 2026-09-01');
    const importIt = panel.append(el('button', { type: 'submit', 'data-episodes-submit': '' }));
```

Tests (`@needs_node`, each `run_dom(tmp_path, DIALOG_FIXTURE + EPISODES_FIXTURE + body)`):

- `test_the_filter_matches_the_date_so_a_year_selects_that_years_episodes`: `filter.value = '2025'; fire(document, 'input', event({target: filter})); fire(document, 'click', event({target: allShown}));` → `r1.label.hidden === false`, `r2.label.hidden === true`, `r1.box.checked === true`, `r2.box.checked === false`, `count.textContent === '1 selected'`.
- `test_all_shown_ticks_only_what_the_filter_left_and_none_clears`: filter 'o' (hits r1? "love…" yes; r2 "crossover" yes; r3 "vogon" yes → pick 'palantir' instead), All shown, None → all unchecked, count '0 selected'.
- `test_ticking_past_the_cap_disables_import_and_says_so`: tick r1, r2, r3 (`box.checked = true; fire(document,'input', event({target: box}))`), `data-max` 2 → `importIt.disabled === true`, count reads `'3 selected - at most 2 in one go'`; untick one → enabled, `'2 selected'`.
- `test_listing_the_episodes_anyway_does_not_close_the_dialog_and_import_does`: `retry = panel.append(el('button', {type: 'button'}))`; fire `htmx:afterRequest` with `elt: retry` → `closed 0`; with `elt: importIt` → `closed 1`.
- `test_a_dropped_link_opens_the_link_tab_fills_the_field_and_previews`: `fire(document, 'drop', event({target: sources, dataTransfer: {files: [], getData: function (t) { return t === 'text/uri-list' ? '# a comment\nhttps://feeds.npr.org/510289/podcast.xml' : ''; }}}))` → `defaultPrevented 1`, `srcUrl.checked === true`, `urlField.value === 'https://feeds.npr.org/510289/podcast.xml'`, an `input` listener count on `urlField` fired (register one in the fixture with `listen(urlField, 'input', …)` counting) and `urlField.focused === 1`.
- `test_a_dropped_text_that_is_not_a_link_is_ignored`: `getData` returns `'D:\\music\\x.mp3'` → field empty, `srcUrl.checked` falsy.
- `test_enter_in_the_link_field_with_a_list_present_still_presses_the_row_button`: reuse the Enter test's shape with the list fixture present → `fetchIt.clicked === 1`.

Note: `fire(document, 'input', …)` calls every `document`-level `input` listener; `event.target` must be the node the handler inspects. `dispatchEvent` in the stub fires listeners registered *on that node*, so the drop test's "preview posted" assertion counts a listener registered on `urlField`, while app.js dispatches `new Event('input', {bubbles: true})` on the field.

- [ ] **Step 2: Run** `tests/test_web_url_dialog.py -k node` → FAIL.
- [ ] **Step 3: Implement** in `wireTranscribeDialog`:

```javascript
    /*
      The episode list a feed or channel turns into (see _url_panel.html).
      Three conveniences over a form that posts correctly without them: the
      filter, All shown / None, and the live count with the cap. All three
      are delegated to document like every listener here, and every check
      below is a property (hidden, checked) or an attribute (data-search),
      never a :checked / [hidden] selector - the Node harness the tests run
      in models neither, and a stub that answers "no" to a question it did
      not understand is how a broken script gets through.
    */
    function episodeRows(panel) {
      return panel ? panel.querySelectorAll('.episode') : [];
    }
    function episodeBox(row) {
      return row.querySelector('input[type="checkbox"]');
    }
    function recount(panel) {
      var out = panel.querySelector('[data-episodes-count]');
      var submit = panel.querySelector('[data-episodes-submit]');
      if (!out) { return; }
      var ticked = 0;
      episodeRows(panel).forEach(function (row) {
        var box = episodeBox(row);
        if (box && Boolean(box.checked)) { ticked += 1; }
      });
      var max = Number(out.getAttribute('data-max')) || 0;
      var over = max > 0 && ticked > max;
      out.textContent = over ? ticked + ' selected - at most ' + max + ' in one go' : ticked + ' selected';
      if (over) { out.classList.add('over'); } else { out.classList.remove('over'); }
      if (submit) { submit.disabled = over; }
    }
    function episodesPanel(el) {
      return el && el.closest ? el.closest('[data-panel="url"]') : null;
    }
    document.addEventListener('input', function (event) {
      var el = event.target;
      if (!el || typeof el.matches !== 'function') { return; }
      if (el.matches('[data-episode-filter]')) {
        var needle = String(el.value || '').trim().toLowerCase();
        episodeRows(episodesPanel(el)).forEach(function (row) {
          var hay = String(row.getAttribute('data-search') || '');
          row.hidden = needle !== '' && hay.indexOf(needle) === -1;
        });
        recount(episodesPanel(el));
      } else if (el.matches('input[type="checkbox"][name="entry"]')) {
        recount(episodesPanel(el));
      }
    });
    document.addEventListener('click', function (event) {
      var el = event.target;
      if (!el || typeof el.closest !== 'function') { return; }
      var all = el.closest('[data-episodes-all]');
      var none = el.closest('[data-episodes-none]');
      if (!all && !none) { return; }
      var panel = episodesPanel(el);
      episodeRows(panel).forEach(function (row) {
        var box = episodeBox(row);
        if (!box) { return; }
        if (none) { box.checked = false; } else if (!row.hidden) { box.checked = true; }
      });
      recount(panel);
    });
```

And in the existing drop handling:

```javascript
    document.addEventListener('dragover', function (event) {
      var zone = dropzoneOf(event);
      var sources = event.target && event.target.closest ? event.target.closest('[data-sources]') : null;
      if (!zone && !sources) { return; }
      event.preventDefault();
      if (zone) { zone.classList.add('over'); }
    });
    document.addEventListener('drop', function (event) {
      var zone = dropzoneOf(event);
      var files = event.dataTransfer ? event.dataTransfer.files : null;
      if (zone && files && files.length) { …existing file path…; return; }
      var sources = event.target && event.target.closest ? event.target.closest('[data-sources]') : null;
      if (!sources) { return; }
      event.preventDefault();
      var link = droppedLink(event.dataTransfer);
      if (!link) { return; }
      var tab = document.getElementById('src-url');
      if (tab) { tab.checked = true; }
      var form = sources.closest('form');
      var field = form ? form.querySelector('input[name="url"]') : null;
      if (!field) { return; }
      field.value = link;
      field.dispatchEvent(new Event('input', { bubbles: true }));
      field.focus();
    });
    function droppedLink(transfer) {
      if (!transfer || typeof transfer.getData !== 'function') { return ''; }
      var text = '';
      try { text = transfer.getData('text/uri-list') || ''; } catch (err) { text = ''; }
      var lines = text.split(/\r?\n/).filter(function (l) { return l.trim() && l.trim()[0] !== '#'; });
      var candidate = lines.length ? lines[0].trim() : '';
      if (!candidate) {
        try { candidate = String(transfer.getData('text/plain') || '').trim(); } catch (err) { candidate = ''; }
      }
      return /^https?:\/\//i.test(candidate) ? candidate : '';
    }
```

Note the existing `drop` handler must be merged with this one (one listener), and the harness's `event()` gives `defaultPrevented` as a counter.

- [ ] **Step 4: Run** `tests/test_web_url_dialog.py` whole → PASS; update the DOM_STUB docstring's test count.
- [ ] **Step 5: Commit** `feat(web): filter, All shown and the cap in the episode list; a dropped link fills the field`

---

### Task 8: Docs and ADR-008

**Files:**
- Modify: `docs/adr/ADR-008-…md` (fill the Proposed template), `docs/superpowers/specs/2026-09-01-myscribe-design.md` (§9 amendment), `README.md`, `scribe/web/ingest_ui.py` docstring (if not done in Task 5)
- Run: `bin/adr-index docs/adr`, `adr_lint --strict`, `adr_readiness ADR-008`

- [ ] **Step 1:** Fill ADR-008: context (the measurements), drivers, options (yt-dlp listing in the web process + browser-held selection + provenance columns / feedparser + own downloader / a feed table and a listing job), decision, contract (Must: listing under the carve-out's three bounds and coalesced per URL; entries fetched verbatim; provenance compared, never an href; Must not: a second feed parser; a feed table without superseding; Verification: the test names), consequences, retrieval metadata (topics `ingest`, `feeds`, `yt-dlp`; aliases `feed import`, `episode list`, `channel import`; components `scribe.ingest.urls`, `scribe.web.ingest_ui`, `scribe.stages.url_stage`; symbols `probe_in_slot`, `known_sources`, `add_url`, `enqueue_many`).
- [ ] **Step 2:** Spec §9 amendment paragraph; README line under the link door ("Paste or drop a podcast feed, a YouTube channel or a playlist: the episodes are listed and you tick what to import.").
- [ ] **Step 3:** `adr-index`, `adr_lint --strict` → `verdict: ok`; `adr_readiness` for ADR-008 → note the classification.
- [ ] **Step 4:** Commit `docs: ADR-008 (proposed), the spec amendment and the README line`

---

### Task 9: Evidence and finalization

- [ ] **Step 1:** The suite in halves: `tests/test_[a-r]*.py`, then `tests/test_[s-z]*.py`; record both summary lines. Run `python -m scribe.doctor --no-gpu`.
- [ ] **Step 2:** `adr_judge` on the staged diff → `verdict: ok`.
- [ ] **Step 3:** The scripted real run per spec §7 (a Python script in the scratchpad using `httpx` against 127.0.0.1:4299 with `SCRIBE_DATA_DIR` in the scratchpad): yt-dlp version; Planet Money / Computerphile / The Daily previews (wall-clock, header line, fragment size); the local 2600-item feed trim; the `queued` mark on a `--no-supervisor` instance; restart with the supervisor; the job rows, the media row (`title`, `orig_name`, `source_url`, `source_id`), the transcribe params' hotwords, the word count; the `in library` mark; one Computerphile video the same way.
- [ ] **Step 4:** `backlog task edit TASK-021 --append-notes …` with the evidence; then `backlog instructions task-finalization` and finish the task.
- [ ] **Step 5:** Final commit; leave the branch for the user to merge.

## Self-review

- Spec coverage: §3.1 → Task 1; §3.3 → Task 2; §3.2 → Task 3; §3.5 → Task 4; §3.4 → Task 5; §3.6 → Tasks 6-7; §3.7 → Task 8; §7 evidence → Task 9; §5 rows → tests 9-11, 13, 17-22 of Task 5, the node tests of Task 7 and the `_entries` tests of Task 1.
- Type consistency: `entry` dicts carry `source_id` (Task 1) → `_fan_out` reads `entry.get("source_id")` (Task 3) → `preview_view` writes it into `value` (Task 5) → `parse_entry` reads it back (Task 5) → `register` stores it (Task 3). `known_sources(conn, entries)` takes the dicts from `UrlInfo.entries`. `probe_or_none(url, patient=)` is keyword-only.
- No placeholders: every step shows its code or names its test and assertions.
