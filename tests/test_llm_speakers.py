"""Two kinds ported from WHYcast-transcribe: `speakers` and `cleanup`.

`speakers` asks who each `SPEAKER_XX` label is and answers with a mapping;
`cleanup` rewrites the transcript for reading. Both see the cluster label on
every line - the summary kinds do not - and `cleanup` is the first `concat`
task: a long transcript is rewritten chunk by chunk and the parts joined,
because notes about a chunk would lose the words the task exists to keep.

The fake provider is the one `test_llm_tasks` uses, registered the same way,
so the privacy pin and the registry stay in the path.
"""

from __future__ import annotations

import json

import pytest

from scribe.exports import doc as docs
from scribe import db
from scribe.llm import base, chunking, privacy, tasks
from tests.seed import seed_media, seed_run
from tests.test_llm_tasks import (
    CHUNK_MARKER,
    conn,  # noqa: F401  (fixture)
    fake_provider,
    register,
    rows,
    seed_long_run,
)

SPEAKERS_ANSWER = json.dumps(
    {
        "format": "conversation",
        "speakers": [
            {
                "cluster": "SPEAKER_00",
                "name": "Arthur",
                "role": "host",
                "confidence": "high",
                "evidence": "[0:00] \"Don't panic\"",
            },
            {"cluster": "SPEAKER_01", "name": "Marvin", "role": "guest", "confidence": "medium"},
            {"cluster": "SPEAKER_07", "name": "Nobody", "role": "other", "confidence": "low"},
        ],
    }
)


@pytest.fixture
def media(conn):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    return media_id


# --- the transcript the model sees ---------------------------------------------------------


def test_segment_speakers_credits_each_segment_to_the_cluster_most_of_its_words_carry(conn, media):
    doc = docs.load(conn, media)
    assert chunking.segment_speakers(doc) == ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01", "SPEAKER_01"]


def test_the_speakers_kind_sees_cluster_labels_and_the_summary_kind_does_not(conn, media, monkeypatch):
    provider, calls = fake_provider([SPEAKERS_ANSWER, tasks_answer("summary")])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    assert "] SPEAKER_00: Don't panic" in calls[0].user
    assert "] SPEAKER_01: Marvin says" in calls[0].user
    assert "SPEAKER_0" not in calls[1].user


def tasks_answer(kind: str) -> str:
    from tests.test_llm_tasks import ANSWERS

    return ANSWERS[kind]


# --- speakers ------------------------------------------------------------------------


def test_a_speakers_answer_is_stored_as_the_validated_mapping(conn, media, monkeypatch):
    provider, _calls = fake_provider([SPEAKERS_ANSWER])
    register(monkeypatch, provider)

    output_id = tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    (row,) = rows(conn, media, "speakers")
    assert row["id"] == output_id
    stored = json.loads(row["content"])
    assert stored["format"] == "conversation"
    assert [s["cluster"] for s in stored["speakers"]] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_07"]
    assert stored["speakers"][1]["evidence"] == ""  # defaulted, not refused


def test_a_speakers_answer_that_names_half_still_validates_and_prose_fails_with_its_text(
    conn, media, monkeypatch
):
    partial = json.dumps({"speakers": [{"cluster": "SPEAKER_00"}]})
    provider, _ = fake_provider([partial, "I cannot tell who these people are."])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    stored = json.loads(rows(conn, media, "speakers")[0]["content"])
    # Confidence defaults to 0 since TASK-024, not to the word "low": an answer
    # that did not say how sure it was has not earned an automatic write, and a
    # default that could clear the threshold is how a gate stops being one.
    assert stored["speakers"] == [
        {"cluster": "SPEAKER_00", "name": "", "role": "", "confidence": 0.0, "evidence": "", "notes": ""}
    ]

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    assert "I cannot tell who these people are" in str(caught.value)
    assert len(rows(conn, media, "speakers")) == 1


def test_a_private_recording_refuses_a_cloud_speakers_pass_before_reading_words(conn, media, monkeypatch):
    provider, calls = fake_provider([SPEAKERS_ANSWER], is_local=False)
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media,))
        conn.commit()

    with pytest.raises(privacy.PrivacyRefused):
        tasks.plan_task(conn, media_id=media, kind="speakers", provider_name="fake")
    assert calls == []


def test_a_long_recording_takes_speaker_notes_per_chunk_and_combines_them(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)

    def script(req):
        if CHUNK_MARKER in req.user:
            assert "SPEAKER_00:" in req.user
            return "[0:00] SPEAKER_00 says word00 a lot; nobody is named"
        return SPEAKERS_ANSWER

    provider, calls = fake_provider(script)
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media_id, kind="speakers", provider_name="fake", model="fake-1", budget_tokens=100)

    assert len([c for c in calls if CHUNK_MARKER in c.user]) >= 2
    assert "who each SPEAKER_XX label is" in calls[0].user


# --- cleanup, a concat task ------------------------------------------------------------------


