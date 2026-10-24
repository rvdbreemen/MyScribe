"""TASK-108 phase 2: ``myscribe mcp``, an MCP server on stdio over /api/v1.

The server is a thin client of the JSON API, exactly as the CLI is: it
reuses `scribe.cli`'s `Api` and transport and never opens the database, so
ADR-013's single writer stays the running app. It is hand-written JSON-RPC
on the standard library (ADR-031): the official SDK would add twelve
packages to every user's frozen environment for a few hundred lines of
protocol.

Most tests drive `serve` with byte streams and answer the API through
`test_cli.ClientTransport` (a TestClient behind the CLI's transport
contract), so every tool call goes through the real routes. One test spawns
the real entry point against uvicorn: the only way to see that nothing but
protocol reaches stdout.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scribe import cli, db, mcp_server
from seed import seed_job, seed_media, seed_run
from test_api_v1 import tiny_wav
from test_cli import ClientTransport, app, conn, data_dir, db_path, jobs_of, live, transport  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


# --- a session --------------------------------------------------------------------------------


def exchange(transport, *messages, raw: bytes | None = None) -> list[dict]:
    """Every line the server writes for these client messages, parsed."""
    data = raw if raw is not None else b"".join(
        (m if isinstance(m, bytes) else json.dumps(m).encode("utf-8")) + b"\n" for m in messages
    )
    stdout = io.BytesIO()
    stderr = io.StringIO()
    api = cli.Api(transport, 4242) if transport is not None else None
    mcp_server.serve(api, stdin=io.BytesIO(data), stdout=stdout, stderr=stderr)
    lines = stdout.getvalue().split(b"\n")
    assert lines[-1] == b"", "every message ends with a newline"
    assert all(not line.endswith(b"\r") for line in lines), "no CRLF on the wire"
    return [json.loads(line) for line in lines[:-1]]


def request(id_: int, method: str, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


INIT = request(0, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                 "clientInfo": {"name": "test", "version": "0"}})
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def call(transport, name: str, arguments: dict | None = None) -> dict:
    """The result of one tools/call after a handshake."""
    answers = exchange(transport, INIT, INITIALIZED,
                       request(1, "tools/call", {"name": name, "arguments": arguments or {}}))
    assert [a["id"] for a in answers] == [0, 1]
    assert "error" not in answers[1], answers[1]
    return answers[1]["result"]


def payload(result: dict):
    assert result.get("isError") is not True, result
    (item,) = result["content"]
    assert item["type"] == "text"
    return json.loads(item["text"])


def error_text(result: dict) -> str:
    assert result["isError"] is True, result
    (item,) = result["content"]
    return item["text"]


@pytest.fixture
def transcribed(conn):
    media_id = seed_media(conn, title="Guide", duration=20.0)
    seed_run(conn, media_id)
    return media_id


# --- the protocol ---------------------------------------------------------------------------


def test_the_handshake_names_the_server_and_its_tools_capability():
    (answer,) = exchange(None, INIT)
    assert answer["id"] == 0 and answer["jsonrpc"] == "2.0"
    result = answer["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "myscribe"
    from scribe import __version__
    assert result["serverInfo"]["version"] == __version__


@pytest.mark.parametrize("asked, answered", [
    ("2024-11-05", "2024-11-05"),
    ("2025-06-18", "2025-06-18"),
    ("2099-01-01", "2025-11-25"),  # unknown: the newest it speaks, the client decides
])
def test_the_protocol_version_is_the_clients_when_known(asked, answered):
    message = request(0, "initialize", {"protocolVersion": asked, "capabilities": {}})
    (answer,) = exchange(None, message)
    assert answer["result"]["protocolVersion"] == answered


def test_a_notification_gets_no_answer():
    assert exchange(None, INITIALIZED, {"jsonrpc": "2.0", "method": "notifications/cancelled",
                                        "params": {"requestId": 3}}) == []


def test_ping_is_answered_empty():
    assert exchange(None, request(7, "ping")) == [{"jsonrpc": "2.0", "id": 7, "result": {}}]


def test_an_unknown_method_is_method_not_found():
    """The SDK 2.x client probes ``server/discover`` first and falls back to
    ``initialize`` on any error; silence would make it wait for its timeout."""
    (answer,) = exchange(None, request(1, "server/discover", {}))
    assert answer["id"] == 1 and answer["error"]["code"] == -32601


def test_a_line_that_is_not_json_is_a_parse_error_and_the_session_goes_on():
    answers = exchange(None, raw=b"{not json\n" + json.dumps(request(2, "ping")).encode() + b"\n")
    assert answers[0] == {"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": answers[0]["error"]["message"]}}
    assert answers[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


@pytest.mark.parametrize("message", [
    [request(1, "ping")],  # a batch: removed from MCP in 2025-06-18
    {"jsonrpc": "1.0", "id": 1, "method": "ping"},
    {"jsonrpc": "2.0", "id": 1},
    "ping",
])
def test_a_message_that_is_not_a_request_is_invalid(message):
    (answer,) = exchange(None, message)
    assert answer["error"]["code"] == -32600


def test_a_reply_from_the_client_is_ignored():
    assert exchange(None, {"jsonrpc": "2.0", "id": 9, "result": {}}) == []


def test_blank_lines_and_crlf_are_tolerated():
    answers = exchange(None, raw=b"\r\n\n" + json.dumps(request(3, "ping")).encode() + b"\r\n")
    assert answers == [{"jsonrpc": "2.0", "id": 3, "result": {}}]


def test_the_server_writes_utf8_not_escapes(transport, conn):
    seed_media(conn, title="Snow ☃ 中文")
    answers = exchange(transport, request(1, "tools/call", {"name": "list_recordings", "arguments": {}}))
    assert "Snow ☃ 中文" in answers[0]["result"]["content"][0]["text"]


# --- the tool list --------------------------------------------------------------------------


EXPECTED_TOOLS = {
    "list_recordings", "get_recording", "search_transcripts", "get_transcript",
    "export_transcript", "add_recording", "transcribe_again", "ask_ai",
    "list_jobs", "get_job", "cancel_job", "get_settings",
}


def test_tools_list_names_every_tool_with_a_schema():
    (answer,) = exchange(None, request(1, "tools/list"))
    tools = answer["result"]["tools"]
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool["description"].strip(), tool["name"]
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema.get("required", ())) <= set(schema["properties"]), tool["name"]
        assert isinstance(tool["annotations"]["readOnlyHint"], bool)
    assert "nextCursor" not in answer["result"]


def test_read_tools_say_so_and_writers_do_not():
    (answer,) = exchange(None, request(1, "tools/list"))
    read_only = {t["name"] for t in answer["result"]["tools"] if t["annotations"]["readOnlyHint"]}
    assert read_only == {"list_recordings", "get_recording", "search_transcripts",
                         "get_transcript", "list_jobs", "get_job", "get_settings"}


def test_the_schemas_enums_are_the_apps():
    """The server imports nothing of the app (it must stay stdlib), so its
    enums are copies; these keep them equal to the originals."""
    from scribe import exports, options
    from scribe.llm import tasks
    from scribe.web import library

    tools = {tool.name: tool.schema["properties"] for tool in mcp_server.TOOLS}
    assert tools["list_recordings"]["view"]["enum"] == list(library.VIEWS)
    assert tools["list_recordings"]["sort"]["enum"] == list(library.SORTS)
    assert tools["export_transcript"]["formats"]["items"]["enum"] == list(exports.FORMATS)
    assert tools["ask_ai"]["kind"]["enum"] == list(tasks.KINDS)
    transcription = set(options.OPTION_FIELDS)
    assert transcription <= set(tools["add_recording"]) and transcription <= set(tools["transcribe_again"])


def test_the_server_loads_nothing_but_the_standard_library():
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); before = set(sys.modules);"
        "import scribe.mcp_server;"
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
    assert ours == ["scribe", "scribe.cli", "scribe.mcp_server"]


# --- arguments ---------------------------------------------------------------------------------


def test_an_unknown_tool_is_invalid_params():
    (answer,) = exchange(None, request(1, "tools/call", {"name": "format_disk", "arguments": {}}))
    assert answer["error"]["code"] == -32602 and "format_disk" in answer["error"]["message"]


@pytest.mark.parametrize("arguments, said", [
    ({}, "media_id"),  # missing
    ({"media_id": "7"}, "integer"),
    ({"media_id": True}, "integer"),  # a bool is not a number here
    ({"media_id": 1, "colour": "red"}, "colour"),
])
def test_bad_arguments_are_a_tool_error_the_model_can_correct(arguments, said):
    """Input validation is a tool execution error (isError), not a protocol
    error, so the model sees what was wrong (MCP 2025-11-25)."""
    (answer,) = exchange(None, request(1, "tools/call", {"name": "get_recording", "arguments": arguments}))
    assert said in error_text(answer["result"])


def test_an_enum_value_outside_the_list_is_refused_before_any_request():
    sent = []
    result = call(lambda *a: sent.append(a), "list_jobs", {"status": "exploded"})
    assert "status" in error_text(result) and sent == []


# --- each tool through the real API ------------------------------------------------------------


def test_list_recordings(transport, conn):
    media_id = seed_media(conn, title="Synthetic talk")
    body = payload(call(transport, "list_recordings", {"sort": "title"}))
    assert [row["id"] for row in body["media"]] == [media_id]
    assert transport.requests[-1][1] == "/api/v1/media?sort=title"


def test_get_recording(transport, transcribed):
    body = payload(call(transport, "get_recording", {"media_id": transcribed}))
    assert body["id"] == transcribed and body["has_transcript"] is True


def test_an_unknown_recording_is_a_tool_error_with_the_apis_code(transport):
    text = error_text(call(transport, "get_recording", {"media_id": 999}))
    assert "404" in text and "not_found" in text


def test_search_transcripts(transport, transcribed):
    direct = transport.client.get("/api/v1/search", params={"q": "towel"}).json()
    body = payload(call(transport, "search_transcripts", {"query": "towel"}))
    assert body == direct


def test_get_transcript_is_the_json_export(transport, transcribed):
    direct = transport.client.get(f"/api/v1/media/{transcribed}/transcript").json()
    body = payload(call(transport, "get_transcript", {"media_id": transcribed}))
    assert body == direct


def test_export_a_text_format_comes_back_as_text(transport, transcribed):
    direct = transport.client.post(f"/api/v1/media/{transcribed}/export", json={"formats": ["srt"]})
    body = payload(call(transport, "export_transcript", {"media_id": transcribed, "formats": ["srt"]}))
    assert body["filename"] == "Guide.srt"
    assert body["text"] == direct.content.decode("utf-8") and "-->" in body["text"]


def test_export_saved_to_a_folder_under_the_servers_name(transport, transcribed, tmp_path):
    body = payload(call(transport, "export_transcript", {
        "media_id": transcribed, "formats": ["srt", "txt"], "options": {"speakers": False},
        "save_to": str(tmp_path)}))
    saved = Path(body["saved"])
    assert saved.parent == tmp_path and body["bytes"] == saved.stat().st_size
    with zipfile.ZipFile(saved) as archive:
        assert sorted(Path(n).suffix for n in archive.namelist()) == [".srt", ".txt"]
    assert json.loads(transport.requests[-1][3]) == {"formats": ["srt", "txt"], "speakers": False}


def test_a_binary_export_without_save_to_is_refused_without_bytes(transport, transcribed):
    text = error_text(call(transport, "export_transcript", {"media_id": transcribed, "formats": ["docx"]}))
    assert "save_to" in text


def test_export_options_cannot_override_the_formats(transport, transcribed):
    text = error_text(call(transport, "export_transcript", {
        "media_id": transcribed, "options": {"formats": ["docx"]}}))
    assert "formats" in text


def test_add_recording_sends_the_bytes_and_queues(transport, conn, tmp_path):
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav())
    body = payload(call(transport, "add_recording", {
        "path": str(clip), "title": "Synthetic", "language": "nl", "diarize": False, "num_speakers": 2}))
    (job,) = jobs_of(conn)
    assert body["job_id"] == job["id"] and body["title"] == "Synthetic"
    params = json.loads(job["params_json"])
    assert params["language"] == "nl" and params["diarize"] is False


def test_add_recording_with_params_sends_them_as_json(transport, conn, tmp_path):
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav())
    payload(call(transport, "add_recording", {"path": str(clip), "params": {"language": "de"}}))
    (job,) = jobs_of(conn)
    assert json.loads(job["params_json"])["language"] == "de"


def test_add_recording_with_a_key_twice_queues_one_job(transport, conn, tmp_path):
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav())
    args = {"path": str(clip), "idempotency_key": "agent-1"}
    first = payload(call(transport, "add_recording", args))
    second = call(transport, "add_recording", args)
    assert payload(second)["job_id"] == first["job_id"]
    assert len(jobs_of(conn)) == 1
    assert second["_meta"] == {"myscribe/replayed": True}
    assert "_meta" not in call(transport, "add_recording", {"path": str(clip)})


def test_add_recording_by_path_outside_the_roots_is_refused(transport, conn, tmp_path, monkeypatch):
    monkeypatch.setattr("scribe.fsbrowse.allowed_roots", lambda conn: (tmp_path / "allowed",))
    clip = tmp_path / "talk.wav"
    clip.write_bytes(tiny_wav())
    error_text(call(transport, "add_recording", {"path": str(clip), "by_path": True}))
    assert json.loads(transport.requests[-1][3]) == {"path": str(clip.resolve())}
    assert jobs_of(conn) == []


def test_add_recording_of_a_missing_file_sends_nothing(transport, tmp_path):
    text = error_text(call(transport, "add_recording", {"path": str(tmp_path / "nope.wav")}))
    assert "not a file" in text and transport.requests == []


def test_transcribe_again(transport, conn, transcribed):
    job = payload(call(transport, "transcribe_again", {"media_id": transcribed, "tier": "max"}))
    assert job["media_id"] == transcribed and job["status"] == "queued"
    assert json.loads(transport.requests[-1][3]) == {"tier": "max"}


def test_ask_ai_without_a_provider_is_the_apis_conflict(transport, transcribed):
    text = error_text(call(transport, "ask_ai", {"media_id": transcribed, "kind": "summary"}))
    assert "409" in text and "no_provider" in text


def test_list_jobs_and_get_job(transport, conn):
    media_id = seed_media(conn)
    old = seed_job(conn, media_id, status="done")
    new = seed_job(conn, media_id, status="failed", error_code="BOOM", error_detail="synthetic")
    listed = payload(call(transport, "list_jobs", {"limit": 10}))
    assert [job["id"] for job in listed["jobs"]] == [new, old]
    one = payload(call(transport, "get_job", {"job_id": new}))
    assert one["error_code"] == "BOOM" and "events" in one


def test_cancel_job(transport, conn):
    job = seed_job(conn, seed_media(conn), status="queued")
    body = payload(call(transport, "cancel_job", {"job_id": job}))
    assert body["id"] == job
    with db.LOCK:
        row = conn.execute("SELECT status, cancel_requested FROM job WHERE id=?", (job,)).fetchone()
    assert row["status"] == "cancelled" or row["cancel_requested"]


def test_get_settings(transport):
    direct = transport.client.get("/api/v1/settings").json()
    assert payload(call(transport, "get_settings")) == direct


def test_every_tool_call_goes_to_api_v1(transport, transcribed, conn, tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(tiny_wav())
    job = seed_job(conn, transcribed, status="done")
    arguments = {
        "list_recordings": {}, "get_recording": {"media_id": transcribed},
        "search_transcripts": {"query": "towel"}, "get_transcript": {"media_id": transcribed},
        "export_transcript": {"media_id": transcribed}, "add_recording": {"path": str(clip)},
        "transcribe_again": {"media_id": transcribed},
        "ask_ai": {"media_id": transcribed, "kind": "summary"},
        "list_jobs": {}, "get_job": {"job_id": job}, "cancel_job": {"job_id": job},
        "get_settings": {},
    }
    assert set(arguments) == EXPECTED_TOOLS
    for name, args in arguments.items():
        call(transport, name, args)
    paths_ = [path for _, path, _, _ in transport.requests]
    assert len(paths_) >= len(arguments) and all(p.startswith("/api/v1/") for p in paths_), paths_


def test_nothing_listening_is_a_tool_error_that_says_so():
    from test_cli import free_port

    port = free_port()
    answers = exchange(cli.UrllibTransport(port, 5), INIT,
                       request(1, "tools/call", {"name": "list_jobs", "arguments": {}}))
    text = error_text(answers[1]["result"])
    assert "not answering" in text


# --- the real entry point --------------------------------------------------------------------


def test_the_real_entry_point_speaks_only_protocol_on_stdout(live, conn):
    """``python -m scribe.cli --port N mcp`` against uvicorn: every stdout
    line is a JSON-RPC message, the call reaches the API, EOF ends it.

    Also an API error through the real entry point: run as ``-m``, the CLI
    is ``__main__`` and the server imports ``scribe.cli`` - two copies of
    `Failed` - and on 2026-10-25's real run a 409 came back as a JSON-RPC
    internal error instead of a tool error."""
    seed_media(conn, title="Over stdio")
    lines = [INIT, INITIALIZED, request(1, "tools/list"),
             request(2, "tools/call", {"name": "list_recordings", "arguments": {}}),
             request(3, "tools/call", {"name": "get_recording", "arguments": {"media_id": 999}})]
    data = b"".join(json.dumps(m).encode() + b"\n" for m in lines)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run(
        [sys.executable, "-m", "scribe.cli", "--port", str(live), "mcp"],
        input=data, capture_output=True, env=env, timeout=120, cwd=str(ROOT),
    )
    assert done.returncode == 0, done.stderr
    out = done.stdout.split(b"\n")
    assert out[-1] == b"" and all(not line.endswith(b"\r") for line in out)
    answers = [json.loads(line) for line in out[:-1]]
    assert [a["id"] for a in answers] == [0, 1, 2, 3]
    assert "Over stdio" in answers[2]["result"]["content"][0]["text"]
    assert "not_found" in error_text(answers[3]["result"])
    assert b"Traceback" not in done.stderr


def test_the_documented_tools_are_the_tools():
    """docs/configuration.md's tool table and `TOOLS` cannot drift apart."""
    import re

    text = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    section = text.split("## Letting an AI agent use MyScribe (MCP)", 1)[1].split("\n## ", 1)[0]
    documented = set()
    for cell in re.findall(r"^\| (`[^|]+) \|", section, re.M):
        documented |= set(re.findall(r"`(\w+)`", cell))
    assert documented == EXPECTED_TOOLS == {tool.name for tool in mcp_server.TOOLS}
