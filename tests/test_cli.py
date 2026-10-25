"""TASK-108 phase 1: the ``myscribe`` command line, a thin client on /api/v1.

The CLI is stdlib only (argparse + urllib) and talks to nothing but
``http://127.0.0.1:<port>/api/v1``. Most tests drive it through a byte-level
fake transport that hands the exact request bytes to a TestClient, so a
hand-built multipart body still goes through the app's real parser. A few
run the real urllib path against uvicorn on a free port in a thread: the
only way to see urllib's HTTPError, a refused connection and a proxy
variable for what they are.
"""

from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tomllib
import zipfile
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from scribe import cli, db, paths
from scribe.app import create_app
from seed import seed_job, seed_media, seed_run
from test_api_v1 import tiny_wav

ROOT = Path(__file__).resolve().parent.parent
E = cli.Exit


# --- fixtures ------------------------------------------------------------------------------------


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


class ClientTransport:
    """`cli`'s transport contract, answered by a TestClient: bytes in, bytes out."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.requests: list[tuple[str, str, dict, bytes]] = []

    def __call__(self, method, path, headers, body):
        data = b"" if body is None else (body if isinstance(body, bytes) else b"".join(body))
        self.requests.append((method, path, dict(headers), data))
        resp = self.client.request(method, path, headers=headers, content=data or None)
        return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, resp.content


@pytest.fixture
def transport(app):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield ClientTransport(client)


class Run:
    def __init__(self, code: int, out: bytes, err: str) -> None:
        self.code, self.out, self.err = code, out, err

    @property
    def text(self) -> str:
        return self.out.decode("utf-8")

    def json(self):
        return json.loads(self.out)


def run(transport, *argv: str) -> Run:
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="utf-8", newline="")
    err = io.StringIO()
    code = cli.main(list(argv), transport=transport, stdout=out, stderr=err)
    out.flush()
    return Run(code, raw.getvalue(), err.getvalue())


@pytest.fixture
def transcribed(conn):
    media_id = seed_media(conn, title="Guide", duration=20.0)
    seed_run(conn, media_id)
    return media_id


def jobs_of(conn):
    with db.LOCK:
        return conn.execute("SELECT * FROM job ORDER BY id").fetchall()


# --- what it is ----------------------------------------------------------------------------------


def test_the_default_port_is_the_apps():
    from scribe import __main__ as app_main

    assert cli.DEFAULT_PORT == app_main.DEFAULT_PORT == 4242


def test_the_cli_loads_nothing_but_the_standard_library():
    """Imported on its own, ``scribe.cli`` pulls in no third-party module:
    no fastapi, no pydantic, no uvicorn - argparse and urllib only."""
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); before = set(sys.modules);"
        "import scribe.cli;"
        "extra = sorted(m for m in set(sys.modules) - before"
        " if m.split('.')[0] not in sys.stdlib_module_names and m.split('.')[0] != 'scribe');"
        "ours = sorted(m for m in set(sys.modules) - before if m.split('.')[0] == 'scribe');"
        "import json; print(json.dumps([extra, ours]))"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(ROOT)],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    extra, ours = json.loads(out)
    assert extra == []
    assert ours == ["scribe", "scribe.cli"]


def test_an_unpackaged_project_declares_no_entry_point():
    """With ``tool.uv.package = false`` uv installs no ``[project.scripts]``
    and says so on every sync: "warning: Skipping installation of entry
    points (`project.scripts`) ... because this project is not packaged"
    (uv 0.12.18), a line the launcher streams into its setup window and log.
    In a clone the command is ``python -m scribe.cli``; an install gets a
    ``myscribe`` shim beside its launcher instead (tests/test_cli_shims.py)."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if pyproject["tool"]["uv"].get("package", True) is False:
        assert "scripts" not in pyproject["project"]
        assert "gui-scripts" not in pyproject["project"]
    assert callable(cli.main)


def test_the_documented_exit_codes_are_the_codes():
    """docs/configuration.md's table and `cli.Exit` cannot drift apart."""
    text = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    section = text.split("### Exit codes", 1)[1].split("\n#", 1)[0]
    documented = {int(n): name for n, name in re.findall(r"^\| (\d+) \| `(\w+)` \|", section, re.M)}
    assert documented == {member.value: member.name for member in E}


