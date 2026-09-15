"""The export routes: the Advanced Export dialog, its live preview, the
download or the write into a folder, the bulk action, and the preset rows.

Everything here is thin over `scribe.exports` (ADR-003). `doc.load` reads a
media's words once, the writers in `exports.FORMATS` turn that document into
bytes, and a route only parses the form into an `ExportOptions`, calls the
writers and hands the bytes on. No route computes a cue or a paragraph of
its own, and nothing grouped is written to the database: a saved preset is
one `ExportOptions` as JSON in `export_preset`, and a preview is the engine
run on the request's options and thrown away with the response.

The dialog (`GET /media/{id}/export`, or `GET /export?ids=...` for a
selection) is one form whose fields are `ExportOptions`' fields by name, so
the same parser serves the preview, the export, the bulk action and a preset
saved from the dialog. Its preview pane posts the form to
`/media/{id}/export/preview` on load and on every change and gets back the
first ten cues - for a subtitle format, with each cue's timing as that
format writes it and the rule it breaks, if any - or the first ten lines of
the text the first selected format would produce, plus the compliance
summary over the whole file (`9 violations: min_duration ×7, cps ×2`).

An export goes one of two ways, chosen by the submit button's
``destination``:

* **download** - one format is the file itself, under `options.filename_for`'s
  name; several are one ZIP built in memory, capped at `ZIP_CAP_BYTES` so a
  bulk export of a library's HTML bundles cannot ask the browser to hold a
  gigabyte. The cap is checked as each media's files are built (`collect`),
  so the server does not hold the library either: the first media past it
  ends the build, and the answer is a 413 that names the other way.
* **folder** - every file is written into ``path``, which has to lie under
  the browse roots (`fsbrowse.is_allowed`, the same rule that decides what
  the transcribe dialog may read) or under the media store itself, which is
  always allowed because the dialog's default is the folder the recording
  lives in and that default has to work whatever the roots say. The store
  is the only place this app knows a recording to be: ingest hardlinks a
  file and records its name, not where it came from. A file is written
  whole to a `.part` name and renamed into place (Phase 1's rule for every
  artifact), and an export into the same folder replaces the last one. A
  selection's files are all built before the first is written, so a
  refusal (a character the encoding cannot hold) leaves the folder as it
  was; what that holds in memory is the selection's files, uncapped - the
  CLI's ``--all`` is the door for a library, and writes one media at a time.

The HTML bundle is the one format that takes more than the words: its
player wants the audio. Up to `EMBED_CAP_BYTES` the recording goes into the
file as a ``data:`` URL; above that a sidecar is written next to the HTML
(or zipped with it) under the name the bundle expects, `html_bundle.
sidecar_name`. The audio is chosen by the player's rule: the original when a
seek in it is exact, and the AAC proxy otherwise (a VBR MP3, a container a
browser cannot open), made here if it is not there yet - the same
`playback.ensure_proxy`, the same fallback to the original when ffmpeg
cannot. The recording is looked up only when ``html`` is among
the formats (`audio_for`): every other export is the words alone, and an
SRT of a video library must not transcode the library.

A bulk export over a selection skips a media without a transcript, says so,
and gives two media that would share a filename distinct ones (`Same.srt`,
`Same (2).srt`) by suffixing the template, so an HTML bundle and its
sidecar stay a pair. The preset routes live in `scribe.web.settings`; the
rows are read and written here so the dialog and the settings page cannot
disagree about what a preset is.

Nothing here loads a model (ADR-001). ffmpeg, for a proxy, is the only
subprocess, and only for an HTML export of a container a browser cannot play.
"""

from __future__ import annotations

import io
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import typing
import urllib.parse
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from scribe import db, exports, fsbrowse, paths, playback, render
from scribe.exports import common, cues, html_bundle, srt
from scribe.exports import doc as export_doc
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import PRESETS, ExportOptions, filename_for
from scribe.media import proxy_path_for
from scribe.web import library, render as render_page, transcribe_dialog, transcript

router = APIRouter()
log = logging.getLogger(__name__)

# The HTML bundle embeds the recording up to this size; above it a sidecar
# file goes next to the bundle. Past ~25 MB a data: URL makes the page slow
# to open, and the bundle is meant to be mailed.
EMBED_CAP_BYTES = 25 * 1024 * 1024

# A download of several files is one ZIP held in memory while it is built;
# this is as much as a browser download should be asked to hold. Above it the
# route refuses and points at write-to-folder, which streams file by file.
ZIP_CAP_BYTES = 500 * 1024 * 1024

# What the preview shows: the first cues of a subtitle, or the first lines of
# a text.
PREVIEW_CUES = 10
PREVIEW_LINES = 10

