"""What the six text writers share: timestamps, stamped prose, and the bytes.

SRT, VTT, TXT, CSV, JSON and Markdown each turn a `TranscriptDoc` into a
file, and three things are the same in all of them, so they live here rather
than six times over:

* **Timestamps.** `render.format_ts` is the one formatter (Phase 4's global
  constraint): ``clock`` is its ``m:ss``, SRT and VTT its ``hh:mm:ss,mmm``
  (VTT swaps the separator), and ``smpte`` and ``seconds`` are derived from
  it rather than computed a second time. SMPTE needs a frame rate the options
  do not carry; `SMPTE_FPS` is 25, the EBU rate, until they do.

* **Stamped prose.** The TXT paragraph and turn layouts and the Markdown
  writer all print `render.paragraphs` with a timestamp wherever
  ``options.timestamps`` says one belongs: per paragraph, per speaker turn,
  per sentence, per word, or once per ``interval_seconds``. `stamped` turns
  each paragraph into `Piece`s of text, each with a start to stamp or None;
  `block` lays a run of pieces out as one body, handing the first piece's
  stamp back for the caller to put in front in its own notation and
  rendering every later one inline before its piece. ``cue`` granularity
  means "per sentence" here, since prose has no cues; without diarization
  every paragraph counts as its own turn, the paragraph break being the only
  structure left.

* **The bytes.** `encode` applies the line ending, then the encoding. Text is
  built with ``\\n`` and converted last, so no writer reasons about ``\\r``. A
  character the chosen code page cannot hold raises `UnicodeEncodeError`
  rather than being replaced: a ``?`` in a subtitle is a silent loss, and a
  refused export names the character.

* **The facts line.** The DOCX and HTML writers (Task 4) open with what
  identifies the recording - filename, duration, model, language, the day it
  was added - and `facts` is that list, so the two headers cannot drift.

Nothing here touches the database (ADR-003): the writers hand in the document
the loader built and get text back. Speaker names are applied here, at write
time, from the document's labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from scribe import render
from scribe.exports.doc import TranscriptDoc, local_date
from scribe.exports.options import ExportOptions

# Frames per second for the smpte style: EBU's 25, until an option carries it.
SMPTE_FPS = 25


# --- timestamps ------------------------------------------------------------------


def timestamp(seconds: float, style: str = "clock") -> str:
    """``seconds`` in a ``timestamp_format`` style: ``clock`` is ``m:ss``,
    ``smpte`` is ``hh:mm:ss:ff``, ``seconds`` a plain number."""
    if style == "clock":
        return render.format_ts(seconds, "clock")
    if style == "seconds":
        return seconds_text(seconds)
    if style == "smpte":
        head, _, millis = render.format_ts(seconds, "srt").partition(",")
        return f"{head}:{int(millis) * SMPTE_FPS // 1000:02d}"
    raise ValueError(f"unknown timestamp format {style!r}; one of clock, smpte, seconds")


def seconds_text(seconds: float) -> str:
    """Seconds as the shortest decimal exact to the millisecond: ``12.3``, ``0``."""
    text = f"{max(0.0, float(seconds)):.3f}".rstrip("0").rstrip(".")
    return text or "0"


# --- speakers --------------------------------------------------------------------


def speaker_name(doc: TranscriptDoc, cluster: str | None, options: ExportOptions) -> str:
    """The name to print for ``cluster``: its label or the default, or ``""``
    when names are off or the words carry no speaker."""
    if not options.speakers or cluster is None:
        return ""
    return render.speaker_display(doc.labels, cluster)


def clusters(doc: TranscriptDoc) -> list[str]:
    """The speaker clusters of the document's words, in order of first appearance."""
    seen: dict[str, None] = {}
    for word in doc.words:
        cluster = word.get("speaker")
        if cluster is not None:
            seen.setdefault(str(cluster), None)
    return list(seen)


# --- the facts line ---------------------------------------------------------------


