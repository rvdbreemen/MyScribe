"""Phase 6 Task 5: the glossary, on both sides of the decode.

The glossary is one list of terms spent twice. Before the decode it is
`hotwords` - a bias, moved here wholesale from `scribe.stages.transcribe` so
the composition and the correction pass read the same rows through the same
regex. After the decode it is a correction pass, and that half is where the
interesting constraint lives.

**ADR-003 is the whole design.** A correction is never a rewrite. The
`word_correction` rows are a layer keyed to `(run_id, word_idx)`; `word.text`
is what Whisper produced and stays that way forever. Readers apply the layer
with one LEFT JOIN, so `DELETE FROM word_correction WHERE run_id=?` restores
the original transcript byte for byte - which is what
`test_deleting_the_corrections_restores_the_original_render_byte_for_byte`
proves, twice: once through the transcript view and once through an export.

Three things measured here rather than assumed, because the plan's text and
the libraries disagree:

* `fuzz.WRatio` **needs a processor**. Without one it compares case-sensitively
  and scores "why cast" against "WHYcast" at 53.3 - below every threshold -
  so the flagship multi-word case would never fire. With
  `utils.default_process` it is 93.3.
* `fuzz.WRatio` alone **deletes words**. It scores a term merely *contained*
  in a longer window at 90 ("met Marieke" against "Marieke"), and accepting
  that would drop "met" from the transcript. Plain `fuzz.ratio` scores the
  same pair 77.8, and is what guards it.
* The plan's phonetic example is wrong on this machine: "Marijke" against
  "Marieke" is WRatio 85.7 - a *fuzzy* hit - and their metaphones differ
  ('MRJK' vs 'MRK'), so metaphone would not catch it either. "Zafod" for
  "Zaphod" is a real phonetic-only match: 72.7 and 'SFT' both ways.

Nothing here loads a model or touches the network.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from scribe import db, glossary, jobs, paths, render, runner
from scribe.app import create_app
from scribe.exports import doc as export_doc
from scribe.stages import TRANSCRIBE_STAGES, correct, transcribe
from scribe.web import transcript as transcript_ui

from tests import seed


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    return data


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "web.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    return path


@pytest.fixture
def client(db_path, data_dir):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def words_from(sentence: str, *, speaker: str | None = None) -> list[dict]:
    """One word row per token, each carrying its own leading space - the
    convention `transcribe._persist` stores and `render.join_text` relies on."""
    out: list[dict] = []
    for i, token in enumerate(sentence.split(" ")):
        start = i * 0.5
        out.append(
            {
                "idx": i,
                "start": start,
                "end": start + 0.4,
                "text": (" " if i else "") + token,
                "probability": 0.9,
                "speaker": speaker,
            }
        )
    return out


def term(name: str, *, weight: float = 1.0, variants: tuple[str, ...] = ()) -> glossary.Term:
    return glossary.Term(term=name, weight=weight, variants=variants)


# --- the term list ----------------------------------------------------------------


def test_a_term_added_comes_back_with_its_weight_and_variants(conn):
    glossary.add(conn, "WHYcast", weight=5.0, variants=("why cast", "wifecast"))

    (stored,) = glossary.terms(conn)

    assert stored.term == "WHYcast"
    assert stored.weight == 5.0
    assert stored.variants == ("why cast", "wifecast")


def test_terms_come_back_heaviest_first(conn):
    glossary.add(conn, "Aap")
    glossary.add(conn, "Noot", weight=5.0)
    glossary.add(conn, "Mies", weight=3.0)

    assert [t.term for t in glossary.terms(conn)] == ["Noot", "Mies", "Aap"]


def test_a_term_can_be_updated_without_touching_the_rest_of_the_row(conn):
    term_id = glossary.add(conn, "Marieke", weight=1.0, variants=("Marijke",))

    glossary.update(conn, term_id, weight=9.0)

    (stored,) = glossary.terms(conn)
    assert (stored.term, stored.weight, stored.variants) == ("Marieke", 9.0, ("Marijke",))


def test_removing_a_term_says_whether_there_was_one(conn):
    term_id = glossary.add(conn, "Marieke")

    assert glossary.remove(conn, term_id) is True
    assert glossary.remove(conn, term_id) is False
    assert glossary.terms(conn) == []


def test_import_takes_one_term_per_line_and_ignores_blanks(conn):
    added = glossary.import_terms(conn, "WHYcast\n\n  Marieke  \nZaphod\n")

    assert added == 3
    assert {t.term for t in glossary.terms(conn)} == {"WHYcast", "Marieke", "Zaphod"}


def test_import_does_not_duplicate_a_term_already_in_the_list(conn):
    glossary.add(conn, "Marieke", weight=7.0)

    added = glossary.import_terms(conn, "Marieke\nZaphod")

    assert added == 1
    assert {(t.term, t.weight) for t in glossary.terms(conn)} == {
        ("Marieke", 7.0),
        ("Zaphod", 1.0),
    }


def test_export_is_what_import_takes_back(conn):
    glossary.import_terms(conn, "Noot\nMies\nAap")

    assert glossary.export_terms(conn).splitlines() == ["Noot", "Mies", "Aap"]


# --- hotword composition, moved here from transcribe.py ----------------------------


def test_the_stage_still_composes_hotwords_through_the_same_function(conn):
    # The Phase 2 tests call transcribe.compose_hotwords; after the move it has
    # to be the very object glossary defines, not a second copy of it.
    assert transcribe.compose_hotwords is glossary.compose_hotwords
    assert transcribe.estimate_tokens is glossary.estimate_tokens
    assert transcribe.glossary_terms is glossary.glossary_terms
    assert transcribe.name_candidates is glossary.name_candidates


def test_the_name_regex_has_one_definition_that_both_ingest_doors_import():
    from scribe.ingest import urls

    assert urls._WORD is glossary._WORD
    assert transcribe._WORD is glossary._WORD


def test_extra_terms_ride_between_the_glossary_and_the_filename(conn):
    glossary.add(conn, "WHYcast")
    row = {"title": "raw-042", "orig_name": "raw-042.mp3"}

    hotwords = glossary.compose_hotwords(conn, row, extra_terms=("Zaphod", "Megadodo"))

    assert hotwords == "WHYcast, Zaphod, Megadodo"


def test_an_extra_term_already_in_the_glossary_is_not_repeated(conn):
    glossary.add(conn, "Zaphod")

    assert glossary.compose_hotwords(conn, {}, extra_terms=("zaphod",)) == "Zaphod"


def test_extra_terms_still_stop_at_the_token_budget(conn):
    hotwords = glossary.compose_hotwords(
        conn, {}, extra_terms=tuple(f"Term{n:03d}" for n in range(400))
    )

    assert glossary.estimate_tokens(hotwords) <= glossary.HOTWORD_TOKEN_LIMIT


# --- corrections_for: the two rules ------------------------------------------------


def test_a_close_misspelling_is_corrected_and_the_row_records_both_forms():
    words = words_from("Ik sprak Vermulen gisteren.")

    (fix,) = glossary.corrections_for(words, [term("Vermeulen")])

    assert fix.word_idx == 2
    assert fix.original == " Vermulen"
    assert fix.corrected == " Vermeulen"
    assert fix.rule == glossary.RULE_FUZZY
    assert fix.confidence > 0.85


def test_a_phonetic_only_match_is_caught_and_marked_with_that_rule():
    # 72.7 on the fuzzy score - below the fuzzy threshold, above the floor -
    # and metaphone 'SFT' both ways. Nothing but the phonetic rule catches it.
    words = words_from("Zafod kwam langs.")

    (fix,) = glossary.corrections_for(words, [term("Zaphod")])

    assert fix.corrected == " Zaphod" or fix.corrected == "Zaphod"
    assert fix.rule == glossary.RULE_PHONETIC
    assert 0.70 <= fix.confidence < 0.85


def test_a_word_below_both_thresholds_is_left_alone():
    words = words_from("De vergadering begint.")

    assert glossary.corrections_for(words, [term("Marieke")]) == []


def test_a_word_that_already_is_the_term_gets_no_correction_row():
    words = words_from("Marieke sprak.")

    assert glossary.corrections_for(words, [term("Marieke")]) == []


def test_a_word_whose_text_a_person_edited_is_never_corrected():
    """The flag is `text_edited_by_user`, and the distinction is the whole
    point of it: a person who retyped a word outranks any glossary, but a
    person who moved that word to another speaker said nothing about its
    spelling. Until this test was renamed it read `edited_by_user`, which is
    the column the reassignment route writes - so it pinned the guard to a
    fact about speakers and passed anyway. See db.py's v9 comment."""
    words = words_from("Ik sprak Vermulen gisteren.")
    words[2]["text_edited_by_user"] = 1

    assert glossary.corrections_for(words, [term("Vermeulen")]) == []


