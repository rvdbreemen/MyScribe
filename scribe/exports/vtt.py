"""WebVTT: the ``WEBVTT`` header, numbered cues, ``hh:mm:ss.mmm`` timings
and ``<v Name>`` voice spans.

The same cues as SRT (`cues.build`), in the other syntax::

    WEBVTT

    1
    00:00:00.000 --> 00:00:04.900
    <v Arthur>Don't panic, the towel is
    still the most important item.

A voice span names the speaker without putting the name in the text, so a
player that styles voices can colour it and one that does not shows the
text alone. It opens the payload and, as the format allows for a span that
runs to the end of the cue, is not closed. Cue identifiers are the cue
numbers, so an SRT and a VTT of the same export number their cues alike.

The payload is markup: ``&``, ``<`` and ``>`` in the text and in a name are
escaped as the format requires, or a transcript's ``AT&T`` would be an
entity and a ``<`` the start of a tag. The timing is `render.format_ts`'s
SRT style with the separator VTT wants; the line ending is applied last.
"""

from __future__ import annotations

from scribe import render
from scribe.exports import common, cues
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions

HEADER = "WEBVTT\n\n"

# In this order: the ampersand first, or the entities would be escaped again.
ESCAPES: tuple[tuple[str, str], ...] = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def escape(text: str) -> str:
    """``text`` as WebVTT cue text: markup characters as entities."""
    for raw, entity in ESCAPES:
        text = text.replace(raw, entity)
    return text


def _timestamp(seconds: float) -> str:
    return render.format_ts(seconds, "srt").replace(",", ".")


def write(doc: TranscriptDoc, options: ExportOptions) -> bytes:
    """The document as WebVTT subtitles."""
    blocks: list[str] = [HEADER]
    for cue in cues.build(doc, options).cues:
        payload = "\n".join(escape(line) for line in cue.lines)
        name = common.speaker_name(doc, cue.speaker, options)
        if name:
            payload = f"<v {escape(name)}>{payload}"
        timing = f"{_timestamp(cue.start)} --> {_timestamp(cue.end)}"
        blocks.append(f"{cue.index}\n{timing}\n{payload}\n\n")
    return common.encode("".join(blocks), options)
