"""``myscribe mcp``: the /api/v1 actions as MCP tools, on stdin and stdout.

    python -m scribe.cli --port 4242 mcp

An AI agent (Claude Code, Claude Desktop, any MCP client) starts this as a
child process and talks JSON-RPC 2.0 to it, one message per line (the MCP
stdio transport). Each tool is one request to a running MyScribe's JSON
API through `scribe.cli`'s `Api` - the same client, the same loopback-only,
proxy-free transport, the same error messages. It never opens the database
or the library: the running app stays the only writer (ADR-013), and what
a tool answers is what the API answers (TASK-108 phase 2, ADR-025).

Hand-written on the standard library, not the official SDK (ADR-031): a
stdio server needs ``initialize``, ``ping``, ``tools/list`` and
``tools/call``, a few hundred lines; the SDK would put twelve packages,
four of them native, into every user's frozen environment. The price is
that a new protocol revision is ours to read and add (`PROTOCOL_VERSIONS`).

What the wire carries:

* **stdout is protocol only.** UTF-8 JSON, one message per line, ``\\n``
  (never ``\\r\\n``). `main` points ``sys.stdout`` at stderr for the
  session, so a stray print cannot break the stream.
* **A failed action is a tool result**, ``isError: true`` with the API's
  status, ``code`` and ``detail`` as text - the model reads it and can try
  again. So are arguments that do not fit the tool's schema. A JSON-RPC
  error is for the protocol only: an unknown tool or method, a line that
  is not JSON.
* One request at a time, in order; a long upload holds the next line until
  it is answered. Notifications (``notifications/initialized``,
  ``notifications/cancelled``) get no answer.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from scribe import __version__
from scribe.cli import Api, Exit, Failed, _filename, _multipart, write_file

# Handshake-era revisions (the ``initialize`` exchange), oldest to newest.
# A client asking one of these gets it; any other gets the newest, and the
# client decides whether it can speak that. Nothing here differs between
# them for a tools-only server: no batches (gone in 2025-06-18), no
# structured output, no tasks.
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

REPLAYED_META = {"myscribe/replayed": True}

INSTRUCTIONS = (
    "MyScribe transcribes recordings on this computer. add_recording sends a file in and "
    "queues its transcription; the answer's job_id can be followed with get_job until its "
    "status is done, then get_transcript or export_transcript read the result. Every tool "
    "talks to the MyScribe app running on 127.0.0.1; if it is not running, start it."
)

# Copies of the app's own lists: importing them would load fastapi and
# pydantic into a process that must start in milliseconds and stay stdlib.
# tests/test_mcp_server.py keeps each equal to its original.
VIEWS = ["recent", "uncategorized", "trash"]  # scribe.web.library.VIEWS
SORTS = ["created_at", "title", "duration"]  # scribe.web.library.SORTS
FORMATS = ["srt", "vtt", "txt", "csv", "json", "md", "docx", "html"]  # scribe.exports.FORMATS
AI_KINDS = ["summary", "action_items", "chapters", "minutes", "blog", "speakers", "labels",
            "cleanup", "custom"]  # scribe.llm.tasks.KINDS
TEXT_FORMATS = frozenset(FORMATS) - {"docx"}
JOB_STATUSES = ["queued", "running", "done", "failed", "cancelled", "interrupted"]

# The transcription dialog's fields (scribe.options.TranscribeOptions).
TRANSCRIPTION = {
    "language": {"type": "string", "description": "A Whisper language code such as en or nl; "
                 "leave out to detect it"},
    "tier": {"type": "string", "enum": ["turbo", "max"], "description": "turbo (default) or "
             "max, the slower and more accurate model"},
    "diarize": {"type": "boolean", "description": "Tell the speakers apart (default true)"},
    "num_speakers": {"type": "integer", "minimum": 1, "description": "The exact number of speakers, if known"},
    "min_speakers": {"type": "integer", "minimum": 1},
    "max_speakers": {"type": "integer", "minimum": 1},
    "translate": {"type": "boolean", "description": "Translate the speech to English"},
    "params": {"type": "object", "description": "Stage keys, for a caller that knows them; "
               "instead of the fields above"},
}
KEY = {"idempotency_key": {"type": "string", "description": "Retry-safe: the same key with the "
                           "same request answers the first job again and queues nothing. Kept 24 hours."}}
MEDIA_ID = {"media_id": {"type": "integer", "description": "The recording's id (list_recordings)"}}
JOB_ID = {"job_id": {"type": "integer", "description": "The job's id"}}


class ToolError(Exception):
    """A tool result with ``isError``: the text goes to the model."""


@dataclass
class Answer:
    value: Any
    replayed: bool = False


@dataclass
class Tool:
    name: str
    title: str
    description: str
    properties: dict
    run: Callable[[Api, dict], Answer]
    required: tuple[str, ...] = ()
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    schema: dict = field(init=False)

    def __post_init__(self) -> None:
        self.schema = {"type": "object", "properties": self.properties, "additionalProperties": False}
        if self.required:
            self.schema["required"] = list(self.required)

    def listing(self) -> dict:
        annotations = {"title": self.title, "readOnlyHint": self.read_only,
                       # The library is this machine's; nothing here reaches the internet
                       # except an AI question to an online provider the user chose.
                       "openWorldHint": self.name == "ask_ai"}
        if not self.read_only:
            annotations["destructiveHint"] = self.destructive
            annotations["idempotentHint"] = self.idempotent
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.schema, "annotations": annotations}


# --- what each tool does ------------------------------------------------------------------------


def _json(api: Api, method: str, path: str, **kwargs) -> Answer:
    value, headers = api.json(method, path, **kwargs)
    return Answer(value, headers.get("idempotent-replayed") == "true")


def _key_header(args: dict) -> dict:
    return {"Idempotency-Key": args["idempotency_key"]} if args.get("idempotency_key") else {}


def _options(args: dict) -> dict:
    return {name: args[name] for name in TRANSCRIPTION if name in args}


def list_recordings(api: Api, args: dict) -> Answer:
    return _json(api, "GET", "/media", query={k: args.get(k) for k in ("view", "folder", "sort", "q", "label")})


def get_recording(api: Api, args: dict) -> Answer:
    return _json(api, "GET", f"/media/{args['media_id']}")


def search_transcripts(api: Api, args: dict) -> Answer:
    return _json(api, "GET", "/search", query={"q": args["query"]})


def get_transcript(api: Api, args: dict) -> Answer:
    query = {"include_confidence": "true"} if args.get("include_confidence") else None
    return _json(api, "GET", f"/media/{args['media_id']}/transcript", query=query)


def export_transcript(api: Api, args: dict) -> Answer:
    options = dict(args.get("options") or {})
    if "formats" in options:
        raise ToolError("give the formats as the formats argument, not inside options")
    body = {"formats": args.get("formats") or ["srt"], **options}
    _, headers, data = api.request("POST", f"/media/{args['media_id']}/export", json_body=body,
                                   headers={"Accept": "*/*"})
    if args.get("save_to"):
        path, replaced = write_file(data, args["save_to"], headers, f"media-{args['media_id']}.export")
        return Answer({"saved": str(path), "bytes": len(data), "replaced": replaced})
    kind = headers.get("content-type", "")
    name = _filename(headers)
    # One format is the file itself, several a ZIP (exports_ui.download); every
    # format but docx is UTF-8 text, whatever its media type (srt is
    # application/x-subrip).
    if len(body["formats"]) == 1 and body["formats"][0] in TEXT_FORMATS:
        try:
            return Answer({"filename": name, "content_type": kind, "text": data.decode("utf-8")})
        except UnicodeDecodeError:
            pass
    raise ToolError(f"{name or 'this export'} ({kind or 'binary'}, {len(data)} bytes) is not text: "
                    "give save_to, a file or folder on this computer, and it is written there")


def add_recording(api: Api, args: dict) -> Answer:
    path = Path(args["path"]).expanduser()
    if not path.is_file():
        raise ToolError(f"{path} is not a file on this computer")
    options = _options(args)
    headers = _key_header(args)
    extra = {k: args[k] for k in ("title", "folder_id") if args.get(k) is not None}
    if args.get("by_path"):
        return _json(api, "POST", "/media", json_body={"path": str(path.resolve()), **extra, **options},
                     headers=headers)
    fields = [(name, _form(value)) for name, value in {**extra, **options}.items()]
    boundary, length, chunks = _multipart(fields, path)
    headers.update({"Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(length)})
    return _json(api, "POST", "/media", body=chunks, headers=headers)


def _form(value: Any) -> str:
    """A form field's text: what the dialog's own form sends."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def transcribe_again(api: Api, args: dict) -> Answer:
    return _json(api, "POST", f"/media/{args['media_id']}/transcribe", json_body=_options(args),
                 headers=_key_header(args))


