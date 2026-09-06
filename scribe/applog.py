"""The application log: one JSON line per event, from every process, in one file.

Job events live in SQLite, per job, and are the right record of a job's
progress. They are blind in three places, and those three are where a
"recording does not transcribe" report actually went wrong:

* **before a job exists** - the recorder's start, chunks and finish, an
  upload, the enqueue itself;
* **between the supervisor and the runner** - which pid was spawned for which
  job, and what exit code it came back with;
* **when the runner dies before its first stage** - a DLL that will not load,
  an import that fails. The supervisor spawns it with no stdout or stderr, so
  the one sentence that would explain the failure goes nowhere.

So this is the layer around the event log, not a replacement for it.
Transitions go to both - a stage beginning, a verdict - because a log that
makes you cross-reference SQLite to understand a failure is a worse log, and
both copies come from the same call in the same frame, so they cannot drift.
Progress goes only to the event log: per-segment percentages, ETA samples,
the things that arrive by the hundred. What this file adds that SQLite never
sees is the process boundary: "runner 31260 started for job 24", "exited 1",
and the child's stderr.

**The file.** ``logs/app.log`` under DATA_DIR, JSON Lines, oldest first. Each
line is ``{"ts", "level", "proc", "pid", "event", ...fields}``; `proc` is
which process wrote it (web / supervisor / runner:<job>). Append-only within
the file: nothing here rewrites a line. Retention is a cap, not forever:
past :data:`MAX_BYTES` the file is renamed to ``app.log.1`` and a new one
started, and one previous file is kept. Decided 2026-09-05 with the question
left open twice; a log that can grow without bound is a disk-full waiting to
happen on the machine it is meant to help.

**Three processes, one file, Windows.** ``open(path, "a")`` is not an atomic
append on Windows - the C runtime seeks to the end and then writes, and two
processes can interleave a line. Neither is ``O_APPEND`` through ``os.write``,
which was the first design here and was caught by its own test. So every line
is one write made while holding a byte lock on a separate ``app.log.lock``
file - separate because Windows locks are mandatory and a lock on the log
itself made every *read* of it fail. Two thousand lines from two processes
parse back whole; see ``_append`` for the rest of the reasoning.

**Secrets.** Provider API keys pass through Settings and through request
forms. A log that writes fields by name is a new place for a key to land, so
any field whose name contains key / token / secret / password / authorization
is redacted before it is written, by name and not by looking at the value.

This is observation, not coordination: nothing reads this file to decide
anything, and ADR-002's rule that SQLite is the only coordination between the
processes is untouched.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from scribe import paths

FILE_NAME = "app.log"
MAX_BYTES = 32 * 1024 * 1024
KEEP_PREVIOUS = 1
# The most one tail() call will read. A page that fell an hour behind - a
# laptop back from sleep with /logs open - asks for everything since its
# offset, and 60,000 lines measured at 1.7 s of Python before rendering.
# Past this much it gets the newest lines and a gap, not a frozen worker.
MAX_TAIL_BYTES = 1024 * 1024

LEVELS = ("debug", "info", "warn", "error")

# Field *names* that never reach the file. Matched case-insensitively as a
# substring, so `api_key`, `Authorization`, `hf_token` and `password2` all
# qualify. The value is replaced, not dropped: a reader still sees that the
# field was there.
_SECRET_NAME = re.compile(r"key|token|secret|password|authorization", re.IGNORECASE)
REDACTED = "[redacted]"

# By shape as well as by name. A URL's query string is where a signed share
# link keeps its credential (`?token=`, `&Signature=`, a one-time Dropbox or
# Zoom link), and the URL is logged under the innocent name `url`. The path
# of an exported cookies.txt is not a secret but is the address of one. Both
# came through `params` on a URL ingest, verbatim, until the review.
_URL_WITH_QUERY = re.compile(r"^(https?://[^\s?#]+)\?[^\s#]*", re.IGNORECASE)
_PATH_NAMES = re.compile(r"cookies_file|cookiefile|cookies", re.IGNORECASE)
PRESENT = "[present]"

# What to call the writer in `proc`. Per *thread*, not per process, and that
# is the whole point: the supervisor is a thread inside the web process
# (ADR-001), and a module global set by its loop renamed every web request's
# line to "supervisor" for as long as the app ran - found by the review, not
# by the tests, which never ran a supervisor thread beside the log. A thread
# that never configured itself falls back to the process-wide name, so the
# runner child (one thread) and the web process (many, all "web") both read
# right; the supervisor thread names itself and nobody else.
_names = threading.local()
_default_proc: str | None = None
_lock = threading.Lock()


def configure(proc: str, *, this_thread_only: bool = False) -> None:
    """Name the writer for every line from now on.

    Process-wide by default (the runner child, the web process). The
    supervisor passes ``this_thread_only=True``: it is a thread among the web
    process's request threads and must not rename them."""
    global _default_proc
    if this_thread_only:
        _names.proc = proc
    else:
        _default_proc = proc


