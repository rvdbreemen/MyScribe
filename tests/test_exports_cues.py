"""Phase 4 Task 2: the subtitle segmentation engine and its compliance report.

`cues.build` turns a transcript's words into subtitle cues under the
constraints an `ExportOptions` carries - characters per line, lines per cue,
reading speed, duration bounds, the gap between cues - and reports every
place it could not satisfy them. Nothing grouped is stored (ADR-003): the cues
are a pure function of the words and the options, so these tests build
`TranscriptDoc`s by hand from synthetic words and check the cues that come
out. No database, no GPU.

An engine that silently drifts changes every caption a user exports, so the
seed transcript's cues are pinned line by line, and determinism is tested for
real: build twice on the same input and compare cue by cue.
"""

import copy

import pytest

from scribe import render
from scribe.exports import cues
from scribe.exports.cues import Cue, CueSet, Report, Violation
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import PRESETS, ExportOptions
from seed import default_words


def words_of(text, *, speaker=None, start=0.0, step=0.5, length=0.4, idx0=0):
    """Words for ``text``, one per whitespace token, timed ``step`` apart.

    Each token keeps faster-whisper's leading space, so joining the words back
    together reproduces ``text`` exactly - the convention the rows follow.
    """
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


def doc_of(words, *, duration=None, labels=None, title="Clip"):
    """A ``TranscriptDoc`` over ``words``; the duration defaults to well past them."""
    if duration is None:
        duration = (words[-1]["end"] + 10.0) if words else 0.0
    return TranscriptDoc(
        media={"id": 1, "title": title, "created_at": 0.0, "duration": duration},
        run={"id": 1, "media_id": 1, "model": "large-v3-turbo", "language": "en"},
        words=tuple(words),
        segments=(),
        labels=labels or {},
        duration=float(duration),
        title=title,
    )


def build(words, **options):
    return cues.build(doc_of(words), ExportOptions(**options))


def text_of(cue):
    return " ".join(cue.lines)


# --- the shape of the result ---------------------------------------------------


def test_an_empty_transcript_gives_no_cues_and_no_violations():
    result = build([])

    assert isinstance(result, CueSet)
    assert result.cues == []
    assert isinstance(result.report, Report)
    assert result.report.violations == []


def test_cues_are_numbered_from_one_and_carry_their_words():
    result = build(words_of("Don't panic.", speaker="SPEAKER_00"))

    (cue,) = result.cues
    assert isinstance(cue, Cue)
    assert cue.index == 1
    assert cue.start == 0.0
    assert cue.lines == ["Don't panic."]
    assert cue.speaker == "SPEAKER_00"
    assert result.report.violations == []


def test_the_speaker_is_the_cluster_label_and_none_without_diarization():
    """Names are applied at render time by the writers (ADR-003); the engine
    hands out the canonical label so a rename never changes a cue."""
    labelled = build(words_of("Yes.", speaker="SPEAKER_01"))
    plain = build(words_of("Yes."))

    assert labelled.cues[0].speaker == "SPEAKER_01"
    assert plain.cues[0].speaker is None


def test_blank_words_are_skipped():
    words = words_of("Don't panic.")
    words.insert(1, {"idx": 9, "start": 0.45, "end": 0.46, "text": " ", "speaker": None})
    words.append({"idx": 10, "start": 5.0, "end": 5.1, "text": "", "speaker": None})

    result = build(words)

    assert [c.lines for c in result.cues] == [["Don't panic."]]


def test_a_cue_ending_on_a_multi_word_correction_keeps_the_time_it_covered():
    """`render.paragraphs` was fixed for empty words; this was not.

    The glossary folds a corrected window into its first word and leaves the
    rest of it empty (`glossary._replacement`), so joining the run reproduces
    the phrase exactly once. Here the empty ones are filtered out before the
    timing is worked out - so a cue that ends on such a term ended at the
    first word of the window instead, and the subtitle vanished a whole word
    early while the phrase was still being spoken. One second, on these
    numbers: 2.8 rather than 3.8.
    """
    words = words_of("Presented by why cast", step=1.0, length=0.8)
    words[2]["text"] = " WHYcast"
    words[3]["text"] = ""

    (cue,) = build(words, min_duration=0.0).cues

    assert cue.lines == ["Presented by WHYcast"]
    assert cue.end == pytest.approx(3.8)


