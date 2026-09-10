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
