"""Rows for UI tests, without a pipeline.

Every web test needs a library that looks transcribed: media rows, a run with
words that carry speakers and probabilities, segments the FTS triggers have
seen, a job or two in known states. Running the pipeline to get there costs a
model load per test; these helpers write the same rows the real stages
would - the same columns, the same conventions - in a millisecond.

Conventions mirrored from the stages, because the views built on these rows
are the views built on the real ones:

* word text keeps faster-whisper's leading space (`" towel"`), so joining is
  `"".join(...)` and never inserts spaces of its own (ADR-003);
* segment text is the stripped join of its words, which is what
  `transcribe._persist` stores and what the FTS index therefore holds;
* `seed_run(current=True)` clears `is_current` on the media's other runs
  first, exactly as `finalize._commit` does.

The default transcript is forty words in four ten-word sentences, two
speakers, and a spread of probabilities across all three confidence bands -
enough to exercise paragraph breaks, sentence breaks, speaker headings, the
confidence tint and full-text search from one call.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time

from scribe import db

DEFAULT_MODEL = "large-v3-turbo"
DEFAULT_COMPUTE_TYPE = "float16"
DEFAULT_LANGUAGE = "en"

# Four sentences, ten words each, two speakers. Sentence-final punctuation on
# the last word of each so sentence splitting has something to split on.
_SENTENCES: tuple[tuple[str, str], ...] = (
    ("SPEAKER_00", "Don't panic, the towel is still the most important item."),
    ("SPEAKER_00", "The answer to life, the universe and everything is forty-two."),
    ("SPEAKER_01", "Marvin says the improbability drive makes him even more depressed."),
    ("SPEAKER_01", "Vogon poetry is the third worst in the known universe."),
)

# Cycled over the words: two high, one mid, one low (render.confidence_band's
# boundaries are 0.85 and 0.6), so every band shows up in every sentence.
_PROBABILITIES = (0.97, 0.91, 0.72, 0.55)

WORD_SECONDS = 0.4
GAP_SECONDS = 0.1


def default_words() -> list[dict]:
    """The forty default words, timed half a second apart."""
    words: list[dict] = []
    for speaker, sentence in _SENTENCES:
        for token in sentence.split(" "):
            i = len(words)
            start = i * (WORD_SECONDS + GAP_SECONDS)
            words.append(
                {
                    "idx": i,
                    "start": start,
                    "end": start + WORD_SECONDS,
                    "text": " " + token,
                    "probability": _PROBABILITIES[i % len(_PROBABILITIES)],
                    "speaker": speaker,
                }
            )
    return words


def _segments_for(words: list[dict], per_segment: int | None) -> list[dict]:
    """One segment per ``per_segment`` words, or one for all of them."""
    size = per_segment or max(1, len(words))
    segments: list[dict] = []
    for i in range(0, len(words), size):
        chunk = words[i : i + size]
        segments.append(
            {
                "idx": len(segments),
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": "".join(w["text"] for w in chunk).strip(),
            }
        )
    return segments


def seed_media(
    conn: sqlite3.Connection,
    *,
    title: str = "Clip",
    duration: float | None = 30.0,
    folder_id: int | None = None,
) -> int:
    """A media row with a unique sha256; returns its id."""
    sha256 = hashlib.sha256(os.urandom(32)).hexdigest()
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO media(sha256, store_path, orig_name, title, folder_id,"
            " duration, size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sha256,
                f"media/{sha256[:2]}/{sha256}.wav",
                f"{title}.wav",
                title,
                folder_id,
                duration,
                4242,
                time.time(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def seed_run(
    conn: sqlite3.Connection,
    media_id: int,
    *,
    words: list[dict] | None = None,
    segments: list[dict] | None = None,
    labels: dict[str, str] | None = None,
    current: bool = True,
) -> int:
    """A run with its words, segments and speaker labels; returns the run id.

    ``words`` are dicts with ``start``, ``end``, ``text`` and optionally
    ``idx`` (defaults to position), ``probability`` and ``speaker``. Without
    ``segments`` the default transcript gets its four sentences as segments
    and custom words get one segment covering all of them. ``labels`` maps a
    cluster label to a display name.
    """
    if words is None:
        rows = default_words()
        segment_rows = segments if segments is not None else _segments_for(rows, 10)
    else:
        rows = [
            {
                "idx": w.get("idx", i),
                "start": float(w["start"]),
                "end": float(w["end"]),
                "text": w["text"],
                "probability": w.get("probability", 0.9),
                "speaker": w.get("speaker"),
            }
            for i, w in enumerate(words)
        ]
        segment_rows = segments if segments is not None else _segments_for(rows, None)

    with db.LOCK:
        try:
            if current:
                conn.execute(
                    "UPDATE run SET is_current=0 WHERE media_id=?", (media_id,)
                )
            cur = conn.execute(
                "INSERT INTO run(media_id, model, compute_type, language, task,"
                " xrt, is_current, created_at) VALUES (?, ?, ?, ?, 'transcribe', ?, ?, ?)",
                (
                    media_id,
                    DEFAULT_MODEL,
                    DEFAULT_COMPUTE_TYPE,
                    DEFAULT_LANGUAGE,
                    14.5,
                    1 if current else 0,
                    time.time(),
                ),
            )
            run_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO segment(run_id, idx, start, end, text)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (run_id, s.get("idx", i), s["start"], s["end"], s["text"])
                    for i, s in enumerate(segment_rows)
                ],
            )
            conn.executemany(
                "INSERT INTO word(run_id, idx, start, end, text, probability, speaker)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, w["idx"], w["start"], w["end"], w["text"],
                        w["probability"], w["speaker"],
                    )
                    for w in rows
                ],
            )
            conn.executemany(
                "INSERT INTO speaker_label(run_id, cluster_label, display_name)"
                " VALUES (?, ?, ?)",
                [(run_id, cluster, name) for cluster, name in (labels or {}).items()],
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return run_id


def seed_job(
    conn: sqlite3.Connection,
    media_id: int,
    status: str = "running",
    stage: str = "transcribe",
    progress: float = 0.4,
    *,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> int:
    """A transcribe job in ``status``; returns its id.

    The timestamps follow the status the way the runner would leave them: a
    queued job has not started and carries no stage; a running one started a
    minute ago and is at ``progress`` within ``stage``; a terminal one also
    finished, in the stage it was in.
    """
    now = time.time()
    queued = status == "queued"
    terminal = status in ("done", "failed", "cancelled", "interrupted")
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO job(type, media_id, status, stage, stage_progress,"
            " params_json, error_code, error_detail, created_at, started_at, finished_at)"
            " VALUES ('transcribe', ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?)",
            (
                media_id,
                status,
                None if queued else stage,
                0.0 if queued else (1.0 if status == "done" else progress),
                error_code,
                error_detail,
                now - 120,
                None if queued else now - 60,
                now if terminal else None,
            ),
        )
        conn.commit()
        return cur.lastrowid
