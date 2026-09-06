"""Watch folders: a file dropped in a folder becomes a recording, by itself.

The fourth door, and the only one nobody is standing at. An upload, a path, a
link and the microphone all have a person watching the result; this one fires
while the laptop is closed, so every decision here is about being certain
rather than being quick.

**Quiescence is the whole design.** A file that is still being copied has a
growing size, and the store is content-addressed: hash it halfway and the
sha256 of half a recording becomes a permanent row pointing at a truncated
file, while the finished copy arrives later, hashes differently and is
ingested a second time. Nothing wrong is ever reported. So a created or
modified file is only taken in once its size *and* mtime have been unchanged
for `QUIESCE_SECONDS`, measured across ticks - which means the earliest a file
can land is one poll interval plus the quiet interval after it stopped
changing. Slow on purpose.

**The observer is a hint; the folder is the truth.** `reconcile` walks every
enabled folder at startup and ingests anything whose content is not already in
`media`. That is what makes a file dropped while the app was closed still
arrive, and it is the fallback for the events the OS never delivers - network
drives drop them routinely, and Windows' `ReadDirectoryChangesW` coalesces
under load. It gates on quiescence too, by mtime age rather than by two looks,
because there is no previous look at startup and because startup is precisely
when a sync client is mid-download; a file written in the last
`QUIESCE_SECONDS` is handed to the same pending table the observer's events go
to, and the next tick decides. The cost is honest and worth naming: reconcile
hashes every file it has no measurement for, which the first time is every file
in every watched folder - so what it has already read is written down and
survives a restart (`cache_path`), or a 100 GB archive would be 100 GB of reads
per app start. It is also why an observer that cannot be built at all is a
warning on stderr rather than a dead thread (`_open_observer`): with no events,
watch folders still work as far as the last startup, and a thread that vanished
would look exactly like a folder nobody has used. The same rule holds one
folder down: an individual folder whose watch cannot be opened is marked and
carried on from, never a reason for the folders after it to get no events.

**A folder that is watched is one the app may still read from.** Every entry
point reads the rows through `watchable`, which asks the `fsbrowse` roots
afresh rather than trusting the check made when the folder was added - so
narrowing "what this app may read" in Settings reaches this thread too, and
does not leave it ingesting from a folder the transcribe dialog and
`POST /api/media` have both begun refusing.

**Deepest folder wins.** Watched folders may nest, and both entry points ask
`folder_for` which one claims a file, so a folder configured inside another
transcribes with its own options whether the file was dropped while the app was
running or found by the walk at the next start.

**The user's files are never touched.** Ingest hardlinks, exactly like the
path picker (`media.ingest_path`), so a watched folder is left byte for byte
as it was - nothing moved, nothing renamed, nothing marked as processed.

**A file the library already has queues no job.** That is a deliberate
divergence from `POST /api/media`, whose docstring says the opposite: there a
person asked for a second transcription, which is a perfectly good request.
Here nobody asked. A cloud-sync client re-stamping a file, an antivirus
touching it, a folder re-appearing after a reconnect - each of those is a
modify event, and each would otherwise cost a GPU hour for a recording that
already has a transcript.

**What is skipped, and why each one is real:** anything without a media
extension (`probe.MEDIA_EXTENSIONS`), hidden files (a dotfile *or* Windows'
hidden attribute - cloud-sync scratch uses the attribute, not the dot),
partial-download suffixes, and anything under `paths.DATA_DIR`. That last one
is not paranoia: the default browse root on Windows is the whole drive, so a
watched folder may well contain this app's own `data/`, where `work/*.wav`,
`work/rec/*/*.webm` and the store itself all pass the extension filter. Left
in, the app would ingest its own scratch, transcribe it, and produce more.

Nothing here loads a model or reaches a network (ADR-001): a tick stats files,
hashes bytes, and writes two rows. The transcription it queues is a job, and
happens in a runner child like every other.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import traceback
from itertools import islice
from pathlib import Path
from typing import Callable, NamedTuple, Sequence

from pydantic import ValidationError

from scribe import db, fsbrowse, jobs, media, paths
from scribe.options import TranscribeOptions
from scribe.stages.probe import MEDIA_EXTENSIONS

QUIESCE_SECONDS = 5.0
"""How long a file's size and mtime must hold still before it is taken in.

Five seconds is the plan's number, and it is a compromise a user can feel: a
copy that stalls for six seconds mid-way is ingested truncated, and every
recording waits at least this long after it lands. The reconcile is what makes
the first failure recoverable - the finished file hashes differently, so it
arrives as its own row on the next start rather than being lost."""

POLL_INTERVAL = 2.0
"""Seconds between ticks. Each tick reconciles the observer's schedules against
the enabled `watch_folder` rows and then pumps the pending files, so a folder
added in Settings starts being watched within one interval rather than at the
next app start."""

PARTIAL_SUFFIXES = frozenset(
    {".part", ".crdownload", ".partial", ".tmp", ".download", ".opdownload", ".!qb"}
)
"""What a downloader calls a file it has not finished writing.

