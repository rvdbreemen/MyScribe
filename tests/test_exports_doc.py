"""Phase 4 Task 1: the transcript document loader.

`doc.load` is the one place the exporters read the database (ADR-003): one
media, its current run (or a named one), and that run's words, segments and
speaker labels, handed out as one immutable value that every writer takes and
none may mutate. These tests seed rows with tests/seed.py and check what comes
back - the order, the fields, the mapping of labels, the frozen shape - and
that a media without a transcript is refused by name rather than exported as
an empty file. No GPU, no model, no pipeline.
"""

import dataclasses

import pytest

from scribe import db
from scribe.exports import doc
from seed import default_words, seed_media, seed_run


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


# `corrected_from` joined the set in Phase 6 Task 5: `load` reads `text`
# through the glossary's correction layer, and a word it changed carries what
# it used to say (None for every other word, and for every word here). The
# writers pick the fields they use by name, so the extra one reaches no export.
WORD_FIELDS = {"idx", "start", "end", "text", "probability", "speaker", "corrected_from"}


def test_load_returns_the_current_run_with_words_in_idx_order(conn):
    media_id = seed_media(conn, title="Guide", duration=30.0)
    words = default_words()
    # Written back to front on purpose: rowid order is not transcript order,
    # and the loader must sort by idx.
    run_id = seed_run(
        conn, media_id, words=list(reversed(words)), labels={"SPEAKER_00": "Arthur"}
    )

    loaded = doc.load(conn, media_id)

    assert isinstance(loaded, doc.TranscriptDoc)
    assert loaded.media["id"] == media_id
    assert loaded.run["id"] == run_id
    assert loaded.title == "Guide"
    assert loaded.duration == 30.0
    assert [w["idx"] for w in loaded.words] == list(range(len(words)))
    assert [w["text"] for w in loaded.words] == [w["text"] for w in words]
    assert [w["speaker"] for w in loaded.words] == [w["speaker"] for w in words]
    assert all(set(w) == WORD_FIELDS for w in loaded.words)
    assert loaded.labels == {"SPEAKER_00": "Arthur"}


def test_segments_come_in_idx_order_with_their_text(conn):
    media_id = seed_media(conn)
    seed_run(conn, media_id)  # the default transcript: four sentences, four segments

    loaded = doc.load(conn, media_id)

    assert [s["idx"] for s in loaded.segments] == [0, 1, 2, 3]
    assert loaded.segments[0]["text"].startswith("Don't panic")
    assert loaded.segments[0]["start"] == loaded.words[0]["start"]
    assert loaded.segments[-1]["end"] == loaded.words[-1]["end"]
    assert {"idx", "start", "end", "text"} <= set(loaded.segments[0])


def test_labels_are_only_those_of_the_loaded_run(conn):
    media_id = seed_media(conn)
    seed_run(conn, media_id, labels={"SPEAKER_00": "Zaphod"}, current=True)
    current = seed_run(
        conn, media_id, labels={"SPEAKER_00": "Arthur", "SPEAKER_01": "Ford"}, current=True
    )

    loaded = doc.load(conn, media_id)

    assert loaded.run["id"] == current
    assert loaded.labels == {"SPEAKER_00": "Arthur", "SPEAKER_01": "Ford"}


def test_a_media_without_a_run_raises_no_transcript(conn):
    media_id = seed_media(conn, title="Silent")

    with pytest.raises(doc.NoTranscript) as excinfo:
        doc.load(conn, media_id)

    assert str(media_id) in str(excinfo.value)


def test_a_media_whose_only_run_is_not_current_raises_no_transcript(conn):
    media_id = seed_media(conn)
    old = seed_run(conn, media_id, current=False)

    with pytest.raises(doc.NoTranscript):
        doc.load(conn, media_id)

    # ...but that run can still be asked for by id.
    assert doc.load(conn, media_id, run_id=old).run["id"] == old


def test_a_run_of_another_media_is_refused(conn):
    mine = seed_media(conn, title="Mine")
    theirs = seed_media(conn, title="Theirs")
    seed_run(conn, mine)
    foreign = seed_run(conn, theirs)

    with pytest.raises(doc.NoTranscript):
        doc.load(conn, mine, run_id=foreign)


def test_a_missing_media_raises_no_transcript(conn):
    with pytest.raises(doc.NoTranscript):
        doc.load(conn, 4242)


def test_the_document_is_frozen(conn):
    media_id = seed_media(conn)
    seed_run(conn, media_id)

    loaded = doc.load(conn, media_id)

    assert isinstance(loaded.words, tuple)
    assert isinstance(loaded.segments, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        loaded.title = "Something else"


def test_duration_falls_back_to_the_last_word_when_the_media_has_none(conn):
    media_id = seed_media(conn, duration=None)
    seed_run(conn, media_id)
    last = default_words()[-1]

    loaded = doc.load(conn, media_id)

    assert loaded.duration == pytest.approx(last["end"])


def test_load_writes_nothing(conn):
    """ADR-003: the exporters read word, segment and speaker_label and never write."""
    media_id = seed_media(conn)
    seed_run(conn, media_id, labels={"SPEAKER_00": "Arthur"})
    before = conn.total_changes

    doc.load(conn, media_id)

    assert conn.total_changes == before
