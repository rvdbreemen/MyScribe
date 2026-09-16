"""The whole pipeline, for real (Phase 2 Task 7).

Every other test file in this repository mocks something. This one mocks as
little as it can get away with, because the thing it is here to prove is
precisely what unit tests cannot: that six stages written on six different days
still hand each other what the next one expects, through the real runner, the
real registry and the real database.

What is real here: the HTTP ingest, ffprobe, ffmpeg, faster-whisper (`tiny`, on
CPU - the plan's model for tests that must run without a card), the word-level
speaker join, the finalize transaction, the FTS index, and `runner.main` walking
the registry exactly as the supervisor's child process does.

What is not real, and why:

* **The model is `tiny` on CPU.** Thirteen seconds instead of a download and a
  GPU. Nothing here asserts a word of English - `tiny` mishears plenty - so the
  contract under test is timestamps, speakers, counts and indexes, never text.
* **Diarization is stubbed in the second run**, and only the `diarize.diarize`
  call itself. Everything downstream of it - the stage, the embedding rows, the
  join, the word update - is the real code. Loading three gigabytes of pyannote
  to find out whether `attribute` can read what `diarize` writes would prove the
  same thing four minutes slower, and the GPU acceptance run in the task notes
  covers the weights.

The two runs share one fixture and one media row on purpose: a library where
the same recording is transcribed twice is exactly where `is_current` has to be
right, and running the pipeline twice is the only way to see the flip happen.
"""

import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scribe import db, fsbrowse, jobs, media, paths, runner
from scribe.app import create_app
from scribe.stages import (
    attribute, correct, diarize, finalize, prepare, probe, proxy, transcribe,
)
from scribe.stages.attribute import Turn

FIXTURES = Path(__file__).parent / "fixtures"
CLIP = FIXTURES / "clip30.wav"

# `tiny`, on the CPU, whatever card this machine happens to have. Every stage
# reads its device from the job params for exactly this reason.
CPU_PARAMS = {"model": "tiny", "device": "cpu", "compute_type": "int8"}

# Where the stubbed diarization puts the speaker change. The clip runs 0..30 s
# and `tiny` finds 32 words that end before 15 s and 42 that start after it, so
# both labels are comfortably populated whichever side the straddling word
# falls on.
SPLIT = 15.0
STUB_TURNS = [Turn(0.0, SPLIT, "SPEAKER_00"), Turn(SPLIT, 30.0, "SPEAKER_01")]
STUB_VECTORS = {"SPEAKER_00": [0.5, -0.25, 0.125], "SPEAKER_01": [-1.0, 0.0, 2.0]}

# The stage names the plan's global constraints fix, in order. `enhance` is
# reserved and deliberately absent until it ships with its A/B harness.
# `correct` joined the list in Phase 6 Task 5: the glossary's post-pass runs
# after attribution and before finalize, so a run becomes the current one with
# its corrections already in place. It changes nothing about the words
# themselves (ADR-003), which is why the assertions below it still hold.
# `proxy` joined after prepare in TASK-057: it makes the copy a browser seeks
# exactly when the original is a VBR MP3, and for this wav it does nothing.
EXPECTED_STAGES = [
    "probe", "prepare", "proxy", "transcribe", "diarize", "attribute", "correct", "finalize",
]


# --- the two pipeline runs ------------------------------------------------------


