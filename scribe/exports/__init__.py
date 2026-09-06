"""The exporters: pure functions from a transcript document to bytes.

`doc.load` is the one reader - it turns a media's current run into a frozen
`TranscriptDoc` - and every writer is `write(doc, options) -> bytes` over that
document and an `ExportOptions`. No writer opens the database, touches the
filesystem or sees a request (ADR-003: words are canonical, and a cue, a
paragraph or a file is something computed from them, never something stored).

`FORMATS` is the registry the dialog, the routes and the CLI all read: the
format's name maps to its writer, its media type and its file extension, in
the order the dialog lists them. The names are fixed here so the option
model can validate against them before any writer exists; each writer
replaces its `NotImplemented` placeholder below, once the table is built.

The registry is defined before anything else in this package is imported, and
that order matters: `options` validates a format against `FORMATS` and reads
it from this module, so a writer that imports `options` (they all will) must
find the table already filled.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class Format(NamedTuple):
    """One registry entry: how to write the format, and what to call the file."""

    writer: Any  # write(doc, options) -> bytes; NotImplemented until its task lands
    mime: str
    extension: str  # without the dot


FORMATS: dict[str, Format] = {
    "srt": Format(NotImplemented, "application/x-subrip", "srt"),
    "vtt": Format(NotImplemented, "text/vtt", "vtt"),
    "txt": Format(NotImplemented, "text/plain", "txt"),
    "csv": Format(NotImplemented, "text/csv", "csv"),
    "json": Format(NotImplemented, "application/json", "json"),
    "md": Format(NotImplemented, "text/markdown", "md"),
    "docx": Format(
        NotImplemented,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "html": Format(NotImplemented, "text/html", "html"),
}

# The writers, imported once the registry above exists: each imports
# `options`, which reads FORMATS from this module (see the module docstring).
from scribe.exports import csvw, docx, html_bundle, jsonw, markdown, srt, txt, vtt  # noqa: E402

FORMATS["srt"] = FORMATS["srt"]._replace(writer=srt.write)
FORMATS["vtt"] = FORMATS["vtt"]._replace(writer=vtt.write)
FORMATS["txt"] = FORMATS["txt"]._replace(writer=txt.write)
FORMATS["csv"] = FORMATS["csv"]._replace(writer=csvw.write)
FORMATS["json"] = FORMATS["json"]._replace(writer=jsonw.write)
FORMATS["md"] = FORMATS["md"]._replace(writer=markdown.write)
FORMATS["docx"] = FORMATS["docx"]._replace(writer=docx.write)
FORMATS["html"] = FORMATS["html"]._replace(writer=html_bundle.write)
