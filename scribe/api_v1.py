"""/api/v1: the versioned JSON surface a program drives MyScribe through.

ADR-025, option B (Robert, 2026-10-24): JSON twins of the actions an outside
program needs, with a published schema (`/openapi.json`), coded errors and an
idempotency key on the writes that queue GPU work - and **no token**. These
routes sit behind exactly what the pages sit behind: the 127.0.0.1 bind and
`scribe.guard` (a foreign Host is a 400, a mutating request from another
site a 403). A token here would guard one door while the form routes beside
it stay open to the same local processes; ADR-025 says why that buys nothing.

Three rules shape the module:

* **No logic of its own.** Every route calls the helper its page or its
  unversioned twin calls - `scribe.app.ingest_request`, `library.media_rows`,
  `ai_ui.ask`, `exports_ui.media_members` and so on - and only translates the
  answer to JSON. A rule fixed for the pages is fixed here.
* **Every error is ``{"code": ..., "detail": ...}``**, and only here. The
  handlers in `install` look at the path: under /api/v1 an HTTPException or
  a validation error gets a stable ``code`` (`CODES` by status, or the
  `CodedError` a route raises for a case worth its own name); anywhere else
  FastAPI's own handler answers, so the pages' ``{"detail"}`` is unchanged.
  An exception no route handled is a coded 500 (``internal_error``) here
  and Starlette's plain-text 500 elsewhere. The guard's two refusals happen before any route and keep their own shape:
  Starlette's plain-text "Invalid host header" (400) and the guard's JSON 403.
* **Nothing of this reaches the phone door.** The door is its own ASGI app
  with two routes (ADR-022); tests/test_api_v1.py walks every /api/v1 path
  through it and expects a 404.

The settings are read from an allowlist: the `setting` table holds the
Hugging Face token and provider keys, and none of them is in the answer.
The one setting a program may change here is the watch folders, through
the functions the Settings page calls (`scribe.web.settings.add_watched`).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Body, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, PlainTextResponse, Response

from scribe import accel, db, doctor, fsbrowse, glossary, idempotency, llm, options
from scribe import app as spine
from scribe.exports import jsonw
from scribe.exports import doc as export_doc
from scribe.exports.options import ExportOptions, filename_for
from scribe.ingest import watching
from scribe.web import ai_ui, exports_ui, library, transcript
from scribe.web import settings as settings_page

PREFIX = "/api/v1"

# The code an HTTPException gets from its status when the route raising it is
# a shared helper that knows nothing of this surface. Stable: a client may
# switch on these, so one changes only with a new version.
CODES: dict[int, str] = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    415: "unsupported_media_type",
    422: "invalid_request",
    500: "internal_error",
}


class CodedError(HTTPException):
    """An HTTPException with a code of its own, for a case a client acts on."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


# --- the schema's shapes ---------------------------------------------------------------------
# Loose on purpose (extra="allow"): each names the fields a client can rely
# on, and the rest of the row comes along, as the unversioned routes send it.


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ApiError(BaseModel):
    code: str
    detail: str


class Job(_Open):
    id: int
    type: str
    status: str
    media_id: int | None = None
    stage: str | None = None


class JobDetail(Job):
    events: list[dict]


class JobList(BaseModel):
    jobs: list[Job]


class Media(_Open):
    id: int
    title: str
    duration: float | None = None
    created_at: float


class MediaList(BaseModel):
    media: list[Media]


class Ingested(Media):
    deduped: bool
    job_id: int


class MediaDetail(Media):
    has_transcript: bool
    latest_job: dict | None = None


class SearchPart(BaseModel):
    text: str
    match: bool


class SearchHit(BaseModel):
    media_id: int
    title: str
    start: float
    parts: list[SearchPart]


class SearchResult(BaseModel):
    hits: list[SearchHit]
    uncorrected: bool


class AiQueued(BaseModel):
    job: Job
    already_queued: bool