def _stub_diarize(monkeypatch):
    """Replace only the pyannote call; the diarize stage around it stays real."""

    def fake(wav, *, on_progress, **kwargs):
        assert Path(wav).exists(), "diarize was handed a wav prepare never wrote"
        on_progress(0.5)
        on_progress(1.0)
        return list(STUB_TURNS), {
            label: diarize.encode_embedding(vector)
            for label, vector in STUB_VECTORS.items()
        }

    monkeypatch.setattr(diarize, "diarize", fake)


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    """Ingest the clip once, then transcribe it twice through the real runner.

    Module-scoped because it is the expensive fixture in this repository: two
    CPU transcriptions of half a minute of audio. Everything below reads its
    result; nothing below writes to the database, so the sharing is safe.
    """
    root = tmp_path_factory.mktemp("e2e")
    with pytest.MonkeyPatch.context() as mp:
        data = root / "data"
        mp.setattr(paths, "DATA_DIR", data)
        mp.setattr(paths, "DB_PATH", data / "myscribe.db")
        mp.setattr(paths, "MEDIA_DIR", data / "media")
        mp.setattr(paths, "LOGS_DIR", data / "logs")
        mp.setattr(paths, "WORK_DIR", data / "work")
        mp.setattr(paths, "MODELS_DIR", data / "models")
        # The clip lives in the repository, which need not be on the home
        # drive `POST /api/media` allows by default.
        mp.setattr(fsbrowse, "ALLOWED_ROOTS", (FIXTURES,))
        paths.ensure_dirs()

        app = create_app(db_path=paths.DB_PATH, start_supervisor=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            # A second connection, the way the runner child has its own.
            conn = db.connect(paths.DB_PATH)
            try:
                plain = _run_one(
                    client, conn, params={**CPU_PARAMS, "diarize": False}
                )
                with pytest.MonkeyPatch.context() as inner:
                    _stub_diarize(inner)
                    spoken = _run_one(client, conn, params=CPU_PARAMS)

                yield SimpleNamespace(
                    conn=conn,
                    plain=plain,  # run 1: no diarization
                    spoken=spoken,  # run 2: diarization, then the join
                    work_dir=paths.WORK_DIR,
                )
            finally:
                conn.close()


def _run_one(client, conn, *, params):
    """POST the clip, claim the job, run it in this process; returns the facts."""
    response = client.post("/api/media", json={"path": str(CLIP), "params": params})
    assert response.status_code == 201, response.text
    body = response.json()
    job_id = body["job_id"]

    # What the supervisor does before spawning the child, done by hand.
    claimed = jobs.claim_next(conn)
    assert claimed is not None and claimed["id"] == job_id

    exit_code = runner.main([str(job_id)])

    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    return SimpleNamespace(
        media=body,
        job_id=job_id,
        job=job,
        run_id=job["run_id"],
        exit_code=exit_code,
        params=params,
    )


def words_of(conn, run_id):
    return conn.execute(
        "SELECT * FROM word WHERE run_id=? ORDER BY idx", (run_id,)
    ).fetchall()


def segments_of(conn, run_id):
    return conn.execute(
        "SELECT * FROM segment WHERE run_id=? ORDER BY idx", (run_id,)
    ).fetchall()


def run_row(conn, run_id):
    return conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()


# --- the job finished -----------------------------------------------------------


def test_both_jobs_run_to_done(pipeline):
    for outcome in (pipeline.plain, pipeline.spoken):
        assert outcome.exit_code == 0
        assert outcome.job["status"] == "done"
        assert outcome.job["error_code"] is None
        assert outcome.job["finished_at"] is not None


def test_every_stage_of_the_pipeline_actually_ran(pipeline):
    """The stage events, in order, for both jobs.

    The cheapest possible proof that the registry was walked rather than
    short-circuited somewhere in the middle.
    """
    for outcome in (pipeline.plain, pipeline.spoken):
        events = jobs.events_after(pipeline.conn, outcome.job_id, 0)
        names = [e["payload"]["name"] for e in events if e["kind"] == "stage"]
        assert names == EXPECTED_STAGES


def test_every_stage_recorded_a_timing_for_the_eta(pipeline):
    rows = pipeline.conn.execute(
        "SELECT stage, model, media_duration FROM stage_perf ORDER BY id"
    ).fetchall()

    assert [r["stage"] for r in rows] == EXPECTED_STAGES * 2
    # Filed against the duration probe measured, or eta_seconds discards them.
    assert all(r["media_duration"] == pytest.approx(30.0, abs=0.2) for r in rows)
    assert all(r["model"] == "tiny" for r in rows)


# --- one media row, two runs ----------------------------------------------------


def test_the_same_file_ingested_twice_is_one_media_row(pipeline):
    assert pipeline.plain.media["deduped"] is False
    assert pipeline.spoken.media["deduped"] is True
    assert pipeline.spoken.media["id"] == pipeline.plain.media["id"]
    assert pipeline.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1


def test_the_latest_run_is_the_current_one_and_the_previous_is_not(pipeline):
    assert run_row(pipeline.conn, pipeline.spoken.run_id)["is_current"] == 1
    assert run_row(pipeline.conn, pipeline.plain.run_id)["is_current"] == 0

    current = pipeline.conn.execute(
        "SELECT COUNT(*) FROM run WHERE media_id=? AND is_current=1",
        (pipeline.plain.media["id"],),
    ).fetchone()[0]
    assert current == 1


def test_both_runs_recorded_how_fast_they_were(pipeline):
    for outcome in (pipeline.plain, pipeline.spoken):
        row = run_row(pipeline.conn, outcome.run_id)
        assert row["xrt"] is not None
        assert row["xrt"] > 0
        assert row["model"] == "tiny"
        assert row["compute_type"] == "int8"


# --- the words are canonical ----------------------------------------------------


def test_the_transcript_is_stored_as_words_with_usable_timestamps(pipeline):
    words = words_of(pipeline.conn, pipeline.plain.run_id)

    assert len(words) > 10
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)
    assert all(w["end"] >= w["start"] for w in words)
    assert all(0.0 <= w["start"] <= 30.5 for w in words)
    assert all(w["probability"] is not None for w in words)


