"""`python -m scribe.export`: the exporters from a terminal.

The third door to `scribe.exports`, after the Advanced Export dialog and the
library's bulk action, for the things a browser is the wrong tool for: a
scheduled task that drops last night's recordings as SRT next to the videos,
a re-export of a whole library after a preset was tuned, a shell script that
wants one Markdown note per media. It is thin in exactly the way the routes
are (ADR-003): `doc.load` reads a media's words once, the writers in
`exports.FORMATS` turn that document into bytes, and this module only parses
flags into an `ExportOptions`, calls them and writes the files. Nothing
grouped is computed here and nothing is written to the database - a test
counts the connection's changes to prove it.

    python -m scribe.export 42 --format srt --format vtt
    python -m scribe.export --all --preset youtube --out D:\\subs
    python -m scribe.export --folder 3 --format md --timestamps sentence --front-matter

**What to export** is one of three things: media ids, `--all` (every media
in the library that is not in the trash) or `--folder ID` (that folder's own
media, not its subfolders' - the library page's view of a folder). An id
named by hand must be exportable - it has a transcript, and the encoding
holds every character of its text. Every named media's files are built
before the first is written, so when one cannot be, the export exits 1 with
the reason and writes nothing: a script that asked for three files should
not find two and a message. `--all` and `--folder` skip a media that cannot
be exported, say so on stderr and go on, as the bulk action does; they build
and write one media at a time, since a library need not fit in memory.

**The options** are `ExportOptions`' fields, one flag each, generated from
the model so a field added there is a flag here without anyone remembering
to add it: `--cpl 42`, `--timestamps sentence`, `--speakers/--no-speakers`,
`--format srt` (repeatable). `--preset NAME` starts from a preset - a
built-in (`PRESETS`) or one saved from the dialog (`export_preset`) - and a
flag given explicitly overrides the preset's value. A flag not given means
"the preset's value", which is why every flag defaults to None rather than
to the model's default.

**The files** go into `--out` (created if missing; the current directory by
default), one per media per format under `options.filename_for`'s name,
written whole to a `.part` name and renamed into place - and they are
exactly the files the dialog's write-to-folder writes, because the members
are built and written by `scribe.web.exports_ui`'s own helpers:
`distinct_members` (the HTML bundle with its recording embedded or its
sidecar beside it; two media of one title told apart as `Same.srt` and
`Same (2).srt`) and `write_into`. Reusing them costs a second of import
(FastAPI comes along) and buys an export that cannot drift from the
dialog's; forty lines of the same thing here would have been the other
choice, and drift is the bug that choice grows.

Unlike the dialog, `--out` is not held to the browse roots
(`fsbrowse.is_allowed`): those guard a localhost page against a stray form
post, and a shell already has every folder the user has. One path per file
written goes to stdout, so the output pipes; everything else goes to stderr.
Exit codes: 0 when every requested file was written; 1 with the reason when
the export could not be done (no such media, no transcript, a character the
encoding cannot hold, no database, a folder that cannot be written); 2 with
the usage line when the command itself is wrong (an unknown format or
preset, a value the options refuse, no target).

`.env` is read by `scribe/export/__main__.py` before this module is
imported, for the reason `python -m scribe` reads it before importing the
app: `SCRIBE_DATA_DIR` has to be set by the time `scribe.paths` is. The
database is never migrated from here - the app owns its schema - and never
created: an export with no database is an export with nothing to export.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import typing
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from fastapi import HTTPException
from pydantic import ValidationError

from scribe import db, exports, paths
from scribe.exports import doc as export_doc
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import PRESETS, ExportOptions
from scribe.web import exports_ui

PROG = "python -m scribe.export"

# Exit codes: every requested file written; the export could not be done;
# the command itself is wrong (argparse's own code for that).
OK, FAILED, USAGE = 0, 1, 2

# The flag for `formats`: singular and repeatable, unlike the field.
FORMAT_FLAG = "--format"

# Metavar and help per option field. A field missing here still gets its
# flag, described by its name; a bool needs no metavar.
FLAGS: dict[str, tuple[str, str]] = {
    "formats": ("FORMAT", "a format to write"),
    "speakers": ("", "speaker names in the text"),
    "timestamps": ("WHEN", "how often a timestamp appears in the text formats"),
    "timestamp_format": ("STYLE", "how a timestamp is written"),
    "interval_seconds": ("SECONDS", "seconds between timestamps for --timestamps interval"),
    "txt_layout": ("LAYOUT", "the layout of the plain-text file"),
    "cpl": ("N", "characters per subtitle line"),
    "max_lines": ("N", "lines per subtitle cue"),
    "max_cps": ("N", "the reading speed a cue may ask for, in characters per second"),
    "min_duration": ("SECONDS", "the shortest a cue is shown"),
    "max_duration": ("SECONDS", "the longest a cue is shown"),
    "cue_gap": ("SECONDS", "the silence kept between two cues"),
    "encoding": ("NAME", "the text encoding of the file"),
    "crlf": ("", "Windows line endings"),
    "filename_template": (
        "TEMPLATE",
        "the filename without its extension; fields {title} {date} {model} {lang}",
    ),
    "include_confidence": ("", "confidence columns in CSV and JSON"),
    "front_matter": ("", "YAML front matter at the top of the Markdown"),
}

EXAMPLES = """\
examples:
  python -m scribe.export 42 --format srt --format vtt
  python -m scribe.export --all --preset youtube --out D:\\subs
  python -m scribe.export --folder 3 --format md --timestamps sentence --front-matter
