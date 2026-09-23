"""How a release points at an adopted library (TASK-089.19, criterion 8).

The launcher forces SCRIBE_DATA_DIR to `<home>/data` into the app's environment,
so `.env` cannot carry a library that lives somewhere else. The pointer file of
TASK-089.14 - `MyScribe.location`, beside the default home - gains a second
fact, `"data"`. The engine decides it, the launcher writes it only when the
engine's result names it, and `Layout.data_dir` prefers it. With no such fact
nothing changes: the app starts on `<home>/data`, as it did.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).resolve().parent.parent / "packaging" / "launcher" / "myscribe_launcher.py"
_spec = importlib.util.spec_from_file_location("myscribe_launcher_library", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


@pytest.fixture
def payload(tmp_path):
    payload = tmp_path / "payload"
    (payload / "app" / "scribe").mkdir(parents=True)
    (payload / "bin").mkdir()
    (payload / "app" / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (payload / "app" / "scribe" / "__init__.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    (payload / "app" / "scribe" / "setup.py").write_text("CONTRACT = 5\n", encoding="utf-8")
    return payload


@pytest.fixture
def pointer(tmp_path, monkeypatch):
    """A pointer file of this test's own, and a release with nothing else
    saying where things are."""
    path = tmp_path / "MyScribe.location"
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: path)
    monkeypatch.setattr(launcher, "default_home", lambda *a, **k: tmp_path / "default")
    monkeypatch.delenv(launcher.HOME_VARIABLE, raising=False)
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    return path


@pytest.fixture
def started(monkeypatch):
    """Where the launcher would have started the app: the layout `main` built,
    caught at the door instead of a sync and a server."""
    caught: list = []

    def run_headless(launch, force_setup=False, at_login=False):
        caught.append(launch.layout)
        return 0

    monkeypatch.setattr(launcher, "run_headless", run_headless)
    return caught


def _main(payload) -> int:
    return launcher.main(["--headless", "--no-browser", "--payload", str(payload)])


def test_a_pointer_that_names_an_adopted_library_starts_the_app_on_it_and_on_no_other(
    tmp_path, payload, pointer, started, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    adopted = tmp_path / "clone" / "data"
    adopted.mkdir(parents=True)
    pointer.write_text(json.dumps({"home": str(home), "data": str(adopted)}), encoding="utf-8")

    assert _main(payload) == 0

    [layout] = started
    assert layout.home == home.absolute()
    assert layout.data_dir == adopted.absolute()
    environment = launcher.app_environment(layout, {})
    assert environment["SCRIBE_DATA_DIR"] == str(adopted.absolute())
    # The gate reads the adopted library's own stamp, and the disk that is
    # measured is the one the library is on: both follow `data_dir`.
    assert launcher.setup_stamp(layout) == adopted.absolute() / "setup.json"
    assert launcher.disk_probe_path(layout) == adopted.absolute()

    # And the process itself gets it: the command's environment, caught at Popen.
    seen: dict = {}

    class Caught:
        def __init__(self, command, **kwargs):
            seen.update(kwargs["env"])

        def poll(self):
            return None

    monkeypatch.setattr(launcher.subprocess, "Popen", Caught)
    launcher.prepare_home(layout)
    launcher.AppProcess(layout, 4299).start()
    assert seen["SCRIBE_DATA_DIR"] == str(adopted.absolute())
    assert not (home / "data").exists(), "prepare_home made the library it was not pointed at"


def test_with_no_such_fact_the_app_starts_on_the_home_as_today(tmp_path, payload, pointer, started):
    home = tmp_path / "home"
    home.mkdir()
    pointer.write_text(json.dumps({"home": str(home)}), encoding="utf-8")

    assert _main(payload) == 0

    [layout] = started
    assert launcher.app_environment(layout, {})["SCRIBE_DATA_DIR"] == str(home.absolute() / "data")


def test_no_pointer_at_all_is_the_default_home_and_its_data(tmp_path, payload, pointer, started):
    (tmp_path / "default").mkdir()
    assert _main(payload) == 0

    [layout] = started
    assert launcher.app_environment(layout, {})["SCRIBE_DATA_DIR"] == str((tmp_path / "default" / "data").absolute())


def test_an_adopted_library_that_is_gone_stops_the_launcher_and_starts_nothing(
    tmp_path, payload, pointer, started
):
    """An unplugged drive. Falling back to `<home>/data` would start a second,
    empty library, which to its owner looks like every recording gone."""
    home = tmp_path / "home"
    home.mkdir()
    gone = tmp_path / "unplugged" / "data"
    pointer.write_text(json.dumps({"home": str(home), "data": str(gone)}), encoding="utf-8")

    assert _main(payload) == 1
    assert started == []
    assert not (home / "data").exists()


def test_a_home_given_by_hand_keeps_everything_under_it(tmp_path, payload, pointer, started):
    """`--home` and MYSCRIBE_HOME say "everything goes here", the library
    included. A pointer's adopted library does not follow somebody into a
    home they named for a test run."""
    adopted = tmp_path / "clone" / "data"
    adopted.mkdir(parents=True)
    pointer.write_text(json.dumps({"data": str(adopted)}), encoding="utf-8")
    elsewhere = tmp_path / "by-hand"

    assert launcher.main(["--headless", "--no-browser", "--payload", str(payload), "--home", str(elsewhere)]) == 0

    [layout] = started
    assert layout.data_dir == elsewhere.absolute() / "data"


def test_writing_the_library_keeps_the_home_and_writing_the_home_keeps_the_library(tmp_path):
    path = tmp_path / "MyScribe.location"
    assert launcher.write_pointer(path, tmp_path / "home") == ""
    assert launcher.write_pointer_data(path, tmp_path / "lib") == ""
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "home": str(tmp_path / "home"), "data": str(tmp_path / "lib"),
    }
    assert launcher.write_pointer(path, tmp_path / "home2") == ""
    assert json.loads(path.read_text(encoding="utf-8"))["data"] == str(tmp_path / "lib")


def test_a_pointer_with_only_a_library_does_not_answer_where_everything_goes(tmp_path, payload):
    """`"data"` alone is what adoption writes after the location question
    was skipped; it answers the library question and not that one, so the
    home is still the default and the location question still reads as open
    for a home that holds no environment yet."""
    path = tmp_path / "MyScribe.location"
    path.write_text(json.dumps({"data": str(tmp_path / "lib")}), encoding="utf-8")

    assert launcher.home_dir("linux", {"XDG_DATA_HOME": str(tmp_path / "xdg")}, pointer=path) == tmp_path / "xdg" / "MyScribe"
    assert launcher.wants_location(tmp_path / "default", payload, path, None, {}) is True


# --- the launcher writes what the engine decided, and nothing it decided itself ----


def _a_plan(*ids: str) -> dict:
    return {"contract": 5, "found": [], "ollama": {"note": "", "state": "absent"},
            "downloads": {"total_bytes": 0},
            "questions": [{"id": id, "kind": "choice", "text": id, "choices": [], "current": "",
                           "default": None, "shown_if": None, "if_skipped": "", "answer_later": ""} for id in ids]}


@pytest.fixture
def engine(tmp_path, payload, monkeypatch):
    """A launch whose setup children are fakes: each apply leaves the result
    the test scripts for it in the file the launcher named, the way
    `scribe.setup` does, and each plan is recorded with what it was given."""
    pointer = tmp_path / "MyScribe.location"
    layout = launcher.Layout(tmp_path / "home", payload, pointer=pointer)
    layout.logs_dir.mkdir(parents=True)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, 4299, False, lambda s, t: reports.append((s, t)))
    plans: list[dict] = []
    results: list[dict] = []
    envs: list[dict] = []

    def setup_plan(layout, report, *, unasked_only=True, named=(), on_start=None):
        plans.append({"data_dir": layout.data_dir, "named": list(named)})
        return _a_plan("library", "llm_provider")

    def run_streaming(command, *, env, cwd, on_line, input=None, on_start=None):
        envs.append(env)
        scripted = results.pop(0) if results else {}
        if scripted:
            Path(env[launcher.SETUP_RESULT_VARIABLE]).write_text(json.dumps(scripted), encoding="utf-8")
        return 0

    monkeypatch.setattr(launcher, "setup_plan", setup_plan)
    monkeypatch.setattr(launcher, "run_streaming", run_streaming)
    monkeypatch.setattr(launcher, "run_prove", lambda *a, **k: 0)
    return launch, pointer, plans, results, envs, reports


def test_an_adoption_the_engine_reports_is_written_into_the_pointer_and_the_sitting_goes_on_there(engine, tmp_path):
    launch, pointer, plans, results, envs, reports = engine
    adopted = tmp_path / "clone" / "data"
    adopted.mkdir(parents=True)
    pointer.write_text(json.dumps({"home": str(tmp_path / "home")}), encoding="utf-8")
    results.append({"data_dir": str(adopted)})

    launcher.open_sitting(launch, lambda plan: {"library": str(adopted)}, launch.report)

    assert json.loads(pointer.read_text(encoding="utf-8")) == {"home": str(tmp_path / "home"), "data": str(adopted)}
    assert launch.layout.data_dir == adopted.absolute()
    assert [plan["data_dir"] for plan in plans] == [tmp_path.absolute() / "home" / "data", adopted.absolute()], \
        "the rest of the sitting was not asked against the adopted library"
    assert all(launcher.SETUP_RESULT_VARIABLE in env for env in envs)
    assert not launcher.setup_result_file(launch.layout).exists(), "the result file was left behind"


def test_without_the_engines_word_the_launcher_writes_no_library(engine, tmp_path):
    """An answer that names a folder is not an adoption: only the engine's
    result is. The launcher never reads a question id to decide this."""
    launch, pointer, plans, results, envs, reports = engine
    launcher.open_sitting(launch, lambda plan: {"library": str(tmp_path / "somewhere")}, launch.report)
    assert not pointer.exists()
    assert launch.layout.data_dir == tmp_path.absolute() / "home" / "data"
    assert len(plans) == 1


def test_a_named_folder_is_planned_again_with_it_listed(engine, tmp_path):
    launch, pointer, plans, results, envs, reports = engine
    named = tmp_path / "named"
    results.append({"library": str(named)})

    launcher.open_sitting(launch, lambda plan: {"library": "named", "library_folder": str(named)}, launch.report)

    assert [plan["named"] for plan in plans] == [[], [str(named)]]
    assert not pointer.exists()


def test_the_console_door_hears_an_adoption_too_and_asks_again_in_that_library(tmp_path, payload, monkeypatch):
    import types

    pointer = tmp_path / "MyScribe.location"
    layout = launcher.Layout(tmp_path / "home", payload, pointer=pointer)
    layout.logs_dir.mkdir(parents=True)
    adopted = tmp_path / "clone" / "data"
    adopted.mkdir(parents=True)
    monkeypatch.setattr(launcher.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(launcher, "run_prove", lambda *a, **k: 0)
    calls: list[str] = []

    def call(command, *, cwd, env):
        calls.append(env["SCRIBE_DATA_DIR"])
        if len(calls) == 1:
            Path(env[launcher.SETUP_RESULT_VARIABLE]).write_text(json.dumps({"data_dir": str(adopted)}), encoding="utf-8")
        return 0

    monkeypatch.setattr(launcher.subprocess, "call", call)
    launch = launcher.Launch(layout, 4299, False, lambda s, t: None)

    launcher.console_sitting(launch, launch.report)

    assert calls == [str(tmp_path.absolute() / "home" / "data"), str(adopted.absolute())]
    assert json.loads(pointer.read_text(encoding="utf-8")) == {"data": str(adopted)}


def test_a_home_given_by_hand_uses_the_adopted_library_for_this_start_and_says_so(engine, tmp_path, payload):
    launch, pointer, plans, results, envs, reports = engine
    launch.layout = launcher.Layout(tmp_path / "home", payload)  # no pointer: --home was given
    adopted = tmp_path / "clone" / "data"
    results.append({"data_dir": str(adopted)})

    launcher.open_sitting(launch, lambda plan: {}, launch.report)

    assert not pointer.exists()
    assert launch.layout.data_dir == adopted.absolute()
    assert any(state == "error" and "for this start only" in text for state, text in reports)
