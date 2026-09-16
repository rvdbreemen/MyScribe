"""Phase 4 Task 3: the text writers - SRT, VTT, TXT, CSV, JSON and Markdown.

Six pure functions `write(doc, options) -> bytes` over a `TranscriptDoc`
(ADR-003: cues and paragraphs are computed from the words on every call and
nothing grouped is stored). These tests build the document by hand from the
seed transcript - `tests/seed.py`'s forty deterministic words, one named
speaker and one left to its default, a fixed creation time - so every byte a
writer produces is a function of things pinned in this file. No database, no
GPU.

Each writer has one golden file under `tests/golden/exports/`, produced from
that document with default options and compared byte for byte; the targeted
tests around them check the things a golden cannot explain: the separator an
SRT timestamp uses, what a voice tag looks like, what a layout leaves out,
which encodings carry a BOM. To regenerate the goldens after a deliberate
change, run the suite once with `SCRIBE_UPDATE_GOLDENS=1`: it rewrites the
goldens that moved and is red for each of them, with the diff; run it again
without the variable to see them match, and commit the result with the
reason in the message. A green run always means the goldens matched.
"""

from __future__ import annotations

import calendar
import copy
import csv
import difflib
import io
import json
import os
import sys
from pathlib import Path

import pytest

from scribe import exports, render
from scribe.exports import common, csvw, cues, jsonw, markdown, srt, txt, vtt
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import PRESETS, ExportOptions
from seed import _segments_for, default_words

GOLDEN_DIR = Path(__file__).parent / "golden" / "exports"
UPDATE_GOLDENS = os.environ.get("SCRIBE_UPDATE_GOLDENS") == "1"

# Noon UTC on 2026-09-02: the same calendar day in every zone from UTC-12 to
# UTC+11, so the local-time `date` in the Markdown front matter is stable.
NOON = float(calendar.timegm((2026, 9, 2, 12, 0, 0)))
SHA256 = "5f" * 32

WRITERS = {
    "srt": srt.write,
    "vtt": vtt.write,
    "txt": txt.write,
    "csv": csvw.write,
    "json": jsonw.write,
    "md": markdown.write,
}


def words_of(text, *, speaker=None, start=0.0, step=0.5, length=0.4, idx0=0):
    """Words for ``text``, one per whitespace token, timed ``step`` apart,
    each keeping faster-whisper's leading space."""
    out = []
    for i, token in enumerate(text.split()):
        begin = start + i * step
        out.append(
            {
                "idx": idx0 + i,
                "start": begin,
                "end": begin + length,
                "text": " " + token,
                "probability": 0.9,
                "speaker": speaker,
            }
        )
    return out


def doc_of(words, *, labels=None, segments=None, title="Clip", duration=30.0) -> TranscriptDoc:
    """A ``TranscriptDoc`` over ``words`` with the rows the loader would carry."""
    words = list(words)
    if segments is None:
        segments = _segments_for(words, 10) if words else []
    return TranscriptDoc(
        media={
            "id": 1,
            "sha256": SHA256,
            "store_path": f"media/5f/{SHA256}.wav",
            "orig_name": f"{title}.wav",
            "title": title,
            "folder_id": None,
            "duration": duration,
            "size_bytes": 4242,
            "created_at": NOON,
            "trashed_at": None,
        },
        run={
            "id": 1,
            "media_id": 1,
            "engine": "faster-whisper",
            "model": "large-v3-turbo",
            "compute_type": "float16",
            "language": "en",
            "task": "transcribe",
            "params_json": "{}",
            "xrt": 14.5,
            "is_current": 1,
            "created_at": NOON,
        },
        words=tuple(words),
        segments=tuple(segments),
        labels=dict(labels or {}),
        duration=float(duration),
        title=title,
    )


def seed_doc() -> TranscriptDoc:
    """The seed transcript: two speakers, one named, one on its default."""
    return doc_of(default_words(), labels={"SPEAKER_00": "Arthur"})


def text_of(data: bytes, encoding: str = "utf-8") -> str:
    return data.decode(encoding)


def _golden_diff(name: str, expected: bytes, data: bytes) -> str:
    return "\n".join(
        difflib.unified_diff(
            expected.decode("utf-8", "replace").splitlines(),
            data.decode("utf-8", "replace").splitlines(),
            fromfile=f"golden/{name}",
            tofile="written",
            lineterm="",
        )
    )