"""


class Refused(Exception):
    """The export cannot be done; the message is the reason, for stderr (exit 1)."""


class BadUsage(Exception):
    """The command is wrong in a way argparse could not see (exit 2, with usage)."""


# --- the command line ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Export transcripts from the library, without the browser.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "media_id",
        nargs="*",
        type=int,
        metavar="MEDIA_ID",
        help="a media to export, by id (the number in its /media/<id> address); several may be given",
    )
    which = parser.add_argument_group("or, instead of ids")
    which.add_argument(
        "--all",
        action="store_true",
        help="every media in the library that has a transcript; the trash is left out",
    )
    which.add_argument(
        "--folder",
        type=int,
        metavar="ID",
        help="every media in the folder with this id (not in its subfolders) that has a transcript",
    )
    how = parser.add_argument_group("how")
    how.add_argument(
        "--preset",
        metavar="NAME",
        help=(
            f"start from a preset: {', '.join(PRESETS)}, or the name of one saved from the"
            " export dialog; an option flag overrides the preset's value"
        ),
    )
    how.add_argument(
        "--out",
        metavar="DIR",
        help="the folder the files are written into, created if missing (default: the current directory)",
    )
    _add_option_flags(
        parser.add_argument_group(
            "export options",
            "the fields of the export dialog; each defaults to the preset's value, or the value shown",
        )
    )
    return parser


def _add_option_flags(group: argparse._ArgumentGroup) -> None:
    """One flag per `ExportOptions` field, typed from the model: a bool is an
    on/off pair, a Literal a choice, a number a number. Every default is
    None - "not given" - so the preset's value shows through."""
    defaults = ExportOptions()
    for name, field in ExportOptions.model_fields.items():
        flag = "--" + name.replace("_", "-")
        metavar, text = FLAGS.get(name, ("VALUE", name.replace("_", " ")))
        default = getattr(defaults, name)
        annotation = field.annotation
        if name == "formats":
            group.add_argument(
                FORMAT_FLAG,
                dest=name,
                action="append",
                choices=list(exports.FORMATS),
                metavar=metavar,
                help=(
                    f"{text}; one of {', '.join(exports.FORMATS)}; repeat for several"
                    f" (default: {', '.join(default)})"
                ),
            )
        elif annotation is bool:
            group.add_argument(
                flag,
                dest=name,
                action=argparse.BooleanOptionalAction,
                default=None,
                help=f"{text} (default: {'on' if default else 'off'})",
            )
        elif typing.get_origin(annotation) is typing.Literal:
            values = typing.get_args(annotation)
            group.add_argument(
                flag,
                dest=name,
                choices=values,
                default=None,
                metavar=metavar,
                help=f"{text}; one of {', '.join(values)} (default: {default})",
            )
        elif annotation in (int, float, str):
            group.add_argument(
                flag, dest=name, type=annotation, default=None, metavar=metavar,
                help=f"{text} (default: {default})",
            )
        else:
            raise TypeError(f"ExportOptions.{name} is {annotation!r}; no flag knows how to take one")


