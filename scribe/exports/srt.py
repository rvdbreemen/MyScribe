"""SubRip: numbered cues, ``hh:mm:ss,mmm`` timings, the speaker as a prefix.

The cues come from `cues.build` under the options' constraints - nothing is
stored, and a preset change is a re-run of the engine (ADR-003). Each is
written as the format expects::

    1
    00:00:00,000 --> 00:00:04,900
    Arthur: Don't panic, the towel is
    still the most important item.

The blank line that closes a block is also what separates it from the next,
so the file ends with one. SubRip has no notion of a speaker; the name goes
on the first line as ``NAME: `` when ``options.speakers`` is on, the
convention every player shows as intended. That prefix is the writer's, not
the engine's: the cue's lines fit ``cpl`` on their own, and a long name can
take the first line past it. So the writer owns that width too: `report`
is the engine's report plus a ``cpl`` violation for each first line the
prefix takes over the limit, and it is what the preview shows for SRT -
the report of the file as written, not of the cues before the names went
on. A run without diarization carries no names, so nothing is prefixed.

Timestamps are `render.format_ts`'s SRT style, rounded to the millisecond.
The text is built with ``\\n`` and the line ending applied last (`common.encode`).
"""

from __future__ import annotations

from scribe import render
from scribe.exports import common, cues
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions

# "no previous cue", distinct from a previous cue whose speaker is None - which
# is what every cue looks like in a transcript that was never diarized.
_UNSET: object = object()


def first_line(
    doc: TranscriptDoc,
    cue: cues.Cue,
    options: ExportOptions,
    previous_speaker: str | None = _UNSET,
) -> str:
    """The cue's first line as written.

    The speaker's ``NAME: `` goes in front only when this cue's speaker
    differs from the one before it: a name repeated on every cue of the same
    run says nothing new and spends characters that the line needs. At cpl 42
    a ``Speaker 1: `` prefix is a quarter of the line, and paying it on every
    cue is what forces breaks in the middle of a phrase.

    ``previous_speaker`` defaults to a sentinel rather than ``None``, because
    ``None`` is a real value here - the speaker of a cue in a transcript with
    no diarization - and a caller that omits the argument (the report, asking
    about one cue on its own) means "assume this cue opens a run", not
    "assume the speaker before it was nobody".
    """
    name = common.speaker_name(doc, cue.speaker, options)
    head = cue.lines[0] if cue.lines else ""
    if not name or (previous_speaker is not _UNSET and cue.speaker == previous_speaker):
        return head
    return f"{name}: {head}"


def write(doc: TranscriptDoc, options: ExportOptions) -> bytes:
    """The document as SubRip subtitles."""
    blocks: list[str] = []
    previous: object = _UNSET
    for cue in cues.build(doc, options).cues:
        lines = [first_line(doc, cue, options, previous), *cue.lines[1:]]
        previous = cue.speaker
        timing = f"{render.format_ts(cue.start, 'srt')} --> {render.format_ts(cue.end, 'srt')}"
        blocks.append(f"{cue.index}\n{timing}\n" + "\n".join(lines) + "\n\n")
    return common.encode("".join(blocks), options)


def report(doc: TranscriptDoc, built: cues.CueSet, options: ExportOptions) -> cues.Report:
    """The compliance report of the file `write` makes from ``built``: the
    engine's violations, plus a ``cpl`` one for each first line that fits
    on its own but not with its ``NAME: `` (a line the engine already
    reports - one word wider than cpl - is not counted twice). In cue
    order, the engine's ahead of the writer's for one cue; the value is
    the written line's width."""
    added: dict[int, cues.Violation] = {}
    previous: object = _UNSET
    for cue in built.cues:
        width = len(first_line(doc, cue, options, previous))
        previous = cue.speaker
        if cue.lines and len(cue.lines[0]) <= options.cpl < width:
            added[cue.index] = cues.Violation(cue.index, "cpl", width, options.cpl)
    if not added:
        return built.report
    by_cue: dict[int, list[cues.Violation]] = {}
    for violation in built.report.violations:
        by_cue.setdefault(violation.cue_index, []).append(violation)
    merged: list[cues.Violation] = []
    for cue in built.cues:
        merged.extend(by_cue.get(cue.index, []))
        if cue.index in added:
            merged.append(added[cue.index])
    return cues.Report(merged)