def test_cue_measures():
    words = words_of("aaaaa bbbbb ccccc ddddd", step=0.25, length=0.2)

    (cue,) = build(words, cpl=11, min_duration=0.0).cues

    assert cue.lines == ["aaaaa bbbbb", "ccccc ddddd"]
    assert cue.chars == 22  # what is on screen; the line break is not a character
    assert cue.duration == pytest.approx(0.95)
    assert cue.cps == pytest.approx(22 / 0.95)


# --- where a cue breaks --------------------------------------------------------


def test_a_speaker_change_always_starts_a_new_cue():
    first = words_of("Yes.", speaker="SPEAKER_00")
    second = words_of("No.", speaker="SPEAKER_01", start=0.5, idx0=1)
    third = words_of("Maybe.", speaker="SPEAKER_00", start=1.0, idx0=2)

    result = build(first + second + third)

    assert [(c.speaker, c.lines) for c in result.cues] == [
        ("SPEAKER_00", ["Yes."]),
        ("SPEAKER_01", ["No."]),
        ("SPEAKER_00", ["Maybe."]),
    ]
    assert [c.index for c in result.cues] == [1, 2, 3]


def test_a_speaker_change_breaks_even_when_speaker_names_are_off():
    first = words_of("Yes.", speaker="SPEAKER_00")
    second = words_of("No.", speaker="SPEAKER_01", start=0.5, idx0=1)

    result = build(first + second, speakers=False)

    assert len(result.cues) == 2


def test_a_long_sentence_splits_into_cues_within_cpl_and_max_lines():
    sentence = " ".join(f"word{i:02d}" for i in range(20)) + "."  # 20 words, 140 chars
    words = words_of(sentence)

    result = build(words, cpl=42, max_lines=2)

    assert len(result.cues) >= 2
    for cue in result.cues:
        assert 1 <= len(cue.lines) <= 2
        assert all(len(line) <= 42 for line in cue.lines)
        assert cue.chars <= 84
    assert " ".join(text_of(c) for c in result.cues) == sentence
    assert result.report.violations == []


def test_a_sentence_end_is_preferred_over_a_later_comma():
    # One line of 30: "The towel and yes. Fine, more" is 29 characters and the
    # next word does not fit. The last 40% of the candidate holds a sentence
    # end (after "yes.") and, later, a comma (after "Fine,"). The sentence end
    # wins even though the comma would fill the cue better.
    words = words_of("The towel and yes. Fine, more stuff to say")

    result = build(words, cpl=30, max_lines=1)

    assert text_of(result.cues[0]) == "The towel and yes."
    assert text_of(result.cues[1]).startswith("Fine, more stuff")


def test_a_comma_is_preferred_over_a_larger_silence():
    # "The towel and yes, fine more" is 28 characters; " stuff" overflows.
    # There is a comma after "yes," and a half-second silence after "fine".
    words = words_of("The towel and yes, fine more stuff", step=0.5)
    for w in words[5:]:  # "more" starts late: half a second of silence after "fine"
        w["start"] += 0.5
        w["end"] += 0.5

    result = build(words, cpl=30, max_lines=1)

    assert text_of(result.cues[0]) == "The towel and yes,"
    assert text_of(result.cues[1]) == "fine more stuff"


def test_without_punctuation_the_break_falls_on_the_largest_silence_in_the_window():
    # Ten words of one line each 42 wide: "w0 w1 ... w7" is 23 chars; the
    # overflow comes at w8 when cpl is 23. The largest silence in the last 40%
    # is after w5; an earlier, even larger one (after w1) is out of the window.
    words = words_of("w0 w1 w2 w3 w4 w5 w6 w7 w8 w9", step=0.5)
    for w in words[2:]:  # 1.0 s of silence after w1
        w["start"] += 1.0
        w["end"] += 1.0
    for w in words[6:]:  # a further 0.6 s after w5
        w["start"] += 0.6
        w["end"] += 0.6

    result = build(words, cpl=23, max_lines=1, max_duration=60)

    assert text_of(result.cues[0]) == "w0 w1 w2 w3 w4 w5"
    assert text_of(result.cues[1]).startswith("w6 w7")


