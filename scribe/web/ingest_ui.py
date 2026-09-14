"""The doors a recording arrives through that are not a file: a link, a mic.

The transcribe dialog already has two doors - an upload and a path on this
machine - and this adds two more to the same form, so the options are chosen
once whichever way the recording arrives. What makes the URL one different is
that the web process never has the bytes: `POST /transcribe/url` writes a
single `ingest_url` job row and answers immediately. The download happens in a
runner child (ADR-001), which is what makes a slow network a progress bar
rather than a page that will not load, and a dead link a job with a reason
instead of a stack trace in a browser.

The microphone is the opposite case: the bytes arrive here and nowhere else,
five seconds at a time, and there is no file to go back to if they are
dropped. So the four `/record/…` routes are as thin as a route can be - each
one is a call into `scribe.ingest.recording`, which owns the directory, the
token's shape and the container fix. See that module's docstring for why its
`-c copy` remux runs in this process and not in a job.

**The options travel nested.** `url_stage.transcribe_params` reads
`params["options"]`, and the playlist fan-out copies the whole params dict
through to each child. Merging `to_params()` at the top level would enqueue a
job that runs, downloads, and then transcribes with none of the options the
user chose - silently, because every layer would still work.

**The preview is the one place under `scribe/web` that reaches the network,
and it is a deliberate carve-out.** ADR-001's contract forbids loading a model
in the web process, and its Enforcement block names the three model libraries;
this reads a page's metadata, which is closer to what `settings.py` already
does when it asks Ollama on loopback whether it is up. The plan grants it by
name ("runs `urls.probe` in a threadpool with a 10 s timeout") and
`ingest/urls.py` records it in its module docstring. The reason it is not a
job: a preview is a courtesy the user reads while typing, and a job would take
longer to appear on the board than the answer takes to fetch.

The carve-out is only safe because it is bounded three ways, and the first one
is the method. **The preview is a POST**, which reads oddly for something that
changes nothing and is the point: `guard.SameOriginPosts` checks non-safe
methods only, on the stated grounds that "reading changes nothing" - true of
every other GET in this app and false of this one, which opens a socket to an
address in the query string. As a GET, any page the user happened to be
visiting could aim this process at a host of the attacker's choosing and use
it as a blind request and a LAN port scanner from inside the network. As a
POST the browser's own `Sec-Fetch-Site` closes it, with no token and no login.

Second, the probe runs in a worker thread started with `abandon_on_cancel=True`
and wrapped in `move_on_after`, so at the timeout the *request* returns and the
thread is let go - it cannot hold the page open. A thread that was merely
waited on would answer with exactly the same words, ten seconds later, which
is the failure that design is about. What ends the abandoned thread is the
listing itself: yt-dlp's `SOCKET_TIMEOUT` ends one whose host has gone silent,
but a host that keeps answering - a long channel, a slow feed - keeps the
thread and its slot until the listing is complete. `MAX_LISTED` caps that
work for a paginated channel; a feed is one document and the cap does not
shorten it. What is bounded is the slot, not the seconds.

Third, `PREVIEW_WORKERS` bounds how many probes may be in flight, and the
count is kept by the probe itself rather than by the request. That is not
belt-and-braces on the timeout, it is the thing the timeout creates: anyio
releases its capacity-limiter token the moment the cancel scope closes
(`anyio/_backends/_asyncio.py`, `async with limiter` wrapped around `await
future`) while the worker thread is still sitting on the socket, so anything
that counts *requests* counts none of the abandoned ones. `_preview_slots` is
taken and given back inside the thread, so what it bounds is live probes.

**One listing per URL at a time.** A probe that outran the 10 s budget is
still listing when the user presses "List the episodes anyway", and that
press must not start a second listing of the same URL on a second slot - it
would double the load on the site and, with a typo in between, put the whole
dialog on `HINT_BUSY`. So `probe_in_slot` keeps the listings in flight by
URL: the first thread for a URL is the *leader*, takes the slot and does the
work; a later thread for the same URL is a *follower*, holds no slot, and
waits on the leader's future. The patient request therefore usually answers
within seconds of the first listing finishing, and the 60 s budget covers the
2500-entry case it was sized on rather than starting it over.

**The list, and what the url route does with it.** A feed, a channel or a
playlist is rendered as one checkbox per episode, each carrying the entry as
JSON in its value. The selection lives in the browser's form between the two
requests, not in a table: there is nothing to keep in sync and nothing to
sweep. `POST /transcribe/url` then has three cases, in this order: ticked
entries (every one checked before any job is queued, then one `ingest_url`
job each in a single transaction), a `feed_url` with nothing ticked (a 400
that says so), and neither - today's door, one job on the link as pasted.

Everything the probe returns is a stranger's text, and so is everything the
browser posts back. It goes into the template as data and is escaped by the
environment like every other string in this app; nothing here is `|safe`, and
no URL that came over the network is ever rendered as a link - autoescaping
quotes an `href` correctly and would still happily emit `javascript:`. The
same URLs do appear as *text* on a job's parameters tab, in `/api/jobs` and on
the log page, exactly as a typed link does today.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import Future
from pathlib import Path
from urllib.parse import urldefrag

from anyio import move_on_after, to_thread
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from scribe import applog, db, fsbrowse, jobs
from scribe.ingest import feeds, recording, urls
from scribe.options import parse_options
from scribe.stages import url_stage
from scribe.web import library, render
from scribe.web.transcribe_dialog import JOB_TYPE, _fields, save_defaults

router = APIRouter()

PREVIEW_TIMEOUT_SECONDS = 10.0
"""How long the dialog waits for a site to say what a link is.

