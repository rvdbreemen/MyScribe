"""Word-level speaker attribution (Phase 2 Task 5).

Pure functions, synthetic fixtures, no GPU and no models. This is the join
where a silent bug mislabels every speaker in the transcript, so it gets the
hardest tests in the codebase.
"""

import pytest

from scribe.stages import attribute
from scribe.stages.attribute import Turn


def w(start, end, text="x", **extra):
    return {"start": start, "end": end, "text": text, **extra}


# --- turns_from_diarization ------------------------------------------------


def test_turns_from_a_pyannote_like_annotation():
    class Segment:
        def __init__(self, start, end):
            self.start, self.end = start, end

    class Annotation:
        def itertracks(self, yield_label=False):
            assert yield_label
            yield Segment(0.0, 1.0), None, "SPEAKER_00"
            yield Segment(1.0, 2.0), None, "SPEAKER_01"

    turns = attribute.turns_from_diarization(Annotation())

    assert turns == [Turn(0.0, 1.0, "SPEAKER_00"), Turn(1.0, 2.0, "SPEAKER_01")]


def test_turns_from_dicts_and_tuples_and_turns():
    turns = attribute.turns_from_diarization(
        [
            {"start": 0.0, "end": 1.0, "speaker": "A"},
            {"start": 1.0, "end": 2.0, "label": "B"},
            (2.0, 3.0, "C"),
            Turn(3.0, 4.0, "D"),
        ]
    )

    assert [t.speaker for t in turns] == ["A", "B", "C", "D"]
    assert all(isinstance(t.start, float) for t in turns)


def test_turns_from_nothing_is_empty_not_an_error():
    assert attribute.turns_from_diarization(None) == []
    assert attribute.turns_from_diarization([]) == []


def test_dict_entry_without_a_speaker_is_skipped_not_guessed():
    turns = attribute.turns_from_diarization([{"start": 0.0, "end": 1.0}])
    assert turns == []


# --- speaker_for_interval --------------------------------------------------


def test_word_fully_inside_one_turn_gets_that_speaker():
    turns = [Turn(0.0, 5.0, "A"), Turn(5.0, 10.0, "B")]
    assert attribute.speaker_for_interval(1.0, 2.0, turns) == "A"
    assert attribute.speaker_for_interval(6.0, 7.0, turns) == "B"


def test_straddling_word_goes_to_the_larger_overlap():
    turns = [Turn(0.0, 5.0, "A"), Turn(5.0, 10.0, "B")]
    # 4.9..5.4 -> 0.1s of A, 0.4s of B
    assert attribute.speaker_for_interval(4.9, 5.4, turns) == "B"
    # 4.6..5.1 -> 0.4s of A, 0.1s of B
    assert attribute.speaker_for_interval(4.6, 5.1, turns) == "A"


def test_overlapping_turns_pick_the_larger_overlap_deterministically():
    turns = [Turn(0.0, 3.0, "A"), Turn(2.0, 6.0, "B")]
    # 2.0..3.5 -> 1.0s of A, 1.5s of B
    assert attribute.speaker_for_interval(2.0, 3.5, turns) == "B"
    # exact tie 2.0..3.0 vs both: A gives 1.0, B gives 1.0 -> first turn wins
    assert attribute.speaker_for_interval(2.0, 3.0, turns) == "A"


def test_word_in_a_gap_overlaps_nothing():
    turns = [Turn(0.0, 1.0, "A"), Turn(5.0, 6.0, "B")]
    assert attribute.speaker_for_interval(2.0, 3.0, turns) is None


def test_zero_length_word_touching_a_turn_edge_is_not_attributed_by_touching():
    turns = [Turn(0.0, 5.0, "A")]
    # zero-length interval has zero overlap everywhere; no evidence, no guess
    assert attribute.speaker_for_interval(3.0, 3.0, turns) is None


# --- attribute_words -------------------------------------------------------