def _proc_name() -> str:
    return getattr(_names, "proc", None) or _default_proc or ""


def path() -> Path:
    return paths.LOGS_DIR / FILE_NAME


def log(event: str, level: str = "info", **fields: Any) -> None:
    """Append one line. Never raises: a log that can take the app down with it
    is worse than a missing line, so every failure here is swallowed after one
    attempt to say so on stderr."""
    if level not in LEVELS:
        level = "info"
    record: dict[str, Any] = {
        "ts": round(time.time(), 3),
        "level": level,
        "proc": _proc_name(),
        "pid": os.getpid(),
        "event": event,
    }
    for name, value in fields.items():
        record[name] = _field(name, value)
    line = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    try:
        _append(line)
    except Exception as exc:  # pragma: no cover - only under a broken disk
        print(f"[applog] could not write {event}: {exc}", file=sys.stderr)


def _field(name: str, value: Any) -> Any:
    if _SECRET_NAME.search(name):
        return REDACTED
    if _PATH_NAMES.search(name) and value:
        return PRESENT
    return _plain(value)


def _plain(value: Any) -> Any:
    """JSON-safe, short, and stripped of the shapes that carry secrets: a
    traceback or a form field can be long, and a log line that is a page is a
    log nobody tails."""
    if isinstance(value, str):
        value = _URL_WITH_QUERY.sub(r"\1?" + REDACTED, value)
        return value[:2000] if len(value) > 2000 else value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value][:50]
    if isinstance(value, dict):
        return {str(k): _field(str(k), v) for k, v in list(value.items())[:50]}
    return str(value)[:2000]


def _append(line: bytes) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # The cross-process mutex is taken *before* the thread lock, so a web
    # request thread waiting on the runner's write does not also hold every
    # other web thread's line hostage. Rotation is decided inside the mutex,
    # so two processes cannot both rename, and a writer cannot open the old
    # file after another has just renamed it.
    mutex = os.open(str(target.with_name(target.name + ".lock")),
                    os.O_RDWR | os.O_CREAT, 0o644)
    try:
        held = _acquire(mutex)
        try:
            with _lock:
                _rotate_if_needed(target, len(line))
                fd = os.open(str(target), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
                try:
                    os.write(fd, line)
                finally:
                    os.close(fd)
        finally:
            if held:
                _release(mutex)
    finally:
        os.close(mutex)


# O_APPEND is not enough on Windows. The C runtime implements _O_APPEND as a
# seek to the end followed by a write - two calls - so two processes can land
# between each other's, and did: two writers of a thousand lines each produced
# spliced lines the first time this was tried.
#
# The mutex is a byte in a *separate* file, app.log.lock, and that is not
# tidiness. Windows byte-range locks are mandatory: while a writer held byte 0
# of app.log itself, every read of app.log - the Log page's tail, a curious
# `type` - failed with "Permission denied", and msvcrt's blocking mode waits
# in one-second steps for up to ten seconds, which turned a busy test suite
# into a slow one. So the lock lives where nobody reads, and is taken with
# short non-blocking tries: a writer that cannot get it within LOCK_WAIT_S
# writes anyway. A rare spliced line is a smaller failure than a process
# that stops to wait for a log. POSIX gets flock on the same file, where
# O_APPEND alone would in fact have sufficed.
LOCK_WAIT_S = 2.0

if sys.platform == "win32":
    import msvcrt

    def _acquire(fd: int) -> bool:
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.002)

    def _release(fd: int) -> None:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:  # pragma: no cover - already unlocked
            pass
