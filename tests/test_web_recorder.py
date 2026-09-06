"""The recorder's four routes: start, chunk, finish, cancel (Phase 6 Task 3).

The browser half cannot run here, so these tests are the server half held to
the contract `recorder.js` was written against: a session token comes back
from one POST, chunks are posted at it as raw bodies while the recording runs,
and one final POST turns the pile into a library row and a queued job.

Real bytes throughout - `tests/fixtures/chunk.webm`, the same one-second Opus
fragment `test_ingest_recording` uses. A route tested with `b"xxx"` would pass
and then fail in the field on the first real recording, because the thing that
actually has to work is the remux at the end of it.

The one design decision worth stating: **a chunk that has arrived is never
thrown away by a mistake made after it**. Options are validated before
`finalize` runs, and a finish that fails leaves the session on disk, so the
user can fix the form and press stop again rather than lose the recording.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe import db, fsbrowse, paths
from scribe.app import create_app
from scribe.ingest import recording
from scribe.stages import transcribe
from test_ingest_recording import RECORDER_FIXTURE
from test_web_url_dialog import needs_node, run_dom
from scribe.web import ingest_ui

CHUNK = Path(__file__).resolve().parent / "fixtures" / "chunk.webm"
WEBM = "audio/webm;codecs=opus"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
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
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


HX = {"HX-Request": "true"}


def chunk_bytes() -> bytes:
    return CHUNK.read_bytes()


def start(client) -> str:
    resp = client.post("/record/start")
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


def send_chunk(client, session: str, data: bytes | None = None):
    return client.post(
        f"/record/{session}/chunk",
        content=chunk_bytes() if data is None else data,
        headers={"Content-Type": WEBM},
    )


def finish(client, session: str, **fields):
    return client.post(f"/record/{session}/finish", data=fields, headers=HX)


def upload(client, name: str, **fields):
    return client.post(
        "/transcribe/upload",
        data=fields,
        files={"files": (name, chunk_bytes(), WEBM)},
        headers=HX,
    )


def jobs_of(conn) -> list:
    return conn.execute("SELECT * FROM job ORDER BY id").fetchall()


def media_of(conn) -> list:
    return conn.execute("SELECT * FROM media ORDER BY id").fetchall()


def setting(conn, key):
    row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


# --- the door in the dialog -----------------------------------------------------------


def test_the_record_dialog_offers_the_recorder_and_the_transcribe_dialog_does_not(client):
    """Recording has a dialog of its own. It was the fourth tab of Transcribe,
    and a person who clicked a microphone found themselves looking at "Upload
    a file" and "From a link" with no idea why."""
    body = client.get("/record", headers=HX).text
    assert "data-recorder" not in client.get("/transcribe", headers=HX).text

    assert "data-recorder" in body
    assert "data-record-start" in body
    assert "data-record-finish" in body
    assert "data-record-cancel" in body
    # The level meter and the elapsed clock the script fills in.
    assert "data-record-level" in body
    assert "data-record-elapsed" in body


def test_the_recorders_name_field_belongs_to_the_recorder_alone(client, conn):
    """The recorder once shared a form with the upload and path doors, and a
    field named `title` there would have been posted by their buttons too. The
    doors are apart now; the name stays because the finish route reads it by
    that name, and this test stays because the invariant it guards - an upload
    never takes a title typed for a recording - is still worth a sentence.
    """
    body = client.get("/record", headers=HX).text
    assert 'name="record_title"' in body
    assert 'name="title"' not in body

    upload(client, "meeting.webm", record_title="Voice note", title="Voice note")

    (row,) = media_of(conn)
    assert row["title"] == "meeting"  # the filename's stem, as it always was


def test_the_recorder_script_is_loaded_by_the_page_not_by_the_fragment(client):
    """htmx runs with allowScriptTags off, so a <script> inside the swapped-in
    dialog fragment would be stripped and the recorder would silently do
    nothing. It has to come from the page skeleton."""
    page = client.get("/").text
    fragment = client.get("/record", headers=HX).text

    assert '/static/recorder.js' in page
    assert "<script" not in fragment
    assert client.get("/static/recorder.js").status_code == 200


# --- starting and chunking ---------------------------------------------------------


def test_start_hands_back_a_session_and_makes_its_directory(client, conn):
    session = start(client)

    assert recording.session_dir(session).is_dir()
    (row,) = conn.execute("SELECT * FROM recording").fetchall()
    assert row["session"] == session


def test_chunks_are_stored_in_the_order_they_arrive(client, conn):
    session = start(client)

    indexes = [send_chunk(client, session).json()["index"] for _ in range(3)]

    assert indexes == [0, 1, 2]
    stored = recording.chunk_paths(session)
    assert len(stored) == 3
    assert all(p.read_bytes() == chunk_bytes() for p in stored)