Mostly redundant with the extension filter - `video.mp4.part` ends in `.part`,
which is not a media extension - and kept anyway, because the pattern also
appears in the middle (`Song.part.mp3` while a sync client swaps a file), and
because a downloader that starts using a media extension for its scratch would
otherwise be a silent regression."""

TRANSCRIBE_JOB_TYPE = "transcribe"
"""What a settled file is queued as. Spelled here rather than imported from
`scribe.web.transcribe_dialog`, for the reason `url_stage` gives: nothing under
`scribe/ingest` may depend on a router."""

OBSERVER_JOIN_SECONDS = 5.0
"""How long the loop waits for the observer's own threads on the way out."""

SAVE_EVERY = 2_000
"""How many files a reconcile walks between writing its measurements down.

Only about the abrupt exits: `stop()` and the loop's `finally` both save, so
the clean routes were already covered. This is for the hard kill and for the
walk phase, where `_stop_event` is not checked."""

TAKEN_MEMORY = 100_000
"""How many files the "already taken in, do not open again" cache remembers.

That cache is only a cache: forgetting an entry costs one re-hash of a file
the store then dedupes anyway, so bounding it is free and not bounding it is
not. Measured - a `reconcile` over a folder of 500 recordings leaves 500
entries, about 160 bytes each, and nothing ever removes them; a synced folder
of twenty thousand files would hold that for the life of the process, and a
folder churned daily would grow past its own size. Ten thousand is more than
a reconcile of a large folder puts in, and a few megabytes at worst.

Eviction is oldest-first, which dicts give for nothing: the entries most worth
keeping are the ones just written, because a modify storm follows an ingest. It
happens once, when the cache is written (`_trim_taken`), never as entries
arrive - doing it on the way in made the bound a cliff rather than a slope, and
`_remember_taken` says why.

Ten thousand was too small for the archive this file's own docstring promises
to handle. 100 GB at 10 MB a recording is exactly 10,000 files, so the stated
scale landed precisely on the old bound and got nothing from the cache at all.
Measured on 2026-09-04: 146 bytes an entry on disk, so 100,000 entries is about
15 MB of JSON, ~630 ms to write at the end of a pass and ~420 ms to read at
startup. That is the trade in full - a little over half a second of JSON
against re-reading a hundred gigabytes."""


class _Pending(NamedTuple):
    """A file we are waiting on, and what it looked like when we last looked.

    ``since`` is when the snapshot below it was taken - not when the file was
    first noticed. Every change restarts it, which is what "unchanged for
    QUIESCE_SECONDS" means.

    The table of these is deliberately *not* bounded, where `_taken` is
    (`TAKEN_MEMORY`), and the asymmetry is the answer rather than an oversight:
    `_taken` only ever grows, because nothing but eviction removes an entry
    once a file has been dealt with - and it no longer even starts empty, since
    it is loaded from the last run's cache file and trimmed to `TAKEN_MEMORY`
    on the way in (`load_cache`). `_pending`, meanwhile, is emptied by every
    tick of everything it settled, ingested, or found gone, and lives only in
    this process. What it holds at any moment is one entry per file currently
    being written to in a watched folder - a number bounded by how many things
    a person and their sync client are copying at once, and one that falls back
    to zero on its own.
    """

    path: Path
    size: int
    mtime: float
    since: float


_UNSEEN = (-1, -1.0, 0.0)
"""The snapshot a freshly noticed file gets: one no real file can have.

So the first tick after a notice always records a change and never ingests,
however long ago the file actually stopped growing. That costs one poll
interval and buys the guarantee that no file is ever taken in on the strength
of a single measurement."""


class _Taken(NamedTuple):
    """A file already dealt with, and the bytes it held when it was.

    ``size`` and ``mtime`` are the cheap question - has anything written to
    this since - and ``sha256`` is what the answer is worth: it is the row the
    library would hold, so a hit can be checked against the library rather
    than merely believed. See `Watcher._already_taken`.
    """

    size: int
    mtime: float
    sha256: str


# --- the rows -------------------------------------------------------------------------


def folders(conn: sqlite3.Connection, *, enabled_only: bool = True) -> list[dict]:
    """The watch folders, each with its options parsed and its reach checked.

    ``allowed`` is whether `fsbrowse`'s roots still cover this folder. It is
    read fresh on every call rather than remembered from when the row was
    added, because the roots are the one control for what this app may read and
    narrowing them has to reach the watcher too - see `watchable`. Every row
    carries the flag, switched-off ones included, so the settings page can say
    *why* a folder it still lists is not being read from.

    One extra SELECT and one `resolve()` per folder per call. This runs every
    poll interval over a handful of rows; the walk it guards reads gigabytes.
    """
    sql = "SELECT * FROM watch_folder"
    if enabled_only:
        sql += " WHERE enabled=1"
    with db.LOCK:
        rows = conn.execute(sql + " ORDER BY id").fetchall()
    roots = fsbrowse.allowed_roots(conn)
    return [
        {
            **dict(row),
            "options": options_of(row["options_json"]),
            "allowed": _within_roots(row["path"], roots),
        }
        for row in rows
    ]


def watchable(conn: sqlite3.Connection) -> list[dict]:
    """The folders the watcher may actually read from right now.

    Enabled *and* still inside the browse roots. The second half was the one
    nobody asked: a folder's containment was checked when it was added and
    never again, so narrowing the roots in Settings - which is how a user says
    "stop reading there" - stopped the transcribe dialog and `POST /api/media`
    and left this thread ingesting from the folder both of them had begun
    refusing. Every entry point asks this rather than `folders`.
    """
    return [folder for folder in folders(conn) if folder["allowed"]]