def test_every_request_goes_to_api_v1(transport, transcribed, conn, tmp_path):
    job = seed_job(conn, transcribed, status="done")
    clip = tmp_path / "clip.wav"
    clip.write_bytes(tiny_wav())
    for argv in (
        ["status"], ["list"], ["search", "towel"], ["upload", str(clip)],
        ["transcribe", str(transcribed)], ["jobs"], ["jobs", str(job)], ["wait", str(job)],
        ["transcript", str(transcribed)], ["export", str(transcribed), "--out", str(tmp_path)],
        ["cancel", str(job)],
    ):
        run(transport, *argv)
    paths_ = [path for _, path, _, _ in transport.requests]
    assert paths_ and all(path.startswith("/api/v1/") for path in paths_), paths_


# --- reading -------------------------------------------------------------------------------------


def test_status_says_it_answers_and_counts_the_queue(transport, conn):
    media_id = seed_media(conn)
    seed_job(conn, media_id, status="running")
    seed_job(conn, media_id, status="queued")
    seed_job(conn, media_id, status="queued")
    result = run(transport, "status")
    assert result.code == E.OK, result.err
    assert "127.0.0.1:4242" in result.text
    assert "1 running, 2 queued" in result.text


def test_status_as_json(transport):
    result = run(transport, "--json", "status")
    body = result.json()
    assert result.code == E.OK
    assert body["running"] == 0 and body["queued"] == 0
    assert set(body["settings"]) >= {"transcribe_defaults", "ai"}


def test_list_prints_each_recording(transport, conn):
    seed_media(conn, title="Alpha", duration=61.0)
    seed_media(conn, title="Beta", duration=None)
    result = run(transport, "list")
    assert result.code == E.OK
    lines = result.text.splitlines()
    assert any("Alpha" in line and "1:01" in line for line in lines)
    assert any("Beta" in line for line in lines)


def test_list_json_is_the_api_answer(transport, conn):
    media_id = seed_media(conn, title="Alpha")
    result = run(transport, "--json", "list", "--q", "Alp")
    assert result.code == E.OK
    assert [row["id"] for row in result.json()["media"]] == [media_id]
    assert transport.requests[-1][1] == "/api/v1/media?q=Alp"


def test_search_prints_the_hits(transport, transcribed):
    result = run(transport, "search", "towel")
    assert result.code == E.OK
    assert "Guide" in result.text and "towel" in result.text.lower()


def test_an_empty_search_is_refused_by_the_server(transport):
    result = run(transport, "search", " ")
    assert result.code == E.REFUSED
    assert "bad_request" in result.err


def test_an_unknown_recording_is_not_found(transport):
    result = run(transport, "transcript", "999")
    assert result.code == E.NOT_FOUND
    assert "not_found" in result.err


def test_no_transcript_yet_is_a_conflict(transport, conn):
    media_id = seed_media(conn)
    result = run(transport, "transcript", str(media_id))
    assert result.code == E.CONFLICT
    assert "no_transcript" in result.err
    assert result.out == b""


def test_the_transcript_is_the_json_export_byte_for_byte(transport, transcribed, tmp_path):
    direct = transport.client.get(f"/api/v1/media/{transcribed}/transcript").content
    result = run(transport, "transcript", str(transcribed))
    assert result.code == E.OK
    assert result.out == direct
    target = tmp_path / "t.json"
    assert run(transport, "transcript", str(transcribed), "--out", str(target)).code == E.OK
    assert target.read_bytes() == direct


def test_export_saves_the_file_under_the_servers_name(transport, transcribed, tmp_path):
    result = run(transport, "export", str(transcribed), "--format", "srt", "--out", str(tmp_path))
    assert result.code == E.OK, result.err
    (saved,) = tmp_path.glob("*.srt")
    assert saved.name == "Guide.srt"
    assert "-->" in saved.read_text(encoding="utf-8")
    assert str(saved) in result.text