def test_both_runs_transcribed_the_same_recording_to_the_same_words(pipeline):
    """`tiny` on CPU is deterministic, so the two runs must agree.

    Not a claim about accuracy: it is a claim that the second run transcribed
    the same audio, which is what makes the speaker comparison below a
    comparison of speakers and not of two different transcripts.

    It is also the one assertion in this file that can fail without anything in
    `scribe` having changed. Greedy decoding at temperature 0 is deterministic
    for a fixed model and backend, so a faster-whisper or CTranslate2 bump that
    shifts a token by a hair breaks this and nothing else. If it ever fails
    alone, suspect the pins in uv.lock before suspecting the
    pipeline.
    """
    plain = [w["text"] for w in words_of(pipeline.conn, pipeline.plain.run_id)]
    spoken = [w["text"] for w in words_of(pipeline.conn, pipeline.spoken.run_id)]

    assert plain == spoken


# --- the speaker join -----------------------------------------------------------


def test_without_diarization_the_words_carry_no_speaker(pipeline):
    words = words_of(pipeline.conn, pipeline.plain.run_id)

    assert words
    assert all(w["speaker"] is None for w in words)


def test_with_diarization_every_word_lands_on_the_speaker_who_said_it(pipeline):
    """The join, end to end: pyannote turns in, `word.speaker` out.

    This is the one path where a silent bug mislabels a whole transcript, and
    the only test in the suite that follows it all the way from a turn to a
    committed row.
    """
    words = words_of(pipeline.conn, pipeline.spoken.run_id)

    assert words
    assert all(w["speaker"] is not None for w in words)
    assert {w["speaker"] for w in words} == {"SPEAKER_00", "SPEAKER_01"}

    early = [w for w in words if w["end"] <= SPLIT]
    late = [w for w in words if w["start"] >= SPLIT]
    assert len(early) > 10 and len(late) > 10, "the clip must straddle the split"
    assert all(w["speaker"] == "SPEAKER_00" for w in early)
    assert all(w["speaker"] == "SPEAKER_01" for w in late)


def test_the_speaker_changes_exactly_once_where_the_turn_does(pipeline):
    words = words_of(pipeline.conn, pipeline.spoken.run_id)

    changes = [
        (previous["speaker"], current["speaker"])
        for previous, current in zip(words, words[1:])
        if previous["speaker"] != current["speaker"]
    ]

    assert changes == [("SPEAKER_00", "SPEAKER_01")]


def test_the_speaker_embeddings_survive_the_round_trip(pipeline):
    rows = pipeline.conn.execute(
        "SELECT * FROM speaker_embedding WHERE run_id=? ORDER BY cluster_label",
        (pipeline.spoken.run_id,),
    ).fetchall()

    assert [r["cluster_label"] for r in rows] == ["SPEAKER_00", "SPEAKER_01"]
    assert [diarize.decode_embedding(r["embedding"]) for r in rows] == [
        pytest.approx(STUB_VECTORS["SPEAKER_00"]),
        pytest.approx(STUB_VECTORS["SPEAKER_01"]),
    ]


def test_a_run_without_diarization_stores_no_embeddings(pipeline):
    count = pipeline.conn.execute(
        "SELECT COUNT(*) FROM speaker_embedding WHERE run_id=?",
        (pipeline.plain.run_id,),
    ).fetchone()[0]
    assert count == 0


# --- the search index -----------------------------------------------------------


def test_every_segment_of_every_run_reached_the_search_index(pipeline):
    for outcome in (pipeline.plain, pipeline.spoken):
        segments = segments_of(pipeline.conn, outcome.run_id)
        assert segments
        assert finalize.indexed_segment_count(
            pipeline.conn, outcome.run_id
        ) == len(segments)


def test_a_word_from_the_clip_finds_its_segment(pipeline):
    """A real MATCH against the real index.

    The search term is read back out of the stored transcript rather than
    written into this test: `tiny` mishears the clip differently on every
    version of faster-whisper, and a hard-coded word would make this test a
    claim about the model's accuracy instead of about the index.
    """
    segments = segments_of(pipeline.conn, pipeline.spoken.run_id)
    term = max(re.findall(r"[A-Za-z]{5,}", segments[0]["text"]), key=len)

    hits = pipeline.conn.execute(
        "SELECT rowid FROM segment_fts WHERE segment_fts MATCH ?", (term,)
    ).fetchall()

    assert segments[0]["id"] in [h["rowid"] for h in hits]


# --- the scratch is cleaned up --------------------------------------------------


def test_the_work_directory_is_gone_when_the_job_is_done(pipeline):
    for outcome in (pipeline.plain, pipeline.spoken):
        assert not (pipeline.work_dir / str(outcome.job_id)).exists()


# --- the registry ---------------------------------------------------------------


def test_the_transcribe_registry_is_the_whole_pipeline_in_order():
    assert [name for name, _ in runner.STAGES["transcribe"]] == EXPECTED_STAGES
    assert [fn for _, fn in runner.STAGES["transcribe"]] == [
        probe.run,
        prepare.run,
        proxy.run,
        transcribe.run,
        diarize.run,
        attribute.run,
        correct.run,
        finalize.run,
    ]


