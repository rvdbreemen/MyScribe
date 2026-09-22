"""The transcribe dialog: recordings in, queued jobs out.

Everything the dialog offers ends the same way - a media row from
`scribe.media` and a `transcribe` job from `jobs.enqueue` - and the only thing
it adds of its own is the options: language, tier, speakers, translate. Those
are one pydantic model, `scribe.options.TranscribeOptions`, whose
`to_params()` is the job's `params_json`. `POST /api/media` validates the same
fields through the same model - which is why it lives outside this package,
where the JSON spine can reach it without importing a router - so the JSON
door and the dialog cannot disagree about what "tier=max" means, and the
runner's stages read the keys they always did (`model`, `task`, `language`,
`diarize`, `*_speakers`).

Three decisions shape the routes:

* **Files are never judged here.** Every upload and every path goes through
  ingest and gets a job, whatever its extension; the probe stage says no,
  inside the job, where the reason is readable. Same rule as `POST /api/media`.
  The browse panel's `is_media` mark is a hint for the eye, not a gate.
* **A path on this machine is hardlinked, never copied or moved**
  (`media.ingest_path`), and only from under `fsbrowse`'s allowed roots -
  the home drive by default, the `fsbrowse_roots` setting otherwise. Outside
  them is a 403, whether the path came from the panel or was typed.
* **The answer is the library's own.** Both posts respond the way every
  library mutation does (`library._after_change`: the rows fragment with the
  sidebar out of band, or a 303 for a plain form) plus an `HX-Trigger:
  jobs-changed` header for whatever is watching the queue.

The options chosen on submit become the next dialog's defaults (`setting`
rows `default_language`, `default_tier`, `default_diarize`); translate and the
speaker counts are per-recording and are not remembered. Model names are
never spelled here: the tiers map through `transcribe.TIER_MODELS` (ADR-004),
and nothing under this package loads a model (ADR-001).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from scribe import applog, db, fsbrowse, jobs, media
from scribe.options import TranscribeOptions, parse_options
from scribe.stages import transcribe
from scribe.web import library, render

router = APIRouter()

JOB_TYPE = "transcribe"

# The multipart field the dialog's file input posts under.
FILES_FIELD = "files"

# The setting rows the dialog reads for its defaults and writes back on submit.
SETTING_LANGUAGE = "default_language"
SETTING_TIER = "default_tier"
SETTING_DIARIZE = "default_diarize"
# Auto-detect. This was "nl" until 2026-09-02, when two English recordings went
# through with language=nl on their runs: Whisper transcribed them correctly
# regardless - a language code is a bias, not a constraint - but the run row
# then claimed a language the audio did not have. Detection gets Dutch right
# too, and the stage pins whatever the first window detects for the rest of
# the file, so the only thing a fixed default bought was a wrong label.
DEFAULT_LANGUAGE: str | None = None


# --- defaults --------------------------------------------------------------------


def read_defaults(conn: sqlite3.Connection) -> TranscribeOptions:
    """The options the dialog opens with: the last ones submitted, else
    auto-detect, Turbo, speakers on. A stored "" is auto-detect too (that is
    how save_defaults writes it). A setting somebody edited into nonsense
    falls back to those rather than breaking the dialog."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT key, value FROM setting WHERE key IN (?, ?, ?)",
            (SETTING_LANGUAGE, SETTING_TIER, SETTING_DIARIZE),
        ).fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    try:
        return TranscribeOptions(
            language=stored.get(SETTING_LANGUAGE) or DEFAULT_LANGUAGE,
            tier=stored.get(SETTING_TIER, "turbo"),
            diarize=stored.get(SETTING_DIARIZE, "1"),
        )
    except ValidationError:
        return TranscribeOptions(language=DEFAULT_LANGUAGE)


def save_defaults(conn: sqlite3.Connection, options: TranscribeOptions) -> None:
    """Remember language, tier and diarize as the next dialog's defaults."""
    with db.LOCK:
        conn.executemany(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            [
                (SETTING_LANGUAGE, options.language or ""),
                (SETTING_TIER, options.tier),
                (SETTING_DIARIZE, "1" if options.diarize else "0"),
            ],
        )
        conn.commit()


