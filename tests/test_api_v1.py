"""TASK-108 phase 0, ADR-025 option B: the versioned JSON surface under /api/v1.

What an outside program needs, without reading source: list and search the
library, read a transcript, send a recording in (with an idempotency key, so a
retry queues no second GPU job), queue a transcription or an AI question, poll
a job, export, and read the settings - all from a published schema, every
error JSON with a stable ``code``.

No token (Robert, 2026-10-24): the routes sit behind what the pages sit
behind - the 127.0.0.1 bind and `scribe.guard`. So this file also pins that the
guard covers them (a foreign Host, a foreign Origin), that the phone door
cannot reach any of them, and that the coded error shape stays on /api/v1 and
does not leak into the pages' own ``{"detail": ...}`` answers.

Each route calls the helper its page calls (ADR-025's first Must); the twin
tests below compare the two doors' answers rather than restating them.
"""

from __future__ import annotations

import io
import json
import os
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe import db, doctor, fsbrowse, paths
from scribe.app import create_app
from scribe.llm import ollama
from seed import seed_media, seed_run
from test_web_phone import _app_paths, door_of, http, install_door
from test_phone_door import FakeClock, FakePublisher

V1 = "/api/v1"


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
def app(db_path, data_dir):
    return create_app(db_path=db_path, start_supervisor=False)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def transcribed(conn):
    media_id = seed_media(conn, title="Guide", duration=20.0)
    seed_run(conn, media_id)
    return media_id


def tiny_wav(seconds: float = 0.25, rate: int = 8000, tone: int = 0) -> bytes:
    """A real, very small WAV; ``tone`` changes the bytes and so the hash."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = int(seconds * rate)
        out.writeframes(bytes([tone % 256, 0]) * frames)
    return buffer.getvalue()


def upload(client, data: bytes, *, key: str | None = None, name: str = "clip.wav", **fields):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        f"{V1}/media", files={"file": (name, data, "audio/wav")}, data=fields, headers=headers
    )


def jobs_of(conn):
    with db.LOCK:
        return conn.execute("SELECT * FROM job ORDER BY id").fetchall()


def v1_paths(app) -> list[str]:
    return [path for path in _app_paths(app) if path.startswith(V1 + "/")]


def assert_coded(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["code"] == code, body
    assert isinstance(body["detail"], str) and body["detail"], body
    return body


# --- the schema --------------------------------------------------------------------------


EXPECTED_OPERATIONS = {
    ("get", "/api/v1/media"),
    ("post", "/api/v1/media"),
    ("get", "/api/v1/media/{media_id}"),
    ("get", "/api/v1/media/{media_id}/transcript"),
    ("post", "/api/v1/media/{media_id}/transcribe"),
    ("post", "/api/v1/media/{media_id}/ai/{kind}"),
    ("post", "/api/v1/media/{media_id}/export"),
    ("get", "/api/v1/search"),
    ("get", "/api/v1/jobs"),
    ("get", "/api/v1/jobs/{job_id}"),
    ("post", "/api/v1/jobs/{job_id}/cancel"),
    ("get", "/api/v1/settings"),
    ("get", "/api/v1/doctor"),
    ("get", "/api/v1/watch-folders"),
    ("post", "/api/v1/watch-folders"),
    ("delete", "/api/v1/watch-folders/{folder_id}"),
}


def test_every_v1_operation_is_in_the_published_schema(client):
    schema = client.get("/openapi.json").json()
    found = {
        (method, path)
        for path, item in schema["paths"].items()
        if path.startswith(V1 + "/")
        for method in item
    }
    assert found == EXPECTED_OPERATIONS


def test_every_v1_operation_documents_the_coded_error(client):
    schema = client.get("/openapi.json").json()
    assert set(schema["components"]["schemas"]["ApiError"]["required"]) == {"code", "detail"}
    for path, item in schema["paths"].items():
        if not path.startswith(V1 + "/"):
            continue
        for method, operation in item.items():
            refs = json.dumps(operation.get("responses", {}))
            assert "#/components/schemas/ApiError" in refs, (method, path)


def test_the_upload_documents_its_idempotency_key_and_both_bodies(client):
    operation = client.get("/openapi.json").json()["paths"][f"{V1}/media"]["post"]
    (key,) = [p for p in operation.get("parameters", []) if p["in"] == "header"]
    assert key["name"] == "Idempotency-Key"
    for promise in ("idempotency_key_reused", "idempotency_key_in_progress", "job is gone"):
        assert promise in key["description"], promise
    content = operation["requestBody"]["content"]
    assert set(content) == {"multipart/form-data", "application/json"}


def test_a_v1_route_needs_no_token(client, transcribed):
    resp = client.get(f"{V1}/media")
    assert resp.status_code == 200
    assert "authorization" not in {k.lower() for k in resp.request.headers}


# --- coded errors, and only here ------------------------------------------------------------


def test_an_unknown_media_is_a_coded_404(client):
    assert_coded(client.get(f"{V1}/media/999"), 404, "not_found")


def test_an_unknown_v1_path_is_a_coded_404(client):
    assert_coded(client.get(f"{V1}/nothing-here"), 404, "not_found")


def test_a_wrong_method_is_a_coded_405(client):
    assert_coded(client.delete(f"{V1}/jobs"), 405, "method_not_allowed")


def test_a_malformed_parameter_is_a_coded_422(client):
    body = assert_coded(client.get(f"{V1}/jobs/not-a-number"), 422, "invalid_request")
    assert "job_id" in body["detail"]


def test_the_pages_errors_keep_their_old_shape(client):
    """app.js reads ``detail``; the pages' answers must not gain or lose a key."""
    assert client.get("/api/jobs/999").json() == {"detail": "no job with id 999"}
    assert "code" not in client.get("/api/jobs/not-a-number").json()


