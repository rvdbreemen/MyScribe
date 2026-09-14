"""The jobs board: what the pipeline is doing right now, and one job up close.

Everything on these pages is a `job` row (and its `job_event` rows) seen
through a template. The web process starts nothing and loads nothing
(ADR-001): Cancel and Retry are forms that post to the JSON job API already
in `scribe.app`, and the pages re-read the rows the runner child writes. The
runner's stage registry is imported for its names only - a doctor job gets
its one-step stepper, a transcribe job its six - never run.

Three decisions shape the routes:

* **The poll interval is part of the fragment.** `_jobs_fragment.html` is
  the element that polls, and it renders its own `hx-trigger`: every 2 s
  while `jobs.active_count` says something is running or queued, every 15 s
  otherwise. A swap that carries the next interval with it is how the board
  slows down by itself the moment the queue drains - and there is no page
  state to get stale, because the page state is the fragment. The same
  element also refreshes on `refresh` (what app.js fires after a Cancel or
  Retry has posted) and on `jobs-changed` (the header the transcribe dialog's
  responses carry).
* **Live detail is SSE, and only there.** The detail page renders the last
  200 events itself and hands the live log a stream that starts after them
  (`?last_event_id=`). The stream replays `job_event` rows as named frames
  with the row's `seq` as the event id - that is what makes a browser's
  automatic reconnect (`Last-Event-ID`) resume rather than repeat - and
  tails them every quarter second while the job is non-terminal. Progress
  within a stage is a column the runner updates, not an event, so the
  stream synthesises an id-less `progress` frame whenever the row's (status,
  stage, stage_progress) changes; the `end` frame is what lets the client
  close the connection, since `EventSource` reopens any stream that merely
  finishes.
* **Nothing here is marked safe.** Titles, params, error text and traces are
  rendered as text by the environment; the live log is written with
  `textContent` in app.js.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import AsyncIterator, Sequence

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse

from scribe import db, jobs, runner
from scribe.render import format_ts
from scribe.stages import transcribe, url_stage
from scribe.web import TIER_ICONS, library, render

router = APIRouter()

# The job type whose params the summary line describes; a doctor job has
# neither media nor options and gets no line.
TRANSCRIBE_JOB_TYPE = "transcribe"


def stage_names_for(job_type: str) -> tuple[str, ...]:
    """The stepper's steps for a job type, in the order the runner walks
    them. Read from the runner's registry so the board cannot show a stage
    the pipeline does not have - nor the transcribe stepper on a doctor job,
    which has one stage of its own. An unknown type has no steps."""
    return tuple(name for name, _ in runner.STAGES.get(job_type, ()))


# The transcribe pipeline's steps: what jobs.html describes, and the default
# a caller of stepper_steps gets.
STAGE_NAMES: tuple[str, ...] = stage_names_for(TRANSCRIBE_JOB_TYPE)

# A job in one of these will not change again; the stream ends on it.
TERMINAL_STATUSES = ("done", "failed", "cancelled", "interrupted")

# The board's history section and the detail page's event list are bounded;
# the stream is not, which is why the live log starts where the list stops.
HISTORY_LIMIT = 50
EVENT_LIMIT = 200

# The stream's clock. A quarter second reads as live and costs one indexed
# query per tick; a comment every fifteen seconds keeps an idle connection
# from being reaped while a long stage runs in silence; `retry` is what the
# browser waits before reconnecting after a drop.
SSE_POLL_SECONDS = 0.25
SSE_HEARTBEAT_SECONDS = 15.0
SSE_RETRY_MS = 2000

# Row kinds with a frame name of their own; everything else the stages emit
# (their per-stage summaries) is a `log` frame carrying the kind inside.
_FRAME_NAMES = {"stage": "stage", "error": "error"}

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, no-transform",
    "X-Accel-Buffering": "no",
}


# --- view models -----------------------------------------------------------------------


def stepper_steps(
    status: str | None, stage: str | None, names: Sequence[str] = STAGE_NAMES
) -> list[dict]:
    """Each of ``names`` with the state the stepper shows it in.

    Everything before the job's stage is done; the stage itself is active
    while the job runs, failed or stopped once it is not; everything after is
    still to do. A finished job has done all of them, and a queued one - no
    stage yet - none.
    """
    active = names.index(stage) if stage in names else -1
    steps = []
    for i, name in enumerate(names):
        if status == "done" or i < active:
            state = "done"
        elif i == active:
            if status == "running":
                state = "active"
            elif status == "failed":
                state = "failed"
            else:
                state = "stopped"
        else:
            state = "todo"
        steps.append({"name": name, "state": state})
    return steps


def params_summary(params: dict) -> str:
    """The job's options in one line: tier, language, translate, speakers."""
    model = str(params.get("model") or transcribe.DEFAULT_MODEL)
    turbo = "turbo" in model.lower()
    parts = [f"{TIER_ICONS['turbo']} Turbo" if turbo else f"{TIER_ICONS['max']} Maximaal"]
    parts.append(str(params.get("language") or "auto-detect language"))
    if params.get("task") == "translate":
        parts.append("translate to English")
    if params.get("diarize", True):
        if params.get("num_speakers"):
            parts.append(f"{params['num_speakers']} speakers")
        elif params.get("min_speakers") or params.get("max_speakers"):
            low = params.get("min_speakers") or "?"
            high = params.get("max_speakers") or "?"
            parts.append(f"{low}–{high} speakers")
        else:
            parts.append("speakers")
    else:
        parts.append("no speakers")
    return " · ".join(parts)


