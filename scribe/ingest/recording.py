"""Microphone recording: chunks to disk as they arrive, one file at the end.

A recording is the one kind of media that does not exist yet when it starts.
Everything else this app ingests is a file somebody already has; this one is
being made while the browser holds it, and the browser is the least durable
thing in the system - a closed tab, a reloaded page, a laptop lid. So nothing
is held in memory and nothing waits for the end: `MediaRecorder` hands over a
chunk every five seconds and each one is written to
``WORK_DIR/rec/<session>/<idx:06d>.webm`` the moment it lands. A two-hour
session that dies at minute 119 has 119 minutes on disk, and the sweep is what
eventually throws those away - not the loss of them.

**The container fix, which is the whole reason `finalize` is not a `cat`.**
MediaRecorder writes WebM to a stream it cannot seek back into, so the Segment
header never gets its duration; `ffprobe` says ``duration=N/A`` and every
progress bar downstream would then have nothing to divide by (see
`prepare.progress_fractions`, and `probe.summarize`'s hunt for a stand-in). A
stream copy into Matroska *to a real file* fixes it: ffmpeg seeks back to the
header at close and stamps the duration it just measured. No decode, no
re-encode, no generation lost - the Opus frames are copied through byte for
byte. Measured on this machine with ffmpeg 8.1: the captured fixture reports
no duration, the remuxed file reports 1.008 s.

**Why this runs where it runs.** ADR-001 keeps models, GPU work and downloads
out of the web process, and this is a subprocess in the web process, so it is
worth being explicit: `-c copy` decodes nothing, loads nothing and reaches no
network - it rewrites a container around bytes the user just recorded, and its
cost is bounded by what they recorded. The plan grants it by name ("`finalize`
... remuxes with `ffmpeg -f webm -i - -c copy -f matroska`", called from
``POST /record/{session}/finish``), which is the same standing the URL
preview's carve-out has. It is not a job because pressing stop should put the
recording in the library, not on the jobs board: the transcription that
follows is the job, and it is enqueued the moment this returns.

The session token is a path segment chosen by whoever is talking to the app,
so it is checked against one alphabet here rather than only in the route -
`urls.ensure_http_url` lives in its own module for exactly this reason. A
token that is not the shape `secrets.token_urlsafe` produces never becomes a
path at all.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from scribe import applog, db, media, paths

# Under WORK_DIR rather than beside the jobs' scratch directories, which are
# named by job id: a recording has no job until it is finished.
REC_DIRNAME = "rec"

# What the browser sends and what a chunk is stored as. MediaRecorder's
# `audio/webm;codecs=opus`, kept as it arrived.
CHUNK_SUFFIX = ".webm"

# Zero-padded so a plain name sort is arrival order - which is what the
# concatenation in `remux` depends on. Six digits is 10 hours of five-second
# chunks; the recorder stops long before that matters.
CHUNK_DIGITS = 6

# The remuxed file, inside the session directory. Fixed rather than named
# after the title: a title is text the user typed and this is a path.
RECORDING_NAME = "recording.mkv"

# Bytes of entropy in a session token. `secrets.token_urlsafe(16)` gives 22
# characters of [A-Za-z0-9_-] - not a secret anyone else has to guess, but a
# name that cannot collide and cannot be a path.
SESSION_BYTES = 16

# How long an unfinished session may sit before it is assumed abandoned. A
# real recording appends to its directory every five seconds, so a day of
# silence is not a long recording; it is a tab that was closed.
SWEEP_AFTER_SECONDS = 86400

# What an untitled recording is called. The date follows, because a library of
# rows all called "Recording" is a library you cannot read.
DEFAULT_TITLE_PREFIX = "Recording "
DEFAULT_TITLE_FORMAT = f"{DEFAULT_TITLE_PREFIX}%Y-%m-%d %H:%M"

# How much of ffmpeg's complaint travels with the exception - the same budget
# `prepare` and `probe` allow themselves.
STDERR_TAIL = 800

# What the pipe into ffmpeg is fed in. The chunks are small; this only bounds
# how much of a long recording is in memory at once.
COPY_SIZE = 1 << 20

# Exactly the alphabet `secrets.token_urlsafe` emits, and nothing else. No
# dot, no separator either way round, so "..", "a/b" and "a\\b" are refused
# before `Path` ever sees them.
_SESSION = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")

# Where each live session's next chunk is expected to land.
#
# A hint and nothing more. The index used to be worked out by listing the whole
# session directory on every chunk, which is O(n) per five-second POST and
# O(n^2) over a session: measured 2026-09-04 on this machine with a warm OS
# cache, enumerating a session directory costs 0.7 ms at 60 chunks, 4.5 ms at
# one hour, 22 ms at four and 62 ms at eight. That is the one cost in this path
# that grows the longer somebody talks, which is precisely backwards for a
# recorder - the sessions that can least afford it are the long ones.
#
# Deliberately unguarded by any lock. Two threads can read the same number and
# both aim at it; the exclusive create in `append` is what makes that safe, and
# a lost update here costs one extra `FileExistsError` and never a chunk. So:
# this dict is the hint, ``O_CREAT | O_EXCL`` is the guarantee. Do not "fix"
# this by adding a lock - it would be guarding nothing that matters, and
# ADR-002 keeps `db.LOCK` the only lock in this process with a real job.
#
# Per-process, so it is empty after a restart. `_next_index` falls back to the
# disk for exactly that reason: the app can be replaced while a browser is
# still recording, and the chunks already written are then the only record of
# how far the session got.
_NEXT_INDEX: dict[str, int] = {}


class UnknownSession(RuntimeError):
    """No such recording session - never started, already finished, or not a
    token at all. One answer for all three: which it is, is not the caller's
    business, and the route maps this to a 404."""


class EmptyRecording(RuntimeError):
    """Stop was pressed before a single chunk arrived. There is nothing here."""


class RemuxFailed(RuntimeError):
    """ffmpeg could not make one playable file out of the chunks."""


# --- where the bytes live ---------------------------------------------------------


def rec_root() -> Path:
    """The directory every session's directory lives under."""
    return paths.WORK_DIR / REC_DIRNAME


