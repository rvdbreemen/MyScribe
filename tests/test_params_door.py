"""TASK-034: a job's params come in through a door, and the door is sieved.

Found 2026-09-11 by the TASK-028 reachability workflow. `POST /api/media`
took a raw `params` object and put it on the transcribe job unread, and the
diarize stage read `params["diarization_model"]` straight into pyannote's
`Pipeline.from_pretrained` - which resolves the class a pipeline config names
and loads its checkpoints with `weights_only=False`. So whoever could post to
127.0.0.1:4242 chose code the runner child executed. The browser route was
already closed by `scribe.guard`; what remained was any local process, another
Windows account included, because loopback is shared.

Three layers, and each test below names the one it holds:

* **The door refuses.** A params object carrying a key outside
  `options.PARAM_KEYS` is a 400 that names the key, before a byte is filed or
  a job queued. `/api/media` is the only route that reads a client's `params`
  object at all (a grep for it finds `scribe/app.py` and nothing else); every
  other door builds its params from validated fields, and the tests here show
  a `diarization_model` field posted to each of them reaching no job.
* **Retry replays only what a transcribe job takes.** A job stored before the
  fix is never re-validated, so the retry route sieves a transcribe request
  wherever one is stored - a transcribe job's params, an ingest_url job's
  nested options - to the door's keys and a speech model, and leaves every
  other key of every other type alone.
* **The stage stops listening.** The diarize stage no longer reads the key at
  all, which is the only thing that covers a job queued before the upgrade
  and never retried.

And the other half: every producer that legitimately writes a transcribe
job's params - the dialog's fields, a params object of stage keys, a feed
poll through a URL import with its hotwords, a bulk re-transcribe, a retry -
still does.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe import db, fsbrowse, jobs, options, paths, runner
from scribe.app import create_app
from scribe.ingest import feeds, urls
from scribe.options import TranscribeOptions
from scribe.stages import diarize, transcribe, url_stage
from seed import seed_media
from test_ingest_urls import FakeYdl, build_returning, single_info
from test_stage_diarize import FakePipeline, FakeWaveform

HX = {"HX-Request": "true"}

FIXTURES = Path(__file__).parent / "fixtures"
CHUNK = FIXTURES / "chunk.webm"
WEBM = "audio/webm;codecs=opus"

FEED_URL = "https://feeds.test/podcast.xml"
EPISODE_URL = "https://cdn.test/ep/default.mp3?d=1"

# The key this task is about: a pyannote pipeline id or directory, read by the
# diarize stage until TASK-034 and handed to `Pipeline.from_pretrained`.
FOREIGN = "attacker/pipeline"


# --- fixtures ---------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    (data / "media").mkdir(parents=True)
    (data / "work").mkdir(parents=True)
    return data


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


@pytest.fixture
def client(db_path, data_dir, tmp_path, monkeypatch):
    # The path doors may read from tmp_path and nowhere else.
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def src(tmp_path):
    """A file under the allowed roots that looks enough like a wav to carry the
    extension; no stage ever decodes it, because no job here runs."""
    path = tmp_path / "src" / "talk.wav"
    path.parent.mkdir()
    path.write_bytes(b"RIFF" + b"x" * 64)
    return path


def _jobs(conn, type_=None) -> list:
    if type_ is None:
        return conn.execute("SELECT * FROM job ORDER BY id").fetchall()
    return conn.execute("SELECT * FROM job WHERE type=? ORDER BY id", (type_,)).fetchall()


def _params(row) -> dict:
    return json.loads(row["params_json"] or "{}")


def _media(conn) -> list:
    return conn.execute("SELECT * FROM media ORDER BY id").fetchall()


def _stored_job(conn, type_, params, *, status="failed", media_id=None) -> int:
    """A job row as an earlier life of the app left it - params and all,
    written straight to the table because no door would write them now."""
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO job(type, media_id, status, params_json, created_at)"
            " VALUES (?, ?, ?, ?, 0)",
            (type_, media_id, status, json.dumps(params)),
        )
        conn.commit()
    return cur.lastrowid


def _retry(client, job_id) -> int:
    resp = client.post(f"/api/jobs/{job_id}/retry")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _row(conn, job_id):
    return conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()


# --- the door: POST /api/media refuses a key it does not know ------------------------

# Three keys, three reasons they must not pass: the pipeline id this task is
# about, a real key of another job type (an ingest_url job's feed id), and a
# real key of a third (a language-model job's prompt). Beside legitimate keys,
# so the refusal is about the stranger and not about the company it keeps.
REFUSED_KEYS = ["diarization_model", url_stage.FEED_KEY, "prompt"]


@pytest.mark.parametrize("key", REFUSED_KEYS)
def test_a_path_post_whose_params_carry_a_foreign_key_is_refused_by_name(client, conn, src, key):
    resp = client.post(
        "/api/media",
        json={"path": str(src), "params": {"model": "tiny", "language": "nl", key: FOREIGN}},
    )

    assert resp.status_code == 400, resp.text
    assert key in resp.json()["detail"]
    assert _jobs(conn) == [] and _media(conn) == []


@pytest.mark.parametrize("key", REFUSED_KEYS)
def test_an_upload_whose_params_carry_a_foreign_key_is_refused_by_name(client, conn, key):
    resp = client.post(
        "/api/media",
        files={"file": ("talk.wav", b"RIFF" + b"u" * 64, "audio/wav")},
        data={"params": json.dumps({"model": "tiny", key: FOREIGN})},
    )

    assert resp.status_code == 400, resp.text
    assert key in resp.json()["detail"]
    # Refused before the bytes were filed, not after: no orphan media row.
    assert _jobs(conn) == [] and _media(conn) == []


# --- every other door: a diarization_model field reaches no job ----------------------


def _carries_it(params: dict) -> bool:
    """True if the key sits on the job or in the options an ingest_url job
    will hand its transcribe job (`url_stage.transcribe_params`)."""
    nested = params.get(url_stage.OPTIONS_KEY) or {}
    return "diarization_model" in params or "diarization_model" in nested


SMUGGLED = {
    "diarization_model": FOREIGN,
    # And as a params object the form doors never read: they build their
    # params from the option fields, so this must be ignored, not merged.
    "params": json.dumps({"diarization_model": FOREIGN}),
}


def test_the_api_media_door_ignores_the_key_as_a_top_level_field(client, conn, src):
    resp = client.post("/api/media", json={"path": str(src), "diarization_model": FOREIGN})

    assert resp.status_code == 201, resp.text
    (job,) = _jobs(conn)
    assert not _carries_it(_params(job))


def test_the_dialog_upload_door_ignores_it(client, conn):
    resp = client.post(
        "/transcribe/upload",
        data={"tier": "turbo", **SMUGGLED},
        files={"files": ("talk.wav", b"RIFF" + b"d" * 64, "audio/wav")},
        headers=HX,
    )

    assert resp.status_code == 200, resp.text
    (job,) = _jobs(conn)
    assert not _carries_it(_params(job))


def test_the_dialog_path_door_ignores_it(client, conn, src):
    resp = client.post("/transcribe/path", data={"path": str(src), **SMUGGLED}, headers=HX)

    assert resp.status_code == 200, resp.text
    (job,) = _jobs(conn)
    assert not _carries_it(_params(job))


def test_the_url_door_ignores_it_for_a_typed_link(client, conn):
    resp = client.post(
        "/transcribe/url",
        data={"url": "https://example.test/watch?v=abc123", **SMUGGLED},
        headers=HX,
    )

    assert resp.status_code == 200, resp.text
    (job,) = _jobs(conn, url_stage.JOB_TYPE)
    assert not _carries_it(_params(job))


def test_the_url_door_ignores_it_for_ticked_episodes(client, conn):
    entry = json.dumps({"url": EPISODE_URL, "title": "Lecture 1", "source_id": "Generic:g1"})
    resp = client.post(
        "/transcribe/url",
        data={"entry": entry, "feed_url": FEED_URL, "feed_title": "Lectures", **SMUGGLED},
        headers=HX,
    )

    assert resp.status_code == 200, resp.text
    (job,) = _jobs(conn, url_stage.JOB_TYPE)
    assert not _carries_it(_params(job))


def test_the_recorder_door_ignores_it(client, conn):
    session = client.post("/record/start").json()["session"]
    sent = client.post(
        f"/record/{session}/chunk", content=CHUNK.read_bytes(), headers={"Content-Type": WEBM}
    )
    assert sent.status_code == 200, sent.text

    resp = client.post(f"/record/{session}/finish", data=SMUGGLED, headers=HX)

    assert resp.status_code == 200, resp.text
    (job,) = _jobs(conn)
    assert not _carries_it(_params(job))


# --- retry: a transcribe job replays only what a transcribe job takes ---------------


def test_retry_of_a_transcribe_job_drops_a_key_it_does_not_take(client, conn):
    """What a job stored before TASK-034 looks like: legitimate keys, the one
    that opened pyannote, and a key of another job type for good measure. The
    retry keeps the request and drops the rest."""
    kept = {
        "model": "tiny",
        "language": "nl",
        "diarize": True,
        transcribe.EXTRA_HOTWORDS_KEY: ["Zaphod", "Trillian"],
    }
    old = _stored_job(
        conn, "transcribe", {**kept, "diarization_model": FOREIGN, url_stage.FEED_KEY: 7}
    )

    new = _retry(client, old)

    assert _params(_row(conn, new)) == kept
    assert _row(conn, new)["retry_of"] == old


def test_retry_of_a_clean_transcribe_job_is_byte_for_byte_the_same_request(client, conn):
    """The sieve reshapes nothing it keeps: same keys, same order, same JSON."""
    old = _stored_job(
        conn,
        "transcribe",
        {"model": "tiny", "task": "transcribe", "language": None, "diarize": False,
         transcribe.EXTRA_HOTWORDS_KEY: ["Zaphod"], "device": "cpu", "compute_type": "int8"},
    )

    new = _retry(client, old)

    assert _row(conn, new)["params_json"] == _row(conn, old)["params_json"]


# The chat model that reached the transcribe stage on 2026-09-03.
CHAT_MODEL = "openai/gpt-5.6-luna"


@pytest.mark.parametrize(
    ("type_", "stored", "replayed"),
    [
        ("transcribe", {"model": CHAT_MODEL, "language": "nl"}, {"language": "nl"}),
        (
            url_stage.JOB_TYPE,
            {"url": EPISODE_URL, url_stage.OPTIONS_KEY: {"model": CHAT_MODEL, "language": "nl"}},
            {"url": EPISODE_URL, url_stage.OPTIONS_KEY: {"language": "nl"}},
        ),
    ],
    ids=["transcribe", "ingest_url-options"],
)
def test_retry_does_not_repeat_a_model_that_is_not_a_speech_model(
    client, conn, type_, stored, replayed
):
    """The 2026-09-03 row, retried instead of re-transcribed: a transcribe
    request carrying a chat model's name. `model` is a key a transcribe job
    takes, so the key sieve keeps it - the value is what is wrong - and the
    stage refuses it (`transcribe.ensure_speech_model`), so a retry that
    replays it fails the same way every time. Dropped, the job takes the
    default tier: what `library._last_params` already does for a re-transcribe
    of the same row. An ingest_url job's options are that request one hop
    early, so they get the same answer."""
    old = _stored_job(conn, type_, stored)

    new = _retry(client, old)

    assert _params(_row(conn, new)) == replayed


def test_retry_of_an_ingest_url_job_keeps_every_key_of_its_own(client, conn):
    """The sieve is for transcribe jobs. An ingest_url job's params are a
    different vocabulary - a feed poll's `feed_id` is what stops a known
    download being transcribed twice - and a retry must not strip them."""
    poll_params = {
        "url": EPISODE_URL,
        "folder_id": None,
        url_stage.OPTIONS_KEY: TranscribeOptions(language="en").to_params(),
        "from_playlist": True,
        url_stage.ENTRY_KEY: {"title": "Lecture 1", "source_id": "Generic:g1"},
        url_stage.SOURCE_KEY: {"url": FEED_URL, "title": "Lectures"},
        url_stage.FEED_KEY: 7,
    }
    old = _stored_job(conn, url_stage.JOB_TYPE, poll_params)

    new = _retry(client, old)

    assert _row(conn, new)["params_json"] == _row(conn, old)["params_json"]


def test_retry_of_an_ingest_url_job_sieves_the_options_its_transcribe_job_is_built_from(
    client, conn
):
    """One key of an ingest_url job is not its own vocabulary: the nested
    `options`, which `url_stage.transcribe_params` copies into the transcribe
    job the download queues. A row stored before TASK-034 is never
    re-validated, so a foreign key there would ride the retry into that
    transcribe job one hop later. The retry holds the options to PARAM_KEYS,
    and every key of the ingest_url job itself stays as stored."""
    clean = TranscribeOptions(language="en").to_params()
    stored = {
        "url": EPISODE_URL,
        "folder_id": None,
        url_stage.OPTIONS_KEY: {**clean, "diarization_model": FOREIGN, url_stage.FEED_KEY: 7},
        "from_playlist": True,
        url_stage.ENTRY_KEY: {"title": "Lecture 1", "source_id": "Generic:g1"},
        url_stage.SOURCE_KEY: {"url": FEED_URL, "title": "Lectures"},
        url_stage.FEED_KEY: 7,
    }
    old = _stored_job(conn, url_stage.JOB_TYPE, stored)

    new = _params(_row(conn, _retry(client, old)))

    assert new == {**stored, url_stage.OPTIONS_KEY: clean}
    # And at the consequence: the params the transcribe job will be built from.
    built = url_stage.transcribe_params(new, ["Zaphod"])
    assert set(built) <= options.PARAM_KEYS, sorted(set(built) - options.PARAM_KEYS)


# --- the stage: diarize opens its own pipeline, whatever the job says ---------------


def _a_job_for_the_diarize_stage(conn, params):
    media_id = seed_media(conn)
    job_id = _stored_job(conn, "transcribe", params, status="running", media_id=media_id)
    with db.LOCK:
        run_id = conn.execute(
            "INSERT INTO run(media_id, model, compute_type, created_at) VALUES (?, 'tiny', 'int8', 0)",
            (media_id,),
        ).lastrowid
        conn.commit()
    job = dict(_row(conn, job_id))
    ctx = runner.RunnerContext(
        conn=conn,
        job=job,
        params=json.loads(job["params_json"]),
        report=lambda p: None,
        cancelled=lambda: False,
        media_path=None,
    )
    ctx.state.update(wav=Path("audio.wav"), run_id=run_id)
    return ctx


def _opened(monkeypatch) -> list:
    """Every source the loader hands pyannote, recorded; each one "loads"."""
    tried: list = []

    def open_pipeline(source, *, device, token):
        tried.append(source)
        return FakePipeline()

    monkeypatch.setattr(diarize, "open_pipeline", open_pipeline)
    monkeypatch.setattr(diarize, "load_waveform", lambda wav: (FakeWaveform(), 16000))
    return tried


@pytest.mark.parametrize("local_copy", [False, True], ids=["hub-default", "models-dir"])
def test_the_diarize_stage_opens_its_default_pipeline_whatever_the_params_say(
    conn, data_dir, monkeypatch, local_copy
):
    """A transcribe job queued before the upgrade is never re-validated; it
    just runs. So the stage itself must not take a pipeline from the job: the
    order is MODELS_DIR/pyannote first, the shipped default second, as it is
    for every job that never named one."""
    local = diarize.local_weights_dir()
    if local_copy:
        local.mkdir(parents=True)
    tried = _opened(monkeypatch)
    ctx = _a_job_for_the_diarize_stage(conn, {"device": "cpu", "diarization_model": FOREIGN})

    diarize.run(ctx)

    expected = local if local_copy else diarize.DEFAULT_PIPELINE
    assert tried == [expected]
    (event,) = [e for e in jobs.events_after(conn, ctx.job["id"], 0) if e["kind"] == "diarize"]
    assert event["payload"]["pipeline"] == str(expected)


def _job_param_reads(module) -> set[str]:
    """The keys a module reads off a job's params - `.get(k)` or `[k]`.

    The owner is `ctx.params`, or a bare `params`: a stage hands the dict to a
    helper under that name (`transcribe._extra_hotwords`, `perf_model_for`).
    The key is a literal, or a name the module resolves to a string - a module
    constant (`EXTRA_HOTWORDS_KEY`) or one reached through an imported module
    (`transcribe.EXTRA_HOTWORDS_KEY`). A literal-only scan of `ctx.params`
    missed `extra_hotwords` on both counts. A key computed at run time - a
    loop variable, a parameter - is still invisible; no stage reads one so.

    A subscript counts only when it loads. Once a bare `params` counts,
    `diarize._note_run` writing `params["diarization_pipeline"]` and
    `params["diarization_note"]` - the run's params_json, not the job's -
    would otherwise read as two job keys the door refuses.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            owner, key = node.func.value, (node.args[0] if node.args else None)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            owner, key = node.value, node.slice
        else:
            continue
        is_job_params = (isinstance(owner, ast.Name) and owner.id == "params") or (
            isinstance(owner, ast.Attribute)
            and owner.attr == "params"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "ctx"
        )
        value = _key_value(module, key)
        if is_job_params and isinstance(value, str):
            found.add(value)
    return found