def options_from(args: argparse.Namespace, base: ExportOptions) -> ExportOptions:
    """The options the flags describe, over ``base`` (the preset, or the
    defaults). Validated as a whole, so a flag that contradicts the preset
    (a max_duration below its min_duration) is refused like any other."""
    given = {
        name: getattr(args, name)
        for name in ExportOptions.model_fields
        if getattr(args, name) is not None
    }
    try:
        return ExportOptions.model_validate({**base.model_dump(), **given})
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'options'}: {error['msg']}"
            for error in exc.errors()
        )
        raise BadUsage(problems)


# --- what the database holds ------------------------------------------------------------


def open_database() -> sqlite3.Connection:
    """The library's database, where `python -m scribe` keeps it. Not created
    and not migrated: both are the app's to do."""
    path = paths.DB_PATH
    if not path.is_file():
        raise Refused(
            f"no database at {path}: start the app once (python -m scribe),"
            " or set SCRIBE_DATA_DIR to the data folder it uses"
        )
    conn = db.connect(path)
    with db.LOCK:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < db.SCHEMA_VERSION:
        conn.close()
        raise Refused(
            f"the database at {path} is at schema version {version} and this code"
            f" needs {db.SCHEMA_VERSION}: start the app once to migrate it"
        )
    return conn


def preset_named(conn: sqlite3.Connection, name: str | None) -> ExportOptions:
    """The options ``--preset`` names: a built-in, else one saved from the
    dialog, matched without regard to case (the dialog refuses two saved
    names that differ only in case). The defaults when no preset was asked for."""
    if name is None:
        return ExportOptions()
    key = name.strip().casefold()
    if key in PRESETS:
        return PRESETS[key]
    saved = exports_ui.saved_presets(conn)
    for preset in saved:
        if preset["name"].casefold() == key:
            return preset["options"]
    known = list(PRESETS) + [preset["name"] for preset in saved]
    raise BadUsage(f"unknown preset {name!r}; one of {', '.join(known)}")


def target_ids(conn: sqlite3.Connection, args: argparse.Namespace) -> list[int]:
    """The media ids the command names: the ids given (each once, in their
    order), or the library's, or a folder's - the latter two in id order and
    without the trash."""
    if args.all:
        sql, params = "SELECT id FROM media WHERE trashed_at IS NULL ORDER BY id", ()
    elif args.folder is not None:
        with db.LOCK:
            known = conn.execute("SELECT 1 FROM folder WHERE id=?", (args.folder,)).fetchone()
        if known is None:
            raise Refused(f"no folder with id {args.folder}")
        sql, params = (
            "SELECT id FROM media WHERE folder_id=? AND trashed_at IS NULL ORDER BY id",
            (args.folder,),
        )
    else:
        return list(dict.fromkeys(args.media_id))
    with db.LOCK:
        return [row["id"] for row in conn.execute(sql, params).fetchall()]


# --- the files ---------------------------------------------------------------------------