def test_equal_silences_break_at_the_fullest_cue():
    words = words_of("w0 w1 w2 w3 w4 w5 w6 w7 w8 w9", step=0.5)

    result = build(words, cpl=23, max_lines=1)

    assert text_of(result.cues[0]) == "w0 w1 w2 w3 w4 w5 w6 w7"


def test_a_sentence_end_before_the_window_is_not_used():
    # "Yes. Then we went on and on" is 27 chars; " again" overflows at cpl 30.
    # The sentence end after "Yes." is the first of seven words - outside the
    # last 40% - so the cue is not cut down to a single word there.
    words = words_of("Yes. Then we went on and on again and again")

    result = build(words, cpl=30, max_lines=1)

    assert text_of(result.cues[0]) != "Yes."
    assert text_of(result.cues[0]).startswith("Yes. Then we went")


def test_max_duration_bounds_every_cue():
    words = words_of(" ".join(["la"] * 12), step=1.0, length=0.4)

    result = build(words, max_duration=2.0, min_duration=0.0)

    assert len(result.cues) == 6
    for cue in result.cues:
        assert cue.duration <= 2.0 + 1e-9
    assert [v for v in result.report.violations if v.rule == "max_duration"] == []


def test_a_single_word_longer_than_max_duration_is_reported():
    words = [{"idx": 0, "start": 0.0, "end": 9.0, "text": " Aaaaaargh", "speaker": None}]

    result = build(words, max_duration=7.0)

    (cue,) = result.cues
    assert cue.duration == pytest.approx(9.0)
    assert result.report.violations == [Violation(1, "max_duration", pytest.approx(9.0), 7.0)]


# --- line balancing --------------------------------------------------------------


def test_lines_are_balanced_not_greedy():
    # The plan's example ("aaaa bbbb cccc dddd" at cpl 9 -> 9/9, not 14/4)
    # scaled to a width ExportOptions accepts, which starts at 10.
    words = words_of("aaaaa bbbbb ccccc ddddd")

    assert build(words, cpl=11).cues[0].lines == ["aaaaa bbbbb", "ccccc ddddd"]
    # At cpl 17 a greedy fill would give 17/5; the balance is still 11/11.
    assert build(words, cpl=17).cues[0].lines == ["aaaaa bbbbb", "ccccc ddddd"]


def test_a_cue_uses_the_fewest_lines_that_fit():
    words = words_of("Mostly harmless, mostly.")

    assert build(words, cpl=42, max_lines=2).cues[0].lines == ["Mostly harmless, mostly."]


def test_a_tie_in_balance_goes_bottom_heavy():
    # 5/10 and 10/5 are equally balanced; the shorter line goes on top.
    words = words_of("aaaaa bbbb ccccc")

    assert build(words, cpl=10, max_lines=2).cues[0].lines == ["aaaaa", "bbbb ccccc"]


def test_three_lines_balance_too():
    words = words_of("aaaa bbbb cccc dddd eeee ffff")

    result = build(words, cpl=10, max_lines=3)

    (cue,) = result.cues
    assert cue.lines == ["aaaa bbbb", "cccc dddd", "eeee ffff"]
    assert result.report.violations == []


def test_a_single_word_longer_than_cpl_is_exactly_one_cpl_violation():
    # Four seconds on screen, so the reading speed is fine and only the line
    # length is reported.
    words = [{"idx": 0, "start": 0.0, "end": 4.0, "text": " " + "x" * 60, "speaker": None}]

    result = build(words, cpl=42)

    (cue,) = result.cues
    assert cue.lines == ["x" * 60]
    assert result.report.violations == [Violation(1, "cpl", 60, 42)]


def test_an_overlong_word_takes_its_own_line_and_the_rest_carries_on():
    words = [{"idx": 0, "start": 0.0, "end": 4.0, "text": " " + "x" * 60, "speaker": None}]
    words += words_of("and more", start=4.5, idx0=1)

    result = build(words, cpl=42, max_lines=2)

    assert result.cues[0].lines == ["x" * 60, "and more"]
    assert [v.rule for v in result.report.violations] == ["cpl"]


# --- timing ----------------------------------------------------------------------