def _key_value(module, node):
    """What a key expression stands for: a literal's value, or what a name or a
    dotted name resolves to in the module's globals; None for anything else."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return getattr(module, node.id, None)
    if isinstance(node, ast.Attribute):
        owner = _key_value(module, node.value)
        return None if owner is None else getattr(owner, node.attr, None)
    return None


def test_every_key_the_transcribe_stages_read_is_one_the_door_admits():
    """The sieve and the stages have to agree, and they did not: the stages
    read `device` and `compute_type` (the CPU end-to-end run depends on it)
    and the sieve did not admit them, while the one key the sieve most needed
    to stop was read by a stage. Asked of the registry, so a stage added to
    the transcribe pipeline is asked too.

    `extra_hotwords` is in the must-find set because it is the key that fell
    out of the sieve once already (see PARAM_KEYS' docstring), and because it
    is read the way a literal-only scan cannot see: through a module constant
    (`transcribe.EXTRA_HOTWORDS_KEY`) off a bare `params` (`_extra_hotwords`)."""
    modules = {sys.modules[fn.__module__] for _name, fn in runner.STAGES["transcribe"]}
    read = set().union(*(_job_param_reads(module) for module in modules))

    must_find = {"model", "language", "diarize", transcribe.EXTRA_HOTWORDS_KEY}
    assert must_find <= read, f"the scan is not finding the reads: {sorted(must_find - read)}"
    assert "diarization_model" not in read
    assert read <= options.PARAM_KEYS, f"read but refused at the door: {sorted(read - options.PARAM_KEYS)}"


# --- the legitimate producers still produce -----------------------------------------


def test_the_dialog_fields_still_go_through_api_media(client, conn, src):
    resp = client.post(
        "/api/media",
        json={"path": str(src), "tier": "max", "language": "nl", "num_speakers": 3, "translate": True},
    )

    assert resp.status_code == 201, resp.text
    (job,) = _jobs(conn)
    expected = TranscribeOptions(tier="max", language="nl", num_speakers=3, translate=True).to_params()
    assert _params(job) == expected
    assert set(expected) <= options.PARAM_KEYS


def test_a_params_object_of_stage_keys_is_stored_as_given(client, conn, src):
    """The raw-object door stays open for a caller who knows the stage keys -
    the end-to-end suite's CPU run is one (`tests/test_pipeline_e2e.py`
    CPU_PARAMS: tiny, on cpu, int8)."""
    given = {
        "model": "tiny",
        "task": "transcribe",
        "language": "nl",
        "diarize": True,
        "num_speakers": 2,
        "min_speakers": 1,
        "max_speakers": 3,
        transcribe.EXTRA_HOTWORDS_KEY: ["Zaphod"],
        "device": "cpu",
        "compute_type": "int8",
    }

    resp = client.post("/api/media", json={"path": str(src), "params": given})

    assert resp.status_code == 201, resp.text
    (job,) = _jobs(conn)
    assert _params(job) == given


def test_a_feed_episode_still_reaches_transcription_with_its_hotwords_and_survives_a_retry(
    client, conn, data_dir, monkeypatch
):
    """The long way round, because it is the producer that writes the most:
    a feed poll queues an ingest_url job carrying `feed_id`, the download
    registers, and the transcribe job it queues carries the dialog's options
    plus the names the listing knew (`extra_hotwords`) - and none of the
    ingest job's own vocabulary. Retrying that transcribe job asks the same."""
    feed_id = feeds.subscribe(conn, FEED_URL, title="The Hitchhiker Lectures")
    listing = urls.UrlInfo(
        kind="playlist",
        title="The Hitchhiker Lectures",
        duration=None,
        uploader="",
        webpage_url=FEED_URL,
        entries=[{"url": EPISODE_URL, "title": "Lecture 1", "source_id": "Generic:guid-1"}],
    )
    feeds.poll(
        conn,
        dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone()),
        probe=lambda url, **kwargs: listing,
        known_sources=lambda c, entries: [None] * len(entries),
        options=TranscribeOptions(language="en").to_params(),
        now=1000.0,
    )
    (ingest,) = _jobs(conn, url_stage.JOB_TYPE)
    assert _params(ingest)[url_stage.FEED_KEY] == feed_id

    fake = FakeYdl(
        single_info(title="default.mp3_ywr3", uploader="", extractor_key="Generic", id="guid-1"),
        files=("download.mp3", "download.info.json"),
    )
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    ctx = runner.RunnerContext(
        conn=conn,
        job=dict(ingest),
        params=_params(ingest),
        report=lambda p: None,
        cancelled=lambda: False,
        media_path=None,
    )
    for _name, stage in url_stage.STAGES:
        stage(ctx)

    (queued,) = _jobs(conn, "transcribe")
    params = _params(queued)
    assert set(params) <= options.PARAM_KEYS
    assert url_stage.FEED_KEY not in params
    assert params["language"] == "en"
    assert params[transcribe.EXTRA_HOTWORDS_KEY] == ["Lecture", "The", "Hitchhiker", "Lectures"]

    with db.LOCK:
        conn.execute("UPDATE job SET status='failed' WHERE id=?", (queued["id"],))
        conn.commit()
    assert _params(_row(conn, _retry(client, queued["id"]))) == params


