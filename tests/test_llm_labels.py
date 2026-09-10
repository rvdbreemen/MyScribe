"""TASK-023: the labels kind, and what applying its answer is allowed to do.

The interesting rules are not in the prompt, they are in `apply_labels`: reuse
is free, invention is capped, and a label a person typed is never overwritten.
The prompt asks for those things too, but a prompt is a request - these tests
pin the guarantee.
"""

from __future__ import annotations

import json

import pytest

from scribe import db
from scribe.llm import base, tasks
from tests.seed import seed_media, seed_run
from tests.test_llm_tasks import (
    conn,  # noqa: F401  (fixture)
    fake_provider,
    register,
    rows,
)

LABELS_ANSWER = json.dumps(
    {
        "labels": [
            {"label": "hacking", "confidence": "high", "evidence": '[0:12] "..."'},
            {"label": "bbs culture", "confidence": "medium"},
            {"label": "phreaking", "confidence": "low", "evidence": ""},
        ]
    }
)


@pytest.fixture
def media(conn):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    return media_id


def _label(conn, name: str, media_id: int | None = None, source: str = "human") -> None:
    conn.execute("INSERT OR IGNORE INTO label(name, created_at) VALUES (?, 0.0)", (name,))
    if media_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO media_label(media_id, label_id, source, created_at)"
            " SELECT ?, id, ?, 0.0 FROM label WHERE name = ? COLLATE NOCASE",
            (media_id, source, name),
        )
    conn.commit()


def _answer(labels: list[str]) -> str:
    return json.dumps({"labels": [{"label": name} for name in labels]})


# --- what the model is shown -------------------------------------------------------


def test_the_labels_kind_does_not_show_the_model_who_is_speaking(conn, media, monkeypatch):
    """A label describes the subject. Prefixing every line with SPEAKER_00
    spends transcript budget on something the answer never mentions."""
    provider, calls = fake_provider([LABELS_ANSWER])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="labels", provider_name="fake", model="fake-1")

    assert "SPEAKER_0" not in calls[0].user


def test_the_prompt_hands_the_model_the_vocabulary_the_library_already_has(
    conn, media, monkeypatch
):
    """Reuse-first only works if the model can see what there is to reuse."""
    _label(conn, "hacking")
    _label(conn, "incident response")
    provider, calls = fake_provider([LABELS_ANSWER])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="labels", provider_name="fake", model="fake-1")

    assert "- hacking" in calls[0].user
    assert "- incident response" in calls[0].user
    assert "Reuse them wherever one fits" in calls[0].user


def test_the_first_recording_is_told_the_library_has_no_labels_yet(conn, media, monkeypatch):
    """An empty list would render as a heading over nothing and read as "reuse
    these", pointing at no labels at all."""
    provider, calls = fake_provider([LABELS_ANSWER])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="labels", provider_name="fake", model="fake-1")

    assert "no labels yet" in calls[0].user
    assert "Reuse them wherever one fits" not in calls[0].user


# --- the stored answer -------------------------------------------------------------


def test_a_labels_answer_is_stored_as_the_validated_list(conn, media, monkeypatch):
    provider, _calls = fake_provider([LABELS_ANSWER])
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn, media_id=media, kind="labels", provider_name="fake", model="fake-1"
    )

    (row,) = rows(conn, media, "labels")
    assert row["id"] == output_id
    stored = json.loads(row["content"])
    assert [entry["label"] for entry in stored["labels"]] == [
        "hacking",
        "bbs culture",
        "phreaking",
    ]
    assert stored["labels"][1]["evidence"] == ""  # defaulted, not refused


def test_prose_instead_of_labels_fails_with_its_own_text(conn, media, monkeypatch):
    """The framework rule: an answer that is not the shape at all is refused
    carrying what it did say, because "it did not parse" is unactionable."""
    provider, _calls = fake_provider(["I had a look and it is mostly about computers."])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as excinfo:
        tasks.run_task(conn, media_id=media, kind="labels", provider_name="fake", model="fake-1")

    assert "mostly about computers" in str(excinfo.value)


# --- applying it -------------------------------------------------------------------


def _fill_vocabulary(conn, count: int) -> None:
    """`count` labels nobody will answer with, to age the vocabulary past the
    point where the cold allowance applies."""
    for i in range(count):
        _label(conn, f"filler {i}")


def test_a_cold_library_lets_more_new_labels_through(conn, media):
    """Measured on the real run 2026-09-10: the first recording in an empty
    library answered with six good labels and the flat cap kept three -
    'incident response' and 'computer forensics' were dropped for want of room
    they were not competing for. Nothing can be reused when there is nothing to
    reuse, so capping invention there only loses what the pass found."""
    payload = tasks.Labels.model_validate_json(
        _answer(["one", "two", "three", "four", "five", "six", "seven"])
    )

    report = tasks.apply_labels(conn, media, payload)

    assert report["created"] == ["one", "two", "three", "four", "five", "six"]
    assert report["dropped"] == ["seven"]