Ten seconds is the plan's number and it is a user-interface budget, not a
network one: past that the field has been sitting there long enough that the
honest answer is "press the button and watch the job". yt-dlp's own
`SOCKET_TIMEOUT` (15 s) is what ends the abandoned thread afterwards."""

PREVIEW_PANEL = "_url_panel.html"

FLASH = "Fetching that link. The transcription is queued behind the download."
"""Not "queued for transcription": nothing is transcribing yet and there is no
recording in the library to point at. Two things have to happen and the first
one can fail on its own."""

HINT_EMPTY = "Paste a link and what it points at will appear here."

HINT_SLOW = (
    "That link took too long to answer. Fetching it may still work - "
    "the download is a job with its own progress and its own error."
)

HINT_BUSY = (
    "Too many links are being looked up at once. The last few are still "
    "waiting on the sites they point at; try this one again in a moment, or "
    "press Fetch and watch the job."
)

PREVIEW_WORKERS = 4
"""How many probes may be on a socket at the same time.

One person types one link at a time, so four is already generous; what it is
really for is the pile-up, where every probe that timed out is still on its
socket for as long as yt-dlp's `SOCKET_TIMEOUT` allows while the field starts
another. Unbounded that is a thread and a connection per keystroke-pause."""

_preview_slots = threading.BoundedSemaphore(PREVIEW_WORKERS)
"""Taken and given back *inside* the worker thread - see the module docstring.
A `threading` primitive rather than an anyio one because the thing being
counted outlives the request that started it."""

_inflight: dict[str, Future] = {}
"""The listings running right now, by the URL text as posted (no
normalisation: a differently spelled URL is simply a second probe). A thread
that finds its URL here becomes a follower of that future; see the module
docstring. Guarded by `_inflight_lock`, and a `threading` primitive for the
same reason as the semaphore: the entry outlives the request."""

_inflight_lock = threading.Lock()

PATIENT_TIMEOUT_SECONDS = 60.0
"""How long "List the episodes anyway" waits. Sized on the measured 40
entries a second yt-dlp pages a channel at: `MAX_LISTED` entries take about
a minute. Read at call time, never as a default argument, because the tests
patch it and `PREVIEW_TIMEOUT_SECONDS` alike."""

MAX_LISTED = 2500
"""The most entries one listing asks yt-dlp for (`playlistend`).