# The formats the preview shows as cues; TXT joins them in its cue layout.
CUE_FORMATS: tuple[str, ...] = ("srt", "vtt", "csv")
# The formats the preview shows as their own text; the rich ones (DOCX, HTML)
# and JSON are previewed as TXT, the closest thing to reading them.
PROSE_FORMATS: tuple[str, ...] = ("txt", "md")

# What the TXT stand-in honours that the format itself does not, said in
# the preview's head line so the pane cannot promise what the file will not
# do. From the writers: DOCX stamps as TXT does but has no layout; the HTML
# bundle only turns stamps on or off and has no layout; JSON carries every
# word's own times and nothing of either.
PREVIEW_CAVEATS: dict[str, str] = {
    "docx": "the TXT layout does not apply",
    "html": "the TXT layout and the timestamp format do not apply; timestamps are on or off",
    "json": "the TXT layout and timestamps do not apply; every word carries its own times",
}

# The two ways out, as the dialog's submit buttons name them.
DESTINATIONS: tuple[str, ...] = ("download", "folder")
DEFAULT_DESTINATION = "download"

ZIP_MIME = "application/zip"
BULK_ZIP_NAME = "export.zip"

# The form fields, by name: ExportOptions' own, plus the ones the dialog adds.
OPTION_FIELDS: tuple[str, ...] = tuple(ExportOptions.model_fields)
FORMATS_FIELD = "formats"
DESTINATION_FIELD = "destination"
PATH_FIELD = "path"
PRESET_NAME_FIELD = "name"
PRESET_JSON_FIELD = "options"

# The preset <select>'s id: the dialog's save button targets it, and the
# settings router recognises it in HX-Target to answer with the select alone.
PRESET_SELECT_ID = "export-preset"

# A preset name: what a form may post. Built-in names are refused whatever
# the case, so "Netflix" cannot shadow the netflix preset.
MAX_PRESET_NAME = 100

# How the formats are labelled in the dialog, in registry order.
FORMAT_LABELS: dict[str, str] = {
    "srt": "SRT subtitles",
    "vtt": "WebVTT subtitles",
    "txt": "Plain text",
    "csv": "CSV (one row per cue)",
    "json": "JSON (word level)",
    "md": "Markdown",
    "docx": "Word (DOCX)",
    "html": "HTML bundle with player",
}

# The built-in presets as the dialog names them.
PRESET_LABELS: dict[str, str] = {
    "netflix": "Netflix",
    "bbc": "BBC",
    "youtube": "YouTube",
    "podcast": "Podcast transcript",
    "obsidian": "Obsidian note",
}

# How many times a clashing filename is suffixed before the export gives up.
MAX_NAME_CLASHES = 99

# Media types a text export may be served under, with the charset it was
# written in. JSON and HTML are always UTF-8 (their writers say so).
_CHARSETS: dict[str, str] = {"utf-8": "utf-8", "utf-8-sig": "utf-8", "cp1252": "windows-1252"}

# The formats whose bytes follow `options.encoding`, and so can refuse a
# character. JSON and HTML are always UTF-8 and DOCX is XML in a zip; the
# encoding cannot fail those.
ENCODED_FORMATS: tuple[str, ...] = ("srt", "vtt", "txt", "csv", "md")


class TooLarge(Exception):
    """The ZIP would exceed `ZIP_CAP_BYTES`."""


def too_large(total: int, *, partial: bool = False) -> TooLarge:
    """The refusal, naming the size (a lower bound when ``partial``: the
    build stopped at the cap) and the other way out."""
    amount = f"{'at least ' if partial else ''}{total / (1024 * 1024):.0f} MB"
    return TooLarge(
        f"these files come to {amount}, more than the {ZIP_CAP_BYTES // (1024 * 1024)} MB"
        " a download holds; use Write to folder instead"
    )


def unencodable(options: ExportOptions, exc: UnicodeEncodeError) -> str:
    """Why the encoding refused, for a person: the first character it cannot
    hold, and the way out. The codec's own message names the code point as
    an escape and the codec as ``charmap``, neither of which helps."""
    char = str(exc.object[exc.start : exc.start + 1])
    return f"{char!r} (U+{ord(char):04X}) is not in {options.encoding}; choose utf-8"


def refused_encoding(doc: TranscriptDoc, options: ExportOptions, exc: UnicodeEncodeError) -> HTTPException:
    """The 400 an export answers when the encoding cannot hold the text."""
    return HTTPException(
        status_code=400,
        detail=(
            f"media {doc.media['id']} ({doc.title!r}) cannot be written as"
            f" {options.encoding}: {unencodable(options, exc)}"
        ),
    )