def _params(row: dict) -> dict:
    try:
        params = json.loads(row.get("params_json") or "{}")
    except json.JSONDecodeError:
        return {}
    return params if isinstance(params, dict) else {}


def _job_title(row: dict, params: dict) -> str:
    """What the board calls a job.

    A job with a recording is its recording. An `ingest_url` job has none yet,
    so it is its episode's name when a listing gave one, else the link itself:
    twenty jobs from one feed were twenty rows called "ingest_url job", which
    told nobody which download had failed. A stranger's text either way, and
    escaped by the template like every other title.
    """
    if row.get("media_title"):
        return str(row["media_title"])
    if row["type"] == url_stage.JOB_TYPE:
        entry = params.get(url_stage.ENTRY_KEY)
        title = str(entry.get("title") or "").strip() if isinstance(entry, dict) else ""
        return title or str(params.get("url") or "").strip() or f"{row['type']} job"
    return f"{row['type']} job"


# The rungs the board offers. Three, because a person moving a job means one of
# three things: ahead of everything, the ordinary case, or last. The bottom is
# url_stage.BULK_PRIORITY itself rather than a copy of -10, so a feed job the
# app queued and a job a person sent to the back land on the same number, and
# TASK-046 moving that constant moves this with it.
FIRST_PRIORITY = 10
PRIORITY_CHOICES: tuple[tuple[int, str], ...] = (
    (FIRST_PRIORITY, "First"),
    (0, "Normal"),
    (url_stage.BULK_PRIORITY, "Bulk"),
)
PRIORITY_LABELS = dict(PRIORITY_CHOICES)

def _neighbour(priority: int, *, up: bool) -> int | None:
    """The next rung up or down from where this job sits, or None at the end.

    Worked out against the ladder rather than by arithmetic, so a job on a
    number nobody offers (a scripted priority) still moves onto the ladder
    instead of drifting further off it.
    """
    rungs = sorted(PRIORITY_LABELS)
    if up:
        above = [p for p in rungs if p > priority]
        return above[0] if above else None
    below = [p for p in rungs if p < priority]
    return below[-1] if below else None