def test_an_unexpected_error_under_v1_is_a_coded_500_that_hides_the_exception(app, monkeypatch):
    def broken(conn, state):
        raise RuntimeError("synthetic secret detail")

    monkeypatch.setattr("scribe.web.library.media_rows", broken)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        resp = c.get(f"{V1}/media")
        body = assert_coded(resp, 500, "internal_error")
        assert "synthetic secret detail" not in resp.text
        assert set(body) == {"code", "detail"}


def test_an_unexpected_error_on_a_page_stays_the_plain_text_500(app, monkeypatch):
    def broken(conn, state):
        raise RuntimeError("synthetic")

    monkeypatch.setattr("scribe.web.library.media_rows", broken)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        resp = c.get("/")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.text == "Internal Server Error"


# --- the library ---------------------------------------------------------------------------------


def test_media_list_is_the_library_tables_rows(client, conn, transcribed):
    other = seed_media(conn, title="Other", duration=5.0)
    listed = client.get(f"{V1}/media").json()["media"]
    assert [row["id"] for row in listed] == [other, transcribed]
    assert {"title", "duration", "labels", "job_status", "folder_name"} <= set(listed[0])


def test_media_list_takes_the_librarys_filters(client, conn, transcribed):
    seed_media(conn, title="Other", duration=5.0)
    listed = client.get(f"{V1}/media", params={"q": "gui"}).json()["media"]
    assert [row["id"] for row in listed] == [transcribed]
    assert_coded(client.get(f"{V1}/media", params={"sort": "nonsense"}), 400, "bad_request")


def test_search_finds_words_and_marks_the_match(client, transcribed):
    hits = client.get(f"{V1}/search", params={"q": "towel"}).json()["hits"]
    assert hits and hits[0]["media_id"] == transcribed
    assert any(part["match"] for part in hits[0]["parts"])
    assert "towel" in "".join(part["text"] for part in hits[0]["parts"]).lower()


def test_one_media_says_whether_it_has_a_transcript(client, conn, transcribed):
    bare = seed_media(conn, title="Bare")
    assert client.get(f"{V1}/media/{transcribed}").json()["has_transcript"] is True
    assert client.get(f"{V1}/media/{bare}").json()["has_transcript"] is False


def test_the_transcript_is_the_json_export(client, transcribed):
    body = client.get(f"{V1}/media/{transcribed}/transcript").json()
    exported = client.post(f"/media/{transcribed}/export", data={"formats": "json"})
    assert body == json.loads(exported.content)
    assert body["schema_version"] == 1 and body["words"]


def test_a_media_without_a_transcript_is_a_coded_409(client, conn):
    bare = seed_media(conn, title="Bare")
    assert_coded(client.get(f"{V1}/media/{bare}/transcript"), 409, "no_transcript")


def test_export_returns_the_bytes_the_page_returns(client, transcribed):
    page = client.post(f"/media/{transcribed}/export", data={"formats": "srt"})
    api = client.post(f"{V1}/media/{transcribed}/export", json={"formats": ["srt"]})
    assert api.status_code == 200
    assert api.content == page.content
    assert api.headers["content-disposition"] == page.headers["content-disposition"]


def test_export_refuses_options_the_dialog_refuses(client, transcribed):
    body = assert_coded(
        client.post(f"{V1}/media/{transcribed}/export", json={"formats": ["pdf"]}), 400, "bad_request"
    )
    assert "pdf" in body["detail"]


# --- sending a recording in -----------------------------------------------------------------


def test_an_upload_files_the_recording_and_queues_its_job(client, conn):
    resp = upload(client, tiny_wav(), title="Synthetic")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Synthetic" and body["deduped"] is False
    (job,) = jobs_of(conn)
    assert job["id"] == body["job_id"] and job["type"] == "transcribe"
    assert job["media_id"] == body["id"]


