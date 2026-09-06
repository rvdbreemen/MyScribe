"""FastAPI shell: /health, media ingest, and the JSON job API.

One web process owns the DB connection (app.state.conn, every access
guarded by db.LOCK) and two threads: the supervisor, and the watch-folder
watcher. Lifespan order matters: ensure dirs -> connect + migrate ->
clear up what a previous life left behind (orphaned jobs, abandoned
recording sessions) -> start the supervisor and the watcher. This module
is the JSON spine; the HTML pages and static files live in `scribe.web`
and are mounted at the end, and `scribe.guard` wraps the whole app so that
only this machine's own browser, on this app's own pages, can change
anything.

`POST /api/media` is the front door: bytes or a path go in, a media row and a
queued transcribe job come out. It deliberately does not decide whether the
file is media - probe does that, inside the job, where a rejection is a job the
user can see and read the reason for rather than an HTTP error that vanishes.
A path does have to lie under `scribe.fsbrowse`'s allowed roots, the same rule
the transcribe dialog applies, so the two doors agree about what may be read.
Ingest itself runs in a threadpool: hashing four gigabytes is tens of seconds
of blocking work, and the jobs board has to stay answerable while it happens.

The job's params come in one of two shapes: the option fields the transcribe
dialog uses (`language`, `tier`, `diarize`, ...), validated by
`scribe.options.TranscribeOptions` - the dialog's model too - so both doors
mean the same thing by them, or a raw `params` object for a caller who knows
the stage keys. Not both: two answers to one question would need a precedence
rule, and a 400 is clearer than one.
"""

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

import scribe
from scribe import applog, db, fsbrowse, guard, jobs, media, paths, supervisor, web
from scribe.ingest import recording, watching
from scribe.options import OPTION_FIELDS, parse_options
from scribe.stages import transcribe

# The form field carrying the upload, and the job type ingest queues.
_UPLOAD_FIELD = "file"
_INGEST_JOB_TYPE = "transcribe"


def _get_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    with db.LOCK:
        row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id}")
    return row