class Settings(BaseModel):
    transcribe_defaults: dict
    browse_roots: list[str]
    watch_folders: list[dict]
    ai: dict
    cpu_fallback: bool


class Check(BaseModel):
    name: str
    ok: bool
    detail: str
    fix_hint: str
    optional: bool
    tested: bool


class GpuRun(BaseModel):
    ts: float
    job_id: int | None = None
    checks: list[Check]


class PendingJob(BaseModel):
    id: int
    status: str


class Doctor(BaseModel):
    ok: bool
    checks: list[Check]
    gpu_last: GpuRun | None = None
    gpu_job: PendingJob | None = None


class WatchFolder(BaseModel):
    id: int
    path: str
    enabled: bool
    include_video: bool
    allowed: bool
    missing: bool
    unwatchable: bool
    left: int
    options: dict


class WatchFolderList(BaseModel):
    watching: bool
    folders: list[WatchFolder]


class WatchFolderAdded(WatchFolder):
    skipped_video: int
    message: str


class WatchFolderRemoved(BaseModel):
    id: int
    removed: bool


class WatchFolderTaken(BaseModel):
    id: int
    taken: int
    message: str


_ERRORS = {
    status: {"model": ApiError, "description": code}
    for status, code in CODES.items()
    if status in (400, 403, 404, 409, 422)
}

router = APIRouter(prefix=PREFIX, tags=["v1"], responses=_ERRORS)

KeyHeader = Annotated[
    str | None,
    Header(alias=idempotency.HEADER, description="Retry-safe key: the same key and request "
           "replay the first answer and queue nothing; the same key with another request is a "
           "409 idempotency_key_reused, and the same key while its first request is still "
           "running a 409 idempotency_key_in_progress. A key whose job is gone (its recording "
           "was deleted) is forgotten and the request runs as new. Kept 24 hours."),
]


def _conn(request: Request):
    return request.app.state.conn


def _key(raw: str | None) -> str | None:
    if raw is None:
        return None
    if not idempotency.valid(raw):
        raise CodedError(400, "bad_request", f"{idempotency.HEADER} must be 1-255 visible ASCII characters")
    return raw


REPLAYED = {"Idempotent-Replayed": "true"}


def _refused(exc: Exception, key: str) -> CodedError:
    if isinstance(exc, idempotency.KeyInProgress):
        return CodedError(409, "idempotency_key_in_progress",
                          f"{idempotency.HEADER} {key!r} belongs to a request that has not answered yet")
    return CodedError(409, "idempotency_key_reused",
                      f"{idempotency.HEADER} {key!r} was used for a different request")


def _upload_alive(conn):
    """Whether a stored upload answer still names *its* recording and job.

    A deleted recording takes its jobs with it, and SQLite hands the freed
    ids out again: job 1 on media 1 may be another upload's by the time a
    client retries. So the job must still point at a media row with the
    answer's id, content hash and filing time, not merely exist.
    """
    def alive(body) -> bool:
        with db.LOCK:
            return conn.execute(
                "SELECT 1 FROM job JOIN media ON media.id = job.media_id"
                " WHERE job.id=? AND media.id=? AND media.sha256=? AND media.created_at=?",
                (body.get("job_id"), body.get("id"), body.get("sha256"), body.get("created_at")),
            ).fetchone() is not None
    return alive


def _job_alive(conn):
    """Whether a stored job answer's job is still that job (id and creation time)."""
    def alive(body) -> bool:
        with db.LOCK:
            return conn.execute(
                "SELECT 1 FROM job WHERE id=? AND created_at=?", (body.get("id"), body.get("created_at"))
            ).fetchone() is not None
    return alive


