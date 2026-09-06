"""Server-side directory listing for "pick a file on this machine".

The transcribe dialog's other way in. An upload pushes bytes through the
browser; for a recording that is already on this disk that is a pointless
copy, so the dialog offers a browse panel that walks the filesystem in the
web process and hands the chosen path to `media.ingest_path`, which hardlinks
it into the store and leaves the original where it is.

The panel is served by a process bound to localhost for one user, so the
roots are a guard against accidents - a mistyped path, a stray form post, an
extension poking at localhost - rather than against an adversary. Three rules
keep it honest all the same:

* **Only paths under the allowed roots are listed or ingested.** The default
  root is the user's home drive on Windows and the home directory elsewhere;
  the `fsbrowse_roots` setting, one path per line, replaces it. A path is
  resolved - symlinks and junctions included - before it is compared, so a
  link pointing out of a root does not lead out of it.
* **`..` is refused, not normalised.** The panel only navigates along paths
  the server itself produced (a directory's entries, its parent), so a `..`
  in a request is something other than the panel. Refusing it outright is
  simpler to reason about than resolving and re-checking.
* **Nothing is opened.** `is_media` is the extension against
  `probe.MEDIA_EXTENSIONS`, a hint for the eye. ffprobe still decides inside
  the job, where a wrong guess is a readable failure rather than a silent skip.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Sequence

from scribe import db
from scribe.stages.probe import MEDIA_EXTENSIONS

# The setting that replaces the default roots: one path per line, blank lines
# ignored. A newline is the one separator no path on any platform contains.
SETTING_KEY = "fsbrowse_roots"


def default_roots() -> tuple[Path, ...]:
    """Where browsing may go when nothing is configured.

    On Windows that is the whole drive the profile lives on - recordings tend
    to sit next to the user, not under them - and elsewhere the home directory.
    """
    home = Path.home()
    if os.name == "nt" and home.anchor:
        return (Path(home.anchor),)
    return (home,)


ALLOWED_ROOTS: tuple[Path, ...] = default_roots()


def roots_from_setting(text: str | None) -> tuple[Path, ...]:
    """The roots a `fsbrowse_roots` value names; the defaults when it is blank."""
    lines = [line.strip() for line in (text or "").splitlines()]
    roots = tuple(Path(line) for line in lines if line)
    return roots or ALLOWED_ROOTS


def allowed_roots(conn: sqlite3.Connection) -> tuple[Path, ...]:
    """The configured roots, read fresh so a settings change applies at once."""
    with db.LOCK:
        row = conn.execute(
            "SELECT value FROM setting WHERE key=?", (SETTING_KEY,)
        ).fetchone()
    return roots_from_setting(None if row is None else row["value"])


def has_traversal(path: str | Path) -> bool:
    """Whether ``path`` contains a ``..`` component anywhere."""
    return ".." in Path(path).parts


def is_allowed(path: str | Path, roots: Sequence[Path] | None = None) -> bool:
    """Whether ``path`` lies under one of ``roots`` (default: ALLOWED_ROOTS).

    Both sides are resolved and case-normalised before the comparison, which
    is what makes ``C:\\users\\...`` and ``c:\\Users\\...`` the same place on
    Windows and a junction into another drive not a way in. A path with a
    ``..`` in it is refused before any of that.
    """
    if has_traversal(path):
        return False
    target = _canonical(path)
    for root in ALLOWED_ROOTS if roots is None else roots:
        base = _canonical(root)
        try:
            if os.path.commonpath([base, target]) == base:
                return True
        except ValueError:
            # Different drives on Windows: no common path, so not under it.
            continue
    return False


def listdir(path: str | Path) -> dict:
    """One directory's entries: ``{path, parent, dirs, files}``.

    ``dirs`` are names; ``files`` are ``(name, size, is_media)``; both sorted
    case-insensitively. Hidden entries (dotfiles, and on Windows the hidden
    attribute) are left out, as is anything that cannot be stat'ed. ``parent``
    is None at a filesystem root. Raises ``ValueError`` for a ``..``,
    ``FileNotFoundError`` and ``NotADirectoryError`` for what they say.
    """
    if has_traversal(path):
        raise ValueError(f"refusing to list {path}: '..' is not allowed in a browse path")
    directory = Path(path)
    if not directory.exists():
        raise FileNotFoundError(f"no such directory: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"not a directory: {directory}")

    dirs: list[str] = []
    files: list[tuple[str, int, bool]] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            try:
                if _hidden(entry):
                    continue
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.is_file():
                    size = entry.stat().st_size
                    is_media = Path(entry.name).suffix.lower() in MEDIA_EXTENSIONS
                    files.append((entry.name, size, is_media))
            except OSError:
                # A broken link, a file being deleted, a permission wall:
                # not this listing's problem, and not a reason to show nothing.
                continue
    dirs.sort(key=str.casefold)
    files.sort(key=lambda item: item[0].casefold())

    parent = directory.parent
    return {
        "path": str(directory),
        "parent": None if parent == directory else str(parent),
        "dirs": dirs,
        "files": files,
    }


def is_hidden(path: str | Path) -> bool:
    """Whether a path is hidden: a dotfile, or Windows' hidden attribute.

    The `os.DirEntry` version of this question is `_hidden` below; both ask
    `_hidden_attribute`, so "hidden" means one thing in this app. A path that
    cannot be stat'ed is not called hidden - it is simply not there, which is
    a different answer and belongs to whoever tries to open it.
    """
    path = Path(path)
    if path.name.startswith("."):
        return True
    try:
        return _hidden_attribute(path.lstat())
    except OSError:
        return False


def _canonical(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _hidden_attribute(status: os.stat_result) -> bool:
    """Whether a stat result carries Windows' FILE_ATTRIBUTE_HIDDEN."""
    try:
        attributes = status.st_file_attributes
    except AttributeError:  # not Windows: there is no hidden attribute
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_HIDDEN)


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    return _hidden_attribute(entry.stat(follow_symlinks=False))