def ask_ai(api: Api, args: dict) -> Answer:
    body = {k: args[k] for k in ("provider", "model", "prompt") if args.get(k)}
    return _json(api, "POST", f"/media/{args['media_id']}/ai/{args['kind']}", json_body=body)


def list_jobs(api: Api, args: dict) -> Answer:
    return _json(api, "GET", "/jobs", query={"status": args.get("status"), "limit": args.get("limit")})


def get_job(api: Api, args: dict) -> Answer:
    return _json(api, "GET", f"/jobs/{args['job_id']}")


def cancel_job(api: Api, args: dict) -> Answer:
    return _json(api, "POST", f"/jobs/{args['job_id']}/cancel")


def get_settings(api: Api, args: dict) -> Answer:
    return _json(api, "GET", "/settings")


TOOLS: tuple[Tool, ...] = (
    Tool("list_recordings", "List the library",
         "The recordings in the library, newest first unless sorted otherwise: id, title, "
         "duration (seconds) and when it was added.",
         {"view": {"type": "string", "enum": VIEWS, "description": "recent, uncategorized or trash"},
          "folder": {"type": "integer", "description": "Only this folder's recordings"},
          "sort": {"type": "string", "enum": SORTS},
          "q": {"type": "string", "description": "Words in the title"},
          "label": {"type": "string"}},
         list_recordings, read_only=True),
    Tool("get_recording", "One recording",
         "One recording: its fields, whether it has a transcript, and its latest job.",
         MEDIA_ID, get_recording, required=("media_id",), read_only=True),
    Tool("search_transcripts", "Search the transcripts",
         "Transcript segments that contain every word of the query, best first, with the "
         "recording, the start time in seconds and the matching words marked.",
         {"query": {"type": "string", "minLength": 1}}, search_transcripts,
         required=("query",), read_only=True),
    Tool("get_transcript", "Read a transcript",
         "The current transcript as MyScribe's JSON export: the speakers, every word with "
         "its start and end time and speaker, and Whisper's segments. Large for a long recording.",
         {**MEDIA_ID, "include_confidence": {"type": "boolean"}}, get_transcript,
         required=("media_id",), read_only=True),
    Tool("export_transcript", "Export a transcript",
         "A transcript as subtitles or a document. A text format (srt, vtt, txt, csv, json, md, "
         "html) comes back as text; docx, or several formats at once (a ZIP), needs save_to.",
         {**MEDIA_ID,
          "formats": {"type": "array", "items": {"type": "string", "enum": FORMATS}, "minItems": 1,
                      "description": "Default [\"srt\"]"},
          "options": {"type": "object", "description": "Export options as the export dialog has "
                      "them, e.g. {\"speakers\": false} or {\"timestamps\": \"turn\"}"},
          "save_to": {"type": "string", "description": "A file, or a folder to write it into under "
                      "its own name; an existing file is replaced"}},
         export_transcript, required=("media_id",), idempotent=True),
    Tool("add_recording", "Transcribe a file",
         "Send an audio or video file on this computer into the library and queue its "
         "transcription. Answers the recording and its job_id; follow the job with get_job. "
         "Known content is not stored twice (deduped), but is transcribed again.",
         {"path": {"type": "string", "description": "The file's path on this computer"},
          "by_path": {"type": "boolean", "description": "Let the app read the file where it is "
                      "instead of sending its bytes; only for files under the app's browse roots"},
          "title": {"type": "string"},
          "folder_id": {"type": "integer"},
          **TRANSCRIPTION, **KEY},
         add_recording, required=("path",)),
    Tool("transcribe_again", "Transcribe a recording again",
         "Queue a new transcription of a recording already in the library. Without options "
         "it repeats the last request; the old transcript stays until the new one is done.",
         {**MEDIA_ID, **TRANSCRIPTION, **KEY}, transcribe_again, required=("media_id",)),
    Tool("ask_ai", "Ask the AI about a recording",
         "Queue an AI job on a transcript (a summary, chapters, minutes, ...; custom needs a "
         "prompt) with the provider chosen in MyScribe's settings unless one is given. An "
         "online provider receives the transcript. Answers the job; the result is on the "
         "recording's page when the job is done.",
         {**MEDIA_ID, "kind": {"type": "string", "enum": AI_KINDS},
          "provider": {"type": "string"}, "model": {"type": "string"},
          "prompt": {"type": "string", "description": "The question, for kind custom"}},
         ask_ai, required=("media_id", "kind")),
    Tool("list_jobs", "List jobs",
         "Jobs, newest first: type, status, stage and progress; queued and running jobs carry "
         "their place in the queue and an estimate in seconds.",
         {"status": {"type": "string", "enum": JOB_STATUSES},
          "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Default 200"}},
         list_jobs, read_only=True),
    Tool("get_job", "One job",
         "One job and its last events; status done, failed, cancelled or interrupted means it "
         "has ended, and a failed job carries error_code and error_detail.",
         JOB_ID, get_job, required=("job_id",), read_only=True),
    Tool("cancel_job", "Cancel a job",
         "Cancel a queued job, or ask a running one to stop.",
         JOB_ID, cancel_job, required=("job_id",), destructive=True, idempotent=True),
    Tool("get_settings", "Read the settings",
         "What the next job gets: the transcription defaults, the browse roots, the watch "
         "folders and the AI provider. Never a key or token.",
         {}, get_settings, read_only=True),
)
BY_NAME = {tool.name: tool for tool in TOOLS}