def _once(conn, key: str | None, route: str, print_: str, status: int, work, alive=None) -> Response:
    """Run ``work`` (which queues and returns the body) once per key.

    Looked up, queued and remembered under one `db.LOCK` hold, so two racing
    retries cannot both queue (see `scribe.idempotency`). For a route that
    files nothing; an upload claims its key before filing (`add_media`).
    """
    if key is None:
        return JSONResponse(work(), status_code=status)
    with db.LOCK:
        try:
            stored = idempotency.replay(conn, key, print_, alive)
        except (idempotency.KeyReused, idempotency.KeyInProgress) as exc:
            raise _refused(exc, key) from None
        if stored is not None:
            status_, body = stored
            return JSONResponse(body, status_code=status_, headers=REPLAYED)
        body = work()
        idempotency.remember(conn, key, route, print_, status, body)
    return JSONResponse(body, status_code=status)


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CodedError(400, "bad_request", f"body is not valid JSON: {exc}") from None
    if not isinstance(body, dict):
        raise CodedError(400, "bad_request", "the body must be a JSON object")
    return body


def _require_transcript(conn, media_id: int) -> None:
    library._get_media(conn, media_id)
    if not exports_ui._has_transcript(conn, media_id):
        raise CodedError(409, "no_transcript", f"media {media_id} has no transcript yet")


# --- the library ----------------------------------------------------------------------------


@router.get("/media", response_model=MediaList, summary="List the library")
def list_media(
    request: Request,
    view: str | None = None,
    folder: int | None = None,
    sort: str | None = None,
    q: str | None = None,
    label: str | None = None,
) -> dict:
    """The library table's rows: ``view`` recent/uncategorized/trash, a
    ``folder`` id, ``sort`` created_at/title/duration, a title filter ``q``
    and a ``label``, the same filters and order as the page."""
    state = library.parse_state(request.query_params)
    return {"media": library.media_rows(_conn(request), state)}


@router.get("/search", response_model=SearchResult, summary="Search the transcripts")
def search(request: Request, q: str = "") -> dict:
    """Segments of current transcripts matching every word of ``q``, best first."""
    if not q.strip():
        raise CodedError(400, "bad_request", "q is empty; give the words to search for")
    conn = _conn(request)
    hits = library.search_hits(conn, q.strip())
    return {
        "hits": [
            {**hit, "parts": [{"text": text, "match": match} for text, match in hit["parts"]]}
            for hit in hits
        ],
        # The search index does not see the glossary's corrections; say so.
        "uncorrected": glossary.any_corrections(conn),
    }


@router.get("/media/{media_id}", response_model=MediaDetail, summary="One recording")
def get_media(media_id: int, request: Request) -> dict:
    conn = _conn(request)
    row = dict(library._get_media(conn, media_id))
    row["has_transcript"] = exports_ui._has_transcript(conn, media_id)
    row["latest_job"] = transcript.latest_job(conn, media_id)
    return row


@router.get(
    "/media/{media_id}/transcript",
    summary="The transcript, word by word",
    responses={200: {"content": {"application/json": {}},
                     "description": "The JSON export, schema_version 1 (scribe.exports.jsonw)"}},
)
def get_transcript(media_id: int, request: Request, include_confidence: bool = False) -> Response:
    """The current run as the JSON export writes it: media, run, speakers,
    every word with its times, and Whisper's segments."""
    conn = _conn(request)
    _require_transcript(conn, media_id)
    try:
        document = export_doc.load(conn, media_id)
    except export_doc.NoTranscript as exc:  # re-transcribed away in between
        raise CodedError(409, "no_transcript", str(exc)) from None
    options_ = ExportOptions(formats=["json"], include_confidence=include_confidence)
    return Response(jsonw.write(document, options_), media_type="application/json")


