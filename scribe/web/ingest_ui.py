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
thread is let go - it cannot hold the page open, and yt-dlp's own
`SOCKET_TIMEOUT` ends it shortly after. A thread that was merely waited on
would answer with exactly the same words, ten seconds later, which is the
failure that design is about.

Third, `PREVIEW_WORKERS` bounds how many probes may be in flight, and the
count is kept by the probe itself rather than by the request. That is not
belt-and-braces on the timeout, it is the thing the timeout creates: anyio
releases its capacity-limiter token the moment the cancel scope closes
(`anyio/_backends/_asyncio.py`, `async with limiter` wrapped around `await
future`) while the worker thread is still sitting on the socket, so anything
that counts *requests* counts none of the abandoned ones. `_preview_slots` is
taken and given back inside the thread, so what it bounds is live probes.

Everything the probe returns is a stranger's text. It goes into the template
as data and is escaped by the environment like every other string in this app;
nothing here is `|safe`, and the page URL yt-dlp reports is deliberately *not*
rendered as a link - autoescaping quotes an `href` correctly and would still
happily emit `javascript:`.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from anyio import move_on_after, to_thread
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from scribe import applog, fsbrowse, jobs
from scribe.ingest import recording, urls
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


@router.post("/transcribe/url", include_in_schema=False)
async def add_url(request: Request) -> Response:
    """Queue the fetch of one link; answer like every other library mutation.

    Deliberately probe-free. Whether the URL is a video or a playlist of fifty
    is `url_stage.fetch`'s question, asked in the child where the answer can
    take as long as it takes; asking it here would put a network round trip in
    front of a button press for no decision this route makes.
    """
    conn = request.app.state.conn
    form = await request.form()
    fields = _fields(form)

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


# --- the preview --------------------------------------------------------------------


def preview_view(info: urls.UrlInfo) -> dict:
    """What `_url_panel.html` shows for a link that answered.

    `webpage_url` is not among the fields on purpose: it is a URL chosen by
    whoever wrote the page, and the only thing to do with it in a template is
    make it an `href`, which is how a `javascript:` scheme would get onto a
    page of ours. The title says enough about what was found.
    """
    return {
        "state": "ok",
        "kind": info.kind,
        "title": info.title,
        "uploader": info.uploader,
        "duration": info.duration,
        "entries": len(info.entries),
    }


def probe_in_slot(url: str) -> urls.UrlInfo:
    """`urls.probe`, holding one of `PREVIEW_WORKERS` slots while it runs.

    Both halves happen here, in the worker thread, and that placement is the
    whole design. Taken in the event loop instead, a slot would have to be
    given back when the request ended - which for an abandoned probe is while
    the socket is still open, counting exactly the ones worth counting as
    free. Taken here it is held for as long as the probe really runs.

    Raising rather than waiting: a request that queued behind three slow sites
    would sit out the timeout and answer `HINT_SLOW`, which is true but
    unhelpful. `HINT_BUSY` says what actually happened.

    A thread that finds no slot has cost nothing - anyio hands it straight
    back to `idle_workers`, and a request cancelled before the thread picked
    the job up never runs this at all (`WorkerThread.run` checks the future).
    """
    if not _preview_slots.acquire(blocking=False):
        raise PreviewBusy(HINT_BUSY)
    try:
        return urls.probe(url)
    finally:
        _preview_slots.release()


async def probe_or_none(url: str) -> urls.UrlInfo | None:
    """`probe_in_slot` in a worker thread; None when it took too long.

    `abandon_on_cancel=True` is the whole point of the function. Without it the
    cancel scope waits for the thread to come back, so the timeout would change
    *what the page says* but not *when it says it* - the request would still
    hang for as long as the site does. Abandoned, the thread finishes into
    nothing, yt-dlp's socket timeout collects it, and its slot goes back then.
    """
    with move_on_after(PREVIEW_TIMEOUT_SECONDS):
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
    """
    text = str(_fields(await request.form()).get("url") or "").strip()
    if not text:
        return _panel(request, {"state": "empty", "message": HINT_EMPTY})
    try:
        urls.ensure_http_url(text)
    except urls.UrlError as exc:
        # Refused here rather than in the thread: a scheme this app will not
        # fetch is not worth a thread, and yt-dlp would open a `file://` URL.
        return _panel(request, {"state": "error", "message": str(exc)})

    try:
        info = await probe_or_none(text)
    except (urls.UrlError, PreviewBusy) as exc:
        return _panel(request, {"state": "error", "message": str(exc)})
    if info is None:
        return _panel(request, {"state": "error", "message": HINT_SLOW})
    return _panel(request, preview_view(info))


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
