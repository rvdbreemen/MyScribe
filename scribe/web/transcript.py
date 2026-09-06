"""The transcript view: one recording, its words, and the audio they came from.

The page is `word` rows seen through `scribe.render` (ADR-003). The route
reads the current run's words and its `speaker_label` rows, hands them to
`render.paragraphs`, and the template writes a heading per paragraph, a
timestamp per sentence and a span per word. Nothing grouped is read from or
written to the database; a rename or a reassignment (Task 7) changes one row
and the next render regroups.

Every number the player needs is rendered into the markup - a word's index and
its start and end, a sentence's start, the storage keys for the resume position
and the timestamp toggle - so app.js reads attributes and computes nothing.

The two edits the view allows are the two ADR-003 leaves room for, and each
is one statement:

* **Renaming a speaker** upserts one `speaker_label` row for the current run
  (UNIQUE on run and cluster, so a second rename updates rather than
  duplicates). Every heading of that cluster follows on the next render;
  no word is touched. A colour may ride along and is kept across a rename
  that sends none.
* **Reassigning a range of words** is `UPDATE word SET speaker=?,
  edited_by_user=1 WHERE run_id=? AND idx BETWEEN ? AND ?`. The target is a
  cluster the run already knows, or ``new``, which mints the next `USER_<n>`
  label with the name the user gave in the same transaction. The paragraphs
  regroup themselves around the new speaker on the next render, because
  they were never stored.

  ``edited_by_user`` says a person chose these words' *speaker*, and nothing
  more. It is not a statement about what they say: a reader that wants "a
  person retyped this word" wants `word.text_edited_by_user` (schema v9),
  which no route writes, because no route edits word text. The glossary's
  correction pass reads that one; while it read this one, reassigning
  speakers silently deleted the corrections over the range on the next
  re-run. See db.py's v9 comment.

Both answer an htmx post with the re-rendered panel and a plain post with a
redirect to the page, the way the library's actions do.

The audio route serves the bytes a browser can play:

* **A playable container is served as it is.** mp3, wav, m4a, aac, ogg, opus,
  flac, webm and mp4 go out as a `FileResponse`, which streams from disk and
  honours `Range` (206) - what a player needs to seek and what a four-hour
  file needs to never be read into memory.
* **Anything else gets a one-time AAC proxy.** A Matroska or AVI original is
  transcoded once with ffmpeg (`-vn -c:a aac -b:a 96k -movflags +faststart`)
  to `MEDIA_DIR/proxy/<sha256>.m4a`, keyed by the content hash like the store
  itself, and served from there ever after. The proxy is written to a `.part`
  name and renamed into place, so a request that arrives mid-transcode never
  finds a half-written file wearing the proxy's name - and it waits on a
  per-proxy lock for that transcode instead of starting a second one, which
  is what a browser's two requests for one `<audio>` would otherwise do.
* **The proxy must be the same audio.** Timestamps came from the original;
  a proxy whose ffprobe duration is not within 50 ms of the original's would
  put every highlight late or early, so it is refused - the route logs why
  and serves the original anyway, which the browser may or may not manage.
  A refused proxy never reaches its final name, so a later request tries
  again rather than trusting a file that already failed once.

The file actions in the right rail (rename, move, trash, restore) are the
library's own routes, posted through app.js's delegated forms with
`data-refresh="#transcript-panel"`: the panel re-fetches itself and the
player, which lives outside it, keeps playing.

Nothing here loads a model (ADR-001): ffmpeg and ffprobe are the only
subprocesses, and they run in the request's threadpool worker.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Annotated, Iterable

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.responses import FileResponse, RedirectResponse, Response

from scribe import db, glossary, paths, render
from scribe.media import proxy_path_for
from scribe.stages import probe
from scribe.web import library, render as render_page

router = APIRouter()
log = logging.getLogger(__name__)

# Containers a browser's <audio> element plays from the original file, with
# the type it is served under. Anything else is transcoded to the proxy.
PLAYABLE: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}

# The proxy: AAC in an MP4 box. Where it lives is the store's business
# (`media.proxy_path_for`), so the purge knows to remove it with the row.
PROXY_MEDIA_TYPE = "audio/mp4"
PROXY_BITRATE = "96k"

# How far the proxy's duration may sit from the original's before the
# timestamps would visibly drift: the spec's 50 ms.
PROXY_TOLERANCE_SECONDS = 0.05

# How much of ffmpeg's complaint travels with the exception.
STDERR_TAIL = 800

# The speeds the player bar offers, in order. 1 is the one marked at load.
SPEEDS: tuple[str, ...] = ("0.75", "1", "1.25", "1.5", "2")

# The localStorage keys app.js uses. Rendered into the markup, not built there.
RESUME_KEY_PREFIX = "scribe:resume:"
HIDE_TS_KEY = "scribe:hide-ts"

# Speakers the user adds are USER_<n>, numbered from 1 per run; pyannote's are
# SPEAKER_<nn>. render.speaker_display shows a USER_ label as it is when it
# has no name, which is why one is never created without one.
USER_PREFIX = "USER_"
_USER_LABEL = re.compile(r"USER_(\d+)")

# The `speaker` a reassignment sends to mean "one that is not in the list".
# No cluster label is ever spelled like this, so it cannot collide with one.
NEW_SPEAKER = "new"

# A speaker colour as a form sends it: #rrggbb. Stored lower-cased.
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


class ProxyError(Exception):
    """The proxy could not be made, or was not the same audio as the original."""


# --- reading the transcript ------------------------------------------------------------


def current_run(conn: sqlite3.Connection, media_id: int) -> dict | None:
    with db.LOCK:
        row = conn.execute(
            "SELECT * FROM run WHERE media_id=? AND is_current=1 ORDER BY id DESC LIMIT 1",
            (media_id,),
        ).fetchone()
    return None if row is None else dict(row)


def run_words(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """The run's words in transcript order, as dicts for the template.

    ``text`` is the glossary's corrected text where there is one and the stored
    text everywhere else - one LEFT JOIN, never a rewrite (ADR-003). What the
    word says in the database is what Whisper produced, and it comes back the
    moment the correction rows are deleted. ``corrected_from`` carries the
    original so the page can say what it used to say, and is None for a word
    nothing was done to.
    """
    with db.LOCK:
        rows = conn.execute(
            f"SELECT w.idx, w.start, w.end, {glossary.CORRECTED_TEXT} AS text,"
            " w.probability, w.speaker, w.edited_by_user,"
            " c.original AS corrected_from, c.rule AS corrected_rule"
            f" FROM word w {glossary.CORRECTION_JOIN}"
            " WHERE w.run_id=? ORDER BY w.idx",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _label_key(label: str) -> tuple[str, int]:
    """Sort key that puts SPEAKER_00..SPEAKER_10 and USER_1..USER_10 in
    numeric order rather than string order."""
    head, _, tail = label.rpartition("_")
    return (head, int(tail)) if tail.isdigit() else (label, -1)


def run_speakers(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Every speaker the run knows, in label order.

    A speaker is a cluster its words carry or a label somebody named - the
    union, so a speaker whose every word was reassigned away is still offered
    as a target and can be given words back. Each is
    ``{cluster, label, name, color, words}``: ``label`` is the stored name or
    None, ``name`` what to show (`render.speaker_display`), ``words`` how many
    the cluster has now.
    """
    with db.LOCK:
        counted = conn.execute(
            "SELECT speaker, COUNT(*) AS n FROM word"
            " WHERE run_id=? AND speaker IS NOT NULL GROUP BY speaker",
            (run_id,),
        ).fetchall()
        labelled = conn.execute(
            "SELECT cluster_label, display_name, color FROM speaker_label WHERE run_id=?",
            (run_id,),
        ).fetchall()
    words = {row["speaker"]: int(row["n"]) for row in counted}
    labels = {row["cluster_label"]: row for row in labelled}
    names = {cluster: row["display_name"] for cluster, row in labels.items()}
    return [
        {
            "cluster": cluster,
            "label": names.get(cluster),
            "name": render.speaker_display(names, cluster),
            "color": labels[cluster]["color"] if cluster in labels else None,
            "words": words.get(cluster, 0),
        }
        for cluster in sorted(set(words) | set(labels), key=_label_key)
    ]