def check_golden(name: str, data: bytes) -> None:
    """``data`` is what the golden says, or the test fails with the diff.

    Under SCRIBE_UPDATE_GOLDENS=1 a golden that moved is rewritten *and* the
    test fails, showing the diff: an update run is red exactly where the
    goldens changed, and the plain run afterwards is the proof that they now
    match. Writing first and comparing after made an update run green
    whatever the writer produced, so a developer with the variable exported
    in their shell had a suite that blessed every change silently (TASK-081).
    """
    path = GOLDEN_DIR / name
    expected = path.read_bytes() if path.is_file() else None
    if UPDATE_GOLDENS and data != expected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        pytest.fail(
            f"{name} rewritten under SCRIBE_UPDATE_GOLDENS=1:\n"
            f"{_golden_diff(name, expected or b'', data)}\n"
            "Read the diff, then run again without the variable to see it match."
        )
    assert expected is not None, f"no golden at {path}; run with SCRIBE_UPDATE_GOLDENS=1 to write it"
    if data != expected:
        pytest.fail(f"{name} differs from its golden:\n{_golden_diff(name, expected, data)}")


# --- the golden check itself -----------------------------------------------------


def test_an_update_run_is_red_where_a_golden_moved(tmp_path, monkeypatch):
    """TASK-081: with SCRIBE_UPDATE_GOLDENS=1 the golden was written first and
    compared after, so an update run was green whatever the writer produced -
    and a developer with the variable exported in their shell had a suite
    that blessed every change silently. A golden that moves is still written,
    and the test fails with the diff; the plain run afterwards is the proof."""
    me = sys.modules[__name__]
    monkeypatch.setattr(me, "GOLDEN_DIR", tmp_path)
    monkeypatch.setattr(me, "UPDATE_GOLDENS", True)
    (tmp_path / "x.txt").write_bytes(b"old\n")

    with pytest.raises(pytest.fail.Exception) as moved:
        check_golden("x.txt", b"new\n")

    assert (tmp_path / "x.txt").read_bytes() == b"new\n"
    assert "-old" in str(moved.value) and "+new" in str(moved.value)
    check_golden("x.txt", b"new\n")  # written; the next run matches and passes


def test_a_plain_run_fails_a_mismatch_with_the_diff_and_writes_nothing(tmp_path, monkeypatch):
    me = sys.modules[__name__]
    monkeypatch.setattr(me, "GOLDEN_DIR", tmp_path)
    monkeypatch.setattr(me, "UPDATE_GOLDENS", False)
    (tmp_path / "x.txt").write_bytes(b"old\n")

    with pytest.raises(pytest.fail.Exception) as differs:
        check_golden("x.txt", b"new\n")

    assert "-old" in str(differs.value) and "+new" in str(differs.value)
    assert (tmp_path / "x.txt").read_bytes() == b"old\n"


# --- the registry --------------------------------------------------------------


def test_the_six_text_writers_are_registered():
    """The rich two (docx, html) are tests/test_exports_rich.py's."""
    for name, writer in WRITERS.items():
        assert exports.FORMATS[name].writer is writer, name


# --- the goldens ---------------------------------------------------------------


@pytest.mark.parametrize("name", list(WRITERS))
def test_the_seed_transcript_matches_its_golden(name):
    data = WRITERS[name](seed_doc(), ExportOptions(formats=[name]))

    assert isinstance(data, bytes)
    check_golden(f"seed.{name}", data)


@pytest.mark.parametrize("name", list(WRITERS))
def test_writers_are_pure_and_deterministic(name):
    doc = seed_doc()
    snapshot = copy.deepcopy(doc)
    options = ExportOptions(formats=[name], timestamps="sentence", include_confidence=True)

    first = WRITERS[name](doc, options)
    second = WRITERS[name](doc, options)

    assert first == second
    assert doc == snapshot


# --- SRT -----------------------------------------------------------------------


def test_srt_timestamps_use_a_comma_and_vtt_a_dot():
    doc = seed_doc()

    srt_text = text_of(srt.write(doc, ExportOptions()))
    vtt_text = text_of(vtt.write(doc, ExportOptions()))

    assert "00:00:00,000 --> 00:00:04,900" in srt_text
    assert "00:00:00.000 --> 00:00:04.900" in vtt_text
    assert "," not in vtt_text.split("\n")[3]  # the first cue's timing line
    assert "-->" in srt_text.split("\n")[1]


