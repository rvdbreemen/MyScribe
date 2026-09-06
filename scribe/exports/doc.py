"""The transcript document: everything an exporter needs, read once.

Every writer in this package is a pure function `(doc, options) -> bytes`,
so something has to do the reading, and it is this module alone. `load`
takes a connection and a media id and hands back a `TranscriptDoc`: the media
row, the run row, the run's words in transcript order, its segments, and its
speaker labels as a cluster-to-name mapping. A writer that has the document
has no reason to know what a connection is.

The document is a frozen dataclass whose sequences are tuples, so a writer
cannot reorder or drop a word by accident and two writers given the same
document see the same thing. The rows inside it are plain dicts - the shape
`render.paragraphs` and the templates already take - and are treated as
read-only by convention; a writer that needs a changed word builds a new one.

What is read, and why:

* **words** - `idx, start, end, text, probability, speaker` from `word`,
  ordered by `idx`. Text keeps faster-whisper's leading space, so joining
  is `"".join(...)`, as `render.join_text` does (ADR-003). ``text`` is read
  through the glossary's correction layer - one LEFT JOIN on
  `word_correction`, `scribe.glossary` - so an export says what the transcript
  view says. The layer is applied here and never written back: `word.text`
  stays what Whisper produced, and deleting the correction rows makes every
  export revert with it. ``corrected_from`` rides along for a writer that
  wants to show what a word used to say; none does today.
* **segments** - Whisper's own immutable output for the run, with its
  per-segment confidence fields. Source data for the JSON export; never
  user-facing segmentation.
* **labels** - `speaker_label.cluster_label -> display_name`. Applied by the
  writers through `render.speaker_display`, so a rename changes one row and
  the next export follows.
* **duration** - the media's, or the last word's end when probe never wrote
  one, so a writer always has a number to put in a header.

The reads happen under `db.LOCK` like every other reader in the web process
(ADR-002); there is no transaction around them because the runner commits a
run's words, segments and `is_current` flag together, and an edit is one
statement, so each SELECT already sees a run that is whole. Nothing here
writes: `tests/test_exports_doc.py` counts the connection's changes to prove it.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from scribe import db, glossary


class NoTranscript(LookupError):
    """The media has no current run (or no run by that id), or does not exist.

    Raised by name so a route can answer 404/409 and the CLI can print the
    reason, instead of either writing an empty file.
    """


@dataclass(frozen=True)
class TranscriptDoc:
    """One media's transcript as the writers take it. See the module docstring."""

    media: dict
    run: dict
    words: tuple[dict, ...]
    segments: tuple[dict, ...]
    labels: dict[str, str]
    duration: float
    title: str


def local_date(epoch: float) -> str:
    """A database epoch as the local calendar day, ``2026-09-02``: the one
    way the day a recording was added is printed - in `options.filename_for`'s
    ``{date}``, the DOCX and HTML facts line and the Markdown front matter -
    so the three cannot disagree. Here, below every module that prints it."""
    return time.strftime("%Y-%m-%d", time.localtime(float(epoch)))


def load(conn: sqlite3.Connection, media_id: int, run_id: int | None = None) -> TranscriptDoc:
    """The document for ``media_id``: its current run, or ``run_id`` if given.

    ``run_id`` must belong to the media; a run of another media is refused
    like a missing one, so a mistyped id cannot export somebody else's words
    under this media's title.
    """
    with db.LOCK:
        media = conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        if media is None:
            raise NoTranscript(f"no media with id {media_id}")
        if run_id is None:
            run = conn.execute(
                "SELECT * FROM run WHERE media_id=? AND is_current=1 ORDER BY id DESC LIMIT 1",
                (media_id,),
            ).fetchone()
            if run is None:
                raise NoTranscript(f"media {media_id} ({media['title']!r}) has no transcript yet")
        else:
            run = conn.execute(
                "SELECT * FROM run WHERE id=? AND media_id=?", (run_id, media_id)
            ).fetchone()
            if run is None:
                raise NoTranscript(f"media {media_id} has no run with id {run_id}")
        words = conn.execute(
            f"SELECT w.idx, w.start, w.end, {glossary.CORRECTED_TEXT} AS text,"
            " w.probability, w.speaker, c.original AS corrected_from"
            f" FROM word w {glossary.CORRECTION_JOIN}"
            " WHERE w.run_id=? ORDER BY w.idx",
            (run["id"],),
        ).fetchall()
        segments = conn.execute(
            "SELECT idx, start, end, text, avg_logprob, no_speech_prob, compression_ratio,"
            " temperature FROM segment WHERE run_id=? ORDER BY idx",
            (run["id"],),
        ).fetchall()
        labels = conn.execute(
            "SELECT cluster_label, display_name FROM speaker_label WHERE run_id=?"
            " ORDER BY cluster_label",
            (run["id"],),
        ).fetchall()

    word_rows = tuple(dict(row) for row in words)
    duration = media["duration"]
    if duration is None:
        duration = word_rows[-1]["end"] if word_rows else 0.0
    return TranscriptDoc(
        media=dict(media),
        run=dict(run),
        words=word_rows,
        segments=tuple(dict(row) for row in segments),
        labels={row["cluster_label"]: row["display_name"] for row in labels},
        duration=float(duration),
        title=str(media["title"]),
    )
