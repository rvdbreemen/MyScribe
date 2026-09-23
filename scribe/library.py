"""An existing MyScribe library: found, looked at without a write, adopted.

The first-run sitting asks whether a library already exists (TASK-089.19, the
spec's question 2). Three things make that less innocent than it sounds, and
each one shaped a function here.

* Looking is a write if it is done carelessly. `db.connect` switches the file
  to WAL, and without a path it renames a `scribe.db` from before the rename.
  A plain `mode=ro` open of a database that has a `-wal` beside it writes the
  wal-index. So `looked_at` never goes through `db.connect`, opens with
  `immutable=1` where no `-wal` stands beside the database, and reads a copy
  in a temporary folder where one does.
* Migrating only goes forward. `db.migrate` walks `range(version + 1,
  SCHEMA_VERSION + 1)`, which is empty for a library newer than this app, and
  the app then runs on a schema it does not know without a word. `refusal`
  says so before anything is written.
* A library somebody is using must not be migrated under them. `/health`
  names the data directory it serves (TASK-089.17); an answer without that
  field is an older MyScribe, which is doubt, and doubt refuses. A job row in
  state `running` refuses whatever port its app is on.

Nothing here decides a question or writes an answer: `scribe.setup` asks, and
this module answers what the sitting needs to know about a folder.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

from scribe import credentials, db, env, paths

VARIABLE = "SCRIBE_DATA_DIR"
"""The one variable that names a library (`scribe/paths.py:4`)."""

APP_NAME = "MyScribe"

CHANGE_LIBRARY = (
    "To use another library, quit MyScribe and start it with --setup (the Setup button), which offers "
    "a library found elsewhere while this one holds no recordings; in a clone, SCRIBE_DATA_DIR in .env "
    "or install.py --data-dir does the same. Not from Settings: the settings live in the library's own "
    "database, and switching it from inside the app would change the database under the process "
    "serving it."
)
"""Where the library question can be answered later (criterion 10), said once:
the question's `answer_later` and Settings > This machine both show it. The
stated exception to "everything is reachable from Settings" (TASK-089.11,
criterion 4, names it)."""


@dataclass(frozen=True)
class Library:
    """What the sitting shows about one library it found, and nothing it
    wrote to find out."""

    data_dir: Path
    database: Path
    legacy: bool
    recordings: int
    size: int
    user_version: int
    never_asked: int
    running: int
    where: str = ""


def database_in(folder: Path) -> Path | None:
    """The library's database in `folder`: `myscribe.db`, else a `scribe.db`
    from before the 2026-09-06 rename, else None."""
    for name in (paths.DB_PATH.name, paths.LEGACY_DB_NAME):
        candidate = Path(folder) / name
        if candidate.is_file():
            return candidate
    return None


SIDECARS = ("-wal", "-shm")


@contextlib.contextmanager
def looked_at(database: Path) -> Iterator[sqlite3.Connection]:
    """The database opened so that nothing beside it is created, renamed or
    modified - no `-wal`, no `-shm`, no migration (criterion 2).

    `immutable=1` where no `-wal` stands beside it: measured in
    `scribe/setup.py`'s `read_only`, that open creates nothing. Where one does,
    immutable would read past the log and report the state before the last
    checkpoint - an old `user_version` among other things, which is the number
    the newer-schema refusal turns on. The plain `mode=ro` open that reads the
    log writes the wal-index beside it. So the three files are copied into a
    temporary folder and the copy is read, which costs the size of the
    database and touches nothing of the original.
    """
    database = Path(database)
    wal = database.with_name(database.name + "-wal")
    if not wal.exists():
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
        return
    with tempfile.TemporaryDirectory(prefix="myscribe-look-") as scratch:
        copy = Path(scratch) / database.name
        shutil.copyfile(database, copy)
        for suffix in SIDECARS:
            source = database.with_name(database.name + suffix)
            if source.exists():
                shutil.copyfile(source, copy.with_name(copy.name + suffix))
        conn = sqlite3.connect(str(copy))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def _count(conn: sqlite3.Connection, sql: str) -> int:
    """One number, or 0 for a library from before the table it asks about."""
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


NEVER_ASKED_SQL = """
    SELECT m.id AS media_id
      FROM media m
      JOIN run r ON r.media_id = m.id AND r.is_current = 1
     WHERE m.trashed_at IS NULL
       AND m.private = 0
       AND EXISTS (SELECT 1 FROM word w WHERE w.run_id = r.id AND w.speaker IS NOT NULL)
       AND NOT EXISTS (SELECT 1 FROM llm_output o WHERE o.media_id = m.id AND o.kind = 'speakers')