def test_a_chunk_larger_than_the_cap_is_refused_and_stores_nothing(
    client, conn, monkeypatch
):
    """Every other ingest door streams; this one held the whole body in memory.

    A five-second Opus chunk is tens of kilobytes, so the cap is three orders
    of magnitude of headroom and still the difference between one POST
    deciding how much memory this process uses and one POST that cannot.
    """
    monkeypatch.setattr(ingest_ui, "MAX_CHUNK_BYTES", 1024)
    session = start(client)

    resp = send_chunk(client, session, b"x" * 4096)

    assert resp.status_code == 413
    assert "1024" in resp.json()["detail"]  # the cap, in the answer
    assert recording.chunk_paths(session) == []
    # And a chunk of a size a recorder actually sends still lands.
    monkeypatch.setattr(ingest_ui, "MAX_CHUNK_BYTES", 32 * 1024 * 1024)
    assert send_chunk(client, session).status_code == 200
    assert len(recording.chunk_paths(session)) == 1


def test_a_chunk_for_a_session_nobody_started_is_a_404(client, conn):
    resp = send_chunk(client, "neverstartedatall")

    assert resp.status_code == 404
    assert media_of(conn) == []


@pytest.mark.parametrize("hostile", ["nope", "evil..evil", "a.b", "x" * 200])
def test_a_session_that_is_not_a_token_is_refused_by_every_route(client, hostile):
    """Same answer for a bad shape as for a session that does not exist: 404
    tells a caller nothing about what else is on this disk."""
    assert send_chunk(client, hostile).status_code == 404
    assert finish(client, hostile, record_title="t").status_code == 404
    assert client.post(f"/record/{hostile}/cancel").status_code == 404


# --- finishing ----------------------------------------------------------------------


def test_finishing_ingests_the_recording_and_queues_a_transcribe_job(client, conn):
    session = start(client)
    send_chunk(client, session)
    send_chunk(client, session)

    resp = finish(client, session, record_title="Standup", language="nl", tier="max", diarize="1")

    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "jobs-changed"
    assert "<html" not in resp.text and "<table" in resp.text  # the library rows

    (row,) = media_of(conn)
    assert row["title"] == "Standup"
    (job,) = jobs_of(conn)
    assert job["type"] == "transcribe"
    assert job["media_id"] == row["id"]
    assert json.loads(job["params_json"]) == {
        "model": transcribe.TRANSLATE_MODEL,
        "task": "transcribe",
        "language": "nl",
        "diarize": True,
    }
    # The bytes are in the store and the scratch directory is gone.
    assert (paths.DATA_DIR / row["store_path"]).is_file()
    assert not recording.session_dir(session).exists()


def test_a_finished_recording_goes_into_the_chosen_folder(client, conn):
    with db.LOCK:
        folder = conn.execute(
            "INSERT INTO folder(name) VALUES ('Notes') RETURNING id"
        ).fetchone()["id"]
        conn.commit()
    session = start(client)
    send_chunk(client, session)

    finish(client, session, record_title="A note", folder_id=str(folder))

    (row,) = media_of(conn)
    assert row["folder_id"] == folder


def test_finishing_remembers_the_options_as_the_next_dialogs_defaults(client, conn):
    session = start(client)
    send_chunk(client, session)

    finish(client, session, record_title="x", language="en", tier="max", diarize="0")

    assert setting(conn, "default_language") == "en"
    assert setting(conn, "default_tier") == "max"
    assert setting(conn, "default_diarize") == "0"


def test_finishing_a_session_nobody_started_is_a_404(client, conn):
    resp = finish(client, "neverstartedatall", record_title="t")

    assert resp.status_code == 404
    assert media_of(conn) == [] and jobs_of(conn) == []


def test_finishing_before_the_first_chunk_says_so_and_keeps_the_session(client, conn):
    session = start(client)

    resp = finish(client, session, record_title="t")

    assert resp.status_code == 400
    assert "nothing" in resp.json()["detail"].lower()
    assert media_of(conn) == [] and jobs_of(conn) == []
    assert recording.session_dir(session).is_dir()


def test_bad_options_do_not_cost_the_user_their_recording(client, conn):
    """The options are checked before the chunks are touched. A typo in the
    form must not be the thing that eats a two-hour session."""
    session = start(client)
    send_chunk(client, session)

    resp = finish(client, session, record_title="t", tier="cheetah")

    assert resp.status_code == 400
    assert "tier" in resp.json()["detail"]
    assert media_of(conn) == [] and jobs_of(conn) == []
    assert len(recording.chunk_paths(session)) == 1