Two costs, and the second is the binding one. A channel pages at about 40
entries a second, so this is about a minute of listing - the patient budget.
And every entry is a row swapped into a modal dialog, walked by the filter
on every keystroke: measured 2026-09-08, 920 Computerphile rows are 450 KB
and 2500 rows of The Daily, whose enclosure URLs carry four tracking
prefixes each, are 2.4 MB. That feed's 2970 episodes are not covered, and
that is the trade; the header says "the first 2500 of 2970" so nobody
mistakes the cut for the whole."""

FORM_FIELD_CEILING = MAX_LISTED + 64
"""How many form fields the url route will read.

The panel renders at most `MAX_LISTED` `entry` checkboxes plus the dialog's
own fields. Starlette's `Request.form()` refuses more than 1000 fields with
its own sentence, for multipart and url-encoded bodies alike, so without
this "All shown, Import" on any feed over about 985 entries would never reach
this route's "at most 500"."""

MAX_ENTRY_BYTES = 4096
"""The most one posted entry value may be. A JSON entry is a few hundred
bytes; the title inside it is cut at `library.MAX_NAME` anyway."""

MAX_FEED_TITLE = 120
"""The feed's title as the flash repeats it; a feed's own title field is a
stranger's text too."""

EPISODES_FLASH = (
    "Fetching {count} episode{s} of {feed}. Each is a job of its own; "
    "the transcription is queued behind each download."
)

HINT_NONE_TICKED = (
    "tick at least one episode - All shown takes everything the filter left"
)


class PreviewBusy(RuntimeError):
    """Every probe slot is taken; nothing was fetched."""


# --- posting a URL ----------------------------------------------------------------