def session_dir(session: str) -> Path:
    """Where ``session``'s chunks are written; raises on anything but a token.

    Does not require the directory to exist - `start` calls this to make it.
    """
    text = str(session or "")
    if not _SESSION.fullmatch(text):
        raise UnknownSession(f"{text!r} is not a recording session")
    return rec_root() / text


def chunk_paths(session: str) -> list[Path]:
    """This session's chunks, in the order they arrived."""
    return sorted(session_dir(session).glob(f"*{CHUNK_SUFFIX}"))


def _live_dir(session: str) -> Path:
    """The session's directory, or `UnknownSession` if it is not there."""
    path = session_dir(session)
    if not path.is_dir():
        raise UnknownSession(f"recording session {session} is not in progress")
    return path


# --- the session --------------------------------------------------------------------


def start(conn: sqlite3.Connection) -> str:
    """Open a session: a token, a directory, and a row saying when."""
    session = secrets.token_urlsafe(SESSION_BYTES)
    session_dir(session).mkdir(parents=True, exist_ok=True)
    with db.LOCK:
        conn.execute(
            "INSERT INTO recording(session, started_at) VALUES (?, ?)",
            (session, time.time()),
        )
        conn.commit()
    return session


def append(session: str, data: bytes) -> int:
    """Write one chunk; returns the index it landed at.

    No database write. The row's counters are taken from what is on disk when
    the session ends, so a chunk POST that dies halfway through costs a partial
    file and not a row that lies about it.

    The index is claimed with ``O_CREAT | O_EXCL`` rather than counted, so two
    chunk requests that overlap - a retry, a browser that pipelines - cannot
    both decide they are number seven and have one silently overwrite the
    other. `_next_index` only says where to start looking; the file system
    says who gets the name, and the loop walks forward until it wins one.

    The session is validated before anything else is done with it, hint
    included: a token that is not a token must not get as far as a dictionary
    lookup, let alone a path.
    """
    directory = _live_dir(session)
    index = _next_index(session, directory)
    while True:
        path = directory / f"{index:0{CHUNK_DIGITS}d}{CHUNK_SUFFIX}"
        try:
            with open(path, "xb") as out:
                out.write(data)
        except FileExistsError:
            index += 1
            continue
        break

    # Never backwards: another thread may have claimed a higher name while this
    # one was writing, and sending the next chunk back over ground that is
    # already taken would just cost it retries.
    _NEXT_INDEX[session] = max(_NEXT_INDEX.get(session, 0), index + 1)
    return index