def test_the_same_idempotency_key_twice_queues_one_job(client, conn):
    first = upload(client, tiny_wav(), key="retry-1")
    again = upload(client, tiny_wav(), key="retry-1")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert again.headers.get("idempotent-replayed") == "true"
    assert len(jobs_of(conn)) == 1


def test_without_a_key_a_second_upload_is_a_second_job(client, conn):
    """The old door's behaviour, unchanged: asking twice is asking twice."""
    upload(client, tiny_wav())
    again = upload(client, tiny_wav())
    assert again.json()["deduped"] is True
    assert len(jobs_of(conn)) == 2


def media_count(conn) -> int:
    with db.LOCK:
        return conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]


def test_a_key_reused_for_other_content_is_a_coded_409_and_files_nothing(client, conn, data_dir):
    upload(client, tiny_wav(tone=1), key="k")
    stored = sorted(p.name for p in (data_dir / "media").rglob("*") if p.is_file())
    assert_coded(upload(client, tiny_wav(tone=2), key="k"), 409, "idempotency_key_reused")
    assert len(jobs_of(conn)) == 1
    assert media_count(conn) == 1, "the refused recording stayed in the library"
    assert sorted(p.name for p in (data_dir / "media").rglob("*") if p.is_file()) == stored


def test_a_reused_key_never_removes_a_recording_that_was_already_there(client, conn):
    """Known content under a reused key is refused, and the old row stays."""
    upload(client, tiny_wav(tone=5))  # in the library, no key
    upload(client, tiny_wav(tone=6), key="k2")
    assert_coded(upload(client, tiny_wav(tone=5), key="k2"), 409, "idempotency_key_reused")
    assert media_count(conn) == 2


def count_inserts(monkeypatch) -> list:
    """Every media row an ingest creates passes `media._insert_media`."""
    from scribe import media

    calls: list = []
    real = media._insert_media

    def counting(*args, **kwargs):
        calls.append(kwargs.get("sha256"))
        return real(*args, **kwargs)

    monkeypatch.setattr(media, "_insert_media", counting)
    return calls


def test_a_refused_reuse_never_files_the_recording(client, conn, monkeypatch):
    """Not filed and taken back, which a concurrent upload can race: never filed."""
    upload(client, tiny_wav(tone=11), key="k-never")
    inserts = count_inserts(monkeypatch)
    assert_coded(upload(client, tiny_wav(tone=12), key="k-never"), 409, "idempotency_key_reused")
    assert inserts == []