def test_export_of_two_formats_is_an_intact_zip(transport, transcribed, tmp_path):
    target = tmp_path / "both.zip"
    result = run(transport, "export", str(transcribed), "--format", "srt,txt",
                 "--option", "speakers=false", "--out", str(target))
    assert result.code == E.OK, result.err
    with zipfile.ZipFile(target) as archive:
        assert sorted(Path(n).suffix for n in archive.namelist()) == [".srt", ".txt"]
    sent = json.loads(transport.requests[-1][3])
    assert sent == {"formats": "srt,txt", "speakers": False}


def test_export_to_stdout_keeps_the_bytes(transport, transcribed):
    direct = transport.client.post(f"/api/v1/media/{transcribed}/export",
                                   json={"formats": "srt,txt"}).content
    result = run(transport, "export", str(transcribed), "--format", "srt,txt", "--out", "-")
    assert result.code == E.OK
    assert result.out == direct  # no newline translation on a ZIP


def test_a_bad_export_option_is_refused(transport, transcribed, tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    result = run(transport, "export", str(transcribed), "--option", "cpl=1", "--out", str(folder))
    assert result.code == E.REFUSED
    assert "invalid_request" in result.err or "bad_request" in result.err
    assert list(folder.iterdir()) == []


# --- sending work in -----------------------------------------------------------------------------


def test_upload_sends_the_file_and_queues_its_job(transport, conn, tmp_path):
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav())
    result = run(transport, "--json", "upload", str(clip), "--title", "Synthetic",
                 "--option", "tier=max", "--option", "language=nl")
    assert result.code == E.OK, result.err
    body = result.json()
    (job,) = jobs_of(conn)
    assert body["job_id"] == job["id"] and body["title"] == "Synthetic"
    params = json.loads(job["params_json"])
    assert params["language"] == "nl"
    with db.LOCK:
        size = conn.execute("SELECT size_bytes FROM media WHERE id=?", (body["id"],)).fetchone()[0]
    assert size == clip.stat().st_size


def test_upload_streams_the_body_with_its_length(app, tmp_path, monkeypatch):
    """The body is an iterator of chunks with a Content-Length, never the
    whole file read at once: a two-hour recording is gigabytes."""
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav(seconds=2.0))
    monkeypatch.setattr(cli, "CHUNK", 1024)
    seen = {}

    with TestClient(app, base_url="http://127.0.0.1") as client:
        inner = ClientTransport(client)

        def spy(method, path, headers, body):
            seen["iterator"] = not isinstance(body, (bytes, bytearray))
            chunks = list(body)
            seen["chunks"] = len(chunks)
            seen["length"] = int(headers["Content-Length"])
            seen["actual"] = sum(map(len, chunks))
            return inner(method, path, headers, chunks)

        assert run(spy, "upload", str(clip)).code == E.OK
    assert seen["iterator"] and seen["chunks"] > 2
    assert seen["length"] == seen["actual"]


def test_upload_with_a_key_twice_queues_one_job(transport, conn, tmp_path):
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav())
    first = run(transport, "upload", str(clip), "--idempotency-key", "retry-1")
    again = run(transport, "upload", str(clip), "--idempotency-key", "retry-1")
    assert first.code == again.code == E.OK
    assert len(jobs_of(conn)) == 1
    assert "replayed" in again.text and "replayed" not in first.text


def test_a_reused_key_is_a_conflict(transport, tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    a.write_bytes(tiny_wav(tone=1))
    b.write_bytes(tiny_wav(tone=2))
    assert run(transport, "upload", str(a), "--idempotency-key", "k").code == E.OK
    result = run(transport, "upload", str(b), "--idempotency-key", "k")
    assert result.code == E.CONFLICT
    assert "idempotency_key_reused" in result.err


def test_upload_by_path_outside_the_roots_is_refused(transport, conn, tmp_path, monkeypatch):
    monkeypatch.setattr("scribe.fsbrowse.allowed_roots", lambda conn: (tmp_path / "allowed",))
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav())
    result = run(transport, "upload", "--by-path", str(clip))
    assert result.code == E.REFUSED
    assert json.loads(transport.requests[-1][3]) == {"path": str(clip.resolve())}
    assert jobs_of(conn) == []