def test_a_word_whose_speaker_a_person_reassigned_is_still_corrected():
    """The other half of the same distinction, and the bug CR-004 found: a
    range whose diarization somebody fixed by hand must stay correctable. Its
    text is untouched by that edit (ADR-003), so the glossary has as much to
    say about it as it ever did."""
    words = words_from("Ik sprak Vermulen gisteren.")
    words[2]["edited_by_user"] = 1  # what /media/{id}/words/reassign writes

    (fix,) = glossary.corrections_for(words, [term("Vermeulen")])

    assert (fix.word_idx, fix.corrected) == (2, " Vermeulen")


def test_a_variant_spelling_maps_onto_the_canonical_term():
    words = words_from("De wifecast van vandaag.")

    (fix,) = glossary.corrections_for(words, [term("WHYcast", variants=("wifecast",))])

    assert fix.corrected == " WHYcast"


def test_punctuation_around_a_corrected_word_survives():
    words = words_from("Dag Vermulen, tot ziens.")

    (fix,) = glossary.corrections_for(words, [term("Vermeulen")])

    assert fix.corrected == " Vermeulen,"


def test_a_plural_of_a_term_is_corrected_and_that_is_the_choice_not_an_accident():
    """"mariekes" scores 93.3 against "Marieke" on both scorers, so the fuzzy
    rule takes it and the plural's "s" goes. Pinned rather than left to a
    future threshold tweak to change quietly: a glossary is a statement that
    this spelling is wanted, the correction is visible in the transcript, and
    one DELETE takes it back. If the trade ever stops being worth it, this is
    the test that has to be argued with first."""
    words = words_from("Twee mariekes op een dag.")

    (fix,) = glossary.corrections_for(words, [term("Marieke")])

    assert fix.corrected == " Marieke"
    assert fix.rule == glossary.RULE_FUZZY


def test_a_correction_never_swallows_the_words_around_it():
    # WRatio scores "met Marieke" against "Marieke" at 90 because the term is
    # contained in the window. Accepting that would delete the word "met".
    words = words_from("Ik sprak met Marieke gisteren.")

    assert glossary.corrections_for(words, [term("Marieke")]) == []


# --- corrections_for: windows of more than one word --------------------------------


def test_two_words_whisper_split_are_joined_back_into_one_term():
    words = words_from("Welkom bij why cast vandaag.")

    fixes = glossary.corrections_for(words, [term("WHYcast")])

    assert [(f.word_idx, f.corrected) for f in fixes] == [(2, " WHYcast"), (3, "")]


def test_a_split_term_keeps_the_punctuation_of_its_last_word():
    words = words_from("Welkom bij why cast.")

    fixes = glossary.corrections_for(words, [term("WHYcast")])

    assert [(f.word_idx, f.corrected) for f in fixes] == [(2, " WHYcast."), (3, "")]


def test_a_two_word_term_is_matched_as_two_words():
    words = words_from("Ik sprak Marijke Vermulen gisteren.")

    fixes = glossary.corrections_for(words, [term("Marieke Vermeulen")])

    assert [(f.word_idx, f.corrected) for f in fixes] == [
        (2, " Marieke Vermeulen"),
        (3, ""),
    ]


def test_a_window_containing_a_word_whose_text_was_edited_is_not_corrected():
    """A multi-word correction replaces the whole window, so it would replace
    the hand-typed word in the middle of it too. Same flag, same reason as
    `test_a_word_whose_text_a_person_edited_is_never_corrected`, and it was
    keyed on the wrong column for the same reason."""
    words = words_from("Welkom bij why cast vandaag.")
    words[3]["text_edited_by_user"] = 1

    assert glossary.corrections_for(words, [term("WHYcast")]) == []


def test_a_split_term_renders_exactly_as_the_unsplit_one_would():
    """The reason the correction lands on the *first* word of the window and
    the rest go empty, and the reason `render` had to learn about an empty
    word: a blanked word standing between two others must not swallow the
    sentence break that the word before it earned."""
    words = words_from("Hallo daar. why cast. Tot ziens.")
    fixes = glossary.corrections_for(words, [term("WHYcast")])
    corrected = apply_in_memory(words, fixes)

    assert render.join_text(corrected) == "Hallo daar. WHYcast. Tot ziens."
    assert sentences_of(corrected) == ["Hallo daar.", "WHYcast.", "Tot ziens."]


def _read(words, terms):
    return render.join_text(apply_in_memory(words, glossary.corrections_for(words, terms)))


def test_a_term_already_spelled_across_two_words_is_not_corrected_inside():
    """TASK-103. Whisper can emit the second half of a word without a leading
    space, so " My" + "Scribe" already reads "MyScribe". That two-word window
    is already right; it used to be dropped, which left "Scribe" (85.7
    against "MyScribe") free to be corrected on its own: "MyMyScribe"."""
    assert _read(_words([" in", " My", "Scribe"]), [term("MyScribe")]) == "in MyScribe"
    assert _read(_words(["My", "Scribe"]), [term("MyScribe")]) == "MyScribe"
    # The stored shape with a space between them was never affected; pinned.
    assert _read(words_from("in My Scribe"), [term("MyScribe")]) == "in MyScribe"


def test_an_already_spelled_term_blocks_its_own_prefix_and_suffix_too():
    """The same bug from both ends: "Notebook" alone is 88.9 against
    "NotebookLM" and "Smithson" alone 88.9 against "J Smithson"."""
    assert _read(_words([" via", " Notebook", "LM"]), [term("NotebookLM")]) == "via NotebookLM"
    assert _read(words_from("met J Smithson dus"), [term("J Smithson")]) == "met J Smithson dus"


def test_a_longer_correction_still_beats_a_shorter_term_already_spelled():
    """An exact "Marieke" claims one word; a two-word correction starting at
    the same word reaches past it and must still win."""
    words = words_from("Ik sprak Marieke Vermulen gisteren.")

    assert _read(words, [term("Marieke"), term("Marieke Vermeulen")]) == (
        "Ik sprak Marieke Vermeulen gisteren."
    )


@pytest.mark.parametrize(
    "sentence",
    ["Ik sprak Jansen u gisteren", "Ik sprak Janssen u gisteren"],
)
def test_a_name_spelled_as_one_term_is_left_alone_next_to_its_near_twin(sentence):
    """A glossary with Jansen and Janssen. Each is a wanted spelling, so a
    word already spelled as either is left as it is - and the short word after
    it stays. "Janssen u" is 87.5 against "Janssen", and 80 against "Jansen"
    with the same metaphone (JNSN): either used to swallow the "u"."""
    assert _read(words_from(sentence), [term("Jansen"), term("Janssen")]) == sentence


@pytest.mark.parametrize(
    "sentence",
    ["Ik sprak Thomas u gisteren", "Ik sprak Tomas u gisteren"],
)
def test_thomas_and_tomas_both_stay_and_keep_the_word_after_them(sentence):
    """"Tomas u" is 83.3 against "Tomas" and sounds the same, so it was
    claimed as a phonetic correction that emptied "u"."""
    assert _read(words_from(sentence), [term("Thomas"), term("Tomas")]) == sentence


@pytest.mark.parametrize(
    ("sentence", "name"),
    [
        ("Ik sprak Vermeulen en Piet", "Vermeulen"),
        ("Ik vind NotebookLM is goed", "NotebookLM"),
        ("Ik woon Amsterdam en Piet", "Amsterdam"),
    ],
)
def test_a_long_term_spelled_right_does_not_swallow_the_short_word_after_it(sentence, name):
    """A long name plus a two-letter word still scores 85.7-87.0 against the
    name alone - at the threshold - so the pair was "corrected" to the name
    and the short word deleted. Older than TASK-103."""
    assert _read(words_from(sentence), [term(name)]) == sentence