def test_cleanup_over_a_short_recording_is_one_call_and_stores_the_text(conn, media, monkeypatch):
    provider, calls = fake_provider(["**Arthur:** Don't panic.\n\n**Marvin:** I am depressed."])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    assert len(calls) == 1
    assert "Clean this transcript up" in calls[0].user
    assert "SPEAKER_00:" in calls[0].user
    (row,) = rows(conn, media, "cleanup")
    assert row["content"].startswith("**Arthur:**")


def test_cleanup_over_a_long_recording_rewrites_each_chunk_and_joins_the_parts(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)

    def script(req):
        assert CHUNK_MARKER not in req.user, "a concat task never asks for notes"
        first = req.user.split("\n")[-1][:24]
        return f"cleaned <{first}>"

    provider, calls = fake_provider(script)
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100)

    parts = [r for r in rows(conn, media_id) if r["kind"].startswith("cleanup:chunk:")]
    (final,) = rows(conn, media_id, "cleanup")
    assert len(parts) >= 2
    assert len(calls) == len(parts)
    assert final["content"] == "\n\n".join(p["content"] for p in parts)
    params = json.loads(final["params_json"])
    assert params["calls"] == len(parts)
    assert params["chunk_output_ids"] == [p["id"] for p in parts]
    # No overlap: a seam rewritten twice would be a sentence said twice.
    ids = [json.loads(p["params_json"])["segment_ids"] for p in parts]
    for a, b in zip(ids, ids[1:]):
        assert not set(a) & set(b)


def test_a_resumed_cleanup_reuses_the_parts_it_already_paid_for(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    seen = {"n": 0}

    def flaky(req):
        seen["n"] += 1
        if seen["n"] >= 2:  # every call after the first, so the retry policy gives up too
            raise base.Unreachable("the endpoint went away")
        return f"part {seen['n']}"

    provider, _ = fake_provider(flaky)
    register(monkeypatch, provider)
    with pytest.raises(base.Unreachable):
        tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100)
    before = len([r for r in rows(conn, media_id) if r["kind"].startswith("cleanup:chunk:")])
    assert before == 1

    again, calls = fake_provider(lambda req: "later part")
    register(monkeypatch, again)
    tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100)

    parts = [r for r in rows(conn, media_id) if r["kind"].startswith("cleanup:chunk:")]
    assert len(calls) == len(parts) - before  # the first part was not asked again
    assert parts[0]["content"] == "part 1"


# --- confidence as a number (TASK-024) ---------------------------------------------


