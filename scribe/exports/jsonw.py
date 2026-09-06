"""JSON: the transcript as data, word by word, for other programs.

::

    {
      "media": {...},
      "run": {...},
      "schema_version": 1,
      "segments": [...],
      "speakers": {...},
      "words": [...]
    }

``words`` is the canonical transcript itself (ADR-003): every word with its
``idx``, ``start``, ``end``, ``text`` and ``speaker``, in order, the text as
stored - faster-whisper's leading space included, so concatenating the texts
gives the transcript back exactly, in any language. ``segments`` are
Whisper's own segments for the run: source data, not a grouping.
``probability`` on a word and ``avg_logprob``, ``no_speech_prob``,
``compression_ratio`` and ``temperature`` on a segment come along with
``include_confidence``. ``speakers`` maps each cluster label in the words to
the name it displays under - the label applied, the default filled in - so
a consumer never needs the naming rule; with ``options.speakers`` off the
map is empty and every word's ``speaker`` is null.

``media`` and ``run`` carry what identifies the recording and the
transcription - title, content hash, model, language, when each was created
(ISO 8601, UTC) - and not the store path or the folder, which mean nothing
outside this installation. ``run.params`` is the run's recorded settings,
parsed; ``{}`` when there are none.

The document is written with sorted keys, two-space indentation and
``ensure_ascii=False``, so two exports of the same words are the same bytes
and a diff between two runs is readable. The encoding is UTF-8 without a BOM
whatever the options say - RFC 8259 allows nothing else, and a JSON file
nobody can parse is not an export - while the line ending follows them.
``schema_version`` is 1; a change to the shape above is a new number.
"""

from __future__ import annotations

import json
import time

from scribe import render
from scribe.exports import common
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions

SCHEMA_VERSION = 1

MEDIA_FIELDS: tuple[str, ...] = ("id", "orig_name", "sha256", "size_bytes")
RUN_FIELDS: tuple[str, ...] = ("id", "engine", "model", "compute_type", "language", "task", "xrt")
SEGMENT_CONFIDENCE: tuple[str, ...] = ("avg_logprob", "no_speech_prob", "compression_ratio", "temperature")


def write(doc: TranscriptDoc, options: ExportOptions) -> bytes:
    """The document as canonical JSON."""
    media = {key: doc.media.get(key) for key in MEDIA_FIELDS}
    media["title"] = doc.title
    media["duration"] = doc.duration
    media["created_at"] = iso(doc.media.get("created_at"))
    run = {key: doc.run.get(key) for key in RUN_FIELDS}
    run["created_at"] = iso(doc.run.get("created_at"))
    run["params"] = _params(doc.run.get("params_json"))
    speakers = (
        {cluster: render.speaker_display(doc.labels, cluster) for cluster in common.clusters(doc)}
        if options.speakers
        else {}
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "media": media,
        "run": run,
        "speakers": speakers,
        "words": [_word(word, options) for word in doc.words],
        "segments": [_segment(segment, options) for segment in doc.segments],
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    return common.encode(text, options, encoding="utf-8")


def _word(word: dict, options: ExportOptions) -> dict:
    out = {
        "idx": word.get("idx"),
        "start": word.get("start"),
        "end": word.get("end"),
        "text": word.get("text"),
        "speaker": word.get("speaker") if options.speakers else None,
    }
    if options.include_confidence:
        out["probability"] = word.get("probability")
    return out


def _segment(segment: dict, options: ExportOptions) -> dict:
    out = {
        "idx": segment.get("idx"),
        "start": segment.get("start"),
        "end": segment.get("end"),
        "text": segment.get("text"),
    }
    if options.include_confidence:
        for key in SEGMENT_CONFIDENCE:
            out[key] = segment.get(key)
    return out


def iso(epoch: float | None) -> str | None:
    """A database epoch as ISO 8601 in UTC, ``2026-09-02T12:00:00Z``; None stays None."""
    if epoch is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))


def _params(raw: object) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
