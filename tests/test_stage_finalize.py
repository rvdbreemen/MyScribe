"""TASK-024: finalize queues the speaker pass by itself.

The narrow question this file answers is when the pass is queued and when it is
not. What the pass then does with its answer is `test_llm_speakers`.
"""

from __future__ import annotations

import json

import pytest

from scribe import db
from scribe.stages import finalize
from tests.seed import seed_media, seed_run
from tests.test_llm_tasks import conn  # noqa: F401  (fixture)


def _set_provider(conn, name):
    with db.LOCK:
        conn.execute(
            "INSERT INTO setting(key, value) VALUES ('llm_provider', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (name,),
        )
        conn.commit()


@pytest.fixture
def recording(conn):
    media_id = seed_media(conn, title="Guide")
    run_id = seed_run(conn, media_id)
    return media_id, run_id


def _queued(conn):
    return [
        json.loads(row["params_json"])
        for row in conn.execute("SELECT params_json FROM job WHERE type='llm' ORDER BY id")
    ]


def test_clusters_mean_a_speaker_pass_is_queued(conn, recording):
    media_id, run_id = recording
    _set_provider(conn, "ollama")

    job_id = finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00", "SPEAKER_01"])

    assert job_id is not None
    (params,) = _queued(conn)
    assert params["kind"] == "speakers"
    assert params["media_id"] == media_id
    assert params["run_id"] == run_id


def test_no_diarization_means_no_question_to_ask(conn, recording):
    """A recording with one speaker, or with diarization switched off, has no
    clusters. There is nothing to identify and nothing to pay for."""
    media_id, run_id = recording
    _set_provider(conn, "ollama")

    assert finalize.queue_speaker_pass(conn, media_id, run_id, []) is None
    assert _queued(conn) == []


def test_a_private_recording_is_not_sent_to_an_external_provider_by_a_pipeline_step(
    conn, recording
):
    """The rule Robert set for bulk actions, and it binds harder here: a
    pipeline step is the least conscious act there is."""
    media_id, run_id = recording
    _set_provider(conn, "openai")
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media_id,))
        conn.commit()

    assert finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00"]) is None
    assert _queued(conn) == []


def test_a_private_recording_is_identified_by_a_local_provider(conn, recording):
    media_id, run_id = recording
    _set_provider(conn, "ollama")
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media_id,))
        conn.commit()

    assert finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00"]) is not None


def test_the_pass_names_the_run_it_was_asked_about(conn, recording):
    """speaker_label hangs off a run. A pass that did not say which run it was
    about could name the clusters of a re-transcription that landed while it
    was in flight."""
    media_id, run_id = recording
    _set_provider(conn, "ollama")

    finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00"])

    assert _queued(conn)[0]["run_id"] == run_id


# --- inheriting names across a re-transcription ------------------------------------


def _name(conn, run_id, cluster, display, source="llm", confidence=95.0):
    with db.LOCK:
        conn.execute(
            "INSERT INTO speaker_label(run_id, cluster_label, display_name, source, confidence)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, cluster, display, source, confidence),
        )
        conn.commit()


def _labels(conn, run_id):
    return {
        row["cluster_label"]: (row["display_name"], row["source"])
        for row in conn.execute(
            "SELECT cluster_label, display_name, source FROM speaker_label WHERE run_id=?",
            (run_id,),
        )
    }


def test_a_re_transcription_inherits_the_names_the_previous_run_earned(conn):
    """Re-transcribing with a better model should not pay a reasoning model to
    rediscover names that were already right - and should certainly not throw
    away a name a person typed."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur", source="llm")
    _name(conn, old_run, "SPEAKER_01", "Ford", source="human")
    new_run = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, media_id, new_run, ["SPEAKER_00", "SPEAKER_01"])

    assert _labels(conn, new_run) == {
        "SPEAKER_00": ("Arthur", "llm"),
        "SPEAKER_01": ("Ford", "human"),
    }


def test_different_clusters_inherit_nothing(conn):
    """The names hang off cluster labels. If diarization came back with a
    different set, the old mapping is not stale - it is meaningless, and
    copying it would put a real person's name on somebody else's voice."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    _name(conn, old_run, "SPEAKER_01", "Ford")
    new_run = seed_run(conn, media_id)

    finalize.inherit_speaker_names(
        conn, media_id, new_run, ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    )

    assert _labels(conn, new_run) == {}


def test_a_fully_inherited_run_does_not_pay_for_the_pass_again(conn):
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    new_run = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, media_id, new_run, ["SPEAKER_00"])
    job_id = finalize.queue_speaker_pass(conn, media_id, new_run, ["SPEAKER_00"])

    assert job_id is None
    assert _queued(conn) == []


def test_a_partly_named_run_still_asks_about_the_rest(conn):
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    new_run = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, media_id, new_run, ["SPEAKER_00", "SPEAKER_01"])
    job_id = finalize.queue_speaker_pass(conn, media_id, new_run, ["SPEAKER_00", "SPEAKER_01"])

    assert job_id is not None


def test_the_first_run_of_a_recording_inherits_nothing_and_asks(conn):
    media_id = seed_media(conn, title="Guide")
    run_id = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, media_id, run_id, ["SPEAKER_00"])

    assert _labels(conn, run_id) == {}
    assert finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00"]) is not None