@router.post(
    "/media/{media_id}/export",
    summary="Export a transcript",
    responses={200: {"content": {"application/octet-stream": {}},
                     "description": "One format as the file itself; several as a ZIP"}},
)
async def export(
    media_id: int,
    request: Request,
    body: Annotated[dict | None, Body(description="ExportOptions fields; {} is the defaults")] = None,
) -> Response:
    """The file the export dialog's download gives, for these options.
    Writing into a folder is the dialog's; a program writes the bytes itself."""
    conn = _conn(request)
    _require_transcript(conn, media_id)
    options_ = exports_ui.validate_options(body or {})

    def build() -> Response:
        document, members = exports_ui.media_members(conn, media_id, options_)
        return exports_ui.download(members, options_, filename_for(document, options_, "zip"))

    return await run_in_threadpool(build)


# --- sending work in -----------------------------------------------------------------------------

_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "title": {"type": "string"},
                        "folder_id": {"type": "integer"},
                        **{name: {"type": "string"} for name in options.OPTION_FIELDS},
                        "params": {"type": "string", "description": "JSON object of stage keys"},
                    },
                }
            },
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string", "description": "A file under the browse roots"},
                        "title": {"type": "string"},
                        "folder_id": {"type": "integer"},
                        "params": {"type": "object"},
                    },
                    "additionalProperties": True,
                }
            },
        },
    }
}


@router.post(
    "/media", status_code=201, response_model=Ingested, openapi_extra=_UPLOAD_BODY,
    summary="Send a recording in and queue its transcription",
    responses={413: {"model": ApiError}, 415: {"model": ApiError}},
)
async def add_media(request: Request, idempotency_key: KeyHeader = None) -> Response:
    """The bytes as multipart (``file``), or ``{"path": ...}`` for a file on
    this machine under the browse roots; options as the dialog's fields or a
    ``params`` object. Known content is not stored twice (``deduped``)."""
    key = _key(idempotency_key)
    conn = _conn(request)
    if key is None:
        row, params = await spine.ingest_request(request)
        return JSONResponse(spine.enqueue_ingested(conn, row, params), status_code=201)

    # The key is decided between hashing and filing (`admit`), so a refusal
    # or a replay files nothing and nothing is ever taken back. Hashing
    # gigabytes holds no lock; the claim, the queueing and the answer do.
    route = "POST /api/v1/media"
    claim = {"held": False, "over": False, "print": ""}

    def admit(sha256: str, params: dict) -> None:
        print_ = idempotency.fingerprint(route, sha256=sha256, params=params)
        with db.LOCK:
            if claim["over"]:  # the request already ended: claim nothing for it
                raise _Gone()
            try:
                stored = idempotency.claim(conn, key, route, print_, _upload_alive(conn))
            except (idempotency.KeyReused, idempotency.KeyInProgress) as exc:
                raise _refused(exc, key) from None
            if stored is not None:
                raise _Replay(*stored)
            claim.update(held=True, print=print_)

    try:
        row, params = await spine.ingest_request(request, admit=admit)
        with db.LOCK:
            body = spine.enqueue_ingested(conn, row, params)
            idempotency.remember(conn, key, route, claim["print"], 201, body)
            claim["held"] = False
        return JSONResponse(body, status_code=201)
    except _Replay as replayed:
        return JSONResponse(replayed.body, status_code=replayed.status, headers=REPLAYED)
    finally:
        # Also on a cancelled request whose filing thread runs on: once this
        # has run, that thread's `admit` claims nothing.
        with db.LOCK:
            claim["over"] = True
            if claim["held"]:
                idempotency.release(conn, key)


class _Replay(Exception):
    """A stored answer, raised out of `admit` so that nothing gets filed."""

    def __init__(self, status: int, body) -> None:
        super().__init__(status)
        self.status = status
        self.body = body


class _Gone(Exception):
    """Raised in a filing thread that outlived its request."""


TRANSCRIBE_FIELDS = frozenset((*options.OPTION_FIELDS, "params"))