def test_min_duration_extends_a_short_cue_when_the_gap_allows():
    first = words_of("Yes.", speaker="SPEAKER_00", length=0.3)
    second = words_of("Indeed.", speaker="SPEAKER_01", start=3.0, idx0=1)

    result = build(first + second, min_duration=1.0)

    assert result.cues[0].start == 0.0
    assert result.cues[0].end == pytest.approx(1.0)
    assert result.report.violations == []


def test_min_duration_extension_stops_at_the_cue_gap_and_is_reported():
    first = words_of("Yes.", speaker="SPEAKER_00", length=0.3)
    second = words_of("Indeed.", speaker="SPEAKER_01", start=0.6, idx0=1)

    result = build(first + second, min_duration=1.0, cue_gap=0.08)

    assert result.cues[0].end == pytest.approx(0.52)
    assert result.cues[1].start == 0.6
    assert [(v.rule, v.cue_index) for v in result.report.violations] == [("min_duration", 1)]
    (violation,) = result.report.violations
    assert violation.value == pytest.approx(0.52)
    assert violation.limit == 1.0


def test_the_last_cue_extends_to_min_duration_but_not_past_the_media_end():
    words = words_of("Bye.", start=10.0, length=0.3)

    assert cues.build(doc_of(words, duration=30.0), ExportOptions(min_duration=1.0)).cues[0].end == pytest.approx(11.0)
    assert cues.build(doc_of(words, duration=10.5), ExportOptions(min_duration=1.0)).cues[0].end == pytest.approx(10.5)


def test_the_cue_gap_is_enforced_by_trimming_the_earlier_cue():
    first = words_of("So that is that.", speaker="SPEAKER_00", step=0.25, length=0.25)
    # The next speaker starts 20 ms after the last word ends: too close.
    second = words_of("Right.", speaker="SPEAKER_01", start=first[-1]["end"] + 0.02, idx0=4)

    result = build(first + second, cue_gap=0.08)

    a, b = result.cues
    assert b.start == second[0]["start"]
    assert a.end == pytest.approx(b.start - 0.08)
    assert b.start - a.end >= 0.08 - 1e-9


def test_a_zero_cue_gap_leaves_abutting_words_alone():
    first = words_of("So that is that.", speaker="SPEAKER_00", step=0.25, length=0.25)
    second = words_of("Right.", speaker="SPEAKER_01", start=first[-1]["end"], idx0=4)

    result = build(first + second, cue_gap=0.0)

    a, b = result.cues
    assert a.end == first[-1]["end"]
    assert b.start == a.end


def test_consecutive_cues_never_overlap_and_keep_the_gap():
    result = build(default_words(), cpl=20, max_lines=1, cue_gap=0.08)

    assert len(result.cues) > 4
    for a, b in zip(result.cues, result.cues[1:]):
        assert a.start <= a.end
        assert b.start - a.end >= 0.08 - 1e-9


def test_a_cue_never_starts_before_the_one_before_it_ends():
    """Word times that run backwards (the next speaker's first word stamped
    before this one's last) must not become overlapping cues out of time
    order: the later cue starts where the earlier ended. The earlier keeps
    its zero length and its report - the input is what it is."""
    words = words_of("Alpha", speaker="A", start=1.0) + words_of("Beta", speaker="B", start=0.5, length=1.0, idx0=1)
    assert words[1]["start"] < words[0]["start"]

    result = build(words)

    first, second = result.cues
    assert first.start == first.end == 1.0
    assert second.start == pytest.approx(1.0)  # clamped to first.end, not 0.5
    assert second.end == pytest.approx(2.0)  # held to min_duration from there
    assert second.start >= first.end
    assert [cue.start for cue in result.cues] == sorted(cue.start for cue in result.cues)
    assert [v.cue_index for v in result.report.violations] == [1, 1]


# --- reading speed ---------------------------------------------------------------


def test_a_cps_violation_is_reported_when_100_chars_land_in_one_second():
    # Ten nine-letter words in one second: 98 characters on two lines of 49.
    words = words_of(" ".join(["abcdefghi"] * 10), step=0.1, length=0.1)

    result = build(words, cpl=50, max_lines=2, max_cps=20.0, min_duration=1.0)

    (cue,) = result.cues
    assert cue.duration == pytest.approx(1.0)
    assert cue.chars == 98
    (violation,) = result.report.violations
    assert violation == Violation(1, "cps", pytest.approx(98.0), 20.0)