def latest_job(conn: sqlite3.Connection, media_id: int) -> dict | None:
    with db.LOCK:
        row = conn.execute(
            "SELECT id, status, stage, error_code FROM job WHERE media_id=?"
            " ORDER BY id DESC LIMIT 1",
            (media_id,),
        ).fetchone()
    return None if row is None else dict(row)


# The formats worth a single click in the rail. Every format MyScribe writes
# is in the dialog; these four are the ones a person downloads without wanting
# to decide anything first - a document, a subtitle, plain words, and the text
# with its structure. The labels say what the file is for, not what it is,
# because the extension beside them already says that.
QUICK_EXPORTS: tuple[tuple[str, str], ...] = (
    ("txt", "Plain text"),
    ("docx", "Word document"),
    ("srt", "Subtitles"),
    ("md", "Markdown"),
)


def page_context(conn: sqlite3.Connection, media_id: int) -> dict:
    """Everything transcript.html and _transcript_panel.html render from.

    The AI context is merged in here rather than fetched by the panel itself,
    so the rail's actions and the six answer panels are drawn by the request
    that draws the words. The import is inside the function because
    ``ai_ui`` imports this module for the current run and the player's
    constants; one lazy import breaks the cycle, and after the first call it
    is a dictionary lookup.
    """
    from scribe.web import ai_ui
    from scribe.web import transcribe_dialog  # human_size; imported late, like ai_ui

    media = dict(library._get_media(conn, media_id))
    run = current_run(conn, media_id)
    paragraphs: list[render.Paragraph] = []
    speakers: list[dict] = []
    if run is not None:
        speakers = run_speakers(conn, run["id"])
        labels = {s["cluster"]: s["label"] for s in speakers if s["label"] is not None}
        paragraphs = render.paragraphs(run_words(conn, run["id"]), labels)
    return {
        "media": media,
        "run": run,
        "paragraphs": paragraphs,
        "speakers": speakers,
        "colors": {s["cluster"]: s["color"] for s in speakers if s["color"]},
        "new_speaker": NEW_SPEAKER,
        "job": latest_job(conn, media_id),
        "folders": library.folder_tree(conn),
        "quick_exports": QUICK_EXPORTS,
        "audio_size": transcribe_dialog.human_size(media["size_bytes"]) if media.get("size_bytes") else "",
        "audio_url": f"/media/{media_id}/audio",
        "speeds": SPEEDS,
        "resume_key": f"{RESUME_KEY_PREFIX}{media_id}",
        "hide_ts_key": HIDE_TS_KEY,
        **ai_ui.panel_context(
            conn,
            media_id,
            has_transcript=run is not None,
            # Which words the page is showing, so an answer made from an
            # earlier transcription of this recording says so instead of
            # passing for an answer about what is on the screen.
            run_id=run["id"] if run is not None else None,
        ),
    }