def test_upload_by_path_under_a_root_queues(transport, conn, tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setattr("scribe.fsbrowse.allowed_roots", lambda conn: (root,))
    clip = root / "talk.wav"
    clip.write_bytes(tiny_wav())
    result = run(transport, "upload", "--by-path", str(clip))
    assert result.code == E.OK, result.err
    assert len(jobs_of(conn)) == 1


def test_a_missing_file_is_a_usage_error_before_any_request(transport, tmp_path):
    result = run(transport, "upload", str(tmp_path / "nope.wav"))
    assert result.code == E.USAGE
    assert transport.requests == []


def test_an_option_without_a_value_is_a_usage_error(transport, transcribed):
    result = run(transport, "transcribe", str(transcribed), "--option", "tier")
    assert result.code == E.USAGE
    assert transport.requests == []


def test_transcribe_queues_again_with_the_options(transport, conn, transcribed):
    result = run(transport, "--json", "transcribe", str(transcribed), "--option", "language=de")
    assert result.code == E.OK, result.err
    (job,) = jobs_of(conn)
    assert result.json()["id"] == job["id"]
    assert json.loads(job["params_json"])["language"] == "de"


def test_transcribe_with_a_key_twice_queues_one_job(transport, conn, transcribed):
    for _ in range(2):
        assert run(transport, "transcribe", str(transcribed), "--idempotency-key", "t-1").code == E.OK
    assert len(jobs_of(conn)) == 1


# --- jobs ----------------------------------------------------------------------------------------


def test_jobs_lists_newest_first(transport, conn):
    media_id = seed_media(conn, title="Clip")
    old = seed_job(conn, media_id, status="done")
    new = seed_job(conn, media_id, status="queued")
    result = run(transport, "jobs")
    assert result.code == E.OK
    ids = [int(line.split()[0]) for line in result.text.splitlines()[1:]]
    assert ids == [new, old]


def test_jobs_with_an_id_shows_its_events(transport, conn):
    media_id = seed_media(conn)
    job = seed_job(conn, media_id, status="failed", error_code="BOOM", error_detail="synthetic")
    result = run(transport, "--json", "jobs", str(job))
    assert result.code == E.OK
    assert result.json()["id"] == job and "events" in result.json()
    assert "BOOM" in run(transport, "jobs", str(job)).text


def test_cancel_a_queued_job(transport, conn):
    job = seed_job(conn, seed_media(conn), status="queued")
    result = run(transport, "cancel", str(job))
    assert result.code == E.OK
    with db.LOCK:
        row = conn.execute("SELECT status, cancel_requested FROM job WHERE id=?", (job,)).fetchone()
    assert row["status"] == "cancelled" or row["cancel_requested"]


def test_wait_for_a_done_job_is_ok(transport, conn):
    job = seed_job(conn, seed_media(conn), status="done")
    result = run(transport, "wait", str(job))
    assert result.code == E.OK
    assert "done" in result.text


@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
def test_wait_for_a_job_that_did_not_finish_well(transport, conn, status):
    job = seed_job(conn, seed_media(conn), status=status, error_code="X_CODE", error_detail="why")
    result = run(transport, "wait", str(job))
    assert result.code == E.JOB_UNSUCCESSFUL
    assert status in result.text
    if status == "failed":
        assert "X_CODE" in result.err


def test_wait_polls_until_the_job_ends(transport, conn, monkeypatch):
    job = seed_job(conn, seed_media(conn), status="running")
    polls = []

    def fake_sleep(seconds):
        polls.append(seconds)
        if len(polls) == 3:
            from scribe import jobs

            jobs.finish(conn, job, "done")

    monkeypatch.setattr(cli, "_sleep", fake_sleep)
    result = run(transport, "wait", str(job), "--interval", "0.5")
    assert result.code == E.OK
    assert polls == [0.5, 0.5, 0.5]


def test_wait_times_out(transport, conn, monkeypatch):
    job = seed_job(conn, seed_media(conn), status="queued")
    clock = iter(float(n) for n in range(1000))
    monkeypatch.setattr(cli, "_now", lambda: next(clock))
    monkeypatch.setattr(cli, "_sleep", lambda seconds: None)
    result = run(transport, "wait", str(job), "--timeout", "5")
    assert result.code == E.TIMEOUT
    assert "queued" in result.err


def test_wait_for_an_unknown_job_is_not_found(transport):
    assert run(transport, "wait", "77").code == E.NOT_FOUND


# --- answers that are not the API's own ---------------------------------------------------------


# --- the doctor and the watch folders --------------------------------------------------------------


def fake_checks(*, failing_required: bool = False) -> list:
    from scribe import doctor

    return [
        doctor.Check("python", True, "3.12.10"),
        doctor.Check("ffmpeg", not failing_required, "not found" if failing_required else "ffmpeg 7.1",
                     fix_hint="install ffmpeg" if failing_required else ""),
        doctor.Check("yt-dlp", False, "not installed", fix_hint="uv sync", optional=True),
    ]


@pytest.fixture
def checks(monkeypatch):
    from scribe import doctor

    state = {"failing": False}
    monkeypatch.setattr(doctor, "web_checks", lambda: fake_checks(failing_required=state["failing"]))
    return state


def test_doctor_prints_each_check_and_passes(transport, conn, checks):
    result = run(transport, "doctor")
    assert result.code == E.OK, result.err
    lines = result.text.splitlines()
    assert any(line.startswith("[OK  ]") and "python" in line and "3.12.10" in line for line in lines)
    assert any(line.startswith("[SKIP]") and "yt-dlp" in line for line in lines), lines
    assert "GPU checks: never run" in result.text
    assert transport.requests[-1][:2] == ("GET", "/api/v1/doctor")
    assert jobs_of(conn) == []


def test_doctor_with_a_failing_required_check_has_its_own_exit(transport, checks):
    checks["failing"] = True
    result = run(transport, "doctor")
    assert result.code == E.CHECK_FAILED
    assert any(line.startswith("[FAIL]") and "ffmpeg" in line for line in result.text.splitlines())
    assert "install ffmpeg" in result.text


def test_doctor_shows_the_last_gpu_run(transport, conn, checks):
    from scribe import doctor

    doctor.store_last_run(conn, [doctor.Check("gpu-smoke", True, "4.2x realtime")])
    result = run(transport, "doctor")
    assert "GPU checks, last run" in result.text and "4.2x realtime" in result.text


def test_doctor_json_is_the_api_answer_and_keeps_the_exit(transport, checks):
    checks["failing"] = True
    result = run(transport, "--json", "doctor")
    assert result.code == E.CHECK_FAILED
    assert result.json()["ok"] is False


def test_watch_add_sends_an_absolute_path_and_the_options(transport, conn, tmp_path, monkeypatch):
    from scribe.ingest import watching

    (tmp_path / "inbox").mkdir()
    monkeypatch.chdir(tmp_path)
    result = run(transport, "watch", "add", "inbox", "--option", "tier=max", "--include-video")
    assert result.code == E.OK, result.err
    (row,) = watching.folders(conn, enabled_only=False)
    assert Path(row["path"]) == tmp_path / "inbox"
    assert row["options"].tier == "max" and row["include_video"] == 1
    sent = json.loads(transport.requests[-1][3])
    assert sent == {"path": str(tmp_path / "inbox"), "include_video": True, "tier": "max"}
    assert f"watch folder {row['id']}" in result.text
    assert "Watching that folder for new recordings." in result.text


def test_watch_list_and_remove(transport, conn, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    folder_id = run(transport, "--json", "watch", "add", str(inbox)).json()["id"]
    listed = run(transport, "watch", "list")
    assert listed.code == E.OK
    assert any(str(folder_id) in line and str(inbox) in line and "on" in line
               for line in listed.text.splitlines()), listed.text
    as_json = run(transport, "--json", "watch", "list").json()
    assert [f["id"] for f in as_json["folders"]] == [folder_id]
    removed = run(transport, "watch", "remove", str(folder_id))
    assert removed.code == E.OK
    assert f"watch folder {folder_id} removed" in removed.text
    assert run(transport, "--json", "watch", "list").json()["folders"] == []


def test_watch_add_twice_is_a_conflict(transport, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    assert run(transport, "watch", "add", str(inbox)).code == E.OK
    again = run(transport, "watch", "add", str(inbox))
    assert again.code == E.CONFLICT
    assert "already_watched" in again.err


def test_watch_add_outside_the_roots_is_refused(transport, conn, tmp_path):
    from scribe import fsbrowse

    (tmp_path / "allowed").mkdir()
    (tmp_path / "inbox").mkdir()
    with db.LOCK:
        conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
                     (fsbrowse.SETTING_KEY, str(tmp_path / "allowed")))
        conn.commit()
    result = run(transport, "watch", "add", str(tmp_path / "inbox"))
    assert result.code == E.REFUSED
    assert "error 403 forbidden" in result.err


def test_watch_remove_of_an_unknown_folder_is_not_found(transport):
    assert run(transport, "watch", "remove", "42").code == E.NOT_FOUND


def test_watch_existing_transcribes_what_the_folder_left_alone(transport, conn, tmp_path):
    """The page's "Transcribe them" button, from a script (TASK-108 review)."""
    from scribe.ingest import watching

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "old.mp3").write_bytes(b"a recording of something")
    folder_id = run(transport, "--json", "watch", "add", str(inbox)).json()["id"]
    assert watching.existing_left(conn, folder_id) == 1

    result = run(transport, "watch", "existing", str(folder_id))

    assert result.code == E.OK, result.err
    assert transport.requests[-1][:2] == ("POST", f"/api/v1/watch-folders/{folder_id}/existing")
    assert "1 audio file already in it will be transcribed" in result.text
    assert watching.existing_left(conn, folder_id) == 0
    as_json = run(transport, "--json", "watch", "existing", str(folder_id)).json()
    assert as_json == {"id": folder_id, "taken": 0, "message": as_json["message"]}


def test_watch_existing_of_an_unknown_folder_is_not_found(transport):
    assert run(transport, "watch", "existing", "42").code == E.NOT_FOUND


def test_a_plain_text_error_is_reported_without_a_traceback():
    """The guard's Host refusal is Starlette's plain text, not a coded body."""
    def plain(method, path, headers, body):
        return 400, {"content-type": "text/plain"}, b"Invalid host header"

    result = run(plain, "list")
    assert result.code == E.REFUSED
    assert "Invalid host header" in result.err and "Traceback" not in result.err


def test_a_codeless_json_error_is_reported():
    """The guard's 403 is ``{"detail": ...}`` with no code."""
    def guard(method, path, headers, body):
        return 403, {"content-type": "application/json"}, b'{"detail": "cross-site request refused"}'

    result = run(guard, "cancel", "1")
    assert result.code == E.REFUSED
    assert "cross-site request refused" in result.err


def test_a_server_error_is_its_own_exit():
    def broken(method, path, headers, body):
        return 500, {"content-type": "application/json"}, b'{"code": "internal_error", "detail": "x"}'

    assert run(broken, "list").code == E.SERVER_ERROR


def test_a_success_that_is_not_json_is_a_server_error():
    def odd(method, path, headers, body):
        return 200, {"content-type": "text/html"}, b"<html>"

    result = run(odd, "list")
    assert result.code == E.SERVER_ERROR and "Traceback" not in result.err


# --- saving a download (review of a5990b9) ------------------------------------------------------


def test_a_title_with_a_semicolon_keeps_its_whole_name(transport, conn, tmp_path):
    """The server quotes an ASCII name and keeps its ';': `Episode 3; the
    return.srt`, not `Episode 3`. Two formats into one folder are two files."""
    media_id = seed_media(conn, title="Episode 3; the return", duration=20.0)
    seed_run(conn, media_id)
    out = tmp_path / "out"  # tmp_path also holds the test's database
    out.mkdir()
    for fmt in ("srt", "txt"):
        result = run(transport, "export", str(media_id), "--format", fmt, "--out", str(out))
        assert result.code == E.OK, result.err
    assert sorted(p.name for p in out.iterdir()) == [
        "Episode 3; the return.srt", "Episode 3; the return.txt"]


def test_the_utf8_name_wins_over_the_ascii_fallback(transport, conn, tmp_path):
    media_id = seed_media(conn, title="Café; deel 2", duration=20.0)
    seed_run(conn, media_id)
    out = tmp_path / "out"
    out.mkdir()
    result = run(transport, "export", str(media_id), "--format", "srt", "--out", str(out))
    assert result.code == E.OK, result.err
    assert [p.name for p in out.iterdir()] == ["Café; deel 2.srt"]


@pytest.mark.parametrize("disposition", [
    'attachment; filename="../../x.srt"',
    'attachment; filename="..\\..\\x.srt"',
    'attachment; filename="/abs/x.srt"',
    'attachment; filename="C:\\x.srt"',
    'attachment; filename="C:x.srt"',
    "attachment; filename*=utf-8''..%2F..%2Fx.srt",
    "attachment; filename*=utf-8''..%5C..%5Cx.srt",
])
def test_the_servers_name_is_never_a_path(disposition):
    name = cli._filename({"content-disposition": disposition})
    assert name, disposition
    assert "/" not in name and "\\" not in name and name not in (".", "..")
    if os.name == "nt":  # a drive, even without a separator, is a path there
        assert ":" not in name


@pytest.mark.parametrize("disposition", ['attachment; filename=".."', 'attachment; filename="../"',
                                         "attachment", ""])
def test_no_usable_name_falls_back(disposition, tmp_path):
    def answer(method, path, headers, body):
        return 200, {"content-disposition": disposition}, b"data"

    result = run(answer, "export", "1", "--out", str(tmp_path))
    assert result.code == E.OK, result.err
    assert [p.name for p in tmp_path.iterdir()] == ["media-1.export"]


def test_a_name_that_climbs_stays_in_the_out_folder(tmp_path):
    out = tmp_path / "a" / "b" / "c"
    out.mkdir(parents=True)

    def climbing(method, path, headers, body):
        return 200, {"content-disposition": 'attachment; filename="../../../x.srt"'}, b"data"

    result = run(climbing, "export", "1", "--out", str(out))
    assert result.code == E.OK, result.err
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()] \
        == ["a/b/c/x.srt"]


