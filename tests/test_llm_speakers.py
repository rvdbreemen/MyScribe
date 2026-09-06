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
    assert stored["speakers"] == [
        {"cluster": "SPEAKER_00", "name": "", "role": "", "confidence": "low", "evidence": "", "notes": ""}
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