def test_a_numeric_confidence_survives_validation(conn):
    payload = tasks.Speakers.model_validate_json(
        json.dumps({"speakers": [{"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 96}]})
    )

    assert payload.speakers[0].confidence == 96.0


def test_the_old_word_scale_still_validates_but_lands_below_the_bar(conn):
    """A model that answers 'high' where a number was asked for has not given
    the evidence the threshold needs, so the word maps to just under it: the
    guess is still shown and can still be accepted by hand, but nothing is
    written unattended on the strength of a word.

    Tolerated rather than refused because refusing would turn a model that
    answered the older way into a failed job, and the answer is still useful.
    """
    payload = tasks.Speakers.model_validate_json(
        json.dumps(
            {
                "speakers": [
                    {"cluster": "SPEAKER_00", "name": "A", "confidence": "high"},
                    {"cluster": "SPEAKER_01", "name": "B", "confidence": "medium"},
                    {"cluster": "SPEAKER_02", "name": "C", "confidence": "low"},
                ]
            }
        )
    )

    high, medium, low = [s.confidence for s in payload.speakers]
    assert high < tasks.SPEAKER_CONFIDENCE_THRESHOLD
    assert medium < high and low < medium


def test_a_confidence_that_means_nothing_is_no_confidence_at_all(conn):
    """Empty, absent or unparseable: zero, which is below every threshold.
    Never a default that would let something through."""
    payload = tasks.Speakers.model_validate_json(
        json.dumps(
            {
                "speakers": [
                    {"cluster": "SPEAKER_00", "name": "A"},
                    {"cluster": "SPEAKER_01", "name": "B", "confidence": ""},
                    {"cluster": "SPEAKER_02", "name": "C", "confidence": "very sure indeed"},
                ]
            }
        )
    )

    assert [s.confidence for s in payload.speakers] == [0.0, 0.0, 0.0]


def test_a_percentage_written_as_a_fraction_is_read_as_one(conn):
    """0.96 and 96 mean the same thing to a person and this must not be the
    difference between applying a name and not."""
    payload = tasks.Speakers.model_validate_json(
        json.dumps({"speakers": [{"cluster": "SPEAKER_00", "name": "A", "confidence": 0.96}]})
    )

    assert payload.speakers[0].confidence == 96.0


def test_the_speakers_prompt_asks_for_a_number(conn):
    body = tasks.render_prompt("speakers", transcript="x", source_label="y", prompt="", known_labels=[])

    assert "0-100" in body or "0 to 100" in body


# --- applying the mapping (TASK-024) -----------------------------------------------


def _named(conn, run_id):
    return {
        row["cluster_label"]: (row["display_name"], row["source"], row["confidence"])
        for row in conn.execute(
            "SELECT cluster_label, display_name, source, confidence FROM speaker_label"
            " WHERE run_id=?",
            (run_id,),
        )
    }


def _answer(*speakers) -> str:
    return json.dumps({"speakers": list(speakers)})


def _run_id(conn, media_id):
    return conn.execute(
        "SELECT id FROM run WHERE media_id=? AND is_current=1", (media_id,)
    ).fetchone()["id"]


def test_a_confident_name_is_written_by_the_job_itself(conn, media, monkeypatch):
    """The whole point of TASK-024: no click. And the row says where the name
    came from, so 'why does this say Arthur' is answerable from the row."""
    provider, _ = fake_provider(
        [_answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 96})]
    )
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1"
    )

    named = _named(conn, _run_id(conn, media))
    assert named["SPEAKER_00"] == ("Arthur", "llm", 96.0)
    row = conn.execute("SELECT llm_output_id FROM speaker_label").fetchone()
    assert row["llm_output_id"] == output_id


def test_a_name_at_or_below_the_threshold_is_not_written(conn, media, monkeypatch):
    """90 is the bar and it is not inclusive-by-accident: a cluster that did
    not clear it keeps its default and stays a suggestion."""
    provider, _ = fake_provider(
        [
            _answer(
                {"cluster": "SPEAKER_00", "name": "Maybe", "confidence": 90},
                {"cluster": "SPEAKER_01", "name": "Unsure", "confidence": 89.9},
                {"cluster": "SPEAKER_02", "name": "Sure", "confidence": 90.1},
            )
        ]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert set(_named(conn, _run_id(conn, media))) == {"SPEAKER_02"}


def test_a_name_a_person_typed_is_never_overwritten(conn, media, monkeypatch):
    """The rule that makes running this unattended safe at all."""
    run_id = _run_id(conn, media)
    with db.LOCK:
        conn.execute(
            "INSERT INTO speaker_label(run_id, cluster_label, display_name, source)"
            " VALUES (?, 'SPEAKER_00', 'Ford', 'human')",
            (run_id,),
        )
        conn.commit()
    provider, _ = fake_provider(
        [_answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 99})]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert _named(conn, run_id)["SPEAKER_00"][:2] == ("Ford", "human")


def test_a_cluster_with_only_a_role_is_left_alone(conn, media, monkeypatch):
    """"Guest" is not a name. Writing it would replace "Speaker 2" with
    something no more informative and harder to notice as a default."""
    provider, _ = fake_provider(
        [_answer({"cluster": "SPEAKER_00", "name": "", "role": "guest", "confidence": 99})]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert _named(conn, _run_id(conn, media)) == {}


def test_running_it_again_updates_rather_than_duplicates(conn, media, monkeypatch):
    """speaker_label is UNIQUE(run_id, cluster_label); a second confident pass
    is a correction, not a second opinion to store beside the first."""
    provider, _ = fake_provider(
        [
            _answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 95}),
            _answer({"cluster": "SPEAKER_00", "name": "Zaphod", "confidence": 97}),
        ]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-2")

    named = _named(conn, _run_id(conn, media))
    assert named == {"SPEAKER_00": ("Zaphod", "llm", 97.0)}


def test_a_failed_analysis_leaves_the_speakers_as_they_were(conn, media, monkeypatch):
    """The failure path, seen twice for real on 2026-09-10 when two episodes
    came back truncated. Nothing half-applied, nothing silently renamed: the
    job fails carrying the model's own words, and the run keeps its defaults
    for a person to sort out."""
    provider, _ = fake_provider(["I could not work out who anybody is, sorry."])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse):
        tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert _named(conn, _run_id(conn, media)) == {}


def test_the_analysis_is_still_readable_after_its_names_were_applied(conn, media, monkeypatch):
    """Accountability outlives the act. The row that got the name points at
    the analysis, and the analysis still holds the quote it rested on."""
    provider, _ = fake_provider(
        [
            _answer(
                {
                    "cluster": "SPEAKER_00",
                    "name": "Arthur",
                    "confidence": 97,
                    "evidence": '[0:07] "My name is Arthur."',
                }
            )
        ]
    )
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1"
    )

    (row,) = rows(conn, media, "speakers")
    assert row["id"] == output_id
    stored = json.loads(row["content"])
    assert stored["speakers"][0]["evidence"] == '[0:07] "My name is Arthur."'

    label = conn.execute(
        "SELECT llm_output_id, confidence FROM speaker_label WHERE cluster_label='SPEAKER_00'"
    ).fetchone()
    assert (label["llm_output_id"], label["confidence"]) == (output_id, 97.0)


def test_a_second_analysis_does_not_erase_the_first(conn, media, monkeypatch):
    """One key, never an overwrite. What an earlier model decided is evidence
    about that model on that day, and a table that replaced it would be making
    a claim about the present instead of keeping a record."""
    provider, _ = fake_provider(
        [
            _answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 95}),
            _answer({"cluster": "SPEAKER_00", "name": "Zaphod", "confidence": 99}),
        ]
    )
    register(monkeypatch, provider)

    first = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1"
    )
    second = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-2"
    )

    stored = rows(conn, media, "speakers")
    assert {row["id"] for row in stored} == {first, second}
    assert first != second
