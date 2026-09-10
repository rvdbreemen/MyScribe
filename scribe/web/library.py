"""The library: every recording, its folders, and what has been done to it.

One page, one table. The sidebar's views (All, Recent, Uncategorized, Trash)
and the folder tree are predicates over the `media` table - there is no `view`
table in v1, and `trashed_at` is the whole of the trash. Nothing here reads
transcript rows beyond what the search page needs; the library shows a file's
title, when it arrived, how long it is, which model transcribed it and what
its latest job did, and every one of those is a column or a subquery away.

Three decisions shape the routes:

* **Every mutation is a POST with a form body, and every one answers three
  ways.** A plain browser post (scripting off) gets a 303 back to the library
  view it came from; an htmx post gets the re-rendered table with the sidebar
  swapped out of band, because the counts on it just changed; a post from
  app.js's data-refresh forms, which re-fetch their own panel afterwards and
  say so with an Accept without text/html, gets a 204 and nothing to throw
  away. Which view "it came from" is read from the request itself -
  `HX-Current-URL` for htmx, `Referer` otherwise - so a row's action forms
  carry no hidden state and cannot go stale.
* **The on-disk file is never renamed, moved or rewritten.** Rename changes
  `title`; move changes `folder_id`; trash sets `trashed_at`. The bytes stay
  under their content hash until a purge deletes the row - and only then is
  the file removed, which is safe because `sha256` is UNIQUE: no second row
  can be pointing at the same bytes.
* **Search never marks anything safe.** FTS5's `snippet()` is asked to wrap
  matches in two control characters no transcript contains, the result is cut
  into (text, highlighted) parts in Python, and the template escapes every
  part and puts the `<mark>` around the highlighted ones itself. The user's
  query is turned into quoted phrase tokens before it reaches MATCH, so a
  stray quote or an `AND` in it is text to search for, not syntax.

Grouping stays derived (ADR-003): this module touches `word` and `segment`
only through the FTS join, read-only, and creates no table of its own. The web
process loads no model (ADR-001): re-transcribe is `jobs.enqueue`, nothing more.
"""

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Annotated, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.responses import FileResponse, RedirectResponse, Response

from scribe import db, glossary, jobs, llm, options, paths
from scribe.llm import privacy
from scribe.stages import llm_stage
from scribe.stages import transcribe
from scribe.media import proxy_path_for
from scribe.web import render

router = APIRouter()

# The sidebar views that are predicates rather than folders.
VIEWS = ("recent", "uncategorized", "trash")
RECENT_LIMIT = 20

# The only ORDER BY clauses a `sort=` may select. The key is what the URL
# says; the clause is what SQLite runs. Anything else is a 400, not a guess.
SORTS: dict[str, str] = {
    "created_at": "m.created_at DESC, m.id DESC",
    "title": "m.title COLLATE NOCASE ASC, m.id ASC",
    "duration": "m.duration DESC, m.id DESC",
}
DEFAULT_SORT = "created_at"

# What a bulk form may ask for, and the job a re-transcribe enqueues. The
# export action is `scribe.web.exports_ui`'s: the same route, so the row
# checkboxes and the ids they post serve every action.
BULK_ACTIONS = ("move", "trash", "restore", "retranscribe", "export", "label")
TRANSCRIBE_JOB_TYPE = "transcribe"

# The most hits a search page shows. bm25 orders them, so the best come first.
SEARCH_LIMIT = 200

# Snippet markers: STX and ETX, which no transcript text contains. The
# template turns them into <mark>, escaping the text on either side.
_MARK_OPEN = "\x02"
_MARK_CLOSE = "\x03"
_MARKED = re.compile(f"{_MARK_OPEN}(.*?){_MARK_CLOSE}", re.DOTALL)
SNIPPET_TOKENS = 12

# Titles and folder names: a form can post anything; the database should not
# have to hold a megabyte of it.
MAX_NAME = 300