# --- the options, out of a form ----------------------------------------------------------


def _list(form: Any, key: str) -> list[str]:
    """Every value posted under ``key``, each split on commas and whitespace,
    so a checkbox group, a comma-separated field and a CLI-style string all
    read the same."""
    if not hasattr(form, "getlist"):
        return []
    return [
        part
        for value in form.getlist(key)
        if isinstance(value, str)
        for part in re.split(r"[,\s]+", value)
        if part
    ]


def validate_options(given: Mapping[str, Any]) -> ExportOptions:
    """An `ExportOptions` out of a mapping; a 400 names the field."""
    try:
        return ExportOptions.model_validate(dict(given))
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'options'}: {error['msg']}"
            for error in exc.errors()
        )
        raise HTTPException(status_code=400, detail=f"invalid export options: {problems}")


def parse_export_options(form: Mapping[str, Any]) -> ExportOptions:
    """The options a form posted: the option fields by name, last value per
    name (a hidden ``0`` ahead of each checkbox, as the dialog sends them),
    with ``formats`` gathered from every checkbox that was ticked.

    A form with no format ticked posts nothing under ``formats``. When any
    other option field came with it, that is the dialog saying "no format"
    and the model refuses with its own message rather than quietly writing
    the default SRT; a post with no option field at all is an API call and
    means the defaults."""
    fields = transcribe_dialog._fields(form)
    given: dict[str, Any] = {key: fields[key] for key in OPTION_FIELDS if key in fields}
    formats = _list(form, FORMATS_FIELD)
    if formats or given:
        given[FORMATS_FIELD] = formats
    return validate_options(given)


def preset_options_from(form: Mapping[str, Any]) -> ExportOptions:
    """The options a preset post carries: an ``options`` field holding JSON
    (the settings page's form), or the option fields themselves (the export
    dialog's)."""
    fields = transcribe_dialog._fields(form)
    raw = (fields.get(PRESET_JSON_FIELD) or "").strip()
    if not raw:
        return parse_export_options(form)
    try:
        given = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"options is not valid JSON: {exc}")
    if not isinstance(given, dict):
        raise HTTPException(status_code=400, detail="options must be a JSON object")
    return validate_options(given)


def parse_destination(form: Mapping[str, Any]) -> str:
    value = (transcribe_dialog._fields(form).get(DESTINATION_FIELD) or "").strip().lower()
    if not value:
        return DEFAULT_DESTINATION
    if value not in DESTINATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown destination {value!r}; one of {', '.join(DESTINATIONS)}",
        )
    return value


def choices(field: str) -> tuple[str, ...]:
    """The values a `Literal` field of `ExportOptions` takes, in its order -
    read from the model so the dialog cannot offer what the model refuses."""
    return typing.get_args(ExportOptions.model_fields[field].annotation)


# --- presets -----------------------------------------------------------------------------


def saved_presets(conn: sqlite3.Connection) -> list[dict]:
    """The `export_preset` rows, oldest first, as ``{id, name, options}``.
    A row whose JSON no longer validates is logged and left out rather than
    breaking every dialog."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT id, name, options_json FROM export_preset ORDER BY id"
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        try:
            options = ExportOptions.model_validate_json(row["options_json"])
        except ValidationError as exc:
            log.warning("export preset %r (id %s) is not valid and is skipped: %s", row["name"], row["id"], exc)
            continue
        out.append({"id": row["id"], "name": row["name"], "options": options})
    return out


def preset_choices(conn: sqlite3.Connection) -> list[dict]:
    """What the dialog's select offers: the built-ins, then the saved ones,
    each with its options as JSON for app.js to fill the fields with."""
    out = [
        {
            "name": name,
            "label": PRESET_LABELS.get(name, name),
            "options_json": options.model_dump_json(),
            "builtin": True,
        }
        for name, options in PRESETS.items()
    ]
    out.extend(
        {
            "name": preset["name"],
            "label": preset["name"],
            "options_json": preset["options"].model_dump_json(),
            "builtin": False,
        }
        for preset in saved_presets(conn)
    )
    return out


def describe(options: ExportOptions) -> str:
    """One line saying what a preset does, for the settings table."""
    return (
        f"{', '.join(options.formats)} · timestamps {options.timestamps} ({options.timestamp_format})"
        f" · speakers {'on' if options.speakers else 'off'}"
        f" · {options.cpl} cpl · {options.max_lines} line{'s' if options.max_lines != 1 else ''}"
        f" · {options.max_cps:g} cps · {options.min_duration:g}–{options.max_duration:g} s"
    )


def clean_preset_name(name: str | None) -> str:
    clean = (name or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="give the preset a name")
    if len(clean) > MAX_PRESET_NAME:
        raise HTTPException(
            status_code=400, detail=f"a preset name is at most {MAX_PRESET_NAME} characters"
        )
    if clean.lower() in PRESETS:
        raise HTTPException(
            status_code=400, detail=f"{clean!r} is a built-in preset; choose another name"
        )
    return clean


def save_preset(conn: sqlite3.Connection, name: str | None, options: ExportOptions) -> str:
    """Store ``options`` under ``name``; returns the name as stored. A name
    already taken (in any case) is a 409 rather than a silent overwrite."""
    clean = clean_preset_name(name)
    with db.LOCK:
        taken = conn.execute(
            "SELECT name FROM export_preset WHERE lower(name)=lower(?)", (clean,)
        ).fetchone()
        if taken is not None:
            raise HTTPException(
                status_code=409,
                detail=f"a preset named {taken['name']!r} exists; delete it first or pick another name",
            )
        conn.execute(
            "INSERT INTO export_preset(name, options_json) VALUES (?, ?)",
            (clean, options.model_dump_json()),
        )
        conn.commit()
    return clean


def delete_preset(conn: sqlite3.Connection, preset_id: int) -> None:
    with db.LOCK:
        cur = conn.execute("DELETE FROM export_preset WHERE id=?", (preset_id,))
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"no preset with id {preset_id}")


# --- the recording, for the HTML bundle --------------------------------------------------


@dataclass(frozen=True)
class Audio:
    """The file the bundle's player gets, its media type and its size."""

    path: Path
    mime: str
    size: int


