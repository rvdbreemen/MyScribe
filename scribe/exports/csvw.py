"""CSV: one row per subtitle cue, for a spreadsheet.

Columns, in order: ``index``, ``start``, ``end``, ``duration`` (seconds, to
the millisecond), ``speaker`` (the display name; empty without one or with
names off), ``text`` (the cue's lines joined by a space), ``chars`` (what is
on screen), ``cps`` (reading speed), and with ``include_confidence`` a
``mean_confidence``: the mean word probability of the cue's words, empty
when none carries one. The rows are `cues.build`'s cues - computed, not
stored (ADR-003) - so a change of constraints changes the rows.

Two things are for Excel. The encoding is ``utf-8-sig``, a BOM in front,
which is how Excel knows the file is UTF-8 and not the local code page -
whether the options say ``utf-8`` or ``utf-8-sig``; ``cp1252`` is honoured as
it is, that being the code page Excel would have assumed. And the quoting is
RFC 4180 through `csv.writer`: a comma or a quote in the text quotes the
field, a quote inside is doubled. Lines are written with ``\\n`` and
converted last, like every text format here, rather than the ``\\r\\n`` the
csv module would write on its own.

A cue does not carry its words, so ``mean_confidence`` needs the partition
back, and `words_by_cue` recovers it by counting characters. The engine
keeps every non-blank word in order, and a cue's lines are its words' text
with only the whitespace rearranged, so the non-blank characters in a cue
are exactly those of its words; walking the words until the counts match
gives the words the cue was built from, in any script, spaced or not.
"""

from __future__ import annotations

import csv
import io
from typing import Sequence

from scribe.exports import common, cues
from scribe.exports.cues import Cue
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions

COLUMNS: tuple[str, ...] = ("index", "start", "end", "duration", "speaker", "text", "chars", "cps")
CONFIDENCE_COLUMN = "mean_confidence"


def write(doc: TranscriptDoc, options: ExportOptions) -> bytes:
    """The document's cues as CSV rows."""
    built = cues.build(doc, options)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS + ((CONFIDENCE_COLUMN,) if options.include_confidence else ()))
    groups = words_by_cue(built.cues, doc.words) if options.include_confidence else None
    for n, cue in enumerate(built.cues):
        row: list[object] = [
            cue.index,
            f"{cue.start:.3f}",
            f"{cue.end:.3f}",
            f"{cue.duration:.3f}",
            common.speaker_name(doc, cue.speaker, options),
            " ".join(cue.lines),
            cue.chars,
            f"{cue.cps:.2f}",
        ]
        if groups is not None:
            row.append(mean_confidence(groups[n]))
        writer.writerow(row)
    encoding = "cp1252" if options.encoding == "cp1252" else "utf-8-sig"
    return common.encode(buffer.getvalue(), options, encoding=encoding)


def mean_confidence(words: Sequence[dict]) -> str:
    """The mean probability of ``words`` to three decimals; ``""`` when none has one."""
    values = [float(w["probability"]) for w in words if w.get("probability") is not None]
    return f"{sum(values) / len(values):.3f}" if values else ""


def _ink(text: object) -> int:
    """The non-blank characters in ``text``."""
    return len("".join(str(text).split()))


def words_by_cue(cue_list: Sequence[Cue], words: Sequence[dict]) -> list[list[dict]]:
    """The words behind each cue, in order. See the module docstring for why
    counting characters recovers the engine's partition exactly."""
    spoken = [w for w in words if str(w.get("text") or "").strip()]
    out: list[list[dict]] = []
    position = 0
    for cue in cue_list:
        wanted = sum(_ink(line) for line in cue.lines)
        got = 0
        group: list[dict] = []
        while got < wanted and position < len(spoken):
            word = spoken[position]
            group.append(word)
            got += _ink(word["text"])
            position += 1
        if got != wanted:
            raise ValueError(
                f"cue {cue.index} does not line up with the words ({got} of {wanted} characters)"
            )
        out.append(group)
    return out