def test_the_proxy_stage_keeps_its_promise_for_a_filesystem_error_too(
    conn, data_dir, monkeypatch, tmp_path
):
    """TASK-062: "its failure is not the job's" - for every failure.

    The stage catches ProxyError and lets the job go on, which is what its
    docstring promises. An OSError from the same call - a full disk, a denied
    directory, Windows refusing to replace a file another process has open -
    walked out of the stage instead, and the runner turns that into a failed
    transcription. The words do not depend on the proxy either way.
    """
    media_row = media.ingest_path(conn, CLIP)
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_row["id"])
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    ctx = runner.RunnerContext(
        conn=conn,
        job=job,
        params={},
        report=lambda p: None,
        cancelled=lambda: False,
        media_path=tmp_path / "talk.mkv",  # a container that never seeks exactly
    )
    (tmp_path / "talk.mkv").write_bytes(b"not really a matroska file")

    def refuse(*args, **kwargs):
        raise PermissionError("[WinError 5] Access is denied: proxy/ab.m4a.part")

    monkeypatch.setattr(proxy.playback, "ensure_proxy", refuse)

    proxy.run(ctx)  # must not raise: the job keeps its words


def test_enhance_is_reserved_and_not_yet_registered():
    assert "enhance" not in [name for name, _ in runner.STAGES["transcribe"]]


# --- the error taxonomy ---------------------------------------------------------


def test_a_missing_file_is_named_as_a_missing_file():
    assert runner._error_code(FileNotFoundError("gone")) == "FILE_MISSING"


def test_a_locked_file_is_named_as_a_locked_file():
    assert runner._error_code(PermissionError("in use by another process")) == "FILE_LOCKED"


def test_a_full_disk_is_named_as_a_full_disk():
    assert runner._error_code(OSError(28, "No space left on device")) == "DISK_FULL"


def test_an_ordinary_os_error_is_not_mistaken_for_a_full_disk():
    assert runner._error_code(OSError(5, "I/O error")) == "RUNTIME"


def test_a_cuda_out_of_memory_is_named_as_one_without_importing_torch(monkeypatch):
    """The lookup goes through `sys.modules`, so it costs nothing when unused.

    Torch is two seconds of import that the CPU suite should not have to pay on
    the failure path of a job that never touched a GPU. A stand-in module proves
    the mechanism; the test below proves the real class matches it.
    """

    class OutOfMemoryError(RuntimeError):
        pass

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(OutOfMemoryError=OutOfMemoryError)),
    )

    assert runner._error_code(OutOfMemoryError("CUDA out of memory")) == "CUDA_OOM"


def test_the_real_torch_out_of_memory_class_is_recognised():
    torch = pytest.importorskip("torch")

    assert runner._error_code(torch.cuda.OutOfMemoryError("CUDA out of memory")) == "CUDA_OOM"


def test_a_runtime_error_is_still_a_runtime_error_when_torch_never_loaded(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    assert runner._error_code(RuntimeError("something else entirely")) == "RUNTIME"


# --- finalize on its own --------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    (data / "media").mkdir(parents=True)
    return data


def a_finished_run(conn, *, media_id=None, segments=1, words=2):
    """A media row, a job, a run, and the segments and words transcribe left."""
    if media_id is None:
        row = media.ingest_path(conn, CLIP)
        media_id = row["id"]
        with db.LOCK:
            conn.execute(
                "UPDATE media SET duration=30.0 WHERE id=?", (media_id,)
            )
            conn.commit()
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_id)
    jobs.claim_next(conn)
    with db.LOCK:
        # Back-date the claim by a second. claim_next stamps started_at with
        # time.time(), and a test that finalizes immediately can measure an
        # elapsed of exactly 0.0 - at which point xrt() correctly returns None
        # ("not measured" is not "infinitely fast") and an assertion like
        # `payload["xrt"] > 0` dies with a TypeError instead of failing. Seen
        # once in a full-suite run and not reproducible alone, which is the
        # signature of a test that depends on clock luck rather than on the
        # behaviour it means to check.
        conn.execute(
            "UPDATE job SET started_at=started_at-1.0 WHERE id=?", (job_id,)
        )
        cur = conn.execute(
            "INSERT INTO run(media_id, model, compute_type, created_at)"
            " VALUES (?, 'tiny', 'int8', 0)",
            (media_id,),
        )
        run_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO segment(run_id, idx, start, end, text) VALUES (?, ?, ?, ?, ?)",
            [(run_id, i, i * 1.0, i + 1.0, f"segment {i} of vogon poetry") for i in range(segments)],
        )
        conn.executemany(
            "INSERT INTO word(run_id, idx, start, end, text) VALUES (?, ?, ?, ?, ?)",
            [(run_id, i, i * 1.0, i + 1.0, f" w{i}") for i in range(words)],
        )
        conn.execute("UPDATE job SET run_id=? WHERE id=?", (run_id, job_id))
        conn.commit()
    return job_id, run_id, media_id