@router.post(
    "/media/{media_id}/transcribe", status_code=201, response_model=Job,
    summary="Transcribe a recording again",
)
async def transcribe(
    media_id: int,
    request: Request,
    idempotency_key: KeyHeader = None,
    body: Annotated[dict | None, Body(description="The dialog's option fields, or a params "
                                       "object; empty repeats the last request")] = None,
) -> Response:
    key = _key(idempotency_key)
    conn = _conn(request)
    library._get_media(conn, media_id)
    body = body or {}
    for field in ("title", "folder_id", "path"):
        if field in body:
            raise CodedError(400, "bad_request", f"{field!r} belongs to an upload; this route takes options")
    # Refused rather than dropped, the rule `_ingest_options` keeps for a
    # params key: {"foo": 1} once queued a job with neither what was asked
    # nor the last request.
    unknown = sorted(set(body) - TRANSCRIBE_FIELDS)
    if unknown:
        raise CodedError(
            400, "bad_request",
            f"this route takes {', '.join(sorted(TRANSCRIBE_FIELDS))}; not {', '.join(map(repr, unknown))}",
        )
    _, _, params = spine._ingest_options(body)
    if not body:
        params = library._last_params(conn, media_id)
    route = "POST /api/v1/media/{id}/transcribe"
    print_ = idempotency.fingerprint(route, media_id=media_id, params=params)

    def queue() -> dict:
        job_id = spine.jobs.enqueue(conn, library.TRANSCRIBE_JOB_TYPE, media_id=media_id, params=params)
        return spine._job_out(conn, spine._get_job(conn, job_id))

    return _once(conn, key, route, print_, 201, queue, _job_alive(conn))


@router.post(
    "/media/{media_id}/ai/{kind}", response_model=AiQueued, status_code=201,
    summary="Ask a question about a recording",
)
def ask_ai(
    media_id: int,
    kind: str,
    request: Request,
    response: Response,
    body: Annotated[dict | None, Body(description="provider, model and prompt; each optional "
                                       "but a custom kind's prompt")] = None,
) -> dict:
    """Queue one AI job (summary, chapters, ... or custom with a ``prompt``).
    The same question while it is still on its way is answered with that job
    (200, ``already_queued``), as the panel's double-click guard does."""
    body = body or {}
    conn = _conn(request)
    asked = ai_ui.ask(
        conn, media_id, kind,
        provider=str(body.get("provider") or ""), model=str(body.get("model") or ""),
        prompt=str(body.get("prompt") or ""),
    )
    if asked.blocked is not None:
        raise CodedError(409, "no_provider" if asked.nobody_chose else "provider_unavailable",
                       asked.blocked["why"])
    if asked.already_queued:
        response.status_code = 200
    return {"job": spine._job_out(conn, spine._get_job(conn, asked.job_id)),
            "already_queued": asked.already_queued}


# --- jobs --------------------------------------------------------------------------------------


@router.get("/jobs", response_model=JobList, summary="Jobs, newest first")
def list_jobs(request: Request, status: str | None = None, limit: int = 200) -> dict:
    """``limit`` is clamped to 1-1000; queued and running jobs carry their
    queue position and an ETA."""
    return {"jobs": spine.job_rows(_conn(request), status, limit)}


@router.get("/jobs/{job_id}", response_model=JobDetail, summary="One job and its last events")
def get_job(job_id: int, request: Request) -> dict:
    return spine.job_with_events(_conn(request), job_id)


@router.post("/jobs/{job_id}/cancel", response_model=Job, summary="Cancel a job")
def cancel_job(job_id: int, request: Request) -> dict:
    return spine.cancel(_conn(request), job_id, getattr(request.app.state, "supervisor", None))


# --- settings, read only ---------------------------------------------------------------------


