"""Telling absent from stopped from running-without-a-chat-model from ready,
and an embedder from a model you can talk to (TASK-089.06).

Every request here goes through an `httpx2.MockTransport`, the house style of
tests/test_llm_ollama.py: the real client builds the real request and only the
socket is fake. What a mock transport cannot see is a proxy variable, because
it never opens a socket - so the assertion that `state()` still reaches
127.0.0.1 with a dead proxy configured lives in tests/test_proxy.py, against a
real server, and not here.

The shapes asserted below were read off the live daemon on this machine on
2026-09-21, read-only (`probe_tags.py` in the task's build directory, Ollama
0.34.2, 8 models). Three carry `completion` and five do not, and no name and no
other capability separates them: `qwen3-embedding:0.6b` reports `['tools',
'thinking', 'embedding']`. Why the test is the presence of `completion` rather
than the absence of `embedding` is written at `ollama.CHAT_CAPABILITY`; the
case that tells the two apart is in tests/test_llm_ollama.py, because on these
eight models they answer the same.

This machine is the awkward one and it is silenced deliberately. It has Ollama
on PATH, four `OLLAMA_*` names in HKCU and the same four in `os.environ`, so
without `no_ollama_anywhere` below nothing here could ever see *absent* - and
that would read as a bug in the detector rather than as a machine answering a
question a test asked.
"""

from __future__ import annotations

import json
import sys

import httpx2
import pytest

from scribe import credentials, ollama_setup
from scribe.llm import ollama

# --- a stand-in daemon ------------------------------------------------------------


EMBEDDERS = (
    ("bge-m3:latest", ["embedding"]),
    ("granite-embedding:278m", ["embedding"]),
    ("embeddinggemma:latest", ["embedding"]),
    ("qwen3-embedding:0.6b", ["tools", "thinking", "embedding"]),
    ("qwen3-embedding:4b", ["tools", "embedding"]),
)
"""The five embedders on this machine, with the capabilities they really
report. The fourth is the one that matters: `tools` and `thinking` as well, so
no capability but `completion` could be used the other way round."""

CHATTERS = (
    ("qwen3.5:4b", ["completion", "vision", "tools", "thinking"]),
    ("qwen3.5:9b", ["completion", "vision", "tools", "thinking"]),
    ("gemma4:12b", ["completion", "vision", "audio", "tools", "thinking"]),
)


def row(name, capabilities="unset"):
    """One `/api/tags` row. `capabilities="unset"` leaves the key out, which is
    what a daemon older than 0.34 sends and what the `/api/show` fallback is
    for."""
    made = {"name": name, "model": name, "size": 1, "details": {}}
    if capabilities != "unset":
        made["capabilities"] = capabilities
    return made


