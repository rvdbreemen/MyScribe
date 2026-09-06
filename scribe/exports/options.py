"""What an export looks like, and what its file is called.

`ExportOptions` is one model for every door an export goes through: the
Advanced Export dialog posts its fields, a saved preset is one serialised as
JSON, the CLI's flags map onto it one to one, and every writer takes it next
to the `TranscriptDoc`. It is built to be fed straight from a form - a
checkbox's "0"/"1" is a bool, a number arrives as text, `formats` may come as
one comma-separated string - and to refuse at the door what a writer could
not honour: a format the registry does not know, a line too short to hold a
word, a filename template naming a field that does not exist.

The options are frozen. `PRESETS` is a shared table and a writer's argument
is a value; neither should be something a caller can quietly change under
somebody else. Derive with `model_copy(update=...)`.

The fields, by the part of the export they shape:

* **which files** - `formats`, a subset of `exports.FORMATS`, in the order
  the user chose them.
* **the text** - `speakers` (names on or off), `timestamps` (how often one
  appears: never, per paragraph, per speaker turn, per cue, per sentence,
  every `interval_seconds`, or per word), `timestamp_format` (`clock` is
  `render.format_ts`'s m:ss, `smpte` hh:mm:ss:ff, `seconds` a plain float),
  `txt_layout` for the plain-text writer, `front_matter` for Markdown, and
  `include_confidence` for the columns CSV and JSON add.
* **the subtitles** - the constraints `cues.build` segments under: `cpl`
  (characters per line), `max_lines`, `max_cps` (reading speed), `min_duration`
  and `max_duration` in seconds, and the `cue_gap` kept between cues.
* **the file** - `encoding`, `crlf` and `filename_template`.

`filename_for` is the one filename rule. The template's fields are `{title}`,
`{date}` (the day the recording was added, in local time - the closest thing
the database holds to the date of the recording), `{model}` and `{lang}`;
then `scrub` makes the result a name Windows accepts, because a file that
cannot be written on the platform this app ships on is not an export.
"""

from __future__ import annotations

import re
import string
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scribe import exports  # the FORMATS registry, read when a value is validated
from scribe.exports.doc import TranscriptDoc, local_date

# What a filename template may name. Anything else is refused when the
# options are validated, so `filename_for` can never meet a KeyError.
TEMPLATE_FIELDS: tuple[str, ...] = ("title", "date", "model", "lang")

# Win32's forbidden characters, plus the C0 controls and DEL. Each becomes
# one underscore, so `a:b` stays two syllables apart rather than fusing.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')

# The device names Win32 reserves whatever the extension: `CON.srt` is still
# the console. Case-insensitive, and the check is on the part before the
# first dot, which is how the Win32 namespace reads them.
RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# NTFS takes 255 characters per component; this leaves room for the
# extension, a `.part` suffix while writing, and a counter if one is ever
# needed to avoid a clash.
MAX_STEM = 200

# A title that scrubs to nothing (all dots, all punctuation) still needs a
# name, and the media id is the one thing guaranteed unique and printable.
FALLBACK_STEM = "media-{id}"