@dataclass(frozen=True)
class State:
    """Which slice of the library a request is looking at."""

    view: str | None = None  # one of VIEWS, or None for a folder / everything
    folder: int | None = None
    sort: str = DEFAULT_SORT
    q: str = ""  # title filter
    label: str = ""  # label name, matched case-insensitively

    @property
    def query(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.view:
            out["view"] = self.view
        if self.folder is not None:
            out["folder"] = str(self.folder)
        if self.sort != DEFAULT_SORT:
            out["sort"] = self.sort
        if self.q:
            out["q"] = self.q
        if self.label:
            out["label"] = self.label
        return out

    def url(self, **changes: object) -> str:
        """The library URL for this state with ``changes`` applied."""
        query = {**self.query, **{k: str(v) for k, v in changes.items() if v not in (None, "")}}
        return "/?" + urlencode(query) if query else "/"


def parse_state(params: Mapping[str, str], *, strict: bool = True) -> State:
    """A ``State`` from query parameters.

    ``strict`` raises a 400 on a value the page does not offer (a sort key
    that is not whitelisted, a view that does not exist); the lenient mode,
    used when recovering the view a POST came from, drops such values
    instead, because a bad Referer must never make a valid action fail.
    """
    view = (params.get("view") or "").strip() or None
    if view is not None and view not in VIEWS:
        if strict:
            raise HTTPException(status_code=400, detail=f"unknown view {view!r}")
        view = None

    folder: int | None = None
    raw = (params.get("folder") or "").strip()
    if raw:
        try:
            folder = int(raw)
        except ValueError:
            if strict:
                raise HTTPException(status_code=400, detail=f"folder must be an integer, got {raw!r}")

    sort = (params.get("sort") or "").strip() or DEFAULT_SORT
    if sort not in SORTS:
        if strict:
            raise HTTPException(
                status_code=400,
                detail=f"unknown sort {sort!r}; one of {', '.join(SORTS)}",
            )
        sort = DEFAULT_SORT

    return State(
        view=view,
        folder=folder,
        sort=sort,
        q=(params.get("q") or "").strip(),
        # Not whitelisted, unlike view and sort: those name things the page
        # offers, so a value outside the list came from a broken link. A label
        # is a word, and a word nothing carries deserves an empty table rather
        # than an error page.
        label=(params.get("label") or "").strip()[:MAX_NAME],
    )


# --- reading the library ---------------------------------------------------------


def folder_tree(conn: sqlite3.Connection) -> list[dict]:
    """Every folder, depth-first, with its depth for indentation.

    One recursive query; siblings sort by name, and a folder's descendants
    follow it directly because their sort path starts with its own.
    """
    with db.LOCK:
        rows = conn.execute(
            """
            WITH RECURSIVE tree(id, name, parent_id, depth, private, path) AS (
              SELECT id, name, parent_id, 0, private, lower(name) || '/' || id
                FROM folder WHERE parent_id IS NULL
              UNION ALL
              SELECT f.id, f.name, f.parent_id, t.depth + 1, f.private,
                     t.path || '/' || lower(f.name) || '/' || f.id
                FROM folder f JOIN tree t ON f.parent_id = t.id
            )
            SELECT id, name, parent_id, depth, private FROM tree ORDER BY path
            """
        ).fetchall()
    return [dict(row) for row in rows]


def folder_counts(conn: sqlite3.Connection) -> tuple[dict[int | None, int], dict[int | None, int]]:
    """(live files per folder, all files per folder), keyed by folder id.

    ``None`` is the Uncategorized bucket. The first mapping is what the
    sidebar shows; the second decides whether a folder may be deleted without
    force, because a file in the trash still belongs to its folder.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT folder_id, SUM(trashed_at IS NULL) AS live, COUNT(*) AS total"
            " FROM media GROUP BY folder_id"
        ).fetchall()
    live = {row["folder_id"]: int(row["live"]) for row in rows}
    total = {row["folder_id"]: int(row["total"]) for row in rows}
    return live, total


def label_counts(conn: sqlite3.Connection) -> list[dict]:
    """Every label something live carries, with how many carry it.

    Two rules it shares with the folder counts, for the same reason: the trash
    is excluded, because the sidebar counts what a click would show; and a
    label nothing carries is left out entirely, because a row that always
    answers "nothing here" is noise rather than information. A label with no
    recordings is not wrong - the vocabulary outlives a purge on purpose - it
    simply has nothing to offer this sidebar today.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT l.name AS name, COUNT(*) AS count"
            " FROM media_label ml"
            " JOIN label l ON l.id = ml.label_id"
            " JOIN media m ON m.id = ml.media_id AND m.trashed_at IS NULL"
            " GROUP BY l.id, l.name"
            " ORDER BY count DESC, l.name COLLATE NOCASE"
        ).fetchall()
    return [{"name": str(row["name"]), "count": int(row["count"])} for row in rows]


