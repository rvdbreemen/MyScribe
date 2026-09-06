"""Word: a title heading, a facts line, one paragraph per speaker paragraph.

::

    Clip                                                    (Heading 1)
    Clip.wav · 0:30 · <model> · en · 2026-09-02
    0:00 Arthur: Don't panic, the towel is still the most important item. ...
    0:10 Speaker 2: Marvin says the improbability drive makes him ...

The body is `render.paragraphs`, one Word paragraph each, built from runs:
the timestamp as a hyperlink run, the speaker's name as a bold run, the
text as a plain one, a space between the parts. Timestamps go wherever
``options.timestamps`` puts them (`common.stamped`) - in front of the
paragraph, or inline before each sentence or word - and read in
``options.timestamp_format``. Each links to ``scribe://media/<id>#t=<s>``,
the app's own deep link to that moment of the recording, so a machine that
has the app can open it there and one that does not sees a harmless link.
Names come from the document's labels at write time (ADR-003).

python-docx has no public API for a hyperlink, so `_add_hyperlink` builds
the ``w:hyperlink`` element by hand: an external relationship from the
document part to the URL, and inside the element a run in the ``Hyperlink``
character style. The default template ships no such style, so one is added
with Word's own blue and underline, and the link looks like a link.

Two things are done so the same words give the same bytes, as they do in
every other writer. The core properties are the document's own - the title,
``MyScribe`` as author, the run's creation time as created and modified -
rather than python-docx's template defaults; and every entry of the zip a
DOCX is gets re-dated to the zip epoch, because python-docx stamps each with
the wall clock as it saves. Word does not read those dates. The text
encoding and line-ending options do not apply here: OOXML is UTF-8 XML in a
zip, whatever the options say.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from typing import Sequence

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.paragraph import Paragraph

from scribe import render
from scribe.exports import common
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions

AUTHOR = "MyScribe"
HEADING_LEVEL = 1
FACTS_SEPARATOR = " · "

# Where a timestamp links: the app's deep link to that moment of the recording.
LINK = "scribe://media/{id}#t={seconds}"

# Word's own hyperlink look: its style name, its blue, underlined.
HYPERLINK_STYLE = "Hyperlink"
HYPERLINK_BASE_STYLE = "Default Paragraph Font"
HYPERLINK_COLOR = RGBColor(0x05, 0x63, 0xC1)

# The date every zip entry gets: the earliest a DOS date can hold.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def write(doc: TranscriptDoc, options: ExportOptions) -> bytes:
    """The document as Word."""
    document = Document()
    _ensure_hyperlink_style(document)
    _set_properties(document, doc)
    document.add_heading(doc.title, level=HEADING_LEVEL)
    document.add_paragraph(FACTS_SEPARATOR.join(common.facts(doc)))
    paragraphs = render.paragraphs(doc.words, doc.labels)
    for paragraph, pieces in zip(paragraphs, common.stamped(paragraphs, options)):
        _write_paragraph(document, doc, options, paragraph, pieces)
    buffer = io.BytesIO()
    document.save(buffer)
    return _redated(buffer.getvalue())


def link_for(doc: TranscriptDoc, seconds: float) -> str:
    """The deep link a timestamp at ``seconds`` points to."""
    return LINK.format(id=doc.media.get("id"), seconds=common.seconds_text(seconds))


# --- the body ----------------------------------------------------------------------


def _write_paragraph(
    document,
    doc: TranscriptDoc,
    options: ExportOptions,
    paragraph: render.Paragraph,
    pieces: Sequence[common.Piece],
) -> None:
    """One Word paragraph: ``[stamp] [Name:] text``, later stamps inline
    before their piece, one space between consecutive parts."""
    parts: list[tuple[str, object]] = []
    if pieces and pieces[0].start is not None:
        parts.append(("stamp", pieces[0].start))
    name = common.speaker_name(doc, paragraph.speaker, options)
    if name:
        parts.append(("name", f"{name}:"))
    for i, piece in enumerate(pieces):
        if i and piece.start is not None:
            parts.append(("stamp", piece.start))
        if piece.text:
            parts.append(("text", piece.text))

    out = document.add_paragraph()
    for i, (kind, value) in enumerate(parts):
        if kind == "stamp":
            if i:
                out.add_run(" ")
            seconds = float(value)  # type: ignore[arg-type]
            _add_hyperlink(out, link_for(doc, seconds), common.timestamp(seconds, options.timestamp_format))
            continue
        run = out.add_run((" " if i else "") + str(value))
        if kind == "name":
            run.bold = True


def _add_hyperlink(paragraph: Paragraph, url: str, text: str) -> None:
    """Append a ``w:hyperlink`` to ``paragraph``: an external relationship
    to ``url`` and one run of ``text`` in the Hyperlink style."""
    r_id = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    style = OxmlElement("w:rStyle")
    style.set(qn("w:val"), HYPERLINK_STYLE)
    properties.append(style)
    run.append(properties)
    node = OxmlElement("w:t")
    node.text = text
    node.set(qn("xml:space"), "preserve")
    run.append(node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


# --- the document's own parts --------------------------------------------------------


def _ensure_hyperlink_style(document) -> None:
    styles = document.styles
    if HYPERLINK_STYLE in styles:
        return
    style = styles.add_style(HYPERLINK_STYLE, WD_STYLE_TYPE.CHARACTER)
    if HYPERLINK_BASE_STYLE in styles:
        style.base_style = styles[HYPERLINK_BASE_STYLE]
    style.font.color.rgb = HYPERLINK_COLOR
    style.font.underline = True


def _set_properties(document, doc: TranscriptDoc) -> None:
    """Title, author and dates from the document, not from the template."""
    props = document.core_properties
    props.title = doc.title
    props.author = AUTHOR
    props.last_modified_by = AUTHOR
    language = doc.run.get("language")
    if language:
        props.language = str(language)
    created = doc.run.get("created_at")
    if created is not None:
        # python-docx writes a datetime as UTC with a trailing Z and takes
        # the fields as given, so it must be handed naive UTC.
        when = datetime.fromtimestamp(float(created), tz=timezone.utc).replace(tzinfo=None)
        props.created = when
        props.modified = when


def _redated(data: bytes) -> bytes:
    """The zip ``data`` with every entry dated ``ZIP_EPOCH``; members, their
    order and their compression kept."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=ZIP_EPOCH)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = info.external_attr
            target.writestr(entry, source.read(info))
    return out.getvalue()