class Daemon:
    """An Ollama that answers `/api/tags`, `/api/version` and `/api/show`, and
    remembers what it was asked.

    `show` is consulted per model name; a name that is not in it gets a 404,
    which is how "neither endpoint will say" is built.
    """

    def __init__(self, *rows, show=None, version="0.34.2", answer=None):
        self.rows = list(rows)
        self.show = dict(show or {})
        self.version = version
        self.answer = answer  # a callable that pre-empts everything, for a refusal
        self.asked: list[tuple[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.asked.append((request.method, request.url.path))
        if self.answer is not None:
            return self.answer(request)
        if request.url.path == "/api/tags":
            return httpx2.Response(200, json={"models": self.rows})
        if request.url.path == "/api/version":
            return httpx2.Response(200, json={"version": self.version})
        if request.url.path == "/api/show":
            name = json.loads(request.content).get("model")
            if name not in self.show:
                return httpx2.Response(404, json={"error": f"model {name!r} not found"})
            return httpx2.Response(200, json={"capabilities": self.show[name]})
        return httpx2.Response(404, json={})

    @property
    def shown(self) -> list[str]:
        return [path for method, path in self.asked if path == "/api/show"]


@pytest.fixture(autouse=True)
def _the_shipped_detector(ollama_state_unstubbed):
    """tests/conftest.py stubs `ollama_setup.state` for every test in the suite,
    so that nothing is answered by the daemon running on this machine. This
    file is the one that is about `state()`, so it gets the real one back - the
    standing `library_db_unstubbed` has, for the same reason.

    Autouse here rather than named in nineteen signatures: every test in this
    file is about the detector, and one that forgot it would assert about a
    stand-in that answers `absent` to everything.
    """


@pytest.fixture
def serving(monkeypatch):
    """Point every client this module builds at a stand-in daemon.

    `ollama.default_client_factory` is the seam rather than a new argument on
    `state()`: it is the one the product really uses, and adding a transport
    parameter to a detector would be scaffolding shipped for a test's sake.
    """

    def serve(daemon: Daemon) -> Daemon:
        def factory(*, base_url, timeout):
            return httpx2.Client(
                base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(daemon)
            )

        monkeypatch.setattr(ollama, "default_client_factory", factory)
        return daemon

    return serve


@pytest.fixture
def no_ollama_anywhere(tmp_path, monkeypatch):
    """The three signals that are not the daemon, silenced - and a machine that
    really has none.

    PATH points at an empty folder, the known install locations are an empty
    folder, and the environment handed to `state()` is empty. The registry and
    `.env` are already answered by tests/conftest.py's stubs. Returns the
    keyword arguments a test passes on, so every absent test says the same
    thing the same way.
    """
    empty = tmp_path / "no-ollama-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return {"locations": (empty / "ollama.exe",), "environ": {"PATH": str(empty)}}


def refused(request):
    raise httpx2.ConnectError("connection refused", request=request)


# --- the four states --------------------------------------------------------------


def test_five_embedders_and_nothing_else_is_running_without_a_chat_model(serving, no_ollama_anywhere):
    """The state this task exists for. Before it, `available()` accepted any
    pulled name, so a machine holding only embedders showed green and the first
    summary failed inside a job."""
    serving(Daemon(*(row(name, caps) for name, caps in EMBEDDERS)))

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.state == ollama_setup.RUNNING_NO_CHAT_MODEL
    assert found.chat_models == ()
    assert found.present is True
    assert found.version == "0.34.2"
    assert "ollama pull" in ollama_setup.describe(found)


def test_the_three_chat_models_are_ready_and_no_embedder_is_among_them(serving, no_ollama_anywhere):
    """This machine's eight models, as the live daemon reports them."""
    serving(Daemon(*(row(name, caps) for name, caps in EMBEDDERS + CHATTERS)))

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.state == ollama_setup.READY
    assert found.chat_models == ("gemma4:12b", "qwen3.5:4b", "qwen3.5:9b")
    assert not [name for name, _caps in EMBEDDERS if name in found.chat_models]


def test_a_tags_row_without_capabilities_falls_back_to_api_show_per_model(serving, no_ollama_anywhere):
    """An Ollama older than the one here sends no `capabilities` on /api/tags.

    Per model, because `/api/show` takes one name: two rows without the key are
    two POSTs, and the row that has it is not asked about at all.
    """
    daemon = serving(
        Daemon(
            row("qwen3.5:4b"),                    # no capabilities key
            row("bge-m3:latest"),                 # no capabilities key
            row("gemma4:12b", ["completion"]),    # has it, must not be asked
            show={"qwen3.5:4b": ["completion", "tools"], "bge-m3:latest": ["embedding"]},
        )
    )

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.state == ollama_setup.READY
    assert found.chat_models == ("gemma4:12b", "qwen3.5:4b")
    assert daemon.shown == ["/api/show", "/api/show"], "one lookup per row that had no key, and no more"


def test_a_daemon_that_will_say_nothing_about_its_models_is_unknown(serving, no_ollama_anywhere):
    """Neither `/api/tags` nor `/api/show` answers the capability question.

    Never ready and never absent, and present wherever the state is used - the
    "cannot be read" row of ADR-017. The offer to install is gated on absent,
    so an unreadable daemon that fell to absent would put an installer over
    somebody's working Ollama.
    """
    serving(Daemon(row("qwen3.5:4b"), row("bge-m3:latest")))  # no key, and /api/show 404s

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.state == ollama_setup.UNKNOWN
    assert found.state not in (ollama_setup.READY, ollama_setup.ABSENT)
    assert found.present is True
    assert found.chat_models == ()


def test_a_refused_connection_with_nothing_else_on_the_machine_is_absent(serving, no_ollama_anywhere):
    """All four signals silent. The only state in which an install may be
    offered at all (ADR-017), which is why it takes all four."""
    serving(Daemon(answer=refused))

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.state == ollama_setup.ABSENT
    assert found.present is False
    assert found.binary == ""
    assert found.version == ""
    assert found.variables == ()
    assert ollama_setup.describe(found) == "not installed on this machine"


def test_a_daemon_with_no_models_at_all_is_running_without_a_chat_model(serving, no_ollama_anywhere):
    """An empty daemon has none, which is not the same as one that will not
    say: this is fixed by a pull and `unknown` is not."""
    serving(Daemon())

    assert ollama_setup.state(**no_ollama_anywhere).state == ollama_setup.RUNNING_NO_CHAT_MODEL


# --- any one signal turns absent into present -------------------------------------


def test_the_binary_on_the_path_alone_makes_it_present(serving, no_ollama_anywhere, tmp_path):
    """Somebody stopped their Ollama to free VRAM. Nothing is started; the
    state simply says so."""
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "ollama.exe").write_text("", encoding="utf-8")
    (binary / "ollama").write_text("", encoding="utf-8")
    # shutil.which wants the execute bit on macOS and Linux; Windows reads
    # the extension instead (TASK-089.24, the first CI run off Windows).
    (binary / "ollama").chmod(0o755)
    serving(Daemon(answer=refused))

    found = ollama_setup.state(locations=(), environ={"PATH": str(binary)})

    assert found.state == ollama_setup.INSTALLED_NOT_RUNNING
    assert found.present is True
    assert found.binary.startswith(str(binary))
    assert "not answering" in ollama_setup.describe(found)


def test_a_binary_at_a_known_install_location_alone_makes_it_present(serving, no_ollama_anywhere, tmp_path):
    """The location table's own test: nothing on PATH, nothing configured, and
    a file where Ollama's installer puts one."""
    where = tmp_path / "Programs" / "Ollama" / "ollama.exe"
    where.parent.mkdir(parents=True)
    where.write_text("", encoding="utf-8")
    serving(Daemon(answer=refused))

    found = ollama_setup.state(
        locations=(where,), environ=no_ollama_anywhere["environ"]
    )

    assert found.state == ollama_setup.INSTALLED_NOT_RUNNING
    assert found.binary == str(where)


def test_an_ollama_variable_alone_makes_it_present(serving, no_ollama_anywhere):
    """No binary anywhere, nothing answering, and one variable set. Present.

    This is the leg that makes the machine this was written on permanently
    present, and that is the right answer: somebody who set `OLLAMA_MODELS` has
    an Ollama, whatever a folder listing says today.
    """
    serving(Daemon(answer=refused))

    found = ollama_setup.state(
        locations=no_ollama_anywhere["locations"],
        environ={"PATH": no_ollama_anywhere["environ"]["PATH"], "OLLAMA_MODELS": "D:/models"},
    )

    assert found.state == ollama_setup.INSTALLED_NOT_RUNNING
    assert found.present is True
    assert [source.name for source in found.variables] == ["OLLAMA_MODELS"]


def test_a_daemon_that_answers_alone_makes_it_present(serving, no_ollama_anywhere):
    """No binary found and nothing configured, but something is serving on the
    loopback port. Present, and `ready` at that."""
    serving(Daemon(row("gemma4:12b", ["completion"])))

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.present is True
    assert found.state == ollama_setup.READY
    assert found.binary == ""


def test_a_blank_variable_configures_nothing_and_is_not_a_signal(serving, no_ollama_anywhere):
    """An exported name with nothing in it is not somebody's Ollama."""
    serving(Daemon(answer=refused))

    found = ollama_setup.state(
        locations=no_ollama_anywhere["locations"],
        environ={"PATH": no_ollama_anywhere["environ"]["PATH"], "OLLAMA_HOST": "   "},
    )

    assert found.state == ollama_setup.ABSENT
    assert found.variables == ()


# --- what the state carries, and what it must never carry -------------------------


def test_the_state_carries_variable_names_and_sources_and_no_value(serving, no_ollama_anywhere, monkeypatch, tmp_path):
    """A name and where it was found is a row that is safe in a print, a log
    line and a JSON body by construction - the manner `credentials.Found`
    keeps, and for the same reason. `OLLAMA_MODELS` is only a folder, but a
    structure that can carry a value carries one that matters eventually.
    """
    secret = "SENTINEL-never-printed"
    monkeypatch.setattr(
        credentials, "dotenv_values", lambda path=None: ({"OLLAMA_HOST": secret}, tmp_path / "x.env")
    )
    monkeypatch.setattr(credentials, "registry_names", lambda prefix: ((credentials.HKCU, "OLLAMA_KEEP_ALIVE"),))
    serving(Daemon(answer=refused))

    found = ollama_setup.state(
        locations=no_ollama_anywhere["locations"],
        environ={"PATH": no_ollama_anywhere["environ"]["PATH"], "OLLAMA_MODELS": secret},
    )

    assert [(source.kind, source.name) for source in found.variables] == [
        (credentials.ENVIRONMENT, "OLLAMA_MODELS"),
        (credentials.DOTENV, "OLLAMA_HOST"),
        (credentials.REGISTRY, "OLLAMA_KEEP_ALIVE"),
    ]
    surfaces = [repr(found), str(found), ollama_setup.describe(found)] + [
        str(source) for source in found.variables
    ]
    for surface in surfaces:
        assert secret not in surface, f"a variable's value reached an output: {surface}"
    assert "OLLAMA_KEEP_ALIVE (Windows registry, HKEY_CURRENT_USER)" in [
        str(source) for source in found.variables
    ]


def test_asking_writes_nothing_and_starts_nothing(serving, no_ollama_anywhere, tmp_path):
    """ADR-017's whole point: an Ollama that is there is left alone in every
    state. A detector that ran a binary or wrote a file would be the thing the
    record forbids, so this asserts the absence.
    """
    import os

    before_environ = dict(os.environ)
    before_files = sorted(path.name for path in tmp_path.iterdir())
    daemon = serving(Daemon(*(row(name, caps) for name, caps in CHATTERS)))

    ollama_setup.state(**no_ollama_anywhere)

    assert dict(os.environ) == before_environ
    assert sorted(path.name for path in tmp_path.iterdir()) == before_files
    # Only reads: nothing that changes what the daemon holds.
    assert {method for method, _path in daemon.asked} <= {"GET", "POST"}
    assert {path for _method, path in daemon.asked} <= {"/api/tags", "/api/version", "/api/show"}


def test_a_version_that_cannot_be_read_does_not_change_the_state(serving, no_ollama_anywhere):
    """`/api/tags` alone decides whether Ollama is answering; the version is
    decoration. Two endpoints deciding one question would put a daemon that
    answers one and not the other in two states at once."""

    def no_version(request):
        if request.url.path == "/api/version":
            return httpx2.Response(500, json={})
        return httpx2.Response(200, json={"models": [row("gemma4:12b", ["completion"])]})

    serving(Daemon(answer=no_version))

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.state == ollama_setup.READY
    assert found.version == ""


def test_a_daemon_that_answers_nonsense_is_present_and_never_ready(serving, no_ollama_anywhere):
    """A 500 from `/api/tags` is not a machine without Ollama: something is
    listening on that port and failing."""

    def broken(request):
        return httpx2.Response(500, text="whoops")

    serving(Daemon(answer=broken))

    found = ollama_setup.state(**no_ollama_anywhere)

    assert found.state == ollama_setup.UNKNOWN
    assert found.present is True


def test_every_state_has_a_sentence_of_its_own():
    """All five, built by hand - which is how a setup front-end will hold one
    (ADR-015: the engine carries the state, the front-end renders it).

    Two of these branches are otherwise only reached through `state()`, and a
    line that raised on a state nobody had printed yet would surface as a
    broken doctor on somebody else's machine.
    """
    said = {
        state: ollama_setup.describe(ollama_setup.State(state=state))
        for state in (
            ollama_setup.ABSENT,
            ollama_setup.INSTALLED_NOT_RUNNING,
            ollama_setup.RUNNING_NO_CHAT_MODEL,
            ollama_setup.READY,
            ollama_setup.UNKNOWN,
        )
    }

    assert len(set(said.values())) == 5, f"two states say the same thing: {said}"
    assert all(line and not line.endswith(" ") for line in said.values())
    assert "not installed" in said[ollama_setup.ABSENT]
    assert "not answering" in said[ollama_setup.INSTALLED_NOT_RUNNING]
    assert "ollama pull" in said[ollama_setup.RUNNING_NO_CHAT_MODEL]
    assert "will not say" in said[ollama_setup.UNKNOWN]


def test_a_stopped_ollama_says_where_it_was_found():
    """The binary when there is one, and otherwise the variable that gave it
    away - the machine this was written on would report the second."""
    with_binary = ollama_setup.State(
        state=ollama_setup.INSTALLED_NOT_RUNNING, binary=r"C:\Programs\Ollama\ollama.exe"
    )
    from_a_variable = ollama_setup.State(
        state=ollama_setup.INSTALLED_NOT_RUNNING,
        variables=(credentials.Source(credentials.REGISTRY, "OLLAMA_MODELS", credentials.HKCU),),
    )

    assert r"C:\Programs\Ollama\ollama.exe" in ollama_setup.describe(with_binary)
    assert "OLLAMA_MODELS" in ollama_setup.describe(from_a_variable)


# --- the location table -----------------------------------------------------------


def test_the_windows_location_is_the_one_ollamas_installer_uses():
    """`install.ps1:115-118` joins %LOCALAPPDATA% with `Programs\\Ollama`, and
    `shutil.which` resolved exactly that folder on this machine on 2026-09-21.
    Two sources, and they agree."""
    found = ollama_setup.install_locations({"LOCALAPPDATA": "C:/Users/someone/AppData/Local"})

    if sys.platform == "win32":
        assert [str(path).replace("\\", "/") for path in found] == [
            "C:/Users/someone/AppData/Local/Programs/Ollama/ollama.exe"
        ]
    else:
        assert all(str(path).endswith("ollama") for path in found)


def test_a_machine_that_will_not_say_where_it_keeps_things_has_no_locations():
    """No %LOCALAPPDATA% is no table, not a path built from an empty string -
    which would be a relative path, and `Path("").exists()` is a question about
    the working directory."""
    if sys.platform != "win32":
        pytest.skip("the empty-LOCALAPPDATA case is Windows only")

    assert ollama_setup.install_locations({}) == ()


# =====================================================================================
# The install offer (TASK-089.18, ADR-017): what is shown, what is checked, what
# runs, and what is left behind.
#
# Every seam below is a module attribute looked up at call time - `subprocess.Popen`,
# `ollama_setup.download_client`, `ollama_setup.verify_signer`,
# `ollama_setup.set_user_variable` - so a test that plants a raiser proves the
# absence of a download or a process for the whole call, not for one argument it
# remembered to pass. Nothing here opens a socket: the artifact server is an
# `httpx2.MockTransport` that honours Range, and the daemon is `Daemon` above.
# =====================================================================================

import hashlib
import shutil
import subprocess
import urllib.request
from pathlib import Path

from scribe import accel, paths

REAL_MEMORY = accel.memory
"""`accel.memory` as it ships, captured at import - before tests/conftest.py's
autouse stub replaces it - for the one test that is about the reader."""


class Refused(RuntimeError):
    """Raised by every seam a test plants to prove that nothing reached it."""


def _raiser(what):
    def raise_it(*args, **kwargs):
        raise Refused(f"{what} was reached: {args[:1]}")

    return raise_it


@pytest.fixture
def fenced(tmp_path, monkeypatch):
    """A data directory, a home and an account of this test's own.

    `marker_path()` reads `paths.DATA_DIR`, `install_plan` reads LOCALAPPDATA and
    the models folder reads USERPROFILE and MYSCRIBE_HOME - all of which point
    at this machine's real ones unless a test says otherwise. Returns the data
    directory.
    """
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    for name in ("MEDIA_DIR", "LOGS_DIR", "WORK_DIR", "MODELS_DIR"):
        monkeypatch.setattr(paths, name, data / name.removesuffix("_DIR").lower())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Users" / "someone"))
    monkeypatch.setenv("MYSCRIBE_HOME", str(tmp_path / "MyScribeHome"))
    for name in credentials.PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setattr(credentials, "system_proxies", lambda: {})
    return data


@pytest.fixture
def nothing_runs(monkeypatch):
    """Every way of running or fetching something, replaced by a raiser."""
    monkeypatch.setattr(subprocess, "Popen", _raiser("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "run", _raiser("subprocess.run"))
    monkeypatch.setattr(urllib.request, "urlopen", _raiser("urllib.request.urlopen"))
    monkeypatch.setattr(httpx2.Client, "send", _raiser("httpx2.Client.send"))
    monkeypatch.setattr(ollama_setup, "set_user_variable", _raiser("set_user_variable"))
    monkeypatch.setattr(ollama_setup, "unset_user_variable", _raiser("unset_user_variable"))


def environ_for(tmp_path) -> dict:
    return {
        "LOCALAPPDATA": str(tmp_path / "AppData" / "Local"),
        "USERPROFILE": str(tmp_path / "Users" / "someone"),
        "HOME": str(tmp_path / "Users" / "someone"),
        "PATH": str(tmp_path / "no-ollama-here"),
    }


class ArtifactServer:
    """A release server for one small artifact, honouring `Range`.

    `break_after` makes the first response die mid-stream after that many
    bytes, which is how an interrupted download is built; `ignore_range` is the
    server that answers 200 to a resume, which a client must treat as a fresh
    start; `status` overrides the answer altogether.
    """

    def __init__(self, body: bytes, *, break_after: int | None = None, ignore_range=False, status=None):
        self.body = body
        self.break_after = break_after
        self.ignore_range = ignore_range
        self.status = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx2.Response(self.status, content=b"nope")
        wanted = request.headers.get("Range")
        start = 0
        if wanted and not self.ignore_range:
            start = int(wanted.removeprefix("bytes=").rstrip("-"))
            if start >= len(self.body):
                return httpx2.Response(416)
        content = self.body[start:]
        if self.break_after is not None:
            cut = self.break_after
            self.break_after = None

            def stream():
                yield content[:cut]
                raise httpx2.ReadError("the connection dropped")

            return httpx2.Response(206 if start else 200, content=stream(),
                                   headers={"Content-Length": str(len(content))})
        return httpx2.Response(206 if start else 200, content=content,
                               headers={"Content-Length": str(len(content))})


def serve_artifact(monkeypatch, server: ArtifactServer) -> ArtifactServer:
    monkeypatch.setattr(
        ollama_setup, "download_client",
        lambda: httpx2.Client(transport=httpx2.MockTransport(server), follow_redirects=True),
    )
    return server


FAKE_ARTIFACT = bytes(range(256)) * 40  # 10,240 bytes, nothing like an installer
FAKE_SHA = hashlib.sha256(FAKE_ARTIFACT).hexdigest()


def a_plan(tmp_path, platform="win32", *, body=FAKE_ARTIFACT) -> dict:
    """The shipped plan, with the artifact figures swapped for the fake's so
    that a small body can pass the checks. Everything else - the URL, the
    argv, the folder - is the plan's own, built from the conftest's canned
    release (TASK-095; until then, from the pin)."""
    plan = ollama_setup.install_plan(platform, environ_for(tmp_path), into=tmp_path / "downloads")
    plan["bytes"] = len(body)
    plan["sha256"] = hashlib.sha256(body).hexdigest()
    # TASK-095: the second source, the release's own sha256sum.txt entry.
    plan["sha256sum"] = hashlib.sha256(body).hexdigest()
    return plan


class FakeProcess:
    def __init__(self, argv, *, exit_code=0, interrupts=0, on_wait=None):
        self.argv = argv
        self.exit_code = exit_code
        self.interrupts = interrupts
        self.on_wait = on_wait
        self.waits = 0
        self.returncode = None

    def wait(self, timeout=None):
        self.waits += 1
        if self.interrupts:
            self.interrupts -= 1
            raise KeyboardInterrupt
        if self.on_wait is not None:
            self.on_wait()
        self.returncode = self.exit_code
        return self.exit_code

    def poll(self):
        return self.returncode


def fake_popen(monkeypatch, *, exit_code=0, interrupts=0, on_wait=None) -> list[FakeProcess]:
    """`subprocess.Popen` replaced by a recorder. Returns the list it fills."""
    started: list[FakeProcess] = []

    def popen(argv, **kwargs):
        made = FakeProcess(list(argv), exit_code=exit_code, interrupts=interrupts, on_wait=on_wait)
        started.append(made)
        return made

    monkeypatch.setattr(subprocess, "Popen", popen)
    return started


def install_a_binary(tmp_path) -> Path:
    """What a finished Windows installer leaves at the known location - the
    Windows one, on whatever machine the test runs: on macOS the default was
    the runner's real /Applications (TASK-089.24)."""
    where = ollama_setup.install_locations(environ_for(tmp_path), "win32")[0]
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_bytes(b"MZ")
    return where


def signer_says(monkeypatch, ok: bool, why: str = "") -> list[Path]:
    checked: list[Path] = []

    def verify(path, *, run=None):
        checked.append(Path(path))
        return ok, why

    monkeypatch.setattr(ollama_setup, "verify_signer", verify)
    return checked


def variable_recorder(monkeypatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(ollama_setup, "set_user_variable", lambda name, value: calls.append(("set", name)))
    monkeypatch.setattr(ollama_setup, "unset_user_variable", lambda name: calls.append(("unset", name)))
    return calls


# --- what the offer shows, and where it comes from (#4; TASK-095) ------------------


def test_the_offer_file_names_no_forbidden_form():
    """scribe/ollama_offer.json (the pin's file until TASK-095): none of the
    forms ADR-017 forbids anywhere in it, and no vendor download URL."""
    text = ollama_setup.OFFER_PATH.read_text(encoding="utf-8")
    assert not any(f"ollama.com/download/{x}" in text for x in ("Ollama", "ollama")), "an unverified vendor URL"
    assert "| sh" not in text and "| iex" not in text


def test_the_shown_figures_are_the_fetched_release_s(tmp_path, canned_release):
    """What `install_plan` shows is read out of the release it was handed -
    here the conftest's canned one - and never typed twice. Before TASK-095
    this test compared the plan with the pin."""
    for platform in ("win32", "darwin", "linux"):
        plan = ollama_setup.install_plan(platform, environ_for(tmp_path), into=tmp_path / "dl")
        asset = canned_release["assets"][plan["name"]]
        assert (plan["url"], plan["bytes"], plan["sha256"], plan["sha256sum"], plan["tag"]) == (
            asset["url"], asset["bytes"], asset["digest"], canned_release["sums"][plan["name"]],
            canned_release["tag"])
        assert plan["vendor_page"] == "https://ollama.com/download"


def test_the_windows_plan_shows_the_exact_argv_the_folder_no_admin_and_self_update(tmp_path):
    """Criterion 3's list, as data: the command is the argv that will run, the
    folder is the one install.ps1:118 uses, and both sentences a person is owed
    are in the plan rather than in a front-end."""
    plan = ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")

    assert plan["runs_here"] is True
    assert plan["command"][0] == str(tmp_path / "dl" / "OllamaSetup.exe")
    assert plan["command"][1:] == ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
    assert plan["shown"] == [" ".join(plan["command"])]
    assert plan["install_dir"] == str(tmp_path / "AppData" / "Local" / "Programs" / "Ollama")
    assert plan["needs_admin"] is False
    assert plan["self_updates"] is True
    assert plan["signer"] == "O=Ollama Inc."


def test_the_mac_plan_opens_the_image_and_the_linux_plan_runs_nothing(tmp_path):
    """macOS: the one command MyScribe runs is `open` on the verified image
    (ADR-017, Exceptions). Linux: the commands are shown - download, check,
    read, run - and `command` is empty, because a root script that can install
    drivers is not run behind a yes or no (ADR-017, Must). No line pipes."""
    mac = ollama_setup.install_plan("darwin", environ_for(tmp_path), into=tmp_path / "dl")
    linux = ollama_setup.install_plan("linux", environ_for(tmp_path), into=tmp_path / "dl")

    assert mac["runs_here"] is True
    assert mac["command"] == ["open", str(tmp_path / "dl" / "Ollama.dmg")]

    assert linux["runs_here"] is False
    assert linux["command"] == []
    assert len(linux["shown"]) == 4
    assert linux["shown"][0].startswith("curl -fsSL -o ")
    assert linux["url"] in linux["shown"][0]
    assert linux["sha256"] in linux["shown"][1]
    assert linux["shown"][3].startswith("sh ")
    assert not any("|" in line for line in linux["shown"])
    assert linux["signer"] == ""


# --- the download (#4, #17) -------------------------------------------------------


def test_a_download_streams_to_a_part_file_and_lands_only_when_size_and_sum_match(tmp_path, monkeypatch):
    server = serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    seen: list[tuple[str, int, int]] = []

    landed = ollama_setup.download(
        "https://example.invalid/OllamaSetup.exe", tmp_path / "dl", "OllamaSetup.exe",
        expected_bytes=len(FAKE_ARTIFACT), expected_sha256=FAKE_SHA,
        on_progress=lambda name, done, total: seen.append((name, done, total)),
    )

    assert landed == tmp_path / "dl" / "OllamaSetup.exe"
    assert landed.read_bytes() == FAKE_ARTIFACT
    assert not (tmp_path / "dl" / "OllamaSetup.exe.part").exists()
    assert seen and seen[-1] == ("OllamaSetup.exe", len(FAKE_ARTIFACT), len(FAKE_ARTIFACT))
    assert "Range" not in server.requests[0].headers


def test_a_404_ends_on_the_vendor_page_and_leaves_nothing(tmp_path, monkeypatch):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT, status=404))

    with pytest.raises(ollama_setup.InstallError) as refused_:
        ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                              expected_bytes=1, expected_sha256="0" * 64)

    assert "https://ollama.com/download" in str(refused_.value)
    assert "Check again" in str(refused_.value)
    assert not (tmp_path / "dl").exists() or not list((tmp_path / "dl").iterdir())


def test_a_wrong_sum_ends_on_the_vendor_page_and_removes_the_part(tmp_path, monkeypatch):
    """A whole file with the wrong sha256 is not kept for a resume: resuming
    it would only reproduce the mismatch."""
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))

    with pytest.raises(ollama_setup.InstallError) as refused_:
        ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                              expected_bytes=len(FAKE_ARTIFACT), expected_sha256="0" * 64)

    assert "sha256" in str(refused_.value) and "https://ollama.com/download" in str(refused_.value)
    assert not (tmp_path / "dl" / "x.exe").exists()
    assert not (tmp_path / "dl" / "x.exe.part").exists()