else:  # pragma: no cover - not the platform this runs on
    import fcntl

    def _acquire(fd: int) -> bool:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return True

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _rotate_if_needed(target: Path, incoming: int) -> None:
    """Rename at the cap. Racy by design and harmless: two processes that both
    decide to rotate at once produce one rename that wins and one that finds
    the file gone and appends to the new one. Nothing is lost either way,
    because nothing is truncated - only renamed."""
    try:
        size = target.stat().st_size
    except FileNotFoundError:
        return
    if size + incoming <= MAX_BYTES:
        return
    previous = target.with_name(target.name + ".1")
    try:
        if previous.exists():
            previous.unlink()
        target.rename(previous)
    except OSError:
        # Another process rotated first, or the previous file is open for
        # reading somewhere. Appending to whatever `target` now is loses
        # nothing; the cap is checked again on the next line.
        pass


# --- reading ------------------------------------------------------------------------------


def size() -> int:
    try:
        return path().stat().st_size
    except FileNotFoundError:
        return 0


def tail(after: int = 0, limit: int = 300) -> tuple[list[dict], int]:
    """Lines written after byte offset ``after``, and the new offset.

    With ``after == 0`` this is the *last* ``limit`` lines - the log page's
    first paint - found by reading back from the end rather than the whole
    file: at 32 MB the whole file is the difference between a page and a
    pause. A partial line at the very end (a writer mid-line) is left for the
    next call, which is what the returned offset guarantees.
    """
    target = path()
    try:
        size = target.stat().st_size
    except FileNotFoundError:
        return [], 0
    if after <= 0 or after > size:
        start = _offset_of_last_lines(target, size, limit)
    else:
        start = after
    skipped = False
    if size - start > MAX_TAIL_BYTES:
        start = size - MAX_TAIL_BYTES
        skipped = True
    with open(target, "rb") as fh:
        fh.seek(start)
        raw = fh.read(size - start)
    if skipped:
        # Landed mid-line: drop the fragment before the first newline.
        first = raw.find(b"\n")
        raw, start = (raw[first + 1:], start + first + 1) if first >= 0 else (b"", size)
    complete = raw.rfind(b"\n")
    if complete < 0:
        return [], start
    body, new_offset = raw[: complete + 1], start + complete + 1
    lines = []
    for chunk in body.split(b"\n"):
        if not chunk:
            continue
        try:
            lines.append(json.loads(chunk.decode("utf-8", "replace")))
        except json.JSONDecodeError:
            lines.append({"ts": None, "level": "warn", "event": "unparseable",
                          "raw": chunk.decode("utf-8", "replace")[:500]})
    return lines[-limit:], new_offset


def _offset_of_last_lines(target: Path, size: int, count: int) -> int:
    """Byte offset where the last ``count`` lines begin, reading backwards in
    64 KB steps; 0 when the file is shorter than that many lines."""
    step = 65536
    seen = 0
    pos = size
    with open(target, "rb") as fh:
        while pos > 0:
            read_from = max(0, pos - step)
            fh.seek(read_from)
            block = fh.read(pos - read_from)
            seen += block.count(b"\n")
            if seen > count:
                # Walk forward inside this block to the (seen - count)th newline.
                extra = seen - count
                idx = -1
                for _ in range(extra):
                    idx = block.find(b"\n", idx + 1)
                return read_from + idx + 1
            pos = read_from
    return 0