def finalize_ctx(conn, job_id, run_id, words=None, progress=None):
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    ctx = runner.RunnerContext(
        conn=conn,
        job=job,
        params=json.loads(job["params_json"] or "{}"),
        report=(lambda p: None) if progress is None else progress.append,
        cancelled=lambda: False,
        media_path=None,
    )
    ctx.state.update(run_id=run_id, words=words if words is not None else [])
    return ctx


def test_finalize_refuses_to_guess_which_run_it_is_finishing(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)
    ctx = finalize_ctx(conn, job_id, run_id)
    ctx.state.pop("run_id")

    with pytest.raises(RuntimeError, match="run_id"):
        finalize.run(ctx)


def test_a_failure_after_the_commit_does_not_undo_a_finished_transcript(
    conn, data_dir, monkeypatch
):
    """TASK-060: the words are committed before the speaker pass is queued.

    Anything raising after `_commit` leaves the stage, and the runner turns any
    exception into a failed verdict - for a transcript that is on disk, current
    and complete. The user then sees a finished transcript under a job that
    says it failed, and a Retry that spends a second GPU pass to replace a good
    run with another one.
    """
    job_id, run_id, _ = a_finished_run(conn, words=3)

    def refuse(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(finalize, "queue_speaker_pass", refuse)

    words = [
        {"idx": 0, "speaker": "SPEAKER_00"},
        {"idx": 1, "speaker": "SPEAKER_01"},
        {"idx": 2, "speaker": None},
    ]

    finalize.run(finalize_ctx(conn, job_id, run_id, words=words))

    row = conn.execute("SELECT is_current FROM run WHERE id=?", (run_id,)).fetchone()
    assert row["is_current"] == 1
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM word WHERE run_id=?", (run_id,)
    ).fetchone()["n"] == 3
    # Caught is not swallowed: the job's own stream says the pass was not queued.
    kinds = [
        r["kind"]
        for r in conn.execute("SELECT kind FROM job_event WHERE job_id=?", (job_id,))
    ]
    assert "speakers-queue-failed" in kinds
    assert "speakers-queued" not in kinds


def test_finalize_writes_the_speakers_attribute_worked_out(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn, words=3)
    words = [
        {"idx": 0, "speaker": "SPEAKER_00"},
        {"idx": 1, "speaker": "SPEAKER_01"},
        {"idx": 2, "speaker": None},
    ]

    finalize.run(finalize_ctx(conn, job_id, run_id, words=words))

    rows = conn.execute("SELECT * FROM word WHERE run_id=? ORDER BY idx", (run_id,)).fetchall()
    assert [r["speaker"] for r in rows] == ["SPEAKER_00", "SPEAKER_01", None]


def test_a_re_transcription_asks_who_is_speaking_again(conn, data_dir):
    """Robert, 2026-09-11: re-transcribing means analysing the speakers again,
    even when every name carried over by voice (TASK-031). The names stay on
    the new run meanwhile, and the pass never writes over one a person typed
    (llm.tasks.apply_speakers). Until then finalize asked only about clusters
    left without a name, and a run whose names all carried over asked nothing."""
    voices = [{"idx": i, "speaker": "SPEAKER_00" if i < 3 else "SPEAKER_01"} for i in range(6)]
    job_a, run_a, media_id = a_finished_run(conn, words=6)
    finalize.run(finalize_ctx(conn, job_a, run_a, words=voices))
    with db.LOCK:
        conn.executemany(
            "INSERT INTO speaker_label(run_id, cluster_label, display_name, source) VALUES (?, ?, ?, ?)",
            [(run_a, "SPEAKER_00", "Arthur", "llm"), (run_a, "SPEAKER_01", "Ford", "human")],
        )
        conn.commit()

    job_b, run_b, _ = a_finished_run(conn, media_id=media_id, words=6)
    finalize.run(finalize_ctx(conn, job_b, run_b, words=voices))

    labels = {
        row["cluster_label"]: (row["display_name"], row["source"])
        for row in conn.execute("SELECT * FROM speaker_label WHERE run_id=?", (run_b,))
    }
    assert labels == {"SPEAKER_00": ("Arthur", "llm"), "SPEAKER_01": ("Ford", "human")}
    asked = [
        json.loads(row["params_json"])
        for row in conn.execute("SELECT params_json FROM job WHERE type='llm' ORDER BY id")
    ]
    assert [(p["kind"], p["run_id"]) for p in asked] == [("speakers", run_a), ("speakers", run_b)]
    assert [e["kind"] for e in jobs.events_after(conn, job_b, 0)].count("speakers-queued") == 1


