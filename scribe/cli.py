"""``myscribe``: drive a running MyScribe from a terminal or a script.

    python -m scribe.cli status
    python -m scribe.cli upload talk.mp3 --option language=nl --idempotency-key talk-1
    python -m scribe.cli wait 12 && python -m scribe.cli export 7 --format srt,txt

A thin client on the JSON API (TASK-108 phase 1, ADR-025): every command is
one or a few requests to ``http://127.0.0.1:<port>/api/v1`` and nothing
else. It never opens the database or the library itself - the running app
owns those (ADR-013's one lock lives in that process) - so what this prints
is what the app answers, and an error is the API's ``code`` and ``detail``.

Two rules keep it small and safe to ship anywhere the app runs:

* **Standard library only.** argparse and urllib, so importing it costs
  nothing and it needs nothing the app's environment does not already have.
  tests/test_cli.py checks that importing it loads no third-party module.
* **Only the loopback, never a proxy.** The host is fixed at 127.0.0.1 (the
  app binds nothing else, `scribe.__main__.HOST`), and the opener has an
  empty `ProxyHandler`: urllib would otherwise send a loopback request to
  ``$HTTP_PROXY``, which cannot reach this machine's 127.0.0.1.

Each outcome a script may act on has its own exit code (`Exit`); the table
is in docs/configuration.md, and a test keeps the two equal.
"""

from __future__ import annotations

import argparse
import enum
import json
import mimetypes
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterator

HOST = "127.0.0.1"
# `scribe.__main__.DEFAULT_PORT`, not imported: that module pulls in uvicorn.
DEFAULT_PORT = 4242
PREFIX = "/api/v1"
CHUNK = 1024 * 1024
TERMINAL = ("done", "failed", "cancelled", "interrupted")


class Exit(enum.IntEnum):
    """One exit code per outcome. Documented in docs/configuration.md."""

    OK = 0
    SERVER_ERROR = 1  # a 5xx, or an answer that is not what the API promises
    USAGE = 2  # a wrong command line (argparse's own code), a file that is not there
    NOT_RUNNING = 3  # nothing answers on 127.0.0.1:<port>
    NOT_FOUND = 4  # 404: no such recording or job
    CONFLICT = 5  # 409: no transcript yet, an idempotency key in use or reused
    REFUSED = 6  # 400, 403, 413, 415, 422: the request itself was refused
    JOB_UNSUCCESSFUL = 7  # wait: the job ended failed, cancelled or interrupted
    TIMEOUT = 8  # wait: the job had not ended when --timeout ran out


# A transport takes (method, path, headers, body) and answers
# (status, lower-cased headers, body bytes). ``body`` is None, bytes, or an
# iterable of bytes chunks with a Content-Length header beside it.
Transport = Callable[[str, str, dict, Any], "tuple[int, dict, bytes]"]

_sleep = time.sleep
_now = time.monotonic


class NotRunning(Exception):
    """Nothing answered on the port."""


class Failed(Exception):
    """An outcome that ends the command with ``exit``; ``message`` goes to stderr."""

    def __init__(self, exit: Exit, message: str) -> None:
        super().__init__(message)
        self.exit = exit
        self.message = message