def _web_url(raw: str | None) -> str:
    """A checked http(s) URL, or a 400 that says what was wrong with it.

    `urls.ensure_http_url` is the check, and it is the module's own rather than
    a second one written here: yt-dlp reads `file://` through its generic
    extractor and turns a bare string into a search query, so the same rule has
    to hold for a job enqueued through this form and for one enqueued any other
    way. `UrlError` is a `RuntimeError` - unmapped it would be a 500, which is
    the wrong thing to tell someone who mistyped a link.
    """
    try:
        return urls.ensure_http_url(raw or "")
    except urls.UrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _cookies_file(conn: sqlite3.Connection, raw: str | None) -> str:
    """The cookies file to fetch with, checked the way a media path is.

    Held to `fsbrowse`'s roots because it is a path this app hands to a child
    process to read, and checked for existence here because a typo is knowable
    now: the alternative is a job that downloads nothing and reports a yt-dlp
    error about a file the user can see is there, spelled differently.

    A file rather than a browser, per `urls.py`'s Windows note:
    `--cookies-from-browser` cannot read Chrome or Edge cookies since
    App-Bound Encryption, and a door that only sometimes works is worse than
    one that says what it needs.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not fsbrowse.is_allowed(path, fsbrowse.allowed_roots(conn)):
        raise HTTPException(
            status_code=403,
            detail=f"{path} is outside the folders this app may read from; widen them under Settings",
        )
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no cookies file at {path}")
    return str(path)


def _refuse_constant(name: str):
    """`json.loads` would otherwise accept NaN and Infinity, which are not
    JSON and not numbers this app wants on a job row."""
    raise ValueError(f"{name} is not a number this app accepts")


def parse_entry(raw: str, index: int) -> dict:
    """One ticked episode, as the browser echoed it back: checked, not trusted.

    The value was written by `preview_view` as JSON, but what comes back is
    whatever the browser - or anything else on the same origin - chose to
    post. So: a size cap before parsing, a parser that refuses NaN, a shape
    check, and the URL through the same `_web_url` as a typed link, which is
    where `file://`, `javascript:` and a bare search string are refused. The
    title is text cut at the library's own name limit. A `UrlError` is a
    `RuntimeError`, so the URL check is not inside the broad `except` below:
    it has to be a 400 with the scheme sentence, not a 500.

    ``index`` is 1-based and names the episode in the 400, because "one of
    them was wrong" helps nobody with three hundred ticked.
    """

    def bad(why: str) -> HTTPException:
        return HTTPException(
            status_code=400,
            detail=f"episode {index} is not something this app can read ({why})",
        )

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
    try:
        url = urls.ensure_http_url(url)
    except urls.UrlError as exc:
        raise bad(str(exc)) from None
    title = str(obj.get("title") or "")[: library.MAX_NAME]
    source_id = obj.get("source_id")
    source_id = source_id if isinstance(source_id, str) and source_id else None
    return {"url": url, "title": title, "source_id": source_id}


@router.post("/transcribe/url", include_in_schema=False)
async def add_url(request: Request) -> Response:
    """Queue the fetch of one link, or of the ticked episodes of a listing.

    Three cases, checked in this order: ticked entries, a list with nothing
    ticked, and neither - see the module docstring. The entries are read with
    `form.getlist`, not through `_fields`, which keeps the last value per
    name: right for the hidden-0/checkbox-1 option pairs, and wrong for a
    checkbox list, where it would turn three ticks into one job with no
    error. The field ceiling is this route's own (`FORM_FIELD_CEILING`).

    The plain case is deliberately probe-free. Whether the URL is a video or
    a playlist of fifty is `url_stage.fetch`'s question, asked in the child
    where the answer can take as long as it takes; asking it here would put
    a network round trip in front of a button press for no decision this
    route makes.
    """
    conn = request.app.state.conn
    form = await request.form(max_fields=FORM_FIELD_CEILING)
    fields = _fields(form)
    raw_entries = [value for value in form.getlist("entry") if isinstance(value, str)]

    if raw_entries:
        return await _add_episodes(request, conn, fields, raw_entries)
    if (fields.get("feed_url") or "").strip():
        raise HTTPException(status_code=400, detail=HINT_NONE_TICKED)

    url = _web_url(fields.get("url"))
    options = parse_options(fields)
    folder_id = library._folder_id_from(conn, fields.get("folder_id"))
    cookies = _cookies_file(conn, fields.get("cookies_file"))

    params: dict = {
        "url": url,
        "folder_id": folder_id,
        # Nested, because that is where url_stage.transcribe_params reads them
        # and because the playlist fan-out copies this dict to every child.
        url_stage.OPTIONS_KEY: options.to_params(),
    }
    if cookies:
        params["cookies_file"] = cookies

    jobs.enqueue(conn, url_stage.JOB_TYPE, params=params)
    save_defaults(conn, options)

    response = library._after_change(request, conn, flash=FLASH)
    response.headers["HX-Trigger"] = "jobs-changed"
    return response


async def _add_episodes(
    request: Request, conn: sqlite3.Connection, fields: dict, raw_entries: list[str]
) -> Response:
    """One `ingest_url` job per ticked episode - after every one has passed.

    The count is checked before a single entry is parsed, so two and a half
    thousand JSON blobs are not read only to be refused. Every entry, the
    feed's URL, the options, the folder and the cookies file are checked
    before anything is queued, and the queueing is one transaction
    (`jobs.enqueue_many`, in the threadpool: five hundred inserts have no
    business on the event loop), so a bad entry queues nothing - including
    the good ones beside it.
    """
    cap = url_stage.MAX_FAN_OUT  # read at request time: the tests patch it
    if len(raw_entries) > cap:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(raw_entries)} episodes ticked, and this app queues at most"
                f" {cap} in one go; tick fewer"
            ),
        )
    entries = [parse_entry(raw, index) for index, raw in enumerate(raw_entries, start=1)]
    feed_url = _web_url(fields.get("feed_url"))
    feed_title = str(fields.get("feed_title") or "").strip()[:MAX_FEED_TITLE]
    options = parse_options(fields)
    folder_id = library._folder_id_from(conn, fields.get("folder_id"))
    cookies = _cookies_file(conn, fields.get("cookies_file"))

    params_list = []
    for entry in entries:
        params: dict = {
            "url": entry["url"],
            "folder_id": folder_id,
            url_stage.OPTIONS_KEY: options.to_params(),
            "from_playlist": True,
            url_stage.ENTRY_KEY: {
                "title": entry.get("name") or entry["title"],
                "source_id": entry["source_id"],
            },
            url_stage.SOURCE_KEY: {"url": feed_url, "title": feed_title},
        }
        if cookies:
            params["cookies_file"] = cookies
        params_list.append(params)

    # A ticked feed import is bulk too: it queues behind hand-started work.
    queued = await run_in_threadpool(
        jobs.enqueue_many, conn, url_stage.JOB_TYPE, params_list, url_stage.BULK_PRIORITY
    )
    save_defaults(conn, options)

    # "Keep following this feed", ticked by default (ADR-008). Subscribing
    # queues nothing by itself: the entries on screen right now are recorded as
    # seen, so only what appears after today counts as new. The ones just
    # queued are among them, which is exactly right - they are accounted for.
    if str(fields.get("follow_feed") or "").strip() not in ("", "0", "false"):
        feeds.subscribe(
            conn,
            feed_url,
            title=feed_title,
            folder_id=folder_id,
            entries=entries,
            # A person who ticked episodes has answered the question this
            # subscription would otherwise ask (TASK-044): the ones they chose
            # are queued above, and the whole listing counts as accounted for.
            answered=True,
        )

    applog.log("ingest.episodes", feed=feed_url, title=feed_title, count=len(queued),
               first=queued[0], last=queued[-1])

    flash = EPISODES_FLASH.format(
        count=len(entries), s="" if len(entries) == 1 else "s", feed=feed_title or "the feed"
    )
    response = library._after_change(request, conn, flash=flash)
    response.headers["HX-Trigger"] = "jobs-changed"
    return response


# --- the preview --------------------------------------------------------------------


def preview_view(
    info: urls.UrlInfo, *, url: str, states: list[str | None] | None = None, cap: int | None = None
) -> dict:
    """What `_url_panel.html` shows for a link that answered.

    `webpage_url` is not among the fields on purpose: it is a URL chosen by
    whoever wrote the page, and the only thing to do with it in a template is
    make it an `href`, which is how a `javascript:` scheme would get onto a
    page of ours. The title says enough about what was found. ``url`` is the
    pasted, checked text the panel echoes back as `feed_url` - never the
    page's own idea of where it lives.

    Each entry's `value` is the JSON the checkbox carries, produced here with
    `json.dumps` and handed to the template as a plain string so the
    environment escapes it like any attribute. Never `|tojson`: that returns
    Markup, leaves `"` unescaped, and breaks a double-quoted value at the
    first quote in a title.
    """
    states = states or [None] * len(info.entries)
    entries = [
        {
            "title": entry["title"],
            "duration": entry["duration"],
            "timestamp": entry.get("timestamp"),
            "state": state,
            "value": json.dumps(
                {"url": entry["url"], "title": entry["title"], "source_id": entry.get("source_id")}
            ),
        }
        for entry, state in zip(info.entries, states)
    ]
    return {
        "state": "ok",
        "kind": info.kind,
        "title": info.title,
        "uploader": info.uploader,
        "duration": info.duration,
        "entries": entries,
        "count": len(entries),
        "total": info.total,
        "truncated": info.truncated,
        "feed_url": url,
        "feed_title": info.title,
        "cap": cap if cap is not None else url_stage.MAX_FAN_OUT,
    }


def _job_params(text: str | None) -> dict:
    try:
        params = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return params if isinstance(params, dict) else {}


def known_sources(conn: sqlite3.Connection, entries: list[dict]) -> list[str | None]:
    """One state per listed entry: "queued", "library", "trash" or None.

    Matched by the entry's URL without its fragment and by its extractor-
    scoped `source_id`, against the media rows' provenance (v10) and the
    `ingest_url` jobs still queued or running. Precedence queued > library >
    trash: the transient fact that stops a duplicate download wins, and "in
    library" reappears on its own once the job finishes. A trashed recording
    is named as such rather than hidden, because re-importing it would dedupe
    onto the trashed row and the finished transcript would be invisible.

    One statement for the media side: at most two parameters per entry, so
    5000 at `MAX_LISTED`, well under the 32766 that SQLite >= 3.35 (asserted
    in `db.connect`) allows. Under `db.LOCK`, like every read in this process
    (ADR-002).
    """
    url_keys = [urldefrag(str(entry.get("url") or "")).url for entry in entries]
    id_keys = [entry.get("source_id") or None for entry in entries]
    wanted_urls = sorted({key for key in url_keys if key})
    wanted_ids = sorted({key for key in id_keys if key})
    by_url: dict[str, str] = {}
    by_id: dict[str, str] = {}

    with db.LOCK:
        if wanted_urls or wanted_ids:
            clauses, params = [], []
            if wanted_urls:
                clauses.append(f"source_url IN ({','.join('?' * len(wanted_urls))})")
                params.extend(wanted_urls)
            if wanted_ids:
                clauses.append(f"source_id IN ({','.join('?' * len(wanted_ids))})")
                params.extend(wanted_ids)
            rows = conn.execute(
                "SELECT source_url, source_id, trashed_at FROM media WHERE " + " OR ".join(clauses),
                params,
            ).fetchall()
            for row in rows:
                state = "library" if row["trashed_at"] is None else "trash"
                for key, table in ((row["source_url"], by_url), (row["source_id"], by_id)):
                    if key and table.get(key) != "library":
                        table[key] = state
        live = conn.execute(
            "SELECT params_json FROM job WHERE type=? AND status IN ('queued', 'running')",
            (url_stage.JOB_TYPE,),
        ).fetchall()

    queued_urls: set[str] = set()
    queued_ids: set[str] = set()
    for row in live:
        params = _job_params(row["params_json"])
        queued_urls.add(urldefrag(str(params.get("url") or "")).url)
        entry = params.get(url_stage.ENTRY_KEY)
        if isinstance(entry, dict) and entry.get("source_id"):
            queued_ids.add(str(entry["source_id"]))

    out: list[str | None] = []
    for url_key, id_key in zip(url_keys, id_keys):
        if (url_key and url_key in queued_urls) or (id_key and id_key in queued_ids):
            out.append("queued")
            continue
        found = {by_url.get(url_key or ""), by_id.get(id_key or "")} - {None}
        out.append("library" if "library" in found else ("trash" if "trash" in found else None))
    return out


def probe_in_slot(url: str) -> urls.UrlInfo:
    """`urls.probe`, holding one of `PREVIEW_WORKERS` slots while it runs -
    unless the same URL is being listed already, in which case this thread
    waits for that answer instead.

    Both halves happen here, in the worker thread, and that placement is the
    whole design. Taken in the event loop instead, a slot would have to be
    given back when the request ended - which for an abandoned probe is while
    the socket is still open, counting exactly the ones worth counting as
    free. Taken here it is held for as long as the probe really runs.

    Leader or follower is decided under `_inflight_lock`, in the thread, so
    there is no check-then-act gap between a lookup and a registration. A
    follower holds no slot: it does no network work, only waits on the
    leader's future, and gets the same answer or the same `UrlError`. The
    leader removes its entry and releases its slot in `finally`, so a
    listing that raised is gone from the table by the time anyone retries.

    Raising rather than waiting when every slot is taken: a request that
    queued behind three slow sites would sit out the timeout and answer
    `HINT_SLOW`, which is true but unhelpful. `HINT_BUSY` says what actually
    happened.

    A thread that finds no slot has cost nothing - anyio hands it straight
    back to `idle_workers`, and a request cancelled before the thread picked
    the job up never runs this at all (`WorkerThread.run` checks the future).
    """
    with _inflight_lock:
        future = _inflight.get(url)
        leader = future is None
        if leader:
            if not _preview_slots.acquire(blocking=False):
                raise PreviewBusy(HINT_BUSY)
            future = Future()
            _inflight[url] = future
    if not leader:
        return future.result()
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
            if _inflight.get(url) is future:
                del _inflight[url]
        _preview_slots.release()


async def probe_or_none(url: str, *, patient: bool = False) -> urls.UrlInfo | None:
    """`probe_in_slot` in a worker thread; None when it took too long.

    `abandon_on_cancel=True` is the whole point of the function. Without it the
    cancel scope waits for the thread to come back, so the timeout would change
    *what the page says* but not *when it says it* - the request would still
    hang for as long as the site does. Abandoned, the thread finishes into
    nothing and its slot goes back then.

    ``patient`` is the retry button: a longer wait, not more work, because the
    thread attaches to the listing already running for that URL. The budget
    is read from the module at call time, never as a default argument: the
    tests patch both names.
    """
    budget = PATIENT_TIMEOUT_SECONDS if patient else PREVIEW_TIMEOUT_SECONDS
    with move_on_after(budget):
        return await to_thread.run_sync(probe_in_slot, url, abandon_on_cancel=True)
    return None


def _panel(request: Request, preview: dict) -> Response:
    return render(request, PREVIEW_PANEL, preview=preview)


@router.post("/transcribe/url/preview", include_in_schema=False)
async def url_preview(request: Request) -> Response:
    """What this link is, as a fragment the field swaps in while it is typed.

    A POST for a route that changes nothing, because the method is what
    `guard.SameOriginPosts` checks and this is the one route in `scribe/web`
    that opens a socket to an address somebody else chose - see the module
    docstring. The URL arrives in the body rather than the query string for
    the same reason: a GET with the same name is not left standing beside it.

    Always a 200, even when the probe failed: this is an htmx swap, and a 4xx
    would swap nothing and put the reason in the page's error line instead of
    under the field it is about. The failure *is* the content here. The one
    thing that is not a 200 is a request from another site, and that is
    refused by the guard before this function is entered at all.

    A cookies file is not read for the preview - the field carries one for the
    fetch, and this route takes only the URL, as the plan's interface says. The
    visible consequence is that a members-only link can preview as unavailable
    and still download fine once the cookies file is filled in.

    ``patient`` is the retry button's field: the same request with the long
    budget. The slow hint offers the button only once - a patient request
    that still timed out gets the hint without it.
    """
    conn = request.app.state.conn
    fields = _fields(await request.form())
    text = str(fields.get("url") or "").strip()
    patient = bool(str(fields.get("patient") or "").strip())
    if not text:
        return _panel(request, {"state": "empty", "message": HINT_EMPTY})
    try:
        urls.ensure_http_url(text)
    except urls.UrlError as exc:
        # Refused here rather than in the thread: a scheme this app will not
        # fetch is not worth a thread, and yt-dlp would open a `file://` URL.
        return _panel(request, {"state": "error", "message": str(exc)})

    try:
        info = await probe_or_none(text, patient=patient)
    except (urls.UrlError, PreviewBusy) as exc:
        return _panel(request, {"state": "error", "message": str(exc)})
    if info is None:
        return _panel(request, {"state": "error", "message": HINT_SLOW, "retry": not patient})
    states = await run_in_threadpool(known_sources, conn, info.entries) if info.entries else []
    return _panel(request, preview_view(info, url=text, states=states, cap=url_stage.MAX_FAN_OUT))


# --- recording from the microphone ------------------------------------------------


RECORD_FLASH = "Queued the recording for transcription."

MAX_CHUNK_BYTES = 32 * 1024 * 1024
"""The most one posted recording chunk may be.