def _save_one(conn: sqlite3.Connection, key: str, value: str) -> None:
    """One default row, and only that one.

    `save_defaults` writes all three in one statement, which is right for a
    dialog that submitted all three and wrong for anybody who answered one: a
    first-run sitting that saved the tier also wrote `default_diarize`, a row
    seven call sites rewrite and read, with a choice nobody made (ADR-015,
    Must Not; TASK-089.09).
    """
    with db.LOCK:
        conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def save_tier(conn: sqlite3.Connection, tier: str) -> None:
    """Remember the transcription tier, and nothing else."""
    _save_one(conn, SETTING_TIER, tier)


def save_diarize(conn: sqlite3.Connection, diarize: bool) -> None:
    """Remember whether speakers are separated by default, and nothing else."""
    _save_one(conn, SETTING_DIARIZE, "1" if diarize else "0")


# --- the dialog ------------------------------------------------------------------


def _fields(form: Mapping[str, Any]) -> dict[str, str]:
    """The text fields of a form, last value per name.

    A checkbox is posted only when it is ticked, so the dialog sends a hidden
    ``name=0`` ahead of each ``name=1`` checkbox; the later value is the
    answer. Uploads are not text and are left out.
    """
    out: dict[str, str] = {}
    for key in form.keys():
        values = [value for value in form.getlist(key) if isinstance(value, str)]
        if values:
            out[key] = values[-1]
    return out


def options_summary(options: TranscribeOptions) -> str:
    """One line saying what the collapsed options will do.

    The summary is the argument for collapsing them at all. Options hidden
    behind a closed panel are fine when a glance says which language, which
    model and whether speakers are on; without that they are not collapsed but
    concealed, and the user has to open the panel every time to be sure.

    Only the language name is looked up - the rest is short enough to spell
    out - and nothing is omitted for being the default, because "the default"
    is precisely what somebody opening this dialog does not yet know.
    """
    names = dict(transcribe.LANGUAGE_CHOICES)
    parts = [names.get(options.language or "", "Auto-detect")]
    parts.append("Maximaal" if options.tier == "max" else "Turbo")

    if not options.diarize:
        parts.append("no speakers")
    elif options.num_speakers:
        parts.append(f"{options.num_speakers} speakers")
    elif options.min_speakers and options.max_speakers:
        parts.append(f"{options.min_speakers}-{options.max_speakers} speakers")
    else:
        parts.append("speakers on")

    if options.translate:
        parts.append("translate to English")
    return " · ".join(parts)


SOURCES: tuple[str, ...] = ("upload", "path", "url")


def opening_source(request: Request) -> str:
    """Which of the three doors the dialog opens on.

    Three doors, all of them files: an upload, a path, a link. Recording has
    a dialog of its own (`record_dialog`) and is not a value here. An unknown
    value is not an error - it is a hand-edited URL, and the dialog opening on
    its usual door is the right answer to that."""
    asked = str(request.query_params.get("source") or "").strip().lower()
    return asked if asked in SOURCES else SOURCES[0]


def _dialog_context(request: Request, conn: sqlite3.Connection) -> dict:
    state = library._origin_state(request)
    defaults = read_defaults(conn)
    return {
        "options": defaults,
        "summary": options_summary(defaults),
        "languages": transcribe.LANGUAGE_CHOICES,
        "folders": library.folder_tree(conn),
        "folder": state.folder,
        "source": opening_source(request),
    }


@router.get("/transcribe", include_in_schema=False)
def dialog(request: Request) -> Response:
    """The dialog: a fragment for the library page's <dialog>, a page otherwise."""
    conn = request.app.state.conn
    ctx = _dialog_context(request, conn)
    if library._is_htmx(request):
        return render(request, "transcribe_dialog.html", **ctx)
    return render(request, "transcribe.html", **ctx)


@router.get("/record", include_in_schema=False)
def record_dialog(request: Request) -> Response:
    """The record dialog: the microphone with the same options and folder
    choice as the transcribe dialog, in a dialog of its own. Same context -
    `_transcribe_options.html` is shared - and the same two shapes."""
    conn = request.app.state.conn
    ctx = _dialog_context(request, conn)
    if library._is_htmx(request):
        return render(request, "record_dialog.html", **ctx)
    return render(request, "record.html", **ctx)


def _queued(request: Request, conn: sqlite3.Connection, count: int) -> Response:
    """Answer like every library mutation, say what happened, and tell the
    page the queue changed."""
    noun = "file" if count == 1 else "files"
    response = library._after_change(
        request, conn, flash=f"Queued {count} {noun} for transcription."
    )
    response.headers["HX-Trigger"] = "jobs-changed"
    return response