def test_a_wrong_sum_names_both_digests_in_full(tmp_path, monkeypatch):
    """A digest that differs in its last hex digit must read differently: a
    twelve-character prefix printed the same figure twice."""
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    near = FAKE_SHA[:-1] + ("0" if FAKE_SHA[-1] != "0" else "1")

    with pytest.raises(ollama_setup.InstallError) as refused_:
        ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                              expected_bytes=len(FAKE_ARTIFACT), expected_sha256=near)

    assert FAKE_SHA in str(refused_.value) and near in str(refused_.value)


def test_a_short_file_is_a_mismatch_too(tmp_path, monkeypatch):
    """The server ends early with a 200 and the right prefix: the byte count
    catches what the sum would also catch, with a sentence that says which."""
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT[:100]))

    with pytest.raises(ollama_setup.InstallError) as refused_:
        ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                              expected_bytes=len(FAKE_ARTIFACT), expected_sha256=FAKE_SHA)

    assert f"{len(FAKE_ARTIFACT):,}" in str(refused_.value)


def test_an_interrupted_download_resumes_from_its_part_with_a_range_request(tmp_path, monkeypatch):
    """The first attempt dies after 3,000 bytes and raises; the second asks for
    `bytes=3000-`, gets a 206, and the sum is over the whole file."""
    server = serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT, break_after=3000))

    with pytest.raises(ollama_setup.InstallError) as first:
        ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                              expected_bytes=len(FAKE_ARTIFACT), expected_sha256=FAKE_SHA)
    part = tmp_path / "dl" / "x.exe.part"
    assert part.exists() and part.stat().st_size == 3000, "the part is kept for the resume"
    assert "https://ollama.com/download" in str(first.value)

    landed = ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                                   expected_bytes=len(FAKE_ARTIFACT), expected_sha256=FAKE_SHA)

    assert server.requests[1].headers["Range"] == "bytes=3000-"
    assert landed.read_bytes() == FAKE_ARTIFACT
    assert not part.exists()


