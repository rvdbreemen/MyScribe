"""Phase 4 Task 1: the export option model, the presets and the filename rule.

`ExportOptions` is what the dialog posts, what a preset stores and what the
CLI's flags become; every writer takes one. These tests pin the defaults the
plan names, the refusals (a format the registry does not know, a CPL no
subtitle could live with, a template field that does not exist), the five
built-in presets, and the filename rule: template fields in, a name Windows
will accept out. The `FORMATS` registry is checked for its shape - eight
names, in order - because the dialog, the CLI and the writers' tasks all
build on it.

Nothing here touches a database: `filename_for` is a pure function of a
`TranscriptDoc`, so the tests build one by hand.
"""

import calendar

import pytest
from pydantic import ValidationError

from scribe import exports
from scribe.exports import options
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import PRESETS, ExportOptions, filename_for

# Noon UTC on 2026-09-02: the same calendar day in every zone from UTC-12 to
# UTC+11, which is where this test suite runs. `{date}` renders local time.
NOON = float(calendar.timegm((2026, 9, 2, 12, 0, 0)))


def _doc(title="Clip", *, media_id=7, created_at=NOON, model="large-v3-turbo", language="en"):
    return TranscriptDoc(
        media={"id": media_id, "title": title, "created_at": created_at},
        run={"id": 1, "media_id": media_id, "model": model, "language": language},
        words=(),
        segments=(),
        labels={},
        duration=0.0,
        title=title,
    )


# --- the registry ---------------------------------------------------------------


def test_the_registry_names_the_eight_formats_in_order():
    assert list(exports.FORMATS) == ["srt", "vtt", "txt", "csv", "json", "md", "docx", "html"]
    for name, entry in exports.FORMATS.items():
        writer, mime, extension = entry  # a (writer, mime, extension) triple
        assert callable(writer), f"{name}: every writer has landed (Tasks 3 and 4)"
        assert entry.mime == mime and "/" in mime
        assert entry.extension == extension == name


# --- the model ------------------------------------------------------------------


def test_the_defaults_are_the_plans():
    o = ExportOptions()

    assert o.formats == ["srt"]
    assert o.speakers is True
    assert o.timestamps == "paragraph"
    assert o.timestamp_format == "clock"
    assert o.interval_seconds == 60
    assert o.txt_layout == "paragraph"
    assert o.cpl == 42
    assert o.max_lines == 2
    assert o.max_cps == 20.0
    assert o.min_duration == 1.0
    assert o.max_duration == 7.0
    assert o.cue_gap == 0.08
    assert o.encoding == "utf-8"
    assert o.crlf is False
    assert o.filename_template == "{title}"
    assert o.include_confidence is False
    assert o.front_matter is False


def test_an_unknown_format_is_refused():
    with pytest.raises(ValidationError) as excinfo:
        ExportOptions(formats=["srt", "pdf"])

    assert "pdf" in str(excinfo.value)


def test_formats_accept_a_comma_separated_string_and_drop_repeats():
    assert ExportOptions(formats="srt, vtt, srt").formats == ["srt", "vtt"]
    assert ExportOptions(formats=["VTT", "md", "md"]).formats == ["vtt", "md"]


def test_no_format_at_all_is_refused():
    with pytest.raises(ValidationError):
        ExportOptions(formats=[])
    with pytest.raises(ValidationError):
        ExportOptions(formats="")


def test_a_cpl_under_ten_is_refused():
    with pytest.raises(ValidationError):
        ExportOptions(cpl=9)

    assert ExportOptions(cpl=10).cpl == 10


@pytest.mark.parametrize(
    "bad",
    [
        {"max_lines": 0},
        {"max_cps": 0},
        {"min_duration": -0.1},
        {"max_duration": 0},
        {"cue_gap": -1},
        {"interval_seconds": 0},
        {"min_duration": 3.0, "max_duration": 2.0},
        {"timestamps": "always"},
        {"timestamp_format": "hex"},
        {"txt_layout": "poem"},
        {"encoding": "utf-16"},
    ],
)
def test_out_of_range_values_are_refused(bad):
    with pytest.raises(ValidationError):
        ExportOptions(**bad)


def test_form_values_arrive_as_text():
    o = ExportOptions(speakers="0", cpl="37", max_cps="16", crlf="on", interval_seconds="30")

    assert o.speakers is False
    assert o.cpl == 37
    assert o.max_cps == 16.0
    assert o.crlf is True
    assert o.interval_seconds == 30


def test_options_are_frozen():
    o = ExportOptions()

    with pytest.raises(ValidationError):
        o.cpl = 5


# --- the presets ----------------------------------------------------------------