@pytest.mark.parametrize(
    ("sentence", "terms", "expected"),
    [
        ("Ik woon in Amsterdam.", ["Amsterdam"], "Ik woon in Amsterdam."),
        (
            "Ik sprak met Marieke Vermeulen gisteren.",
            ["Marieke Vermeulen"],
            "Ik sprak met Marieke Vermeulen gisteren.",
        ),
        ("Dag u Jansen gisteren", ["Jansen", "Janssen"], "Dag u Jansen gisteren"),
        ("Dag u Jansen gisteren", ["Janssen"], "Dag u Janssen gisteren"),
    ],
)
def test_the_word_before_a_term_is_not_swallowed_by_it(sentence, terms, expected):
    """TASK-110. "in amsterdam" is 90 against "Amsterdam" on WRatio and 85.7
    on ratio - at the threshold - so the two-word window was replaced by the
    name and "in" was gone. The window holds the term, it is not the term."""
    assert _read(words_from(sentence), [term(t) for t in terms]) == expected


@pytest.mark.parametrize(
    ("sentence", "terms", "expected"),
    [
        ("Zie Git Hub vandaag", ["Git", "GitHub"], "Zie GitHub vandaag"),
        ("Zie My Scribe vandaag", ["My", "MyScribe"], "Zie MyScribe vandaag"),
        ("Zie Mi Scribe vandaag", ["MyScribe"], "Zie MyScribe vandaag"),
        ("Zie Notebook LM vandaag", ["Notebook", "NotebookLM"], "Zie NotebookLM vandaag"),
        ("Zie Jan sen vandaag", ["Jan", "Jansen"], "Zie Jansen vandaag"),
    ],
)
def test_a_split_term_is_joined_even_when_its_first_half_is_a_term(sentence, terms, expected):
    """"Git" is spelled right and claims its word, but "Git Hub" is the
    longer window and both its words are part of "GitHub"."""
    assert _read(words_from(sentence), [term(t) for t in terms]) == expected


@pytest.mark.parametrize(
    ("sentence", "name", "expected"),
    [
        ("Zie Mai Scribe vandaag", "MyScribe", "Zie MyScribe vandaag"),
        ("Zie Mei Scribe vandaag", "MyScribe", "Zie MyScribe vandaag"),
        ("Zie Pie Torch vandaag", "PyTorch", "Zie PyTorch vandaag"),
        ("Zie pie torch vandaag", "PyTorch", "Zie PyTorch vandaag"),
    ],
)
def test_a_split_term_whose_first_half_only_sounds_right_is_joined(sentence, name, expected):
    """"Mai" against the leftover "my" is 40 on spelling, but both are 'M' on
    metaphone; the edge word is accounted for by sound. Refused, the lone
    "Scribe" was respelled on its own: "Mai MyScribe" (the TASK-103 shape)."""
    assert _read(words_from(sentence), [term(name)]) == expected


@pytest.mark.parametrize(
    ("sentence", "name", "expected"),
    [
        ("Zie Linked In vandaag", "LinkedIn", "Zie LinkedIn vandaag"),
        ("Zie Dendermon de vandaag", "Dendermonde", "Zie Dendermonde vandaag"),
        ("Zie Tint in vandaag", "Tintin", "Zie Tintin vandaag"),
    ],
)
def test_a_split_term_is_cut_where_its_halves_meet_not_where_a_half_first_occurs(
    sentence, name, expected
):
    """"in" first occurs at the start of "linkedin" ("l-IN-kedin"); aligned
    there, the leftover for "Linked" was "l" (28.6) and the join was refused.
    The rest of the window is the start (or the end) of the term, so the cut
    is anchored to that end."""
    assert _read(words_from(sentence), [term(name)]) == expected


@pytest.mark.parametrize("short", ["u", "en", "a", "in", "is"])
@pytest.mark.parametrize(
    "name", ["MyScribe", "PyTorch", "LinkedIn", "Dendermonde", "Amsterdam", "Vermeulen"]
)
def test_a_short_word_next_to_a_term_is_never_joined_to_it(short, name):
    """Neither the anchored cut nor the sound rule may let a function word
    become a piece of the term it stands next to (TASK-110)."""
    for sentence in (f"Ik zag {short} {name} gisteren", f"Ik zag {name} {short} gisteren"):
        assert _read(words_from(sentence), [term(name)]) == sentence


def test_a_term_spelled_right_is_not_joined_to_the_next_word_on_sound_alone():
    """With "Dirk" and "Dirks" listed, "Dirk is" is 83.3 against "Dirks" and
    sounds like it (TRKS), but "is" against the leftover "s" is 66.7 on
    spelling - under `PAST_CLAIM_FLOOR`. That is not enough to respell a name
    already spelled right and drop "is"."""
    sentence = "Ik zag Dirk is gisteren"

    assert _read(words_from(sentence), [term("Dirk"), term("Dirks")]) == sentence


@pytest.mark.parametrize(
    ("first", "patronymic", "sentence"),
    [
        ("Peter", "Peters", "Peter is ziek"),
        ("Simon", "Simons", "Simon is thuis"),
        ("Willem", "Willems", "Willem is thuis"),
        ("Jacob", "Jacobs", "Jacob is thuis"),
        ("Robert", "Roberts", "Robert is thuis"),
    ],
)
def test_a_first_name_next_to_its_listed_patronymic_keeps_the_verb(first, patronymic, sentence):
    """With "Peter" and "Peters" both listed, "Peter is" is a fuzzy match for
    "Peters" (the "is" leaves "s", 66.7 on spelling) and a correction over
    more words than the exact "Peter" claims, so without a floor it wins:
    "Peters ziek". Before TASK-103 the name was respelled but "is" kept.
    An exact listed spelling is left alone,
    and a word past it joins the term only if it is spelled as the rest."""
    assert _read(words_from(sentence), [term(first), term(patronymic)]) == sentence


def test_a_lone_listed_patronymic_still_respells_the_first_name():
    """No claim without "Peter" listed: "Peter" alone is the fuzzy match for
    "Peters" and "is" stays, as before TASK-103."""
    assert _read(words_from("Peter is ziek"), [term("Peters")]) == "Peters is ziek"


def test_a_word_with_a_vowel_does_not_sound_like_a_leftover_without_one():
    """With "Sanders" listed, "zo anders" leaves "s" for "zo": nothing alike
    on spelling, but both are 'S' on metaphone, which drops the vowel, so
    "zo" was joined and deleted. "anders" alone is still a fuzzy match for
    "Sanders"."""
    assert _read(words_from("dat is zo anders"), [term("Sanders")]) == "dat is zo Sanders"


@pytest.mark.parametrize(
    ("sentence", "name", "expected"),
    [
        ("Ik zag Z interklaas vandaag", "Sinterklaas", "Ik zag Sinterklaas vandaag"),
        ("Ik zag F ermeulen vandaag", "Vermeulen", "Ik zag Vermeulen vandaag"),
        ("Ik zag S andvoort vandaag", "Zandvoort", "Ik zag Zandvoort vandaag"),
    ],
)
def test_a_one_letter_split_without_a_vowel_still_joins_on_sound(sentence, name, expected):
    """The vowel rule, not a length rule: "Z" against the leftover "s" has no
    vowel to lose and sounds the same. Refusing every one-letter leftover
    left the piece standing next to the respelled rest: "Z Sinterklaas"."""
    assert _read(words_from(sentence), [term(name)]) == expected


@pytest.mark.parametrize(
    ("sentence", "terms"),
    [
        ("We sent Marie a card", ["Marie", "Maria"]),
        ("I gave Anne a book yesterday", ["Anne", "Anna"]),
        ("ik zag Lise a daar", ["Lise", "Lisa"]),
        ("We sent Sophie a card", ["Sophie", "Sophia"]),
        ("ik zag Sophia e daar", ["Sophia", "Sophie"]),
        ("We sent Louise a card", ["Louise", "Louisa"]),
    ],
)
def test_a_listed_name_is_not_respelled_as_another_listed_name_past_its_article(
    sentence, terms
):
    """"Marie a" sounds like "Maria" (83.3 on spelling, below the fuzzy
    threshold), and "a" is exactly the rest of it. Only a fuzzy correction
    beats a claim, so with both names listed "Marie" keeps its word and "a"
    stays. Before TASK-103 this read "Marie card"; letting a phonetic join beat the
    claim read "Maria card" - the article gone and the wrong person named.
    "Sophie a" is a fuzzy match for "Sophia", but a correction past a claim
    must extend the claimed term, not respell its end."""
    assert _read(words_from(sentence), [term(t) for t in terms]) == sentence