def test_a_server_that_ignores_range_starts_the_file_over(tmp_path, monkeypatch):
    """A 200 to a Range request is the whole file again; appending it to the
    part would be a corrupt file with a plausible size."""
    part = tmp_path / "dl" / "x.exe.part"
    part.parent.mkdir()
    part.write_bytes(FAKE_ARTIFACT[:3000])
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT, ignore_range=True))

    landed = ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                                   expected_bytes=len(FAKE_ARTIFACT), expected_sha256=FAKE_SHA)

    assert landed.read_bytes() == FAKE_ARTIFACT


def test_a_file_that_already_landed_is_checked_and_not_fetched_again(tmp_path, monkeypatch):
    (tmp_path / "dl").mkdir()
    (tmp_path / "dl" / "x.exe").write_bytes(FAKE_ARTIFACT)
    server = serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))

    ollama_setup.download("https://example.invalid/x.exe", tmp_path / "dl", "x.exe",
                          expected_bytes=len(FAKE_ARTIFACT), expected_sha256=FAKE_SHA)

    assert server.requests == []


def test_ctrl_c_during_the_download_keeps_the_part_and_writes_no_marker(tmp_path, monkeypatch, fenced):
    """A cancelled download is `absent` again at the next sitting: no marker,
    and the part stays so that the same button finishes it (G6)."""

    def dies(request):
        def stream():
            yield FAKE_ARTIFACT[:1000]
            raise KeyboardInterrupt

        return httpx2.Response(200, content=stream(), headers={"Content-Length": str(len(FAKE_ARTIFACT))})

    monkeypatch.setattr(ollama_setup, "download_client",
                        lambda: httpx2.Client(transport=httpx2.MockTransport(dies)))

    with pytest.raises(KeyboardInterrupt):
        ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert (tmp_path / "downloads" / "OllamaSetup.exe.part").stat().st_size == 1000
    assert ollama_setup.read_marker() is None


def test_a_failed_download_names_a_configured_proxy_and_an_unconfigured_one_is_not_mentioned(
        tmp_path, monkeypatch, fenced):
    """Criterion 17: the same clause the two earlier downloads add
    (`credentials.proxy_note`), on the installer's download."""
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT, status=404))
    plain = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", environ=environ_for(tmp_path))
    assert "proxy" not in plain.sentence

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp:3128")
    behind = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert behind.ok is False
    assert "a proxy is configured at proxy.corp:3128" in behind.sentence
    assert behind.sentence.endswith(credentials.proxy_note())
    assert behind.sentence.removesuffix(credentials.proxy_note()) == plain.sentence


# --- the signer (#5) ----------------------------------------------------------------


def _powershell_answering(monkeypatch, status: str, subject: str, exit_code: int = 0) -> list[list[str]]:
    """`subprocess.run` replaced by the JSON PowerShell really prints. The shape
    was read on this machine on 2026-09-23 against notepad.exe:
    {"status":"Valid","subject":"CN=Microsoft Windows, O=Microsoft Corporation, ..."}."""
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, exit_code, stdout=json.dumps({"status": status, "subject": subject}), stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize("status,subject,accepted", [
    ("Valid", "CN=Ollama Inc., O=Ollama Inc., L=Palo Alto, S=California, C=US", True),
    # Measured 2026-09-23 with Get-AuthenticodeSignature on ollama.exe, ollama app.exe and
    # unins000.exe of the Ollama 0.34.2 installed on the reference machine: all three Valid.
    ("Valid", "CN=Ollama Inc., O=Ollama Inc., L=Toronto, S=Ontario, C=CA, SERIALNUMBER=2713355, "
              "OID.2.5.4.15=Private Organization, OID.1.3.6.1.4.1.311.60.2.1.2=Ontario, "
              "OID.1.3.6.1.4.1.311.60.2.1.3=CA", True),
    ("Valid", "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US", False),
    ("Valid", "CN=Not Ollama Inc., O=Not Ollama Inc., C=US", False),
    ("NotSigned", "", False),
    ("HashMismatch", "CN=Ollama Inc., O=Ollama Inc., C=US", False),
])
def test_the_signer_must_be_valid_and_ollama_inc_anchored(monkeypatch, tmp_path, status, subject, accepted):
    """install.ps1:84-87's rule: Status Valid and a subject carrying
    `O=Ollama Inc.` between commas, so that `O=Not Ollama Inc.` does not pass.
    The certificate on the pinned installer is unread (nobody downloaded it);
    a mismatch fails safe, which is the side the check is built for."""
    calls = _powershell_answering(monkeypatch, status, subject)

    ok, why = ollama_setup.verify_signer(tmp_path / "OllamaSetup.exe")

    assert ok is accepted, why
    assert calls and calls[0][0].lower().startswith("powershell")
    assert "Get-AuthenticodeSignature" in " ".join(calls[0])
    if not accepted:
        assert why


def test_a_powershell_that_prints_nonsense_or_fails_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="not json", stderr=""))
    assert ollama_setup.verify_signer(tmp_path / "x.exe")[0] is False

    monkeypatch.setattr(subprocess, "run", _raiser("powershell"))
    ok, why = ollama_setup.verify_signer(tmp_path / "x.exe")
    assert ok is False and "powershell" in why.lower()


