"""The content-addressed media store: hash once, keep the bytes once.

A file's sha256 is its identity here. It is the dedupe key, the store path,
and later the share key - one algorithm everywhere (spec section 2). Two
uploads of the same recording under two different names are one `media` row
and one copy on disk, which is what keeps a library of re-sent meeting
recordings from quietly doubling in size.

Three rules this module exists to enforce:

* **The source file is never touched.** Ingest hardlinks the bytes into the
  store (`os.link`, one inode, no second copy) and falls back to a copy only
  when the source is on another volume. A user who points MyScribe at their
  own archive gets their archive back, unmoved and unrenamed.
* **Nothing is ever read whole.** Hashing streams in 1 MiB chunks; a four-hour
  video costs 1 MiB of RAM, not four hours of it.
* **The store never holds a half-written file.** Copies land on a temporary
  name and are moved into place with `os.replace`, so a crash mid-copy leaves
  a stray `.part` file, never a truncated file wearing a valid hash's name.

`store_path` is stored relative to `paths.DATA_DIR` (`media/<h[:2]>/<h><ext>`)
so the whole data directory stays movable - absolute paths in rows would break
the moment someone repoints `SCRIBE_DATA_DIR`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from scribe import db, paths

# Read size for hashing and streamed ingest. 1 MiB is comfortably above the
# point where syscall overhead stops mattering and far below the point where
# it costs anything.
CHUNK_SIZE = 1 << 20

# Where a streamed upload is buffered while it is being hashed. Under
# MEDIA_DIR so the finished file moves into the store with a rename on the
# same volume rather than a second full copy.
INCOMING_DIRNAME = ".incoming"

# A suffix we are willing to put in a store path: a dot and a short run of
# alphanumerics. Anything else (a filename with dots in the middle of it, a
# hostile name) contributes no extension at all rather than a surprise.
_PLAIN_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,12}\Z")


def hash_stream(stream, sink: BinaryIO | None = None) -> str:
    """sha256 of everything ``stream`` yields, optionally copied to ``sink``.

    Hashing and writing share one pass because an upload has to be read
    exactly once: there is no file to go back to.
    """
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        if sink is not None:
            sink.write(chunk)
    return digest.hexdigest()


def hash_file(path: str | Path) -> str:
    """sha256 hex of a file on disk, read in CHUNK_SIZE pieces."""
    with open(path, "rb") as fh:
        return hash_stream(fh)


def store_path_for(sha256: str, suffix: str = "") -> Path:
    """Absolute path of the stored bytes: ``MEDIA_DIR/<h[:2]>/<h><suffix>``.

    The two-character shard keeps any one directory to a few thousand entries
    even with a very large library, which Windows Explorer and ``os.scandir``
    both appreciate.
    """
    return paths.MEDIA_DIR / sha256[:2] / f"{sha256}{suffix}"


# The browser-playable proxy the transcript view transcodes for a container
# a browser cannot play: AAC in an MP4 box under MEDIA_DIR/proxy, named by the
# same content hash as the store. Keyed that way so a purge of the row knows
# what else belongs to it.
PROXY_DIRNAME = "proxy"
PROXY_SUFFIX = ".m4a"


def proxy_path_for(sha256: str) -> Path:
    """Where the proxy of the stored bytes lives, or would if one were made."""
    return paths.MEDIA_DIR / PROXY_DIRNAME / f"{sha256}{PROXY_SUFFIX}"


def ingest_path(
    conn: sqlite3.Connection,
    src: str | Path,
    *,
    title: str | None = None,
    folder_id: int | None = None,
    link: bool = True,
    source_url: str | None = None,
    source_id: str | None = None,
) -> dict:
    """Take a file already on disk into the store; returns the media row.

    Known content short-circuits: the existing row comes back with
    ``deduped=True``, and neither the store nor the source file is touched.
    The one thing a duplicate may add is provenance it lacked - see
    `_fill_source`.

    ``source_url`` and ``source_id`` say where the bytes came from when they
    came over the network (the feed import writes them; an upload or a path
    passes nothing). Compared by the dialog's listing, rendered nowhere.
    """
    src = Path(src)
    sha256 = hash_file(src)
    existing = _existing_row(conn, sha256)
    if existing is not None:
        return _fill_source(conn, existing, source_url, source_id)

    dst = store_path_for(sha256, _suffix_of(src.name))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():  # a leftover from an interrupted ingest is the same bytes
        _place(src, dst, link=link)

    return _insert_media(
        conn,
        sha256=sha256,
        store_path=dst,
        orig_name=src.name,
        title=title,
        folder_id=folder_id,
        size_bytes=src.stat().st_size,
        source_url=source_url,
        source_id=source_id,
    )


def _fill_source(
    conn: sqlite3.Connection, row: dict, source_url: str | None, source_id: str | None
) -> dict:
    """A known recording gains the provenance it lacked; a set one stands.

    Title, folder and the privacy pin stay as the first arrival chose them,
    and so does a source that is already there. But a row that arrived
    through an upload, a watch folder or a version before v10 has NULL here,
    and NULL is no provenance: without this it would never be marked "in
    library", however often a feed re-imported it. The `IS NULL` guard is in
    the SQL because two fan-out children can dedupe the same bytes at once;
    the second one's update matches no row, which is the right answer.
    """
    wanted = {
        column: value
        for column, value in (("source_url", source_url), ("source_id", source_id))
        if value and not row.get(column)
    }
    if not wanted:
        return row
    with db.LOCK:
        for column, value in wanted.items():
            conn.execute(
                f"UPDATE media SET {column}=? WHERE id=? AND {column} IS NULL",
                (value, row["id"]),
            )
        conn.commit()
        fresh = conn.execute(
            "SELECT source_url, source_id FROM media WHERE id=?", (row["id"],)
        ).fetchone()
    return {**row, "source_url": fresh["source_url"], "source_id": fresh["source_id"]}


def ingest_stream(
    conn: sqlite3.Connection,
    stream,
    filename: str,
    *,
    title: str | None = None,
    folder_id: int | None = None,
) -> dict:
    """Take bytes from an upload or a recording into the store.

    The hash is only known once the last byte has arrived, so the data is
    buffered in ``MEDIA_DIR/.incoming`` and hashed on the way through, then
    renamed into its content-addressed home. The temporary file is removed on
    every path out of here, including a connection that dies mid-upload.
    """
    incoming = paths.MEDIA_DIR / INCOMING_DIRNAME
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "wb") as out:
            sha256 = hash_stream(stream, sink=out)
        size_bytes = tmp.stat().st_size

        existing = _existing_row(conn, sha256)
        if existing is not None:
            return existing

        name = _basename(filename)
        dst = store_path_for(sha256, _suffix_of(name))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            os.replace(tmp, dst)

        return _insert_media(
            conn,
            sha256=sha256,
            store_path=dst,
            orig_name=name,
            title=title,
            folder_id=folder_id,
            size_bytes=size_bytes,
        )
    finally:
        tmp.unlink(missing_ok=True)  # a no-op once it has been renamed away


def _place(src: Path, dst: Path, *, link: bool) -> None:
    """Put the source bytes at ``dst``, preferring a hardlink to a copy."""
    if link:
        try:
            os.link(src, dst)
            return
        except OSError:
            # Another volume, a filesystem without hard links, or a source the
            # OS will not link (a network share). Copying is the honest answer.
            pass
    tmp = dst.with_name(f"{dst.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


PRIVATE_DEFAULT_SETTING = "private_default"
"""Whether a newly ingested recording starts pinned private.

