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

Looking a key up, queueing the job and remembering the key happen under one
`db.LOCK` hold (an RLock, so `jobs.enqueue` inside it nests): two identical
requests racing each other queue one job between them. The lock is held for
the lookup, one INSERT and one more, never for the upload or the hashing.

Not a library: the candidates on PyPI (ichido, fastapi-idempotent, idemptx,
idemkit; checked 2026-10-24) cache whole responses in Redis or are
pre-release, and none ties a key to the job row it queued.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from scribe import db

HEADER = "Idempotency-Key"
KEEP_SECONDS = 24 * 3600
MAX_KEY_LENGTH = 255


class KeyReused(Exception):
    """The key was used before for a different request."""


def valid(key: str) -> bool:
    """1-255 visible ASCII characters: what fits in a header and a log line."""
    return 0 < len(key) <= MAX_KEY_LENGTH and key.isascii() and key.isprintable() and key.strip() == key


def fingerprint(route: str, **parts: Any) -> str:
    """A stable hash of what a request asked for."""
    canonical = json.dumps({"route": route, **parts}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def replay(conn: sqlite3.Connection, key: str, print_: str) -> tuple[int, Any] | None:
    """The stored (status, body) for ``key``, None when it is new; `KeyReused`
    when it was used for another request. Expired keys are forgotten first."""
    with db.LOCK:
        conn.execute("DELETE FROM api_idempotency WHERE created_at < ?", (time.time() - KEEP_SECONDS,))
        conn.commit()
        row = conn.execute(
            "SELECT fingerprint, status, response_json FROM api_idempotency WHERE key=?", (key,)
        ).fetchone()
    if row is None:
        return None
    if row["fingerprint"] != print_:
        raise KeyReused(key)
    return int(row["status"]), json.loads(row["response_json"])


def remember(
    conn: sqlite3.Connection, key: str, route: str, print_: str, status: int, body: Any
) -> None:
    with db.LOCK:
        conn.execute(
            "INSERT INTO api_idempotency(key, route, fingerprint, status, response_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (key, route, print_, status, json.dumps(body, default=str), time.time()),
        )
        conn.commit()