def test_windows_refuses_an_installer_whose_signer_is_not_ollama_and_runs_nothing(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    signer_says(monkeypatch, False, "signed by O=Somebody Else")
    started = fake_popen(monkeypatch)
    variables = variable_recorder(monkeypatch)

    outcome = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads",
                                   models_dir=tmp_path / "models", environ=environ_for(tmp_path))

    assert outcome.ok is False and outcome.installed is False
    assert "O=Somebody Else" in outcome.sentence and "https://ollama.com/download" in outcome.sentence
    assert started == [], "the installer never ran"
    assert variables == [], "OLLAMA_MODELS was never written"
    assert ollama_setup.read_marker() is None


# --- the executed command is the shown command (#3) ---------------------------------


def test_the_executed_command_equals_the_shown_command(tmp_path, monkeypatch, fenced):
    """The argv `subprocess.Popen` receives is `plan["command"]`, list for
    list, and it runs only after the sum and the signer passed."""
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    checked = signer_says(monkeypatch, True)
    started = fake_popen(monkeypatch, on_wait=lambda: install_a_binary(tmp_path))
    plan = a_plan(tmp_path)

    outcome = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert outcome.ok is True
    assert [p.argv for p in started] == [plan["command"]]
    assert checked == [Path(plan["command"][0])], "the signer was asked about the very file that ran"


def test_the_shown_command_is_the_executed_one_even_with_a_space_in_the_path(tmp_path):
    """Criterion 3 compares what the person reads with what runs. A data
    directory under a folder named "Jan de Vries" put a bare space in the shown
    line, which then read as a different command. On Windows Popen turns a list
    into its command line with subprocess.list2cmdline, so that is the line to
    show; on macOS the line is shell words that split back into the argv."""
    import shlex
    import subprocess

    into = tmp_path / "Jan de Vries" / "downloads"
    windows = ollama_setup.install_plan("win32", {"LOCALAPPDATA": str(tmp_path)}, into=into)
    mac = ollama_setup.install_plan("darwin", {"HOME": str(tmp_path)}, into=into)

    assert windows["shown"] == [subprocess.list2cmdline(windows["command"])]
    assert mac["shown"] == [shlex.join(mac["command"])]
    assert shlex.split(mac["shown"][0]) == mac["command"]


def test_the_mac_path_opens_the_verified_image_writes_no_marker_and_waits(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    started = fake_popen(monkeypatch)
    plan = a_plan(tmp_path, "darwin")

    outcome = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert [p.argv for p in started] == [["open", str(tmp_path / "downloads" / "Ollama.dmg")]]
    assert outcome.installed is False and ollama_setup.read_marker() is None
    assert "Check again" in outcome.sentence


def test_the_linux_path_shows_the_commands_and_runs_and_fetches_nothing(tmp_path, monkeypatch, fenced, nothing_runs):
    plan = a_plan(tmp_path, "linux")

    outcome = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert outcome.ok is False and outcome.installed is False
    for line in plan["shown"]:
        assert line in outcome.sentence
    assert "Check again" in outcome.sentence
    assert ollama_setup.read_marker() is None


# --- the marker (#6, #7) ----------------------------------------------------------------


def test_the_marker_is_written_only_after_exit_zero_and_a_binary_at_the_known_path(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    signer_says(monkeypatch, True)
    plan = a_plan(tmp_path)

    fake_popen(monkeypatch, exit_code=1)
    failed = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))
    assert failed.ok is False and ollama_setup.read_marker() is None
    assert "exit code 1" in failed.sentence

    fake_popen(monkeypatch, exit_code=0)
    no_binary = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))
    assert no_binary.ok is False and ollama_setup.read_marker() is None
    assert "no ollama" in no_binary.sentence.lower()

    fake_popen(monkeypatch, exit_code=0, on_wait=lambda: install_a_binary(tmp_path))
    done = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path), model="qwen3.5:4b")

    assert done.ok is True and done.installed is True
    marker = ollama_setup.read_marker()
    assert marker is not None
    assert set(ollama_setup.MARKER_FIELDS) <= set(marker)
    assert marker["tag"] == plan["tag"] and marker["version"] == plan["tag"].lstrip("v")
    assert marker["binary"] == str(install_a_binary(tmp_path))
    assert marker["model"] == "qwen3.5:4b"
    assert not (tmp_path / "downloads" / "OllamaSetup.exe").exists(), "1.57 GB of installer is not kept after it ran"


def test_a_failed_download_leaves_no_marker(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT, status=404))
    started = fake_popen(monkeypatch)

    outcome = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert outcome.ok is False and ollama_setup.read_marker() is None and started == []


def a_marker(tmp_path, **changes) -> dict:
    """A marker as `install` writes one, bound to the binary the fake installer
    leaves, with any field overridden."""
    binary = install_a_binary(tmp_path)
    marker = {"tag": "v0.34.3", "version": "0.34.3", "binary": str(binary), "model": "qwen3.5:4b",
              "installed": 1789930000.0, "models_dir": ""}
    marker.update(changes)
    return marker


def stopped(binary: Path) -> ollama_setup.State:
    return ollama_setup.State(state=ollama_setup.INSTALLED_NOT_RUNNING, binary=str(binary))


def running(binary: Path, version="0.34.3") -> ollama_setup.State:
    return ollama_setup.State(state=ollama_setup.RUNNING_NO_CHAT_MODEL, binary=str(binary), version=version)


def test_a_marker_stands_for_the_same_version_at_the_same_path(tmp_path, fenced):
    marker = a_marker(tmp_path)
    binary = Path(marker["binary"])

    assert ollama_setup.valid_marker(running(binary), marker) is True
    assert ollama_setup.valid_marker(stopped(binary), marker) is True, "stopped: the version is unread, not wrong"


def test_the_marker_lapses_when_the_version_differs(tmp_path, fenced):
    """Ollama updated itself. The honest reason for a lapse, and the one the
    binding is chosen to fail towards (G7)."""
    marker = a_marker(tmp_path)

    assert ollama_setup.valid_marker(running(Path(marker["binary"]), version="0.34.4"), marker) is False


def test_the_marker_lapses_when_the_path_differs(tmp_path, fenced):
    marker = a_marker(tmp_path)
    elsewhere = tmp_path / "elsewhere" / "ollama.exe"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"MZ")

    assert ollama_setup.valid_marker(running(elsewhere), marker) is False


def test_the_marker_lapses_when_no_binary_stands_at_the_recorded_path(tmp_path, fenced):
    marker = a_marker(tmp_path)
    Path(marker["binary"]).unlink()

    assert ollama_setup.valid_marker(running(Path(marker["binary"])), marker) is False


def test_the_marker_lapses_when_it_cannot_be_read_or_lacks_a_field(tmp_path, fenced):
    ollama_setup.marker_path().write_text("{not json", encoding="utf-8")
    assert ollama_setup.read_marker() is None

    ollama_setup.marker_path().write_text("[1, 2]", encoding="utf-8")
    assert ollama_setup.read_marker() is None

    marker = a_marker(tmp_path)
    binary = Path(marker["binary"])
    for field in ollama_setup.MARKER_FIELDS:
        short = {k: v for k, v in marker.items() if k != field}
        assert ollama_setup.valid_marker(running(binary), short) is False, f"a marker without {field} stood"
    assert ollama_setup.valid_marker(running(binary), None) is False


def test_the_marker_lapses_once_ollama_has_been_seen_ready_or_is_absent(tmp_path, fenced):
    marker = a_marker(tmp_path)
    binary = Path(marker["binary"])

    ready = ollama_setup.State(state=ollama_setup.READY, binary=str(binary), version="0.34.3", chat_models=("qwen3.5:4b",))
    assert ollama_setup.valid_marker(ready, marker) is False
    assert ollama_setup.valid_marker(ollama_setup.State(state=ollama_setup.ABSENT), marker) is False


def test_a_replaced_ollama_lapses_the_marker(tmp_path, fenced):
    """The user removes what MyScribe installed and installs their own - another
    version, or another path - and never pulls a chat model. That Ollama is
    theirs: the marker lapses and R2's copyable command is what they get."""
    marker = a_marker(tmp_path)
    binary = Path(marker["binary"])

    assert ollama_setup.valid_marker(running(binary, version="0.35.0"), marker) is False
    theirs = tmp_path / "their-ollama" / "ollama.exe"
    theirs.parent.mkdir()
    theirs.write_bytes(b"MZ")
    assert ollama_setup.valid_marker(running(theirs, version="0.34.3"), marker) is False


def test_the_same_version_installed_again_at_the_same_path_is_what_the_binding_cannot_tell(tmp_path, fenced):
    """Said in the notes and pinned here: version and path together cannot tell
    a reinstall of the same version at the same path from MyScribe's own. That
    case gets the one question whose default is No (ADR-017, Open Questions)."""
    marker = a_marker(tmp_path)

    assert ollama_setup.valid_marker(running(Path(marker["binary"])), marker) is True


# --- OLLAMA_MODELS and the folders (#10) ----------------------------------------------


def test_the_default_models_folder_per_platform_is_the_faq_s(tmp_path):
    """docs.ollama.com/faq, read 2026-09-23: `C:\\Users\\%username%\\.ollama\\models`,
    `~/.ollama/models`, `/usr/share/ollama/.ollama/models`."""
    environ = environ_for(tmp_path)

    assert ollama_setup.default_models_dir("win32", environ) == Path(environ["USERPROFILE"]) / ".ollama" / "models"
    assert ollama_setup.default_models_dir("darwin", environ) == Path(environ["HOME"]) / ".ollama" / "models"
    assert ollama_setup.default_models_dir("linux", environ) == Path("/usr/share/ollama/.ollama/models")