def _within_roots(path: str | Path, roots: Sequence[Path]) -> bool:
    """Whether the browse roots still cover ``path``; True when unanswerable.

    `fsbrowse.is_allowed` resolves both sides, and resolving a path on a share
    that has just gone away can raise. That case is "not there", which the
    settings page already says as `missing`; answering it as "outside the
    folders this app may read" would be a different claim and an untrue one, so
    the roots are left to decide again on the next tick.
    """
    try:
        return fsbrowse.is_allowed(path, roots)
    except OSError:
        return True


def options_of(text: str | None) -> TranscribeOptions:
    """The options a stored `options_json` names; the defaults when it cannot.

    Never raises. A row written by an older version, or edited by hand, must
    not stop a folder from being watched - the app's current defaults are a
    better answer than silence, and the settings form shows what is in effect.
    """
    try:
        return TranscribeOptions.model_validate(json.loads(text or "{}"))
    except (TypeError, ValueError, ValidationError):
        return TranscribeOptions()


def add_folder(
    conn: sqlite3.Connection,
    path: str | Path,
    options: TranscribeOptions,
    *,
    enabled: bool = True,
) -> int:
    """Register a folder to watch; returns its id.

    The path is stored as given, not resolved: it is what the settings page
    shows back, and a user who typed a mapped drive letter should see it.
    Raises `sqlite3.IntegrityError` on a path already registered - the column
    is UNIQUE, and two rows for one folder would be two ingests of one file.
    """
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO watch_folder(path, enabled, options_json) VALUES (?, ?, ?)",
            (str(path), 1 if enabled else 0, json.dumps(options.model_dump())),
        )
        conn.commit()
        return cur.lastrowid


def set_enabled(conn: sqlite3.Connection, folder_id: int, enabled: bool) -> bool:
    """Switch a folder on or off; False when there is no such row."""
    with db.LOCK:
        cur = conn.execute(
            "UPDATE watch_folder SET enabled=? WHERE id=?", (1 if enabled else 0, folder_id)
        )
        conn.commit()
        return cur.rowcount == 1


def remove_folder(conn: sqlite3.Connection, folder_id: int) -> bool:
    """Stop watching a folder; False when there is no such row.

    Nothing already ingested is touched. The folder was a source, not an
    owner: the recordings that came from it are in the library on their own.
    """
    with db.LOCK:
        cur = conn.execute("DELETE FROM watch_folder WHERE id=?", (folder_id,))
        conn.commit()
        return cur.rowcount == 1


# --- what may be ingested ----------------------------------------------------------------


def in_data_dir(path: str | Path) -> bool:
    """Whether this path is inside the app's own data directory.

    `fsbrowse.is_allowed` asks exactly this question - is this path under that
    root - and it is reused rather than re-derived so the case-folding and
    symlink resolution are the same rule in both places.
    """
    return fsbrowse.is_allowed(path, (paths.DATA_DIR,))


def is_candidate(path: str | Path) -> bool:
    """Whether this path is a file a watch folder should take in.

    Cheap on purpose: a name test, one `lstat` for the hidden attribute, and
    no open. Everything expensive - the hash, the ingest - happens once, after
    the file has held still.
    """
    path = Path(path)
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes or suffixes[-1] not in MEDIA_EXTENSIONS:
        return False
    if any(suffix in PARTIAL_SUFFIXES for suffix in suffixes):
        return False
    if in_data_dir(path):
        return False
    return not fsbrowse.is_hidden(path)


def folder_for(path: str | Path, watched: Sequence[dict]) -> dict | None:
    """The watched folder this path belongs to, deepest first.

    Deepest, so a folder nested inside another watched folder decides with its
    own options rather than its parent's.
    """
    for folder in sorted(watched, key=lambda row: len(str(row["path"])), reverse=True):
        if fsbrowse.is_allowed(path, (Path(folder["path"]),)):
            return folder
    return None


def take_in(conn: sqlite3.Connection, path: Path, folder: dict) -> dict:
    """Hardlink one file into the store and queue its transcription.

    Always returns the media row, with ``path`` and ``job_id`` on it;
    ``job_id`` is None when the store already had those bytes - see this
    module's docstring for why that is silence rather than a second job. The
    row comes back either way because its ``sha256`` is what the do-not-reopen
    cache stores, and a file the library already had is precisely one we must
    also not hash again.
    """
    row = media.ingest_path(conn, path)
    job_id = None
    if not row.get("deduped"):
        job_id = jobs.enqueue(
            conn,
            TRANSCRIBE_JOB_TYPE,
            media_id=row["id"],
            params=folder["options"].to_params(),
        )
    return {**row, "job_id": job_id, "path": str(path)}


# --- the cache that outlives the process -------------------------------------------------
#
# Why this exists, from the store's own arithmetic (2026-09-04): `reconcile`
# hashes every file it walks and the do-not-reopen cache started empty at every
# launch, so a 100 GB watched archive was 100 GB of reads per app start - about
# eight minutes of a disk saturated at 200 MB/s, several times that over SMB,
# and all of it competing with the running job for the same disk. Nothing about
# it was visible: every recording still arrived, it just arrived after the whole
# folder had been read again.

CACHE_NAME = "watch-taken.json"
CACHE_VERSION = 1