def test_bulk_retranscribe_still_copies_the_last_request_and_nothing_foreign(client, conn):
    media_id = seed_media(conn)
    _stored_job(
        conn,
        "transcribe",
        {"language": "nl", transcribe.EXTRA_HOTWORDS_KEY: ["Zaphod"], "diarization_model": FOREIGN},
        status="done",
        media_id=media_id,
    )

    resp = client.post("/media/bulk", data={"ids": [media_id], "action": "retranscribe"}, headers=HX)

    assert resp.status_code == 200, resp.text
    (queued,) = conn.execute("SELECT * FROM job WHERE status='queued'").fetchall()
    assert _params(queued) == {"language": "nl", transcribe.EXTRA_HOTWORDS_KEY: ["Zaphod"]}


def test_bulk_retranscribe_now_carries_the_device_the_last_request_chose(client, conn):
    """A deliberate change, and the price of admitting `device` and
    `compute_type` at the door: `library._last_params` keeps what PARAM_KEYS
    admits, so a re-transcribe of a job that asked for the CPU asks for it
    again. Before TASK-034 both keys were dropped here, silently."""
    media_id = seed_media(conn)
    last = {"model": "tiny", "device": "cpu", "compute_type": "int8"}
    _stored_job(conn, "transcribe", last, status="done", media_id=media_id)

    client.post("/media/bulk", data={"ids": [media_id], "action": "retranscribe"}, headers=HX)

    (queued,) = conn.execute("SELECT * FROM job WHERE status='queued'").fetchall()
    assert _params(queued) == last