def test_srt_is_numbered_blocks_with_the_speaker_on_the_first_line_only():
    doc = seed_doc()

    blocks = text_of(srt.write(doc, ExportOptions())).split("\n\n")

    assert blocks[-1] == ""  # the file ends with the last block's blank line
    assert [b.split("\n")[0] for b in blocks[:-1]] == ["1", "2", "3", "4"]
    first = blocks[0].split("\n")
    assert first[2] == "Arthur: Don't panic, the towel is"
    assert first[3] == "still the most important item."
    third = blocks[2].split("\n")
    assert third[2].startswith("Speaker 2: ")  # the unlabelled cluster's default name


def test_srt_without_speakers_has_no_names():
    doc = seed_doc()

    text = text_of(srt.write(doc, ExportOptions(speakers=False)))

    assert "Arthur" not in text
    assert "Speaker 2" not in text
    assert "Don't panic, the towel is\n" in text


def test_srt_reports_a_first_line_the_speaker_prefix_takes_past_cpl():
    """The engine fits a cue's lines to cpl; the `NAME: ` prefix is the
    writer's own, so the writer's report is where a first line that runs
    past cpl with its name is counted - a preview that said "No violations."
    about a YouTube export with 40-character lines was lying."""
    doc = seed_doc()
    options = PRESETS["youtube"]  # cpl 32
    built = cues.build(doc, options)
    assert built.report.violations == []  # the cue text alone complies

    report = srt.report(doc, built, options)

    over = [v for v in report.violations if v.rule == "cpl"]
    # Only the cues that actually carry a name: cue 1 opens Arthur's run and
    # cue 3 opens the next speaker's. Cues 2, 4 and 5 continue a run, so they
    # are written without a prefix and there is nothing extra to count.
    assert [v.cue_index for v in over] == [1, 3]
    assert over[1].value == len("Speaker 2: Marvin says the improbability") == 40
    assert over[1].limit == 32
    # The engine's own violations stay, in cue order, ahead of the writer's for the same cue.
    tight = options.model_copy(update={"max_cps": 5.0})
    mixed = srt.report(doc, cues.build(doc, tight), tight)
    rules = [(v.cue_index, v.rule) for v in mixed.violations]
    assert rules == sorted(rules, key=lambda item: item[0])
    assert (1, "cps") in rules and (1, "cpl") in rules
    assert rules.index((1, "cps")) < rules.index((1, "cpl"))

    # Names off, nothing to add.
    plain = options.model_copy(update={"speakers": False})
    assert srt.report(doc, cues.build(doc, plain), plain).violations == []


# --- VTT -----------------------------------------------------------------------


def test_vtt_starts_with_the_header_and_numbers_its_cues():
    text = text_of(vtt.write(seed_doc(), ExportOptions()))

    assert text.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:04.900\n")
    assert "\n\n2\n00:00:05.000 --> 00:00:09.900\n" in text
    assert text.endswith("\n\n")


def test_vtt_voice_tags_appear_only_when_speakers_are_on():
    doc = seed_doc()

    on = text_of(vtt.write(doc, ExportOptions(speakers=True)))
    off = text_of(vtt.write(doc, ExportOptions(speakers=False)))

    assert "<v Arthur>Don't panic, the towel is\nstill the most important item." in on
    assert "<v Speaker 2>Marvin" in on
    assert "<v " not in off
    assert "Arthur" not in off


def test_vtt_escapes_markup_in_text_and_in_names():
    words = words_of("AT&T beats <b>bold</b>.", speaker="SPEAKER_00")
    doc = doc_of(words, labels={"SPEAKER_00": "R&D <lead>"})

    text = text_of(vtt.write(doc, ExportOptions()))

    assert "<v R&amp;D &lt;lead&gt;>AT&amp;T beats &lt;b&gt;bold&lt;/b&gt;." in text
    assert "<b>" not in text


# --- TXT -----------------------------------------------------------------------


def test_txt_paragraph_layout_is_one_stamped_line_per_paragraph():
    text = text_of(txt.write(seed_doc(), ExportOptions(txt_layout="paragraph")))

    assert text == (
        "[0:00] Arthur: Don't panic, the towel is still the most important item."
        " The answer to life, the universe and everything is forty-two.\n"
        "\n"
        "[0:10] Speaker 2: Marvin says the improbability drive makes him even more depressed."
        " Vogon poetry is the third worst in the known universe.\n"
    )