def test_the_folder_with_myscribe_is_beside_the_library_and_never_inside_a_git_tree(tmp_path):
    """Row 8a: outside the git working tree in a clone (`git clean -fdx`) and
    nothing an uninstall removes - the library folder survives both."""
    library = tmp_path / "home" / "data"
    assert ollama_setup.models_dir_with_myscribe(environ_for(tmp_path), data_dir=library, platform="win32") == library / "ollama-models"

    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    in_tree = clone / "data"
    environ = dict(environ_for(tmp_path), MYSCRIBE_HOME=str(tmp_path / "MyScribeHome"))
    assert ollama_setup.models_dir_with_myscribe(environ, data_dir=in_tree, platform="win32") == tmp_path / "MyScribeHome" / "ollama-models"
    environ.pop("MYSCRIBE_HOME")
    assert ollama_setup.models_dir_with_myscribe(environ, data_dir=in_tree, platform="win32") == Path(environ["LOCALAPPDATA"]) / "MyScribe" / "ollama-models"
    assert ollama_setup.models_dir_with_myscribe(environ, data_dir=in_tree, platform="linux") == Path(environ["HOME"]) / ".local" / "share" / "MyScribe" / "ollama-models"


def test_room_is_measured_on_the_nearest_folder_that_exists(tmp_path, monkeypatch):
    asked: list[Path] = []

    def usage(path):
        asked.append(Path(path))
        return shutil._ntuple_diskusage(total=10 * 2**30, used=8 * 2**30, free=2 * 2**30)

    monkeypatch.setattr(shutil, "disk_usage", usage)

    found = ollama_setup.room(tmp_path / "not" / "there" / "yet", needed=3 * 2**30)

    assert asked == [tmp_path]
    assert found["enough"] is False and found["free"] == 2 * 2**30 and found["needed"] == 3 * 2**30


def test_on_windows_the_variable_is_set_after_the_checks_and_before_the_installer_and_removed_on_failure(
        tmp_path, monkeypatch, fenced):
    """Row 8a's order: sum, signer, then OLLAMA_MODELS, then the installer.
    A failed download or a refused signer writes nothing; an installer that
    then fails takes the variable with it, so `state()` reads absent again."""
    order: list[str] = []
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    monkeypatch.setattr(ollama_setup, "verify_signer", lambda path, run=None: (order.append("signer"), (True, ""))[1])
    monkeypatch.setattr(ollama_setup, "set_user_variable", lambda name, value: order.append(f"set {name}"))
    monkeypatch.setattr(ollama_setup, "unset_user_variable", lambda name: order.append(f"unset {name}"))
    environ = environ_for(tmp_path)

    def popen(argv, **kwargs):
        order.append("installer")
        assert environ.get("OLLAMA_MODELS") == str(tmp_path / "models"), "the child chain inherits it"
        return FakeProcess(list(argv), exit_code=3)

    monkeypatch.setattr(subprocess, "Popen", popen)

    outcome = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", models_dir=tmp_path / "models", environ=environ)

    assert outcome.ok is False
    assert order == ["signer", "set OLLAMA_MODELS", "installer", "unset OLLAMA_MODELS"]
    assert "OLLAMA_MODELS" not in environ
    assert ollama_setup.read_marker() is None


def test_on_windows_the_variable_stays_with_a_finished_install_and_is_named_in_the_sentence(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    signer_says(monkeypatch, True)
    variables = variable_recorder(monkeypatch)
    fake_popen(monkeypatch, on_wait=lambda: install_a_binary(tmp_path))

    outcome = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", models_dir=tmp_path / "models",
                                   environ=environ_for(tmp_path))

    assert outcome.installed is True and variables == [("set", "OLLAMA_MODELS")]
    assert outcome.variable == "OLLAMA_MODELS"
    assert str(tmp_path / "models") in outcome.sentence
    assert ollama_setup.read_marker()["models_dir"] == str(tmp_path / "models")


def test_elsewhere_the_variable_is_a_sentence_with_the_command_and_is_never_written(tmp_path, fenced, nothing_runs):
    """macOS and Linux: a systemd unit wants root and a launch agent is a
    second mechanism nobody has run (G8), so it stays a sentence."""
    for platform in ("darwin", "linux"):
        sentence = ollama_setup.models_dir_sentence(platform, tmp_path / "models")
        assert "OLLAMA_MODELS" in sentence and str(tmp_path / "models") in sentence
        assert "launchctl setenv OLLAMA_MODELS" in sentence or "Environment=OLLAMA_MODELS=" in sentence