@router.get("/media/{media_id}", include_in_schema=False)
def transcript_page(media_id: int, request: Request) -> Response:
    """The transcript; on an htmx request the panel alone (what a rail
    action's `refresh` re-fetches, leaving the player untouched)."""
    conn = request.app.state.conn
    ctx = page_context(conn, media_id)
    if library._is_htmx(request):
        return render_page(request, "_transcript_panel.html", **ctx)
    return render_page(request, "transcript.html", **ctx)


# --- speakers: naming them, and moving words between them -------------------------------


def _require_run(conn: sqlite3.Connection, media_id: int) -> dict:
    """The current run, or a 409: a media without one has no speakers to
    name and no words to move."""
    run = current_run(conn, media_id)
    if run is None:
        raise HTTPException(
            status_code=409, detail=f"media {media_id} has no transcript yet"
        )
    return run


def _clean_color(value: str | None) -> str | None:
    """A form's colour field: empty means "leave it", otherwise #rrggbb."""
    raw = (value or "").strip()
    if not raw:
        return None
    if not _COLOR.fullmatch(raw):
        raise HTTPException(
            status_code=400, detail=f"color must be #rrggbb, got {raw!r}"
        )
    return raw.lower()


def _parse_idx(value: str | None, what: str) -> int:
    raw = (value or "").strip()
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{what} must be an integer, got {raw!r}")