def test_the_five_built_in_presets_validate_and_round_trip():
    assert list(PRESETS) == ["netflix", "bbc", "youtube", "podcast", "obsidian"]
    for name, preset in PRESETS.items():
        assert isinstance(preset, ExportOptions), name
        assert ExportOptions.model_validate(preset.model_dump()) == preset, name


def test_netflix_is_the_plans_numbers():
    p = PRESETS["netflix"]
    assert (p.cpl, p.max_lines, p.max_cps, p.min_duration, p.max_duration) == (42, 2, 20.0, 0.83, 7.0)
    assert p.formats == ["srt"]


def test_bbc_is_the_plans_numbers():
    p = PRESETS["bbc"]
    assert (p.cpl, p.max_cps) == (37, 16.0)


def test_youtube_is_the_plans_numbers():
    p = PRESETS["youtube"]
    assert (p.cpl, p.max_lines, p.max_duration) == (32, 2, 5.0)


def test_podcast_is_a_text_transcript_by_turn():
    p = PRESETS["podcast"]
    assert p.formats == ["txt"]
    assert p.txt_layout == "paragraph"
    assert p.timestamps == "turn"
    assert p.speakers is True


def test_obsidian_is_markdown_with_front_matter():
    p = PRESETS["obsidian"]
    assert p.formats == ["md"]
    assert p.timestamps == "sentence"
    assert p.timestamp_format == "clock"
    assert p.front_matter is True


# --- filenames ------------------------------------------------------------------


def test_scrub_replaces_what_win32_forbids():
    assert options.scrub("a:b/c?.txt") == "a_b_c_.txt"
    assert options.scrub('<>:"/\\|?*') == "_" * 9
    assert options.scrub("tab\there\x00and\x7f") == "tab_here_and_"


def test_filename_for_scrubs_the_title():
    assert filename_for(_doc("a:b/c?"), ExportOptions(), "txt") == "a_b_c_.txt"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("CON", "CON_.srt"),
        ("nul", "nul_.srt"),
        ("com1", "com1_.srt"),
        ("Lpt9.old", "Lpt9_.old.srt"),
        ("console", "console.srt"),  # only the exact names are reserved
    ],
)
def test_reserved_device_names_are_suffixed(title, expected):
    assert filename_for(_doc(title), ExportOptions(), "srt") == expected


def test_trailing_dots_and_spaces_are_stripped():
    assert filename_for(_doc("Notes... "), ExportOptions(), "srt") == "Notes.srt"


def test_template_fields_fill_in():
    o = ExportOptions(filename_template="{title}-{date}-{model}-{lang}")

    assert filename_for(_doc(), o, "srt") == "Clip-2026-09-02-large-v3-turbo-en.srt"


def test_a_run_without_a_language_renders_lang_empty():
    o = ExportOptions(filename_template="{title}_{lang}")

    assert filename_for(_doc(language=None), o, "vtt") == "Clip_.vtt"


def test_an_unknown_template_field_is_refused_up_front():
    with pytest.raises(ValidationError) as excinfo:
        ExportOptions(filename_template="{titel}")
    assert "titel" in str(excinfo.value)

    with pytest.raises(ValidationError):
        ExportOptions(filename_template="{")  # not even a template

    with pytest.raises(ValidationError):
        ExportOptions(filename_template="   ")


def test_a_name_that_scrubs_to_nothing_falls_back_to_the_media_id():
    assert filename_for(_doc("...", media_id=7), ExportOptions(), "srt") == "media-7.srt"


def test_the_extension_may_come_with_or_without_its_dot():
    assert filename_for(_doc(), ExportOptions(), ".srt") == "Clip.srt"
    assert filename_for(_doc(), ExportOptions(), "srt") == "Clip.srt"


def test_a_very_long_title_is_cut_to_what_a_filesystem_takes():
    name = filename_for(_doc("a" * 300), ExportOptions(), "srt")

    assert name == "a" * options.MAX_STEM + ".srt"


def test_the_cut_falls_on_the_title_not_on_what_the_template_adds():
    """A bulk export tells two media of one title apart by suffixing the
    template ` (2)`; with a title at the limit that suffix must survive the
    cut, or the two files are still one name."""
    numbered = ExportOptions(filename_template="{title} (2)")

    name = filename_for(_doc("a" * 300), numbered, "srt")

    assert name == "a" * (options.MAX_STEM - len(" (2)")) + " (2).srt"
    assert filename_for(_doc("a" * 300), ExportOptions(filename_template="{title}-{lang}"), "srt").endswith("-en.srt")
    # A short title is not touched by the rule.
    assert filename_for(_doc("Clip"), numbered, "srt") == "Clip (2).srt"
