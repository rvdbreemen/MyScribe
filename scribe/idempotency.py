"""Idempotency keys for the /api/v1 writes that queue GPU work (ADR-025).

A program that sends a recording and times out cannot tell "it never arrived"
from "it arrived and the answer got lost". It sends the same request again,
and without a key that is a second transcribe job for the same file. With an
``Idempotency-Key`` header the second request gets the first one's answer
back, and nothing new is queued.

The rules, after the IETF httpapi draft "The Idempotency-Key HTTP Header
Field" as far as this app needs it:

* A key is remembered with a *fingerprint* of what the first request asked
  for: the route, and for an upload the content hash and the job options -
  not the raw body, whose multipart boundary changes on every retry.
* The same key with the same fingerprint replays the stored status and body.
* The same key with another fingerprint is refused (409,
  ``idempotency_key_reused``): answering it with somebody else's job would be
  the one wrong answer.
* Keys are kept `KEEP_SECONDS`, in the database, so a retry across an app
  restart still finds its key.
* A stored answer whose job is gone (its recording was deleted, and the
  job with it) is no answer any more: the key is forgotten and the request
  runs as a new one, rather than replaying a job id that 404s.
* An upload *claims* its key before it files anything (`claim`): a pending
  row, status `PENDING`, written once the content hash is known and before
  the media row is. A second request with that key while the first is still
  running is refused (`KeyInProgress`, 409 ``idempotency_key_in_progress``,
  as the draft says), and one with other content is refused before a media
  row or a stored file exists. Nothing is ever filed and then taken back, so no cleanup can race
  a concurrent upload that deduped onto the same row. The claim is let go
  when the request fails (`release`) and at every start (`forget_pending`):
  ADR-001's one process means a pending row found at start was left by a
  life that ended mid-upload.

For a route that files nothing (a re-transcribe), looking a key up, queueing
the job and remembering the key happen under one `db.LOCK` hold (an RLock,
so `jobs.enqueue` inside it nests): two identical requests racing each other
queue one job between them. The lock is held for a lookup and an INSERT or
two, never for the upload or the hashing.

Not a library: the candidates on PyPI (ichido, fastapi-idempotent, idemptx,
idemkit; checked 2026-10-24) cache whole responses in Redis or are
pre-release, and none ties a key to the job row it queued.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Callable

from scribe import db

HEADER = "Idempotency-Key"
KEEP_SECONDS = 24 * 3600
MAX_KEY_LENGTH = 255


# The status of a claimed key whose request has not answered yet. No HTTP
# status is 0, so a pending row can never be replayed as an answer.
PENDING = 0


class KeyReused(Exception):
    """The key was used before for a different request."""


class KeyInProgress(Exception):
    """The key's first request is still running."""


def valid(key: str) -> bool:
    """1-255 visible ASCII characters: what fits in a header and a log line."""
    return 0 < len(key) <= MAX_KEY_LENGTH and key.isascii() and key.isprintable() and key.strip() == key


def fingerprint(route: str, **parts: Any) -> str:
    """A stable hash of what a request asked for."""
    canonical = json.dumps({"route": route, **parts}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


Alive = Callable[[Any], bool]


def _lookup(conn: sqlite3.Connection, key: str, print_: str, alive: Alive | None):
    conn.execute("DELETE FROM api_idempotency WHERE created_at < ?", (time.time() - KEEP_SECONDS,))
    conn.commit()
    row = conn.execute(
        "SELECT fingerprint, status, response_json FROM api_idempotency WHERE key=?", (key,)
    ).fetchone()
    if row is None:
        return None
    if row["fingerprint"] != print_:
        raise KeyReused(key)
    if row["status"] == PENDING:
        raise KeyInProgress(key)
    body = json.loads(row["response_json"])
    if alive is not None and not alive(body):
        conn.execute("DELETE FROM api_idempotency WHERE key=?", (key,))
        conn.commit()
        return None
    return int(row["status"]), body


def replay(
    conn: sqlite3.Connection, key: str, print_: str, alive: Alive | None = None
) -> tuple[int, Any] | None:
    """The stored (status, body) for ``key``, None when it is new; `KeyReused`
    when it was used for another request, `KeyInProgress` while its first
    request runs. Expired keys, and answers ``alive`` calls stale, are
    forgotten first."""
    with db.LOCK:
        return _lookup(conn, key, print_, alive)


def claim(
    conn: sqlite3.Connection, key: str, route: str, print_: str, alive: Alive | None = None
) -> tuple[int, Any] | None:
    """`replay`, and when the key is new, hold it as `PENDING` for this request."""
    with db.LOCK:
        stored = _lookup(conn, key, print_, alive)
        if stored is None:
            conn.execute(
                "INSERT INTO api_idempotency(key, route, fingerprint, status, response_json, created_at)"
                " VALUES (?, ?, ?, ?, 'null', ?)",
                (key, route, print_, PENDING, time.time()),
            )
            conn.commit()
        return stored


def remember(
    conn: sqlite3.Connection, key: str, route: str, print_: str, status: int, body: Any
) -> None:
    """Store the answer for ``key``: a claimed key's pending row becomes it."""
    with db.LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO api_idempotency(key, route, fingerprint, status, response_json,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, route, print_, status, json.dumps(body, default=str), time.time()),
        )
        conn.commit()


def release(conn: sqlite3.Connection, key: str) -> None:
    """Let go of a claim whose request failed; an answered key stays."""
    with db.LOCK:
        conn.execute("DELETE FROM api_idempotency WHERE key=? AND status=?", (key, PENDING))
        conn.commit()


def forget_pending(conn: sqlite3.Connection) -> int:
    """At start: every pending claim was left by a process that has ended."""
    with db.LOCK:
        cur = conn.execute("DELETE FROM api_idempotency WHERE status=?", (PENDING,))
        conn.commit()
    return cur.rowcount
