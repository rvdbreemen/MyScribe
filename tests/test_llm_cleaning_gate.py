"""TASK-026: the gate that decides whether a cleaning may be published.

Robert's rule: a clean reading stays as close to the original as it can and
must not collapse in length; when it does, the cleaning is undone. Undoing is
free here because the cleaned text is a derived reading and nothing was
replaced - refusing it means not publishing it.
"""

from __future__ import annotations

import pytest

from scribe import db
from scribe.exports import doc as docs
from scribe.llm import tasks
from tests.seed import seed_media, seed_run
from tests.test_llm_tasks import (  # noqa: F401  (fixtures)
    conn,
    fake_provider,
    register,
)


def w(n: int) -> str:
    """`n` words, so a test can talk about lengths and nothing else."""
    return " ".join(["word"] * n)


def test_honest_cleaning_passes(conn=None):
    """Filler, repetition and false starts are real words and removing them is
    the job. A gate that called that a collapse would reject the feature."""
    verdict = tasks.check_cleaning([w(1000)], [w(820)])

    assert verdict["ok"]
    assert verdict["reasons"] == []
    assert verdict["overall"] == pytest.approx(0.82)


def test_a_summary_is_refused():
    verdict = tasks.check_cleaning([w(1000)], [w(200)])

    assert not verdict["ok"]
    assert any("20%" in reason for reason in verdict["reasons"])


def test_a_runaway_is_refused_and_that_is_not_hypothetical():
    """Measured 2026-09-10: a 168-word recording came back as 5742 words."""
    verdict = tasks.check_cleaning([w(168)], [w(5742)])

    assert not verdict["ok"]
    assert any("over" in reason for reason in verdict["reasons"])


def test_one_collapsed_part_is_caught_even_when_the_total_looks_healthy():
    """The reason the check is per part as well as overall. A concat task joins
    its parts, so a chunk that became a paragraph hides inside a total that
    another chunk padded back up."""
    verdict = tasks.check_cleaning([w(1000), w(1000)], [w(1400), w(300)])

    assert verdict["overall"] == pytest.approx(0.85)  # would have passed alone
    assert not verdict["ok"]
    assert len(verdict["reasons"]) == 2


def test_a_cleaning_with_the_wrong_number_of_parts_is_refused():
    """Parts are joined in order. A different count means the mapping between
    transcript and reading is not what the caller thinks it is, and comparing
    them pairwise would compare the wrong things."""
    verdict = tasks.check_cleaning([w(500), w(500)], [w(900)])

    assert not verdict["ok"]
    assert "part(s)" in verdict["reasons"][0]


def test_the_verdict_carries_the_numbers_a_refusal_has_to_quote():
    verdict = tasks.check_cleaning([w(100), w(200)], [w(90), w(20)])

    assert verdict["words_in"] == 300
    assert verdict["words_out"] == 110
    assert verdict["chunks"][1] == {
        "index": 1,
        "words_in": 200,
        "words_out": 20,
        "ratio": pytest.approx(0.1),
    }


def test_an_empty_transcript_is_not_a_pass():
    """Zero words in and zero out is a ratio of nothing. It must not read as
    100% agreement and sail through."""
    verdict = tasks.check_cleaning([""], [""])

    assert verdict["overall"] == 0.0
    assert not verdict["ok"]


def test_the_bounds_leave_room_on_both_sides_of_honest_work():
    """Pins the gap the floor was argued into: a fifth removed is normal
    cleaning, a fifth kept is a summary, and the floor sits between them."""
    assert tasks.CLEAN_MIN_RATIO < 0.8   # forgives normal disfluency removal
    assert tasks.CLEAN_MIN_RATIO > 0.3   # refuses anything summary-shaped
    assert 1.0 < tasks.CLEAN_MAX_RATIO < 1.5


# --- publishing it, or refusing to ------------------------------------------------


@pytest.fixture
def media(conn):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    return media_id


def _source_words(conn, media_id) -> int:
    doc = docs.load(conn, media_id)
    return len(" ".join(seg["text"] for seg in doc.segments).split())