def audio_source(media: Mapping[str, Any]) -> Audio | None:
    """The playable audio of a media row, chosen by the player's rule
    (`playback.seeks_exactly`): the original when a seek in it is exact,
    else the AAC proxy - made now if it is missing, or the original after
    all when ffmpeg cannot. None when the stored file is gone."""
    original = paths.DATA_DIR / str(media["store_path"])
    if not original.is_file():
        return None
    suffix = original.suffix.lower()
    if playback.seeks_exactly(original):
        return Audio(original, playback.PLAYABLE[suffix], original.stat().st_size)
    proxy = proxy_path_for(str(media["sha256"]))
    try:
        playback.ensure_proxy(original, proxy)
    except playback.ProxyError as exc:
        log.warning(
            "no proxy for media %s (%s); the HTML export carries the original: %s",
            media.get("id"), media.get("orig_name"), exc,
        )
        mime = (
            playback.PLAYABLE.get(suffix)
            or mimetypes.guess_type(str(media.get("orig_name") or ""))[0]
            or "application/octet-stream"
        )
        return Audio(original, mime, original.stat().st_size)
    return Audio(proxy, playback.PROXY_MEDIA_TYPE, proxy.stat().st_size)


def audio_for(media: Mapping[str, Any], options: ExportOptions) -> Audio | None:
    """`audio_source`, but only when the options ask for the HTML bundle -
    the one format that takes the recording. Every other export is computed
    from the words alone, so an SRT of a video never transcodes the video."""
    if "html" not in options.formats:
        return None
    return audio_source(media)


# --- the files of an export --------------------------------------------------------------


class Member(NamedTuple):
    """One file of an export: its name and either its bytes or, for an audio
    sidecar, the file to copy. ``format`` is the registry name, None for a
    sidecar."""

    name: str
    data: bytes | None
    path: Path | None
    format: str | None

    @property
    def size(self) -> int:
        if self.data is not None:
            return len(self.data)
        return self.path.stat().st_size if self.path is not None else 0


def _needs_sidecar(options: ExportOptions, audio: Audio | None) -> bool:
    return "html" in options.formats and audio is not None and audio.size > EMBED_CAP_BYTES


def filenames_for(doc: TranscriptDoc, options: ExportOptions, audio: Audio | None) -> list[str]:
    """The names `members_for` will give, without writing anything."""
    names = [filename_for(doc, options, exports.FORMATS[name].extension) for name in options.formats]
    if _needs_sidecar(options, audio):
        names.append(html_bundle.sidecar_name(doc, options, audio.mime))
    return names