def test_an_out_folder_that_is_not_there_is_a_usage_error(tmp_path):
    def answer(method, path, headers, body):
        return 200, {"content-disposition": 'attachment; filename="x.srt"'}, b"data"

    result = run(answer, "export", "1", "--out", str(tmp_path / "no" / "such" / "x.srt"))
    assert result.code == E.USAGE
    assert "Traceback" not in result.err and "x.srt" in result.err


def test_a_file_that_is_there_is_replaced_and_said_so(tmp_path):
    target = tmp_path / "x.srt"
    target.write_bytes(b"old")

    def answer(method, path, headers, body):
        return 200, {}, b"new"

    result = run(answer, "export", "1", "--out", str(target))
    assert result.code == E.OK, result.err
    assert target.read_bytes() == b"new"
    assert "replaced" in result.err and str(target) in result.err
    assert "replaced" not in run(answer, "export", "1", "--out", str(tmp_path / "y.srt")).err


# --- numbers and answers the CLI cannot use -----------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["wait", "1", "--interval", "-1"],
    ["wait", "1", "--interval", "nan"],
    ["wait", "1", "--interval", "0"],
    ["wait", "1", "--timeout", "-1"],
    ["--http-timeout", "0", "status"],
    ["--http-timeout", "-5", "status"],
])
def test_a_number_that_cannot_be_is_a_usage_error(argv):
    asked = []

    def answer(method, path, headers, body):
        asked.append(path)
        return 200, {}, b'{"id": 1, "status": "done"}'

    result = run(answer, *argv)
    assert result.code == E.USAGE
    assert asked == [] and "Traceback" not in result.err