# --- checking arguments against a schema ---------------------------------------------------------

_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def check(schema: dict, args: Any) -> list[str]:
    """What is wrong with ``args`` for this tool's schema: the subset of
    JSON Schema the tools use (type, required, enum, bounds, items)."""
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    props = schema["properties"]
    problems = [f"{name} is required" for name in schema.get("required", ()) if name not in args]
    problems += [f"{name} is not an argument of this tool; it takes {', '.join(props) or 'none'}"
                 for name in args if name not in props]
    for name, value in args.items():
        if name in props:
            problems += _check_value(name, props[name], value)
    return problems


def _check_value(name: str, spec: dict, value: Any) -> list[str]:
    kind = spec.get("type")
    if kind and not _TYPES[kind](value):
        return [f"{name} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}, not {json.dumps(value)}"]
    if "enum" in spec and value not in spec["enum"]:
        return [f"{name} must be one of {', '.join(map(str, spec['enum']))}, not {json.dumps(value)}"]
    if "minimum" in spec and value < spec["minimum"]:
        return [f"{name} must be at least {spec['minimum']}"]
    if "maximum" in spec and value > spec["maximum"]:
        return [f"{name} must be at most {spec['maximum']}"]
    if "minLength" in spec and len(value.strip()) < spec["minLength"]:
        return [f"{name} is empty"]
    if kind == "array":
        if len(value) < spec.get("minItems", 0):
            return [f"{name} needs at least {spec['minItems']} item(s)"]
        problems = []
        for index, item in enumerate(value):
            problems += _check_value(f"{name}[{index}]", spec.get("items", {}), item)
        return problems
    return []


