"""Private mode: the pin that makes "this never leaves the machine" a control.

The promise is simple enough to state in one line - *a media pinned private,
or living anywhere inside a folder pinned private, is never sent to a provider
that is not local* - and everything in this module exists to make that promise
hold by construction rather than by anyone remembering it.

Three design decisions, each of which is the reason a test exists:

* **Fail closed, and closed means closed.** The only inputs that produce "you
  may send this" are a media row that exists and answers 0 for itself and
  every folder above it. A media id with no row is a `LookupError`, not a
  `False`: a caller asking about a file that is not there has a bug, and a bug
  must not read as consent.

* **The check runs before anything is built.** `assert_allowed` takes a
  provider *class* as happily as an instance and reads one class attribute,
  `is_local`, so `llm.chat()` can refuse before a client, a request body or a
  connection exists. "Before the transport is touched" is then a structural
  fact rather than a claim about the order of two lines.

* **`PrivacyRefused` is deliberately not an `LlmError`.** `LlmError` is the
  family a caller may reasonably answer with "retry" or "try another provider"
  - `base.retarget` exists for exactly that fallback. A refusal is a decision,
  not a failure, and it must never be reachable from a handler whose recovery
  is *send the same text somewhere else*. Sitting outside that tree makes that
  impossible rather than merely discouraged.

The inheritance is a walk *up* the folder chain, which is why it is a
different recursive query from `web/library.py`'s (that one walks down, to
render the sidebar). Pinning a folder has to mean something to the file three
levels inside it, or "pin the folder" is advice rather than a control.
"""

from __future__ import annotations

import sqlite3

from scribe import db
from scribe.llm.base import Provider

MAX_FOLDER_DEPTH = 64
"""How far up the chain the walk climbs.

`folder.parent_id` has no constraint that forbids a cycle, and an unbounded
recursive CTE meeting one spins forever inside a request. Sixty-four is far
past any real library and cheap to bound. A cycle is a corrupt tree either
way; this makes it a wrong answer instead of a hung process.
"""


class PrivacyRefused(Exception):
    """A pinned media's text was about to go to a provider that is not local.

    Carries the media id and the provider name so a jobs board can say what was
    refused - and nothing from the transcript, which is the thing being
    protected and has no business in an error string that ends up in
    `job.error_detail` or a pasted bug report.
    """

    def __init__(self, message: str, *, media_id: int | None = None, provider: str | None = None):
        super().__init__(message)
        self.media_id = media_id
        self.provider = provider


_CHAIN_SQL = """
WITH RECURSIVE chain(folder_id, private, depth) AS (
    SELECT folder_id, private, 0 FROM media WHERE id = ?
  UNION ALL
    SELECT f.parent_id, f.private, c.depth + 1
      FROM folder f JOIN chain c ON f.id = c.folder_id
     WHERE c.depth < ?
)
SELECT COUNT(*) AS rows_seen, MAX(private) AS private FROM chain
"""
"""The media row and every folder above it, in one query.

The seed row is the media itself, so its own flag counts and a media with no
folder (`folder_id IS NULL`) terminates immediately with the right answer
rather than falling off the end. `rows_seen = 0` means the media id matched
nothing at all - the case that must never be read as "not private".
"""


def is_private(conn: sqlite3.Connection, media_id: int) -> bool:
    """True when this media, or any folder above it, is pinned private.

    Raises `LookupError` when there is no such media.
    """
    with db.LOCK:
        row = conn.execute(_CHAIN_SQL, (media_id, MAX_FOLDER_DEPTH)).fetchone()

    if row is None or not row["rows_seen"]:
        raise LookupError(f"no media {media_id!r}: refusing to decide whether it is private")
    return bool(row["private"])


def assert_allowed(
    conn: sqlite3.Connection, media_id: int, provider: Provider | type[Provider]
) -> None:
    """Raise `PrivacyRefused` if `provider` would take a private media off-machine.

    Called at the top of `llm.chat()`, before a provider instance, a request
    body or a socket exists. `provider` may be the class - reading `is_local`
    needs no instance, and refusing before construction is the point.
    """
    if provider.is_local:
        return
    if not is_private(conn, media_id):
        return

    name = provider.name
    raise PrivacyRefused(
        f"media {media_id} is pinned private, so its text cannot be sent to "
        f"{name!r}, which is not local; use a local provider, or un-pin the "
        f"media if you meant to send it",
        media_id=media_id,
        provider=name,
    )
