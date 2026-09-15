"""The self-contained HTML bundle: the transcript, its player and its audio
in one file. This is the Share artifact.

`write` renders ``scribe/templates/export_bundle.html``: the styling inline,
the transcript as `render.paragraphs` through the same macro the app's
panel uses (``_transcript_macros.html`` - speaker headings, a ``data-start``
timestamp per sentence, a span per word), an ``<audio>`` element, and a
small inline script for click-to-seek and follow-along highlighting.
Nothing in the file points at the network - no stylesheet, no htmx, no
fetch - so it opens the same from a mail attachment, a USB stick or a
shared folder, offline.

The audio goes one of two ways, and the caller chooses by what it passes:

* ``audio`` given - the bytes are embedded as a ``data:`` URL of
  ``audio_mime``. One file, nothing to keep next to it. The route caps this
  at 25 MB, above which a browser gets slow to open the page.
* ``audio`` None - the ``src`` is a relative name, `sidecar_name`: the same
  stem `options.filename_for` gives the HTML file, with the extension that
  fits ``audio_mime``. The route writes the audio under that name next to
  the HTML; a reader who moves both together keeps the player working.

Two options are read differently here from the text writers, and on
purpose. ``timestamps == "none"`` hides the stamps (``body.hide-ts``) rather
than dropping them: the ``a.ts`` elements are what a click seeks by and what
a ``#t=`` deep link lands on, so they stay in the markup. And the encoding
is always UTF-8 without a BOM, whatever ``options.encoding`` says: the file
declares ``<meta charset="utf-8">`` and has to be what it declares. The
line ending follows the options.

The exporter has a Jinja environment of its own rather than `scribe.web`'s,
so nothing here imports the web layer (the CLI would drag FastAPI in
otherwise). Autoescape is on; every word, name and title is user content.
Pure over the document (ADR-003): the template is the only file read, and
it is the package's own.
"""

from __future__ import annotations

import base64
import re
import urllib.parse
from pathlib import Path

import jinja2

from scribe import render
from scribe.exports import common
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions, filename_for

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE = "export_bundle.html"

DEFAULT_AUDIO_MIME = "audio/mp4"

# The file extension a sidecar gets for its media type: the containers the
# app serves (`scribe.playback.PLAYABLE`, inverted) and the proxy's
# own. Anything else takes its subtype as the extension.
AUDIO_EXTENSIONS: dict[str, str] = {
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/flac": "flac",
    "audio/webm": "webm",
    "video/webm": "webm",
    "video/mp4": "mp4",
}
FALLBACK_EXTENSION = "bin"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["clock"] = render.format_ts
_env.filters["band"] = render.confidence_band


def write(
    doc: TranscriptDoc,
    options: ExportOptions,
    *,
    audio: bytes | None = None,
    audio_mime: str = DEFAULT_AUDIO_MIME,
) -> bytes:
    """The document as one HTML file, with ``audio`` embedded when given."""
    if audio is not None:
        src = f"data:{audio_mime};base64,{base64.b64encode(audio).decode('ascii')}"
    else:
        src = urllib.parse.quote(sidecar_name(doc, options, audio_mime))
    html = _env.get_template(TEMPLATE).render(
        title=doc.title,
        facts=common.facts(doc),
        language=doc.run.get("language") or "",
        paragraphs=render.paragraphs(doc.words, doc.labels),
        speakers=options.speakers,
        hide_ts=options.timestamps == "none",
        audio_src=src,
        duration=doc.duration,
    )
    if not html.endswith("\n"):
        html += "\n"
    return common.encode(html, options, encoding="utf-8")


def sidecar_name(doc: TranscriptDoc, options: ExportOptions, audio_mime: str = DEFAULT_AUDIO_MIME) -> str:
    """The name of the audio file a bundle without embedded audio expects
    next to it: the HTML file's own stem, with ``audio_mime``'s extension."""
    return filename_for(doc, options, audio_extension(audio_mime))


def audio_extension(mime: str) -> str:
    """The extension for a media type: from the table, else the subtype
    reduced to letters and digits (``audio/x-foo`` gives ``foo``)."""
    key = mime.partition(";")[0].strip().lower()
    if key in AUDIO_EXTENSIONS:
        return AUDIO_EXTENSIONS[key]
    subtype = key.partition("/")[2]
    if subtype.startswith("x-"):
        subtype = subtype[2:]
    return re.sub(r"[^a-z0-9]", "", subtype) or FALLBACK_EXTENSION
