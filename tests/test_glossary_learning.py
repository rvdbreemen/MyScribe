"""A correction typed by a person teaches the glossary (TASK-087).

The evidence is the best there is: somebody read what Whisper produced, knew
it was wrong, and typed what it should say. What these tests hold is that the
lesson is drawn only where there is one, that it never grows a second copy of
a term, and that ADR-003 is untouched - `word.text` is what Whisper produced
and a correction remains a layer that can be lifted off.
"""

from __future__ import annotations

import pytest

from scribe import db, glossary
from tests import seed


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def run_id(conn):
    """One run whose words the tests correct, seeded the way every other
    suite seeds one - a media row with a real sha256 and the foreign keys the
    schema wants."""
    media_id = seed.seed_media(conn, title="Clip")
    return seed.seed_run(
        conn,
        media_id,
        words=[{"start": float(i), "end": i + 1.0, "text": t} for i, t in enumerate(WORDS)],
    )


WORDS = [" Vermulen,", " Vermulen", " Fermeulen", " teh ", " x ", " - ", " a "]
HEARD = {text.strip().strip(",-"): i for i, text in enumerate(WORDS)}


def term_names(conn) -> list[str]:
    return [t.term for t in glossary.terms(conn)]


def test_a_name_typed_over_a_mishearing_becomes_a_term(conn, run_id):
    """The flagship case: the term is what the person typed, the variant is
    what Whisper heard - the same pair `corrections_for` matches on."""
    glossary.correct_word(conn, run_id, 0, "Vermeulen")

    learned = glossary.terms(conn)
    assert [t.term for t in learned] == ["Vermeulen"]
    assert "Vermulen" in learned[0].variants


def test_a_common_word_teaches_nothing(conn, run_id):
    """"teh" corrected to "the" must not become a hotword biasing every future
    decode towards a word the decoder already knows."""
    glossary.correct_word(conn, run_id, HEARD["teh"], "the")

    assert term_names(conn) == []


def test_punctuation_and_single_letters_teach_nothing(conn, run_id):
    for heard, typed in (("x", "y"), ("", "."), ("a", "A")):
        glossary.correct_word(conn, run_id, HEARD.get(heard, 5), typed)

    assert term_names(conn) == []


def test_the_same_correction_twice_does_not_grow_a_second_term(conn, run_id):
    glossary.correct_word(conn, run_id, 0, "Vermeulen")
    glossary.correct_word(conn, run_id, 1, "Vermeulen")

    assert term_names(conn) == ["Vermeulen"]
    assert glossary.terms(conn)[0].variants.count("Vermulen") == 1


def test_a_second_mishearing_joins_the_term_it_belongs_to(conn, run_id):
    """One name, heard wrong in two ways, is one term with two variants."""
    glossary.correct_word(conn, run_id, 0, "Vermeulen")
    glossary.correct_word(conn, run_id, HEARD["Fermeulen"], "Vermeulen")

    learned = glossary.terms(conn)
    assert len(learned) == 1
    assert set(learned[0].variants) == {"Vermulen", "Fermeulen"}


def test_a_term_the_user_already_added_keeps_its_weight(conn, run_id):
    """A learned variant must not quietly demote a term somebody chose."""
    glossary.add(conn, "Vermeulen", weight=5.0, variants=["Vermuelen"])

    glossary.correct_word(conn, run_id, 0, "Vermeulen")

    learned = glossary.terms(conn)
    assert len(learned) == 1 and learned[0].weight == 5.0
    assert set(learned[0].variants) == {"Vermuelen", "Vermulen"}


def test_learning_leaves_the_transcript_exactly_as_whisper_made_it(conn, run_id):
    """ADR-003: a correction is never a rewrite, and learning from one cannot
    change that. Taking the correction away restores the word byte for byte."""
    glossary.correct_word(conn, run_id, 0, "Vermeulen")
    glossary.correct_word(conn, run_id, 0, "")  # the restore path

    with db.LOCK:
        row = conn.execute("SELECT text FROM word WHERE run_id=? AND idx=0", (run_id,)).fetchone()
        left = conn.execute("SELECT COUNT(*) FROM word_correction").fetchone()[0]
    assert row["text"] == " Vermulen,"
    assert left == 0
    assert term_names(conn) == ["Vermeulen"], "the lesson outlives the correction"


def test_what_was_learned_is_an_ordinary_glossary_term(conn, run_id):
    """It has to be removable: a term learned by accident is a term a person
    can delete, through the same door as any other."""
    glossary.correct_word(conn, run_id, 0, "Vermeulen")

    glossary.remove(conn, glossary.terms(conn)[0].id)

    assert term_names(conn) == []


def test_a_spelling_another_term_answers_for_is_not_taken(conn, run_id):
    """Found by the existing suite. Somebody types "Vermeulen-Smit" over one
    word; they mean that word. Keeping "Vermulen" as its variant would make
    every other "Vermulen" in the library come out hyphenated, including the
    ones an existing "Vermeulen" already fixes. The name still earns a hotword;
    the variant is what would have rewritten other people's sentences."""
    glossary.add(conn, "Vermeulen")

    glossary.correct_word(conn, run_id, 0, "Vermeulen-Smit")

    learned = {t.term: t.variants for t in glossary.terms(conn)}
    assert set(learned) == {"Vermeulen", "Vermeulen-Smit"}
    assert learned["Vermeulen-Smit"] == (), "it took a spelling that was not its to take"


def test_a_first_name_still_learns_its_variant(conn, run_id):
    """The guard above must not swallow the ordinary case: with nothing else
    in the glossary, the mishearing is exactly what should be remembered."""
    glossary.correct_word(conn, run_id, 0, "Vermeulen")

    assert glossary.terms(conn)[0].variants == ("Vermulen",)