@router.get("/settings", response_model=Settings, summary="What the next job gets")
def get_settings(request: Request) -> dict:
    """The transcription defaults, the browse roots, the watch folders, the AI
    provider a question goes to, and the CPU fallback. Never a key or token."""
    conn = _conn(request)
    provider = llm.default_provider(conn)
    try:
        local = bool(provider) and bool(llm.provider_class(provider).is_local)
    except ValueError:
        local = False
    return {
        "transcribe_defaults": options.read_defaults(conn).model_dump(),
        "browse_roots": [str(root) for root in fsbrowse.allowed_roots(conn)],
        "watch_folders": [
            {
                "id": folder["id"],
                "path": folder["path"],
                "enabled": bool(folder["enabled"]),
                "include_video": bool(folder.get("include_video", 1)),
                "allowed": bool(folder["allowed"]),
                "options": folder["options"].model_dump(),
            }
            for folder in watching.folders(conn, enabled_only=False)
        ],
        "ai": {
            "provider": provider,
            "model": llm.default_model(conn, provider) if provider else "",
            "local": local,
        },
        "cpu_fallback": bool(accel.cpu_fallback_allowed(conn)),
    }


# --- the doctor, read only -------------------------------------------------------------------


@router.get("/doctor", response_model=Doctor, summary="Does this computer work")
async def get_doctor(request: Request) -> dict:
    """The checks the Settings page runs when it opens (`doctor.web_checks`:
    none that loads a model runtime into the web process, ADR-001), the last
    GPU check's stored result and a GPU check still waiting. It queues
    nothing: the GPU check is a job, started from the page. ``ok`` is false
    when a required check of this run failed; the stored GPU run is not
    counted in it."""
    conn = _conn(request)
    checks = await run_in_threadpool(doctor.web_checks)
    context = settings_page.doctor_context(conn, cpu_checks=checks)
    last = context["gpu_last"]
    return {
        "ok": doctor.exit_code(checks) == 0,
        "checks": [asdict(check) for check in checks],
        "gpu_last": None if last is None else {**last, "checks": [asdict(c) for c in last["checks"]]},
        "gpu_job": context["gpu_job"],
    }


# --- watch folders ---------------------------------------------------------------------------

WATCH_FIELDS = frozenset((*options.OPTION_FIELDS, settings_page.FIELD_WATCH_PATH,
                          settings_page.FIELD_WATCH_VIDEO))


def _watch_folders(request: Request) -> dict:
    """The page's own `watch_context`, as JSON."""
    conn = _conn(request)
    context = settings_page.watch_context(conn, watcher=getattr(request.app.state, "watcher", None))
    stored = {folder["id"]: folder for folder in watching.folders(conn, enabled_only=False)}
    return {
        "watching": context["watching"],
        "folders": [
            {
                "id": row["id"],
                "path": row["path"],
                "enabled": row["enabled"],
                "include_video": row["include_video"],
                "allowed": not row["outside_roots"],
                "missing": row["missing"],
                "unwatchable": row["unwatchable"],
                "left": row["left"],
                "options": stored[row["id"]]["options"].model_dump(),
            }
            for row in context["watch_folders"]
        ],
    }


@router.get("/watch-folders", response_model=WatchFolderList, summary="The watch folders")
def list_watch_folders(request: Request) -> dict:
    """Each folder as the Settings page shows it: whether it is on, takes
    video, is still under the browse roots, is on this machine, could be
    watched live (``unwatchable``; always false while ``watching`` is false,
    an app run without its supervisor), and how many of the files it held
    when it was added it is still leaving alone (``left``)."""
    return _watch_folders(request)


@router.post("/watch-folders", status_code=201, response_model=WatchFolderAdded,
             summary="Watch a folder")
