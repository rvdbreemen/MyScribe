"""Shared pytest fixtures for the scribe test suite.

Task 1 keeps this minimal; the tmp-DB fixture and fake-runner helpers
arrive with the tasks that need them (db, jobs, supervisor).
"""

import pytest

import contextlib
import shutil
import threading
from pathlib import Path

from scribe import applog, credentials, env, paths

_LIBRARY_DB = credentials.library_db
"""`credentials.library_db` as it ships, captured while this file is imported
- before any fixture has stubbed it. `library_db_unstubbed` hands it back to
the tests that are about it."""

_REGISTRY_NAMES = credentials.registry_names
"""`credentials.registry_names` as it ships, for the same reason and in the
same manner: the autouse fixture below replaces it everywhere, so the tests
that are about the reader itself ask for `registry_names_unstubbed`."""


@contextlib.contextmanager
def _no_library_db():
    """No installation database, which is the truth in a test."""
    yield None


@pytest.fixture(autouse=True)
def _no_weights_fetched(request, monkeypatch):
    """No test downloads speech weights (TASK-107.04).

    `transcribe.load_model` now fetches the weights it is about to load when
    they are missing, through `models.ensure` - and a test that calls the real
    `load_model` with a fake model class reached the Hugging Face Hub for real.
    So the fetch is a no-op everywhere, except in tests marked
    `real_weights_fetch`, which stand in for `models.ensure` themselves.
    """
    if request.node.get_closest_marker("real_weights_fetch") is not None:
        return
    monkeypatch.setattr("scribe.stages.transcribe.fetch_missing_weights", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    """Every test runs on a volume with room, unless it says otherwise.

    TASK-043 put a floor under the url stage: it refuses below
    `doctor.DISK_FLOOR_GB` free at `paths.DATA_DIR`, and DATA_DIR is a
    tmp_path here - so without this, the whole import suite would quietly
    depend on the free space of the machine's TEMP volume, and fail with
    DISK_LOW on a small disk while passing on a big one. A test about the
    floor patches `shutil.disk_usage` itself and wins, because monkeypatch
    applies it after this fixture.
    """
    plenty = shutil._ntuple_diskusage(total=1000 * 2**30, used=100 * 2**30, free=900 * 2**30)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: plenty)


@pytest.fixture(autouse=True)
def _own_log_dir(tmp_path, monkeypatch):
    """Every test writes its application log under its own tmp_path.

    applog resolves paths.LOGS_DIR at every call, so a test that patched
    DB_PATH and DATA_DIR but not LOGS_DIR - most of them, written before the
    log existed - would append to the developer's real data/logs/app.log.
    It happened: 190 lines from fake runner jobs were found there the day the
    log went in. Autouse, so no future fixture has to remember.
    """
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "applog")
    # runner.main and Supervisor._loop name the writer in-process; without
    # this the name leaks from one test into the next.
    monkeypatch.setattr(applog, "_default_proc", None)
    monkeypatch.setattr(applog, "_names", threading.local())