class UrllibTransport:
    """The real transport: urllib to 127.0.0.1, with no proxy."""

    def __init__(self, port: int, timeout: float) -> None:
        self.base = f"http://{HOST}:{port}"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, method: str, path: str, headers: dict, body: Any) -> tuple[int, dict, bytes]:
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as resp:
                return resp.status, _lower(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, _lower(exc.headers), exc.read()
        except (urllib.error.URLError, ConnectionError, TimeoutError, socket.timeout) as exc:
            reason = getattr(exc, "reason", exc)
            raise NotRunning(str(reason)) from None


def _lower(headers) -> dict:
    return {key.lower(): value for key, value in (headers or {}).items()}


# --- talking to the API ------------------------------------------------------------------------


_STATUS_EXIT = {404: Exit.NOT_FOUND, 409: Exit.CONFLICT}
_REFUSED = frozenset({400, 403, 405, 413, 415, 422})


class Api:
    def __init__(self, transport: Transport, port: int) -> None:
        self.transport = transport
        self.where = f"{HOST}:{port}"

    def request(self, method: str, path: str, *, query: dict | None = None, json_body: Any = None,
                body: Any = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
        """The answer to one request to /api/v1; `Failed` for any error status."""
        url = PREFIX + path
        given = {k: v for k, v in (query or {}).items() if v is not None}
        if given:
            url += "?" + urllib.parse.urlencode(given)
        headers = {"Accept": "application/json", **(headers or {})}
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            status, answer_headers, data = self.transport(method, url, headers, body)
        except NotRunning as exc:
            raise Failed(Exit.NOT_RUNNING, f"MyScribe is not answering on {self.where} ({exc}). "
                         "Is it running? Start it, or give its --port.") from None
        if status >= 400:
            raise _error(status, answer_headers, data)
        return status, answer_headers, data

    def json(self, method: str, path: str, **kwargs) -> tuple[Any, dict]:
        _, headers, data = self.request(method, path, **kwargs)
        try:
            return json.loads(data), headers
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise Failed(Exit.SERVER_ERROR, f"the answer to {method} {PREFIX}{path} is not JSON: "
                         f"{_preview(data)}") from None


def _error(status: int, headers: dict, data: bytes) -> Failed:
    """The API's ``code`` and ``detail``, or what there is: the guard's
    refusals come before any route, one as plain text, one without a code."""
    code, detail = None, None
    try:
        body = json.loads(data)
        if isinstance(body, dict):
            code, detail = body.get("code"), body.get("detail")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    if detail is None:
        detail = _preview(data) or "(no detail)"
    elif not isinstance(detail, str):
        detail = json.dumps(detail)
    exit_ = _STATUS_EXIT.get(status) or (Exit.REFUSED if status in _REFUSED else Exit.SERVER_ERROR)
    return Failed(exit_, f"error {status}{' ' + code if code else ''}: {detail}")


def _preview(data: bytes) -> str:
    return data[:300].decode("utf-8", "replace").strip()


# --- what a command line gives ---------------------------------------------------------------------


def _pairs(values: list[str] | None, parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    pairs = []
    for item in values or ():
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            parser.error(f"--option takes NAME=VALUE, not {item!r}")
        pairs.append((name.strip(), value))
    return pairs


def _value(text: str) -> Any:
    """``true``, ``3`` or ``null`` as JSON; anything else as the text itself."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    return value if isinstance(value, (bool, int, float)) or value is None else text


def _params(text: str | None, parser: argparse.ArgumentParser) -> dict | None:
    if text is None:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        parser.error(f"--params is not JSON: {exc}")
    if not isinstance(value, dict):
        parser.error("--params must be a JSON object")
    return value


def _key_header(key: str | None) -> dict:
    return {"Idempotency-Key": key} if key else {}


def _multipart(fields: list[tuple[str, str]], path: Path) -> tuple[str, int, Iterator[bytes]]:
    """A multipart body that reads ``path`` in chunks: (boundary, length, chunks)."""
    boundary = "myscribe-" + secrets.token_hex(16)
    head = b""
    for name, value in fields:
        head += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{_quote(name)}\"\r\n\r\n"
                 f"{value}\r\n").encode("utf-8")
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{_quote(path.name)}\"\r\nContent-Type: {kind}\r\n\r\n").encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("ascii")
    length = len(head) + path.stat().st_size + len(tail)

    def chunks() -> Iterator[bytes]:
        yield head
        with path.open("rb") as handle:
            while block := handle.read(CHUNK):
                yield block
        yield tail

    return boundary, length, chunks()


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', "%22").replace("\r", " ").replace("\n", " ")


def _filename(headers: dict) -> str | None:
    """The name in a Content-Disposition, its RFC 5987 form first; no path."""
    disposition = headers.get("content-disposition", "")
    name = None
    for part in disposition.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "filename*" and value.lower().startswith("utf-8''"):
            name = urllib.parse.unquote(value[7:])
            break
        if key.lower() == "filename" and name is None:
            name = value.strip().strip('"')
    if not name:
        return None
    name = Path(name.replace("\\", "/")).name
    return name if name not in ("", ".", "..") else None


# --- output ----------------------------------------------------------------------------------------


class Out:
    def __init__(self, stdout, stderr, as_json: bool) -> None:
        self.stdout, self.stderr, self.as_json = stdout, stderr, as_json

    def line(self, text: str = "") -> None:
        self.stdout.write(text + "\n")

    def note(self, text: str) -> None:
        self.stderr.write(text + "\n")

    def data(self, value: Any) -> None:
        # ASCII-escaped: lossless on any console code page.
        self.line(json.dumps(value, indent=2, ensure_ascii=True))

    def raw(self, data: bytes) -> None:
        """Bytes as they came: a ZIP or a UTF-8 JSON, no newline translation."""
        self.stdout.flush()
        target = getattr(self.stdout, "buffer", None)
        if target is None:  # a text-only stream: the best it can take
            self.stdout.write(data.decode("utf-8", "replace"))
            return
        target.write(data)
        target.flush()


def _clock(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return "-"
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _replayed(headers: dict) -> str:
    return " (replayed: the answer to an earlier request with this key)" \
        if headers.get("idempotent-replayed") == "true" else ""


# --- the commands ----------------------------------------------------------------------------------


def cmd_status(api: Api, args, out: Out) -> Exit:
    settings, _ = api.json("GET", "/settings")
    running, _ = api.json("GET", "/jobs", query={"status": "running", "limit": 1000})
    queued, _ = api.json("GET", "/jobs", query={"status": "queued", "limit": 1000})
    summary = {
        "url": f"http://{api.where}{PREFIX}",
        "running": len(running["jobs"]),
        "queued": len(queued["jobs"]),
        "settings": settings,
    }
    if out.as_json:
        out.data(summary)
        return Exit.OK
    out.line(f"MyScribe answers on http://{api.where}")
    out.line(f"jobs: {summary['running']} running, {summary['queued']} queued")
    ai = settings.get("ai") or {}
    if ai.get("provider"):
        where = "on this computer" if ai.get("local") else "online"
        out.line(f"AI: {ai['provider']} {ai.get('model') or ''} ({where})".replace("  ", " "))
    else:
        out.line("AI: no provider chosen")
    out.line(f"watch folders: {len(settings.get('watch_folders') or [])}")
    return Exit.OK


def cmd_list(api: Api, args, out: Out) -> Exit:
    query = {"view": args.view, "folder": args.folder, "sort": args.sort, "q": args.q, "label": args.label}
    body, _ = api.json("GET", "/media", query=query)
    if out.as_json:
        out.data(body)
        return Exit.OK
    out.line(f"{'id':>6}  {'duration':>8}  title")
    for row in body["media"]:
        out.line(f"{row['id']:>6}  {_clock(row.get('duration')):>8}  {row.get('title', '')}")
    return Exit.OK


def cmd_search(api: Api, args, out: Out) -> Exit:
    body, _ = api.json("GET", "/search", query={"q": args.query})
    if out.as_json:
        out.data(body)
        return Exit.OK
    for hit in body["hits"]:
        text = "".join(f"[{p['text']}]" if p["match"] else p["text"] for p in hit["parts"])
        out.line(f"{hit['media_id']:>6}  {_clock(hit['start']):>8}  {hit['title']}: {text.strip()}")
    if not body["hits"]:
        out.note("no matches")
    if body.get("uncorrected"):
        out.note("note: search reads the transcripts without the glossary's corrections")
    return Exit.OK


def cmd_upload(api: Api, args, out: Out) -> Exit:
    path = Path(args.file).expanduser()
    if not path.is_file():
        raise Failed(Exit.USAGE, f"{path} is not a file")
    options = _pairs(args.option, args.parser)
    params = _params(args.params, args.parser)
    headers = _key_header(args.idempotency_key)
    if args.by_path:
        body: dict = {"path": str(path.resolve()), **{k: _value(v) for k, v in options}}
        for name, value in (("title", args.title), ("folder_id", args.folder_id), ("params", params)):
            if value is not None:
                body[name] = value
        answer, answer_headers = api.json("POST", "/media", json_body=body, headers=headers)
    else:
        fields = [(k, v) for k, v in options]
        if args.title is not None:
            fields.append(("title", args.title))
        if args.folder_id is not None:
            fields.append(("folder_id", str(args.folder_id)))
        if params is not None:
            fields.append(("params", json.dumps(params)))
        boundary, length, chunks = _multipart(fields, path)
        headers.update({"Content-Type": f"multipart/form-data; boundary={boundary}",
                        "Content-Length": str(length)})
        answer, answer_headers = api.json("POST", "/media", body=chunks, headers=headers)
    if out.as_json:
        out.data(answer)
        return Exit.OK
    known = " (already in the library)" if answer.get("deduped") else ""
    out.line(f"media {answer['id']} \"{answer.get('title', '')}\"{known}: "
             f"job {answer['job_id']} queued{_replayed(answer_headers)}")
    return Exit.OK


def cmd_transcribe(api: Api, args, out: Out) -> Exit:
    body: dict = {k: _value(v) for k, v in _pairs(args.option, args.parser)}
    params = _params(args.params, args.parser)
    if params is not None:
        body["params"] = params
    job, headers = api.json("POST", f"/media/{args.media_id}/transcribe", json_body=body,
                            headers=_key_header(args.idempotency_key))
    if out.as_json:
        out.data(job)
        return Exit.OK
    out.line(f"job {job['id']} queued for media {job.get('media_id')}{_replayed(headers)}")
    return Exit.OK


def _job_line(job: dict) -> str:
    progress = job.get("stage_progress")
    shown = f"{progress:.0%}" if isinstance(progress, (int, float)) and job["status"] == "running" else ""
    media = job.get("media_id") if job.get("media_id") is not None else "-"
    return f"{job['id']:>6}  {job['status']:<11}  {job['type']:<10}  {media!s:>6}  " \
           f"{job.get('stage') or '':<12}  {shown}".rstrip()


def cmd_jobs(api: Api, args, out: Out) -> Exit:
    if args.job_id is not None:
        job, _ = api.json("GET", f"/jobs/{args.job_id}")
        if out.as_json:
            out.data(job)
            return Exit.OK
        for name in ("id", "type", "status", "media_id", "stage", "stage_progress",
                     "queue_position", "eta_seconds", "error_code", "error_detail"):
            if job.get(name) is not None:
                out.line(f"{name}: {job[name]}")
        for event in job.get("events") or []:
            out.line(f"  {event.get('seq')}  {event.get('kind')}  {json.dumps(event.get('payload'))}")
        return Exit.OK
    body, _ = api.json("GET", "/jobs", query={"status": args.status, "limit": args.limit})
    if out.as_json:
        out.data(body)
        return Exit.OK
    out.line(f"{'id':>6}  {'status':<11}  {'type':<10}  {'media':>6}  {'stage':<12}  progress")
    for job in body["jobs"]:
        out.line(_job_line(job))
    return Exit.OK


def cmd_cancel(api: Api, args, out: Out) -> Exit:
    job, _ = api.json("POST", f"/jobs/{args.job_id}/cancel")
    if out.as_json:
        out.data(job)
    else:
        out.line(f"job {job['id']}: {job['status']}"
                 + (" (cancel asked)" if job["status"] not in TERMINAL else ""))
    return Exit.OK


def cmd_wait(api: Api, args, out: Out) -> Exit:
    started = _now()
    stage = None
    while True:
        job, _ = api.json("GET", f"/jobs/{args.job_id}")
        if job["status"] in TERMINAL:
            break
        if (job["status"], job.get("stage")) != stage and not args.quiet:
            stage = (job["status"], job.get("stage"))
            out.note(f"job {job['id']}: {job['status']}{' ' + job['stage'] if job.get('stage') else ''}")
        if args.timeout is not None and _now() - started >= args.timeout:
            raise Failed(Exit.TIMEOUT, f"job {job['id']} is still {job['status']} after {args.timeout:g} s")
        _sleep(args.interval)
    if out.as_json:
        out.data(job)
    else:
        out.line(f"job {job['id']} {job['status']}")
    if job["status"] == "done":
        return Exit.OK
    if job.get("error_code") or job.get("error_detail"):
        out.note(f"{job.get('error_code') or 'error'}: {job.get('error_detail') or ''}".rstrip(": "))
    return Exit.JOB_UNSUCCESSFUL


def _save(data: bytes, target: str, headers: dict, fallback: str, out: Out) -> None:
    if target == "-":
        out.raw(data)
        return
    path = Path(target).expanduser()
    if path.is_dir():
        path = path / (_filename(headers) or fallback)
    path.write_bytes(data)
    out.line(f"saved {path}")


def cmd_transcript(api: Api, args, out: Out) -> Exit:
    query = {"include_confidence": "true"} if args.include_confidence else None
    _, headers, data = api.request("GET", f"/media/{args.media_id}/transcript", query=query)
    _save(data, args.out, headers, f"media-{args.media_id}.json", out)
    return Exit.OK


def cmd_export(api: Api, args, out: Out) -> Exit:
    body: dict = {}
    if args.format:
        body["formats"] = args.format
    body.update({k: _value(v) for k, v in _pairs(args.option, args.parser)})
    _, headers, data = api.request("POST", f"/media/{args.media_id}/export", json_body=body,
                                   headers={"Accept": "*/*"})
    _save(data, args.out, headers, f"media-{args.media_id}.export", out)
    return Exit.OK


# --- the parser --------------------------------------------------------------------------------


def _with_options(parser: argparse.ArgumentParser, what: str) -> None:
    parser.add_argument("--option", action="append", metavar="NAME=VALUE",
                        help=f"{what}; repeat for more. VALUE is read as JSON when it is a "
                             "number, true/false or null, else as text")


def _with_key(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--idempotency-key", metavar="KEY",
                        help="a retry with the same key gets the first answer and queues nothing")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myscribe",
        description="Drive a running MyScribe through its JSON API on 127.0.0.1.",
        epilog="Exit codes: " + ", ".join(f"{code.value} {code.name.lower().replace('_', ' ')}"
                                          for code in Exit) + ".",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"the app's port on {HOST} (default {DEFAULT_PORT})")
    parser.add_argument("--json", action="store_true", help="print the API's JSON instead of text")
    parser.add_argument("--http-timeout", type=float, default=60.0, metavar="SECONDS",
                        help="how long one request may wait for an answer (default 60)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("status", help="is it running, and how full is the queue")
    p.set_defaults(run=cmd_status)

    p = sub.add_parser("list", help="the library")
    for name, help_ in (("--view", "recent, uncategorized or trash"), ("--folder", "a folder id"),
                        ("--sort", "created_at, title or duration"), ("--q", "words in the title"),
                        ("--label", "a label")):
        p.add_argument(name, type=int if name == "--folder" else str, help=help_)
    p.set_defaults(run=cmd_list)

    p = sub.add_parser("search", help="search the transcripts")
    p.add_argument("query")
    p.set_defaults(run=cmd_search)

    p = sub.add_parser("upload", help="send a recording in and queue its transcription")
    p.add_argument("file")
    p.add_argument("--by-path", action="store_true",
                   help="send the file's path, not its bytes; the app reads it, and only "
                        "from under its browse roots")
    p.add_argument("--title")
    p.add_argument("--folder-id", type=int)
    p.add_argument("--params", metavar="JSON", help="a JSON object of stage keys")
    _with_options(p, "a transcription option (language, tier, diarize, num_speakers, "
                     "min_speakers, max_speakers, translate)")
    _with_key(p)
    p.set_defaults(run=cmd_upload)

    p = sub.add_parser("transcribe", help="transcribe a recording again")
    p.add_argument("media_id", type=int)
    p.add_argument("--params", metavar="JSON", help="a JSON object of stage keys")
    _with_options(p, "a transcription option; none repeats the last request")
    _with_key(p)
    p.set_defaults(run=cmd_transcribe)

    p = sub.add_parser("jobs", help="the jobs, newest first; with an id, that one job")
    p.add_argument("job_id", type=int, nargs="?")
    p.add_argument("--status", help="queued, running, done, failed, cancelled or interrupted")
    p.add_argument("--limit", type=int, help="at most this many (1-1000, default 200)")
    p.set_defaults(run=cmd_jobs)

    p = sub.add_parser("cancel", help="cancel a job")
    p.add_argument("job_id", type=int)
    p.set_defaults(run=cmd_cancel)

    p = sub.add_parser("wait", help="wait until a job ends; exit 0 only when it is done")
    p.add_argument("job_id", type=int)
    p.add_argument("--timeout", type=float, metavar="SECONDS", help="give up after this long (exit 8)")
    p.add_argument("--interval", type=float, default=2.0, metavar="SECONDS",
                   help="how often to ask (default 2)")
    p.add_argument("--quiet", action="store_true", help="no progress lines on stderr")
    p.set_defaults(run=cmd_wait)

    p = sub.add_parser("transcript", help="the transcript as JSON, word by word")
    p.add_argument("media_id", type=int)
    p.add_argument("--out", default="-", help="a file or folder; - (the default) is stdout")
    p.add_argument("--include-confidence", action="store_true")
    p.set_defaults(run=cmd_transcript)

    p = sub.add_parser("export", help="download an export (one format as itself, several as a ZIP)")
    p.add_argument("media_id", type=int)
    p.add_argument("--format", metavar="LIST", help="e.g. srt or srt,txt,docx (default srt)")
    _with_options(p, "an export option, e.g. speakers=false or timestamps=turn")
    p.add_argument("--out", default=".", help="a file or folder (default .); - is stdout")
    p.set_defaults(run=cmd_export)
    return parser


def _console(stream) -> None:
    """A console that cannot show a character shows '?', never a traceback."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None, *, transport: Transport | None = None,
         stdout=None, stderr=None) -> int:
    if stdout is None:
        stdout = sys.stdout
        _console(stdout)
    if stderr is None:
        stderr = sys.stderr
        _console(stderr)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        args.parser = parser
        out = Out(stdout, stderr, args.json)
        api = Api(transport or UrllibTransport(args.port, args.http_timeout), args.port)
        return int(args.run(api, args, out))
    except SystemExit as exc:  # argparse: --help is 0, a usage error 2
        return exc.code if isinstance(exc.code, int) else Exit.USAGE
    except Failed as exc:
        stderr.write(f"myscribe: {exc.message}\n")
        return int(exc.exit)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