def job_view(conn: sqlite3.Connection, row: dict, now: float) -> dict:
    """A job row plus what the templates show: title, summary, stepper
    steps, elapsed and took, and for a running job the time left in its
    stage. ``row`` is a job joined to its media (see ``_JOB_SELECT``). The
    steps are the registry's for the job's type; the summary describes
    transcribe params and is empty for any other type."""
    params = _params(row)
    transcribing = row["type"] == TRANSCRIBE_JOB_TYPE
    view = {
        **row,
        "title": _job_title(row, params),
        "params": params,
        "summary": params_summary(params) if transcribing else "",
        "steps": stepper_steps(row["status"], row["stage"], stage_names_for(row["type"])),
        "elapsed": None,
        "took": None,
        "eta_left": None,
        "retryable": row["status"] in jobs.RETRYABLE_STATUSES,
        # The rung this job is on, and the ones either side of it, worked out
        # here so a button carries its target and the page needs no scripting.
        "priority_label": PRIORITY_LABELS.get(int(row["priority"] or 0), str(row["priority"])),
        "raise_to": _neighbour(int(row["priority"] or 0), up=True),
        "lower_to": _neighbour(int(row["priority"] or 0), up=False),
    }
    started, finished = row.get("started_at"), row.get("finished_at")
    if started and row["status"] == "running":
        view["elapsed"] = max(0.0, now - float(started))
    if started and finished:
        view["took"] = max(0.0, float(finished) - float(started))
    if row["status"] == "running":
        eta = jobs.job_eta(conn, row, transcribe.perf_model_for(params))
        if eta is not None:
            view["eta_left"] = max(0.0, eta * (1.0 - float(row["stage_progress"] or 0.0)))
    return view


# Every job read joins its media, so a view has the title and the duration
# without a query per row.
_JOB_SELECT = (
    "SELECT j.*, m.title AS media_title, m.duration AS media_duration"
    " FROM job j LEFT JOIN media m ON m.id = j.media_id"
)


def board_context(conn: sqlite3.Connection) -> dict:
    """Everything _jobs_fragment.html renders from.

    Queued rows come back in claim order (`jobs.claim_next`'s ORDER BY), so
    a row's position in the list is its position in the queue.
    """
    now = time.time()
    with db.LOCK:
        running = conn.execute(
            f"{_JOB_SELECT} WHERE j.status='running' ORDER BY j.started_at, j.id"
        ).fetchall()
        queued = conn.execute(
            f"{_JOB_SELECT} WHERE j.status='queued' ORDER BY j.priority DESC, j.queue_seq, j.id"
        ).fetchall()
        history = conn.execute(
            f"{_JOB_SELECT} WHERE j.status IN ('done','failed','cancelled','interrupted')"
            " ORDER BY j.finished_at DESC, j.id DESC LIMIT ?",
            (HISTORY_LIMIT,),
        ).fetchall()
    return {
        "running": [job_view(conn, dict(row), now) for row in running],
        "queued": [
            {**job_view(conn, dict(row), now), "position": i}
            for i, row in enumerate(queued, start=1)
        ],
        "history": [job_view(conn, dict(row), now) for row in history],
        "active": jobs.active_count(conn),
        "stages": STAGE_NAMES,
        "history_limit": HISTORY_LIMIT,
    }


def _get_job(conn: sqlite3.Connection, job_id: int) -> dict:
    with db.LOCK:
        row = conn.execute(f"{_JOB_SELECT} WHERE j.id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id}")
    return dict(row)


def event_summary(kind: str, payload: dict) -> str:
    """One line for an event: the stage's name, the error's last line, or
    the payload as ``key=value`` pairs. The trace itself goes to the error
    card, not the list."""
    if kind == "stage":
        return str(payload.get("name", ""))
    if kind == "log" and "text" in payload:
        # The transcribe stage's glimpse of the text: shown as the transcript
        # would show it, not as the key=value pairs it travels as.
        return f"[{format_ts(float(payload.get('at') or 0))}] {payload['text']}"
    if kind == "error":
        lines = str(payload.get("trace", "")).strip().splitlines()
        return lines[-1] if lines else ""
    return ", ".join(f"{key}={_short(value)}" for key, value in payload.items())


def _short(value: object, limit: int = 80) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"