def test_txt_cue_layout_is_one_cue_per_line_with_its_start():
    text = text_of(txt.write(seed_doc(), ExportOptions(txt_layout="cue")))

    lines = text.split("\n")
    assert lines[0] == "[0:00] Arthur: Don't panic, the towel is still the most important item."
    # No name on the second line: same speaker, so the name would say nothing.
    assert lines[1] == "[0:05] The answer to life, the universe and everything is forty-two."
    assert lines[2].startswith("[0:10] Speaker 2: Marvin")  # the speaker changed
    assert lines[3].startswith("[0:15] Vogon")
    assert lines[4:] == [""]


def test_txt_cue_layout_without_timestamps_or_speakers_is_bare_lines():
    options = ExportOptions(txt_layout="cue", timestamps="none", speakers=False)

    lines = text_of(txt.write(seed_doc(), options)).split("\n")

    assert lines[0] == "Don't panic, the towel is still the most important item."
    assert "[" not in lines[0] and "Arthur" not in "".join(lines)


def test_txt_monologue_has_no_speaker_names_or_timestamps_and_wraps_at_80():
    text = text_of(txt.write(seed_doc(), ExportOptions(txt_layout="monologue")))

    assert "Arthur" not in text and "Speaker" not in text
    assert "[" not in text
    lines = text.rstrip("\n").split("\n")
    assert len(lines) > 1  # 240 characters of transcript do not fit one line of 80
    assert all(len(line) <= 80 for line in lines)
    assert " ".join(lines) == render.join_text(default_words())
    assert "\n\n" not in text


def test_txt_turn_layout_names_the_speaker_with_the_turns_start():
    text = text_of(txt.write(seed_doc(), ExportOptions(txt_layout="turn", timestamps="turn")))

    assert text == (
        "Arthur (0:00): Don't panic, the towel is still the most important item."
        " The answer to life, the universe and everything is forty-two.\n"
        "\n"
        "Speaker 2 (0:10): Marvin says the improbability drive makes him even more depressed."
        " Vogon poetry is the third worst in the known universe.\n"
    )


def test_txt_turn_layout_merges_a_speakers_consecutive_paragraphs():
    # Two paragraphs by the first speaker (a three-second silence splits
    # them), then another speaker: two turns, the first holding both.
    first = words_of("Yes.", speaker="SPEAKER_00")
    second = words_of("Indeed.", speaker="SPEAKER_00", start=5.0, idx0=1)
    third = words_of("No.", speaker="SPEAKER_01", start=6.0, idx0=2)
    doc = doc_of(first + second + third)
    assert len(render.paragraphs(doc.words)) == 3

    text = text_of(txt.write(doc, ExportOptions(txt_layout="turn", timestamps="turn")))

    assert text == "Speaker 1 (0:00): Yes. Indeed.\n\nSpeaker 2 (0:06): No.\n"


def test_txt_turn_layout_without_names_or_stamps_degrades_cleanly():
    doc = seed_doc()

    no_names = text_of(txt.write(doc, ExportOptions(txt_layout="turn", speakers=False)))
    no_stamps = text_of(txt.write(doc, ExportOptions(txt_layout="turn", timestamps="none")))

    assert no_names.startswith("[0:00] Don't panic")
    assert no_stamps.startswith("Arthur: Don't panic")


def test_timestamps_none_drops_every_stamp():
    for layout in ("cue", "paragraph", "turn"):
        text = text_of(txt.write(seed_doc(), ExportOptions(txt_layout=layout, timestamps="none")))
        assert "[" not in text and "(" not in text, layout
        assert text.startswith("Arthur: Don't panic"), layout


def test_sentence_timestamps_stamp_every_sentence():
    text = text_of(txt.write(seed_doc(), ExportOptions(timestamps="sentence")))

    assert text.startswith("[0:00] Arthur: Don't panic, the towel is still the most important item. [0:05] The answer")
    assert "[0:10] Speaker 2: Marvin says" in text
    assert "depressed. [0:15] Vogon poetry" in text


def test_word_timestamps_stamp_every_word():
    text = text_of(txt.write(seed_doc(), ExportOptions(timestamps="word")))

    assert text.startswith("[0:00] Arthur: Don't [0:00] panic, [0:01] the [0:01] towel [0:02] is")
    assert text.count("[") == len(default_words())