def test_names_come_from_the_run_that_was_current_not_from_a_failed_one(conn, data_dir):
    """A re-transcription that failed after its transcribe stage leaves a run
    that never became current and has no names. Found in review 2026-09-11:
    the next re-transcription inherited from that run - the newest - and the
    name a person typed was gone from the transcript they opened."""
    voices = [{"idx": i, "speaker": "SPEAKER_00" if i < 3 else "SPEAKER_01"} for i in range(6)]
    job_a, run_a, media_id = a_finished_run(conn, words=6)
    finalize.run(finalize_ctx(conn, job_a, run_a, words=voices))
    with db.LOCK:
        conn.executemany(
            "INSERT INTO speaker_label(run_id, cluster_label, display_name, source) VALUES (?, ?, ?, ?)",
            [(run_a, "SPEAKER_00", "Arthur", "llm"), (run_a, "SPEAKER_01", "Ford", "human")],
        )
        conn.execute(  # the failed attempt: a run row, never current, no names
            "INSERT INTO run(media_id, model, compute_type, created_at) VALUES (?, 'tiny', 'int8', 0)",
            (media_id,),
        )
        conn.commit()

    job_c, run_c, _ = a_finished_run(conn, media_id=media_id, words=6)
    finalize.run(finalize_ctx(conn, job_c, run_c, words=voices))

    labels = {
        row["cluster_label"]: (row["display_name"], row["source"])
        for row in conn.execute("SELECT * FROM speaker_label WHERE run_id=?", (run_c,))
    }
    assert labels == {"SPEAKER_00": ("Arthur", "llm"), "SPEAKER_01": ("Ford", "human")}


def test_finalize_only_clears_the_current_run_of_this_media(conn, data_dir):
    """Two files, each with a current run; finishing one must not blank the other."""
    job_a, run_a, media_a = a_finished_run(conn)
    finalize.run(finalize_ctx(conn, job_a, run_a))

    other = media.ingest_stream(conn, __import__("io").BytesIO(b"other bytes"), "b.wav")
    job_b, run_b, _ = a_finished_run(conn, media_id=other["id"])
    finalize.run(finalize_ctx(conn, job_b, run_b))

    assert run_row(conn, run_a)["is_current"] == 1
    assert run_row(conn, run_b)["is_current"] == 1


def test_finalize_refuses_a_run_the_search_index_has_not_got(conn, data_dir):
    """A segment that never reached FTS is a transcript search cannot find.

    Provoked the way it would actually happen - an insert that bypassed the
    trigger - because the check has to compare the index against the segments,
    not the segments against themselves.
    """
    job_id, run_id, _ = a_finished_run(conn, segments=2)
    with db.LOCK:
        conn.execute("DROP TRIGGER segment_ai")
        conn.execute(
            "INSERT INTO segment(run_id, idx, start, end, text)"
            " VALUES (?, 9, 9.0, 10.0, 'never indexed')",
            (run_id,),
        )
        conn.commit()

    with pytest.raises(finalize.IndexOutOfStep, match="3"):
        finalize.run(finalize_ctx(conn, job_id, run_id))

    # And the run it refused is not the one a user would now be shown.
    assert run_row(conn, run_id)["is_current"] == 0
    assert run_row(conn, run_id)["xrt"] is None


def test_finalize_rolls_back_the_speakers_it_had_already_written(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn, words=2)
    with db.LOCK:
        conn.execute("DROP TRIGGER segment_ai")
        conn.execute(
            "INSERT INTO segment(run_id, idx, start, end, text)"
            " VALUES (?, 9, 9.0, 10.0, 'never indexed')",
            (run_id,),
        )
        conn.commit()

    with pytest.raises(finalize.IndexOutOfStep):
        finalize.run(
            finalize_ctx(conn, job_id, run_id, words=[{"idx": 0, "speaker": "A"}])
        )

    rows = conn.execute("SELECT speaker FROM word WHERE run_id=?", (run_id,)).fetchall()
    assert all(r["speaker"] is None for r in rows)


def test_finalize_deletes_the_jobs_work_directory(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)
    work = paths.job_work_dir(job_id)
    work.mkdir(parents=True)
    (work / "audio.wav").write_bytes(b"scratch")

    finalize.run(finalize_ctx(conn, job_id, run_id))

    assert not work.exists()


def test_finalize_does_not_mind_a_job_that_left_no_scratch(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)

    finalize.run(finalize_ctx(conn, job_id, run_id))  # no work dir at all

    assert run_row(conn, run_id)["is_current"] == 1


def test_finalize_announces_what_it_finished(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn, segments=2, words=3)

    finalize.run(
        finalize_ctx(
            conn, job_id, run_id, words=[{"idx": i, "speaker": "A"} for i in range(3)]
        )
    )

    events = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "finalize"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["run_id"] == run_id
    assert payload["n_segments"] == 2
    assert payload["n_speakers"] == 1
    assert payload["xrt"] > 0


def test_finalize_finishes_at_one(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)
    progress: list[float] = []

    finalize.run(finalize_ctx(conn, job_id, run_id, progress=progress))

    assert progress[-1] == 1.0


# --- xrt ------------------------------------------------------------------------