def test_reading_speed_counts_the_extended_display_time():
    # The same words shown for five seconds read at under twenty per second.
    words = words_of(" ".join(["abcdefghi"] * 10), step=0.1, length=0.1)

    result = build(words, cpl=50, max_lines=2, max_cps=20.0, min_duration=5.0, max_duration=7.0)

    assert result.cues[0].cps == pytest.approx(98 / 5.0)
    assert result.report.violations == []


def test_a_cue_exactly_at_max_cps_is_not_a_violation():
    words = words_of("abcdefghij abcdefghi", step=0.5, length=0.5)  # 20 chars in 1 s

    result = build(words, max_cps=20.0, min_duration=1.0)

    assert result.cues[0].cps == pytest.approx(20.0)
    assert result.report.violations == []


# --- the seed transcript at the defaults ------------------------------------------


def test_the_seed_transcript_becomes_one_cue_per_sentence_at_the_defaults():
    """The default constraints on ordinary speech: four sentences, four cues,
    each balanced over two lines, and a clean report. Pinned line by line so
    a drift in the engine shows up here before it shows up in a caption."""
    result = build(default_words())

    assert [c.lines for c in result.cues] == [
        ["Don't panic, the towel is", "still the most important item."],
        ["The answer to life, the universe", "and everything is forty-two."],
        ["Marvin says the improbability drive", "makes him even more depressed."],
        ["Vogon poetry is the third", "worst in the known universe."],
    ]
    assert [c.speaker for c in result.cues] == ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01", "SPEAKER_01"]
    assert [(c.start, c.end) for c in result.cues] == [
        (0.0, pytest.approx(4.9)),
        (5.0, pytest.approx(9.9)),
        (10.0, pytest.approx(14.9)),
        (15.0, pytest.approx(19.9)),
    ]
    assert result.report.violations == []


def test_every_word_comes_out_once_in_order():
    words = default_words()

    for options in (ExportOptions(), *PRESETS.values()):
        result = cues.build(doc_of(words), options)
        assert " ".join(text_of(c) for c in result.cues) == render.join_text(words), options
        assert [c.index for c in result.cues] == list(range(1, len(result.cues) + 1))


def test_the_presets_constraints_hold_on_the_seed_transcript():
    for name in ("netflix", "bbc", "youtube"):
        options = PRESETS[name]
        result = cues.build(doc_of(default_words()), options)
        for cue in result.cues:
            assert len(cue.lines) <= options.max_lines, name
            assert all(len(line) <= options.cpl for line in cue.lines), name
            assert cue.duration <= options.max_duration + 1e-9, name
        assert result.report.violations == [], name


# --- determinism -----------------------------------------------------------------


def test_build_is_deterministic():
    """Same input and options, twice: identical cues, field by field, and the
    same report; the document's words come out untouched."""
    words = default_words()
    doc = doc_of(words)
    snapshot = copy.deepcopy(doc.words)
    options = ExportOptions(cpl=20, max_lines=2, max_cps=12.0)

    first = cues.build(doc, options)
    second = cues.build(doc, options)

    assert len(first.cues) == len(second.cues) > 4
    for a, b in zip(first.cues, second.cues):
        assert a.index == b.index
        assert a.start == b.start
        assert a.end == b.end
        assert a.lines == b.lines
        assert a.speaker == b.speaker
    assert first.cues == second.cues
    assert first.report.violations == second.report.violations
    assert doc.words == snapshot


def test_build_does_not_depend_on_the_order_of_a_words_keys():
    words = default_words()
    reordered = [dict(reversed(list(w.items()))) for w in words]
    assert [list(w) for w in reordered] != [list(w) for w in words]

    a = cues.build(doc_of(words), ExportOptions(cpl=20))
    b = cues.build(doc_of(reordered), ExportOptions(cpl=20))

    assert a.cues == b.cues
    assert a.report == b.report


def test_build_does_not_depend_on_extra_keys_or_row_ids():
    words = default_words()
    decorated = [{**w, "id": 1000 - w["idx"], "run_id": 3, "edited_by_user": 0} for w in words]

    assert cues.build(doc_of(decorated), ExportOptions()).cues == cues.build(doc_of(words), ExportOptions()).cues