def test_interval_timestamps_land_on_the_first_sentence_past_each_boundary():
    # Sentences start at 0, 5, 10 and 15 s. Every seven seconds: 0 (stamp;
    # next mark 7), 5 (no), 10 (stamp; next mark 14), 15 (stamp).
    text = text_of(txt.write(seed_doc(), ExportOptions(timestamps="interval", interval_seconds=7)))

    lines = text.rstrip("\n").split("\n\n")
    assert lines[0].startswith("[0:00] Arthur: Don't panic")
    assert "[0:05]" not in lines[0]
    assert lines[1].startswith("[0:10] Speaker 2: Marvin")
    assert "depressed. [0:15] Vogon" in lines[1]


def test_turn_timestamps_stamp_only_the_first_paragraph_of_a_turn():
    first = words_of("Yes.", speaker="SPEAKER_00")
    second = words_of("Indeed.", speaker="SPEAKER_00", start=5.0, idx0=1)
    third = words_of("No.", speaker="SPEAKER_01", start=6.0, idx0=2)
    doc = doc_of(first + second + third)

    text = text_of(txt.write(doc, ExportOptions(timestamps="turn")))

    assert text == "[0:00] Speaker 1: Yes.\n\nSpeaker 1: Indeed.\n\n[0:06] Speaker 2: No.\n"


def test_cue_granularity_in_a_prose_layout_stamps_sentences():
    by_cue = txt.write(seed_doc(), ExportOptions(timestamps="cue"))
    by_sentence = txt.write(seed_doc(), ExportOptions(timestamps="sentence"))

    assert by_cue == by_sentence


@pytest.mark.parametrize(
    ("style", "expected"),
    [("clock", "1:02:05"), ("smpte", "01:02:05:12"), ("seconds", "3725.5")],
)
def test_timestamp_formats(style, expected):
    assert common.timestamp(3725.5, style) == expected


def test_timestamp_format_reaches_the_text_writers():
    doc = seed_doc()

    smpte = text_of(txt.write(doc, ExportOptions(timestamp_format="smpte")))
    seconds = text_of(txt.write(doc, ExportOptions(timestamp_format="seconds")))
    md = text_of(markdown.write(doc, ExportOptions(timestamp_format="smpte")))

    assert smpte.startswith("[00:00:00:00] Arthur:")
    assert "[00:00:10:00] Speaker 2:" in smpte
    assert seconds.startswith("[0] Arthur:")
    assert "[10] Speaker 2:" in seconds
    assert "[00:00:10:00](#t=10)" in md


def test_seconds_text_is_compact_and_exact_to_the_millisecond():
    assert common.seconds_text(0.0) == "0"
    assert common.seconds_text(12.3) == "12.3"
    assert common.seconds_text(4.9) == "4.9"
    assert common.seconds_text(3725.512) == "3725.512"
    assert common.seconds_text(10.0) == "10"


# --- CSV -----------------------------------------------------------------------


def test_csv_starts_with_a_bom_and_parses_back_to_the_cue_count():
    doc = seed_doc()
    data = csvw.write(doc, ExportOptions())

    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    cue_count = len(cues.build(doc, ExportOptions()).cues)
    assert rows[0] == ["index", "start", "end", "duration", "speaker", "text", "chars", "cps"]
    assert len(rows) == cue_count + 1
    assert rows[1][:6] == [
        "1", "0.000", "4.900", "4.900", "Arthur",
        "Don't panic, the towel is still the most important item.",
    ]
    assert rows[1][6] == "55"
    assert rows[1][7] == f"{55 / 4.9:.2f}"
    assert rows[3][4] == "Speaker 2"


def test_csv_quotes_like_rfc_4180():
    words = words_of('He said "no", then left.', speaker="SPEAKER_00")

    data = csvw.write(doc_of(words), ExportOptions())

    text = data.decode("utf-8-sig")
    assert '"He said ""no"", then left."' in text
    assert list(csv.reader(io.StringIO(text)))[1][5] == 'He said "no", then left.'