# --- the session ---------------------------------------------------------------------------------


class ProtocolError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _text_result(text: str, *, error: bool = False) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return result


def call_tool(api: Api, params: Any) -> dict:
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise ProtocolError(INVALID_PARAMS, "tools/call needs a tool name")
    tool = BY_NAME.get(params["name"])
    if tool is None:
        raise ProtocolError(INVALID_PARAMS, f"Unknown tool: {params['name']}")
    args = params.get("arguments")
    args = {} if args is None else args
    problems = check(tool.schema, args)
    if problems:
        return _text_result(f"{tool.name}: " + "; ".join(problems), error=True)
    try:
        answer = tool.run(api, args)
    except (Failed, ToolError) as exc:
        return _text_result(getattr(exc, "message", None) or str(exc), error=True)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        # As the CLI: a 2xx answer without what the API promises.
        return _text_result("an answer from the app is not what the API promises "
                            f"({type(exc).__name__}: {exc})", error=True)
    result = _text_result(json.dumps(answer.value, indent=2, ensure_ascii=False))
    if answer.replayed:
        result["_meta"] = dict(REPLAYED_META)
    return result


def handle(api: Api | None, message: Any) -> dict | None:
    """The answer to one parsed message, or None when it wants none."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None if not isinstance(message, dict) else message.get("id"),
                      INVALID_REQUEST, "not a JSON-RPC 2.0 message (MCP has no batches since 2025-06-18)")
    if "method" not in message:
        if "id" in message and ("result" in message or "error" in message):
            return None  # a reply to a request: this server sends none
        return _error(message.get("id"), INVALID_REQUEST, "a request needs a method")
    method, id_ = message["method"], message.get("id")
    if "id" not in message:
        return None  # a notification
    try:
        return {"jsonrpc": "2.0", "id": id_, "result": _dispatch(api, method, message.get("params"))}
    except ProtocolError as exc:
        return _error(id_, exc.code, exc.message)


def _dispatch(api: Api | None, method: Any, params: Any) -> dict:
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "myscribe", "title": "MyScribe", "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [tool.listing() for tool in TOOLS]}
    if method == "tools/call":
        return call_tool(api, params)
    raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}")


def _error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def serve(api: Api | None, *, stdin, stdout, stderr) -> None:
    """Answer each line of ``stdin`` (bytes) on ``stdout`` (bytes) until EOF."""
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            answer: dict | None = _error(None, PARSE_ERROR, f"Parse error: {exc}")
        else:
            try:
                answer = handle(api, message)
            except Exception as exc:  # a bug here must not end the agent's session
                stderr.write(f"myscribe mcp: {type(exc).__name__}: {exc}\n")
                stderr.flush()
                id_ = message.get("id") if isinstance(message, dict) else None
                answer = _error(id_, INTERNAL_ERROR, f"internal error ({type(exc).__name__})")
        if answer is not None:
            stdout.write(json.dumps(answer, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


def main(api: Api, *, stdout=None, stderr=None) -> Exit:
    """The ``mcp`` command: serve on the process's own stdin and stdout."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    binary_out = getattr(stdout, "buffer", stdout)
    binary_in = getattr(sys.stdin, "buffer", sys.stdin)
    stderr.write(f"myscribe mcp {__version__}: tools for http://{api.where}/api/v1 on stdio\n")
    stderr.flush()
    saved, sys.stdout = sys.stdout, sys.stderr  # nothing but protocol on the real stdout
    try:
        serve(api, stdin=binary_in, stdout=binary_out, stderr=stderr)
    finally:
        sys.stdout = saved
    return Exit.OK