def members_of(
    document: TranscriptDoc, options: ExportOptions, taken: set[str]
) -> list[exports_ui.Member]:
    """One media's files, built in memory and not yet written: the dialog's
    own members (see the module docstring). `Refused` names what stops
    them - a character the encoding cannot hold, or no filename left that
    is not already used in this export."""
    audio = exports_ui.audio_for(document.media, options)
    try:
        return exports_ui.distinct_members(document, options, audio, taken)
    except UnicodeEncodeError as exc:
        raise Refused(
            f"media {document.media['id']} ({document.title!r}) cannot be written as"
            f" {options.encoding}: {exc}; choose --encoding utf-8"
        )
    except HTTPException as exc:  # distinct_members ran out of distinct names
        raise Refused(exc.detail)


def named_exports(
    conn: sqlite3.Connection, ids: Sequence[int], options: ExportOptions
) -> list[list[exports_ui.Member]]:
    """Every named id's files, all built before any is written: an id named
    by hand is a promise, and nothing is written when one cannot be kept.
    `Refused` at the first id without a transcript, then at the first whose
    files cannot be built. Held in memory together, which a list typed by
    hand allows and a library would not."""
    try:
        documents = [export_doc.load(conn, media_id) for media_id in ids]
    except export_doc.NoTranscript as exc:
        raise Refused(str(exc))
    taken: set[str] = set()
    return [members_of(document, options, taken) for document in documents]


def found_exports(
    conn: sqlite3.Connection, ids: Sequence[int], options: ExportOptions
) -> Iterator[list[exports_ui.Member]]:
    """The files of the ids that can be exported, one media at a time since
    ``--all`` may be a library; a media that cannot be - no transcript, a
    character the encoding cannot hold - is skipped with a line on stderr."""
    taken: set[str] = set()
    for media_id in ids:
        try:
            members = members_of(export_doc.load(conn, media_id), options, taken)
        except (export_doc.NoTranscript, Refused) as exc:
            print(f"skipped: {exc}", file=sys.stderr)
            continue
        yield members


def output_folder(raw: str | None) -> Path:
    """``--out`` as an absolute path: the current directory when not given.
    Not created yet - that waits for the first file, so an export with
    nothing to write leaves no empty folder behind."""
    out = Path(os.path.abspath(raw or os.getcwd()))
    if out.exists() and not out.is_dir():
        raise Refused(f"{out} is not a directory")
    return out


def export_into(batches: Iterable[list[exports_ui.Member]], out: Path) -> int:
    """Write each media's files into ``out`` and print every path; returns
    how many media were written. The write is the dialog's (`write_into`:
    whole to a `.part` name, renamed into place), so the files are the same."""
    written = 0
    for members in batches:
        out.mkdir(parents=True, exist_ok=True)
        for item in exports_ui.write_into(members, out):
            print(item.path)
        written += 1
    return written


def _nothing(args: argparse.Namespace, ids: Sequence[int]) -> str:
    where = f"folder {args.folder}" if args.folder is not None else "the library"
    if not ids:
        return f"nothing to export: {where} has no media outside the trash"
    return f"nothing to export: each of the {len(ids)} media in {where} was skipped, for the reason above"


def _tolerant_streams() -> None:
    """Never let a title's character crash the run after its file is
    written: a redirected stdout on Windows is the console code page, and a
    path is printed whatever it holds."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


def _failed(reason: object) -> int:
    print(f"{PROG}: {reason}", file=sys.stderr)
    return FAILED


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if sum((bool(args.media_id), args.all, args.folder is not None)) != 1:
        parser.error("give what to export: media ids, --all, or --folder ID (one of the three)")
    _tolerant_streams()
    try:
        conn = open_database()
    except Refused as exc:
        return _failed(exc)
    try:
        try:
            options = options_from(args, preset_named(conn, args.preset))
        except BadUsage as exc:
            parser.error(str(exc))
        out = output_folder(args.out)
        ids = target_ids(conn, args)
        if args.media_id:
            batches: Iterable[list[exports_ui.Member]] = named_exports(conn, ids, options)
        else:
            batches = found_exports(conn, ids, options)
        if not export_into(batches, out):
            raise Refused(_nothing(args, ids))
    except Refused as exc:
        return _failed(exc)
    except OSError as exc:
        return _failed(exc)
    finally:
        conn.close()
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