@pytest.fixture(autouse=True)
def _no_credentials_from_this_machine(tmp_path, monkeypatch):
    """No test is answered by a place on this machine that a test cannot control.

    `scribe.credentials` reads five of them: the Windows registry by name and
    by prefix, Hugging Face's login file, the `.env` beside the repository,
    and - since TASK-089.04 gave the doctor and `python -m scribe.models` a
    way to see a token saved in Settings - this installation's own database at
    `paths.DB_PATH`. This machine's HKLM holds HF_TOKEN, so without the first
    every test that asks "is a token set" would be answered by the
    developer's registry; tests/test_stage_diarize.py's
    `no_token_anywhere_is_none` is the one that would go green for the wrong
    reason, and the doctor's "no token" test the one that would go red.

    `registry_names` is the prefix reader (TASK-089.06), and it is stubbed for
    the same reason one step further: this machine's HKCU holds four real
    `OLLAMA_*` names, so without it no test here could ever see an Ollama
    report absent - and that would look like a bug in the detector rather than
    a machine answering a question a test asked.

    `library_db` is stubbed rather than `paths.DB_PATH` patched: DB_PATH
    without DATA_DIR would be a pair that disagrees (tests/test_paths.py:8
    asserts they do not), and the live 100 MB library is what an unfenced
    `pytest tests/test_doctor.py` would otherwise open and run a WAL pragma
    on. A test that is about `library_db` itself asks for the
    `library_db_unstubbed` fixture and points DB_PATH at its own database.

    `env._applied` is process-global and never cleared, so a test that loaded
    a `.env` would have every later lookup in that process attribute its
    names to the file. Emptied per test, which is what "this test's
    environment" means.

    What is deliberately *not* stubbed is `os.environ`: a live `-m gpu` run
    resolves this machine's real OPENROUTER_TOKEN from it, and deleting the
    names here would turn tests/test_llm_live.py into silent skips. A test
    that must see no token deletes the names itself, as
    tests/test_stage_diarize.py does.

    The readers are stubbed rather than the registry, `SCRIBE_ENV_FILE` or
    `env.DEFAULT_PATH` themselves: `scribe.env` and its own tests keep owning
    what `.env` means, and a test that wants a credential found somewhere
    patches these same seams afterwards and wins, because monkeypatch applies
    in order.
    """
    monkeypatch.setattr(credentials, "registry_hits", lambda name: ())
    monkeypatch.setattr(credentials, "registry_names", lambda prefix: ())
    monkeypatch.setattr(credentials, "login_file", lambda: tmp_path / "no-hugging-face-login-file")
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, tmp_path / "no.env"))
    monkeypatch.setattr(credentials, "library_db", _no_library_db)
    monkeypatch.setattr(env, "_applied", set())


@pytest.fixture
def registry_names_unstubbed(monkeypatch):
    """`credentials.registry_names` as it ships, for the test that is about it.

    The autouse fixture replaces it with `lambda prefix: ()` for every test, so
    that this machine's four real `OLLAMA_*` names cannot make `absent`
    unreachable here - and that left the shipped reader asserted by nothing at
    all. A mutant returning `()` unconditionally passed both files that name
    it.

    The test it serves reads names and never a value, which is what makes
    asking the real registry safe.
    """
    monkeypatch.setattr(credentials, "registry_names", _REGISTRY_NAMES)
    assert credentials.registry_names is _REGISTRY_NAMES, "the stub is still in place; this fixture proved nothing"
    return _REGISTRY_NAMES


@pytest.fixture(autouse=True)
def _no_ollama_from_this_machine(monkeypatch):
    """No test is answered by the Ollama running on the developer's machine.

    The same rule as the fixture above, one module further: `doctor.checks()`
    runs `check_ollama`, `check_ollama` asks `ollama_setup.state()`, and
    `state()` opens a loopback socket. Six tests in tests/test_doctor.py call
    `checks()` or `main(["--no-gpu"])` and stub none of it, so they reached
    this machine's daemon - one GET, plus one POST per pulled model on a
    daemon too old to report capabilities, which is eight more requests here.
    A green run could not see it: the check is optional, so the one test that
    asserts about the results filters it out.

    Absent is the state to stand in with: it is the one this machine can never
    report, so a test that means to see another says so itself and wins,
    because monkeypatch applies in order.

    Imported inside the fixture the way `doctor.check_ollama` imports it - it
    pulls in `scribe.llm`, and this file is imported by every test process.

    The real function is handed back rather than dropped: `ollama_state_unstubbed`
    is what the two files that are *about* the detector ask for.
    """
    from scribe import ollama_setup

    shipped = ollama_setup.state
    monkeypatch.setattr(
        ollama_setup, "state", lambda **kwargs: ollama_setup.State(state=ollama_setup.ABSENT)
    )
    return shipped