def test_xrt_is_how_many_times_faster_than_realtime_the_run_was():
    assert finalize.xrt(300.0, 20.0) == pytest.approx(15.0)
    assert finalize.xrt(30.0, 60.0) == pytest.approx(0.5)


def test_xrt_of_a_file_nobody_measured_is_unknown_rather_than_zero():
    assert finalize.xrt(0.0, 20.0) is None
    assert finalize.xrt(300.0, 0.0) is None
    assert finalize.xrt(300.0, -1.0) is None


# --- the attribute stage --------------------------------------------------------


def test_the_attribute_stage_joins_the_words_onto_the_turns(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)
    ctx = finalize_ctx(conn, job_id, run_id)
    ctx.state.update(
        words=[
            {"idx": 0, "start": 0.1, "end": 0.5, "text": " a"},
            {"idx": 1, "start": 6.0, "end": 6.5, "text": " b"},
        ],
        turns=[Turn(0.0, 5.0, "A"), Turn(5.0, 10.0, "B")],
    )

    attribute.run(ctx)

    assert [w["speaker"] for w in ctx.state["words"]] == ["A", "B"]


def test_the_attribute_stage_leaves_speakers_none_when_nobody_diarized(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)
    ctx = finalize_ctx(conn, job_id, run_id)
    ctx.state.update(
        words=[{"idx": 0, "start": 0.1, "end": 0.5, "text": " a"}], turns=[]
    )

    attribute.run(ctx)

    assert [w["speaker"] for w in ctx.state["words"]] == [None]


def test_the_attribute_stage_reports_progress_across_the_words(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)
    progress: list[float] = []
    ctx = finalize_ctx(conn, job_id, run_id, progress=progress)
    ctx.state.update(
        words=[
            {"idx": i, "start": i * 0.1, "end": i * 0.1 + 0.05, "text": " w"}
            for i in range(attribute.PROGRESS_CHUNK * 3)
        ],
        turns=[Turn(0.0, 1000.0, "A")],
    )

    attribute.run(ctx)

    assert len(progress) >= 3
    assert progress == sorted(progress)
    assert progress[-1] == 1.0


def test_the_attribute_stage_without_a_transcription_refuses_to_guess(conn, data_dir):
    job_id, run_id, _ = a_finished_run(conn)
    ctx = finalize_ctx(conn, job_id, run_id)
    ctx.state.pop("words")

    with pytest.raises(RuntimeError, match="words"):
        attribute.run(ctx)