def test_an_established_vocabulary_tightens_the_allowance(conn, media):
    """Once the library has words of its own, a new one has to earn its place:
    the risk the cap exists for - a subject arriving under four spellings -
    only exists when there is something to fragment against."""
    _fill_vocabulary(conn, tasks.VOCABULARY_ESTABLISHED)
    for name in ("hacking", "security"):
        _label(conn, name)
    payload = tasks.Labels.model_validate_json(
        _answer(
            [
                "hacking",
                "security",
                "phreaking",
                "bbs culture",
                "lockpicking",
                "social engineering",
            ]
        )
    )

    report = tasks.apply_labels(conn, media, payload)

    assert report["reused"] == ["hacking", "security"]
    assert report["created"] == ["phreaking", "bbs culture", "lockpicking"]
    assert report["dropped"] == ["social engineering"]


def test_reuse_is_free_however_full_the_vocabulary_is(conn, media):
    """The cap is on invention only. A recording that genuinely touches ten
    subjects the library already names carries all ten."""
    _fill_vocabulary(conn, tasks.VOCABULARY_ESTABLISHED)
    known = [f"filler {i}" for i in range(10)]
    payload = tasks.Labels.model_validate_json(_answer(known))

    report = tasks.apply_labels(conn, media, payload)

    assert report["reused"] == known
    assert report["created"] == [] and report["dropped"] == []
    assert len(tasks.labels_for(conn, media)) == 10


def test_the_allowance_is_the_cold_one_right_up_to_the_threshold(conn):
    assert tasks.new_label_allowance(0) == tasks.MAX_NEW_LABELS_COLD
    assert tasks.new_label_allowance(tasks.VOCABULARY_ESTABLISHED - 1) == tasks.MAX_NEW_LABELS_COLD
    assert tasks.new_label_allowance(tasks.VOCABULARY_ESTABLISHED) == tasks.MAX_NEW_LABELS
    assert tasks.new_label_allowance(500) == tasks.MAX_NEW_LABELS


def test_a_case_variant_lands_on_the_label_that_exists(conn, media):
    """"Hacking" where the library holds "hacking" means the one that exists;
    a second row would split the count the sidebar shows."""
    _label(conn, "hacking")
    payload = tasks.Labels.model_validate_json(_answer(["Hacking"]))

    report = tasks.apply_labels(conn, media, payload)

    assert report["created"] == []
    assert report["reused"] == ["hacking"]
    assert [row["name"] for row in tasks.labels_for(conn, media)] == ["hacking"]
    assert conn.execute("SELECT COUNT(*) FROM label").fetchone()[0] == 1


def test_a_label_a_person_typed_survives_a_later_automatic_run(conn, media):
    """The one rule that makes re-running safe. The link is written INSERT OR
    IGNORE, so the row a person made keeps its source."""
    _label(conn, "lockpicking", media_id=media, source="human")
    payload = tasks.Labels.model_validate_json(_answer(["lockpicking", "hacking"]))

    tasks.apply_labels(conn, media, payload)

    by_name = {row["name"]: row["source"] for row in tasks.labels_for(conn, media)}
    assert by_name == {"lockpicking": "human", "hacking": "llm"}


def test_the_same_answer_twice_does_not_double_anything(conn, media):
    payload = tasks.Labels.model_validate_json(_answer(["hacking", "phreaking"]))

    tasks.apply_labels(conn, media, payload)
    second = tasks.apply_labels(conn, media, payload)

    assert second["reused"] == ["hacking", "phreaking"]
    assert second["created"] == []
    assert len(tasks.labels_for(conn, media)) == 2


def test_an_empty_or_repeated_label_is_not_a_label(conn, media):
    payload = tasks.Labels.model_validate_json(
        _answer(["hacking", "  ", "hacking", "Hacking", "phreaking."])
    )

    report = tasks.apply_labels(conn, media, payload)

    assert report["created"] == ["hacking", "phreaking"]
    assert [row["name"] for row in tasks.labels_for(conn, media)] == ["hacking", "phreaking"]


def test_an_answer_with_no_labels_applies_nothing_and_says_so(conn, media):
    report = tasks.apply_labels(conn, media, tasks.Labels())

    assert report == {"reused": [], "created": [], "dropped": []}
    assert tasks.labels_for(conn, media) == []


def test_the_vocabulary_is_offered_most_used_first(conn):
    """It is handed to a model with a budget, so the labels that earn their
    place are the ones already describing the most recordings."""
    one = seed_media(conn, title="one")
    two = seed_media(conn, title="two")
    seed_run(conn, one)
    seed_run(conn, two)
    _label(conn, "rare", media_id=one, source="llm")
    _label(conn, "common", media_id=one, source="llm")
    _label(conn, "common", media_id=two, source="llm")

    assert tasks.vocabulary(conn) == ("common", "rare")