def test_attribute_words_labels_each_word_by_overlap():
    words = [w(0.5, 1.0), w(1.0, 1.5), w(6.0, 6.5)]
    turns = [Turn(0.0, 5.0, "A"), Turn(5.0, 10.0, "B")]

    out = attribute.attribute_words(words, turns)

    assert [x["speaker"] for x in out] == ["A", "A", "B"]


def test_attribute_words_does_not_mutate_the_input():
    words = [w(0.5, 1.0)]
    attribute.attribute_words(words, [Turn(0.0, 5.0, "A")])
    assert "speaker" not in words[0]


def test_no_turns_leaves_every_speaker_none_without_raising():
    words = [w(0.5, 1.0), w(1.0, 1.5)]

    out = attribute.attribute_words(words, [])

    assert [x["speaker"] for x in out] == [None, None]


def test_no_words_returns_empty():
    assert attribute.attribute_words([], [Turn(0.0, 1.0, "A")]) == []


# --- fill_unattributed -----------------------------------------------------


def test_gap_word_inherits_the_previous_speaker():
    words = [
        {"start": 0.5, "end": 1.0, "text": "a", "speaker": "A"},
        {"start": 2.0, "end": 2.2, "text": "yeah", "speaker": None},
        {"start": 6.0, "end": 6.5, "text": "b", "speaker": "B"},
    ]

    out = attribute.fill_unattributed(words)

    assert [x["speaker"] for x in out] == ["A", "A", "B"]


def test_leading_gap_words_inherit_the_first_known_speaker():
    words = [
        {"start": 0.0, "end": 0.2, "text": "uh", "speaker": None},
        {"start": 0.3, "end": 0.5, "text": "um", "speaker": None},
        {"start": 1.0, "end": 1.5, "text": "hello", "speaker": "A"},
    ]

    out = attribute.fill_unattributed(words)

    assert [x["speaker"] for x in out] == ["A", "A", "A"]


def test_all_none_stays_all_none():
    words = [{"start": 0.0, "end": 1.0, "text": "a", "speaker": None}]
    assert [x["speaker"] for x in attribute.fill_unattributed(words)] == [None]


def test_trailing_gap_inherits_the_last_known_speaker():
    words = [
        {"start": 0.0, "end": 1.0, "text": "a", "speaker": "A"},
        {"start": 9.0, "end": 9.5, "text": "mm", "speaker": None},
    ]

    out = attribute.fill_unattributed(words)

    assert [x["speaker"] for x in out] == ["A", "A"]


# --- the composed join -----------------------------------------------------


def test_join_end_to_end_over_a_two_speaker_conversation():
    turns = [Turn(0.0, 2.0, "SPEAKER_00"), Turn(2.5, 5.0, "SPEAKER_01")]
    words = [
        w(0.1, 0.4, "Hallo"),
        w(0.5, 0.9, "daar"),
        w(2.1, 2.3, "eh"),          # in the gap between turns
        w(2.6, 3.0, "Hoi"),
        w(3.1, 3.6, "terug"),
    ]

    out = attribute.join(words, turns)

    assert [x["speaker"] for x in out] == [
        "SPEAKER_00",
        "SPEAKER_00",
        "SPEAKER_00",  # gap word carried forward
        "SPEAKER_01",
        "SPEAKER_01",
    ]
    assert [x["text"] for x in out] == ["Hallo", "daar", "eh", "Hoi", "terug"]


def test_join_is_stable_under_unsorted_turns():
    turns = [Turn(5.0, 10.0, "B"), Turn(0.0, 5.0, "A")]
    words = [w(1.0, 2.0), w(6.0, 7.0)]

    assert [x["speaker"] for x in attribute.join(words, turns)] == ["A", "B"]


@pytest.mark.parametrize("count", [1, 2, 50])
def test_join_preserves_word_count_and_order(count):
    words = [w(i * 1.0, i * 1.0 + 0.5, f"w{i}") for i in range(count)]
    turns = [Turn(0.0, count * 1.0, "A")]

    out = attribute.join(words, turns)

    assert [x["text"] for x in out] == [f"w{i}" for i in range(count)]