def _parse_range(conn: sqlite3.Connection, run_id: int, from_idx: str, to_idx: str) -> tuple[int, int]:
    """``(first, last)``, inclusive, both within the run's words and in order.

    Word idx runs 0..count-1 without gaps (the transcribe stage numbers words
    as it collects them), so the count is the whole bound. A back-to-front
    range is refused rather than swapped: app.js sends them ordered, and a
    pair that is not is a bug worth seeing.
    """
    first, last = _parse_idx(from_idx, "from_idx"), _parse_idx(to_idx, "to_idx")
    with db.LOCK:
        count = conn.execute("SELECT COUNT(*) FROM word WHERE run_id=?", (run_id,)).fetchone()[0]
    if not 0 <= first <= last < count:
        raise HTTPException(
            status_code=400,
            detail=(
                f"the range {first}-{last} is not within the run's {count} words"
                f" (0-{count - 1}, from_idx <= to_idx)"
            ),
        )
    return first, last


def next_user_label(clusters: Iterable[str]) -> str:
    """The next unused ``USER_<n>`` among ``clusters``, counting from 1."""
    taken = [int(m.group(1)) for c in clusters if (m := _USER_LABEL.fullmatch(c))]
    return f"{USER_PREFIX}{max(taken, default=0) + 1}"


def _upsert_label(
    conn: sqlite3.Connection, run_id: int, cluster: str, name: str, color: str | None
) -> None:
    """One label row per (run, cluster): insert it, or update the name and -
    when one was sent - the colour. Under the caller's lock, no commit."""
    conn.execute(
        "INSERT INTO speaker_label(run_id, cluster_label, display_name, color)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(run_id, cluster_label) DO UPDATE SET"
        "   display_name = excluded.display_name,"
        "   color = COALESCE(excluded.color, speaker_label.color)",
        (run_id, cluster, name, color),
    )


def _after_edit(
    request: Request, conn: sqlite3.Connection, media_id: int, **extra: Any
) -> Response:
    """The re-rendered panel for htmx; a redirect to the page for a plain post.

    ``extra`` rides into the panel's context: what one edit wants to say on
    top of the page (an offer to repeat itself, for instance) without the
    page's context having to know about every edit."""
    if library._is_htmx(request):
        return render_page(
            request, "_transcript_panel.html", **page_context(conn, media_id), **extra
        )
    return RedirectResponse(f"/media/{media_id}", status_code=303)


@router.post("/media/{media_id}/words/{idx}/correct", include_in_schema=False)
def correct_word(
    media_id: int,
    idx: int,
    request: Request,
    text: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "one",
) -> Response:
    """Type over one misheard word - and, if asked, over every word that says
    the same.

    A correction row, never a rewrite (ADR-003): `word.text` keeps what
    Whisper produced, exports and the view read through the layer, and an
    empty text takes the row away. ``scope=one`` fixes this word and answers
    with an offer naming how many other words say the same; ``scope=all``
    fixes them all. The glossary pass leaves these rows alone.
    """
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    run = _require_run(conn, media_id)
    if scope not in ("one", "all"):
        raise HTTPException(status_code=400, detail=f"scope is 'one' or 'all', not {scope!r}")
    typed = text.strip()
    if len(typed) > MAX_WORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"a word is at most {MAX_WORD_LENGTH} characters")
    try:
        if scope == "all":
            changed = glossary.correct_same(conn, run["id"], idx, typed)
            return _after_edit(
                request, conn, media_id,
                word_flash=f"{changed} words now say “{typed}”." if changed else None,
            )
        glossary.correct_word(conn, run["id"], idx, typed)
        others = glossary.same_word(conn, run["id"], idx) if typed else []
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    offer = None
    if others:
        offer = {
            "idx": idx,
            "text": typed,
            "others": len(others),
            "was": _core_text(others[0]["text"]),
            "url": f"/media/{media_id}/words/{idx}/correct",
        }
    return _after_edit(request, conn, media_id, word_offer=offer)


MAX_WORD_LENGTH = 100
"""Typed over one word. Longer than any word, shorter than a paragraph
somebody pasted into the wrong box."""