def test_csv_confidence_column_only_with_include_confidence():
    doc = seed_doc()

    plain = csvw.write(doc, ExportOptions()).decode("utf-8-sig")
    with_confidence = csvw.write(doc, ExportOptions(include_confidence=True)).decode("utf-8-sig")

    assert "mean_confidence" not in plain
    rows = list(csv.reader(io.StringIO(with_confidence)))
    assert rows[0][-1] == "mean_confidence"
    # Ten words per cue; the probabilities cycle 0.97, 0.91, 0.72, 0.55 over
    # the whole transcript, so each cue's mean is that of its own ten words.
    words = default_words()

    def mean(chunk):
        return f"{sum(w['probability'] for w in chunk) / len(chunk):.3f}"

    assert rows[1][-1] == mean(words[0:10])
    assert rows[4][-1] == mean(words[30:40])
    assert rows[1][-1] != rows[4][-1]


def test_csv_encodings():
    doc = seed_doc()

    assert csvw.write(doc, ExportOptions(encoding="utf-8-sig")).startswith(b"\xef\xbb\xbf")
    cp = csvw.write(doc, ExportOptions(encoding="cp1252"))
    assert not cp.startswith(b"\xef\xbb\xbf")
    assert cp.decode("cp1252").startswith("index,")


def test_csv_speaker_column_is_empty_without_speakers():
    rows = list(csv.reader(io.StringIO(csvw.write(seed_doc(), ExportOptions(speakers=False)).decode("utf-8-sig"))))

    assert [row[4] for row in rows[1:]] == ["", "", "", ""]


# --- JSON ----------------------------------------------------------------------


def test_json_round_trips_with_schema_version_one():
    doc = seed_doc()
    data = jsonw.write(doc, ExportOptions())

    payload = json.loads(data.decode("utf-8"))

    assert payload["schema_version"] == 1
    assert list(payload) == sorted(payload)
    assert payload["media"]["id"] == 1
    assert payload["media"]["title"] == "Clip"
    assert payload["media"]["sha256"] == SHA256
    assert payload["media"]["duration"] == 30.0
    assert payload["media"]["created_at"] == "2026-09-02T12:00:00Z"
    assert payload["run"]["model"] == "large-v3-turbo"
    assert payload["run"]["language"] == "en"
    assert payload["run"]["params"] == {}
    assert payload["speakers"] == {"SPEAKER_00": "Arthur", "SPEAKER_01": "Speaker 2"}
    assert len(payload["words"]) == len(default_words())
    assert len(payload["segments"]) == 4
    assert payload["segments"][0]["text"].startswith("Don't panic")


def test_json_words_keep_their_text_as_stored_and_confidence_only_on_request():
    doc = seed_doc()

    plain = json.loads(jsonw.write(doc, ExportOptions()).decode("utf-8"))
    rich = json.loads(jsonw.write(doc, ExportOptions(include_confidence=True)).decode("utf-8"))

    assert plain["words"][1] == {"idx": 1, "start": 0.5, "end": 0.9, "text": " panic,", "speaker": "SPEAKER_00"}
    assert rich["words"][1]["probability"] == 0.91
    assert "avg_logprob" not in plain["segments"][0]
    assert rich["segments"][0]["avg_logprob"] is None  # the seed writes none; the key is still there


def test_json_is_indented_by_two_and_keeps_non_ascii():
    words = words_of("Café ünïcode.", speaker="SPEAKER_00")

    text = jsonw.write(doc_of(words), ExportOptions()).decode("utf-8")

    assert '\n  "media": {\n    "created_at"' in text
    assert "Café" in text and "\\u00e9" not in text
    assert text.endswith("}\n")


def test_json_without_speakers_nulls_them():
    payload = json.loads(jsonw.write(seed_doc(), ExportOptions(speakers=False)).decode("utf-8"))

    assert payload["speakers"] == {}
    assert {w["speaker"] for w in payload["words"]} == {None}


def test_json_is_always_utf8_without_a_bom():
    doc = seed_doc()

    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        data = jsonw.write(doc, ExportOptions(encoding=encoding))
        assert not data.startswith(b"\xef\xbb\xbf"), encoding
        assert json.loads(data.decode("utf-8"))["schema_version"] == 1, encoding


# --- Markdown ------------------------------------------------------------------


def test_markdown_front_matter_only_when_requested():
    doc = seed_doc()

    without = text_of(markdown.write(doc, ExportOptions()))
    with_it = text_of(markdown.write(doc, ExportOptions(front_matter=True)))

    assert not without.startswith("---")
    assert with_it.startswith(
        "---\n"
        'title: "Clip"\n'
        "date: 2026-09-02\n"
        'duration: "0:30"\n'
        'model: "large-v3-turbo"\n'
        'language: "en"\n'
        "speakers:\n"
        '  - "Arthur"\n'
        '  - "Speaker 2"\n'
        "---\n"
        "\n"
        "# Clip\n"
    )