@pytest.mark.parametrize("argv, body", [
    (["wait", "1"], b"{}"),
    (["wait", "1"], b"[]"),
    (["upload", "{clip}"], b'{"title": "x"}'),
    (["list"], b'{"media": [{"title": "no id"}]}'),
    (["jobs"], b'{"jobs": "not a list of jobs"}'),
])
def test_an_answer_without_what_the_api_promises_is_a_server_error(argv, body, tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")

    def answer(method, path, headers, data):
        if data is not None and not isinstance(data, bytes):
            b"".join(data)  # the upload's streamed body, read as a server would
        return 200, {"content-type": "application/json"}, body

    result = run(answer, *[a.format(clip=clip) for a in argv])
    assert result.code == E.SERVER_ERROR, result.err
    assert "Traceback" not in result.err and "not what the API promises" in result.err


# --- the real urllib path ------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live(app):
    """The app under uvicorn on a free 127.0.0.1 port, in a thread."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield port
    server.should_exit = True
    thread.join(timeout=30)
    sock.close()


def test_nothing_listening_is_not_running():
    port = free_port()
    result = run(None, "--port", str(port), "status")
    assert result.code == E.NOT_RUNNING
    assert f"127.0.0.1:{port}" in result.err and "Traceback" not in result.err


def test_the_real_transport_reads_answers_and_errors(live, conn):
    seed_media(conn, title="Live one")
    listed = run(None, "--port", str(live), "list")
    assert listed.code == E.OK and "Live one" in listed.text
    missing = run(None, "--port", str(live), "jobs", "999")
    assert missing.code == E.NOT_FOUND and "not_found" in missing.err


def test_a_proxy_variable_does_not_divert_the_loopback(live, conn, monkeypatch):
    """urllib would send every request to $HTTP_PROXY; a corporate proxy
    cannot reach this laptop's 127.0.0.1, so the CLI never asks it."""
    seed_media(conn, title="Behind a proxy")
    dead = f"http://127.0.0.1:{free_port()}"
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, dead)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    result = run(None, "--port", str(live), "list")
    assert result.code == E.OK, result.err
    assert "Behind a proxy" in result.text