`recorder.js` calls `MediaRecorder.start(5000)`, and five seconds of Opus is
tens of kilobytes - so this is three orders of magnitude of headroom, chosen
so that a browser that batches while its tab is throttled still gets through.
What it stops is the other case: one POST deciding how much memory the web
process uses."""

RECORD_TITLE_FIELD = "record_title"
"""The name field of the recorder panel, and it is not called `title`.

The recorder once shared a form with the upload and path doors, so a field
called `title` there would have been posted by their buttons too: a name typed
for a recording that was never made, riding along with a file that was. The
recorder has its own dialog now, and the name stays - the finish route reads
it by this name, and a field named for its owner can never be mistaken for
someone else's."""


def _refuse_unknown(exc: recording.UnknownSession) -> HTTPException:
    """A session that is not in progress, whatever the reason.

    404 for a token that was never started, one that has already finished, and
    one that is not a token at all. Three answers would let a caller tell which
    directories exist under `WORK_DIR`; one answer tells them nothing, and the
    user of a recorder that has lost its session needs the same thing in every
    case - to start again.
    """
    return HTTPException(status_code=404, detail=str(exc))


@router.post("/record/start", include_in_schema=False)
def record_start(request: Request) -> dict:
    """Open a session. The browser posts its chunks at the token this returns."""
    session = recording.start(request.app.state.conn)
    applog.log("record.start", session=session)
    return {"session": session}