@pytest.fixture
def ollama_state_unstubbed(monkeypatch, _no_ollama_from_this_machine):
    """`ollama_setup.state` as it ships, for the tests whose subject it is.

    tests/test_ollama_setup.py hands it a fake machine through its seams, and
    tests/test_proxy.py points it at a real socket; both would otherwise be
    answered by the stub above, which is nobody's subject.

    The stub fixture is requested by name so that it is set up first and this
    one wins. The assertion is not ceremony: an ordering that left the stub in
    place would leave tests/test_proxy.py's detector test asserting about
    requests that were never made.
    """
    from scribe import ollama_setup

    monkeypatch.setattr(ollama_setup, "state", _no_ollama_from_this_machine)
    assert ollama_setup.state is _no_ollama_from_this_machine, (
        "the stub is still in place; this fixture proved nothing"
    )
    return _no_ollama_from_this_machine


# --- TASK-095: no test asks GitHub which Ollama release is newest ------------------

CANNED_OLLAMA_TAG = "v0.99.1"
"""The release every test is answered with. Deliberately not v0.34.3, the pin
TASK-095 removed: a figure that still came from the old pin would show up as
the wrong tag instead of passing by coincidence."""


def canned_ollama_release() -> dict:
    """What `ollama_setup.latest_release` returns, for a release that does not
    exist: all three platforms' assets, because the macOS and Linux runners
    build their own platform's offer, each with its API digest and the same
    digest in sha256sum.txt."""
    base = f"https://github.com/ollama/ollama/releases/download/{CANNED_OLLAMA_TAG}"
    assets = {
        "OllamaSetup.exe": (1_600_000_001, "1" * 64),
        "Ollama.dmg": (200_000_002, "2" * 64),
        "install.sh": (16_003, "3" * 64),
    }
    return {
        "tag": CANNED_OLLAMA_TAG,
        "published": "2026-09-25T12:00:00Z",
        "assets": {
            name: {"url": f"{base}/{name}", "bytes": size, "digest": digest}
            for name, (size, digest) in assets.items()
        },
        "sums": {name: digest for name, (_, digest) in assets.items()},
    }


@pytest.fixture(autouse=True)
def _no_github_from_a_test(monkeypatch):
    """Every `setup.plan()` on a machine the stub above calls absent now builds
    the install offer, and the offer asks GitHub for Ollama's newest release
    (TASK-095). No test may reach api.github.com, so the reader is replaced by
    the canned release everywhere; the real one is handed back for the tests
    that are about it (`latest_release_unstubbed`).

    `raising=False` so that this file also loads against code from before the
    reader existed, which is how the red runs of TASK-095 were made.
    """
    from scribe import ollama_setup

    shipped = getattr(ollama_setup, "latest_release", None)
    monkeypatch.setattr(ollama_setup, "latest_release", lambda **kwargs: canned_ollama_release(), raising=False)
    return shipped


@pytest.fixture
def latest_release_unstubbed(monkeypatch, _no_github_from_a_test):
    """`ollama_setup.latest_release` as it ships; its HTTP goes through
    `ollama_setup.release_client`, which the test then points at a fake."""
    from scribe import ollama_setup

    monkeypatch.setattr(ollama_setup, "latest_release", _no_github_from_a_test, raising=False)
    return _no_github_from_a_test


@pytest.fixture
def canned_release() -> dict:
    return canned_ollama_release()


@pytest.fixture
def library_db_unstubbed(monkeypatch):
    """`credentials.library_db` as it ships, for the tests that are about it.

    The autouse fixture above replaces it for every test; this puts the real
    one back for the ones whose subject it is - and they point
    `paths.DB_PATH` at a database of their own, so it still never reaches
    this machine's library.

    The assertion is not ceremony: two of those tests assert that no database
    is no connection, which is exactly what the stub answers, so an ordering
    that left the stub in place would leave them green and proving nothing.
    """
    monkeypatch.setattr(credentials, "library_db", _LIBRARY_DB)
    assert credentials.library_db is _LIBRARY_DB, "the stub is still in place; this fixture proved nothing"
    return _LIBRARY_DB


