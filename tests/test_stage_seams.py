"""TASK-035: where two decodes meet, the same words must not be written twice.

Counted in the library on 2026-09-11: 31 pairs of consecutive words in the
current runs overlap in time, and one decode never writes such a pair - all 31
sit at a window cut or a second-opinion splice edge. 18 are one word written
twice at a cut, one is two words written twice at a splice (media 34), and 12
are two different words, which need a listen and are left alone.
"""

from __future__ import annotations

from scribe.stages import seams


def w(start, end, text):
    return {"start": start, "end": end, "text": f" {text}"}


def test_one_word_written_by_both_sides_is_an_echo():
    # Media 17: "opportunities when when they are presented".
    left = [w(595.20, 595.82, "opportunities"), w(595.82, 596.72, "when")]
    right = [w(596.30, 596.74, "when"), w(596.74, 596.86, "they")]

    assert seams.echo(left, right) == 1


def test_two_words_written_by_both_sides_are_one_echo():
    # Media 34: "Yeah. And that And that was quite impressive."
    left = [w(800.54, 801.12, "And"), w(801.12, 803.36, "that")]
    right = [w(802.66, 803.10, "And"), w(803.10, 803.36, "that"), w(803.36, 803.60, "was")]

    assert seams.echo(left, right) == 2


def test_case_and_punctuation_do_not_hide_an_echo():
    # Media 51 "women Women", media 22 "like, like".
    assert seams.echo([w(1195.84, 1196.98, "women")], [w(1196.50, 1196.98, "Women")]) == 1
    assert seams.echo([w(2390.63, 2392.67, "like,")], [w(2392.10, 2392.66, "like")]) == 1


def test_the_same_word_with_a_pause_between_is_said_twice():
    # "no, no": the copies do not overlap, so the speaker said it twice.
    assert seams.echo([w(9.0, 9.3, "no,")], [w(9.6, 9.9, "no")]) == 0


def test_copies_that_only_touch_were_said_twice():
    # Overlap is the proof; two words that meet end to start are two words.
    assert seams.echo([w(5.0, 5.3, "I")], [w(5.3, 5.5, "I")]) == 0


def test_a_word_said_three_times_loses_only_its_echo():
    """"no, no, no" with the middle one written by both sides: the left "no"
    at 9.0-9.3 overlaps nothing, so it was said. Found in review: the
    two-word match took the one-word overlap as its proof and dropped it."""
    left = [w(9.0, 9.3, "no,"), w(9.5, 10.4, "no,")]
    right = [w(10.0, 10.42, "no,"), w(10.7, 11.0, "no")]

    assert seams.echo(left, right) == 1


def test_a_phrase_said_twice_across_a_seam_was_said_twice():
    # "I think, I think": "think" overlaps the next "I", a different word -
    # the twins (think and think) do not overlap, so nothing is an echo.
    left = [w(598.9, 599.3, "I"), w(599.3, 600.3, "think,")]
    right = [w(600.0, 600.4, "I"), w(600.4, 600.8, "think")]

    assert seams.echo(left, right) == 0


def test_two_different_words_that_overlap_are_left_alone():
    # Media 11: "you" 595.64-596.66 against "Yeah," from 596.40. Which of the
    # two was said needs a listen; this rule only drops what it can prove.
    assert seams.echo([w(595.64, 596.66, "you")], [w(596.40, 596.66, "Yeah,")]) == 0


def test_a_word_of_only_punctuation_is_not_an_echo():
    assert seams.echo([w(1.0, 1.5, "-")], [w(1.2, 1.4, "-")]) == 0


def test_nothing_either_side_is_no_echo():
    assert seams.echo([], [w(1.0, 1.2, "a")]) == 0
    assert seams.echo([w(1.0, 1.2, "a")], []) == 0


def _rows():
    # Segment edges a little outside their words, as Whisper's are, so a test
    # can tell an edge that moved from one that was recomputed.
    words = [w(0.1, 0.4, "for"), w(0.4, 0.8, "those"), w(1.0, 1.9, "when"), w(1.5, 1.9, "when"), w(1.9, 2.1, "they")]
    for i, word in enumerate(words):
        word["idx"] = i
        word["segment_idx"] = [0, 0, 1, 2, 2][i]
    segments = [
        {"idx": 0, "start": 0.0, "end": 0.9, "text": "for those"},
        {"idx": 1, "start": 1.0, "end": 1.9, "text": "when"},
        {"idx": 2, "start": 1.4, "end": 2.2, "text": "when they"},
    ]
    return segments, words


def test_removing_words_says_each_segment_again_from_what_is_left():
    segments, words = _rows()

    segments, words = seams.remove_words(segments, words, range(2, 3))
    seams.renumber(segments, words)

    assert [s["text"] for s in segments] == ["for those", "when they"]
    assert [s["idx"] for s in segments] == [0, 1]
    assert [(x["idx"], x["segment_idx"]) for x in words] == [(0, 0), (1, 0), (2, 1), (3, 1)]


def test_a_segment_that_loses_its_first_or_last_word_moves_that_edge():
    segments, words = _rows()

    head, _ = seams.remove_words(segments, words, range(3, 4))  # "when" of "when they"
    tail, _ = seams.remove_words(segments, words, range(1, 2))  # "those" of "for those"

    # Only the edge whose word went moves; the other keeps Whisper's.
    assert (head[2]["start"], head[2]["end"], head[2]["text"]) == (1.9, 2.2, "they")
    assert (tail[0]["start"], tail[0]["end"], tail[0]["text"]) == (0.0, 0.4, "for")


def test_removing_leaves_the_rows_it_was_given_alone():
    segments, words = _rows()
    before = ([dict(s) for s in segments], [dict(x) for x in words])

    seams.remove_words(segments, words, range(1, 2))

    assert (segments, words) == before