def members_for(doc: TranscriptDoc, options: ExportOptions, audio: Audio | None) -> list[Member]:
    """Every file the options ask for, in the formats' order: each writer's
    bytes under `filename_for`'s name, the HTML bundle with its audio
    embedded or its sidecar after it."""
    out: list[Member] = []
    for name in options.formats:
        entry = exports.FORMATS[name]
        filename = filename_for(doc, options, entry.extension)
        if name != "html":
            out.append(Member(filename, entry.writer(doc, options), None, name))
        elif audio is None:
            out.append(Member(filename, html_bundle.write(doc, options), None, name))
        elif audio.size <= EMBED_CAP_BYTES:
            data = html_bundle.write(doc, options, audio=audio.path.read_bytes(), audio_mime=audio.mime)
            out.append(Member(filename, data, None, name))
        else:
            out.append(Member(filename, html_bundle.write(doc, options, audio_mime=audio.mime), None, name))
            out.append(Member(html_bundle.sidecar_name(doc, options, audio.mime), None, audio.path, None))
    return out


def distinct_members(
    doc: TranscriptDoc, options: ExportOptions, audio: Audio | None, taken: set[str]
) -> list[Member]:
    """`members_for`, with the filename template suffixed `` (2)``, `` (3)``
    ... until none of the names is in ``taken`` (compared case-insensitively,
    as Windows compares them); the names then join ``taken``. The suffix
    goes on the template rather than the file so a bundle and its sidecar
    keep the same stem."""
    for n in range(1, MAX_NAME_CLASHES + 1):
        attempt = options if n == 1 else options.model_copy(
            update={"filename_template": f"{options.filename_template} ({n})"}
        )
        names = filenames_for(doc, attempt, audio)
        keys = {name.casefold() for name in names}
        if not keys & taken:
            taken |= keys
            return members_for(doc, attempt, audio)
    raise HTTPException(
        status_code=409,
        detail=f"could not give {doc.title!r} a filename that is not already used in this export",
    )


@dataclass(frozen=True)
class Skipped:
    """A media the bulk export left out, and why."""

    title: str
    reason: str


def collect(
    conn: sqlite3.Connection, ids: Sequence[int], options: ExportOptions, *, cap: int | None = None
) -> tuple[list[Member], list[Skipped]]:
    """The files of every media in ``ids`` that has a transcript, in the
    ids' order, and the ones that have none. With ``cap``, the bytes the
    files may come to in all: checked as each media's are built, so a
    download refused for its size (`TooLarge`) is refused before the rest
    of the selection is built and held."""
    members: list[Member] = []
    skipped: list[Skipped] = []
    taken: set[str] = set()
    total = 0
    for media_id in ids:
        row = library._get_media(conn, media_id)
        try:
            document = export_doc.load(conn, media_id)
        except export_doc.NoTranscript:
            skipped.append(Skipped(str(row["title"]), "no transcript yet"))
            continue
        try:
            batch = distinct_members(document, options, audio_for(document.media, options), taken)
        except UnicodeEncodeError as exc:
            raise refused_encoding(document, options, exc)
        total += sum(member.size for member in batch)
        if cap is not None and total > cap:
            raise too_large(total, partial=media_id != ids[-1])
        members.extend(batch)
    return members, skipped


# --- delivering them -----------------------------------------------------------------------


def content_type_for(member: Member, options: ExportOptions) -> str:
    """The media type a single file is served under, with its charset when
    the type is text: the options' encoding for the text writers, UTF-8 for
    HTML, which its writer always is."""
    if member.format is None:
        return mimetypes.guess_type(member.name)[0] or "application/octet-stream"
    mime = exports.FORMATS[member.format].mime
    if not mime.startswith("text/"):
        return mime
    charset = "utf-8" if member.format == "html" else _CHARSETS[options.encoding]
    return f"{mime}; charset={charset}"


def attachment(name: str) -> str:
    """A ``Content-Disposition`` for ``name``: quoted as it is when it is
    plain ASCII (the scrub has already removed quotes and backslashes), with
    an RFC 5987 ``filename*`` beside an ASCII fallback otherwise."""
    if name.isascii() and name.isprintable():
        return f'attachment; filename="{name}"'
    fallback = "".join(ch if ch.isascii() and ch.isprintable() else "_" for ch in name)
    return f"attachment; filename=\"{fallback}\"; filename*=utf-8''{urllib.parse.quote(name)}"


def zip_bytes(members: Sequence[Member]) -> bytes:
    """The members as one ZIP in memory; `TooLarge` when they would exceed
    the cap, decided before the archive is built (a bulk export has already
    checked while building; a single media's files are checked here)."""
    total = sum(member.size for member in members)
    if total > ZIP_CAP_BYTES:
        raise too_large(total)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            if member.data is not None:
                archive.writestr(member.name, member.data)
            else:
                archive.write(member.path, arcname=member.name)  # type: ignore[arg-type]
    return buffer.getvalue()