@pytest.fixture(autouse=True)
def _no_card_from_this_machine(monkeypatch):
    """No test is answered by the graphics card on the developer's machine.

    `accel.memory()` imports torch and opens a CUDA context on device 0 to read
    the card's total memory, for the model offer a sitting builds when no
    Ollama is on the machine (TASK-089.18) - and the fixture above makes every
    machine such a machine. Without this, every plan in the suite would pay
    for torch in its process, which pytest.ini's default run is written not to
    do. None reads as "no card": the default model is offered and nothing
    bigger. A test about the threshold hands `ollama_setup.choices()` its bytes
    itself; the one about the reader puts the shipped function back.
    """
    from scribe import accel

    monkeypatch.setattr(accel, "memory", lambda: None)



OLLAMA_PORT = 11434
"""Where this machine's Ollama daemon listens, and so the port no test reaches."""


@pytest.fixture(autouse=True)
def _no_daemon_answers_a_test(monkeypatch):
    """No test is answered by the Ollama daemon running on this machine.

    `_no_ollama_from_this_machine` stubs the detector; this closes the port.
    Measured for TASK-090 with a socket tripwire: 61 tests in 11 files - the
    settings page, the AI panel, the glossary, exports - opened a real socket
    to 127.0.0.1:11434 and were answered by whatever this laptop had running
    and pulled. A suite that answers differently when Ollama is up tests the
    machine, not the code.

    Closed at the socket rather than by stubbing a provider method, because
    the tests that are about those methods hand them a MockTransport, which
    never opens a socket and so is untouched here. A connection to the port is
    refused the way a stopped daemon refuses it, a state every caller already
    handles. A test that means a daemon to be up stubs `tags` itself
    (`ollama_ready`) and wins; one that needs a real listener on this port
    asks for `ollama_port_open`.
    """
    import errno
    import socket

    shipped_connect = socket.socket.connect
    shipped_connect_ex = socket.socket.connect_ex

    def _to_ollama(address) -> bool:
        return isinstance(address, tuple) and len(address) >= 2 and address[1] == OLLAMA_PORT

    def connect(self, address):
        if _to_ollama(address):
            raise ConnectionRefusedError(f"the test suite keeps port {OLLAMA_PORT} closed (TASK-090)")
        return shipped_connect(self, address)

    def connect_ex(self, address):
        if _to_ollama(address):
            return errno.ECONNREFUSED
        return shipped_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    return shipped_connect, shipped_connect_ex


@pytest.fixture
def ollama_ready(monkeypatch):
    """A daemon that is up and has qwen3.5:4b, the model a test names when it
    names none.

    For a test about what happens once a provider can answer - the rows a
    bulk pass queues, the speaker pass a re-transcription asks for. Since
    TASK-089.10 and TASK-089.26 those ask whether Ollama can answer before
    they write a job, and since TASK-090 the port is closed, so a test that
    needs the answer to be yes has to say so. tests/test_web_ai.py and
    tests/test_web_library.py keep their own, which name more models.
    """
    from scribe.llm import ollama

    monkeypatch.setattr(
        ollama.OllamaProvider,
        "tags",
        lambda self: [{"name": "qwen3.5:4b", "model": "qwen3.5:4b", "capabilities": ["completion"]}],
    )