def test_a_recording_that_is_not_media_fails_without_taking_the_chunks(client, conn):
    session = start(client)
    send_chunk(client, session, b"not a webm file at all")

    resp = finish(client, session, record_title="t")

    assert resp.status_code == 400
    assert media_of(conn) == [] and jobs_of(conn) == []
    assert len(recording.chunk_paths(session)) == 1


# --- cancelling ----------------------------------------------------------------------


def test_cancel_removes_the_directory_and_queues_nothing(client, conn):
    session = start(client)
    send_chunk(client, session)

    resp = client.post(f"/record/{session}/cancel")

    assert resp.status_code == 200
    assert not recording.session_dir(session).exists()
    assert media_of(conn) == [] and jobs_of(conn) == []
    (row,) = conn.execute("SELECT * FROM recording").fetchall()
    assert row["finished_at"] is not None and row["media_id"] is None


def test_cancelling_a_session_nobody_started_is_a_404(client):
    assert client.post("/record/neverstartedatall/cancel").status_code == 404


# --- the scope and the microphone picker -----------------------------------------------


def test_the_record_dialog_shows_the_input_as_a_waveform_and_names_the_device(client):
    """A recording made on 2026-09-05 was eight seconds of -91 dB and the
    pipeline transcribed the silence; the level meter had sat at zero the
    whole time and nobody looked. The scope is a picture of the samples the
    MediaRecorder is encoding, and the device line names where they came from,
    so a wrong or muted input is something a person reads. recorder.js paints
    both; the markup only has to be there for it to find."""
    body = client.get("/record", headers=HX).text

    assert "<canvas" in body and "data-record-scope" in body
    assert 'aria-label="Live audio input"' in body
    # Which microphone: a select to change it and a line naming the one in use.
    assert "data-record-input" in body
    assert '<option value="">System default</option>' in body
    assert "data-record-device" in body
    # The meter stays as the one-glance level.
    assert "data-record-level" in body


def test_the_scope_is_painted_by_the_page_script_and_reads_the_theme_tokens(client):
    """No <script> in the fragment (htmx strips them); the scope's colours come
    from the same custom properties the stylesheet uses, resolved through the
    browser rather than read raw - `light-dark()` is not a colour a canvas can
    parse, and the first attempt crashed on exactly that."""
    js = client.get("/static/recorder.js").text

    assert "part('scope')" in js
    assert "getFloatTimeDomainData" in js
    assert "data-record-scope-state" in js
    for token in ("--accent", "--accent-2", "--warn", "--fg-muted"):
        assert re.search(r"tone\(canvas, '" + re.escape(token) + r"'", js), token
    assert "getComputedStyle(probe).color" in js
    # CR-003: the scope's verdict also goes to a live region, on change only.
    assert "part('scope-say')" in js
    assert re.search(r"if \(state === saidState\) \{ return; \}", js)


@needs_node
def test_a_start_that_fails_after_the_session_opened_is_torn_down(tmp_path):
    """`live` is assigned before recorder.start(); a start that throws used to
    leave a session that looked alive - timer counting, data-busy set, and a
    Record button that `begin` refused because `live` was set. Found by
    switching microphones in a harness that handed back a stopped stream.

    Behavioural, in the node harness: the fake recorder's start() throws, and
    what is asserted is what a person and the server would see. The first
    version of this test grepped a 900-character window of the source for
    "release()" and was shown to pass with the call deleted and a comment
    added (review #3, CR-009)."""
    result = run_dom(
        tmp_path,
        RECORDER_FIXTURE
        + r"""
    load(RECORDER);
    FakeRecorder.prototype.start = function () { throw new Error('codec refused'); };
    press(block.querySelector('[data-record-start]'));
    await settle();
    await settle();
    await settle();
    done({
      busy: block.getAttribute('data-busy'),
      start: block.querySelector('[data-record-start]').hidden,
      finish: block.querySelector('[data-record-finish]').hidden,
      status: block.querySelector('[data-record-status]').textContent,
      posted: posted.map(function (p) { return p.url; }),
      tracksStopped: tracks.map(function (t) { return t.stopped; })
    });
""",
    )

    assert result["busy"] is None, "the dialog stayed claimed by a recording that never started"
    assert result["start"] is False and result["finish"] is True, "the controls were not idle"
    assert "Could not start recording" in result["status"] and "codec refused" in result["status"]
    assert any(url.endswith("/cancel") for url in result["posted"]), "the server was never told"
    assert result["tracksStopped"] == [1], "the microphone was left open"


def test_the_scope_has_a_spoken_twin_for_its_silence_verdict(client):
    """CR-003. The amber sentence is pixels; a screen reader hears this one."""
    body = client.get("/record", headers=HX).text

    assert 'data-record-scope-say role="status" aria-live="polite"' in body
    assert 'class="visually-hidden" data-record-scope-say' in body