@pytest.mark.parametrize(
    ("sentence", "terms", "expected"),
    [
        (
            "met Jose Mourino en",
            [term("Jose"), term("José Mourinho")],
            "met José Mourinho en",
        ),
        (
            "met Mai Scribe en",
            [term("Mai"), term("MyScribe", variants=("Mai Scribe",))],
            "met MyScribe en",
        ),
    ],
)
def test_a_correction_past_a_claim_extends_it_up_to_accents_and_variants(
    sentence, terms, expected
):
    """"Jose" is the start of "José Mourinho" once the accent is set aside,
    and "Mai" the start of the variant "Mai Scribe" that matched. Comparing
    only against the canonical spelling refused both, and the variant case
    read "Mai MyScribe"."""
    assert _read(words_from(sentence), terms) == expected


def test_an_accented_vowel_is_a_vowel():
    """"zó" (emphatic, as Whisper may write it) has a vowel; without one it
    would be 'S' against the "s" of "Sanders" and be deleted."""
    assert _read(words_from("dat is zó anders"), [term("Sanders")]) == "dat is zó Sanders"


# --- TASK-111: a lone function word, a middle word, a name and its "en" -------------


@pytest.mark.parametrize(
    ("sentence", "name"),
    [
        ("Ik zag het huis op de berg in Utrecht", "Hetty"),
        ("Ik zag het huis op de berg in Utrecht", "Opa"),
        ("Ik zag het huis op de berg in Utrecht", "Ina"),
        ("Dat is er niet", "Isa"),
        ("Dat is er niet", "Era"),
        ("Jan en Piet", "Ena"),
        ("Hij liep door de deur maar niet verder", "Doori"),
        ("Hij liep door de deur maar niet verder", "Maars"),
        ("the cat and the dog", "Thea"),
        ("the cat and the dog", "Anda"),
        ("it is on the table at noon", "Ito"),
        ("it is on the table at noon", "Ona"),
        ("it is on the table at noon", "Ata"),
    ],
)
def test_a_lone_function_word_is_never_respelled_as_a_short_term(sentence, name):
    """TASK-111. A one-word window only has to clear `fuzz.ratio` 85, or 70
    with the same metaphone, and a two- or three-letter word against a name
    one or two letters longer lands there: "het" is 75 against "Hetty" and
    'HT' both ways, "the" 85.7 against "Thea". So "Ik zag het huis" read
    "Ik zag Hetty huis". "door" and "maar" are four letters; the vendored
    function-word list is what keeps them."""
    assert _read(words_from(sentence), [term(name)]) == sentence


def test_a_lone_word_of_three_letters_is_not_respelled_as_a_longer_name():
    """The length guard, for words that are on no list: "Ari" is 85.7 against
    "Arie". A lone word of three letters or fewer is corrected only when it is
    spelled as a listed variant - the cost, measured in the TASK-111 sweep, is
    that short names are no longer guessed."""
    assert _read(words_from("Ik zag Ari gisteren"), [term("Arie")]) == "Ik zag Ari gisteren"


@pytest.mark.parametrize(
    ("sentence", "entry", "expected"),
    [
        ("Ik zag het huis", term("Hetty", variants=("het",)), "Ik zag Hetty huis"),
        ("Het huis", term("Hetty", variants=("het",)), "Hetty huis"),
        ("Ik zag Ari gisteren", term("Arie", variants=("Ari",)), "Ik zag Arie gisteren"),
    ],
)
def test_a_short_word_listed_as_a_variant_is_still_corrected(sentence, entry, expected):
    """A variant is the user saying that this spelling means that name, so
    the guards stand aside for it."""
    assert _read(words_from(sentence), [entry]) == expected


@pytest.mark.parametrize(
    ("sentence", "entry", "expected"),
    [
        ("de api werkt", term("API"), "de api werkt"),
        ("de gpu is heet", term("GPU"), "de gpu is heet"),
        ("Ik ben er", term("Ben"), "Ik ben er"),
        ("de api werkt", term("API", variants=("api",)), "de API werkt"),
    ],
)
def test_a_short_terms_capitals_are_fixed_only_through_a_listed_variant(
    sentence, entry, expected
):
    """The disclosed cost of the length guard: the listed term itself is not
    a variant, so "api" stays lower case with "API" listed - counting it
    would also turn "Ik ben er" into "Ik Ben er" with "Ben" listed. Listing
    the lower-case spelling as a variant fixes the capitals again."""
    assert _read(words_from(sentence), [entry]) == expected


def test_a_short_term_spelled_right_still_claims_its_word():
    """The guards stop a correction, never a claim: an exact "Ida" still
    holds its word, so nothing else may be written over it (TASK-103)."""
    words = words_from("Ik zag Ida en Eva")

    assert glossary.corrections_for(words, [term("Ida"), term("Eva")]) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Marieke en Vermeulen kwamen",
        "Ik zag Marieke het Vermeulen gisteren",
        "Ik zag Marieke op Vermeulen gisteren",
    ],
)
def test_a_middle_word_that_is_not_a_piece_of_the_term_is_kept(sentence):
    """TASK-111. `_covers` checked the first and the last word of a window,
    never the one between them: "Marieke en Vermeulen" scores above the
    threshold against "Marieke Vermeulen", both edges are pieces of it, and
    "en" was deleted. Anchored to the first and the last word,
    "marieke vermeulen" leaves only the space for the middle word."""
    assert _read(words_from(sentence), [term("Marieke Vermeulen")]) == sentence


@pytest.mark.parametrize(
    ("sentence", "name", "expected"),
    [
        ("Ik zag Jan de Fries gisteren", "Jan de Vries", "Ik zag Jan de Vries gisteren"),
        ("Ik zag Anna de Boer gisteren", "Anna de Boer", "Ik zag Anna de Boer gisteren"),
        ("Ik zag Mar ie ke gisteren", "Marieke", "Ik zag Marieke gisteren"),
        (
            "Ik zag Ma rieke Vermeulen gisteren",
            "Marieke Vermeulen",
            "Ik zag Marieke Vermeulen gisteren",
        ),
    ],
)
def test_a_term_whose_middle_word_is_a_piece_of_it_is_still_corrected(sentence, name, expected):
    """The control for the middle-word test: a real three-word split, or a
    name with "de" in it, still matches."""
    assert _read(words_from(sentence), [term(name)]) == expected


@pytest.mark.parametrize(
    ("sentence", "terms"),
    [
        ("Niels en Peter en ik", ["Niels", "Nielsen", "Peter", "Petersen"]),
        ("Henk een boek", ["Henk", "Henken"]),
        ("Henk den Uyl", ["Henk", "Henken"]),
        ("Karel en ik", ["Karel", "Karelsen"]),
    ],
)
def test_a_listed_name_keeps_the_function_word_after_it(sentence, terms):
    """TASK-111, Robert's decision (option b, 2026-10-24): with "Niels" and
    "Nielsen" both listed, "Niels en" is 93.3 against "Nielsen" and reached
    past the claim of "Niels", so "Niels en Peter en ik" read "Nielsen
    Petersen ik". When the head is itself a listed term and the word past it
    is a function word, the join is refused."""
    assert _read(words_from(sentence), [term(t) for t in terms]) == sentence


@pytest.mark.parametrize(
    ("sentence", "terms", "expected"),
    [
        ("Jan de Fries kwam", ["Jan", "Jan de Vries"], "Jan de Vries kwam"),
        ("Henk van Dijck kwam", ["Henk", "Henk van Dijk"], "Henk van Dijk kwam"),
        ("Jan de Fries kwam", ["Jan de Vries"], "Jan de Vries kwam"),
    ],
)
def test_a_listed_first_name_does_not_stop_its_full_name_with_a_tussenvoegsel(
    sentence, terms, expected
):
    """Option b refuses a join when the leftover past the claim is a function
    word ("Niels en"), not whenever a function word is somewhere past it.
    With "Jan" and "Jan de Vries" both listed, the leftover of "Jan de Fries"
    is "de Fries": the name still needs its correction (review of 8ebaf4b)."""
    assert _read(words_from(sentence), [term(t) for t in terms]) == expected


