"""TASK-024: finalize queues the speaker pass by itself.

The narrow question this file answers is when the pass is queued and when it is
not. What the pass then does with its answer is `test_llm_speakers`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from scribe import db, paths
from scribe.app import create_app
from scribe.stages import finalize
from tests.seed import default_words, seed_media, seed_run
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
    """Re-transcribing should not throw away names that were already right -
    certainly not one a person typed. They stand on the new run while the pass
    finalize queues anyway (TASK-037) works out its answer."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur", source="llm")
    _name(conn, old_run, "SPEAKER_01", "Ford", source="human")
    new_run = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, new_run, previous=old_run, clusters=["SPEAKER_00", "SPEAKER_01"])

    assert _labels(conn, new_run) == {
        "SPEAKER_00": ("Arthur", "llm"),
        "SPEAKER_01": ("Ford", "human"),
    }


def test_an_inherited_label_keeps_the_colour_it_had(conn):
    """TASK-076: the copy carried name, source, receipt and confidence and
    dropped the colour, so a re-transcription painted the same person plain.
    The rename route takes a colour; whatever set it, it belongs to the label."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Nancy", source="human")
    with db.LOCK:
        conn.execute(
            "UPDATE speaker_label SET color='#ff8800' WHERE run_id=? AND cluster_label='SPEAKER_00'",
            (old_run,),
        )
        conn.commit()
    new_run = seed_run(conn, media_id)

    finalize.inherit_speaker_names(conn, new_run, previous=old_run, clusters=["SPEAKER_00", "SPEAKER_01"])

    row = conn.execute(
        "SELECT display_name, color FROM speaker_label WHERE run_id=? AND cluster_label='SPEAKER_00'",
        (new_run,),
    ).fetchone()
    assert (row["display_name"], row["color"]) == ("Nancy", "#ff8800")


def test_a_cluster_the_old_run_did_not_have_does_not_cost_the_others_their_names(conn):
    """Diarization found a third voice this time. The two it found before are
    still under their labels, word for word, so their names still fit; the
    new one is the pass's question."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    _name(conn, old_run, "SPEAKER_01", "Ford")
    new_run = seed_run(conn, media_id)

    finalize.inherit_speaker_names(
        conn, new_run, previous=old_run, clusters=["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    )

    assert _labels(conn, new_run) == {
        "SPEAKER_00": ("Arthur", "llm"),
        "SPEAKER_01": ("Ford", "llm"),
    }


def _three_voices():
    """The default transcript with its last ten words given to a third voice."""
    words = default_words()
    for word in words[30:]:
        word["speaker"] = "SPEAKER_02"
    return words


def test_a_partly_named_run_keeps_the_names_it_had(conn):
    """The pass names the clusters it is sure of and leaves the rest - media 3
    has Danny and Nancy and an unnamed third. Measured 2026-09-11: 12 of the
    53 named recordings are like that, and a re-transcription dropped every
    name on them because the named labels were not the whole set."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id, words=_three_voices())
    _name(conn, old_run, "SPEAKER_00", "Danny")
    _name(conn, old_run, "SPEAKER_02", "Nancy", source="human")
    new_run = seed_run(conn, media_id, words=_three_voices())
    _set_provider(conn, "ollama")
    clusters = ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]

    named = finalize.inherit_speaker_names(conn, new_run, previous=old_run, clusters=clusters)

    assert named == ["SPEAKER_00", "SPEAKER_02"]
    assert _labels(conn, new_run) == {
        "SPEAKER_00": ("Danny", "llm"),
        "SPEAKER_02": ("Nancy", "human"),
    }
    assert finalize.queue_speaker_pass(conn, media_id, new_run, clusters) is not None


def test_a_label_that_swallowed_another_voice_loses_its_name(conn):
    """Two old clusters merged into one new one. Every word Ford had is still
    under SPEAKER_01, but so are Zaphod's now: the label kept its voice and
    took somebody else's, and it is not Ford's any more."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id, words=_three_voices())
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    _name(conn, old_run, "SPEAKER_01", "Ford")
    _name(conn, old_run, "SPEAKER_02", "Zaphod")
    new_run = seed_run(conn, media_id)  # words 20-39 all SPEAKER_01

    named = finalize.inherit_speaker_names(
        conn, new_run, previous=old_run, clusters=["SPEAKER_00", "SPEAKER_01"]
    )

    assert named == ["SPEAKER_00"]
    assert _labels(conn, new_run) == {"SPEAKER_00": ("Arthur", "llm")}


def _swapped_voices():
    """The default transcript with the two cluster labels traded: the same
    set of labels, each now on the other person's words."""
    swap = {"SPEAKER_00": "SPEAKER_01", "SPEAKER_01": "SPEAKER_00"}
    return [{**word, "speaker": swap[word["speaker"]]} for word in default_words()]


def test_the_same_labels_on_other_voices_inherit_nothing(conn):
    """A label set that matches is not the same speakers. Diarization numbers
    its clusters, and a new pipeline or model can number them differently: then
    SPEAKER_00 is the other voice, and copying its name puts a person's name -
    maybe one a person typed - on somebody else's words."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    _name(conn, old_run, "SPEAKER_01", "Ford", source="human")
    new_run = seed_run(conn, media_id, words=_swapped_voices())

    named = finalize.inherit_speaker_names(
        conn, new_run, previous=old_run, clusters=["SPEAKER_00", "SPEAKER_01"]
    )

    assert named == []
    assert _labels(conn, new_run) == {}


def test_only_the_label_whose_voice_moved_loses_its_name(conn):
    """Three speakers, two of them traded: the one that stayed keeps its
    name, the two that moved get asked about again."""
    words = _three_voices()
    swap = {"SPEAKER_00": "SPEAKER_01", "SPEAKER_01": "SPEAKER_00", "SPEAKER_02": "SPEAKER_02"}
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id, words=words)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    _name(conn, old_run, "SPEAKER_01", "Ford")
    _name(conn, old_run, "SPEAKER_02", "Zaphod", source="human")
    new_run = seed_run(
        conn, media_id, words=[{**w, "speaker": swap[w["speaker"]]} for w in words]
    )

    named = finalize.inherit_speaker_names(
        conn, new_run, previous=old_run, clusters=["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    )

    assert named == ["SPEAKER_02"]
    assert _labels(conn, new_run) == {"SPEAKER_02": ("Zaphod", "human")}


def test_a_run_every_name_is_on_is_asked_about_only_when_asked_to(conn):
    """The catch-up leaves a run whose every cluster has a name: the speakers
    were assigned, so there is nothing it should ask. A re-transcription asks
    anyway (Robert, 2026-09-11) - finalize says so with `even_if_named`."""
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    new_run = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, new_run, previous=old_run, clusters=["SPEAKER_00"])

    assert finalize.queue_speaker_pass(conn, media_id, new_run, ["SPEAKER_00"]) is None
    assert _queued(conn) == []
    job_id = finalize.queue_speaker_pass(conn, media_id, new_run, ["SPEAKER_00"], even_if_named=True)
    assert job_id is not None
    assert _queued(conn)[0]["run_id"] == new_run


def test_asking_again_still_never_sends_a_private_recording_out(conn):
    media_id = seed_media(conn, title="Guide")
    run_id = seed_run(conn, media_id)
    _name(conn, run_id, "SPEAKER_00", "Arthur")
    _set_provider(conn, "openai")
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media_id,))
        conn.commit()

    assert finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00"], even_if_named=True) is None
    assert _queued(conn) == []


def test_a_partly_named_run_still_asks_about_the_rest(conn):
    media_id = seed_media(conn, title="Guide")
    old_run = seed_run(conn, media_id)
    _name(conn, old_run, "SPEAKER_00", "Arthur")
    new_run = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, new_run, previous=old_run, clusters=["SPEAKER_00", "SPEAKER_01"])
    job_id = finalize.queue_speaker_pass(conn, media_id, new_run, ["SPEAKER_00", "SPEAKER_01"])

    assert job_id is not None


def test_the_first_run_of_a_recording_inherits_nothing_and_asks(conn):
    media_id = seed_media(conn, title="Guide")
    run_id = seed_run(conn, media_id)
    _set_provider(conn, "ollama")

    finalize.inherit_speaker_names(conn, run_id, previous=None, clusters=["SPEAKER_00"])

    assert _labels(conn, run_id) == {}
    assert finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00"]) is not None


# --- catching up recordings that were never asked (TASK-024, Robert 2026-09-10) ----


def _diarized(conn, title="Guide", *, private=False, clusters=("SPEAKER_00", "SPEAKER_01")):
    """A recording with a current run whose words carry cluster labels."""
    media_id = seed_media(conn, title=title)
    run_id = seed_run(conn, media_id)
    with db.LOCK:
        for i, cluster in enumerate(clusters):
            conn.execute(
                "UPDATE word SET speaker=? WHERE run_id=? AND idx=?", (cluster, run_id, i)
            )
        if private:
            conn.execute("UPDATE media SET private=1 WHERE id=?", (media_id,))
        conn.commit()
    return media_id, run_id


def _analysed(conn, media_id):
    with db.LOCK:
        conn.execute(
            "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version,"
            " content, created_at) VALUES (?, 'speakers', 'p', 'm', '1', '{}', 0.0)",
            (media_id,),
        )
        conn.commit()


def test_the_sweep_asks_about_a_recording_that_was_never_asked(conn):
    """Sixty runs in this library carried clusters and three carried names,
    because the pass only existed for recordings finished after it did."""
    media_id, _ = _diarized(conn)
    _set_provider(conn, "ollama")

    queued = finalize.sweep_speaker_passes(conn)

    assert len(queued) == 1
    assert _queued(conn)[0]["media_id"] == media_id


def test_the_sweep_leaves_a_recording_that_was_already_asked(conn):
    """One stored answer is enough, even a refused one. Asking again is a
    decision, and this runs at every start."""
    media_id, _ = _diarized(conn)
    _analysed(conn, media_id)
    _set_provider(conn, "ollama")

    assert finalize.sweep_speaker_passes(conn) == []


def test_the_sweep_leaves_a_recording_a_person_named_whole(conn):
    """No stored answer, but every speaker has a name somebody typed: the
    speakers were assigned, so the catch-up has nothing to ask - Robert's rule
    is for recordings whose speaker assignment never happened."""
    media_id, run_id = _diarized(conn)
    _name(conn, run_id, "SPEAKER_00", "Arthur", source="human")
    _name(conn, run_id, "SPEAKER_01", "Ford", source="human")
    _set_provider(conn, "ollama")

    assert finalize.sweep_speaker_passes(conn) == []
    assert _queued(conn) == []


def test_the_sweep_never_asks_about_a_recording_in_a_private_folder(conn):
    """Private is the recording or any folder above it (llm.privacy). Found in
    review 2026-09-11: the sweep read only the recording's own pin, so on a
    local provider a recording in a pinned folder was named automatically."""
    with db.LOCK:
        folder = conn.execute(
            "INSERT INTO folder(name, private) VALUES ('Diary', 1) RETURNING id"
        ).fetchone()["id"]
        conn.commit()
    media_id, _ = _diarized(conn)
    with db.LOCK:
        conn.execute("UPDATE media SET folder_id=? WHERE id=?", (folder, media_id))
        conn.commit()
    _set_provider(conn, "ollama")

    assert finalize.sweep_speaker_passes(conn) == []
    assert _queued(conn) == []


def test_the_sweep_never_offers_a_private_recording_to_an_external_provider(conn):
    """A startup sweep is the least deliberate act there is."""
    _diarized(conn, private=True)
    _set_provider(conn, "openai")

    assert finalize.sweep_speaker_passes(conn) == []
    assert _queued(conn) == []


def test_a_private_recording_is_skipped_even_on_a_local_provider(conn):
    """Robert's instruction is unconditional: only recordings that are NOT
    private. A local provider would be harmless - nothing leaves the machine -
    but "harmless" is a judgement about privacy that belongs to the person who
    pinned the recording, not to a sweep that runs while nobody is watching.

    The consequence is real and worth knowing: a private recording is never
    named automatically, on any provider. The transcript page's own Suggest
    names button still works, because that is somebody deciding."""
    _diarized(conn, private=True)
    _set_provider(conn, "ollama")

    assert finalize.sweep_speaker_passes(conn) == []


def test_a_recording_with_no_diarization_has_nothing_to_ask_about(conn):
    media_id = seed_media(conn, title="Mono")
    run_id = seed_run(conn, media_id)
    with db.LOCK:
        # seed_run's words carry cluster labels; a recording transcribed with
        # diarization off has none, and that is the case under test.
        conn.execute("UPDATE word SET speaker=NULL WHERE run_id=?", (run_id,))
        conn.commit()
    _set_provider(conn, "ollama")

    assert finalize.sweep_speaker_passes(conn) == []


def test_a_second_sweep_finds_nothing_because_the_first_left_a_job(conn):
    """Otherwise every restart before the queue drains adds another copy of
    the same question."""
    _diarized(conn)
    _set_provider(conn, "ollama")
    first = finalize.sweep_speaker_passes(conn)

    assert first and finalize.sweep_speaker_passes(conn) == []


def test_a_trashed_recording_is_left_alone(conn):
    media_id, _ = _diarized(conn)
    with db.LOCK:
        conn.execute("UPDATE media SET trashed_at=1.0 WHERE id=?", (media_id,))
        conn.commit()
    _set_provider(conn, "ollama")

    assert finalize.sweep_speaker_passes(conn) == []


# --- ADR-016: with nobody chosen the sweep queues nothing and waits -------------------
#
# The sweep is the widest path in the app: it runs from the lifespan
# (`scribe/app.py:261`) over the whole back catalogue, with nobody at the
# screen. These drive it through a real app start on a scratch database, and
# count `job` rows, because "queued nothing" is a fact about rows.


@pytest.fixture
def scratch_paths(tmp_path, monkeypatch):
    """Everything an app start writes to, pointed at tmp_path.

    The lifespan also sweeps the recorder's chunks and the stderr files; a test
    that only moved the database would still walk the real library's folders.
    """
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    return data


def _seed_diarized(conn, n: int) -> list[int]:
    """`n` recordings the sweep would ask about: diarized, never asked, not private."""
    ids = []
    for i in range(n):
        media_id = seed_media(conn, title=f"Episode {i}")
        seed_run(conn, media_id)
        ids.append(media_id)
    return ids


def _start_the_app_once(db_path) -> None:
    """One lifespan, start to stop - which is what runs the sweep."""
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1"):
        pass


def _queued_media(conn) -> list[int]:
    return sorted(int(params["media_id"]) for params in _queued(conn))


def test_no_provider_chosen_means_the_speaker_pass_is_not_queued(conn, recording):
    """The pass is queued by the pipeline, with nobody there to choose."""
    media_id, run_id = recording

    assert finalize.queue_speaker_pass(conn, media_id, run_id, ["SPEAKER_00", "SPEAKER_01"]) is None
    assert _queued(conn) == []


def test_the_sweep_does_not_raise_when_nobody_has_chosen(conn):
    """Its loop has no try/except and it runs from the lifespan, so a guard
    written after `llm.provider_class()` instead of before it would turn
    "nothing to send" into "this machine has no app"."""
    _seed_diarized(conn, 1)

    assert finalize.sweep_speaker_passes(conn) == []


def test_an_app_start_with_no_provider_row_queues_nothing_for_a_back_catalogue(
    conn, tmp_path, scratch_paths
):
    """Point MyScribe at an existing library and skip the provider question:
    the first start used to queue one cloud job per diarized recording."""
    _seed_diarized(conn, 3)

    _start_the_app_once(tmp_path / "test.db")

    assert _queued(conn) == []


def test_the_pass_waits_and_catches_up_at_the_first_start_after_somebody_chose(
    conn, tmp_path, scratch_paths
):
    """Nothing is lost by waiting: the sweep marks nothing asked, so the four
    conditions still hold the next time the app starts."""
    ids = _seed_diarized(conn, 3)
    _start_the_app_once(tmp_path / "test.db")
    assert _queued(conn) == []

    _set_provider(conn, "ollama")
    _start_the_app_once(tmp_path / "test.db")

    assert _queued_media(conn) == sorted(ids)