def detail_context(conn: sqlite3.Connection, job_id: int) -> dict:
    """Everything _job_panel.html and job_detail.html render from."""
    row = _get_job(conn, job_id)
    view = job_view(conn, row, time.time())
    with db.LOCK:
        last_seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM job_event WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        retried_by = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM job WHERE retry_of=? ORDER BY id", (job_id,)
            ).fetchall()
        ]
    events = jobs.events_after(conn, job_id, max(0, last_seq - EVENT_LIMIT))
    trace = None
    for event in events:
        if event["kind"] == "error" and event["payload"].get("trace"):
            trace = str(event["payload"]["trace"])
    summarised = [
        {**event, "summary": event_summary(event["kind"], event["payload"])} for event in events
    ]
    return {
        "job": view,
        "stages": STAGE_NAMES,
        "events": summarised,
        # The table leaves the live-text lines out: they are the log, and a
        # table of two hundred sentences would bury the thirteen events that
        # say what the job did.
        "listed": [event for event in summarised if event["kind"] != "log"],
        "event_limit": EVENT_LIMIT,
        "last_seq": last_seq,
        "trace": trace,
        "retried_by": retried_by,
        "terminal": view["status"] in TERMINAL_STATUSES,
    }


# --- pages -----------------------------------------------------------------------------


@router.get("/jobs", include_in_schema=False)
def jobs_page(request: Request) -> Response:
    """The board; on an htmx request the polling fragment alone."""
    conn = request.app.state.conn
    ctx = board_context(conn)
    if library._is_htmx(request):
        return render(request, "_jobs_fragment.html", **ctx)
    return render(request, "jobs.html", **ctx)


@router.get("/jobs/fragment", include_in_schema=False)
def jobs_fragment(request: Request) -> Response:
    """What the board polls: the three sections, with the next interval."""
    conn = request.app.state.conn
    return render(request, "_jobs_fragment.html", **board_context(conn))


@router.get("/jobs/{job_id}", include_in_schema=False)
def job_detail(job_id: int, request: Request) -> Response:
    """One job; on an htmx request the panel alone (what a Cancel, a Retry or
    the stream's `end` refreshes)."""
    conn = request.app.state.conn
    ctx = detail_context(conn, job_id)
    if library._is_htmx(request):
        return render(request, "_job_panel.html", **ctx)
    return render(request, "job_detail.html", **ctx)


@router.get("/jobs/{job_id}/details", include_in_schema=False)
def job_details(job_id: int, request: Request) -> Response:
    """The parameters and event tables alone: what the tabs show, and what a
    Cancel, a Retry or the stream's `end` refreshes alongside the panel. Its
    own region so the status block above it stays still while a long event
    list is replaced."""
    conn = request.app.state.conn
    return render(request, "_job_details.html", **detail_context(conn, job_id))


# --- the stream ------------------------------------------------------------------------


def sse_frame(event: str, payload: object, event_id: int | None = None) -> str:
    """One SSE frame. ``event_id`` becomes the browser's ``Last-Event-ID``."""
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    body = json.dumps(payload, ensure_ascii=False, default=str)
    # json.dumps escapes newlines inside strings, so this is one data line.
    # Splitting anyway keeps the framing right rather than depending on it.
    for part in body.split("\n"):
        lines.append(f"data: {part}")
    return "\n".join(lines) + "\n\n"


def last_event_id(request: Request) -> int:
    """The sequence number the client already has, or 0.

    ``Last-Event-ID`` is what a browser's ``EventSource`` resends by itself
    on a reconnect; the query parameter is the manual equivalent, which the
    detail page uses to start the live log after the events it rendered.
    Anything unparsable means "from the beginning": replaying is the safe way
    to be wrong.
    """
    raw = request.headers.get("last-event-id")
    if raw is None:
        raw = request.query_params.get("last_event_id")
    if raw is None:
        return 0
    try:
        return max(int(str(raw).strip()), 0)
    except ValueError:
        return 0