@pytest.mark.parametrize(
    ("sentence", "name", "expected"),
    [
        ("Niels en ik", "Nielsen", "Nielsen ik"),
        ("Henk een boek", "Henken", "Henken boek"),
        ("Henk den boek", "Henken", "Henken boek"),
        ("Henk men boek", "Henken", "Henken boek"),
    ],
)
def test_a_name_and_en_are_still_joined_when_only_the_longer_name_is_listed(
    sentence, name, expected
):
    """The other half of option b: only the longer name listed, nothing
    claims the head, and the text is the same as a split "Niels en". Joined,
    as before; only the audio could tell. "een", "den" and "men" are 80
    against the leftover "en", which is exactly `FUNCTION_WORD_EDGE_FLOOR`."""
    assert _read(words_from(sentence), [term(name)]) == expected


@pytest.mark.parametrize(
    ("sentence", "name"),
    [
        ("Marieke are Vermeulen kwamen", "Marieke Vermeulen"),
        ("Ik zag are Vermeulen", "Marieke Vermeulen"),
        ("Anna er Boer kwamen", "Anna Boer"),
    ],
)
def test_a_function_word_is_a_piece_of_a_term_only_when_spelled_as_that_piece(
    sentence, name
):
    """TASK-111. `EDGE_WORD_FLOOR` (50) lets "are" stand for the "marieke" of
    "Marieke Vermeulen" (60) and "er" for the "boer" of "Anna Boer" (66.7).
    With the middle-word check refusing "Marieke are Vermeulen", the
    two-word window behind it won and the name was doubled: "Marieke Marieke
    Vermeulen kwamen". A function word at an edge must be spelled as its
    piece of the term (`FUNCTION_WORD_EDGE_FLOOR`), and sound is not enough."""
    assert _read(words_from(sentence), [term(name)]) == sentence


def apply_in_memory(words: list[dict], fixes: list[glossary.Correction]) -> list[dict]:
    """The read-time join, done by hand: what the LEFT JOIN produces."""
    by_idx = {fix.word_idx: fix.corrected for fix in fixes}
    return [{**w, "text": by_idx.get(w["idx"], w["text"])} for w in words]


def sentences_of(words: list[dict]) -> list[str]:
    return [s.text for p in render.paragraphs(words) for s in p.sentences]


def test_render_ignores_an_empty_word_when_it_looks_for_the_next_one():
    words = words_from("Hallo daar. Tot ziens.")
    blanked = [{**w, "text": ""} if w["idx"] == 2 else w for w in words]

    assert sentences_of(blanked) == ["Hallo daar.", "ziens."]


# --- storing the layer --------------------------------------------------------------


def seeded(conn, sentence="Ik sprak Vermulen gisteren."):
    media_id = seed.seed_media(conn, title="Gesprek")
    run_id = seed.seed_run(conn, media_id, words=words_from(sentence))
    return media_id, run_id


def test_storing_corrections_writes_one_row_per_word_and_nothing_else(conn):
    _media_id, run_id = seeded(conn)
    fixes = glossary.corrections_for(
        glossary.words_of(conn, run_id), [term("Vermeulen")]
    )

    assert glossary.store(conn, run_id, fixes) == 1

    (row,) = glossary.stored(conn, run_id)
    assert (row["word_idx"], row["original"], row["corrected"]) == (
        2,
        " Vermulen",
        " Vermeulen",
    )
    assert row["rule"] == glossary.RULE_FUZZY
    assert row["created_at"] > 0


def test_storing_twice_replaces_the_layer_instead_of_doubling_it(conn):
    _media_id, run_id = seeded(conn)
    fixes = glossary.corrections_for(
        glossary.words_of(conn, run_id), [term("Vermeulen")]
    )

    glossary.store(conn, run_id, fixes)
    glossary.store(conn, run_id, fixes)

    assert len(glossary.stored(conn, run_id)) == 1


def test_storing_an_empty_layer_clears_the_one_that_was_there(conn):
    _media_id, run_id = seeded(conn)
    glossary.store(
        conn, run_id, glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")])
    )

    glossary.store(conn, run_id, [])

    assert glossary.stored(conn, run_id) == []


def test_a_second_writer_waits_for_the_first_instead_of_doubling_the_layer(tmp_path):
    """`db.LOCK` is in-process, so this is the cross-process question ADR-002
    answers with SQL: two app instances on one database (4242 and 4299, say)
    have a supervisor each, and one can be running a `correct` job while the
    other's pipeline runs the `correct` stage on the same run.

    Measured rather than argued: `store`'s DELETE and its INSERTs are one
    deferred transaction, so the write lock is taken at the DELETE and held to
    the commit. A second writer waits out `busy_timeout` and then does its own
    delete-and-insert over the first one's rows. It cannot see them half
    written, and the UNIQUE index cannot fire.
    """
    path = tmp_path / "two.db"
    first = db.connect(path)
    db.migrate(first)
    media_id, run_id = seeded(first)
    second = db.connect(path)
    first.execute("BEGIN IMMEDIATE")
    first.execute(
        "INSERT INTO word_correction(run_id, word_idx, original, corrected, rule,"
        " confidence, created_at) VALUES (?, 0, 'a', 'b', 'fuzzy', 1.0, 0)",
        (run_id,),
    )

    done: list = []

    def write() -> None:
        try:
            done.append(
                glossary.store(
                    second, run_id, [glossary.Correction(2, " Vermulen", " Vermeulen", "fuzzy", 0.9)]
                )
            )
        except BaseException as exc:  # pragma: no cover - the failure this pins
            done.append(exc)

    writer = threading.Thread(target=write)
    writer.start()
    time.sleep(0.3)  # long enough that the writer is certainly inside the wait
    first.commit()  # the first writer lets go
    writer.join(timeout=15)

    assert done == [1], f"the second writer did not get through: {done}"
    assert [row["word_idx"] for row in glossary.stored(first, run_id)] == [2]
    first.close()
    second.close()


def test_the_word_table_itself_is_never_touched(conn):
    _media_id, run_id = seeded(conn)
    before = raw_text(conn, run_id)

    glossary.store(
        conn, run_id, glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")])
    )

    assert raw_text(conn, run_id) == before
    assert " Vermulen" in before