@pytest.fixture
def ollama_port_open(monkeypatch, _no_daemon_answers_a_test):
    """Port 11434 as the operating system has it, for a test that serves on it."""
    import socket

    shipped_connect, shipped_connect_ex = _no_daemon_answers_a_test
    monkeypatch.setattr(socket.socket, "connect", shipped_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", shipped_connect_ex)

_FENCED = ("DATA_DIR", "DB_PATH", "WORK_DIR", "MEDIA_DIR", "MODELS_DIR")
"""The five places a test could write into somebody's library (TASK-090)."""


@pytest.fixture(autouse=True)
def _library_under_tmp_path(tmp_path, monkeypatch):
    """Every test gets a library of its own, under its own tmp_path.

    `scribe.paths` is fixed at import from SCRIBE_DATA_DIR, else the
    repository's `data/`, and until TASK-090 only LOGS_DIR was fenced here:
    35 test files patched what they needed and the rest relied on never
    reaching it. Two did - the doctor's checks probed and migrated the live
    database, and the runner's cleanup removed WORK_DIR/<job id> from the
    live scratch. This points all five at a directory nobody else owns.

    The environment is fenced too, for what runs outside this process or
    reloads `paths`: a child a test starts inherits SCRIBE_DATA_DIR, and
    `paths.refresh()` rebuilds the same five from it. SCRIBE_ENV_FILE names a
    file that does not exist, so no test reads the developer's `.env` and
    the tokens in it.

    A test that wants another place patches it afterwards and wins, because
    its own fixtures run after this one.
    """
    library = tmp_path / "library"
    monkeypatch.setenv("SCRIBE_DATA_DIR", str(library))
    monkeypatch.setenv(env.PATH_VARIABLE, str(tmp_path / "no-such.env"))
    monkeypatch.setattr(paths, "DATA_DIR", library)
    monkeypatch.setattr(paths, "DB_PATH", library / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", library / "media")
    monkeypatch.setattr(paths, "WORK_DIR", library / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", library / "models")
    return library


@pytest.fixture(autouse=True)
def _the_library_is_out_of_reach(tmp_path, _library_under_tmp_path):
    """Every test starts with the five paths under its own tmp_path.

    Checked at setup, before the test's own fixtures run, so a test that
    points one somewhere else on purpose still can; what this catches is a
    test that never thought about it and would have reached `data/`.
    """
    import os

    for name in _FENCED:
        value = getattr(paths, name)
        assert Path(value).resolve().is_relative_to(tmp_path.resolve()), (
            f"paths.{name} is {value}, outside this test's tmp_path: "
            "a test run here would reach the library in it"
        )
    assert not Path(os.environ.get(env.PATH_VARIABLE, "") or "\0").is_file() or os.environ[env.PATH_VARIABLE] == "", (
        f"{env.PATH_VARIABLE} names a .env that exists; a test would read the developer's tokens"
    )


# --- TASK-089.19: no library on this machine is found by a test ----------------------

_MACHINE_CANDIDATES = None
"""`scribe.library.machine_candidates` as it ships, captured on first use by
`machine_candidates_unstubbed` - importing `scribe.library` here at the top
would pull `scribe.db` into every test's collection for nothing."""


@pytest.fixture(autouse=True)
def _no_library_found_on_this_machine(monkeypatch):
    """The first-run sitting looks for a library in `<repo>/data`, in the
    per-user MyScribe folder and in any SCRIBE_DATA_DIR a layer names
    (TASK-089.19). Every one of those is somebody's real library on the
    machine running the suite - in the main checkout `<repo>/data` is Robert's
    live one - and even a read-only look is a look his rule forbids. So no
    test finds any; a test about finding hands `library.found` its own."""
    global _MACHINE_CANDIDATES
    from scribe import library

    if _MACHINE_CANDIDATES is None:
        _MACHINE_CANDIDATES = library.machine_candidates
    monkeypatch.setattr(library, "machine_candidates", lambda: [])


@pytest.fixture
def machine_candidates_unstubbed(monkeypatch, _no_library_found_on_this_machine):
    """The shipped lookup, for the tests about the lookup itself. Its three
    sources are still fenced: `env.REPO_DIR` and the per-user folder are the
    test's to point, and the registry and `.env` are stubbed above."""
    from scribe import library

    monkeypatch.setattr(library, "machine_candidates", _MACHINE_CANDIDATES)