@router.post("/transcribe/upload", include_in_schema=False)
async def upload(request: Request) -> Response:
    """One or many files, each ingested and queued with the same options.

    Options and folder are validated before the first byte is filed, so a bad
    choice costs nothing; the same bytes twice dedupe to one media row and
    still get a job each, because asking for a re-run is a fair request.
    """
    conn = request.app.state.conn
    form = await request.form()
    uploads = [
        part for part in form.getlist(FILES_FIELD)
        if hasattr(part, "file") and part.filename
    ]
    if not uploads:
        raise HTTPException(status_code=400, detail="choose at least one file to upload")
    fields = _fields(form)
    options = parse_options(fields)
    folder_id = library._folder_id_from(conn, fields.get("folder_id"))
    params = options.to_params()

    for part in uploads:
        row = await run_in_threadpool(
            media.ingest_stream, conn, part.file, part.filename, folder_id=folder_id
        )
        applog.log("ingest.upload", filename=part.filename, media=row["id"],
                   bytes=row.get("size_bytes"), deduped=bool(row.get("deduped")))
        jobs.enqueue(conn, JOB_TYPE, media_id=row["id"], params=params)
    save_defaults(conn, options)
    return _queued(request, conn, len(uploads))


@router.post("/transcribe/path", include_in_schema=False)
async def add_path(request: Request) -> Response:
    """A file already on this machine: hardlinked into the store and queued."""
    conn = request.app.state.conn
    form = await request.form()
    fields = _fields(form)
    raw = (fields.get("path") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="give the path of a file on this machine")
    options = parse_options(fields)
    folder_id = library._folder_id_from(conn, fields.get("folder_id"))

    src = Path(raw)
    if not fsbrowse.is_allowed(src, fsbrowse.allowed_roots(conn)):
        raise HTTPException(
            status_code=403,
            detail=f"{src} is outside the folders this app may read from; widen them under Settings",
        )
    if src.is_dir():
        raise HTTPException(status_code=400, detail=f"{src} is a directory; pick a file in it")
    try:
        row = await run_in_threadpool(media.ingest_path, conn, src, folder_id=folder_id)
        applog.log("ingest.path", path=str(src), media=row["id"], bytes=row.get("size_bytes"))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no file at {src}")
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=f"cannot read {src}: {exc}")

    jobs.enqueue(conn, JOB_TYPE, media_id=row["id"], params=options.to_params())
    save_defaults(conn, options)
    return _queued(request, conn, 1)


# --- the browse panel ------------------------------------------------------------


def _fs_url(path: str | Path) -> str:
    return "/fs?path=" + quote(str(path), safe="")


def human_size(size: int) -> str:
    """1234567 -> '1.2 MB'. Decimal units, as file managers show them."""
    value = float(size)
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"  # pragma: no cover - the loop returns first


@router.get("/fs", include_in_schema=False)
def browse(request: Request, path: str = "") -> Response:
    """One level of the filesystem as a panel; no path lists the roots."""
    conn = request.app.state.conn
    roots = fsbrowse.allowed_roots(conn)
    raw = path.strip()
    if not raw:
        return render(
            request,
            "_fs_panel.html",
            path="",
            parent_url=None,
            dirs=[{"name": str(root), "url": _fs_url(root)} for root in roots],
            files=[],
        )

    if fsbrowse.has_traversal(raw):
        raise HTTPException(status_code=400, detail="'..' is not allowed in a browse path")
    if not fsbrowse.is_allowed(raw, roots):
        raise HTTPException(
            status_code=403,
            detail=f"{raw} is outside the folders this app may read from; widen them under Settings",
        )
    try:
        listing = fsbrowse.listdir(raw)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"no such directory: {raw}")
    except NotADirectoryError:
        raise HTTPException(status_code=400, detail=f"not a directory: {raw}")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"cannot list {raw}: {exc}")

    base = Path(listing["path"])
    parent = listing["parent"]
    return render(
        request,
        "_fs_panel.html",
        path=listing["path"],
        # Up from a root goes to the list of roots, not out of them.
        parent_url=_fs_url(parent) if parent and fsbrowse.is_allowed(parent, roots) else "/fs",
        dirs=[{"name": name, "url": _fs_url(base / name)} for name in listing["dirs"]],
        files=[
            {"name": name, "size": human_size(size), "is_media": is_media, "path": str(base / name)}
            for name, size, is_media in listing["files"]
        ],
    )