def raw_text(conn: sqlite3.Connection, run_id: int) -> str:
    """The stored words, read without the correction join. ADR-003's canon."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT text FROM word WHERE run_id=? ORDER BY idx", (run_id,)
        ).fetchall()
    return "".join(row["text"] for row in rows)


def test_the_pass_reads_the_text_edit_flag_out_of_the_database(conn):
    """`words_of` is the reason the pass reads the table instead of taking the
    stage's handoff: the flag only exists there. It has to be the *text* flag
    that travels, which is what this asks the whole way down - column, SELECT,
    window builder."""
    _media_id, run_id = seeded(conn)
    with db.LOCK:
        conn.execute(
            "UPDATE word SET text_edited_by_user=1 WHERE run_id=? AND idx=2", (run_id,)
        )
        conn.commit()

    assert glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")]) == []


# --- applying the layer at read time -------------------------------------------------


def test_the_transcript_view_shows_the_corrected_text(conn):
    _media_id, run_id = seeded(conn)
    glossary.store(
        conn, run_id, glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")])
    )

    words = transcript_ui.run_words(conn, run_id)

    assert render.join_text(words) == "Ik sprak Vermeulen gisteren."


def test_a_corrected_word_carries_what_it_used_to_say(conn):
    _media_id, run_id = seeded(conn)
    glossary.store(
        conn, run_id, glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")])
    )

    words = transcript_ui.run_words(conn, run_id)

    assert words[2]["corrected_from"] == " Vermulen"
    assert words[0]["corrected_from"] is None


def test_the_export_document_reads_the_corrected_text_too(conn):
    media_id, run_id = seeded(conn)
    glossary.store(
        conn, run_id, glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")])
    )

    document = export_doc.load(conn, media_id)

    assert render.join_text(document.words) == "Ik sprak Vermeulen gisteren."


def test_deleting_the_corrections_restores_the_original_render_byte_for_byte(conn):
    media_id, run_id = seeded(conn, "Welkom bij why cast. Hallo Vermulen, tot ziens.")
    before_view = transcript_ui.run_words(conn, run_id)
    before_export = export_doc.load(conn, media_id).words
    glossary.store(
        conn,
        run_id,
        glossary.corrections_for(
            glossary.words_of(conn, run_id), [term("WHYcast"), term("Vermeulen")]
        ),
    )
    assert render.join_text(transcript_ui.run_words(conn, run_id)) != render.join_text(
        before_view
    )

    glossary.clear(conn, run_id)

    after_view = transcript_ui.run_words(conn, run_id)
    after_export = export_doc.load(conn, media_id).words
    assert [w["text"] for w in after_view] == [w["text"] for w in before_view]
    assert render.join_text(after_view) == render.join_text(before_view)
    assert sentences_of(after_view) == sentences_of(before_view)
    assert [w["text"] for w in after_export] == [w["text"] for w in before_export]


# --- the correct stage ----------------------------------------------------------------


def stage_context(conn, job_id: int, **state):
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    return runner.RunnerContext(
        conn=conn,
        job=job,
        params={},
        report=lambda _fraction: None,
        cancelled=lambda: False,
        state=dict(state),
    )


def test_the_correct_stage_is_the_last_thing_before_finalize():
    names = [name for name, _fn in TRANSCRIBE_STAGES]

    assert names == [
        "probe", "prepare", "proxy", "transcribe", "diarize", "attribute", "correct", "finalize",
    ]


def test_correct_is_a_job_type_of_its_own(conn):
    assert [name for name, _fn in runner.STAGES[correct.JOB_TYPE]] == ["correct"]


def test_the_stage_corrects_the_run_the_transcribe_stage_left_in_state(conn):
    media_id, run_id = seeded(conn)
    glossary.add(conn, "Vermeulen")
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_id)

    correct.run(stage_context(conn, job_id, run_id=run_id))

    assert render.join_text(transcript_ui.run_words(conn, run_id)) == (
        "Ik sprak Vermeulen gisteren."
    )


def test_a_standalone_correct_job_finds_the_media_s_current_run_itself(conn):
    media_id, run_id = seeded(conn)
    glossary.add(conn, "Vermeulen")
    job_id = jobs.enqueue(conn, correct.JOB_TYPE, media_id=media_id)

    correct.run(stage_context(conn, job_id))

    assert len(glossary.stored(conn, run_id)) == 1


def test_re_running_the_stage_after_a_term_is_removed_takes_the_layer_away(conn):
    media_id, run_id = seeded(conn)
    term_id = glossary.add(conn, "Vermeulen")
    job_id = jobs.enqueue(conn, correct.JOB_TYPE, media_id=media_id)
    correct.run(stage_context(conn, job_id))
    assert glossary.stored(conn, run_id)

    glossary.remove(conn, term_id)
    correct.run(stage_context(conn, job_id))

    assert glossary.stored(conn, run_id) == []
    assert render.join_text(transcript_ui.run_words(conn, run_id)) == (
        "Ik sprak Vermulen gisteren."
    )


def test_the_stage_says_in_its_event_how_many_words_it_changed(conn):
    media_id, run_id = seeded(conn)
    glossary.add(conn, "Vermeulen")
    job_id = jobs.enqueue(conn, correct.JOB_TYPE, media_id=media_id)

    correct.run(stage_context(conn, job_id))

    (event,) = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "correct"]
    assert event["payload"]["run_id"] == run_id
    assert event["payload"]["n_corrections"] == 1
    assert event["payload"]["n_terms"] == 1


def test_a_correct_job_for_a_media_with_no_transcript_says_so(conn):
    media_id = seed.seed_media(conn, title="Nog niets")
    job_id = jobs.enqueue(conn, correct.JOB_TYPE, media_id=media_id)

    with pytest.raises(RuntimeError) as exc:
        correct.run(stage_context(conn, job_id))

    assert "no current run" in str(exc.value)


def test_an_empty_glossary_costs_nothing_and_writes_nothing(conn):
    media_id, run_id = seeded(conn)
    job_id = jobs.enqueue(conn, correct.JOB_TYPE, media_id=media_id)

    correct.run(stage_context(conn, job_id))

    assert glossary.stored(conn, run_id) == []


# --- the settings page ------------------------------------------------------------------


def test_the_settings_page_lists_the_glossary(client, conn):
    glossary.add(conn, "WHYcast", weight=5.0)

    page = client.get("/settings").text

    assert "WHYcast" in page
    assert 'id="glossary"' in page


def test_posting_a_term_adds_it_and_answers_with_the_section(client, conn):
    response = client.post(
        "/settings/glossary",
        data={"term": "Zaphod", "weight": "3", "variants": "Zafod, Zafodd"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Zaphod" in response.text
    (stored,) = glossary.terms(client.app.state.conn)
    assert (stored.weight, stored.variants) == (3.0, ("Zafod", "Zafodd"))


def test_a_blank_term_is_refused(client):
    response = client.post("/settings/glossary", data={"term": "   "})

    assert response.status_code == 400


def test_the_same_term_twice_is_refused_rather_than_duplicated(client, conn):
    client.post("/settings/glossary", data={"term": "Zaphod"})

    response = client.post("/settings/glossary", data={"term": "Zaphod"})

    assert response.status_code == 409


def test_a_term_can_be_deleted_from_the_page(client, conn):
    term_id = glossary.add(client.app.state.conn, "Zaphod")

    response = client.post(
        f"/settings/glossary/{term_id}/delete", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert glossary.terms(client.app.state.conn) == []


def test_deleting_a_term_that_is_not_there_is_a_404(client):
    assert client.post("/settings/glossary/4242/delete").status_code == 404


def test_a_pasted_list_is_imported_one_term_per_line(client):
    response = client.post(
        "/settings/glossary/import",
        data={"terms": "Zaphod\nMarieke\nWHYcast"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert len(glossary.terms(client.app.state.conn)) == 3


def test_the_transcript_page_says_what_a_corrected_word_used_to_say(client):
    """A correction the reader cannot see is not far from the silent rewrite
    ADR-003 exists to forbid."""
    conn = client.app.state.conn
    media_id = seed.seed_media(conn, title="Gesprek")
    run_id = seed.seed_run(conn, media_id, words=words_from("Ik sprak Vermulen gisteren."))
    glossary.store(
        conn, run_id, glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")])
    )

    page = client.get(f"/media/{media_id}").text

    assert 'data-was=" Vermulen"' in page
    assert "Vermeulen" in page


def test_an_uncorrected_transcript_carries_no_such_marker(client):
    """None of the three - the attribute, the tooltip, the description - so the
    markup of an uncorrected transcript is exactly what it was before there was
    a glossary. The same `{% if %}` guards all of them, which is what keeps the
    export bundle's markup (the same macro) unchanged too."""
    conn = client.app.state.conn
    media_id = seed.seed_media(conn, title="Gesprek")
    seed.seed_run(conn, media_id, words=words_from("Ik sprak Vermulen gisteren."))

    page = client.get(f"/media/{media_id}").text

    assert "data-was" not in page
    assert "aria-description" not in page


def test_what_a_corrected_word_used_to_say_reaches_more_than_a_mouse(client):
    """`title` is a hover tooltip, and on a plain `<span>` it is not reliably
    announced: a keyboard or screen-reader user got no audit trail at all, and
    a corrected name read as though Whisper had produced it. `aria-description`
    puts the same sentence in the accessible tree.

    Honest about its reach: `aria-description` is ARIA 1.3 and support is
    partial (Chromium yes, others are catching up), so the `title` stays as
    the mouse affordance and the fallback rather than being replaced.
    """
    conn = client.app.state.conn
    media_id = seed.seed_media(conn, title="Gesprek")
    run_id = seed.seed_run(conn, media_id, words=words_from("Ik sprak Vermulen gisteren."))
    glossary.store(
        conn, run_id, glossary.corrections_for(glossary.words_of(conn, run_id), [term("Vermeulen")])
    )

    page = client.get(f"/media/{media_id}").text

    assert 'aria-description="Glossary: was &quot;Vermulen&quot;"' in page
    assert 'title="Glossary: was &quot;Vermulen&quot;"' in page


def test_the_built_in_name_says_so_on_hover_and_not_glossary(client):
    """TASK-102.04: the app's own name is not in anybody's glossary, so the
    transcript must not say it came from there."""
    conn = client.app.state.conn
    media_id = seed.seed_media(conn, title="Gesprek")
    run_id = seed.seed_run(conn, media_id, words=words_from("Een opname voor MiScribe."))
    glossary.store(conn, run_id, glossary.built_in_corrections(glossary.words_of(conn, run_id)))

    page = client.get(f"/media/{media_id}").text

    assert 'title="App name: was &quot;MiScribe.&quot;"' in page
    assert "Glossary: was" not in page



