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