def _reading(conn, media_id):
    run_id = conn.execute(
        "SELECT id FROM run WHERE media_id=? AND is_current=1", (media_id,)
    ).fetchone()["id"]
    return tasks.clean_reading(conn, run_id)


def test_a_cleaning_that_passes_the_gate_is_published(conn, media, monkeypatch):
    """The words are untouched either way; this is the second reading."""
    doc = docs.load(conn, media)
    source = " ".join(seg["text"] for seg in doc.segments)
    provider, _ = fake_provider([source])  # unchanged text: ratio 1.0
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1"
    )

    reading = _reading(conn, media)
    assert reading is not None
    assert reading["llm_output_id"] == output_id
    assert reading["words_out"] == _source_words(conn, media)


def test_a_cleaning_that_collapses_is_refused_and_nothing_is_published(
    conn, media, monkeypatch
):
    """Robert's rule. The undo is free because nothing was replaced: refusing
    means this row is never written, and the recording reads as it did."""
    provider, _ = fake_provider(["Two people talked."])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    assert _reading(conn, media) is None


def test_the_refused_answer_is_still_stored_so_the_refusal_is_checkable(
    conn, media, monkeypatch
):
    """'This was refused for keeping 8% of the words' is only checkable while
    the thing that was refused survives. store_output runs before the gate on
    purpose."""
    provider, _ = fake_provider(["Two people talked."])
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1"
    )

    row = conn.execute("SELECT content FROM llm_output WHERE id=?", (output_id,)).fetchone()
    assert row["content"] == "Two people talked."
    assert _reading(conn, media) is None


def test_a_runaway_cleaning_is_refused_too(conn, media, monkeypatch):
    """The ceiling side. Measured for real on 2026-09-10: 168 words in, 5742
    out, from a model asked only to tidy."""
    provider, _ = fake_provider([" ".join(["invented"] * 4000)])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    assert _reading(conn, media) is None


def test_cleaning_again_replaces_the_reading_rather_than_adding_one(
    conn, media, monkeypatch
):
    doc = docs.load(conn, media)
    source = " ".join(seg["text"] for seg in doc.segments)
    provider, _ = fake_provider([source, source + " again"])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")
    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-2")

    assert conn.execute("SELECT COUNT(*) FROM clean_reading").fetchone()[0] == 1
    assert _reading(conn, media)["text"].endswith("again")


def test_publishing_a_reading_touches_no_word(conn, media, monkeypatch):
    """ADR-003: words are canonical. The cleaned text is a second way to read
    them, so publishing one must leave every word row exactly as it was - that
    is the property the whole derived-reading design exists to keep."""
    run_id = conn.execute(
        "SELECT id FROM run WHERE media_id=? AND is_current=1", (media,)
    ).fetchone()["id"]
    before = [
        tuple(row)
        for row in conn.execute(
            "SELECT idx, text, speaker, edited_by_user FROM word WHERE run_id=? ORDER BY idx",
            (run_id,),
        )
    ]
    doc = docs.load(conn, media)
    provider, _ = fake_provider([" ".join(seg["text"] for seg in doc.segments)])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    after = [
        tuple(row)
        for row in conn.execute(
            "SELECT idx, text, speaker, edited_by_user FROM word WHERE run_id=? ORDER BY idx",
            (run_id,),
        )
    ]
    assert after == before
    assert tasks.clean_reading(conn, run_id) is not None


def test_a_refusal_is_reported_with_the_numbers_it_rested_on(conn, media, monkeypatch):
    """A refusal that only says 'no' cannot be argued with. The verdict the
    apply step returns carries the ratio and the part, and the job records it."""
    provider, _ = fake_provider(["Two people talked."])
    register(monkeypatch, provider)
    plan = tasks.plan_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="f")

    verdict = tasks.apply_cleanup(conn, plan, "Two people talked.", 1)

    assert verdict["published"] is False
    assert verdict["words_out"] == 3
    assert verdict["reasons"] and "%" in verdict["reasons"][0]