# --- POST /api/media ------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    # The clip is in the repository and the wrong paths below are in tmp_path;
    # both must be under the roots, or a 403 would hide the answer under test.
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (FIXTURES, tmp_path))
    app = create_app(db_path=data / "myscribe.db", start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def test_posting_a_path_ingests_it_and_queues_a_transcribe_job(client):
    response = client.post("/api/media", json={"path": str(CLIP)})

    assert response.status_code == 201
    body = response.json()
    assert body["sha256"]
    assert body["orig_name"] == "clip30.wav"
    assert body["deduped"] is False
    assert body["job_id"]

    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["type"] == "transcribe"
    assert job["status"] == "queued"
    assert job["media_id"] == body["id"]


def test_the_job_carries_the_parameters_the_caller_chose(client):
    response = client.post(
        "/api/media",
        json={"path": str(CLIP), "title": "Hackerspace", "params": CPU_PARAMS},
    )

    body = response.json()
    assert body["title"] == "Hackerspace"
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert json.loads(job["params_json"]) == CPU_PARAMS


def test_posting_the_same_path_twice_reuses_the_media_and_queues_a_second_job(client):
    first = client.post("/api/media", json={"path": str(CLIP)}).json()
    second = client.post("/api/media", json={"path": str(CLIP)}).json()

    assert second["id"] == first["id"]
    assert second["deduped"] is True
    assert second["job_id"] != first["job_id"]


def test_posting_a_path_that_is_not_there_is_a_404_not_a_crash(client, tmp_path):
    response = client.post("/api/media", json={"path": str(tmp_path / "nope.wav")})

    assert response.status_code == 404
    assert "nope.wav" in response.json()["detail"]


def test_posting_a_directory_is_refused_rather_than_ingested(client, tmp_path):
    response = client.post("/api/media", json={"path": str(tmp_path)})

    assert response.status_code == 400


def test_posting_a_path_that_is_not_media_is_refused_before_it_is_read(client, tmp_path):
    """TASK-059: the door reads what it is handed, so it must want media.

    The roots say where the app may look, not what it may take. A caller that
    can reach 127.0.0.1 but not the file itself - another account, a sandboxed
    process - otherwise has the web process read it with the user's rights and
    hand the bytes back through the library's download route.
    """
    secret = tmp_path / "id_rsa"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")

    response = client.post("/api/media", json={"path": str(secret)})

    assert response.status_code == 415
    assert "id_rsa" in response.json()["detail"]
    conn = sqlite3.connect(paths.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        taken = conn.execute(
            "SELECT COUNT(*) AS n FROM media WHERE orig_name=?", ("id_rsa",)
        ).fetchone()["n"]
    finally:
        conn.close()
    assert taken == 0
    assert not list(paths.MEDIA_DIR.rglob("*")), "nothing may reach the store"


def test_posting_no_path_at_all_says_so(client):
    assert client.post("/api/media", json={}).status_code == 400


def test_posting_something_that_is_not_json_says_so(client):
    response = client.post(
        "/api/media", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400


def test_uploading_a_file_ingests_the_bytes_and_queues_a_job(client):
    with open(CLIP, "rb") as fh:
        response = client.post(
            "/api/media",
            files={"file": ("meeting.wav", fh, "audio/wav")},
            data={"title": "Meeting", "params": json.dumps(CPU_PARAMS)},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["orig_name"] == "meeting.wav"
    assert body["title"] == "Meeting"
    assert body["size_bytes"] == CLIP.stat().st_size
    assert body["sha256"] == media.hash_file(CLIP)

    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert json.loads(job["params_json"]) == CPU_PARAMS


def test_a_multipart_post_without_a_file_part_says_so(client):
    response = client.post("/api/media", files={}, data={"title": "nothing"})

    assert response.status_code == 400


# --- the acceptance run on the card ---------------------------------------------


@pytest.mark.gpu
def test_the_whole_pipeline_on_the_gpu_with_the_default_model(client, monkeypatch, capsys):
    """The same flow again, on the card, with the weights that actually ship.

    Everything above runs `tiny` on the CPU because it has to run anywhere.
    This one exists to prove the three things only a real card can show:

    * `large-v3-turbo` - the spec's default, chosen by passing no model at all -
      transcribes the clip through the registry rather than through a fixture;
    * Whisper's six gigabytes are gone before pyannote's three are asked for.
      Both models loading in one job on one 16 GB card is the global constraint
      about freeing VRAM between stages, and this is the only test that puts it
      to the test;
    * the xRT finalize records is a measurement of this machine.

    Read that xRT as a lower bound, not as throughput: the fixture is 30 s and
    CUDA warmup dominates a clip that short (the pinned stack
    measures ~1.1x on 30 s against 14.5x on five minutes). It is here to prove
    the number is real and positive, not to publish a benchmark.

    Diarization runs on substituted public components, for the reason
    test_stage_diarize spells out at length: `speaker-diarization-community-1`
    is gated and this account is not authorized, so the shipped pipeline cannot
    be built here at all. The constants are imported from that module rather
    than copied so there is one place to fix when the weights become reachable.
    """
    from scribe import cuda_setup

    cuda_setup.ensure_cuda_libs()
    import torch

    if not torch.cuda.is_available():
        pytest.skip("no CUDA device")

    import huggingface_hub
    import test_stage_diarize as diarize_tests
    from pyannote.audio.core.plda import PLDA
    from pyannote.audio.pipelines import SpeakerDiarization

    def build(source, *, device, token):
        try:
            pipeline = SpeakerDiarization(
                segmentation=diarize_tests.PUBLIC_SEGMENTATION,
                embedding=diarize_tests.PUBLIC_EMBEDDING,
                clustering="AgglomerativeClustering",
                segmentation_batch_size=32,
                embedding_batch_size=32,
                embedding_exclude_overlap=True,
                plda=object.__new__(PLDA),  # never read; see test_stage_diarize
                token=token,
            )
        except (OSError, huggingface_hub.errors.HfHubHTTPError) as exc:
            pytest.skip(f"public component weights unreachable: {exc}")
        pipeline.instantiate(diarize_tests.PUBLIC_PARAMS)
        return pipeline.to(torch.device(device))

    monkeypatch.setattr(diarize, "open_pipeline", build)

    conn = db.connect(paths.DB_PATH)
    try:
        # No model, no device: the defaults are what this test is about. No
        # pipeline either: since TASK-034 a job cannot name one and the door
        # refuses the key. `build` above ignores the source it is handed, so
        # the stage's default source is built from the public components.
        outcome = _run_one(client, conn, params={})

        assert outcome.exit_code == 0, outcome.job["error_detail"]
        assert outcome.job["status"] == "done"

        row = run_row(conn, outcome.run_id)
        assert row["model"] == transcribe.DEFAULT_MODEL
        assert row["compute_type"] == "float16"
        assert row["is_current"] == 1
        assert row["xrt"] > 0

        words = words_of(conn, outcome.run_id)
        assert len(words) > 10
        assert all(w["speaker"] is not None for w in words)

        with capsys.disabled():
            print(
                f"\n[acceptance] {transcribe.DEFAULT_MODEL} + diarization on"
                f" {torch.cuda.get_device_name(0)}:"
                f" {row['xrt']:.2f}x realtime over 30 s (warmup-dominated)"
            )
    finally:
        conn.close()
