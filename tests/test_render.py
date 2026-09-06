"""Derived view models over canonical words (Phase 3 Task 2).

Pure functions, synthetic words, no database. Everything a transcript page or
an exporter shows that is not a word - a paragraph, a sentence, a timestamp, a
speaker's name, a confidence band - is computed here from word rows and never
stored (ADR-003), so these tests are the whole contract for what a reader sees.

The word dictionaries mirror the rows the transcribe stage persists: text
carries faster-whisper's leading space, timestamps are seconds, `speaker` is a
diarization cluster label or None.
"""

import pytest

from scribe import render
from scribe.render import Paragraph, Sentence
from seed import default_words


def words_of(text, *, speaker=None, start=0.0, step=0.5, length=0.4, idx0=0):
    """Words for ``text``, one per whitespace token, timed ``step`` apart.

    Each token keeps the leading space faster-whisper gives it, so joining the
    words back together reproduces ``text`` exactly.
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


def texts(paragraphs):
    return [[s.text for s in p.sentences] for p in paragraphs]


# --- paragraphs: the breaks --------------------------------------------------


def test_one_speaker_without_gaps_is_one_paragraph():
    words = words_of("Don't panic. Mostly harmless.", speaker="SPEAKER_00")

    paragraphs = render.paragraphs(words)

    assert len(paragraphs) == 1
    (p,) = paragraphs
    assert isinstance(p, Paragraph)
    assert p.speaker == "SPEAKER_00"
    assert p.display_name == "Speaker 1"
    assert p.start == words[0]["start"]
    assert p.end == words[-1]["end"]
    assert [s.text for s in p.sentences] == ["Don't panic.", "Mostly harmless."]
    assert p.text == "Don't panic. Mostly harmless."


def test_paragraph_splits_on_speaker_change():
    first = words_of("I think that is fine.", speaker="SPEAKER_00")
    second = words_of("I do not.", speaker="SPEAKER_01", start=2.5, idx0=len(first))

    paragraphs = render.paragraphs(first + second)

    assert [p.speaker for p in paragraphs] == ["SPEAKER_00", "SPEAKER_01"]
    assert texts(paragraphs) == [["I think that is fine."], ["I do not."]]
    assert paragraphs[0].end == first[-1]["end"]
    assert paragraphs[1].start == second[0]["start"]


def test_paragraph_splits_on_a_silence_gap_of_at_least_two_seconds():
    first = words_of("So that is that.", speaker="SPEAKER_00")
    # 2.5 s of silence between the end of the last word and the next start.
    second = words_of(
        "Anyway, moving on.", speaker="SPEAKER_00", start=first[-1]["end"] + 2.5, idx0=4
    )

    paragraphs = render.paragraphs(first + second)

    assert texts(paragraphs) == [["So that is that."], ["Anyway, moving on."]]
    assert all(p.speaker == "SPEAKER_00" for p in paragraphs)


def test_paragraph_does_not_split_on_a_gap_under_two_seconds():
    first = words_of("So that is that.", speaker="SPEAKER_00")
    second = words_of(
        "Anyway, moving on.", speaker="SPEAKER_00", start=first[-1]["end"] + 1.9, idx0=4
    )

    paragraphs = render.paragraphs(first + second)

    assert texts(paragraphs) == [["So that is that.", "Anyway, moving on."]]


def test_the_gap_threshold_is_a_parameter():
    first = words_of("So that is that.", speaker="SPEAKER_00")
    second = words_of(
        "Anyway, moving on.", speaker="SPEAKER_00", start=first[-1]["end"] + 1.9, idx0=4
    )

    assert len(render.paragraphs(first + second, gap=1.0)) == 2
    assert len(render.paragraphs(first + second, gap=2.0)) == 1


def test_paragraph_splits_after_max_chars_when_no_sentence_ends():
    # Sixty five-character words (" word") and not a full stop anywhere: the
    # only thing that can end a paragraph is the character budget.
    words = words_of(" ".join(["word"] * 60), speaker="SPEAKER_00")

    paragraphs = render.paragraphs(words, max_chars=50)

    assert len(paragraphs) == 6
    assert all(len(p.text) <= 50 for p in paragraphs)
    assert [len(p.sentences) for p in paragraphs] == [1] * 6
    # Nothing lost, nothing reordered.
    assert [w for p in paragraphs for s in p.sentences for w in s.words] == words


def test_max_chars_breaks_at_the_last_sentence_end_before_the_budget():
    words = words_of("One two three. Four five six. Seven eight nine.", speaker="SPEAKER_00")

    # " One two three." + " Four five six." is 30 characters; " Seven" would
    # make it 36, so the paragraph closes after the second sentence.
    paragraphs = render.paragraphs(words, max_chars=35)

    assert texts(paragraphs) == [
        ["One two three.", "Four five six."],
        ["Seven eight nine."],
    ]


def test_a_paragraph_never_passes_max_chars_when_a_sentence_fits():
    words = words_of("One two three. Four five six. Seven eight nine.", speaker="SPEAKER_00")

    paragraphs = render.paragraphs(words, max_chars=700)

    assert len(paragraphs) == 1
    assert len(paragraphs[0].sentences) == 3


def test_no_words_means_no_paragraphs():
    assert render.paragraphs([]) == []


def test_without_diarization_there_is_no_speaker_and_no_display_name():
    words = words_of("Nobody knows who said this.")

    (p,) = render.paragraphs(words)

    assert p.speaker is None
    assert p.display_name is None


def test_labels_name_the_paragraphs_and_unlabelled_clusters_get_a_number():
    first = words_of("Hello there.", speaker="SPEAKER_00")
    second = words_of("General Kenobi.", speaker="SPEAKER_01", start=1.5, idx0=2)

    paragraphs = render.paragraphs(first + second, {"SPEAKER_00": "Arthur"})

    assert [p.display_name for p in paragraphs] == ["Arthur", "Speaker 2"]


def test_the_default_seed_transcript_groups_into_two_paragraphs_of_two_sentences():
    words = default_words()

    paragraphs = render.paragraphs(words, {"SPEAKER_01": "Marvin"})

    assert [p.speaker for p in paragraphs] == ["SPEAKER_00", "SPEAKER_01"]
    assert [p.display_name for p in paragraphs] == ["Speaker 1", "Marvin"]
    assert texts(paragraphs) == [
        [
            "Don't panic, the towel is still the most important item.",
            "The answer to life, the universe and everything is forty-two.",
        ],
        [
            "Marvin says the improbability drive makes him even more depressed.",
            "Vogon poetry is the third worst in the known universe.",
        ],
    ]
    assert paragraphs[0].start == 0.0
    assert paragraphs[1].end == words[-1]["end"]


def test_paragraphs_take_row_like_mappings_not_only_dicts():
    class Row(dict):
        """sqlite3.Row has item access and no .get; a dict subclass with .get
        removed stands in for it."""

        def get(self, *args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("render must not rely on .get()")

    words = [Row(w) for w in words_of("Row access only.", speaker="SPEAKER_00")]

    (p,) = render.paragraphs(words)

    assert p.text == "Row access only."


def test_paragraphs_take_the_rows_the_database_hands_back(tmp_path):
    # The stand-in above pins item access; this pins the real type, whose
    # missing-key error is IndexError rather than KeyError.
    from scribe import db
    from seed import seed_media, seed_run

    conn = db.connect(tmp_path / "render.db")
    db.migrate(conn)
    run_id = seed_run(conn, seed_media(conn), labels={"SPEAKER_00": "Arthur"})
    rows = conn.execute("SELECT * FROM word WHERE run_id=? ORDER BY idx", (run_id,)).fetchall()
    labels = {
        r["cluster_label"]: r["display_name"]
        for r in conn.execute("SELECT * FROM speaker_label WHERE run_id=?", (run_id,))
    }
    conn.close()

    paragraphs = render.paragraphs(rows, labels)

    assert [p.display_name for p in paragraphs] == ["Arthur", "Speaker 2"]
    assert [len(p.sentences) for p in paragraphs] == [2, 2]
    assert paragraphs[0].sentences[0].words[0] is rows[0]


def test_words_without_a_speaker_key_are_read_as_unattributed():
    words = [{"start": 0.0, "end": 0.4, "text": " Hi."}, {"start": 0.5, "end": 0.9, "text": " There."}]

    (p,) = render.paragraphs(words)

    assert p.speaker is None
    assert p.text == "Hi. There."


# --- sentences ----------------------------------------------------------------


def test_sentence_splits_on_a_period_followed_by_a_space():
    words = words_of("Don't panic. Mostly harmless.", speaker="SPEAKER_00")

    (p,) = render.paragraphs(words)

    assert len(p.sentences) == 2
    first, second = p.sentences
    assert isinstance(first, Sentence)
    assert first.text == "Don't panic."
    assert (first.start, first.end) == (words[0]["start"], words[1]["end"])
    assert second.text == "Mostly harmless."
    assert (second.start, second.end) == (words[2]["start"], words[3]["end"])


def test_sentence_does_not_split_on_a_decimal_point():
    words = words_of("Pi is 3.14 roughly.", speaker="SPEAKER_00")

    (p,) = render.paragraphs(words)

    assert [s.text for s in p.sentences] == ["Pi is 3.14 roughly."]


def test_sentence_splits_on_question_exclamation_and_ellipsis():
    words = words_of("Really? Yes! Well… fine.", speaker="SPEAKER_00")

    (p,) = render.paragraphs(words)

    assert [s.text for s in p.sentences] == ["Really?", "Yes!", "Well…", "fine."]


def test_a_closing_quote_after_the_full_stop_still_ends_the_sentence():
    words = words_of('He said "no." Then left.', speaker="SPEAKER_00")

    (p,) = render.paragraphs(words)

    assert [s.text for s in p.sentences] == ['He said "no."', "Then left."]


def test_the_last_sentence_closes_without_punctuation():
    words = words_of("no full stop here", speaker="SPEAKER_00")

    (p,) = render.paragraphs(words)

    assert [s.text for s in p.sentences] == ["no full stop here"]


def test_a_paragraph_break_closes_an_open_sentence():
    first = words_of("I think that", speaker="SPEAKER_00")
    second = words_of("you are wrong.", speaker="SPEAKER_01", start=1.5, idx0=3)

    paragraphs = render.paragraphs(first + second)

    assert texts(paragraphs) == [["I think that"], ["you are wrong."]]


def test_sentence_words_are_the_input_objects_in_order():
    words = words_of("Keep the rows. Please.", speaker="SPEAKER_00")

    (p,) = render.paragraphs(words)

    assert [w["idx"] for s in p.sentences for w in s.words] == [0, 1, 2, 3]
    assert p.sentences[0].words[0] is words[0]
    assert p.sentences[1].words[0] is words[3]


# --- join_text ----------------------------------------------------------------


def test_join_text_keeps_the_leading_spaces_and_strips_once():
    joined = render.join_text([{"text": " Don't"}, {"text": " panic."}, {"text": " Mostly"}])

    assert joined == "Don't panic. Mostly"
    assert "  " not in joined


def test_join_text_never_inserts_a_space_of_its_own():
    # A language written without spaces, or a token faster-whisper attached to
    # the previous one, must come back exactly as it was stored.
    assert render.join_text([{"text": "こんにちは"}, {"text": "世界"}]) == "こんにちは世界"
    assert render.join_text([{"text": " don"}, {"text": "'t"}]) == "don't"


def test_join_text_of_nothing_is_empty():
    assert render.join_text([]) == ""


# --- format_ts ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0:00"),
        (5, "0:05"),
        (59.9, "0:59"),
        (65, "1:05"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (36000, "10:00:00"),
    ],
)
def test_format_ts_clock(seconds, expected):
    assert render.format_ts(seconds) == expected
    assert render.format_ts(seconds, "clock") == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00,000"),
        (1.5, "00:00:01,500"),
        (65, "00:01:05,000"),
        (3661.0421, "01:01:01,042"),
    ],
)
def test_format_ts_srt(seconds, expected):
    assert render.format_ts(seconds, "srt") == expected


def test_format_ts_clamps_a_negative_rounding_error_to_zero():
    assert render.format_ts(-0.001) == "0:00"
    assert render.format_ts(-0.001, "srt") == "00:00:00,000"


def test_format_ts_of_none_is_empty():
    # media.duration is NULL until probe has run; a library row must still render.
    assert render.format_ts(None) == ""
    assert render.format_ts(None, "srt") == ""


def test_format_ts_rejects_an_unknown_style():
    with pytest.raises(ValueError):
        render.format_ts(1.0, "vogon")


# --- speaker_display ----------------------------------------------------------


def test_speaker_display_derives_a_one_based_number_from_the_cluster():
    assert render.speaker_display({}, "SPEAKER_03") == "Speaker 4"
    assert render.speaker_display({}, "SPEAKER_00") == "Speaker 1"
    assert render.speaker_display(None, "SPEAKER_11") == "Speaker 12"


def test_speaker_display_prefers_the_label():
    assert render.speaker_display({"SPEAKER_00": "Arthur"}, "SPEAKER_00") == "Arthur"


def test_speaker_display_falls_back_when_the_label_is_blank():
    assert render.speaker_display({"SPEAKER_00": "   "}, "SPEAKER_00") == "Speaker 1"


def test_speaker_display_passes_an_unfamiliar_cluster_through():
    assert render.speaker_display({}, "USER_1") == "USER_1"


def test_speaker_display_of_no_cluster_is_empty():
    assert render.speaker_display({}, None) == ""


# --- confidence_band ----------------------------------------------------------


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (None, "unknown"),
        (1.0, "high"),
        (0.85, "high"),
        (0.849, "mid"),
        (0.6, "mid"),
        (0.599, "low"),
        (0.0, "low"),
    ],
)
def test_confidence_band_boundaries(probability, expected):
    assert render.confidence_band(probability) == expected