class ExportOptions(BaseModel):
    """Every option an export takes. See the module docstring for the groups."""

    model_config = ConfigDict(frozen=True)

    formats: list[str] = ["srt"]
    speakers: bool = True
    timestamps: Literal["none", "paragraph", "turn", "cue", "sentence", "interval", "word"] = "paragraph"
    timestamp_format: Literal["clock", "smpte", "seconds"] = "clock"
    interval_seconds: int = Field(default=60, ge=1)
    txt_layout: Literal["cue", "paragraph", "monologue", "turn"] = "paragraph"
    cpl: int = Field(default=42, ge=10)
    max_lines: int = Field(default=2, ge=1)
    max_cps: float = Field(default=20.0, gt=0)
    min_duration: float = Field(default=1.0, ge=0)
    max_duration: float = Field(default=7.0, gt=0)
    cue_gap: float = Field(default=0.08, ge=0)
    encoding: Literal["utf-8", "utf-8-sig", "cp1252"] = "utf-8"
    crlf: bool = False
    filename_template: str = "{title}"
    include_confidence: bool = False
    front_matter: bool = False

    @field_validator("formats", mode="before")
    @classmethod
    def _split_a_string(cls, value: Any) -> Any:
        """One string is a comma- or whitespace-separated list: what a CLI
        flag or a single form field carries."""
        if isinstance(value, str):
            return [part for part in re.split(r"[,\s]+", value) if part]
        return value

    @field_validator("formats")
    @classmethod
    def _known_formats(cls, value: list[str]) -> list[str]:
        names = list(dict.fromkeys(name.strip().lower() for name in value if name.strip()))
        if not names:
            raise ValueError("select at least one format")
        unknown = [name for name in names if name not in exports.FORMATS]
        if unknown:
            raise ValueError(
                f"unknown format {', '.join(repr(name) for name in unknown)};"
                f" one of {', '.join(exports.FORMATS)}"
            )
        return names

    @field_validator("filename_template")
    @classmethod
    def _known_template_fields(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("filename_template cannot be empty")
        try:
            fields = [name for _, name, _, _ in string.Formatter().parse(value) if name is not None]
        except ValueError as exc:
            raise ValueError(f"filename_template is not a valid template: {exc}")
        unknown = [name for name in fields if name not in TEMPLATE_FIELDS]
        if unknown:
            raise ValueError(
                f"unknown filename template field {', '.join(repr(name) for name in unknown)};"
                f" one of {', '.join('{' + f + '}' for f in TEMPLATE_FIELDS)}"
            )
        return value

    @model_validator(mode="after")
    def _durations_in_order(self) -> "ExportOptions":
        if self.max_duration < self.min_duration:
            raise ValueError("max_duration cannot be below min_duration")
        return self


# The built-in presets, in the order the dialog offers them. The subtitle
# three carry the constraints their house styles publish; the other two are
# text shapes people keep asking for by name.
PRESETS: dict[str, ExportOptions] = {
    "netflix": ExportOptions(
        formats=["srt"], cpl=42, max_lines=2, max_cps=20.0, min_duration=0.83, max_duration=7.0
    ),
    "bbc": ExportOptions(formats=["srt"], cpl=37, max_cps=16.0),
    "youtube": ExportOptions(formats=["srt"], cpl=32, max_lines=2, max_duration=5.0),
    "podcast": ExportOptions(
        formats=["txt"], txt_layout="paragraph", timestamps="turn", speakers=True
    ),
    "obsidian": ExportOptions(
        formats=["md"], timestamps="sentence", timestamp_format="clock", front_matter=True
    ),
}


def scrub(name: str) -> str:
    """``name`` as a Win32 filename component: forbidden characters and
    controls to ``_``, trailing dots and spaces gone, a reserved device name
    suffixed, and no longer than ``MAX_STEM``. May come back empty."""
    cleaned = _FORBIDDEN.sub("_", name).rstrip(". ")
    head, dot, tail = cleaned.partition(".")
    if head.upper() in RESERVED_NAMES:
        cleaned = f"{head}_{dot}{tail}"
    return cleaned[:MAX_STEM].rstrip(". ")


def filename_for(doc: TranscriptDoc, options: ExportOptions, ext: str) -> str:
    """The file's name for ``doc`` in format ``ext``, from the options' template.

    ``ext`` is taken with or without its dot. The template was validated with
    the options, so every field it names is one of ``TEMPLATE_FIELDS``. A
    name past ``MAX_STEM`` is cut at the title, not at the end: what the
    template adds after it - the `` (2)`` a bulk export numbers a clashing
    title with, a ``-{lang}`` - is the part that has to survive.
    """
    values = {
        "title": doc.title,
        "date": local_date(doc.media["created_at"]),
        "model": doc.run["model"],
        "lang": doc.run.get("language") or "",
    }
    full = options.filename_template.format(**values)
    over = len(full) - MAX_STEM
    if over > 0 and values["title"]:
        values["title"] = values["title"][: max(0, len(values["title"]) - over)]
        full = options.filename_template.format(**values)
    stem = scrub(full)
    if not stem:
        stem = FALLBACK_STEM.format(id=doc.media["id"])
    return f"{stem}.{ext.lstrip('.')}"