def _core_text(text: str) -> str:
    return glossary._core(text)


@router.post("/media/{media_id}/speakers/{cluster}/rename", include_in_schema=False)
def rename_speaker(
    media_id: int,
    cluster: str,
    request: Request,
    display_name: Annotated[str, Form()] = "",
    color: Annotated[str, Form()] = "",
) -> Response:
    """Name a speaker of the current run. Applied at render time to every
    heading of the cluster; the words keep their cluster label and text."""
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    run = _require_run(conn, media_id)
    if cluster not in {s["cluster"] for s in run_speakers(conn, run["id"])}:
        raise HTTPException(
            status_code=404, detail=f"no speaker {cluster!r} in the transcript of media {media_id}"
        )
    name = library._clean_name(display_name, "speaker name")
    chosen = _clean_color(color)
    with db.LOCK:
        _upsert_label(conn, run["id"], cluster, name, chosen)
        conn.commit()
    return _after_edit(request, conn, media_id)


@router.post("/media/{media_id}/speakers/apply", include_in_schema=False)
async def apply_speaker_names(media_id: int, request: Request) -> Response:
    """Name several speakers at once, from the suggestions the "Who is
    speaking" answer made.

    The form carries one checkbox per suggestion (`apply=<cluster>`) and one
    text field per cluster (`name:<cluster>`), so a person unticks what the
    model got wrong and edits what it nearly got right before anything is
    written. Only ticked clusters are touched; an unticked one keeps whatever
    name it had. Nothing else about the answer row changes: it stays the
    record of what the model said, and `speaker_label` is what was accepted.
    """
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    run = _require_run(conn, media_id)
    form = await request.form()
    wanted = [str(c) for c in form.getlist("apply")]
    known = {s["cluster"] for s in run_speakers(conn, run["id"])}
    unknown = [c for c in wanted if c not in known]
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"no speaker {unknown[0]!r} in the transcript of media {media_id}",
        )
    names = {
        cluster: library._clean_name(str(form.get(f"name:{cluster}") or ""), "speaker name")
        for cluster in wanted
    }
    with db.LOCK:
        for cluster, name in names.items():
            _upsert_label(conn, run["id"], cluster, name, None)
        conn.commit()
    response = _after_edit(request, conn, media_id)
    # The suggestions panel shows the name each cluster has now next to the
    # suggestion; it lives outside the transcript panel, so it is told.
    response.headers["HX-Trigger"] = "speakers-applied"
    return response


@router.post("/media/{media_id}/words/reassign", include_in_schema=False)
def reassign_words(
    media_id: int,
    request: Request,
    from_idx: Annotated[str, Form()] = "",
    to_idx: Annotated[str, Form()] = "",
    speaker: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
) -> Response:
    """Give the words ``from_idx..to_idx`` (inclusive) to ``speaker``.

    ``speaker`` is a cluster the run knows, or ``new`` with a ``display_name``
    for the speaker to create. The words' text is never touched; only their
    cluster changes, and ``edited_by_user`` records that a person chose that
    cluster - a fact about speakers, not about spelling (see the module
    docstring, and `word.text_edited_by_user` for the other one).
    """
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    run = _require_run(conn, media_id)
    first, last = _parse_range(conn, run["id"], from_idx, to_idx)
    known = [s["cluster"] for s in run_speakers(conn, run["id"])]

    target = (speaker or "").strip()
    name: str | None = None
    if target == NEW_SPEAKER:
        name = library._clean_name(display_name, "the new speaker's name")
        target = next_user_label(known)
    elif target not in known:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown speaker {target!r}; one of {', '.join(known) or 'none'},"
                f" or {NEW_SPEAKER!r} with a display_name"
            ),
        )

    with db.LOCK:
        if name is not None:
            _upsert_label(conn, run["id"], target, name, None)
        conn.execute(
            "UPDATE word SET speaker=?, edited_by_user=1 WHERE run_id=? AND idx BETWEEN ? AND ?",
            (target, run["id"], first, last),
        )
        conn.commit()
    return _after_edit(request, conn, media_id)


# --- the audio --------------------------------------------------------------------------