def trash_count(conn: sqlite3.Connection) -> int:
    with db.LOCK:
        return conn.execute(
            "SELECT COUNT(*) FROM media WHERE trashed_at IS NOT NULL"
        ).fetchone()[0]


def _where(state: State) -> tuple[str, list]:
    if state.view == "trash":
        clauses, args = ["m.trashed_at IS NOT NULL"], []
    else:
        clauses, args = ["m.trashed_at IS NULL"], []
        if state.view == "uncategorized":
            clauses.append("m.folder_id IS NULL")
        elif state.view is None and state.folder is not None:
            clauses.append("m.folder_id = ?")
            args.append(state.folder)
    if state.q:
        escaped = state.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("m.title LIKE ? ESCAPE '\\'")
        args.append(f"%{escaped}%")
    if state.label:
        # EXISTS rather than a JOIN: this builder feeds a SELECT that already
        # joins a job and a run, and a join here would multiply the row list by
        # however many links matched instead of narrowing it.
        clauses.append(
            "EXISTS (SELECT 1 FROM media_label ml JOIN label l ON l.id = ml.label_id"
            " WHERE ml.media_id = m.id AND l.name = ? COLLATE NOCASE)"
        )
        args.append(state.label)
    return " AND ".join(clauses), args


def media_rows(conn: sqlite3.Connection, state: State) -> list[dict]:
    """The table's rows for ``state``: media plus its current model and latest job."""
    where, args = _where(state)
    order = "m.created_at DESC, m.id DESC" if state.view == "recent" else SORTS[state.sort]
    limit = f" LIMIT {RECENT_LIMIT:d}" if state.view == "recent" else ""
    with db.LOCK:
        rows = conn.execute(
            f"""
            SELECT m.id, m.title, m.orig_name, m.folder_id, m.duration, m.size_bytes,
                   m.created_at, m.trashed_at, m.private,
                   (SELECT r.model FROM run r WHERE r.media_id = m.id AND r.is_current = 1
                     ORDER BY r.id DESC LIMIT 1) AS run_model,
                   j.id AS job_id, j.status AS job_status
              FROM media m
              LEFT JOIN job j ON j.id = (SELECT id FROM job WHERE media_id = m.id
                                          ORDER BY id DESC LIMIT 1)
             WHERE {where}
             ORDER BY {order}{limit}
            """,
            args,
        ).fetchall()
    return [dict(row) for row in rows]


def _heading(state: State, folders: list[dict]) -> str:
    if state.view == "recent":
        return "Recent"
    if state.view == "uncategorized":
        return "Uncategorized"
    if state.view == "trash":
        return "Trash"
    if state.label:
        return state.label
    if state.folder is not None:
        for folder in folders:
            if folder["id"] == state.folder:
                return folder["name"]
        raise HTTPException(status_code=404, detail=f"no folder with id {state.folder}")
    return "All files"


def sidebar_context(conn: sqlite3.Connection, state: State) -> dict:
    """What _sidebar.html (and the folder selects) render from: the tree and
    the counts. Three cheap queries, and no rows - the search page needs
    exactly this much of the library and nothing of its table."""
    folders = folder_tree(conn)
    live, total = folder_counts(conn)
    has_children = {f["parent_id"] for f in folders if f["parent_id"] is not None}
    return {
        "state": state,
        "folders": folders,
        "counts": {f["id"]: live.get(f["id"], 0) for f in folders},
        "nonempty": {f["id"] for f in folders if total.get(f["id"]) or f["id"] in has_children},
        "uncategorized_count": live.get(None, 0),
        "total_count": sum(live.values()),
        "trash_count": trash_count(conn),
        "labels": label_counts(conn),
    }


def library_context(conn: sqlite3.Connection, state: State) -> dict:
    """Everything library.html and its fragments render from: the sidebar's
    context plus the table's rows for ``state``."""
    ctx = sidebar_context(conn, state)
    return {
        **ctx,
        "heading": _heading(state, ctx["folders"]),
        "rows": media_rows(conn, state),
        "sort_links": {key: state.url(sort=key) for key in SORTS},
        "hits": None,
    }


# --- answering ---------------------------------------------------------------------


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _wants_html(request: Request) -> bool:
    """Whether the caller would render an HTML answer.

    A browser's form post says `text/html` (and `*/*`); so does curl. app.js's
    data-refresh forms say `application/json` alone, because they re-fetch
    their own panel after the post and would only throw a page away.
    """
    accept = request.headers.get("Accept", "*/*")
    return "text/html" in accept or "*/*" in accept