Written by the AI settings page, read here. It lives in this module rather
than in `scribe/llm/privacy.py`, where it would read more naturally, for one
blunt reason: importing `scribe.llm.privacy` initialises the `scribe.llm`
package, which imports the OpenAI SDK. Ingest runs on the CLI path and has no
business loading a vendor SDK to find out whether a checkbox is ticked.
`privacy.py` still owns what private *means*; this is only where the initial
value of one column comes from.
"""


def private_default(conn: sqlite3.Connection) -> bool:
    """Whether the next recording taken into the store starts pinned.

    The same words every other checkbox in this app is stored as
    (`web/library._truthy`), spelled again rather than imported: the web layer
    depends on this module and must not be depended on back.
    """
    with db.LOCK:
        row = conn.execute(
            "SELECT value FROM setting WHERE key=?", (PRIVATE_DEFAULT_SETTING,)
        ).fetchone()
    if row is None:
        return False
    return str(row["value"] or "").strip().lower() in ("1", "true", "on", "yes")


def _existing_row(conn: sqlite3.Connection, sha256: str) -> dict | None:
    with db.LOCK:
        row = conn.execute("SELECT * FROM media WHERE sha256=?", (sha256,)).fetchone()
    return None if row is None else {**dict(row), "deduped": True}


def _insert_media(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    store_path: Path,
    orig_name: str,
    title: str | None,
    folder_id: int | None,
    size_bytes: int,
    source_url: str | None = None,
    source_id: str | None = None,
) -> dict:
    with db.LOCK:
        try:
            cur = conn.execute(
                "INSERT INTO media(sha256, store_path, orig_name, title, folder_id,"
                " size_bytes, private, created_at, source_url, source_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sha256,
                    _relative_store_path(store_path),
                    orig_name,
                    title or Path(orig_name).stem or orig_name,
                    folder_id,
                    size_bytes,
                    # The pin is chosen once, here, at the only moment a media
                    # row is created. The dedupe short-circuit above returns an
                    # existing row untouched, so turning the default on is a
                    # promise about what arrives next rather than a switch over
                    # a library that is already there.
                    1 if private_default(conn) else 0,
                    time.time(),
                    source_url or None,
                    source_id or None,
                ),
            )
        except sqlite3.IntegrityError:
            # Someone else ingested the same content between our lookup and
            # this insert; their row is as good as ours would have been.
            conn.rollback()
            existing = _existing_row(conn, sha256)
            if existing is None:  # not the UNIQUE(sha256) clash we assumed
                raise
            return existing
        conn.commit()
        row = conn.execute("SELECT * FROM media WHERE id=?", (cur.lastrowid,)).fetchone()
    return {**dict(row), "deduped": False}


def _relative_store_path(path: Path) -> str:
    """The store path as it is written to the row: relative to DATA_DIR."""
    return path.relative_to(paths.DATA_DIR).as_posix()


def _basename(filename: str) -> str:
    """Last path component of a client-supplied name, separators either way."""
    return PurePosixPath(str(filename).replace("\\", "/")).name or "upload"


def _suffix_of(name: str) -> str:
    suffix = PurePosixPath(_basename(name)).suffix.lower()
    return suffix if _PLAIN_SUFFIX.fullmatch(suffix) else ""