def _next_index(session: str, directory: Path) -> int:
    """Where ``session``'s next chunk is expected to land - a hint, not a claim.

    Cheap every time but the first: what is remembered from the last `append`
    if this process did one, and otherwise a single look at the disk. See
    `_NEXT_INDEX` for why being wrong here is affordable and being slow is not.
    """
    remembered = _NEXT_INDEX.get(session)
    return _scan_next_index(directory) if remembered is None else remembered


def _scan_next_index(directory: Path) -> int:
    """One past the highest chunk name in ``directory``; the only listing here.

    Reads the highest *name* rather than counting files. Counting was the old
    way and it is wrong the moment there is a gap - a chunk removed by hand, a
    cleanup that half finished - because it aims the next write at a name that
    is already taken and leaves the exclusive create to walk out of it one
    collision at a time. The highest name has no such failure mode.

    Names that are not a number are skipped rather than fatal: `RECORDING_NAME`
    and its ffmpeg log share this directory during `finalize`, and anything
    else in here is somebody else's business, not a reason to refuse a chunk.
    """
    highest = -1
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.name.endswith(CHUNK_SUFFIX):
                continue
            try:
                highest = max(highest, int(entry.name[: -len(CHUNK_SUFFIX)]))
            except ValueError:
                continue
    return highest + 1


def _forget(session: str) -> None:
    """Drop the session's index hint, because its directory is gone.

    Not needed for correctness - `_live_dir` refuses a session whose directory
    has been removed long before the hint would be read - but a process that
    records all day should not keep an integer per session it finished this
    morning.
    """
    _NEXT_INDEX.pop(session, None)


def finalize(
    conn: sqlite3.Connection,
    session: str,
    *,
    title: str = "",
    folder_id: int | None = None,
) -> dict:
    """Turn the chunks into one file in the library; returns the media row.

    The order matters on the way out: the bytes are in the store *before* the
    session directory is removed, and the directory is only removed once
    `media.ingest_path` has hardlinked them. Both live under `DATA_DIR`, so
    that link always succeeds and this is a rename rather than a second copy of
    a two-hour recording.

    A failure - no chunks, or bytes ffmpeg cannot read - leaves the directory
    exactly as it was. Whatever went wrong at the end of a recording is not a
    reason to throw away the recording.
    """
    directory = _live_dir(session)
    chunks = chunk_paths(session)
    if not chunks:
        raise EmptyRecording("nothing was recorded in this session")

    remuxed = remux(chunks, directory / RECORDING_NAME)
    applog.log("record.remuxed", session=session, chunks=len(chunks),
               chunk_bytes=sum(chunk.stat().st_size for chunk in chunks),
               out_bytes=remuxed.stat().st_size)
    row = media.ingest_path(
        conn,
        remuxed,
        title=(title or "").strip() or time.strftime(DEFAULT_TITLE_FORMAT),
        folder_id=folder_id,
    )
    _close(
        conn,
        session,
        media_id=row["id"],
        chunk_count=len(chunks),
        size=sum(chunk.stat().st_size for chunk in chunks),
    )
    shutil.rmtree(directory, ignore_errors=True)
    _forget(session)
    return row


def cancel(conn: sqlite3.Connection, session: str) -> None:
    """Throw the session away: the chunks, and any hope of a recording."""
    directory = _live_dir(session)
    chunks = chunk_paths(session)
    _close(
        conn,
        session,
        media_id=None,
        chunk_count=len(chunks),
        size=sum(chunk.stat().st_size for chunk in chunks),
    )
    shutil.rmtree(directory, ignore_errors=True)
    _forget(session)