def facts(doc: TranscriptDoc) -> list[str]:
    """What identifies the recording, for a header line: the original
    filename, the duration as ``m:ss``, the model, the language and the day
    the recording was added (`doc.local_date` - the same day
    `options.filename_for` puts in a name). Whatever is unknown is left out."""
    created = doc.media.get("created_at")
    parts = (
        doc.media.get("orig_name"),
        render.format_ts(doc.duration, "clock"),
        doc.run.get("model"),
        doc.run.get("language"),
        None if created is None else local_date(created),
    )
    return [str(part) for part in parts if part]


# --- stamped prose ---------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """A run of text and the start to stamp before it, or None for no stamp."""

    start: float | None
    text: str


def starts_turn(paragraph: render.Paragraph, previous: render.Paragraph | None) -> bool:
    """Whether ``paragraph`` opens a speaker turn: the first one does, a
    change of speaker does, and without diarization every paragraph does."""
    return previous is None or paragraph.speaker is None or paragraph.speaker != previous.speaker


def turns(paragraphs: Sequence[render.Paragraph]) -> list[tuple[int, int]]:
    """The paragraphs grouped by speaker turn, as ``(first, past-the-last)``
    index pairs covering every paragraph."""
    spans: list[tuple[int, int]] = []
    previous: render.Paragraph | None = None
    for i, paragraph in enumerate(paragraphs):
        if starts_turn(paragraph, previous):
            spans.append((i, i + 1))
        else:
            spans[-1] = (spans[-1][0], i + 1)
        previous = paragraph
    return spans


def stamped(paragraphs: Sequence[render.Paragraph], options: ExportOptions) -> list[list[Piece]]:
    """Each paragraph as pieces, stamped where ``options.timestamps`` says.
    One list per paragraph, in order."""
    where = options.timestamps
    interval = float(options.interval_seconds)
    next_mark = 0.0
    previous: render.Paragraph | None = None
    out: list[list[Piece]] = []
    for paragraph in paragraphs:
        if where == "none":
            pieces = [Piece(None, paragraph.text)]
        elif where == "paragraph":
            pieces = [Piece(paragraph.start, paragraph.text)]
        elif where == "turn":
            start = paragraph.start if starts_turn(paragraph, previous) else None
            pieces = [Piece(start, paragraph.text)]
        elif where == "word":
            pieces = [
                Piece(float(word["start"]), str(word["text"]).strip())
                for word in paragraph.words
                if str(word.get("text") or "").strip()
            ]
        elif where == "interval":
            pieces = []
            for sentence in paragraph.sentences:
                if sentence.start >= next_mark:
                    pieces.append(Piece(sentence.start, sentence.text))
                    next_mark = (sentence.start // interval + 1) * interval
                else:
                    pieces.append(Piece(None, sentence.text))
        else:  # sentence - and cue, of which prose has none
            pieces = [Piece(sentence.start, sentence.text) for sentence in paragraph.sentences]
        out.append(pieces)
        previous = paragraph
    return out


def block(pieces: Sequence[Piece], stamp: Callable[[float], str]) -> tuple[float | None, str]:
    """``pieces`` as one body: ``(lead, text)``, where ``lead`` is the first
    piece's start for the caller to put in front (None for no stamp) and
    ``text`` carries every later stamp inline, rendered by ``stamp``."""
    if not pieces:
        return None, ""
    first, rest = pieces[0], pieces[1:]
    parts = [first.text]
    for piece in rest:
        parts.append(piece.text if piece.start is None else f"{stamp(piece.start)} {piece.text}")
    return first.start, " ".join(part for part in parts if part)


# --- the bytes -------------------------------------------------------------------


def encode(text: str, options: ExportOptions, *, encoding: str | None = None) -> bytes:
    """``text``, built with ``\\n``, as the file's bytes: the options' line
    ending first, then ``encoding`` (a format's own choice) or the options'."""
    if options.crlf:
        text = text.replace("\n", "\r\n")
    return text.encode(encoding or options.encoding)