"""
"""The recordings `finalize.sweep_speaker_passes` would queue: diarized, never
asked who is speaking, not in the bin, not pinned private. The sweep's own
conditions, minus the one about a job already queued - a queued pass has not
been sent yet either, and the sentence is about what would be sent."""


def _never_asked(conn: sqlite3.Connection) -> int:
    """How many recordings a cloud provider chosen later would be sent, once
    each (criterion 7). A folder pinned private above a recording counts too,
    through the same check the sweep makes."""
    from scribe.llm import privacy

    try:
        rows = conn.execute(NEVER_ASKED_SQL).fetchall()
    except sqlite3.Error:
        return 0
    counted = 0
    for row in rows:
        try:
            if privacy.is_private(conn, int(row["media_id"])):
                continue
        except (LookupError, sqlite3.Error):
            continue
        counted += 1
    return counted


def size_on_disk(folder: Path) -> int:
    """Every byte under the library's folder: the database and the media.
    A file that vanishes or cannot be read while this walks is left out."""
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def inspect(folder: Path, where: str = "") -> Library | None:
    """What the sitting says about the library in `folder`, or None when
    there is none there or it cannot be read as one."""
    folder = Path(folder)
    database = database_in(folder)
    if database is None:
        return None
    try:
        with looked_at(database) as conn:
            version = _count(conn, "PRAGMA user_version")
            recordings = _count(conn, "SELECT COUNT(*) FROM media WHERE trashed_at IS NULL")
            never_asked = _never_asked(conn)
            running = _count(conn, "SELECT COUNT(*) FROM job WHERE status = 'running'")
    except (sqlite3.Error, OSError):
        return None
    return Library(
        data_dir=folder,
        database=database,
        legacy=database.name == paths.LEGACY_DB_NAME,
        recordings=recordings,
        size=size_on_disk(folder),
        user_version=version,
        never_asked=never_asked,
        running=running,
        where=where,
    )


# --- where a library can be -------------------------------------------------------


def default_home(platform: str = sys.platform, environ: dict | None = None) -> Path:
    """The per-user MyScribe folder a release installs into.

    Spelled a second time, on purpose: it is the launcher's `default_home`, and
    the launcher is stdlib-only and imports nothing from the app, nor the app
    from it (ADR-011). `tests/test_library.py` compares the two, so they
    cannot drift apart unnoticed.
    """
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def same(one: Path | str, other: Path | str) -> bool:
    """One folder reached by two spellings is one folder (TASK-089.17's rule)."""
    return os.path.normcase(os.path.realpath(str(one))) == os.path.normcase(os.path.realpath(str(other)))


def machine_candidates() -> list[tuple[Path, str]]:
    """Every folder on this machine that may hold a library, with where it was
    found - whether it does is `found`'s question.

    Criterion 1's list: `<repo>/data`, the per-user home, and a
    SCRIBE_DATA_DIR in any environment layer - the process, `.env`, and on
    Windows the user and machine hives. In a release `env.REPO_DIR` is the
    payload's `app/`, so a clone's library elsewhere on the disk is never
    among these; that is why the question also takes a folder somebody names.

    tests/conftest.py replaces this for every test, so no test can open a
    library that lives on the machine running it, not even read-only.
    """
    listed: list[tuple[Path, str]] = [
        (env.REPO_DIR / "data", "this checkout's data folder"),
        (default_home() / "data", "the per-user MyScribe folder"),
    ]
    named = os.environ.get(VARIABLE, "").strip()
    if named:
        listed.append((Path(named), f"{VARIABLE} in the environment"))
    values, dotenv = credentials.dotenv_values()
    if (values.get(VARIABLE) or "").strip():
        listed.append((Path(values[VARIABLE].strip()), f"{VARIABLE} in {dotenv}"))
    for hive, value in credentials.registry_hits(VARIABLE):
        if value.strip():
            listed.append((Path(value.strip()), f"{VARIABLE} in {hive}"))
    return listed


def found(target: Path, extra: Iterable[Path] = (), *,
          candidates: Callable[[], list[tuple[Path, str]]] | None = None) -> list[Library]:
    """The libraries on this machine other than `target`, each once.

    `extra` is a folder somebody named: read the same way and listed after
    the ones found, so a named folder shows its numbers before anybody says
    yes to it (criterion 7).
    """
    listed = (candidates or machine_candidates)() + [(Path(folder), "the folder you named") for folder in extra]
    libraries: list[Library] = []
    for folder, where in listed:
        if same(folder, target) or any(same(folder, seen.data_dir) for seen in libraries):
            continue
        library = inspect(folder, where)
        if library is not None:
            libraries.append(library)
    return libraries


def recordings_in(target: Path) -> int:
    """How many recordings the target library holds; 0 for none at all.

    Whether the target "already holds a library" is read from this and not
    from the file: every sitting that applies anything creates an empty,
    migrated database first, and that is not a library anybody would miss.
    """
    library = inspect(target)
    return library.recordings if library is not None else 0


# --- what is refused, and why ----------------------------------------------------


def newer_than_this_app(library: Library) -> str:
    """Criterion 5, in one sentence, or ""."""
    if library.user_version <= db.SCHEMA_VERSION:
        return ""
    return (f"The library at {library.data_dir} was written by a newer MyScribe (schema "
            f"{library.user_version}, this version knows {db.SCHEMA_VERSION}); this version "
            "cannot use it and has not touched it.")


def job_running(library: Library) -> str:
    if not library.running:
        return ""
    return (f"A job is running in the library at {library.data_dir}: a MyScribe is working in it. "
            "Stop that MyScribe first; nothing was migrated.")


def served(library: Library, answered: dict | None, port: int) -> str:
    """Criterion 11 on the port that was asked, or "".

    Three answers and one of them passes. Nothing on the port passes. An app
    that names this library refuses. An app that answers without naming what
    it serves is a MyScribe from before TASK-089.17, and that is doubt, which
    refuses the same way - unlike `--prove`, where False only means "do not
    load", here the wrong answer migrates somebody's library under them.
    """
    if answered is None:
        return ""
    theirs = str(answered.get("data_dir") or "").strip()
    if not theirs:
        return (f"A MyScribe on port {port} does not say which library it serves, so it may be "
                f"serving {library.data_dir}. Stop that MyScribe first; nothing was migrated.")
    if same(theirs, library.data_dir):
        return (f"The MyScribe on port {port} is serving the library at {library.data_dir}. "
                "Stop that MyScribe first; nothing was migrated.")
    return ""


def adopt(library: Library) -> Path:
    """Use this library from now on: rename a legacy database through
    `paths.adopt_legacy_db`, then migrate it to this version's schema.

    In place. Nothing is copied and nothing is moved: the sitting said so
    before the yes (criterion 4), and the release is pointed at it rather than
    it at the release. Returns the data directory that is now in use.
    """
    target = library.data_dir / paths.DB_PATH.name
    if library.legacy:
        paths.adopt_legacy_db(target)
    conn = db.connect(target)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    return library.data_dir