def _state_from_url(url: str | None) -> State:
    """The library state a URL of this app encodes, or the default.

    Only the library's own path counts: a Referer or HX-Current-URL that
    points anywhere else (the transcript page, another site) yields the
    default view rather than an attempt to parse it.
    """
    if not url:
        return State()
    parts = urlsplit(url)
    if parts.path != "/":
        return State()
    params = {key: values[0] for key, values in parse_qs(parts.query).items()}
    return parse_state(params, strict=False)


def _origin_state(request: Request) -> State:
    """The view the user was on when they posted."""
    if _is_htmx(request):
        return _state_from_url(request.headers.get("HX-Current-URL"))
    return _state_from_url(request.headers.get("Referer"))


def _library_page(request: Request, conn: sqlite3.Connection, state: State) -> Response:
    """The page, or on an htmx request the table alone."""
    ctx = library_context(conn, state)
    if _is_htmx(request):
        return render(request, "_media_rows.html", oob=False, **ctx)
    return render(request, "library.html", **ctx)


def _after_change(
    request: Request, conn: sqlite3.Connection, *, flash: str | None = None
) -> Response:
    """What every mutation answers: the fragment plus sidebar, a redirect, or
    nothing at all.

    A view that no longer exists - the user deleted the folder they were
    looking at - cannot be re-rendered, so the browser is sent to the library
    root instead: htmx gets an `HX-Redirect` and follows it as a full
    navigation, a plain post gets the 303 it was going to get anyway.
    ``flash`` is a line for the page's status region, carried out of band
    with the sidebar; a redirect has nowhere to put it and drops it.

    A caller that will not render HTML (app.js's data-refresh forms, which
    re-fetch their own panel afterwards) gets a 204: a 303 would make fetch
    follow it into a full library page that is thrown away unread.
    """
    if not _is_htmx(request) and not _wants_html(request):
        return Response(status_code=204)
    state = _origin_state(request)
    if state.folder is not None and not _folder_exists(conn, state.folder):
        state = State(sort=state.sort, q=state.q)
        if _is_htmx(request):
            return Response(status_code=200, headers={"HX-Redirect": state.url()})
    if _is_htmx(request):
        return render(
            request, "_media_rows.html", oob=True, flash=flash, **library_context(conn, state)
        )
    return RedirectResponse(state.url(), status_code=303)


def _folder_exists(conn: sqlite3.Connection, folder_id: int) -> bool:
    with db.LOCK:
        return conn.execute("SELECT 1 FROM folder WHERE id=?", (folder_id,)).fetchone() is not None


def _get_media(conn: sqlite3.Connection, media_id: int) -> sqlite3.Row:
    with db.LOCK:
        row = conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no media with id {media_id}")
    return row