def cache_path() -> Path:
    """Where the do-not-reopen cache is kept between runs.

    A plain JSON file under `paths.DATA_DIR` rather than a table. It is a cache
    of measurements, not something two processes coordinate on, so it has no
    business in the database the web process and the runner children are
    already sharing (ADR-002) - and none in a schema migration either. Losing
    it whole costs exactly one pass of hashing.
    """
    return paths.DATA_DIR / CACHE_NAME


def load_cache() -> dict[str, _Taken]:
    """What the last run remembered, or nothing at all - never an exception.

    A file truncated by a power cut, one written by a version that spelled it
    differently, a data directory that has moved: each of those costs one full
    pass of hashing, and each of them raising here would cost a watcher that
    does not start. Same judgement as `options_of`, for the same reason.
    """
    try:
        with open(cache_path(), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        # The version was written and never read, so the one field that exists
        # to make a format change safe did nothing: the first change of shape
        # would not have cost the pass of hashing promised above but a process
        # that would not start.
        if stored.get("version") != CACHE_VERSION:
            return {}
        taken = {
            str(key): _Taken(int(size), float(mtime), str(sha256))
            for key, (size, mtime, sha256) in stored["files"].items()
        }
    except Exception:  # noqa: BLE001 - the module's own idiom; see _survive
        # Deliberately every exception, not a tuple of the ones imagined. The
        # tuple missed AttributeError, which `{"files": []}` raises because a
        # list has no `.items`, and `Watcher.__init__` runs inside the FastAPI
        # lifespan before it yields - so a malformed cache took the whole web
        # process down rather than costing one pass of hashing.
        return {}
    # Trimmed on the way in as well as on the way out: a file written when
    # TAKEN_MEMORY was larger must not carry that size into this process.
    excess = len(taken) - TAKEN_MEMORY
    if excess > 0:
        # See `_trim_taken`: the same quadratic loop, and this one runs inside
        # `Watcher.__init__`, which the lifespan calls before it yields.
        taken = dict(islice(taken.items(), excess, None))
    return taken


def save_cache(taken: dict[str, _Taken]) -> None:
    """Write the cache where the next run will find it; never raises.

    Through a temporary file and `os.replace`, so a crash mid-write leaves the
    previous cache rather than half of this one - the same rule `media._place`
    keeps for the store's own bytes, and for the same reason: a half-read cache
    would be silently believed.
    """
    target = cache_path()
    tmp = target.with_name(f"{target.name}.{os.getpid()}.part")
    payload = {
        "version": CACHE_VERSION,
        "files": {key: [entry.size, entry.mtime, entry.sha256] for key, entry in taken.items()},
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, target)
    except OSError:
        # A read-only data directory, a full disk. The watcher works without
        # this file; it just works harder, so this is said and not raised.
        traceback.print_exc()
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _in_library(conn: sqlite3.Connection, sha256: str) -> bool:
    """Whether the store still holds these bytes.

    `media.sha256` is UNIQUE, so this is an index hit rather than a scan - one
    per file the cache claims to know, which is what makes it affordable to ask
    it about every file in a folder.
    """
    with db.LOCK:
        row = conn.execute("SELECT 1 FROM media WHERE sha256=? LIMIT 1", (sha256,)).fetchone()
    return row is not None


# --- the watcher ------------------------------------------------------------------------------


class Watcher:
    """One thread: a watchdog observer, a tick, and the startup reconcile.

    `notice` is what the observer's handler calls and it does no I/O beyond
    the candidate test; `pump` is the tick that stats, decides and ingests,
    and takes its clock as an argument. That split is what makes this testable
    without waiting on real filesystem events, which on Windows arrive when
    they arrive.

    ``observer_factory`` builds the observer, so a test can hand in one with
    the threads taken out. It defaults to watchdog's, imported inside the
    factory: a machine without the package can still import this module,
    reconcile a folder and answer the settings page.
    """

    def __init__(
        self,
        db_path: str | Path,
        poll_interval: float = POLL_INTERVAL,
        quiesce: float = QUIESCE_SECONDS,
        observer_factory: Callable[[], object] | None = None,
    ) -> None:
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.quiesce = quiesce
        self._observer_factory = observer_factory or _default_observer
        self.handler = _Handler(self)
        self._pending: dict[str, _Pending] = {}
        # Read here rather than at the top of the loop so a caller can build a
        # Watcher and reconcile on its own thread - which the tests do - and
        # still get the last run's measurements. A megabyte of JSON at worst,
        # and `load_cache` answers with an empty dict for every way it can fail.
        self._taken: dict[str, _Taken] = load_cache()
        # Tri-state on purpose. None means the loop has not run, so nothing is
        # known and nothing is claimed; False means `_open_observer` came back
        # empty and NO folder has a live watch. The per-folder `_unschedulable`
        # set covers the smaller failure; this covers the larger one, which was
        # previously said only on stderr.
        self._observer_live: bool | None = None
        self._taken_dirty = False
        self._watches: dict[str, object] = {}
        # The folders `observer.schedule` refused, by `_key`, with the path as
        # the value. Remembered so the traceback is printed once rather than
        # every poll interval, and so `cannot_watch` can say it on the page.
        self._unschedulable: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # --- the seam the tests drive ---------------------------------------------------

    def notice(self, path: str | Path) -> bool:
        """Remember a file to look at on the next tick; True if it is one.

        Called from the observer's thread, so it does the least it can: no
        stat of its own, no database, no hash. Whether the file has settled is
        `pump`'s question, and asking it here would answer it once instead of
        repeatedly, which is the same as not asking it.
        """
        path = Path(path)
        if not is_candidate(path):
            return False
        with self._lock:
            self._pending.setdefault(_key(path), _Pending(path, *_UNSEEN))
        return True

    def pump(self, conn: sqlite3.Connection, now: float | None = None) -> list[dict]:
        """One tick: ingest every pending file that has held still.

        Returns the media rows taken in, each carrying the `job_id` queued for
        it. A file that changed since the last look has its clock restarted; a
        file that has gone, or that no watched folder claims, is forgotten.
        """
        now = time.time() if now is None else now
        watched = watchable(conn)
        taken: list[dict] = []

        for key, pending in self._due(now):
            try:
                status = pending.path.stat()
            except OSError:
                # Deleted, renamed away, or on a drive that just vanished.
                # Nothing to wait for; an event will bring it back if it returns.
                self._forget(key)
                continue

            snapshot = (status.st_size, status.st_mtime)
            if self._already_taken(conn, key, snapshot):
                self._forget(key)  # unchanged since we took it in; do not re-hash
                continue
            if snapshot != (pending.size, pending.mtime):
                self._remember(key, pending.path, snapshot, now)  # still moving
                continue

            folder = folder_for(pending.path, watched)
            self._forget(key)
            if folder is None:
                # The folder was removed, switched off, or dropped out of the
                # browse roots while we waited - `watchable` asks all three.
                continue
            try:
                result = take_in(conn, pending.path, folder)
            except Exception:  # noqa: BLE001 - one bad file is not a dead watcher
                # A file another program holds open, a cloud placeholder that is
                # not really on this disk, a full drive. Every one of those is
                # temporary, so nothing is written down: the next event for
                # this file, or the next reconcile, opens it again. The other
                # files in this tick still land.
                traceback.print_exc()
                continue
            # Recorded only now, and whether or not it was new: a file the
            # store already had must not be re-hashed either. Recording it
            # before the attempt made a file that failed once unopenable for
            # the life of the process, since only eviction clears the entry.
            self._remember_taken(key, snapshot, result["sha256"])
            if result["job_id"] is not None:
                taken.append(result)
        return taken

    def reconcile(self, conn: sqlite3.Connection, now: float | None = None) -> int:
        """Walk every enabled folder and take in what the library does not have.

        Returns how many recordings were added. This is the startup pass and
        the fallback for missed events, so what decides is the sha256 of the
        file and nothing else - either read now, or remembered from the last
        run beside the size and mtime it was read at and confirmed against the
        library (`_already_taken`). It is what makes a file dropped while the
        app was closed still arrive.

        **It takes each file's own folder's options.** The walk is recursive
        and a watched folder may sit inside another one, so the row being
        iterated is not necessarily the folder that claims the file:
        `folder_for` decides, deepest first, exactly as `pump` does. It used to
        take whichever row came first - normally the parent, because the rows
        are ordered by id - so the same file was transcribed with the nested
        folder's options when it landed while the app was up and with the
        parent's when it was found at startup. A GPU hour on the wrong options,
        reported as a success.

        **It gates on quiescence too, and it has to.** Reconcile runs at
        startup, which is exactly when a cloud-sync client is downloading -
        and a file hashed while it is still being written is stored under the
        sha256 of half of itself. Because ingest hardlinks, the file in the
        store then goes on growing under a row that records the wrong hash and
        the wrong size, for good; the finished copy hashes differently later
        and arrives a second time, with a second GPU job. Nothing is ever
        reported. So a file whose mtime is younger than `quiesce` is handed to
        `notice` instead and settles under `pump`'s rule, one tick later.

        Age rather than two looks, because at startup there is no previous
        look to compare against and waiting for one would mean the first pass
        ingested nothing. It answers a slightly different question - "has
        anything written to this in the last five seconds" rather than "has it
        held still across two ticks" - and it is the right one here: a copy in
        progress moves its mtime, and a file that was finished before the app
        started has an mtime older than the app.

        On a large folder this runs for minutes, from a thread, while the web
        process serves pages on its own connection - which is fine, and is
        fine for one specific reason worth writing down: `db.LOCK` is taken
        per row and never across a hash (`media.ingest_path` hashes first,
        then locks), so the longest this holds the shared lock is one INSERT.
        Cross-process it is two WAL connections and `busy_timeout` (ADR-002).
        """
        added = 0
        seen = 0
        watched = watchable(conn)
        for folder in watched:
            root = Path(folder["path"])
            for path in _walk(root):
                seen += 1
                if seen % SAVE_EVERY == 0:
                    # A clean stop writes what it has on the way out, and a
                    # hard kill does not - and the first pass over a large
                    # archive is precisely where a kill costs the most, because
                    # everything measured so far is thrown away and the next
                    # start reads the whole thing again. A write is 63 ms at
                    # ten thousand entries (measured 2026-09-04), so doing it
                    # every few thousand files is a cost nobody can feel.
                    self._save_taken()
                if self._stop_event.is_set():
                    # Checked per file, not per folder: this is the one thing
                    # in the loop that runs for minutes, so it is the one
                    # place a shutdown has to be able to land. Whatever is
                    # left unwalked is walked at the next start - and what has
                    # been walked so far is written down on the way out.
                    self._save_taken()
                    return added
                snapshot = _snapshot(path)
                if snapshot == _UNSEEN[:2]:
                    # Gone between the walk and the stat. Nothing to open and
                    # nothing to wait for; an event brings it back if it returns.
                    continue
                if self._still_changing(snapshot, now):
                    # Not ours to open yet. The pending table is where the
                    # observer's events go too, so the next tick decides.
                    self.notice(path)
                    continue
                key = _key(path)
                if self._already_taken(conn, key, snapshot):
                    # This is what a nested watched folder costs now: its files
                    # are walked twice, once under the parent and once under
                    # itself, and the second look is a dict lookup rather than
                    # a second hash of the same bytes.
                    continue
                owner = folder_for(path, watched)
                if owner is None:
                    # The same refusal `pump` makes, and for the same reason.
                    # This used to read `folder_for(...) or folder`, which
                    # turned the one answer that means "this file is not inside
                    # anything we may read" into "use the folder the walk
                    # started from". The walk descends directory junctions -
                    # os.path.islink is False for them, and `mklink /J` needs
                    # no administrator - so a junction inside a watched folder
                    # pointing outside the browse roots was a way in, which is
                    # exactly what `fsbrowse.is_allowed` says in its own
                    # docstring it is not. `watched` here is `watchable`, so a
                    # file directly in a walked folder always finds its owner;
                    # None only ever means the path resolved somewhere else.
                    continue
                try:
                    result = take_in(conn, path, owner)
                except Exception:  # noqa: BLE001 - see pump: keep going
                    traceback.print_exc()
                    continue
                # Remembered whether or not it was new, exactly as `pump` does:
                # a file the library already had is one we must also not
                # re-hash the next time an event mentions it.
                self._remember_taken(key, snapshot, result["sha256"])
                if result["job_id"] is not None:
                    added += 1
        self._save_taken()
        return added

    # --- the thread -------------------------------------------------------------------

    def start(self) -> None:
        """Start the observer and the tick on a daemon thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="scribe-watcher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the loop to finish and join it, observer threads included.

        A join that runs out of time is said on stderr and the thread is
        *kept*. Dropping the reference either way made the two outcomes
        indistinguishable, and the difference matters twice: the app's
        lifespan closes the database next, and `start()` would cheerfully
        begin a second loop over the same folders while the first was still
        ingesting - which is two rows and two GPU jobs per file.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout)
        if thread.is_alive():
            print(
                f"scribe: the watch-folder thread is still running {timeout:g}s after"
                " being asked to stop; it is most likely mid-reconcile over a large"
                " folder and will finish on its own.",
                file=sys.stderr,
                flush=True,
            )
            return
        self._thread = None

    def _loop(self) -> None:
        """Its own connection, opened here and closed here.

        A thread never borrows `app.state.conn`: the hashing in `reconcile`
        takes minutes on a large folder, and `db.LOCK` is not held across it
        (`media.ingest_path` hashes before it takes the lock) precisely so the
        jobs board keeps answering while this runs.
        """
        conn = db.connect(self.db_path)
        observer = self._open_observer()
        self._observer_live = observer is not None
        try:
            _survive(self.reconcile, conn)
            while not self._stop_event.is_set():
                if observer is not None:
                    _survive(self._sync_schedules, observer, conn)
                _survive(self.pump, conn)
                self._stop_event.wait(self.poll_interval)
        finally:
            if observer is not None:
                _survive(observer.stop)
                _survive(observer.join, OBSERVER_JOIN_SECONDS)
            # Whatever the ticks took in since the last reconcile, so a clean
            # stop is not a reason to read the folder again tomorrow.
            _survive(self._save_taken)
            conn.close()

    def _open_observer(self):
        """The observer, started - or None when this machine cannot give one.

        Building it is the one thing in this loop that happens before there is
        anything to catch, and letting it kill the thread is the worst
        available outcome: the app keeps serving, `app.state.watcher` still
        holds a Watcher, and nothing is ever ingested - which looks exactly
        like a folder nobody dropped anything into. So it is caught, said out
        loud on stderr, and the loop carries on without events.

        What still works without one is the startup `reconcile`, which is not
        nothing: everything already in a watched folder when the app started
        arrives. What does not is anything dropped afterwards, until the next
        start. Stated here because a degraded mode nobody can see is a lie.
        """
        try:
            observer = self._observer_factory()
            observer.start()
            return observer
        except Exception:  # noqa: BLE001 - a broken install must not be silent
            traceback.print_exc()
            print(
                "scribe: no filesystem watcher could be started, so watched folders"
                " are only read at startup. Reinstall watchdog"
                " (pip install -r requirements.txt) to have files picked up as they land.",
                file=sys.stderr,
                flush=True,
            )
            return None

    def _sync_schedules(self, observer, conn: sqlite3.Connection) -> None:
        """Point the observer at exactly the folders it may watch now.

        Run every tick, so adding a folder in Settings takes effect within one
        poll interval instead of at the next start. A folder that is not there
        (an unplugged drive, a path typed before it existed) is not scheduled
        and is picked up the tick after it appears, and neither is one the
        browse roots no longer cover - `watchable` asks both questions.

        **Every folder now fails on its own.** One try/except used to sit
        around the whole of this, out in `_loop`'s `_survive`, so a single
        folder whose watch could not be opened - a share that dropped away
        between the row being read and the handle being asked for, a directory
        this account may list but not open for change notifications - ended the
        tick. Every folder ordered after it then got no filesystem events for
        the life of the process, while the settings page went on showing it as
        On and stderr collected the same traceback every two seconds.
        """
        wanted = {
            _key(Path(folder["path"])): Path(folder["path"])
            for folder in watchable(conn)
            if Path(folder["path"]).is_dir()
        }
        for key in list(self._watches):
            if key not in wanted:
                # Popped before the call, deliberately: whatever state the
                # observer is in, this is a folder we are done with, and one
                # that could never be unscheduled would otherwise be retried
                # every tick for the life of the process.
                watch = self._watches.pop(key)
                try:
                    observer.unschedule(watch)
                except Exception:  # noqa: BLE001 - a watch we no longer want
                    traceback.print_exc()
        with self._lock:
            for key in list(self._unschedulable):
                if key not in wanted:
                    # Switched off, removed, or no longer inside the roots:
                    # there is nothing left to warn the settings page about.
                    self._unschedulable.pop(key)
        for key, path in wanted.items():
            if key in self._watches:
                continue
            try:
                watch = observer.schedule(self.handler, str(path), recursive=True)
            except Exception:  # noqa: BLE001 - one folder, not all of them
                self._note_unschedulable(key, path)
                continue
            self._watches[key] = watch
            with self._lock:
                # It works again - a drive plugged back in, a share that came
                # back. Every tick retries what it has no watch for, which is
                # what makes that recovery automatic.
                self._unschedulable.pop(key, None)

    def _note_unschedulable(self, key: str, path: Path) -> None:
        """Remember a folder the observer refused, and say so exactly once.

        Once per failure rather than once per tick: this loop retries every
        folder it has no watch for on every tick, which is what makes a drive
        that comes back start delivering events again - and which would
        otherwise put the same traceback on stderr every two seconds until
        somebody stopped reading stderr.
        """
        with self._lock:
            first = key not in self._unschedulable
            self._unschedulable[key] = str(path)
        if first:
            traceback.print_exc()
            print(
                f"scribe: {path} is a watch folder, but no filesystem watch could be"
                " opened on it - files dropped there are only picked up when"
                " MyScribe next starts. It is marked on the Settings page.",
                file=sys.stderr,
                flush=True,
            )

    def cannot_watch(self, path: str | Path) -> bool:
        """Whether the observer refused a live watch on this folder.

        For the settings page. The folder is still walked at every start, so
        "On" is not a lie - but nothing dropped in it arrives until the next
        one, and a degraded mode nobody can see is exactly what
        `_open_observer` refuses to allow for the observer as a whole.
        `scribe.web.settings.watch_context` is where this is read.
        """
        if self._observer_live is False:
            # No observer at all: every folder is in this state, not just the
            # ones `_sync_schedules` recorded - it never ran.
            return True
        with self._lock:
            return _key(Path(path)) in self._unschedulable

    # --- the pending table ---------------------------------------------------------------

    def _due(self, now: float) -> list[tuple[str, _Pending]]:
        """The pending entries whose quiet interval has elapsed, as a snapshot
        of the dict - the observer thread keeps adding to it while we work."""
        with self._lock:
            return [
                (key, pending)
                for key, pending in self._pending.items()
                if now - pending.since >= self.quiesce
            ]

    def _still_changing(self, snapshot: tuple[int, float], now: float | None = None) -> bool:
        """Whether something wrote to the file this snapshot came from inside
        the last `quiesce`.

        Takes the snapshot rather than the path so the caller can stat once and
        ask this and the cache the same question about the same measurement.
        False for a file that has gone: there is nothing to wait for.

        A file stamped in the future - clock skew, or a sync client restoring
        the original mtime - is not called settled here, and does not need to
        be: `notice` puts it in front of `pump`, which compares two looks
        rather than reading the clock, so it lands one tick later anyway.
        """
        if snapshot == _UNSEEN[:2]:
            return False
        return (time.time() if now is None else now) - snapshot[1] < self.quiesce

    def _already_taken(
        self, conn: sqlite3.Connection, key: str, snapshot: tuple[int, float]
    ) -> bool:
        """Whether this exact file is dealt with and need not be opened again.

        Two questions, and the second is what lets the cache survive a restart
        honestly. The snapshot answers "has anything written to this since we
        last looked"; the sha256 stored beside it answers "and does the library
        still hold what we took in". Before the cache was persisted it began
        empty at every launch, so a recording purged from the library was
        hashed again on the next start and taken back in - keeping that true is
        the difference between a cache and a decision, and it costs one indexed
        lookup per file.
        """
        known = self._taken.get(key)
        if known is None or (known.size, known.mtime) != snapshot:
            return False
        return _in_library(conn, known.sha256)

    def _remember(self, key: str, path: Path, snapshot: tuple[int, float], now: float) -> None:
        with self._lock:
            self._pending[key] = _Pending(path, snapshot[0], snapshot[1], now)

    def _remember_taken(self, key: str, snapshot: tuple[int, float], sha256: str) -> None:
        """Record a file as handled, oldest evicted past `TAKEN_MEMORY`.

        Losing an entry is not a correctness problem - the file gets hashed
        once more and `media.ingest_path` dedupes it - which is exactly why a
        hard bound is affordable here and an unbounded dict is not.
        """
        with self._lock:
            # Deliberately no eviction here. Evicting on insert turned the bound
            # into a cliff rather than a slope: `_walk` yields sorted paths, so
            # a pass over a folder larger than the bound ended holding the LAST
            # n entries in sort order, and the next pass starts at the FIRST,
            # misses every one, and evicts exactly what it is about to need.
            # Measured with the bound at 10: ten files re-hashed nothing, eleven
            # re-hashed everything. No eviction policy fixes that - a sequential
            # scan of more than n files thrashes under FIFO and under LRU alike.
            # Trimming once, on the way out, is what makes the loss proportional
            # instead: a folder of n+5 costs five hashes next time, not n+5.
            self._taken[key] = _Taken(snapshot[0], snapshot[1], sha256)
            self._taken_dirty = True

    def _trim_taken(self) -> None:
        """Bring the cache back inside the bound, keeping what was seen last.

        Called on the way out rather than on the way in; see `_remember_taken`
        for why that difference is the whole finding. Oldest-first, which dicts
        give for nothing: within one pass the entries most worth keeping are the
        ones just written, because a modify storm follows an ingest.
        """
        with self._lock:
            excess = len(self._taken) - TAKEN_MEMORY
            if excess > 0:
                # One slice, not `excess` pops. `pop(next(iter(d)))` rescans the
                # dict's entry array past every tombstone the previous pop left,
                # so cutting 200,000 entries to 100,000 took 11.0 s by popping
                # and 0.047 s this way (measured 2026-09-04). That was harmless
                # while eviction ran on insert and the dict never grew past the
                # bound; moving the trim here - which is what stopped the bound
                # being a cliff - is exactly what made it reachable.
                self._taken = dict(islice(self._taken.items(), excess, None))

    def _save_taken(self) -> None:
        """Write the cache out, if anything has changed since the last time.

        At the end of a reconcile and on the way out of the loop, rather than
        every tick: the file is up to a megabyte and a tick is two seconds. What
        that leaves on the floor is the handful of files a *pump* took in since
        the last reconcile when the process is killed rather than stopped - and
        each of those costs exactly one re-hash on the next start, which is the
        thing this file is a cache against in the first place.
        """
        self._trim_taken()
        with self._lock:
            if not self._taken_dirty:
                return
            # Cleared before the write, not after: `_remember_taken` can run
            # between the two and must leave the flag set rather than have its
            # entry silently dropped from the next save.
            self._taken_dirty = False
            snapshot = dict(self._taken)
        save_cache(snapshot)

    def _forget(self, key: str) -> None:
        with self._lock:
            self._pending.pop(key, None)


class _Handler:
    """The observer's end of `notice`.

    Not a `FileSystemEventHandler` subclass by inheritance - watchdog calls
    `dispatch`, and these three methods are the ones it routes to; keeping the
    class free of the base means this module imports watchdog only when an
    observer is actually built.
    """

    def __init__(self, watcher: Watcher) -> None:
        self._watcher = watcher

    def dispatch(self, event) -> None:
        """What watchdog actually calls; it routes to the three below."""
        handler = getattr(self, f"on_{event.event_type}", None)
        if handler is not None:
            handler(event)

    def on_created(self, event) -> None:
        self._file(event, getattr(event, "src_path", ""))

    def on_modified(self, event) -> None:
        self._file(event, getattr(event, "src_path", ""))

    def on_moved(self, event) -> None:
        # The destination: a file moved *into* a watched folder is a new file,
        # and a download renamed from `x.mp4.part` to `x.mp4` arrives this way.
        self._file(event, getattr(event, "dest_path", ""))

    def _file(self, event, path) -> None:
        if getattr(event, "is_directory", False) or not path:
            return
        self._watcher.notice(_text(path))


# --- the plumbing ---------------------------------------------------------------------------


def _survive(step: Callable, *args) -> None:
    """Run one step of the loop and live through whatever it does.

    `Supervisor._loop` does the same, for the same reason: a background thread
    that dies on a transient error - a drive that went away for a second, a
    database busy timeout - stops doing its job with nobody to tell.
    """
    try:
        step(*args)
    except Exception:  # noqa: BLE001 - the whole point is to catch everything
        traceback.print_exc()


def _default_observer():
    """watchdog's observer, imported at the moment one is needed."""
    from watchdog.observers import Observer

    return Observer()


def _walk(root: Path) -> list[Path]:
    """Every candidate file under ``root``, hidden directories pruned.

    Pruned rather than filtered per file: a `.git` or a `node_modules` full of
    nothing this app wants should not be descended into at all, and the same
    goes for the app's own data directory if a watch folder contains it.
    """
    found: list[Path] = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if not fsbrowse.is_hidden(here / name) and not in_data_dir(here / name)
        ]
        found.extend(
            here / name for name in filenames if is_candidate(here / name)
        )
    return sorted(found)


def _key(path: Path) -> str:
    """One name for one file: case-folded and separator-normalised.

    `normcase`, not `resolve`: this is a dictionary key touched on every
    event, and resolving would be a syscall per event for a difference that
    only shows up with symlinks into the same folder.
    """
    return os.path.normcase(str(path))


def _snapshot(path: Path) -> tuple[int, float]:
    try:
        status = path.stat()
    except OSError:
        return _UNSEEN[:2]
    return status.st_size, status.st_mtime


def _text(path) -> str:
    """watchdog reports bytes when it was given a bytes path; we give str."""
    return path.decode("utf-8", "replace") if isinstance(path, bytes) else str(path)