def test_no_variable_is_written_without_a_models_folder(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    signer_says(monkeypatch, True)
    variables = variable_recorder(monkeypatch)
    fake_popen(monkeypatch, on_wait=lambda: install_a_binary(tmp_path))

    outcome = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", models_dir=None, environ=environ_for(tmp_path))

    assert outcome.installed is True and variables == [] and outcome.variable == ""


def test_where_the_models_landed_is_checked_and_never_assumed(tmp_path):
    """Whether the daemon inherits the variable is unmeasured (criterion 13),
    so a sentence about where the model went is earned by a folder with blobs
    in it."""
    folder = tmp_path / "models"
    assert ollama_setup.models_landed(folder) is False
    (folder / "blobs").mkdir(parents=True)
    assert ollama_setup.models_landed(folder) is False
    (folder / "blobs" / "sha256-abc").write_bytes(b"x")
    assert ollama_setup.models_landed(folder) is True


# --- the installer process and Ctrl-C (#14) -------------------------------------------


def test_ctrl_c_while_the_installer_runs_waits_for_it_and_says_so(monkeypatch):
    """The engine's half of criterion 14: a KeyboardInterrupt during the wait
    prints one line and waits again; the installer is never interrupted. The
    event line brackets the run for the launcher's half."""
    said: list[str] = []
    events: list[bool] = []
    started = fake_popen(monkeypatch, exit_code=0, interrupts=1)

    code = ollama_setup.run_installer(["x.exe", "/VERYSILENT"], on_line=said.append, on_event=events.append)

    assert code == 0
    assert started[0].waits == 2, "waited again after the interrupt"
    assert events == [True, False]
    assert len(said) == 1 and "installer" in said[0].lower() and "wait" in said[0].lower()


def test_the_event_line_is_printed_false_even_when_the_installer_fails(monkeypatch):
    events: list[bool] = []
    fake_popen(monkeypatch, exit_code=7)

    assert ollama_setup.run_installer(["x.exe"], on_event=events.append) == 7
    assert events == [True, False]


# --- the version poll (#8) ----------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_the_version_is_polled_and_a_late_answer_is_recorded(serving):
    answers = iter([refused, refused, None])

    def late(request):
        turn = next(answers)
        if turn is not None:
            return turn(request)
        return httpx2.Response(200, json={"version": "0.34.3"})

    serving(Daemon(answer=late))
    clock = Clock()
    provider = ollama.OllamaProvider()

    found = ollama_setup.wait_for_version(provider, deadline=120.0, clock=clock, sleep=clock.sleep)

    assert found == "0.34.3"
    assert len(clock.slept) == 2, "slept between the two refusals and the answer"


def test_a_daemon_that_never_answers_reads_installed_not_answering_yet_not_failure(serving, tmp_path, fenced):
    serving(Daemon(answer=refused))
    clock = Clock()
    provider = ollama.OllamaProvider()
    marker = a_marker(tmp_path)
    ollama_setup.write_marker(marker)

    found = ollama_setup.wait_for_version(provider, deadline=120.0, clock=clock, sleep=clock.sleep)

    assert found == ""
    assert clock.now >= 120.0
    sentence = ollama_setup.not_answering_yet_sentence()
    assert "not answering yet" in sentence and "Check again" in sentence
    assert "fail" not in sentence.lower()
    assert ollama_setup.read_marker() == marker, "the marker stands: the pull is offered by hand later"


# --- the pull (#11, #17) --------------------------------------------------------------


def pulling(monkeypatch, *lines, status=200):
    """A daemon whose POST /api/pull streams these NDJSON lines."""
    asked: list[tuple[str, str, bytes]] = []

    def handler(request):
        asked.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/pull":
            body = "".join(json.dumps(line) + "\n" for line in lines).encode()
            return httpx2.Response(status, content=body)
        return httpx2.Response(404, json={})

    def factory(*, base_url, timeout):
        return httpx2.Client(base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(handler))

    monkeypatch.setattr(ollama, "default_client_factory", factory)
    return asked


def test_a_pull_with_an_error_line_after_200_is_reported_failed_with_ollama_s_words(monkeypatch):
    asked = pulling(monkeypatch, {"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"})

    with pytest.raises(ollama_setup.PullError) as failed:
        ollama_setup.pull(ollama.OllamaProvider(), "qwen3.5:4b")

    assert "file does not exist" in str(failed.value)
    assert [(m, p) for m, p, _ in asked] == [("POST", "/api/pull")]
    assert json.loads(asked[0][2]) == {"model": "qwen3.5:4b", "stream": True}


def test_a_pull_whose_stream_ends_without_success_is_failed(monkeypatch):
    pulling(monkeypatch, {"status": "pulling manifest"}, {"status": "pulling abc", "total": 10, "completed": 5})

    with pytest.raises(ollama_setup.PullError) as failed:
        ollama_setup.pull(ollama.OllamaProvider(), "qwen3.5:4b")

    assert "success" in str(failed.value)


def test_a_pull_that_ends_in_success_passes_and_reports_progress(monkeypatch):
    pulling(monkeypatch, {"status": "pulling manifest"},
            {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 40},
            {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 100},
            {"status": "verifying sha256 digest"}, {"status": "success"})
    seen: list[tuple[str, int, int]] = []

    ollama_setup.pull(ollama.OllamaProvider(), "qwen3.5:4b", on_progress=lambda name, done, total: seen.append((name, done, total)))

    assert seen == [("qwen3.5:4b", 40, 100), ("qwen3.5:4b", 100, 100)]


def test_a_pull_the_daemon_refuses_with_a_status_is_failed_and_a_dead_daemon_names_a_proxy(monkeypatch, fenced):
    pulling(monkeypatch, {"error": "nope"}, status=500)
    with pytest.raises(ollama_setup.PullError):
        ollama_setup.pull(ollama.OllamaProvider(), "qwen3.5:4b")

    def factory(*, base_url, timeout):
        return httpx2.Client(base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(refused))

    monkeypatch.setattr(ollama, "default_client_factory", factory)
    with pytest.raises(ollama_setup.PullError) as plain:
        ollama_setup.pull(ollama.OllamaProvider(), "qwen3.5:4b")
    assert "proxy" not in str(plain.value)

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
    with pytest.raises(ollama_setup.PullError) as behind:
        ollama_setup.pull(ollama.OllamaProvider(), "qwen3.5:4b")
    assert str(behind.value).endswith(credentials.proxy_note())
    assert "proxy.corp:3128" in str(behind.value)


# --- the model offer and its unit (#9) -----------------------------------------------


def test_gemma_is_listed_at_the_threshold_not_below_and_never_on_apple_silicon():
    """22 GiB = 23,622,320,128 bytes, compared in bytes against the reported
    total. Robert's nominal 16 GB card reports 17,179,344,896 bytes (measured
    with torch on 2026-09-20): the only measured case, and it is not listed."""

    def listed(cuda, apple=False):
        return [c["value"] for c in ollama_setup.choices(cuda, apple)]

    assert listed(23_622_320_128) == ["qwen3.5:4b", "gemma4:12b"]
    assert listed(23_622_320_127) == ["qwen3.5:4b"]
    assert listed(17_179_344_896) == ["qwen3.5:4b"]
    assert listed(None) == ["qwen3.5:4b"]
    assert listed(100 * 2**30, apple=True) == ["qwen3.5:4b"], "never on Apple Silicon until a Mac has measured it"
    assert ollama_setup.GEMMA_THRESHOLD_BYTES == 23_622_320_128


def test_every_choice_names_its_bytes_its_unit_and_the_estimate():
    for choice in ollama_setup.choices(30 * 2**30, False):
        assert f"{ollama_setup.MODEL_BYTES[choice['value']]:,} bytes" in choice["note"]
        assert "GB" in choice["note"] and "10^9" in choice["note"], "the unit is named"
    gemma = ollama_setup.choices(30 * 2**30, False)[1]
    assert "estimate" in gemma["note"] and "23,622,320,128" in gemma["note"]
    assert ollama_setup.choices(None, False)[0]["value"] == ollama_setup.DEFAULT_MODEL == "qwen3.5:4b"


def test_the_memory_read_is_none_without_a_card(monkeypatch):
    """`accel.memory()` is the number the threshold was modelled on. It imports
    torch, which is why the plan asks for it only when the offer is built."""
    monkeypatch.setattr(accel, "memory", REAL_MEMORY)
    monkeypatch.setattr(accel, "cuda_available", lambda: False)
    assert accel.memory() is None


# =====================================================================================
# TASK-095: the offer follows Ollama's newest release, checked at install time.
#
# Robert decided on 2026-09-26 that MyScribe no longer pins the Ollama release it
# offers. The offer asks GitHub's releases/latest - which by definition skips
# drafts and prereleases - and the download must match both the API's digest and
# the release's own sha256sum.txt. Nothing here reaches GitHub: `GitHub` below is
# an `httpx2.MockTransport` behind `ollama_setup.release_client`.
# =====================================================================================


LATEST_URL = "https://api.github.com/repos/ollama/ollama/releases/latest"
NEWER_TAG = "v0.35.0"
"""Newer than the v0.34.3 the pin held: the release the red runs were made with."""


def newer_release(**digests) -> dict:
    """The assets of `NEWER_TAG`, each a name -> (bytes, sha256). The Windows
    one is the fake artifact, so an install can be run against it."""
    assets = {
        "OllamaSetup.exe": (len(FAKE_ARTIFACT), FAKE_SHA),
        "Ollama.dmg": (201_000_003, "d" * 64),
        "install.sh": (16_104, "e" * 64),
    }
    assets.update(digests)
    return assets


class GitHub:
    """releases/latest and the release's sha256sum.txt, with one knob per
    failure the task names. Records every request with its Authorization
    header, so a test can say which requests carried the token."""

    def __init__(self, assets=None, *, tag=NEWER_TAG, api_status=200, api_headers=None,
                 no_digest=(), missing=(), no_sums_asset=False, sums_status=200,
                 sums_lines=None, sums_raise=False, api_raise=False):
        self.assets = newer_release() if assets is None else assets
        self.tag = tag
        self.api_status = api_status
        self.api_headers = api_headers or {}
        self.no_digest = set(no_digest)
        self.missing = set(missing)
        self.no_sums_asset = no_sums_asset
        self.sums_status = sums_status
        self.sums_lines = sums_lines
        self.sums_raise = sums_raise
        self.api_raise = api_raise
        self.asked: list[tuple[str, str, str]] = []

    def url_of(self, name: str) -> str:
        return f"https://github.com/ollama/ollama/releases/download/{self.tag}/{name}"

    def __call__(self, request):
        url = str(request.url)
        self.asked.append((request.method, url, request.headers.get("Authorization", "")))
        if url == LATEST_URL:
            if self.api_raise:
                raise httpx2.ConnectError("no route to api.github.com")
            if self.api_status != 200:
                return httpx2.Response(self.api_status, json={"message": "no"}, headers=self.api_headers)
            listed = []
            for name, (size, digest) in self.assets.items():
                if name in self.missing:
                    continue
                entry = {"name": name, "size": size, "browser_download_url": self.url_of(name)}
                if name not in self.no_digest:
                    entry["digest"] = "sha256:" + digest
                listed.append(entry)
            if not self.no_sums_asset:
                listed.append({"name": "sha256sum.txt", "size": 1472, "digest": "sha256:" + "a" * 64,
                               "browser_download_url": self.url_of("sha256sum.txt")})
            return httpx2.Response(200, json={
                "tag_name": self.tag, "published_at": "2026-09-25T10:00:00Z",
                "draft": False, "prerelease": False, "assets": listed})
        if url == self.url_of("sha256sum.txt"):
            if self.sums_raise:
                raise httpx2.ConnectError("objects.githubusercontent.com did not answer")
            if self.sums_status != 200:
                return httpx2.Response(self.sums_status, text="gone")
            lines = self.sums_lines
            if lines is None:
                lines = [f"{digest}  ./{name}" for name, (_, digest) in self.assets.items()]
            return httpx2.Response(200, text="\n".join(lines) + "\n")
        return httpx2.Response(404)


def serve_github(monkeypatch, server: GitHub) -> GitHub:
    """`raising=False`: before TASK-095 the seam did not exist, and the red run
    had to fail on what the offer showed, not on a missing attribute."""
    monkeypatch.setattr(
        ollama_setup, "release_client",
        lambda: httpx2.Client(transport=httpx2.MockTransport(server), follow_redirects=True),
        raising=False,
    )
    return server


ASSET_OF = {"win32": "OllamaSetup.exe", "darwin": "Ollama.dmg", "linux": "install.sh"}


@pytest.fixture
def no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# --- criterion 1: the offer shows the newest release -----------------------------------


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_the_offer_shows_the_newest_release_s_url_size_and_sha256(
        tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed, platform):
    """Red before TASK-095: the plan showed v0.34.3's figures out of the pin
    whatever GitHub said."""
    server = serve_github(monkeypatch, GitHub())
    size, digest = server.assets[ASSET_OF[platform]]

    plan = ollama_setup.install_plan(platform, environ_for(tmp_path), into=tmp_path / "dl")

    assert (plan["tag"], plan["url"], plan["bytes"], plan["sha256"]) == (
        NEWER_TAG, server.url_of(ASSET_OF[platform]), size, digest)
    assert plan.get("sha256sum") == digest, "the second source is carried beside the API's digest"


def test_the_reader_asks_releases_latest_and_sha256sum_and_nothing_else(
        tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed):
    """Two GETs, no installer, no HEAD: the offer is shown before anything big
    is fetched."""
    server = serve_github(monkeypatch, GitHub())

    ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")

    assert [(m, u) for m, u, _ in server.asked] == [("GET", LATEST_URL), ("GET", server.url_of("sha256sum.txt"))]


def test_the_windows_offer_still_shows_the_command_the_folder_no_admin_and_self_update(
        tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed):
    """TASK-089.18 criterion 3 keeps holding with a fetched release."""
    serve_github(monkeypatch, GitHub())

    plan = ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")

    assert plan["command"][1:] == ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
    assert plan["shown"] == [subprocess.list2cmdline(plan["command"])]
    assert plan["install_dir"] == str(tmp_path / "AppData" / "Local" / "Programs" / "Ollama")
    assert plan["needs_admin"] is False and plan["self_updates"] is True
    assert plan["signer"] == "O=Ollama Inc."
    assert plan["tag"] == NEWER_TAG


def test_the_linux_commands_check_the_fetched_sum(tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed):
    server = serve_github(monkeypatch, GitHub())

    linux = ollama_setup.install_plan("linux", environ_for(tmp_path), into=tmp_path / "dl")

    assert server.url_of("install.sh") in linux["shown"][0]
    assert server.assets["install.sh"][1] in linux["shown"][1]
    assert not any("|" in line for line in linux["shown"])


# --- criteria 3 and 4: every way the release cannot be trusted or reached --------------


REFUSALS = {
    # why: (the fake GitHub's knobs, the words the sentence must use for it)
    "the two digests disagree": (dict(sums_lines=[f"{'f' * 64}  ./OllamaSetup.exe"]), "two different sha256"),
    "the asset carries no digest": (dict(no_digest={"OllamaSetup.exe"}), "gives no sha256"),
    "the release has no sha256sum.txt": (dict(no_sums_asset=True), "has no sha256sum.txt"),
    "sha256sum.txt answers 404": (dict(sums_status=404), "sha256sum.txt of Ollama's release v0.35.0 answered HTTP 404"),
    "sha256sum.txt cannot be reached": (dict(sums_raise=True), "could not be fetched"),
    "sha256sum.txt has no line for the asset": (dict(sums_lines=[f"{'e' * 64}  ./install.sh"]), "has no line for OllamaSetup.exe"),
    "the asset is not in the release": (dict(missing={"OllamaSetup.exe"}), "has no OllamaSetup.exe"),
    "releases/latest answers 404": (dict(api_status=404), "GitHub answered HTTP 404"),
    "GitHub cannot be reached": (dict(api_raise=True), "GitHub could not be asked"),
    "rate-limited with 403": (dict(api_status=403, api_headers={"X-RateLimit-Remaining": "0"}), "rate limit"),
    "rate-limited with 429": (dict(api_status=429), "rate limit"),
}


@pytest.mark.parametrize("why", list(REFUSALS))
def test_every_refusal_ends_on_the_vendor_page_and_check_again(
        tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed, why):
    """One sentence, the vendor page, Check again - and no plan, so nothing
    downstream can download or write. Each case names its own reason: a
    missing digest refused as "two digests disagree" would pass a test that
    only asked whether something was refused (mutant 02 of TASK-095 did)."""
    knobs, reason = REFUSALS[why]
    serve_github(monkeypatch, GitHub(**knobs))

    with pytest.raises(ollama_setup.InstallError) as refused:
        ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")

    sentence = str(refused.value)
    assert "https://ollama.com/download" in sentence and "Check again" in sentence
    assert reason in sentence, sentence
    assert "\n" not in sentence


def test_a_rate_limit_is_named_as_one(tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed):
    for knobs, _ in (REFUSALS["rate-limited with 403"], REFUSALS["rate-limited with 429"]):
        serve_github(monkeypatch, GitHub(**knobs))
        with pytest.raises(ollama_setup.InstallError) as refused:
            ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")
        assert "rate" in str(refused.value).lower()


def test_an_unreachable_github_names_a_configured_proxy(tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed):
    """The clause the other downloads add (credentials.proxy_note)."""
    serve_github(monkeypatch, GitHub(api_raise=True))
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:3128")

    with pytest.raises(ollama_setup.InstallError) as refused:
        ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")

    assert credentials.proxy_note() and credentials.proxy_note() in str(refused.value)


# --- the token: used for the API, never printed or stored ------------------------------


def test_a_github_token_goes_to_the_api_only_and_is_never_shown(tmp_path, monkeypatch, fenced, latest_release_unstubbed):
    token = "ghp_not_a_real_token_095"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    server = serve_github(monkeypatch, GitHub())

    plan = ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")

    auth = {url: header for _, url, header in server.asked}
    assert auth[LATEST_URL] == f"Bearer {token}"
    assert auth[server.url_of("sha256sum.txt")] == "", "the token is for api.github.com, not for the download host"
    assert token not in json.dumps(plan)

    serve_github(monkeypatch, GitHub(api_status=500))
    with pytest.raises(ollama_setup.InstallError) as refused:
        ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")
    assert token not in str(refused.value)


def test_without_a_token_the_offer_is_still_built(tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed):
    server = serve_github(monkeypatch, GitHub())

    plan = ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "dl")

    assert plan["tag"] == NEWER_TAG
    assert server.asked and all(header == "" for _, _, header in server.asked)