def test_a_refused_reuse_of_a_local_path_never_files_it(client, conn, tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    for tone in (18, 19):
        (root / f"t{tone}.wav").write_bytes(tiny_wav(tone=tone))
    monkeypatch.setattr("scribe.fsbrowse.allowed_roots", lambda conn: (root,))
    headers = {"Idempotency-Key": "k-path"}
    assert client.post(f"{V1}/media", json={"path": str(root / "t18.wav")}, headers=headers).status_code == 201
    inserts = count_inserts(monkeypatch)
    resp = client.post(f"{V1}/media", json={"path": str(root / "t19.wav")}, headers=headers)
    assert_coded(resp, 409, "idempotency_key_reused")
    assert inserts == []

def test_a_replay_after_the_recording_was_deleted_files_it_again_with_a_job(client, conn):
    """The stored answer names a job that is gone: replaying it would hand
    the client a job id that 404s and leave the new filing without a job."""
    first = upload(client, tiny_wav(tone=13), key="k-gone").json()
    assert client.post(f"{V1}/jobs/{first['job_id']}/cancel").status_code == 200
    assert client.post(f"/media/{first['id']}/trash").status_code < 400
    assert client.post(f"/media/{first['id']}/purge").status_code < 400
    assert media_count(conn) == 0 and jobs_of(conn) == []

    again = upload(client, tiny_wav(tone=13), key="k-gone")
    assert again.status_code == 201, again.text
    assert "idempotent-replayed" not in again.headers
    (job,) = jobs_of(conn)
    assert media_count(conn) == 1
    assert job["id"] == again.json()["job_id"] and job["media_id"] == again.json()["id"]
    replayed = upload(client, tiny_wav(tone=13), key="k-gone")
    assert replayed.headers.get("idempotent-replayed") == "true"
    assert replayed.json() == again.json()


def test_a_replay_never_answers_with_somebody_elses_recording_that_reused_the_ids(client, conn):
    """SQLite hands a purged row's id out again: job 1 on media 1 may be
    another upload's by the time the client retries."""
    import hashlib

    first = upload(client, tiny_wav(tone=20), key="k-ids").json()
    client.post(f"{V1}/jobs/{first['job_id']}/cancel")
    client.post(f"/media/{first['id']}/trash")
    client.post(f"/media/{first['id']}/purge")
    other = upload(client, tiny_wav(tone=21)).json()
    assert (other["id"], other["job_id"]) == (first["id"], first["job_id"]), "ids not reused"

    again = upload(client, tiny_wav(tone=20), key="k-ids")
    assert again.status_code == 201, again.text
    assert "idempotent-replayed" not in again.headers
    assert again.json()["sha256"] == hashlib.sha256(tiny_wav(tone=20)).hexdigest()
    assert again.json()["job_id"] != other["job_id"]
    assert len(jobs_of(conn)) == 2

def _hold(conn, key: str, data: bytes, params: dict | None = None) -> None:
    """The key as a request that is still uploading leaves it."""
    import hashlib

    from scribe import idempotency

    route = "POST /api/v1/media"
    print_ = idempotency.fingerprint(route, sha256=hashlib.sha256(data).hexdigest(), params=params or {})
    with db.LOCK:
        assert idempotency.claim(conn, key, route, print_) is None


def test_the_same_key_while_its_first_request_is_still_running_is_a_coded_409(
    client, conn, monkeypatch
):
    _hold(conn, "k-busy", tiny_wav(tone=14))
    inserts = count_inserts(monkeypatch)
    assert_coded(upload(client, tiny_wav(tone=14), key="k-busy"), 409, "idempotency_key_in_progress")
    assert_coded(upload(client, tiny_wav(tone=15), key="k-busy"), 409, "idempotency_key_reused")
    assert inserts == [] and jobs_of(conn) == []


def test_an_upload_that_fails_after_taking_its_key_lets_it_go(app, conn, monkeypatch):
    from scribe import app as spine

    real = spine.enqueue_ingested

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        monkeypatch.setattr(spine, "enqueue_ingested", broken)
        assert upload(c, tiny_wav(tone=16), key="k-fail").status_code == 500
        monkeypatch.setattr(spine, "enqueue_ingested", real)
        again = upload(c, tiny_wav(tone=16), key="k-fail")
    assert again.status_code == 201, again.text
    assert len(jobs_of(conn)) == 1


def test_a_key_left_held_by_a_previous_life_is_let_go_at_start(db_path, data_dir):
    check = db.connect(db_path)
    try:
        _hold(check, "k-crash", tiny_wav(tone=17))
    finally:
        check.close()
    with TestClient(create_app(db_path=db_path, start_supervisor=False),
                    base_url="http://127.0.0.1") as c:
        resp = upload(c, tiny_wav(tone=17), key="k-crash")
    assert resp.status_code == 201, resp.text


def test_two_racing_requests_with_one_key_queue_one_job(app, conn):
    """`_once` looks up, queues and remembers under one lock hold."""
    import threading
    import time as time_

    from scribe import api_v1, jobs

    with TestClient(app, base_url="http://127.0.0.1"):
        live = app.state.conn
        media_id = seed_media(conn, title="Race")
        print_ = api_v1.idempotency.fingerprint("race", media_id=media_id)

        def work():
            time_.sleep(0.2)
            return {"job_id": jobs.enqueue(live, "transcribe", media_id=media_id, params={})}

        answers = []
        threads = [
            threading.Thread(target=lambda: answers.append(
                api_v1._once(live, "race-key", "race", print_, 201, work).body))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert len(jobs_of(conn)) == 1
    assert answers[0] == answers[1]


def test_a_key_survives_a_restart(db_path, data_dir):
    """A client that retries across an app restart is the case a key exists for."""
    for _ in range(2):
        with TestClient(create_app(db_path=db_path, start_supervisor=False),
                        base_url="http://127.0.0.1") as c:
            assert upload(c, tiny_wav(), key="across").status_code == 201
    check = db.connect(db_path)
    try:
        assert len(jobs_of(check)) == 1
    finally:
        check.close()


def test_an_upload_without_a_file_part_is_a_coded_400(client, conn):
    resp = client.post(f"{V1}/media", files={"other": ("x.wav", b"x")})
    assert_coded(resp, 400, "bad_request")
    assert jobs_of(conn) == []


def test_a_path_outside_the_roots_is_a_coded_403(client, tmp_path, monkeypatch):
    monkeypatch.setattr("scribe.fsbrowse.allowed_roots", lambda conn: (tmp_path / "allowed",))
    resp = client.post(f"{V1}/media", json={"path": str(tmp_path / "elsewhere.wav")})
    assert_coded(resp, 403, "forbidden")


def test_a_foreign_param_key_is_refused_as_the_old_door_refuses_it(client, conn):
    resp = upload(client, tiny_wav(), params=json.dumps({"diarization_model": "evil/pipe"}))
    body = assert_coded(resp, 400, "bad_request")
    assert "diarization_model" in body["detail"]
    assert jobs_of(conn) == []


# --- queueing work on what is there --------------------------------------------------------


def test_transcribe_again_reuses_the_last_request(client, conn, transcribed):
    with db.LOCK:
        conn.execute(
            "INSERT INTO job(type, media_id, params_json, status, created_at, queue_seq)"
            " VALUES ('transcribe', ?, ?, 'done', 0, 1)",
            (transcribed, json.dumps({"language": "nl"})),
        )
        conn.commit()
    resp = client.post(f"{V1}/media/{transcribed}/transcribe")
    assert resp.status_code == 201, resp.text
    job = jobs_of(conn)[-1]
    assert resp.json()["id"] == job["id"]
    assert json.loads(job["params_json"]) == {"language": "nl"}


def test_transcribe_takes_the_dialogs_options(client, conn, transcribed):
    resp = client.post(f"{V1}/media/{transcribed}/transcribe", json={"language": "en", "diarize": False})
    assert resp.status_code == 201, resp.text
    params = json.loads(jobs_of(conn)[-1]["params_json"])
    assert params["language"] == "en" and params["diarize"] is False


def test_transcribe_with_the_same_key_queues_once(client, conn, transcribed):
    for _ in range(2):
        resp = client.post(
            f"{V1}/media/{transcribed}/transcribe", json={"language": "en"},
            headers={"Idempotency-Key": "t-1"},
        )
        assert resp.status_code == 201
    assert len(jobs_of(conn)) == 1
    other = client.post(
        f"{V1}/media/{transcribed}/transcribe", json={"language": "nl"},
        headers={"Idempotency-Key": "t-1"},
    )
    assert_coded(other, 409, "idempotency_key_reused")


def test_transcribe_refuses_a_title(client, transcribed):
    assert_coded(
        client.post(f"{V1}/media/{transcribed}/transcribe", json={"title": "x"}), 400, "bad_request"
    )


def test_transcribe_refuses_a_field_it_does_not_take(client, conn, transcribed):
    """Refused, not dropped: ``{"foo": 1}`` once queued a job with the
    defaults, neither what was asked nor the last request."""
    body = assert_coded(
        client.post(f"{V1}/media/{transcribed}/transcribe", json={"foo": 1}), 400, "bad_request"
    )
    assert "foo" in body["detail"]
    assert jobs_of(conn) == []


def test_transcribe_with_an_empty_params_object_asks_for_the_defaults(client, conn, transcribed):
    """An explicit ``params`` is the request; only an empty body repeats the last one."""
    with db.LOCK:
        conn.execute(
            "INSERT INTO job(type, media_id, params_json, status, created_at, queue_seq)"
            " VALUES ('transcribe', ?, ?, 'done', 0, 1)",
            (transcribed, json.dumps({"language": "nl"})),
        )
        conn.commit()
    resp = client.post(f"{V1}/media/{transcribed}/transcribe", json={"params": {}})
    assert resp.status_code == 201, resp.text
    assert json.loads(jobs_of(conn)[-1]["params_json"]) == {}


def test_a_transcribe_replay_whose_job_is_gone_queues_a_new_one(client, conn, transcribed):
    headers = {"Idempotency-Key": "t-gone"}
    first = client.post(f"{V1}/media/{transcribed}/transcribe", json={"language": "en"}, headers=headers)
    with db.LOCK:
        conn.execute("DELETE FROM job WHERE id=?", (first.json()["id"],))
        conn.commit()
    again = client.post(f"{V1}/media/{transcribed}/transcribe", json={"language": "en"}, headers=headers)
    assert again.status_code == 201 and "idempotent-replayed" not in again.headers
    (job,) = jobs_of(conn)
    assert again.json()["id"] == job["id"]


def test_a_transcribe_replay_never_answers_with_a_later_job_that_reused_the_id(
    client, conn, transcribed
):
    headers = {"Idempotency-Key": "t-ids"}
    first = client.post(f"{V1}/media/{transcribed}/transcribe", json={"language": "en"}, headers=headers)
    with db.LOCK:
        conn.execute("DELETE FROM job WHERE id=?", (first.json()["id"],))
        conn.commit()
    later = client.post(f"{V1}/media/{transcribed}/transcribe", json={"language": "nl"})
    assert later.json()["id"] == first.json()["id"], "id not reused"
    again = client.post(f"{V1}/media/{transcribed}/transcribe", json={"language": "en"}, headers=headers)
    assert again.status_code == 201 and "idempotent-replayed" not in again.headers
    assert again.json()["id"] != later.json()["id"]
    assert json.loads(again.json()["params_json"])["language"] == "en"


def test_two_racing_claims_on_one_key_hold_it_once(conn):
    """`claim` looks up and holds under one lock: of two at once, one holds
    the key and the other is told it is in progress."""
    import threading
    import time as time_

    from scribe import idempotency

    real = idempotency._lookup

    def slow(*args, **kwargs):
        found = real(*args, **kwargs)
        time_.sleep(0.2)
        return found

    outcomes = []

    def one():
        try:
            outcomes.append(idempotency.claim(conn, "race-claim", "r", "p"))
        except idempotency.KeyInProgress:
            outcomes.append("in progress")

    idempotency._lookup = slow
    try:
        threads = [threading.Thread(target=one) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        idempotency._lookup = real
    assert sorted(map(str, outcomes)) == ["None", "in progress"]


@pytest.fixture
def ollama_ready(monkeypatch):
    monkeypatch.setattr(
        ollama.OllamaProvider, "tags",
        lambda self: [{"name": "qwen3.5:4b", "model": "qwen3.5:4b", "capabilities": ["completion"]}],
    )


def test_an_ai_question_queues_the_job_the_panel_queues(client, conn, transcribed, ollama_ready):
    resp = client.post(f"{V1}/media/{transcribed}/ai/summary", json={"provider": "ollama", "model": "qwen3.5:4b"})
    assert resp.status_code == 201, resp.text
    (job,) = jobs_of(conn)
    assert resp.json()["job"]["id"] == job["id"] and resp.json()["already_queued"] is False
    assert json.loads(job["params_json"]) == {
        "media_id": transcribed, "kind": "summary", "provider": "ollama", "model": "qwen3.5:4b",
    }
    again = client.post(f"{V1}/media/{transcribed}/ai/summary", json={"provider": "ollama", "model": "qwen3.5:4b"})
    assert again.status_code == 200
    assert again.json() == {**again.json(), "already_queued": True}
    assert again.json()["job"]["id"] == job["id"]
    assert len(jobs_of(conn)) == 1


def test_an_ai_question_with_nobody_chosen_is_a_coded_409(client, conn, transcribed):
    assert_coded(client.post(f"{V1}/media/{transcribed}/ai/summary", json={}), 409, "no_provider")
    assert jobs_of(conn) == []


def test_an_ai_question_to_a_provider_that_cannot_answer_is_a_coded_409(
    client, conn, transcribed, monkeypatch
):
    def down(self):
        from scribe.llm import base
        raise base.NothingAnswered("Ollama is not running")

    monkeypatch.setattr(ollama.OllamaProvider, "tags", down)
    resp = client.post(f"{V1}/media/{transcribed}/ai/summary", json={"provider": "ollama"})
    assert_coded(resp, 409, "provider_unavailable")
    assert jobs_of(conn) == []


def test_an_unknown_ai_kind_is_a_coded_404(client, transcribed):
    assert_coded(client.post(f"{V1}/media/{transcribed}/ai/poetry", json={}), 404, "not_found")


# --- jobs ------------------------------------------------------------------------------------------


def test_jobs_list_matches_the_pages_poll(client, conn):
    upload(client, tiny_wav(tone=3))
    upload(client, tiny_wav(tone=4))
    assert client.get(f"{V1}/jobs").json()["jobs"] == client.get("/api/jobs").json()
    assert client.get(f"{V1}/jobs", params={"status": "done"}).json()["jobs"] == []


def test_job_status_carries_its_events(client, conn):
    job_id = upload(client, tiny_wav()).json()["job_id"]
    body = client.get(f"{V1}/jobs/{job_id}").json()
    assert body == client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "queued" and "events" in body


def test_cancel_cancels_a_queued_job(client, conn):
    job_id = upload(client, tiny_wav()).json()["job_id"]
    assert client.post(f"{V1}/jobs/{job_id}/cancel").json()["status"] == "cancelled"


def test_an_unknown_job_is_a_coded_404(client):
    assert_coded(client.get(f"{V1}/jobs/999"), 404, "not_found")


# --- settings, read only and without secrets ----------------------------------------------------


def test_settings_read_says_what_the_next_job_gets(client, conn, tmp_path):
    folder = tmp_path / "watched"
    folder.mkdir()
    with db.LOCK:
        conn.execute(
            "INSERT INTO watch_folder(path, enabled, options_json) VALUES (?, 1, '{}')",
            (str(folder),),
        )
        conn.commit()
    body = client.get(f"{V1}/settings").json()
    assert set(body) == {"transcribe_defaults", "browse_roots", "watch_folders", "ai", "cpu_fallback"}
    assert {"language", "tier", "diarize"} <= set(body["transcribe_defaults"])
    assert [f["path"] for f in body["watch_folders"]] == [str(folder)]
    assert body["ai"]["provider"] == ""


def test_settings_read_never_carries_a_secret(client, conn):
    secrets = {"hf_token": "hf_SECRETsecret42", "llm_key_openrouter": "sk-or-SECRET-42"}
    with db.LOCK:
        for key, value in secrets.items():
            conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    text = client.get(f"{V1}/settings").text
    for value in secrets.values():
        assert value not in text


# --- the doctor, read only ------------------------------------------------------------------------


def fake_checks(*, failing_required: bool = False) -> list:
    return [
        doctor.Check("python", True, "3.12.10"),
        doctor.Check("ffmpeg", not failing_required, "not found" if failing_required else "ffmpeg 7.1",
                     fix_hint="install ffmpeg" if failing_required else ""),
        doctor.Check("yt-dlp", False, "not installed", fix_hint="uv sync", optional=True),
    ]


def test_the_doctor_answers_the_checks_the_settings_page_runs(client, conn, monkeypatch):
    """The page's GET runs `doctor.web_checks` (TASK-022: never a check that
    imports torch in the web process); the route runs exactly those."""
    monkeypatch.setattr(doctor, "web_checks", fake_checks)
    body = client.get(f"{V1}/doctor").json()
    assert set(body) == {"ok", "checks", "gpu_last", "gpu_job"}
    assert [c["name"] for c in body["checks"]] == ["python", "ffmpeg", "yt-dlp"]
    assert body["checks"][2] == {"name": "yt-dlp", "ok": False, "detail": "not installed",
                                 "fix_hint": "uv sync", "optional": True, "tested": True}
    # An optional check that fails does not make the machine fail.
    assert body["ok"] is True
    assert body["gpu_last"] is None and body["gpu_job"] is None


def test_the_doctor_is_not_ok_when_a_required_check_fails(client, monkeypatch):
    monkeypatch.setattr(doctor, "web_checks", lambda: fake_checks(failing_required=True))
    body = client.get(f"{V1}/doctor").json()
    assert body["ok"] is False
    assert body["checks"][1]["fix_hint"] == "install ffmpeg"


def test_the_doctor_reads_the_last_gpu_run_and_queues_nothing(client, conn, monkeypatch):
    monkeypatch.setattr(doctor, "web_checks", fake_checks)
    doctor.store_last_run(conn, [doctor.Check("gpu-smoke", True, "4.2x realtime")], job_id=None)
    body = client.get(f"{V1}/doctor").json()
    assert [c["name"] for c in body["gpu_last"]["checks"]] == ["gpu-smoke"]
    assert isinstance(body["gpu_last"]["ts"], float)
    assert jobs_of(conn) == []


def test_the_doctor_names_a_gpu_check_that_is_still_waiting(client, conn, monkeypatch):
    from scribe import jobs

    monkeypatch.setattr(doctor, "web_checks", fake_checks)
    job_id = jobs.enqueue(conn, doctor.JOB_TYPE)
    assert client.get(f"{V1}/doctor").json()["gpu_job"] == {"id": job_id, "status": "queued"}
    assert len(jobs_of(conn)) == 1


# --- watch folders ------------------------------------------------------------------------------


@pytest.fixture
def inbox(tmp_path):
    path = tmp_path / "inbox"
    path.mkdir()
    return path


def settled_file(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"a recording of something")
    old = time.time() - 60
    os.utime(path, (old, old))
    return path


def folders_of(conn) -> list:
    from scribe.ingest import watching

    return watching.folders(conn, enabled_only=False)


def test_a_watch_folder_added_here_is_the_one_the_page_adds(client, conn, inbox):
    resp = client.post(f"{V1}/watch-folders",
                       json={"path": str(inbox), "language": "nl", "tier": "max", "diarize": False})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    (row,) = folders_of(conn)
    assert body["id"] == row["id"]
    assert Path(body["path"]) == inbox
    assert body["enabled"] is True
    # Audio only unless asked, as the page's form.
    assert body["include_video"] is False and row["include_video"] == 0
    assert row["options"].language == "nl" and row["options"].tier == "max"
    assert row["options"].diarize is False
    assert body["options"]["tier"] == "max"
    assert body["message"] == "Watching that folder for new recordings."


def test_adding_a_folder_says_what_it_left_alone(client, conn, inbox):
    settled_file(inbox, "old.mp3")
    settled_file(inbox, "old.mp4")
    body = client.post(f"{V1}/watch-folders", json={"path": str(inbox)}).json()
    assert body["left"] == 1
    assert body["skipped_video"] == 1
    assert "left alone" in body["message"] and "video is off" in body["message"]
    assert jobs_of(conn) == []


def test_the_watch_list_is_what_the_page_shows(client, conn, inbox, tmp_path):
    client.post(f"{V1}/watch-folders", json={"path": str(inbox), "include_video": True})
    gone = tmp_path / "gone"
    gone.mkdir()
    client.post(f"{V1}/watch-folders", json={"path": str(gone)})
    gone.rmdir()
    body = client.get(f"{V1}/watch-folders").json()
    # No supervisor in these tests, so no watcher: the page says so too.
    assert body["watching"] is False
    by_path = {Path(f["path"]).name: f for f in body["folders"]}
    assert set(by_path) == {"inbox", "gone"}
    assert by_path["inbox"]["include_video"] is True
    assert by_path["inbox"]["missing"] is False and by_path["gone"]["missing"] is True
    assert set(by_path["inbox"]) == {"id", "path", "enabled", "include_video", "allowed", "missing",
                                     "unwatchable", "left", "options"}


@pytest.mark.parametrize(
    "case, status, code",
    [
        ("empty", 400, "bad_request"),
        ("relative", 400, "bad_request"),
        ("not_there", 400, "bad_request"),
        ("a_file", 400, "bad_request"),
        ("data_dir", 400, "bad_request"),
        ("outside_roots", 403, "forbidden"),
        ("bad_option", 400, "bad_request"),
        ("unknown_field", 400, "bad_request"),
        ("not_an_object", 400, "bad_request"),
    ],
)
def test_a_watch_folder_the_page_refuses_is_refused_here(client, conn, inbox, data_dir, tmp_path,
                                                         case, status, code):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    if case == "outside_roots":
        with db.LOCK:
            conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
                         (fsbrowse.SETTING_KEY, str(allowed)))
            conn.commit()
    data_dir.mkdir(parents=True, exist_ok=True)
    body = {
        "empty": {"path": "  "},
        "relative": {"path": "relative/folder"},
        "not_there": {"path": str(inbox / "nope")},
        "a_file": {"path": str(settled_file(inbox, "x.mp3"))},
        "data_dir": {"path": str(data_dir)},
        "outside_roots": {"path": str(inbox)},
        "bad_option": {"path": str(inbox), "tier": "huge"},
        "unknown_field": {"path": str(inbox), "colour": "blue"},
        "not_an_object": ["path"],
    }[case]
    assert_coded(client.post(f"{V1}/watch-folders", json=body), status, code)
    assert folders_of(conn) == []


def test_the_same_folder_twice_is_already_watched(client, conn, inbox):
    assert client.post(f"{V1}/watch-folders", json={"path": str(inbox)}).status_code == 201
    body = assert_coded(client.post(f"{V1}/watch-folders", json={"path": str(inbox)}),
                        409, "already_watched")
    assert "already being watched" in body["detail"]
    assert len(folders_of(conn)) == 1


def test_removing_a_watch_folder_keeps_the_library(client, conn, inbox, transcribed):
    folder_id = client.post(f"{V1}/watch-folders", json={"path": str(inbox)}).json()["id"]
    resp = client.delete(f"{V1}/watch-folders/{folder_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": folder_id, "removed": True}
    assert folders_of(conn) == []
    assert client.get(f"{V1}/media/{transcribed}").status_code == 200


def test_removing_a_watch_folder_that_is_not_there_is_a_coded_404(client):
    body = assert_coded(client.delete(f"{V1}/watch-folders/42"), 404, "not_found")
    assert "42" in body["detail"]


def test_a_foreign_host_or_site_changes_no_watch_folder(app, conn, inbox):
    with TestClient(app, base_url="http://evil.example") as foreign:
        assert foreign.post(f"{V1}/watch-folders", json={"path": str(inbox)}).status_code == 400
    assert folders_of(conn) == []
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            resp = client.post(f"{V1}/watch-folders", json={"path": str(inbox)}, headers=headers)
            assert resp.status_code == 403
        assert folders_of(conn) == []
        folder_id = client.post(f"{V1}/watch-folders", json={"path": str(inbox)}).json()["id"]
        for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            assert client.delete(f"{V1}/watch-folders/{folder_id}", headers=headers).status_code == 403
    with TestClient(app, base_url="http://evil.example") as foreign:
        assert foreign.delete(f"{V1}/watch-folders/{folder_id}").status_code == 400
    assert len(folders_of(conn)) == 1


# --- the guard covers it, and the phone door does not reach it -------------------------------------


def test_a_foreign_host_is_refused_before_any_v1_route_runs(app, conn):
    with TestClient(app, base_url="http://evil.example") as foreign:
        assert foreign.get(f"{V1}/media").status_code == 400
        assert upload(foreign, tiny_wav(), key="h").status_code == 400
    assert jobs_of(conn) == []


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}],
)
def test_a_post_from_another_site_is_refused(client, conn, headers):
    resp = client.post(
        f"{V1}/media", files={"file": ("clip.wav", tiny_wav(), "audio/wav")}, headers=headers
    )
    assert resp.status_code == 403
    assert jobs_of(conn) == []


def test_the_phone_door_reaches_no_v1_route(app):
    found = v1_paths(app)
    for known in ("/api/v1/media", "/api/v1/jobs/1", "/api/v1/media/1/transcript", "/api/v1/settings"):
        assert known in found, known
    with TestClient(app, base_url="http://127.0.0.1") as client:
        install_door(app, FakeClock(), FakePublisher())
        try:
            client.post("/phone/open", headers={"HX-Request": "true"})
            opening = door_of(client).opening
            base = f"http://127.0.0.1:{opening.port}"
            with http() as c:
                for path in found:
                    for prefix in ("", f"/{opening.secret}"):
                        for method in ("GET", "POST", "DELETE"):
                            answer = c.request(method, base + prefix + path)
                            assert answer.status_code == 404, (method, prefix and "<secret>", path)
            assert door_of(client).is_open() is True
        finally:
            app.state.phone_door.close()