def _get_folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row:
    with db.LOCK:
        row = conn.execute("SELECT * FROM folder WHERE id=?", (folder_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no folder with id {folder_id}")
    return row


def _folder_id_from(conn: sqlite3.Connection, value: str | None) -> int | None:
    """A form's folder field: empty means Uncategorized, otherwise an existing id."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        folder_id = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"folder_id must be an integer, got {raw!r}")
    _get_folder(conn, folder_id)
    return folder_id


def _clean_name(value: str | None, what: str) -> str:
    name = (value or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail=f"{what} cannot be empty")
    if len(name) > MAX_NAME:
        raise HTTPException(status_code=400, detail=f"{what} is longer than {MAX_NAME} characters")
    return name


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "on", "yes")


# --- pages ---------------------------------------------------------------------------


@router.get("/", include_in_schema=False)
def library(request: Request) -> Response:
    conn = request.app.state.conn
    state = parse_state(request.query_params)
    return _library_page(request, conn, state)


def fts_query(q: str) -> str:
    """The user's words as FTS5 phrase tokens, implicitly ANDed.

    Quoting each whitespace-separated term (with any inner quote doubled) is
    what makes ``don't``, ``AND`` and ``(`` searchable text instead of
    query syntax; the alternative is an OperationalError the user cannot fix.
    """
    return " ".join('"' + term.replace('"', '""') + '"' for term in q.split())


def marked_parts(snippet: str) -> list[tuple[str, bool]]:
    """A snippet with STX/ETX markers as (text, highlighted) parts, in order."""
    parts: list[tuple[str, bool]] = []
    for i, chunk in enumerate(_MARKED.split(snippet)):
        if chunk:
            parts.append((chunk, i % 2 == 1))
    return parts


def search_hits(conn: sqlite3.Connection, q: str) -> list[dict]:
    """Segments of current runs of live media matching ``q``, best first."""
    match = fts_query(q)
    if not match:
        return []
    with db.LOCK:
        rows = conn.execute(
            """
            SELECT m.id AS media_id, m.title, s.start,
                   snippet(segment_fts, 0, ?, ?, '…', ?) AS snippet
              FROM segment_fts
              JOIN segment s ON s.id = segment_fts.rowid
              JOIN run r ON r.id = s.run_id AND r.is_current = 1
              JOIN media m ON m.id = r.media_id AND m.trashed_at IS NULL
             WHERE segment_fts MATCH ?
             ORDER BY bm25(segment_fts), m.id, s.start
             LIMIT ?
            """,
            (_MARK_OPEN, _MARK_CLOSE, SNIPPET_TOKENS, match, SEARCH_LIMIT),
        ).fetchall()
    return [
        {
            "media_id": row["media_id"],
            "title": row["title"],
            "start": row["start"],
            "parts": marked_parts(row["snippet"]),
        }
        for row in rows
    ]


@router.get("/search", include_in_schema=False)
def search(request: Request, q: str = "") -> Response:
    conn = request.app.state.conn
    q = q.strip()
    if not q:
        return RedirectResponse("/", status_code=303)
    ctx = sidebar_context(conn, State())
    ctx.update(
        heading=f"Search: {q}",
        hits=search_hits(conn, q),
        search_q=q,
        search_limit=SEARCH_LIMIT,
        # This index is the one reader of the transcript the glossary's
        # correction layer does not reach (`glossary.any_corrections`), so the
        # page says so - but only in a library where there is a layer to be
        # wrong about.
        search_uncorrected=glossary.any_corrections(conn),
    )
    if _is_htmx(request):
        return render(request, "_search_hits.html", **ctx)
    return render(request, "library.html", **ctx)


# --- folders ---------------------------------------------------------------------------


@router.post("/folders", include_in_schema=False)
def create_folder(
    request: Request,
    name: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
) -> Response:
    conn = request.app.state.conn
    clean = _clean_name(name, "folder name")
    parent = _folder_id_from(conn, parent_id)
    with db.LOCK:
        conn.execute("INSERT INTO folder(name, parent_id) VALUES (?, ?)", (clean, parent))
        conn.commit()
    return _after_change(request, conn)


@router.post("/folders/{folder_id}/rename", include_in_schema=False)
def rename_folder(
    folder_id: int, request: Request, name: Annotated[str, Form()] = ""
) -> Response:
    conn = request.app.state.conn
    _get_folder(conn, folder_id)
    clean = _clean_name(name, "folder name")
    with db.LOCK:
        conn.execute("UPDATE folder SET name=? WHERE id=?", (clean, folder_id))
        conn.commit()
    return _after_change(request, conn)


@router.post("/folders/{folder_id}/delete", include_in_schema=False)
def delete_folder(
    folder_id: int, request: Request, force: Annotated[str, Form()] = ""
) -> Response:
    """Delete a folder; a non-empty one only with ``force``, which moves its
    files and subfolders to the parent (or to Uncategorized / the top level)."""
    conn = request.app.state.conn
    folder = _get_folder(conn, folder_id)
    with db.LOCK:
        files = conn.execute(
            "SELECT COUNT(*) FROM media WHERE folder_id=?", (folder_id,)
        ).fetchone()[0]
        subfolders = conn.execute(
            "SELECT COUNT(*) FROM folder WHERE parent_id=?", (folder_id,)
        ).fetchone()[0]
        if (files or subfolders) and not _truthy(force):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"folder {folder['name']!r} holds {files} file(s) and "
                    f"{subfolders} subfolder(s); delete with force=1 to move them "
                    "to the parent folder"
                ),
            )
        parent = folder["parent_id"]
        conn.execute("UPDATE media SET folder_id=? WHERE folder_id=?", (parent, folder_id))
        conn.execute("UPDATE folder SET parent_id=? WHERE parent_id=?", (parent, folder_id))
        conn.execute("DELETE FROM folder WHERE id=?", (folder_id,))
        conn.commit()
    return _after_change(request, conn)


# --- one file ----------------------------------------------------------------------------


@router.post("/media/{media_id}/rename", include_in_schema=False)
def rename_media(
    media_id: int, request: Request, title: Annotated[str, Form()] = ""
) -> Response:
    """The display title only; `orig_name` and the store path never change."""
    conn = request.app.state.conn
    _get_media(conn, media_id)
    clean = _clean_name(title, "title")
    with db.LOCK:
        conn.execute("UPDATE media SET title=? WHERE id=?", (clean, media_id))
        conn.commit()
    return _after_change(request, conn)


@router.post("/media/{media_id}/move", include_in_schema=False)
def move_media(
    media_id: int, request: Request, folder_id: Annotated[str, Form()] = ""
) -> Response:
    conn = request.app.state.conn
    _get_media(conn, media_id)
    target = _folder_id_from(conn, folder_id)
    with db.LOCK:
        conn.execute("UPDATE media SET folder_id=? WHERE id=?", (target, media_id))
        conn.commit()
    return _after_change(request, conn)


@router.post("/media/{media_id}/labels", include_in_schema=False)
def add_label(media_id: int, request: Request, name: Annotated[str, Form()] = "") -> Response:
    """Put a label on a recording, by hand.

    Not subject to `tasks.MAX_NEW_LABELS`. That ceiling exists because an
    automatic pass cannot be asked whether it is sure, and a model inventing a
    label per recording turns the vocabulary into a word cloud. A person typing
    a label has already decided; rationing that would be the app second-
    guessing its user.

    The label is matched case-insensitively against the vocabulary before it is
    created, so typing "Hacking" where "hacking" exists joins that word rather
    than forking it - the same rule `apply_labels` follows, for the same reason.
    """
    conn = request.app.state.conn
    _get_media(conn, media_id)
    clean = _clean_name(name, "label")
    now = time.time()
    with db.LOCK:
        conn.execute(
            "INSERT OR IGNORE INTO label(name, created_at) VALUES (?, ?)", (clean, now)
        )
        conn.execute(
            "INSERT OR IGNORE INTO media_label(media_id, label_id, source, created_at)"
            " SELECT ?, id, 'human', ? FROM label WHERE name = ? COLLATE NOCASE",
            (media_id, now, clean),
        )
        conn.commit()
    return _after_change(request, conn)


@router.post("/media/{media_id}/labels/remove", include_in_schema=False)
def remove_label(media_id: int, request: Request, name: Annotated[str, Form()] = "") -> Response:
    """Take a label off a recording.

    The link goes; the word stays. Another recording may still carry it, and a
    label briefly used by nothing is not wrong - `label_counts` simply stops
    offering it. Removing something that was not there is not an error: the
    page ends in the state that was asked for either way.
    """
    conn = request.app.state.conn
    _get_media(conn, media_id)
    clean = _clean_name(name, "label")
    with db.LOCK:
        conn.execute(
            "DELETE FROM media_label WHERE media_id = ? AND label_id ="
            " (SELECT id FROM label WHERE name = ? COLLATE NOCASE)",
            (media_id, clean),
        )
        conn.commit()
    return _after_change(request, conn)


@router.get("/media/{media_id}/status", include_in_schema=False)
def media_status(media_id: int, request: Request) -> Response:
    """One row's status cell, for the cell to fetch itself with.

    The library used to be a still photograph: a transcribe run finished and
    the row went on saying `running` until something else made the page
    re-render. Reloading the whole table on a timer would have cost an open
    row menu and any half-typed rename, so the cell is what refreshes, and
    only while there is something to watch - the fragment stops asking as soon
    as the status is terminal."""
    conn = request.app.state.conn
    _get_media(conn, media_id)
    with db.LOCK:
        row = conn.execute(
            """
            SELECT m.id, j.id AS job_id, j.status AS job_status
              FROM media m
              LEFT JOIN job j ON j.id = (SELECT id FROM job WHERE media_id = m.id
                                          ORDER BY id DESC LIMIT 1)
             WHERE m.id = ?
            """,
            (media_id,),
        ).fetchone()
    return render(request, "_media_status.html", row=dict(row))


@router.post("/media/{media_id}/trash", include_in_schema=False)
def trash_media(media_id: int, request: Request) -> Response:
    conn = request.app.state.conn
    _get_media(conn, media_id)
    _set_trashed(conn, [media_id], True)
    return _after_change(request, conn)


@router.post("/media/{media_id}/restore", include_in_schema=False)
def restore_media(media_id: int, request: Request) -> Response:
    conn = request.app.state.conn
    _get_media(conn, media_id)
    _set_trashed(conn, [media_id], False)
    return _after_change(request, conn)


def _set_trashed(conn: sqlite3.Connection, ids: list[int], trashed: bool) -> None:
    marks = ",".join("?" * len(ids))
    with db.LOCK:
        if trashed:
            conn.execute(
                f"UPDATE media SET trashed_at=? WHERE id IN ({marks}) AND trashed_at IS NULL",
                (time.time(), *ids),
            )
        else:
            conn.execute(f"UPDATE media SET trashed_at=NULL WHERE id IN ({marks})", ids)
        conn.commit()


@router.post("/media/{media_id}/purge", include_in_schema=False)
def purge_media(media_id: int, request: Request) -> Response:
    """Delete a trashed file for good: the row, everything that cascades from
    it (runs, words, jobs), and the stored bytes.

    Not while a job for it is queued or running: `job.media_id` cascades, so
    the delete would take the live job's row with it and leave the runner
    child burning GPU on rows that no longer exist, with nowhere to write its
    verdict. The check and the delete share one lock, so a job cannot be
    queued in between.
    """
    conn = request.app.state.conn
    row = _get_media(conn, media_id)
    if row["trashed_at"] is None:
        raise HTTPException(
            status_code=409,
            detail=f"media {media_id} is not in the trash; trash it first",
        )
    with db.LOCK:
        live = conn.execute(
            "SELECT id, status FROM job WHERE media_id=? AND status IN ('queued', 'running')"
            " ORDER BY id DESC LIMIT 1",
            (media_id,),
        ).fetchone()
        if live is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"media {media_id} has a {live['status']} job (job {live['id']});"
                    " cancel it and wait for it to finish before deleting the file"
                ),
            )
        conn.execute("DELETE FROM media WHERE id=?", (media_id,))
        conn.commit()
    # sha256 is UNIQUE on media, so with the row gone nobody else points at
    # these bytes - nor at the browser proxy the transcript view may have
    # transcoded under the same hash. A file something still holds open
    # cannot be unlinked on Windows; that is litter for a later sweep, not a
    # failed purge.
    for path in (paths.DATA_DIR / row["store_path"], proxy_path_for(row["sha256"])):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return _after_change(request, conn)


@router.get("/media/{media_id}/download", include_in_schema=False)
def download_media(media_id: int, request: Request) -> Response:
    """The original bytes, as an attachment under the original filename.

    Starlette's FileResponse streams the file and honours Range requests
    (206), so a browser can resume a download and a player can seek; nothing
    here reads the file into memory.
    """
    conn = request.app.state.conn
    row = _get_media(conn, media_id)
    path = paths.DATA_DIR / row["store_path"]
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail=f"the stored file for media {media_id} is missing"
        )
    media_type = mimetypes.guess_type(row["orig_name"])[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=row["orig_name"])


# --- many files ---------------------------------------------------------------------------


@router.post("/media/bulk", include_in_schema=False)
async def bulk(request: Request) -> Response:
    """One action for the checked rows: move, trash, restore, re-transcribe
    or export (which answers with the files, not the table)."""
    conn = request.app.state.conn
    form = await request.form()
    action = (form.get("action") or "").strip()
    if action not in BULK_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown bulk action {action!r}; one of {', '.join(BULK_ACTIONS)}",
        )
    ids = _parse_ids(form.getlist("ids") + form.getlist("ids[]"))
    _require_media(conn, ids)

    if action == "export":
        # Imported here: exports_ui builds on this module's helpers, so a
        # top-level import would be a cycle.
        from scribe.web import exports_ui

        return await exports_ui.bulk_export(request, conn, ids, form)
    if action == "move":
        target = _folder_id_from(conn, form.get("folder_id"))
        with db.LOCK:
            conn.execute(
                f"UPDATE media SET folder_id=? WHERE id IN ({','.join('?' * len(ids))})",
                (target, *ids),
            )
            conn.commit()
    elif action == "trash":
        _set_trashed(conn, ids, True)
    elif action == "restore":
        _set_trashed(conn, ids, False)
    elif action == "label":
        _enqueue_labels(conn, ids)
    else:
        for media_id in ids:
            jobs.enqueue(conn, TRANSCRIBE_JOB_TYPE, media_id=media_id, params=_last_params(conn, media_id))
    return _after_change(request, conn)


def _enqueue_labels(conn: sqlite3.Connection, ids: list[int]) -> None:
    """One `labels` pass per selected recording.

    This is how a library that predates the labels pass catches up: tick the
    rows, choose the action, and each becomes a job like any other - visible on
    the jobs board, cancellable, and paid for one at a time rather than in a
    sweep nobody can stop.

    Imported here rather than at the top: `ai_ui` builds on this module, so a
    top-level import would be a cycle. `exports_ui` is reached the same way.

    A recording with no transcript is skipped rather than queued: the pass
    reads words, and a job that can only fail is not worth a row on the board.
    The privacy pin is enforced *before* anything is queued, for the whole
    selection at once - queueing forty jobs and letting three of them fail on a
    refusal would spend real money to arrive at an error the check could see
    first.
    """
    from scribe.web import ai_ui

    provider_name = ai_ui.default_provider(conn)
    model = ai_ui.default_model(conn, provider_name)

    private = [media_id for media_id in ids if privacy.is_private(conn, media_id)]
    if private and not llm.provider_class(provider_name).is_local:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{len(private)} of the {len(ids)} chosen recordings are pinned private, and "
                f"{provider_name} is not a local provider. Choose a local provider in Settings, "
                "or leave the private recordings out of the selection."
            ),
        )

    with db.LOCK:
        has_words = {
            int(row["media_id"])
            for row in conn.execute(
                "SELECT DISTINCT r.media_id AS media_id FROM run r"
                f" WHERE r.is_current = 1 AND r.media_id IN ({','.join('?' * len(ids))})",
                ids,
            )
        }
    for media_id in ids:
        if media_id not in has_words:
            continue
        jobs.enqueue(
            conn,
            llm_stage.JOB_TYPE,
            media_id,
            {"media_id": media_id, "kind": "labels", "provider": provider_name, "model": model},
        )


def _parse_ids(values: list) -> list[int]:
    ids: list[int] = []
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        try:
            ids.append(int(raw))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"ids must be integers, got {raw!r}")
    if not ids:
        raise HTTPException(status_code=400, detail="select at least one file")
    return list(dict.fromkeys(ids))


def _require_media(conn: sqlite3.Connection, ids: list[int]) -> None:
    with db.LOCK:
        found = {
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM media WHERE id IN ({','.join('?' * len(ids))})", ids
            ).fetchall()
        }
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"no media with id {missing}")


def _last_params(conn: sqlite3.Connection, media_id: int) -> dict:
    """The params of the media's most recent transcribe job, or none for a first run.

    "The media's last params" are read from the job, not from the current
    run, for two reasons. A job's `params_json` is the request as the user
    made it (model, language, diarize...), the shape `jobs.enqueue` takes;
    a run's `params_json` is what the transcribe stage recorded about the
    engine's effective settings (requested_model, vad_filter, device...) and
    is not a request. And the most recent job is what the user last asked
    for, even when it failed: "transcribe again" after a CUDA_OOM should
    retry that request, not silently fall back to the older run's.

    The type filter is the whole point of the query and used to be absent.
    When this was written a recording only ever had transcribe jobs; phases 5
    and 6 gave it language-model and correction jobs too, and on 2026-09-03
    asking the AI panel a question and then pressing re-transcribe handed a
    chat request's params to the transcribe stage - which dutifully asked
    HuggingFace for the chat model and failed with a 404. Params are not
    interchangeable between job types, so this reads only its own kind.
    """
    with db.LOCK:
        row = conn.execute(
            "SELECT params_json FROM job WHERE media_id=? AND type=? ORDER BY id DESC LIMIT 1",
            (media_id, TRANSCRIBE_JOB_TYPE),
        ).fetchone()
    if row is None:
        return {}
    try:
        params = json.loads(row["params_json"] or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(params, dict):
        return {}

    # Two sieves, because the type filter alone was not enough. The row that
    # started this was already a transcribe job carrying a chat request's
    # params, so "the last transcribe request" copied it forward and every
    # re-transcribe queued another doomed job. Keep only the keys a transcribe
    # job has (options.PARAM_KEYS), and drop a model that is not a checkpoint -
    # `model` is a valid key on both kinds of job, which is precisely why the
    # name got this far. Dropping it falls back to the default tier, so a
    # poisoned row heals instead of repeating itself.
    kept = {key: value for key, value in params.items() if key in options.PARAM_KEYS}
    if "model" in kept and not transcribe.is_speech_model(str(kept["model"])):
        kept.pop("model")
    return kept
