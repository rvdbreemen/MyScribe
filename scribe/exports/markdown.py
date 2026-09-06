"""Markdown: a title, a heading per speaker paragraph, timestamps as links.

::

    ---
    title: "Clip"
    date: 2026-09-02
    ...
    ---

    # Clip

    ## Arthur

    [0:00](#t=0) Don't panic, the towel is still the most important item. ...

The body is `render.paragraphs`: a ``##`` heading with the speaker's name
above each paragraph (none without diarization, or with names off), then the
paragraph's text with a timestamp wherever ``options.timestamps`` puts one
(`common.stamped`), each a link ``[m:ss](#t=12.3)`` whose fragment is the
start in seconds - the deep link the app's own transcript view uses, so a
tool that knows the recording can seek to it, and a viewer that does not
shows a harmless anchor. The link text follows ``timestamp_format``.

The front matter, on request (``options.front_matter``; the ``obsidian``
preset turns it on): ``title``, ``date`` (the day the recording was added,
local time - the same day `options.filename_for` puts in a name),
``duration`` as ``m:ss``, ``model``, ``language`` and ``speakers``, the
names in order of first appearance. Strings are written as JSON strings,
which YAML reads as double-quoted scalars, so a title with a colon, a name
with a quote or a value that looks like a number all survive; ``date`` is
left bare, as a date.

The transcript's text goes in unescaped. Escaping Markdown's own characters
in speech (an underscore, an asterisk) would deface more transcripts than it
would fix, so a paragraph that happens to open with a ``#`` or a ``-`` once
both stamps and names are turned off is a known limit, not a bug.
"""

from __future__ import annotations

import json

from scribe import render
from scribe.exports import common
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions


def write(doc: TranscriptDoc, options: ExportOptions) -> bytes:
    """The document as Markdown."""

    def link(seconds: float) -> str:
        label = common.timestamp(seconds, options.timestamp_format)
        return f"[{label}](#t={common.seconds_text(seconds)})"

    paragraphs = render.paragraphs(doc.words, doc.labels)
    blocks: list[str] = []
    if options.front_matter:
        blocks.append(front_matter(doc, options))
    blocks.append(f"# {doc.title}")
    for paragraph, pieces in zip(paragraphs, common.stamped(paragraphs, options)):
        name = common.speaker_name(doc, paragraph.speaker, options)
        if name:
            blocks.append(f"## {name}")
        lead, body = common.block(pieces, link)
        blocks.append(f"{link(lead)} {body}" if lead is not None else body)
    return common.encode("\n\n".join(blocks) + "\n", options)


def front_matter(doc: TranscriptDoc, options: ExportOptions) -> str:
    """The YAML block between the ``---`` fences, without a trailing newline."""
    lines = ["---", f"title: {_yaml(doc.title)}"]
    created = doc.media.get("created_at")
    if created is not None:
        lines.append(f"date: {common.local_date(created)}")
    lines.append(f"duration: {_yaml(render.format_ts(doc.duration, 'clock'))}")
    lines.append(f"model: {_yaml(doc.run.get('model'))}")
    lines.append(f"language: {_yaml(doc.run.get('language'))}")
    names = (
        [render.speaker_display(doc.labels, cluster) for cluster in common.clusters(doc)]
        if options.speakers
        else []
    )
    if names:
        lines.append("speakers:")
        lines.extend(f"  - {_yaml(name)}" for name in names)
    lines.append("---")
    return "\n".join(lines)


def _yaml(value: object) -> str:
    """A scalar YAML will read back as given: a JSON string (or null)."""
    return json.dumps(value, ensure_ascii=False)