def transcode(src: Path, dst: Path) -> None:
    """Write ``src``'s audio as AAC in a faststart MP4 at ``dst``.

    ``-f mp4`` is spelled out because ``dst`` is a ``.part`` name ffmpeg
    cannot infer a container from. CREATE_NO_WINDOW keeps a console from
    flashing up on Windows; there is no timeout, because any constant would
    be a lie about somebody else's ten-hour recording.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-i", str(src),
            "-vn", "-sn", "-dn",
            "-c:a", "aac", "-b:a", PROXY_BITRATE,
            "-movflags", "+faststart",
            "-f", "mp4",
            str(dst),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[-STDERR_TAIL:]
        raise ProxyError(f"ffmpeg could not convert {src.name}: {detail}")


def probe_duration(path: Path) -> float | None:
    """The file's duration as ffprobe reports it; None when it cannot say."""
    try:
        return probe.probe_media(path)["duration"]
    except (probe.NotMediaError, OSError, subprocess.SubprocessError):
        return None


def durations_agree(original: float | None, proxy: float | None) -> bool:
    """Whether the proxy is the same length as the original, to the tolerance.
    An unknown duration on either side is a disagreement: the timestamps
    cannot be trusted to a file nobody could measure."""
    if original is None or proxy is None:
        return False
    return abs(float(original) - float(proxy)) <= PROXY_TOLERANCE_SECONDS


# One lock per proxy path. A browser asks for the audio twice (`preload=
# "metadata"`, then playback), and the second request lands while the first
# is still transcoding, before the proxy exists; it has to wait for that
# transcode rather than start its own. The dict is never pruned: one lock per
# recording ever proxied by this process, which is bytes.
_proxy_locks: dict[str, threading.Lock] = {}
_proxy_locks_guard = threading.Lock()


def _proxy_lock(proxy: Path) -> threading.Lock:
    key = os.path.normcase(str(proxy))
    with _proxy_locks_guard:
        return _proxy_locks.setdefault(key, threading.Lock())


def ensure_proxy(original: Path, proxy: Path) -> None:
    """Make the proxy for ``original`` at ``proxy`` unless it is already there.

    The transcode lands on a temporary name and is renamed into place only
    once its duration has been checked against the original's, so the proxy
    path never names a file that is not known to be the same audio. One
    transcode per proxy at a time: a request that arrives during one waits
    for it and finds the result.
    """
    if proxy.is_file():
        return
    with _proxy_lock(proxy):
        if proxy.is_file():  # the request ahead of this one made it
            return
        proxy.parent.mkdir(parents=True, exist_ok=True)
        tmp = proxy.with_name(f"{proxy.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            transcode(original, tmp)
            wanted, got = probe_duration(original), probe_duration(tmp)
            if not durations_agree(wanted, got):
                raise ProxyError(
                    f"the proxy of {original.name} lasts {got} s where the original"
                    f" lasts {wanted} s, more than {PROXY_TOLERANCE_SECONDS * 1000:.0f} ms apart"
                )
            os.replace(tmp, proxy)
        finally:
            tmp.unlink(missing_ok=True)  # a no-op once it has been renamed away


@router.get("/media/{media_id}/audio", include_in_schema=False)
def audio(media_id: int, request: Request) -> Response:
    """The recording as a browser can play it, with Range support.

    Inline, not an attachment: this is the player's source. The download
    route is the one that hands out the original under its own name.
    """
    conn = request.app.state.conn
    row = library._get_media(conn, media_id)
    original = paths.DATA_DIR / row["store_path"]
    if not original.is_file():
        raise HTTPException(
            status_code=404, detail=f"the stored file for media {media_id} is missing"
        )

    suffix = original.suffix.lower()
    if suffix in PLAYABLE:
        return FileResponse(original, media_type=PLAYABLE[suffix])

    proxy = proxy_path_for(row["sha256"])
    try:
        ensure_proxy(original, proxy)
    except ProxyError as exc:
        log.warning("no proxy for media %s (%s); serving the original: %s", media_id, row["orig_name"], exc)
        media_type = mimetypes.guess_type(row["orig_name"])[0] or "application/octet-stream"
        return FileResponse(original, media_type=media_type)
    return FileResponse(proxy, media_type=PROXY_MEDIA_TYPE)
