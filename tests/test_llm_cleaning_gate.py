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
    seed_long_run,
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
        "unchanged": False,
    }


def test_an_empty_transcript_is_not_a_pass():
    """Zero words in and zero out is a ratio of nothing. It must not read as
    100% agreement and sail through."""
    verdict = tasks.check_cleaning([""], [""])

    assert verdict["overall"] == 0.0
    assert not verdict["ok"]


COPIED_PART = (
    "[0:00] SPEAKER_00: uh so the towel is um the most important item\n"
    "[0:05] SPEAKER_01: you know the answer is forty-two"
)
"""A part as the model is sent it: a stamp and a label in front of each line."""


def test_a_cleaning_that_came_back_as_it_went_in_is_refused():
    """The copy this rule refuses: every part came back as it went in. The
    ratio gate counts words, so a copy sits in the middle of its band and
    only this rule sees it. A blank line between the lines is the only change
    here, and whitespace is not cleaning - nor is it the regrouping
    `cleanup.md` asks for, which drops the stamps and labels inside a stretch.

    Not the copy that was measured. On 2026-09-11 qwen3.5:4b answered media
    12's part 0 with its input's counts, twice (963 of 963 words, 45 of 45
    stamps; the text was not kept), but changed words in the other ten parts
    - and that reading still publishes (the next test)."""
    regrouped = COPIED_PART.replace("\n", "\n\n")

    verdict = tasks.check_cleaning([COPIED_PART, w(100)], [regrouped, w(100)])

    assert [part["unchanged"] for part in verdict["chunks"]] == [True, True]
    assert verdict["overall"] == pytest.approx(1.0), "the ratio alone cannot see it"
    assert not verdict["ok"]
    assert any("nothing was cleaned" in reason for reason in verdict["reasons"]), verdict["reasons"]


RUN_ON_PART = (
    "[0:00] SPEAKER_00: so the towel is the most important item\n"
    "[0:04] SPEAKER_00: a hitchhiker can have\n"
    "[0:08] SPEAKER_00: you always know where it is"
)
"""One speaker over three lines: `cleanup.md` keeps only the stamp that starts
a stretch, so a cleaning of this has two stamps to drop."""


def test_a_cleaning_with_some_parts_unchanged_is_still_published():
    """The shape measured on 2026-09-11, and a gap the copy rule leaves open.

    qwen3.5:4b's reading of media 12 had 11 parts. Part 0 came back with its
    input's counts - 963 words in and out, 45 of 45 stamps; a copy by every
    count kept, though the text itself was not - and the other ten
    changed words (ratios 0.79-0.999), so not every part was a copy and the
    reading published, at 0.894 and 0.902. That part had work to do: 43 of
    its 45 lines continue the speaker before them, as every line after the
    first does here. The rule refuses only a reading that is a copy
    throughout; refusing one copied part is ADR-010's open question, for
    Robert. Pinned, so that closing the gap has to move this test."""
    cleaned = "[0:00] SPEAKER_00: So the towel is the most important item."
    run_on_as_paragraphs = RUN_ON_PART.replace("\n", "\n\n")

    verdict = tasks.check_cleaning(
        [RUN_ON_PART, COPIED_PART],
        [run_on_as_paragraphs, cleaned + " The answer is forty-two."],
    )

    assert [part["unchanged"] for part in verdict["chunks"]] == [True, False]
    assert verdict["ok"], verdict["reasons"]


def test_a_part_with_its_punctuation_fixed_is_a_change():
    """Fixing punctuation is part of the job (`cleanup.md`), so only
    whitespace is ignored when deciding whether a part came back unchanged."""
    verdict = tasks.check_cleaning(
        ["[0:00] SPEAKER_00: dont panic its fine"], ["[0:00] SPEAKER_00: Don't panic, it's fine."]
    )

    assert verdict["chunks"][0]["unchanged"] is False
    assert verdict["ok"], verdict["reasons"]


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
    # The segments' own words with the stamps and labels dropped: 40 of the 48
    # words sent (ratio 0.83), so a change - a copy is refused (see above).
    provider, _ = fake_provider([source])
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


def test_a_verbatim_copy_is_stored_but_not_published(conn, media, monkeypatch):
    """The end-to-end half of the copy rule: the model echoes exactly what it
    was sent, the answer is stored like any refused one, and no reading is
    published - the recording reads as it did, which is all a copy offers."""

    def echo(req):
        return "\n".join(line for line in req.user.splitlines() if line.startswith("[0:"))

    provider, calls = fake_provider(echo)
    register(monkeypatch, provider)
    plan = tasks.plan_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    output_id = tasks.run_task(
        conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1"
    )

    stored = conn.execute("SELECT content FROM llm_output WHERE id=?", (output_id,)).fetchone()
    assert stored["content"].split() == plan.chunks[0].text.split(), "the fake must echo its input"
    assert _reading(conn, media) is None
    verdict = tasks.apply_cleanup(conn, plan, stored["content"], output_id)
    assert verdict["published"] is False
    assert any("nothing was cleaned" in reason for reason in verdict["reasons"]), verdict["reasons"]


def naming(name: str):
    """A cleaner that makes a real change at ratio 1.0 - the cluster label
    becomes `name` - so a reading shows whose parts it was built from."""

    def clean(req):
        return "\n".join(
            line.replace("SPEAKER_00:", f"{name}:")
            for line in req.user.splitlines()
            if line.startswith("[0:")
        )

    return clean


def test_a_rerun_publishes_its_own_parts_not_another_models_newer_ones(conn, monkeypatch):
    """Measured 2026-09-11 on a copy of the library: media 12 cleaned on the
    cloud (7 parts), then locally (11 parts), then on the cloud again. The
    last run reused its own 7 parts - `stored_chunk` keys a part by provider,
    model and segments - but the gate read the newest part per index across
    the whole run, which were the local ones, judged them against the cloud
    chunks and refused a good cleaning ('part 1 kept 48%'). In this fixture
    the old read does worse: the local parts pass the gate, so it publishes
    them under the cloud row's id - another model's cleaning under this
    row's name, which the text assertion catches (the id assertion passes).
    A reading is built from the parts its own row names."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    cloud, _ = fake_provider(naming("Cloud"), name="cloud")
    local, _ = fake_provider(naming("Local"), name="local")
    register(monkeypatch, cloud)
    register(monkeypatch, local)
    wide = dict(media_id=media_id, kind="cleanup", provider_name="cloud", model="cloud-1",
                budget_tokens=100)
    narrow = dict(media_id=media_id, kind="cleanup", provider_name="local", model="local-1",
                  budget_tokens=60)
    assert (
        len(tasks.plan_task(conn, **narrow).chunks) > len(tasks.plan_task(conn, **wide).chunks) > 1
    ), "the test needs one run cut two ways, the second into more parts"

    tasks.run_task(conn, **wide)
    tasks.run_task(conn, **narrow)
    again = tasks.run_task(conn, **wide)

    final = conn.execute("SELECT content FROM llm_output WHERE id=?", (again,)).fetchone()
    reading = _reading(conn, media_id)
    assert reading is not None and reading["llm_output_id"] == again, (
        f"the rerun's reading was not published; the reading is {reading}"
    )
    assert reading["text"] == final["content"]
    assert "Local:" not in reading["text"]


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