# --- criterion 3: the file must match both sources -------------------------------------


def test_a_file_that_matches_the_api_but_not_sha256sum_is_refused(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    signer_says(monkeypatch, True)
    started = fake_popen(monkeypatch, on_wait=lambda: install_a_binary(tmp_path))
    plan = a_plan(tmp_path)
    plan["sha256sum"] = "0" * 64

    outcome = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert outcome.ok is False and started == [] and ollama_setup.read_marker() is None
    assert "https://ollama.com/download" in outcome.sentence and "Check again" in outcome.sentence


def test_a_file_that_matches_sha256sum_but_not_the_api_is_refused(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    signer_says(monkeypatch, True)
    started = fake_popen(monkeypatch, on_wait=lambda: install_a_binary(tmp_path))
    plan = a_plan(tmp_path)
    plan["sha256"] = "0" * 64

    outcome = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert outcome.ok is False and started == [] and ollama_setup.read_marker() is None
    assert "https://ollama.com/download" in outcome.sentence


def test_a_plan_without_the_second_source_is_refused(tmp_path, monkeypatch, fenced):
    """A plan that lost its sha256sum.txt figure - a hand-made one, a front-end
    that dropped the field - is doubt, and doubt downloads nothing."""
    server = serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    started = fake_popen(monkeypatch)
    plan = a_plan(tmp_path)
    del plan["sha256sum"]

    outcome = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert outcome.ok is False and started == [] and server.requests == []


def test_a_download_refusal_no_longer_speaks_of_a_pin(tmp_path, monkeypatch):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))

    with pytest.raises(ollama_setup.InstallError) as refused:
        ollama_setup.download("https://example.invalid/OllamaSetup.exe", tmp_path / "dl", "OllamaSetup.exe",
                              expected_bytes=len(FAKE_ARTIFACT), expected_sha256="0" * 64)

    assert "pin" not in str(refused.value).lower()


def test_the_windows_signer_is_still_checked_on_a_fetched_release(tmp_path, monkeypatch, fenced):
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    checked = signer_says(monkeypatch, False, "signed by CN=Somebody Else")
    started = fake_popen(monkeypatch)

    outcome = ollama_setup.install(a_plan(tmp_path), into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert checked and started == [] and outcome.ok is False
    assert "signature was refused" in outcome.sentence


# --- the marker binds the fetched version ------------------------------------------------


def test_the_marker_records_the_version_of_the_release_that_was_fetched(
        tmp_path, monkeypatch, fenced, no_token, latest_release_unstubbed):
    serve_github(monkeypatch, GitHub())
    serve_artifact(monkeypatch, ArtifactServer(FAKE_ARTIFACT))
    signer_says(monkeypatch, True)
    fake_popen(monkeypatch, on_wait=lambda: install_a_binary(tmp_path))
    plan = ollama_setup.install_plan("win32", environ_for(tmp_path), into=tmp_path / "downloads")

    outcome = ollama_setup.install(plan, into=tmp_path / "downloads", environ=environ_for(tmp_path))

    assert outcome.installed is True
    marker = ollama_setup.read_marker()
    assert (marker["tag"], marker["version"]) == (NEWER_TAG, NEWER_TAG.lstrip("v"))
    assert marker["binary"] == str(install_a_binary(tmp_path))


# --- criteria 5 and 6: no pin left as a source of truth ----------------------------------


REPO = Path(ollama_setup.__file__).resolve().parents[1]


def test_the_pin_file_is_gone_and_what_stays_is_not_a_pin():
    """scribe/ollama_offer.json keeps the vendor page, the model sizes and the
    per-platform facts that are not a release (asset name, flags, signer,
    folder). No tag, no URL, no byte count, no digest of an installer."""
    assert not (REPO / "scribe" / "ollama_release.json").exists()
    kept = REPO / "scribe" / "ollama_offer.json"
    assert kept.exists()
    data = json.loads(kept.read_text(encoding="utf-8"))
    assert "tag" not in data
    for platform in ("win32", "darwin", "linux"):
        facts = data["artifacts"][platform]
        assert facts["name"] == ASSET_OF[platform]
        assert not {"url", "bytes", "sha256"} & set(facts), f"{platform} still pins a release figure"
    assert "releases/download" not in kept.read_text(encoding="utf-8")
    assert data["vendor_page"] == "https://ollama.com/download"
    assert set(data["models"]) == set(ollama_setup.MODEL_BYTES)


def test_the_launcher_s_ollama_figure_is_labelled_an_estimate_and_its_model_is_the_offer_s():
    """Replaces the test that held footprint.json equal to the pin. The
    launcher may import nothing from the app (ADR-011) and cannot ask GitHub
    before the first window, so its installer sizes are an estimate from one
    past release and say so; the offer shows the fetched size. The model size
    is still the one the offer itself uses."""
    paper = json.loads((REPO / "scribe" / "footprint.json").read_text(encoding="utf-8"))["ollama"]

    assert "estimate" in paper["measured"].lower()
    assert "pin" not in paper["source"].lower()
    assert paper["model"] == ollama_setup.DEFAULT_MODEL
    assert paper["model_bytes"] == ollama_setup.MODEL_BYTES[ollama_setup.DEFAULT_MODEL]
    for platform, size in paper["installer_bytes"].items():
        assert size is None or (isinstance(size, int) and size > 0), platform


def test_ci_and_the_release_steps_no_longer_check_a_pin():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    releasing = (REPO / "docs" / "RELEASING.md").read_text(encoding="utf-8")

    assert "check-pin" not in ci and "ollama_release" not in ci
    assert "check-pin" not in releasing and "ollama_release.json" not in releasing
    assert "2b." not in releasing
    assert not hasattr(ollama_setup, "check_pin")