async def add_watch_folder(request: Request) -> dict:
    """``path`` (absolute, a folder under the browse roots and outside the
    app's data directory), ``include_video`` (default false, as the page's
    form) and the transcribe dialog's option fields for what it takes in.
    The function the page calls: what is already in the folder is left
    alone and counted (``left``, ``skipped_video``), and ``message`` is the
    sentence the page shows, except that it names Settings > Watch folders
    where the page says "below". A folder already watched is a 409
    ``already_watched``."""
    body = await _json_body(request)
    unknown = sorted(set(body) - WATCH_FIELDS)
    if unknown:
        raise CodedError(400, "bad_request", f"this route takes {', '.join(sorted(WATCH_FIELDS))}; "
                         f"not {', '.join(map(repr, unknown))}")
    include_video = body.get(settings_page.FIELD_WATCH_VIDEO, False)
    if not isinstance(include_video, bool):
        raise CodedError(400, "bad_request", "include_video must be true or false")
    path = body.get(settings_page.FIELD_WATCH_PATH)
    if path is not None and not isinstance(path, str):
        raise CodedError(400, "bad_request", "path must be a string")
    conn = _conn(request)
    chosen = options.parse_options(body)
    try:
        added, left = settings_page.add_watched(conn, path, chosen, include_video=include_video)
    except HTTPException as exc:
        if exc.status_code == 409:
            raise CodedError(409, "already_watched", exc.detail) from None
        raise
    (row,) = [f for f in _watch_folders(request)["folders"] if f["path"] == str(added)]
    return {**row, "skipped_video": left.skipped_video,
            "message": settings_page.watch_added_sentence(left, include_video, on_page=False)}


@router.delete("/watch-folders/{folder_id}", response_model=WatchFolderRemoved,
               summary="Stop watching a folder")
def remove_watch_folder(folder_id: int, request: Request) -> dict:
    """Nothing already in the library is touched, as on the page."""
    conn = _conn(request)
    settings_page._get_watch_folder(conn, folder_id)
    watching.remove_folder(conn, folder_id)
    return {"id": folder_id, "removed": True}


@router.post("/watch-folders/{folder_id}/existing", response_model=WatchFolderTaken,
             summary="Transcribe what a folder held when it was added")
def take_existing_files(folder_id: int, request: Request) -> dict:
    """The page's "Transcribe them" button: the files the folder left alone
    when it was added (``left``) are no longer left alone, and the watcher's
    next walk takes them in with the folder's options. Only what the folder
    takes: an audio-only folder keeps leaving its video alone. ``taken`` is
    how many; asked again, it is 0. The jobs are the watcher's to queue, so
    an app run without its supervisor queues them at its next start with
    one."""
    conn = _conn(request)
    taken, sentence = settings_page.take_existing_watched(
        conn, folder_id, getattr(request.app.state, "watcher", None))
    return {"id": folder_id, "taken": taken, "message": sentence}


# --- wiring --------------------------------------------------------------------------------------


def _ours(request: Request) -> bool:
    path = request.url.path
    return path == PREFIX or path.startswith(PREFIX + "/")


def _coded(status: int, code: str, detail: Any, headers=None, **extra) -> JSONResponse:
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    return JSONResponse({"code": code, "detail": text, **extra}, status_code=status, headers=headers)


async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
    if not _ours(request):
        return await http_exception_handler(request, exc)
    code = getattr(exc, "code", None) or CODES.get(exc.status_code, f"http_{exc.status_code}")
    return _coded(exc.status_code, code, exc.detail, headers=getattr(exc, "headers", None))


async def _invalid(request: Request, exc: RequestValidationError) -> Response:
    if not _ours(request):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    detail = "; ".join(
        f"{'.'.join(str(part) for part in error.get('loc', ()))}: {error.get('msg', '')}"
        for error in errors
    )
    return _coded(422, CODES[422], detail, errors=json.loads(json.dumps(errors, default=str)))


async def _unexpected(request: Request, exc: Exception) -> Response:
    """An exception no route handled: a coded 500 here, Starlette's own text
    elsewhere. The exception itself stays out of the answer; Starlette
    raises it on after this, to the server as before."""
    if not _ours(request):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return _coded(500, CODES[500], "an unexpected error ended this request")


def install(app: FastAPI) -> None:
    """Add the routes, and the coded error shape for their paths only."""
    app.include_router(router)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _invalid)
    app.add_exception_handler(Exception, _unexpected)