def test_markdown_front_matter_quotes_what_yaml_would_misread():
    doc = doc_of(default_words(), labels={"SPEAKER_00": 'Arthur "Dent": yes'}, title="Notes: #1")

    text = text_of(markdown.write(doc, ExportOptions(front_matter=True)))

    assert 'title: "Notes: #1"\n' in text
    assert '  - "Arthur \\"Dent\\": yes"\n' in text


def test_markdown_has_a_title_speaker_headings_and_timestamp_links():
    text = text_of(markdown.write(seed_doc(), ExportOptions()))

    assert text == (
        "# Clip\n"
        "\n"
        "## Arthur\n"
        "\n"
        "[0:00](#t=0) Don't panic, the towel is still the most important item."
        " The answer to life, the universe and everything is forty-two.\n"
        "\n"
        "## Speaker 2\n"
        "\n"
        "[0:10](#t=10) Marvin says the improbability drive makes him even more depressed."
        " Vogon poetry is the third worst in the known universe.\n"
    )


def test_markdown_sentence_timestamps_link_every_sentence():
    text = text_of(markdown.write(seed_doc(), ExportOptions(timestamps="sentence")))

    assert "[0:00](#t=0) Don't panic, the towel is still the most important item. [0:05](#t=5) The answer" in text
    assert "[0:10](#t=10) Marvin" in text
    assert "depressed. [0:15](#t=15) Vogon" in text


def test_markdown_without_timestamps_or_speakers_is_plain_paragraphs():
    text = text_of(markdown.write(seed_doc(), ExportOptions(timestamps="none", speakers=False)))

    assert text == (
        "# Clip\n"
        "\n"
        "Don't panic, the towel is still the most important item."
        " The answer to life, the universe and everything is forty-two.\n"
        "\n"
        "Marvin says the improbability drive makes him even more depressed."
        " Vogon poetry is the third worst in the known universe.\n"
    )


# --- line endings and encodings -------------------------------------------------


@pytest.mark.parametrize("name", list(WRITERS))
def test_crlf_yields_crlf_and_no_bare_newline(name):
    doc = seed_doc()

    lf = WRITERS[name](doc, ExportOptions(formats=[name], front_matter=True))
    crlf = WRITERS[name](doc, ExportOptions(formats=[name], front_matter=True, crlf=True))

    assert b"\r\n" in crlf
    assert b"\r" not in lf
    assert crlf.replace(b"\r\n", b"\n") == lf
    assert not crlf.replace(b"\r\n", b"").count(b"\n")


@pytest.mark.parametrize("name", ["srt", "vtt", "txt", "csv", "md"])
def test_cp1252_encodes_an_e_acute(name):
    words = words_of("Un café, s'il vous plaît.", speaker="SPEAKER_00")
    doc = doc_of(words, labels={"SPEAKER_00": "Zoë"})

    data = WRITERS[name](doc, ExportOptions(formats=[name], encoding="cp1252"))

    assert b"\xe9" in data  # é in CP1252, one byte
    assert b"\xc3\xa9" not in data  # not UTF-8
    assert "café" in data.decode("cp1252")
    assert not data.startswith(b"\xef\xbb\xbf")


def test_a_character_cp1252_cannot_hold_is_refused_not_replaced():
    words = words_of("Arrows → everywhere.", speaker="SPEAKER_00")

    with pytest.raises(UnicodeEncodeError):
        txt.write(doc_of(words), ExportOptions(encoding="cp1252"))


def test_utf8_sig_puts_a_bom_on_every_text_format_that_allows_one():
    doc = seed_doc()

    for name in ("srt", "vtt", "txt", "csv", "md"):
        data = WRITERS[name](doc, ExportOptions(formats=[name], encoding="utf-8-sig"))
        assert data.startswith(b"\xef\xbb\xbf"), name
    # Plain UTF-8 carries none, except CSV, which Excel cannot read without one.
    for name in ("srt", "vtt", "txt", "md"):
        assert not WRITERS[name](doc, ExportOptions(formats=[name])).startswith(b"\xef\xbb\xbf"), name


