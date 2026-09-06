"""Plain text, in the four layouts the dialog offers.

* ``paragraph`` (the default) - one line per `render.paragraphs` paragraph,
  a blank line between, ``[m:ss] NAME: `` in front when timestamps and
  speakers are on. What a reader wants of a transcript on paper.
* ``turn`` - one block per speaker turn, ``NAME (m:ss): text``, consecutive
  paragraphs by the same speaker joined, so an answer the paragraph rules
  split on a pause reads as the one answer it was. Without a name the block
  is ``[m:ss] text``; without a stamp, ``NAME: text``.
* ``cue`` - one subtitle cue per line, ``[m:ss] NAME: text``: the subtitle
  as a list, for checking timing against the audio or for a tool that wants
  a line per cue. The cues are `cues.build`'s, under the options' constraints.
* ``monologue`` - the words and nothing else, wrapped at 80 columns: no
  names, no stamps, no paragraph breaks. The layout for pasting into a
  prompt or a search box.

Timestamps follow ``options.timestamps`` and ``options.timestamp_format``
through `common.stamped` and `common.timestamp`: a paragraph's or a turn's
own stamp goes in front of it and finer ones (sentence, word, interval) go
inline as ``[m:ss]``. In the cue layout the cue is the unit, and any
granularity but ``none`` stamps each cue. Names come from the document's
labels at write time (ADR-003); the text is joined as the words are stored,
and only where stamps are put between sentences or words is a space added.
"""

from __future__ import annotations

import textwrap
from typing import Callable

from scribe import render
from scribe.exports import common, cues
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions

WRAP_COLUMNS = 80


def write(doc: TranscriptDoc, options: ExportOptions) -> bytes:
    """The document as plain text in ``options.txt_layout``."""
    layout = options.txt_layout
    if layout == "cue":
        text = _cues(doc, options)
    elif layout == "monologue":
        text = _monologue(doc)
    elif layout == "turn":
        text = _turns(doc, options)
    else:
        text = _paragraphs(doc, options)
    return common.encode(text, options)


def _bracketed(options: ExportOptions) -> Callable[[float], str]:
    """The inline stamp: ``[m:ss]`` in the options' format."""
    return lambda seconds: f"[{common.timestamp(seconds, options.timestamp_format)}]"


def _blocks(blocks: list[str]) -> str:
    """Blocks separated by a blank line, the file ending in one newline."""
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _cues(doc: TranscriptDoc, options: ExportOptions) -> str:
    stamp = _bracketed(options)
    lines: list[str] = []
    # Sentinel, not None: None is the speaker of a cue in an undiarized
    # transcript, so the first cue must still be able to open a run.
    previous: object = object()
    for cue in cues.build(doc, options).cues:
        parts: list[str] = []
        if options.timestamps != "none":
            parts.append(stamp(cue.start))
        # Only on a change of speaker, for the same reason as the SRT writer:
        # a name on every cue of one run is noise.
        name = common.speaker_name(doc, cue.speaker, options) if cue.speaker != previous else ""
        previous = cue.speaker
        if name:
            parts.append(f"{name}:")
        parts.append(" ".join(cue.lines))
        lines.append(" ".join(parts))
    return "".join(line + "\n" for line in lines)


def _monologue(doc: TranscriptDoc) -> str:
    text = textwrap.fill(
        render.join_text(doc.words), WRAP_COLUMNS, break_long_words=False, break_on_hyphens=False
    )
    return text + "\n" if text else ""


def _paragraphs(doc: TranscriptDoc, options: ExportOptions) -> str:
    stamp = _bracketed(options)
    paragraphs = render.paragraphs(doc.words, doc.labels)
    blocks: list[str] = []
    for paragraph, pieces in zip(paragraphs, common.stamped(paragraphs, options)):
        lead, body = common.block(pieces, stamp)
        parts: list[str] = []
        if lead is not None:
            parts.append(stamp(lead))
        name = common.speaker_name(doc, paragraph.speaker, options)
        if name:
            parts.append(f"{name}:")
        parts.append(body)
        blocks.append(" ".join(part for part in parts if part))
    return _blocks(blocks)


def _turns(doc: TranscriptDoc, options: ExportOptions) -> str:
    stamp = _bracketed(options)
    paragraphs = render.paragraphs(doc.words, doc.labels)
    stamped = common.stamped(paragraphs, options)
    blocks: list[str] = []
    for first, last in common.turns(paragraphs):
        pieces = [piece for group in stamped[first:last] for piece in group]
        lead, body = common.block(pieces, stamp)
        name = common.speaker_name(doc, paragraphs[first].speaker, options)
        if name and lead is not None:
            head = f"{name} ({common.timestamp(lead, options.timestamp_format)}):"
        elif name:
            head = f"{name}:"
        elif lead is not None:
            head = stamp(lead)
        else:
            head = ""
        blocks.append(" ".join(part for part in (head, body) if part))
    return _blocks(blocks)