def _job_state(conn: sqlite3.Connection, job_id: int) -> dict | None:
    with db.LOCK:
        row = conn.execute(
            "SELECT status, stage, stage_progress, error_code, cancel_requested"
            " FROM job WHERE id=?",
            (job_id,),
        ).fetchone()
    return None if row is None else dict(row)


def _event_frame(event: dict) -> str:
    name = _FRAME_NAMES.get(event["kind"], "log")
    payload = {
        "seq": event["seq"],
        "ts": event["ts"],
        "kind": event["kind"],
        "payload": event["payload"],
    }
    return sse_frame(name, payload, event_id=event["seq"])


async def job_event_stream(
    request: Request, conn: sqlite3.Connection, job_id: int, after_seq: int
) -> AsyncIterator[str]:
    """SSE frames for one job until it is terminal.

    Shape: ``retry``, then every `job_event` row after ``after_seq`` as a
    ``stage`` / ``error`` / ``log`` frame with the row's seq as its id; an
    id-less ``progress`` frame whenever the row's status, stage or progress
    changes (and once at the start, so a late client knows what it is looking
    at); ``: heartbeat`` comments through silence; and once the job is
    terminal, a last drain and an ``end`` frame.

    The order in the terminal case is drain, check status, drain again: the
    runner writes its last events and then its verdict, and a reader that
    checked the verdict first would occasionally lose the last event.

    Every database read goes through a thread: the shared connection is
    behind `db.LOCK`, and an event loop that waits on a lock waits for every
    other request too.
    """
    last_seq = after_seq
    last_state: tuple | None = None
    last_write = time.monotonic()

    yield f"retry: {SSE_RETRY_MS}\n\n"

    while True:
        if await request.is_disconnected():
            return

        try:
            events = await run_in_threadpool(jobs.events_after, conn, job_id, last_seq)
            state = await run_in_threadpool(_job_state, conn, job_id)
        except sqlite3.Error as exc:
            # Almost always the connection closing under us at shutdown. Say
            # so and stop; the client reconnects to the next process.
            yield sse_frame("error", {"detail": f"the job queue closed: {exc}"})
            return

        for event in events:
            last_seq = event["seq"]
            yield _event_frame(event)
            last_write = time.monotonic()

        if state is None:
            yield sse_frame("end", {"job_id": job_id, "status": None, "last_seq": last_seq})
            return

        current = (state["status"], state["stage"], state["stage_progress"], state["cancel_requested"])
        if current != last_state:
            last_state = current
            yield sse_frame(
                "progress",
                {
                    "status": state["status"],
                    "stage": state["stage"],
                    "stage_progress": state["stage_progress"],
                    "cancel_requested": bool(state["cancel_requested"]),
                },
            )
            last_write = time.monotonic()

        if state["status"] in TERMINAL_STATUSES:
            for event in await run_in_threadpool(jobs.events_after, conn, job_id, last_seq):
                last_seq = event["seq"]
                yield _event_frame(event)
            yield sse_frame(
                "end",
                {
                    "job_id": job_id,
                    "status": state["status"],
                    "error_code": state["error_code"],
                    "last_seq": last_seq,
                },
            )
            return

        if time.monotonic() - last_write >= SSE_HEARTBEAT_SECONDS:
            # A comment: valid SSE, ignored by EventSource, enough traffic to
            # keep an idle connection open.
            yield ": heartbeat\n\n"
            last_write = time.monotonic()

        await asyncio.sleep(SSE_POLL_SECONDS)


@router.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: int, request: Request) -> Response:
    """Live progress for one job as ``text/event-stream``.

    Reconnect with ``Last-Event-ID`` (or ``?last_event_id=``) to resume after
    that sequence number. The stream ends by itself when the job is terminal;
    see `job_event_stream`.
    """
    conn = request.app.state.conn
    # A 404 inside a stream is a 200 with an error frame, which is worse.
    _get_job(conn, job_id)
    return StreamingResponse(
        job_event_stream(request, conn, job_id, last_event_id(request)),
        media_type="text/event-stream",
        headers=dict(_SSE_HEADERS),
    )