def download(members: Sequence[Member], options: ExportOptions, zip_name: str) -> Response:
    """One file as itself, several as a ZIP called ``zip_name``."""
    if len(members) == 1 and members[0].data is not None:
        member = members[0]
        return Response(
            member.data,
            headers={
                "Content-Type": content_type_for(member, options),
                "Content-Disposition": attachment(member.name),
            },
        )
    try:
        data = zip_bytes(members)
    except TooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    return Response(
        data, headers={"Content-Type": ZIP_MIME, "Content-Disposition": attachment(zip_name)}
    )


def write_roots(conn: sqlite3.Connection) -> tuple[Path, ...]:
    """Where an export may be written: the browse roots, and always the
    media store, where the dialog's default folder lies."""
    return fsbrowse.allowed_roots(conn) + (paths.MEDIA_DIR,)


def default_folder(media: Mapping[str, Any]) -> Path:
    """The dialog's default: the folder the stored recording is in - the
    only place this app knows the recording to be (see the module docstring)."""
    return (paths.DATA_DIR / str(media["store_path"])).parent


def target_folder(conn: sqlite3.Connection, raw: str | None) -> Path:
    """The folder a form asked to write into: absolute, under the write
    roots, and there. A 400 says what is wrong; outside the roots is a 403."""
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="give the folder to write the files into")
    folder = Path(text)
    if not folder.is_absolute():
        raise HTTPException(
            status_code=400, detail=f"{text} is not an absolute path; give the whole path from the drive"
        )
    if not fsbrowse.is_allowed(folder, write_roots(conn)):
        raise HTTPException(
            status_code=403,
            detail=f"{folder} is outside the folders this app may write to; widen them under Settings",
        )
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"{folder} is not a directory on this machine")
    return folder


class Written(NamedTuple):
    """A file `write_into` put in place, and whether a file of that name
    was there before - the result fragment marks those, since an export
    into a folder replaces without asking."""

    path: Path
    replaced: bool


class WriteFailed(OSError):
    """A member could not be put in place (a target held open by another
    program, a folder of that name, a full disk). ``written`` is what was
    put in place before it, so the caller can say so."""

    def __init__(self, target: Path, written: Sequence[Written], cause: OSError) -> None:
        super().__init__(f"could not write {target}: {cause.strerror or cause}")
        self.target = target
        self.written = list(written)
        self.cause = cause


def write_into(members: Sequence[Member], folder: Path) -> list[Written]:
    """Write every member into ``folder``, each whole to a `.part` name and
    renamed into place, replacing a file of the same name. Returns what was
    written, in order; `WriteFailed` at the first that could not be, with
    the ones before it, and no `.part` left behind."""
    written: list[Written] = []
    for member in members:
        target = folder / member.name
        tmp = folder / f"{member.name}.{uuid.uuid4().hex[:8]}.part"
        try:
            replaced = target.exists()
            if member.data is not None:
                tmp.write_bytes(member.data)
            else:
                shutil.copyfile(member.path, tmp)  # type: ignore[arg-type]
            os.replace(tmp, target)
        except OSError as exc:
            raise WriteFailed(target, written, exc) from exc
        finally:
            tmp.unlink(missing_ok=True)  # a no-op once it has been renamed away
        written.append(Written(target, replaced))
    return written


# --- the preview ----------------------------------------------------------------------------


def summary(report: cues.Report) -> str:
    """The compliance line: ``No violations.`` or ``N violations: rule ×n, ...``,
    the rules most broken first."""
    counts = Counter(violation.rule for violation in report.violations)
    if not counts:
        return "No violations."
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda item: (-item[1], cues.RULES.index(item[0])))
    parts = ", ".join(f"{rule} ×{count}" for rule, count in ordered)
    return f"{total} violation{'s' if total != 1 else ''}: {parts}"


def _timing(seconds: float, name: str) -> str:
    stamp = render.format_ts(seconds, "srt")
    return stamp.replace(",", ".") if name == "vtt" else stamp


def encoding_warning(name: str, options: ExportOptions, text: str) -> str | None:
    """The preview's line when ``text`` - what format ``name`` would write -
    has a character ``options.encoding`` cannot hold, so the dialog says
    before the export refuses. None when it can, or when the format's bytes
    do not follow the encoding at all."""
    if name not in ENCODED_FORMATS:
        return None
    try:
        text.encode(options.encoding)
    except UnicodeEncodeError as exc:
        return f"The file cannot be written as {options.encoding}: {unencodable(options, exc)}."
    return None