@router.post("/record/{session}/chunk", include_in_schema=False)
async def record_chunk(session: str, request: Request) -> dict:
    """One MediaRecorder chunk, straight to disk; answers with its index.

    The body is read raw and the `Content-Type` is not checked. MediaRecorder
    sends `audio/webm;codecs=opus` on Chrome and Firefox and there is no
    promise it will keep saying exactly that, so a strict check here would be
    a test that passes and a browser that cannot record. What the bytes turn
    out to be is ffmpeg's question, asked once at the end where the answer can
    be a sentence.

    The *size* is checked, because this is the one ingest door that holds a
    whole body in memory - the upload spools to disk, the path and the URL
    never carry bytes at all - and without a cap one POST decides how much
    memory this process uses. Counted as it is read rather than taken from
    `Content-Length`: that header is a claim, and a chunked request does not
    make it at all.
    """
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > MAX_CHUNK_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"a recording chunk may be at most {MAX_CHUNK_BYTES} bytes;"
                    " a five-second one is a few tens of kilobytes"
                ),
            )
    data = bytes(data)
    try:
        index = await run_in_threadpool(recording.append, session, data)
    except recording.UnknownSession as exc:
        applog.log("record.chunk_refused", level="warn", session=session, bytes=len(data),
                   reason=str(exc))
        raise _refuse_unknown(exc) from None
    applog.log("record.chunk", level="debug", session=session, index=index, bytes=len(data))
    return {"index": index, "bytes": len(data)}