def sweep(conn: sqlite3.Connection, older_than: float = SWEEP_AFTER_SECONDS) -> int:
    """Remove sessions nothing has written to in ``older_than`` seconds.

    Returns how many went. The age is the newest thing in the directory, never
    the directory's own timestamp alone: a session that was started yesterday
    and is *still* recording gains a chunk every five seconds, and sweeping it
    would delete a recording in progress.

    Never raises on one bad directory: this is meant to run at startup, where
    a file somebody still has open is litter for the next sweep and not a
    reason to refuse to start.

    Called from `scribe.app.create_app`'s lifespan, beside
    `supervisor.reconcile`: both answer the same question, which is what a
    previous life of this process left behind.
    """
    root = rec_root()
    if not root.is_dir():
        return 0

    cutoff = time.time() - float(older_than)
    removed = 0
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or _newest_mtime(directory) > cutoff:
            continue
        chunks = sorted(directory.glob(f"*{CHUNK_SUFFIX}"))
        _close(
            conn,
            directory.name,
            media_id=None,
            chunk_count=len(chunks),
            size=sum(_size(chunk) for chunk in chunks),
        )
        shutil.rmtree(directory, ignore_errors=True)
        _forget(directory.name)
        removed += 1
    return removed


# --- the container fix ---------------------------------------------------------------


def remux(chunks: list[Path], dst: str | Path) -> Path:
    """Concatenate the chunks into ``dst`` as Matroska; returns ``dst``.

    ``-c copy`` is the point: the Opus frames go through untouched and only the
    container is rewritten. ``dst`` must be a real path and not a pipe -
    measured on ffmpeg 8.1, a Matroska muxer writing to a pipe produces
    ``duration=N/A`` because it cannot seek back to the header, which is the
    very problem this function exists to solve.

    The chunks are streamed into stdin rather than read into one buffer: a
    two-hour recording is a few hundred megabytes, and there is no reason for
    any of it to be resident. stderr goes to a file rather than a second pipe,
    for the deadlock reason `prepare.to_wav` explains at length.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    log = dst.with_name(dst.name + ".ffmpeg.log")

    try:
        with open(log, "wb") as errors:
            proc = subprocess.Popen(
                [
                    "ffmpeg", "-v", "error", "-y",
                    # Said outright because stdin cannot be probed the way a
                    # file can, and the demuxer's name covers both containers.
                    "-f", "webm", "-i", "pipe:0",
                    "-c", "copy",
                    "-f", "matroska",
                    str(dst),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                _feed(proc, chunks)
                returncode = proc.wait()
            except BaseException:
                proc.kill()
                proc.wait()
                raise

        if returncode != 0:
            # Half a file is worse than none: the next thing to look at this
            # directory would ingest it.
            dst.unlink(missing_ok=True)
            raise RemuxFailed(f"ffmpeg could not assemble the recording: {_tail(log)}")
    finally:
        log.unlink(missing_ok=True)
    return dst


def _feed(proc: subprocess.Popen, chunks: list[Path]) -> None:
    """Pour the chunks into ffmpeg's stdin, in order, and close it.

    A `BrokenPipeError` is ffmpeg having given up on the input already - bytes
    it cannot read, most likely. Swallowed here so the exit code and the words
    in the log are what the caller reports, rather than a traceback about a
    pipe that says nothing about why.
    """
    assert proc.stdin is not None
    try:
        with proc.stdin as sink:
            for chunk in chunks:
                with open(chunk, "rb") as source:
                    shutil.copyfileobj(source, sink, COPY_SIZE)
    except (BrokenPipeError, OSError):
        pass


# --- the plumbing ---------------------------------------------------------------------


def _close(
    conn: sqlite3.Connection,
    session: str,
    *,
    media_id: int | None,
    chunk_count: int,
    size: int,
) -> None:
    """Stamp a session as over, with what it actually produced.

    `finished_at` is set whichever way it ended, and `media_id` is what says
    which: a row with a time and no media is a session that was cancelled,
    swept, or given up on. A directory with no row - a leftover from a database
    that was replaced under it - updates nothing and is still removed.
    """
    with db.LOCK:
        conn.execute(
            "UPDATE recording SET finished_at=?, media_id=?, chunk_count=?, bytes=?"
            " WHERE session=?",
            (time.time(), media_id, chunk_count, size, session),
        )
        conn.commit()


def _newest_mtime(directory: Path) -> float:
    """The most recent mtime in ``directory``, including its own.

    The directory's own is what an empty session has; a file's is what a
    session that is still being written to has, since each chunk is a new file.
    """
    newest = _mtime(directory)
    for child in directory.iterdir():
        newest = max(newest, _mtime(child))
    return newest


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _tail(log: Path) -> str:
    try:
        return log.read_bytes().decode("utf-8", "replace").strip()[-STDERR_TAIL:]
    except OSError:
        return "ffmpeg failed and left no readable error output"