def preview_context(doc: TranscriptDoc, options: ExportOptions) -> dict:
    """What _export_preview.html renders from, for the first selected format:
    cues for a subtitle, lines for a text."""
    name = options.formats[0]
    if name in CUE_FORMATS or (name == "txt" and options.txt_layout == "cue"):
        built = cues.build(doc, options)
        # SRT's report is the file's, with the `NAME: ` prefix counted
        # against cpl; the other cue formats write the lines as the engine
        # laid them out.
        report = srt.report(doc, built, options) if name == "srt" else built.report
        broken: dict[int, list[str]] = {}
        for violation in report.violations:
            broken.setdefault(violation.cue_index, []).append(violation.rule)
        shown = [
            {
                "index": cue.index,
                "start": _timing(cue.start, name),
                "end": _timing(cue.end, name),
                "name": common.speaker_name(doc, cue.speaker, options),
                "lines": cue.lines,
                "rules": broken.get(cue.index, []),
            }
            for cue in built.cues[:PREVIEW_CUES]
        ]
        # What the file's text is made of: every cue's lines and every name.
        written = "\n".join(
            [line for cue in built.cues for line in cue.lines]
            + [common.speaker_name(doc, cue.speaker, options) for cue in built.cues]
        )
        return {
            "kind": "cues",
            "format": name,
            "cues": shown,
            "total": len(built.cues),
            "summary": summary(report),
            "clean": not report.violations,
            "warning": encoding_warning(name, options, written),
        }
    source = name if name in PROSE_FORMATS else "txt"
    plain = options.model_copy(update={"encoding": "utf-8", "crlf": False})
    text = exports.FORMATS[source].writer(doc, plain).decode("utf-8")
    lines = text.splitlines()
    return {
        "kind": "lines",
        "format": name,
        "source": source,
        "lines": lines[:PREVIEW_LINES],
        "total": len(lines),
        "as_text": source != name,
        "caveat": PREVIEW_CAVEATS.get(name),
        "warning": encoding_warning(name, options, text),
    }


# --- the dialog ------------------------------------------------------------------------------


def _has_transcript(conn: sqlite3.Connection, media_id: int) -> bool:
    return transcript.current_run(conn, media_id) is not None


def _load(conn: sqlite3.Connection, media_id: int) -> TranscriptDoc:
    """The document, or a 409: a media without a transcript has nothing to export."""
    try:
        return export_doc.load(conn, media_id)
    except export_doc.NoTranscript as exc:
        raise HTTPException(status_code=409, detail=str(exc))


def dialog_context(
    conn: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    *,
    bulk: bool = False,
    options: ExportOptions | None = None,
) -> dict:
    """Everything export_dialog.html renders from, for one media or a selection."""
    targets = [
        {"id": row["id"], "title": row["title"], "has_transcript": _has_transcript(conn, row["id"])}
        for row in rows
    ]
    previewable = next((target for target in targets if target["has_transcript"]), None)
    first = rows[0]
    return {
        "bulk": bulk,
        "targets": targets,
        "action_url": "/media/bulk" if bulk else f"/media/{first['id']}/export",
        "back_url": "/" if bulk else f"/media/{first['id']}",
        "preview_url": None if previewable is None else f"/media/{previewable['id']}/export/preview",
        "formats": [
            {"name": name, "label": FORMAT_LABELS.get(name, name), "extension": entry.extension}
            for name, entry in exports.FORMATS.items()
        ],
        "presets": preset_choices(conn),
        "selected_preset": None,
        "preset_select_id": PRESET_SELECT_ID,
        "options": options or ExportOptions(),
        "choices": {
            field: choices(field)
            for field in ("timestamps", "timestamp_format", "txt_layout", "encoding")
        },
        "default_path": str(default_folder(first)),
        "written": None,
        "skipped": [],
        "folder": None,
    }


def _rows(conn: sqlite3.Connection, ids: Sequence[int]) -> list[sqlite3.Row]:
    return [library._get_media(conn, media_id) for media_id in ids]


@router.get("/media/{media_id}/export", include_in_schema=False)
def dialog(media_id: int, request: Request) -> Response:
    """The export dialog for one media: a fragment for the page's <dialog>,
    a page of its own otherwise. A media without a transcript is a 409."""
    conn = request.app.state.conn
    row = library._get_media(conn, media_id)
    if not _has_transcript(conn, media_id):
        raise HTTPException(status_code=409, detail=f"media {media_id} has no transcript yet")
    ctx = dialog_context(conn, [row])
    template = "export_dialog.html" if library._is_htmx(request) else "export.html"
    return render_page(request, template, **ctx)