def test_the_real_transport_uploads(live, conn, tmp_path):
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav(seconds=1.0))
    result = run(None, "--port", str(live), "--json", "upload", str(clip), "--title", "Over HTTP")
    assert result.code == E.OK, result.err
    assert result.json()["title"] == "Over HTTP"
    assert len(jobs_of(conn)) == 1


def test_a_title_a_cp1252_console_cannot_show(live, conn):
    seed_media(conn, title="Snow ☃ 中文")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": str(ROOT)}
    done = subprocess.run(
        [sys.executable, "-m", "scribe.cli", "--port", str(live), "list"],
        capture_output=True, env=env, timeout=120, cwd=str(ROOT),
    )
    assert done.returncode == E.OK, done.stderr
    assert b"Traceback" not in done.stderr
    assert b"Snow" in done.stdout


@pytest.fixture
def raw_server():
    """A 127.0.0.1 port that answers every connection with fixed bytes."""
    servers = []

    def start(reply: bytes) -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()

        def serve():
            while True:
                try:
                    conn_, _ = sock.accept()
                except OSError:
                    return
                with conn_:
                    conn_.settimeout(5)
                    try:
                        conn_.recv(65536)
                        conn_.sendall(reply)
                    except OSError:
                        pass

        threading.Thread(target=serve, daemon=True).start()
        servers.append(sock)
        return sock.getsockname()[1]

    yield start
    for sock in servers:
        sock.close()


@pytest.mark.parametrize("reply", [
    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"jobs\"",
    b"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"co",
    b"NOT HTTP AT ALL\r\n\r\n",
], ids=["short-body", "short-error-body", "bad-status-line"])
def test_an_answer_that_breaks_off_is_a_server_error(raw_server, reply):
    port = raw_server(reply)
    result = run(None, "--port", str(port), "jobs")
    assert result.code == E.SERVER_ERROR, result.err
    assert "Traceback" not in result.err and f"127.0.0.1:{port}" in result.err