def test_a_corrected_word_is_marked_in_the_stylesheet_and_not_only_on_hover(client):
    """The other half of the same finding: a correction nobody can see until
    they hover is a correction most readers never learn about. The rule is
    asserted by its selector, not its declaration, so changing the colour or
    the thickness does not fail this."""
    css = client.get("/static/app.css").text

    assert ".w[data-was]" in css


def test_a_term_with_markup_in_it_is_escaped_on_the_page(client):
    glossary.add(client.app.state.conn, "<script>alert(42)</script>")

    page = client.get("/settings").text

    assert "<script>alert(42)</script>" not in page
    assert "&lt;script&gt;" in page


def test_re_running_corrections_queues_one_job_per_transcribed_media(client):
    conn = client.app.state.conn
    first = seed.seed_media(conn, title="Een")
    seed.seed_run(conn, first, words=words_from("Ik sprak Vermulen."))
    second = seed.seed_media(conn, title="Twee")
    seed.seed_run(conn, second, words=words_from("Nog een gesprek."))
    seed.seed_media(conn, title="Drie zonder transcript")

    response = client.post("/settings/glossary/recorrect", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "jobs-changed"
    queued = conn.execute(
        "SELECT media_id FROM job WHERE type=? ORDER BY id", (correct.JOB_TYPE,)
    ).fetchall()
    assert [row["media_id"] for row in queued] == [first, second]


def test_a_re_run_covers_the_run_people_read_and_leaves_an_older_one_alone(client):
    """A re-transcription makes a new run current and leaves the old one where
    it is, layer and all. That layer stays: it is that run's, it was right when
    it was made, and no route in the app renders a run that is not current -
    `transcript.current_run` is the only chooser, and every export path calls
    `doc.load` without a run id. The one caller that passes one is the LLM
    stage, and it passes the run its own plan just resolved.

    Stated here rather than left to be discovered, because `doc.load(conn,
    media_id, run_id)` is a public path: whoever wires a route to an older run
    inherits that run's corrections as they were, not as the glossary is now.
    """
    conn = client.app.state.conn
    media_id = seed.seed_media(conn, title="Twee keer")
    old_run = seed.seed_run(conn, media_id, words=words_from("Ik sprak Vermulen."))
    glossary.store(conn, old_run, [glossary.Correction(2, " Vermulen", " Oud", "fuzzy", 0.9)])
    new_run = seed.seed_run(conn, media_id, words=words_from("Ik sprak Vermulen."))
    glossary.add(conn, "Vermeulen")

    client.post("/settings/glossary/recorrect")
    job_id = conn.execute(
        "SELECT id FROM job WHERE type=? ORDER BY id DESC LIMIT 1", (correct.JOB_TYPE,)
    ).fetchone()["id"]
    correct.run(stage_context(conn, job_id))

    assert [row["corrected"] for row in glossary.stored(conn, old_run)] == [" Oud"]
    # The full stop rides along, as test_punctuation_around_a_corrected_word_survives
    # pins: the sentence's last word is " Vermulen." and only its middle moved.
    assert [row["corrected"] for row in glossary.stored(conn, new_run)] == [" Vermeulen."]


def test_re_running_corrections_does_not_queue_a_second_round_on_top_of_the_first(client):
    conn = client.app.state.conn
    media_id = seed.seed_media(conn, title="Een")
    seed.seed_run(conn, media_id, words=words_from("Ik sprak Vermulen."))

    client.post("/settings/glossary/recorrect")
    client.post("/settings/glossary/recorrect")

    count = conn.execute(
        "SELECT COUNT(*) FROM job WHERE type=?", (correct.JOB_TYPE,)
    ).fetchone()[0]
    assert count == 1


def recorrect(client, conn) -> None:
    """POST /settings/glossary/recorrect and run the job it queues.

    The route only queues; the supervisor's runner child is what calls the
    stage, and that is what this stands in for. A second call finds the first
    job still queued (the route does not stack rounds) and runs it again -
    which is exactly what a user gets by pressing the button twice.
    """
    client.post("/settings/glossary/recorrect")
    job = conn.execute(
        "SELECT id FROM job WHERE type=? ORDER BY id DESC LIMIT 1", (correct.JOB_TYPE,)
    ).fetchone()
    correct.run(stage_context(conn, job["id"]))


def test_reassigning_speakers_does_not_make_a_range_uncorrectable(client):
    """CR-004, and the reason `edited_by_user` and `text_edited_by_user` are
    two columns.

    Fixing mislabelled diarization by hand is one UPDATE on `word.speaker`;
    ADR-003 means it does not touch a single character of the transcript. So
    re-running the glossary over that range has to produce what it produced
    before. While the window builder read `edited_by_user` - the flag that
    reassignment writes - it did not: `store` deletes the run's layer and
    re-inserts, the skipped range regenerated nothing, and the names silently
    fell back to what Whisper produced, in the view and in every export, with
    no way to get them back.
    """
    conn = client.app.state.conn
    media_id = seed.seed_media(conn, title="Verkeerd gelabeld")
    run_id = seed.seed_run(conn, media_id, words=words_from("Ik sprak Vermulen gisteren."))
    glossary.add(conn, "Vermeulen")
    recorrect(client, conn)
    assert [row["corrected"] for row in glossary.stored(conn, run_id)] == [" Vermeulen"]

    response = client.post(
        f"/media/{media_id}/words/reassign",
        data={"from_idx": "0", "to_idx": "3", "speaker": "new", "display_name": "Arthur"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # The premise of the test, spelled out: all four words moved to the new
    # cluster, `edited_by_user` records that a person chose it, and the text
    # flag stays 0 because nobody retyped anything.
    moved = conn.execute(
        "SELECT DISTINCT speaker, edited_by_user, text_edited_by_user FROM word WHERE run_id=?",
        (run_id,),
    ).fetchall()
    assert [tuple(row) for row in moved] == [("USER_1", 1, 0)]

    recorrect(client, conn)

    assert [row["corrected"] for row in glossary.stored(conn, run_id)] == [" Vermeulen"]
    assert render.join_text(transcript_ui.run_words(conn, run_id)) == (
        "Ik sprak Vermeulen gisteren."
    )


# --- the URL-import metadata reaches the decode bias --------------------------------------


def test_the_url_stage_hands_its_metadata_names_to_the_transcribe_job(conn):
    from scribe.stages import url_stage

    params = url_stage.transcribe_params(
        {"options": {"model": "large-v3-turbo"}}, extra_terms=["Zaphod", "Megadodo"]
    )

    assert params[transcribe.EXTRA_HOTWORDS_KEY] == ["Zaphod", "Megadodo"]
    assert params["model"] == "large-v3-turbo"


def test_the_transcribe_stage_spends_those_terms_as_hotwords(conn):
    glossary.add(conn, "WHYcast")

    hotwords = glossary.compose_hotwords(
        conn, {"title": "raw-042", "orig_name": "raw-042.mp3"}, extra_terms=["Zaphod"]
    )

    assert hotwords == "WHYcast, Zaphod"


# --- what it costs -------------------------------------------------------------------------


def test_a_correction_past_the_first_match_block_lands_on_the_right_word():
    """The blocks are an optimisation and this is the only thing that checks
    they are transparent.

    `corrections_for` scores `_MATCH_BLOCK` windows per `cdist` call, and
    within a block the score matrix is indexed from zero while the transcript
    is not. Every real transcript is longer than one block - three windows per
    word puts the seam around word 1,365 - so an off-by-one on the slice, a
    `break` where the loop wants `continue`, or the block-local row index used
    where the absolute start is meant would drop or misplace every correction
    past that seam, in silence: no error, no log line, and a glossary that
    just stops working part-way down a long recording.

    Two misspellings, because one placement does not catch both mistakes.
    Windows are emitted `max_window` per starting word, so the one-word window
    of word `_MATCH_BLOCK // MAX_WINDOW_WORDS` is the *last* of the first
    block: a block scored one window short drops that correction and nothing
    else. A word well past the seam catches the opposite mistake, where the
    block-local row number is used as if it were the transcript-wide one - the
    correction still comes back, carrying the wrong word.

    Both indices are derived from the constants rather than written out, so
    this follows a change to either instead of quietly testing one block.

    Two terms for a third reason: `cdist` zeroes every score under the cutoff,
    and the loop over a row's scores must skip those columns rather than stop
    at them. With one term in the glossary a `break` and a `continue` there do
    the same thing, so the decoy - which matches nothing - is what tells them
    apart. It is listed first, so a `break` never reaches "Vermeulen".
    """
    seam = glossary._MATCH_BLOCK // glossary.MAX_WINDOW_WORDS
    past = seam + 200
    words = words_from(
        " ".join(
            "Vermulen" if n in (seam, past) else f"woord{n:04d}" for n in range(past + 100)
        )
    )

    fixes = glossary.corrections_for(words, [term("Megadodo"), term("Vermeulen")])

    assert [(f.word_idx, f.corrected) for f in fixes] == [
        (seam, " Vermeulen"),
        (past, " Vermeulen"),
    ]


def test_a_long_transcript_against_a_large_glossary_stays_under_a_stage_s_worth_of_time():
    """Measured, not hoped for. 6,500 words against 400 terms over windows of
    one to three words is 19,500 x 400 comparisons; `rapidfuzz.process.cdist`
    does it in ~3 s on this machine, where the per-window `extractOne` loop
    took 22 s. The bound is generous so a slower machine does not fail the
    suite; the point is that it is seconds, not minutes."""
    words = words_from(" ".join(f"woord{n:04d}" for n in range(2000)))
    terms = [term(f"Term{n:03d}") for n in range(400)]

    started = time.perf_counter()
    glossary.corrections_for(words, terms)
    elapsed = time.perf_counter() - started

    assert elapsed < 30.0


# --- corrections a person types ------------------------------------------------------


def _layer(conn, run_id):
    return {r["word_idx"]: (r["corrected"], r["rule"]) for r in glossary.stored(conn, run_id)}


def test_a_typed_correction_keeps_the_words_padding_and_can_be_taken_back(conn):
    media_id = seed.seed_media(conn, title="Guide")
    run_id = seed.seed_run(conn, media_id, words=_words([" Don't", " panic,", " the", " towel."]))

    assert glossary.correct_word(conn, run_id, 1, "PANIC") == " PANIC,"
    assert _layer(conn, run_id) == {1: (" PANIC,", glossary.RULE_MANUAL)}
    # `words_of` reads the raw words on purpose (the pass matches what Whisper
    # wrote); the view and the exports read through the layer.
    assert [w["text"] for w in transcript_ui.run_words(conn, run_id)][:2] == [" Don't", " PANIC,"]
    assert [w["text"] for w in glossary.words_of(conn, run_id)][:2] == [" Don't", " panic,"]

    assert glossary.correct_word(conn, run_id, 1, "   ") is None
    assert _layer(conn, run_id) == {}


def test_the_glossary_pass_leaves_a_typed_correction_alone_and_does_not_fight_it(conn):
    media_id = seed.seed_media(conn, title="Guide")
    run_id = seed.seed_run(conn, media_id, words=_words([" met", " Vermulen", " en", " Vermulen."]))
    glossary.add(conn, "Vermeulen")
    glossary.correct_word(conn, run_id, 1, "Vermeulen-Smit")

    terms = glossary.terms(conn)
    fixes = glossary.corrections_for(glossary.words_of(conn, run_id), terms)
    written = glossary.store(conn, run_id, fixes)

    layer = _layer(conn, run_id)
    assert layer[1] == (" Vermeulen-Smit", glossary.RULE_MANUAL), "the glossary overwrote a hand-made fix"
    assert layer[3][0] == " Vermeulen." and layer[3][1] != glossary.RULE_MANUAL
    assert written == 1  # the glossary's own row; the manual one is not counted as its work

    # And the other way round: an empty glossary layer clears its rows only.
    glossary.store(conn, run_id, [])
    assert _layer(conn, run_id) == {1: (" Vermeulen-Smit", glossary.RULE_MANUAL)}


def test_correct_same_reaches_every_word_that_says_the_same_and_nothing_else(conn):
    media_id = seed.seed_media(conn, title="Guide")
    run_id = seed.seed_run(conn, media_id, words=_words([" Ott", " and", " ott,", " said", " Otto", " Ott."]))

    assert [w["idx"] for w in glossary.same_word(conn, run_id, 0)] == [2, 5]
    assert glossary.correct_same(conn, run_id, 0, "Ad") == 3
    assert _layer(conn, run_id) == {
        0: (" Ad", "manual"), 2: (" Ad,", "manual"), 5: (" Ad.", "manual"),
    }
    # Already fixed: not offered again.
    assert glossary.same_word(conn, run_id, 0) == []


def test_correct_same_teaches_the_glossary_once_not_per_occurrence(conn, monkeypatch):
    """Every `correct_word` call used to run `learn_from_correction`, which
    reads the whole glossary and scores the variant against it - N identical
    lessons for a batch where every word says the same thing."""
    media_id = seed.seed_media(conn, title="Guide")
    run_id = seed.seed_run(conn, media_id, words=_words([" Ott", " ott,", " Ott.", " oTT"]))
    lessons = []
    monkeypatch.setattr(
        glossary, "learn_from_correction", lambda conn, heard, typed: lessons.append(typed)
    )

    assert glossary.correct_same(conn, run_id, 0, "Otto") == 4
    assert lessons == ["Otto"]


def _words(texts):
    return [
        {"idx": i, "start": i * 0.5, "end": i * 0.5 + 0.4, "text": t, "probability": 0.9, "speaker": "SPEAKER_00"}
        for i, t in enumerate(texts)
    ]


# --- TASK-102.04: the app's own name, without steering Whisper ----------------------


def _fixes(sentence):
    return [(c.word_idx, c.original, c.corrected) for c in glossary.built_in_corrections(words_from(sentence))]


def test_the_app_s_name_as_whisper_spells_it_is_corrected():
    """The outside macOS walk of 0.8.0: 'MyScribe' came back as 'MiScribe'
    with an empty glossary."""
    assert _fixes("Dit is een proefopname voor MiScribe.") == [(5, " MiScribe.", " MyScribe.")]
    assert _fixes("Open Myscribe en") == [(1, " Myscribe", " MyScribe")]
    assert _fixes("met Mi Scribe, zei hij") == [(1, " Mi", " MyScribe,"), (2, " Scribe,", "")]
    assert _fixes("in My Scribe") == [(1, " My", " MyScribe"), (2, " Scribe", "")]


def test_words_that_only_look_like_the_name_are_left_alone():
    for sentence in ("the scribe wrote", "a Scribe of old", "describe it", "my scribe wrote it",
                     "MyScribe is fine", "prescribe and subscribe"):
        assert _fixes(sentence) == [], sentence


def test_the_built_in_name_never_steers_the_decoder(conn):
    assert "MyScribe" not in glossary.compose_hotwords(conn, {})


def test_the_stage_corrects_the_name_with_an_empty_glossary(conn):
    media_id, run_id = seeded(conn, "Een opname voor MiScribe.")
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_id)

    correct.run(stage_context(conn, job_id, run_id=run_id))

    assert render.join_text(transcript_ui.run_words(conn, run_id)) == "Een opname voor MyScribe."
    (row,) = glossary.stored(conn, run_id)
    assert row["rule"] == glossary.RULE_BUILT_IN


def test_a_user_s_own_term_outranks_the_built_in_name(conn):
    media_id, run_id = seeded(conn, "Een opname voor MiScribe.")
    glossary.add(conn, "MiScribe Pro", variants=["MiScribe"])
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_id)

    correct.run(stage_context(conn, job_id, run_id=run_id))

    (row,) = glossary.stored(conn, run_id)
    assert row["rule"] != glossary.RULE_BUILT_IN


def test_same_word_matches_beyond_ascii_case(conn):
    """#50: the match moved into SQL, where LOWER() folds ASCII only - the
    predicate there is this module's own casefold, so Ø and ß still match."""
    media_id = seed.seed_media(conn, title="Guide")
    run_id = seed.seed_run(conn, media_id, words=_words(
        [" Ørsted", " en", " ØRSTED,", " Straße", " ørsted.", " Orsted"]))

    assert [w["idx"] for w in glossary.same_word(conn, run_id, 0)] == [2, 4]