@router.get("/export", include_in_schema=False)
def bulk_dialog(request: Request) -> Response:
    """The export dialog for a selection (``ids``): the same form, posting to
    the bulk route. Media without a transcript are listed as skipped."""
    conn = request.app.state.conn
    ids = library._parse_ids(request.query_params.getlist("ids") + request.query_params.getlist("ids[]"))
    library._require_media(conn, ids)
    ctx = dialog_context(conn, _rows(conn, ids), bulk=True)
    template = "export_dialog.html" if library._is_htmx(request) else "export.html"
    return render_page(request, template, **ctx)


@router.post("/media/{media_id}/export/preview", include_in_schema=False)
async def preview(media_id: int, request: Request) -> Response:
    """The first cues or lines the posted options would produce, and the
    compliance summary. Nothing is written anywhere."""
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    options = parse_export_options(await request.form())
    return await run_in_threadpool(_preview, request, conn, media_id, options)


def _preview(request: Request, conn: sqlite3.Connection, media_id: int, options: ExportOptions) -> Response:
    """The preview's work, in a worker thread: the engine over the whole
    transcript is what the dialog runs on every change."""
    document = _load(conn, media_id)
    return render_page(request, "_export_preview.html", **preview_context(document, options))


def _deliver(
    request: Request,
    conn: sqlite3.Connection,
    form: Mapping[str, Any],
    members: list[Member],
    skipped: list[Skipped],
    options: ExportOptions,
    *,
    zip_name: str,
    rows: Sequence[Mapping[str, Any]],
    bulk: bool,
) -> Response:
    """Hand the members out the way the form asked: a download, or a write
    into the folder answered with the list of what was written (a fragment
    for htmx, the dialog page around it otherwise)."""
    destination = parse_destination(form)
    if destination == "download":
        return download(members, options, zip_name)
    folder = target_folder(conn, transcribe_dialog._fields(form).get(PATH_FIELD))
    try:
        written = write_into(members, folder)
    except WriteFailed as exc:
        before = ", ".join(item.path.name for item in exc.written)
        raise HTTPException(
            status_code=409,
            detail=f"{exc}; " + (f"written before it: {before}" if before else "nothing was written"),
        )
    if library._is_htmx(request):
        return render_page(
            request, "_export_written.html", written=written, skipped=skipped, folder=folder
        )
    ctx = dialog_context(conn, rows, bulk=bulk, options=options)
    ctx.update(written=written, skipped=skipped, folder=folder)
    return render_page(request, "export.html", **ctx)


@router.post("/media/{media_id}/export", include_in_schema=False)
async def export(media_id: int, request: Request) -> Response:
    """Export one media: the file (or a ZIP of several) as a download, or
    the files written into a folder. The form is refused here; the work -
    the writers, the recording's base64, a ZIP, the disk - runs in a worker
    thread, as every heavy route's does, so the app stays answerable."""
    conn = request.app.state.conn
    row = library._get_media(conn, media_id)
    form = await request.form()
    options = parse_export_options(form)
    parse_destination(form)  # refused before any work is done
    return await run_in_threadpool(_export, request, conn, row, form, options)


def _export(
    request: Request, conn: sqlite3.Connection, row: Mapping[str, Any], form: Mapping[str, Any],
    options: ExportOptions,
) -> Response:
    document = _load(conn, row["id"])
    try:
        members = members_for(document, options, audio_for(document.media, options))
    except UnicodeEncodeError as exc:
        raise refused_encoding(document, options, exc)
    return _deliver(
        request, conn, form, members, [], options,
        zip_name=filename_for(document, options, "zip"), rows=[row], bulk=False,
    )


async def bulk_export(
    request: Request, conn: sqlite3.Connection, ids: Sequence[int], form: Mapping[str, Any]
) -> Response:
    """`POST /media/bulk` with ``action=export``: every selected media with a
    transcript, in one ZIP or into the folder; the rest are named as skipped.
    Called by `library.bulk`, which has parsed the ids and checked they exist.
    The work runs in a worker thread, like `export`'s."""
    options = parse_export_options(form)
    parse_destination(form)
    return await run_in_threadpool(_bulk_export, request, conn, ids, form, options)


def _bulk_export(
    request: Request, conn: sqlite3.Connection, ids: Sequence[int], form: Mapping[str, Any],
    options: ExportOptions,
) -> Response:
    cap = ZIP_CAP_BYTES if parse_destination(form) == "download" else None
    try:
        members, skipped = collect(conn, ids, options, cap=cap)
    except TooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    if not members:
        raise HTTPException(
            status_code=409,
            detail="none of the selected files has a transcript yet: "
            + ", ".join(item.title for item in skipped),
        )
    return _deliver(
        request, conn, form, members, skipped, options,
        zip_name=BULK_ZIP_NAME, rows=_rows(conn, ids), bulk=True,
    )
