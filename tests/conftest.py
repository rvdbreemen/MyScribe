"""Shared pytest fixtures for the scribe test suite.

Task 1 keeps this minimal; the tmp-DB fixture and fake-runner helpers
arrive with the tasks that need them (db, jobs, supervisor).
"""

import pytest

import contextlib
import shutil
import threading

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