def _job_out(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Job row as a JSON-ready dict; queued/running get position and ETA."""
    out = dict(row)
    if out["status"] in ("queued", "running"):
        out["queue_position"] = jobs.queue_position(conn, out["id"])
        params = json.loads(out["params_json"] or "{}")
        out["eta_seconds"] = jobs.job_eta(conn, out, transcribe.perf_model_for(params))
    return out


def _ingest_options(source: dict) -> tuple[str | None, int | None, dict]:
    """title, folder_id and job params out of a JSON body or a form.

    One function for both because a form gives everything back as a string:
    `folder_id` arrives as "3" and `params` as the JSON text of an object, and
    the caller should not have to care which door it came through.
    """
    title = source.get("title")
    title = str(title) if title not in (None, "") else None

    folder_id = source.get("folder_id")
    if folder_id in (None, ""):
        folder_id = None
    else:
        try:
            folder_id = int(folder_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"folder_id must be an integer, got {folder_id!r}"
            )

    params = source.get("params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"params is not valid JSON: {exc}")
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be a JSON object")

    given = {key: source[key] for key in OPTION_FIELDS if key in source}
    if given:
        if params:
            raise HTTPException(
                status_code=400,
                detail=(
                    "send the transcribe options as fields "
                    f"({', '.join(OPTION_FIELDS)}) or as a params object, not both"
                ),
            )
        params = parse_options(given).to_params()

    return title, folder_id, params


async def _ingest_upload(request: Request, conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Multipart: the bytes are already spooled, so this only hashes and files them."""
    form = await request.form()
    upload = form.get(_UPLOAD_FIELD)
    if upload is None or not hasattr(upload, "file"):
        raise HTTPException(
            status_code=400,
            detail=f"a multipart upload needs a {_UPLOAD_FIELD!r} part carrying the media",
        )
    title, folder_id, params = _ingest_options(dict(form))
    row = await run_in_threadpool(
        media.ingest_stream,
        conn,
        upload.file,
        upload.filename or "upload",
        title=title,
        folder_id=folder_id,
    )
    return row, params


async def _ingest_local_path(request: Request, conn: sqlite3.Connection) -> tuple[dict, dict]:
    """JSON `{"path": ...}`: a file already on this machine, left where it is."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # Narrowly these two: a malformed body is the caller's problem, and
        # anything else going wrong in here is ours and should surface as a 500.
        raise HTTPException(status_code=400, detail=f"body is not valid JSON: {exc}")
    if not isinstance(body, dict) or not body.get("path"):
        raise HTTPException(
            status_code=400,
            detail="send a file as multipart/form-data, or {\"path\": ...} for a file already on this machine",
        )

    src = Path(str(body["path"]))
    # The same roots the dialog's browse panel and path field honour
    # (scribe.fsbrowse): a path they would refuse is refused here too, or the
    # roots would guard one of two doors.
    if not fsbrowse.is_allowed(src, fsbrowse.allowed_roots(conn)):
        raise HTTPException(
            status_code=403,
            detail=f"{src} is outside the folders this app may read from; widen them under Settings",
        )
    title, folder_id, params = _ingest_options(body)
    try:
        row = await run_in_threadpool(
            media.ingest_path, conn, src, title=title, folder_id=folder_id
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no file at {src}")
    except (IsADirectoryError, PermissionError) as exc:
        # A directory is PermissionError on Windows and IsADirectoryError on
        # POSIX; a locked file is PermissionError on both. All three are the
        # caller's to fix, so none of them is a 500.
        raise HTTPException(status_code=400, detail=f"cannot read {src}: {exc}")
    return row, params


def _event_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "seq": row["seq"],
        "ts": row["ts"],
        "kind": row["kind"],
        "payload": json.loads(row["payload_json"]),
    }


def create_app(
    db_path: str | Path | None = None,
    start_supervisor: bool = True,
    start_watcher: bool | None = None,
) -> FastAPI:
    """Build the app; tests pass a tmp db_path and start_supervisor=False.

    `.env` is not read here. `python -m scribe` loads it before importing
    this module, which is the only moment it can still influence
    scribe.paths; an app built anywhere else (the tests) must not pull a
    developer's HF_TOKEN or SCRIBE_DATA_DIR into its process.

    ``start_watcher`` follows ``start_supervisor`` unless it is given, and
    that default is a decision rather than a convenience: `--no-supervisor`
    exists so a second instance can be run against the same data without
    disturbing the first, and a second watch-folder observer over the same
    folders would be a second ingest of every file that lands.
    """
    if start_watcher is None:
        start_watcher = start_supervisor

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        paths.ensure_dirs()
        applog.configure("web")
        applog.log("app.start", version=scribe.__version__, db=str(db_path or paths.DB_PATH),
                   supervisor=start_supervisor, watcher=start_watcher)
        conn = db.connect(db_path)
        db.migrate(conn)
        # Three questions about what a previous life of this process left
        # behind, asked in the order their answers cost: an orphaned job row,
        # an abandoned microphone session's chunks, and - inside the watcher's
        # own thread, because it hashes every file it walks - a folder that
        # was written to while the app was closed.
        supervisor.reconcile(conn)
        supervisor.sweep_stderr()
        recording.sweep(conn)
        app.state.conn = conn
        sup: supervisor.Supervisor | None = None
        if start_supervisor:
            sup = supervisor.Supervisor(db_path or paths.DB_PATH)
            sup.start()
        app.state.supervisor = sup
        watcher: watching.Watcher | None = None
        if start_watcher:
            watcher = watching.Watcher(db_path or paths.DB_PATH)
            watcher.start()
        app.state.watcher = watcher
        try:
            yield
        finally:
            if watcher is not None:
                watcher.stop()
            if sup is not None:
                sup.stop()
            conn.close()
            applog.log("app.stop")

    app = FastAPI(title="scribe", version=scribe.__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": scribe.__version__}

    @app.post("/api/media", status_code=201)
    async def add_media(request: Request) -> dict:
        """Take a recording in and queue the job that transcribes it.

        Known content is not re-stored - the existing row comes back with
        `deduped: true` - but it does get a new job, because asking to
        transcribe a file you already have is a perfectly good request and runs
        are append-only anyway.
        """
        conn = request.app.state.conn
        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("multipart/form-data"):
            row, params = await _ingest_upload(request, conn)
        else:
            row, params = await _ingest_local_path(request, conn)

        job_id = jobs.enqueue(
            conn, _INGEST_JOB_TYPE, media_id=row["id"], params=params
        )
        return {**row, "job_id": job_id}

    @app.get("/api/jobs")
    def list_jobs(request: Request, status: str | None = None) -> list[dict]:
        conn = request.app.state.conn
        with db.LOCK:
            if status:
                rows = conn.execute(
                    "SELECT * FROM job WHERE status=? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM job ORDER BY id DESC").fetchall()
        return [_job_out(conn, row) for row in rows]

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: int, request: Request) -> dict:
        conn = request.app.state.conn
        row = _get_job(conn, job_id)
        with db.LOCK:
            events = conn.execute(
                "SELECT * FROM job_event WHERE job_id=? ORDER BY seq DESC LIMIT 100",
                (job_id,),
            ).fetchall()
        out = _job_out(conn, row)
        out["events"] = [_event_out(e) for e in reversed(events)]
        return out

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, request: Request) -> dict:
        conn = request.app.state.conn
        _get_job(conn, job_id)
        jobs.request_cancel(conn, job_id)
        return _job_out(conn, _get_job(conn, job_id))

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: int, request: Request) -> dict:
        conn = request.app.state.conn
        row = _get_job(conn, job_id)
        if row["status"] not in jobs.RETRYABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"job {job_id} is {row['status']}; only "
                    f"{'/'.join(jobs.RETRYABLE_STATUSES)} jobs can be retried"
                ),
            )
        new_id = jobs.enqueue(
            conn,
            row["type"],
            media_id=row["media_id"],
            params=json.loads(row["params_json"] or "{}"),
            priority=row["priority"],
            retry_of=job_id,
        )
        return _job_out(conn, _get_job(conn, new_id))

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: int, request: Request, after: int = 0) -> list[dict]:
        conn = request.app.state.conn
        _get_job(conn, job_id)
        return jobs.events_after(conn, job_id, after)

    web.mount(app)
    # Localhost is not a login: a page on any site can post to 127.0.0.1
    # from the user's own browser. The guards refuse a foreign Host header
    # and a mutating request from another origin (scribe.guard).
    guard.install(app)
    return app