# --- edge cases -----------------------------------------------------------------


def test_an_empty_transcript_still_writes_a_well_formed_file():
    doc = doc_of([])

    assert srt.write(doc, ExportOptions()) == b""
    assert vtt.write(doc, ExportOptions()) == b"WEBVTT\n\n"
    assert txt.write(doc, ExportOptions()) == b""
    assert txt.write(doc, ExportOptions(txt_layout="monologue")) == b""
    assert csvw.write(doc, ExportOptions()).decode("utf-8-sig") == "index,start,end,duration,speaker,text,chars,cps\n"
    payload = json.loads(jsonw.write(doc, ExportOptions()).decode("utf-8"))
    assert payload["words"] == [] and payload["segments"] == [] and payload["speakers"] == {}
    assert markdown.write(doc, ExportOptions()) == b"# Clip\n"


def test_a_run_without_diarization_has_no_names_anywhere():
    doc = doc_of(words_of("Don't panic. Mostly harmless."))

    assert text_of(srt.write(doc, ExportOptions())).split("\n")[2] == "Don't panic. Mostly harmless."
    assert "<v " not in text_of(vtt.write(doc, ExportOptions()))
    assert text_of(txt.write(doc, ExportOptions())) == "[0:00] Don't panic. Mostly harmless.\n"
    assert text_of(txt.write(doc, ExportOptions(txt_layout="turn"))) == "[0:00] Don't panic. Mostly harmless.\n"
    assert "##" not in text_of(markdown.write(doc, ExportOptions()))
    payload = json.loads(jsonw.write(doc, ExportOptions()).decode("utf-8"))
    assert payload["speakers"] == {}
    assert payload["words"][0]["speaker"] is None


# --- the speaker name marks a change of speaker, not every cue -----------------
#
# Found by exporting a real 5-minute transcript: three consecutive cues of one
# speaker each carried "Speaker 1: ". Subtitle convention is to name a speaker
# when the speaker changes, and repeating it also spends 11 of the 42 characters
# a line has on saying nothing new - which is what forced the cue to break after
# "part of". The plan said "a leading NAME: on the first line" and the writer
# followed it; the plan was wrong.


def _runs_of_two_speakers():
    """A doc whose words run SPEAKER_00 for a while, then SPEAKER_01, then back."""
    words = []
    plan = [("SPEAKER_00", 24), ("SPEAKER_01", 24), ("SPEAKER_00", 12)]
    t = 0.0
    for cluster, count in plan:
        for _ in range(count):
            words.append(
                {"start": t, "end": t + 0.4, "text": " woord", "probability": 0.9, "speaker": cluster}
            )
            t += 0.5
    for i, word in enumerate(words):
        word["idx"] = i
    return doc_of(words, duration=t)


def test_srt_names_a_speaker_only_when_the_speaker_changes():
    doc = _runs_of_two_speakers()
    options = ExportOptions(formats=["srt"], cpl=42, max_lines=2, speakers=True)

    blocks = srt.write(doc, options).decode("utf-8").strip().split("\n\n")
    named = [i for i, b in enumerate(blocks) if "Speaker " in b]

    assert len(blocks) > 3, "need several cues per speaker for this to mean anything"
    assert len(named) == 3, f"expected one name per speaker run, got {len(named)} of {len(blocks)} cues"


def test_srt_still_names_every_speaker_run():
    doc = _runs_of_two_speakers()
    options = ExportOptions(formats=["srt"], cpl=42, max_lines=2, speakers=True)

    text = srt.write(doc, options).decode("utf-8")

    # Both speakers are introduced, and the first one again when he comes back.
    assert text.count("Speaker 1:") == 2
    assert text.count("Speaker 2:") == 1


def test_srt_names_nobody_when_speakers_are_off():
    doc = _runs_of_two_speakers()
    text = srt.write(doc, ExportOptions(formats=["srt"], speakers=False)).decode("utf-8")
    assert "Speaker" not in text


def test_txt_cue_layout_names_a_speaker_only_on_a_change():
    doc = _runs_of_two_speakers()
    options = ExportOptions(formats=["txt"], txt_layout="cue", cpl=42, speakers=True)

    lines = [l for l in txt.write(doc, options).decode("utf-8").splitlines() if l.strip()]
    named = [l for l in lines if "Speaker " in l]

    assert len(lines) > 3
    assert len(named) == 3