@router.post("/record/{session}/finish", include_in_schema=False)
async def record_finish(session: str, request: Request) -> Response:
    """Stop: the chunks become a recording in the library, and a queued job.

    Options and folder are checked *before* the chunks are touched, the same
    order `transcribe_dialog.upload` uses and for a sharper reason here: a
    mistyped tier must not be the thing that eats a two-hour session. Every
    failure below leaves the session directory exactly as it was, so the user
    can correct the form and press stop again.
    """
    conn = request.app.state.conn
    form = await request.form()
    fields = _fields(form)
    options = parse_options(fields)
    folder_id = library._folder_id_from(conn, fields.get("folder_id"))

    try:
        row = await run_in_threadpool(
            recording.finalize,
            conn,
            session,
            title=fields.get(RECORD_TITLE_FIELD) or "",
            folder_id=folder_id,
        )
    except recording.UnknownSession as exc:
        applog.log("record.finish_refused", level="warn", session=session, reason=str(exc))
        raise _refuse_unknown(exc) from None
    except (recording.EmptyRecording, recording.RemuxFailed) as exc:
        # 400 rather than 500: the bytes came from the caller, and ffmpeg's own
        # sentence is the most useful thing anyone can be told about them.
        applog.log("record.finish_failed", level="error", session=session,
                   error=type(exc).__name__, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from None

    applog.log("record.finished", session=session, media=row["id"], title=row["title"],
               bytes=row.get("size_bytes"), seconds=row.get("duration"))
    jobs.enqueue(conn, JOB_TYPE, media_id=row["id"], params=options.to_params())
    save_defaults(conn, options)

    response = library._after_change(request, conn, flash=RECORD_FLASH)
    response.headers["HX-Trigger"] = "jobs-changed"
    return response


@router.post("/record/{session}/cancel", include_in_schema=False)
def record_cancel(session: str, request: Request) -> dict:
    """Throw the session away, chunks and all. Nothing reaches the library."""
    try:
        recording.cancel(request.app.state.conn, session)
    except recording.UnknownSession as exc:
        raise _refuse_unknown(exc) from None
    return {"cancelled": session}
