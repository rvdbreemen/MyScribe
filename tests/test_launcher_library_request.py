"""Where the library goes, asked apart from the engine home (TASK-097, ADR-036).

The Windows installer's library page - and `/LIBRARY=` on a silent install -
records a `"library_request"` in `MyScribe.location`, only while that file
does not exist yet. macOS and Linux have no installer page, so their first
start asks the same question and records the same request. One stdlib
resolver in the launcher then settles it before anything is downloaded:

- the per-user default, left as it was prefilled, is today's layout
  (`<home>/data`) and writes no `"data"`;
- an empty or a new folder becomes the library at once, as `"data"`;
- a folder that holds other things gets a `MyScribe` subfolder;
- a folder that holds `myscribe.db` or `scribe.db` - itself, or in its `data/`,
  which is what a former home looks like - is not used by the launcher: the
  engine adopts it after the download, through the `--library` path that shows
  its numbers first (ADR-019);
- anything else is refused with a sentence.

Every folder here is under the test's own tmp_path, and every library is an
empty file with the right name: the launcher never opens one.
"""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).resolve().parent.parent / "packaging" / "launcher" / "myscribe_launcher.py"
_spec = importlib.util.spec_from_file_location("myscribe_launcher_request", LAUNCHER_PATH)
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
def machine(tmp_path, monkeypatch):
    """A release on a machine of the test's own: the default home and the
    pointer beside it under tmp_path, nothing in the environment, no Tk."""
    default = tmp_path / "local" / "MyScribe"
    pointer = tmp_path / "local" / "MyScribe.location"
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: pointer)
    monkeypatch.setattr(launcher, "default_home", lambda *a, **k: default)
    monkeypatch.setattr(launcher, "install_dir", lambda: tmp_path / "Programs" / "MyScribe")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv(launcher.HOME_VARIABLE, raising=False)
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    return types.SimpleNamespace(default=default, pointer=pointer)


@pytest.fixture
def started(monkeypatch):
    """The layout `main` built, caught at the door instead of a sync."""
    caught: list = []

    def run_headless(launch, force_setup=False, at_login=False):
        caught.append(launch.layout)
        return 0

    monkeypatch.setattr(launcher, "run_headless", run_headless)
    return caught


def _request(machine, folder) -> None:
    """What the installer's library page leaves: the pointer with one key."""
    machine.pointer.parent.mkdir(parents=True, exist_ok=True)
    machine.pointer.write_text(json.dumps({launcher.REQUEST_KEY: str(folder)}), encoding="utf-8")


def _pointer(machine) -> dict:
    return json.loads(machine.pointer.read_text(encoding="utf-8"))


def _main(payload, *extra: str) -> int:
    return launcher.main(["--headless", "--no-browser", "--payload", str(payload), *extra])


def _library(folder: Path, name: str = "myscribe.db") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"")
    return folder


# --- the request is settled before anything is downloaded (AC #1, #2) -----------------


def test_a_new_folder_requested_at_install_is_the_library_from_the_first_start(
    tmp_path, payload, machine, started
):
    chosen = tmp_path / "D" / "Data" / "MyScribe"
    _request(machine, chosen)

    assert _main(payload) == 0

    [layout] = started
    assert layout.data_dir == chosen.resolve()
    assert launcher.app_environment(layout, {})["SCRIBE_DATA_DIR"] == str(chosen.resolve())
    assert layout.home == machine.default.absolute(), "the engine home moved with the library"
    assert chosen.is_dir()
    # Decided, so recorded as the library and no longer as a request.
    assert _pointer(machine) == {"data": str(chosen.resolve())}
    assert not (machine.default / "data").exists()


def test_an_empty_folder_is_used_as_it_is(tmp_path, payload, machine, started):
    chosen = tmp_path / "empty"
    chosen.mkdir()
    _request(machine, chosen)

    assert _main(payload) == 0

    [layout] = started
    assert layout.data_dir == chosen.resolve()
    assert not (chosen / "MyScribe").exists()


@pytest.mark.parametrize("left", ["home", "home/data"])
def test_the_page_left_as_it_was_prefilled_gives_todays_layout(tmp_path, payload, machine, started, left):
    """The page opens on the per-user folder. Taken as a library folder it
    would put myscribe.db beside env/ and python/; it means "as today"."""
    _request(machine, machine.default if left == "home" else machine.default / "data")

    assert _main(payload) == 0

    [layout] = started
    assert layout.data_dir == machine.default.absolute() / "data"
    assert "data" not in _pointer(machine)
    assert not (machine.default / "MyScribe").exists()


def test_a_folder_that_holds_other_things_gets_a_myscribe_subfolder(tmp_path, payload, machine, started):
    chosen = tmp_path / "Data"
    chosen.mkdir()
    (chosen / "taxes-2025.pdf").write_bytes(b"%PDF")
    _request(machine, chosen)

    assert _main(payload) == 0

    [layout] = started
    assert layout.data_dir == (chosen / "MyScribe").resolve()
    assert (chosen / "MyScribe").is_dir()
    assert sorted(p.name for p in chosen.iterdir()) == ["MyScribe", "taxes-2025.pdf"]


def test_a_myscribe_subfolder_that_already_holds_other_things_is_refused_with_a_sentence(
    tmp_path, payload, machine, started
):
    chosen = tmp_path / "Data"
    (chosen / "MyScribe").mkdir(parents=True)
    (chosen / "MyScribe" / "somebody-elses.txt").write_text("x", encoding="utf-8")
    (chosen / "other.txt").write_text("x", encoding="utf-8")
    _request(machine, chosen)

    assert _main(payload) == 0, "a configuration problem, not a crash"

    assert started == []
    said = (tmp_path / "local" / "MyScribe-launcher.log").read_text(encoding="utf-8")
    assert str(chosen / "MyScribe") in said and "holds other files" in said
    assert sorted(p.name for p in (chosen / "MyScribe").iterdir()) == ["somebody-elses.txt"]


# --- an existing library goes to the engine, numbers first (AC #3, ADR-019) ------------


@pytest.mark.parametrize("name", ["myscribe.db", "scribe.db"])
def test_a_folder_holding_a_library_is_offered_to_the_engine_and_not_used_by_the_launcher(
    tmp_path, payload, machine, started, name
):
    existing = _library(tmp_path / "Library", name)
    _request(machine, existing)

    assert _main(payload) == 0

    [layout] = started
    assert layout.data_dir == machine.default.absolute() / "data", "the launcher adopted it itself"
    assert layout.offer == existing.resolve()
    assert _pointer(machine) == {launcher.REQUEST_KEY: str(existing)}, "the request went before it was decided"


def test_a_former_home_is_recognised_by_the_library_in_its_data_folder(tmp_path, payload, machine, started):
    """`D:\\Data\\MyScribe` after TASK-098: a home, with env/ and cache/ beside
    data/myscribe.db. Read as "a folder that holds other things" it would get
    an empty MyScribe subfolder right beside the live library."""
    former = tmp_path / "D" / "Data" / "MyScribe"
    (former / "env").mkdir(parents=True)
    (former / "cache").mkdir()
    _library(former / "data")
    _request(machine, former)

    assert _main(payload) == 0

    [layout] = started
    assert layout.offer == (former / "data").resolve()
    assert not (former / "MyScribe").exists()
    assert layout.data_dir == machine.default.absolute() / "data"


def test_the_first_sitting_lists_the_offered_library_and_a_decision_drops_the_request(tmp_path, payload, monkeypatch):
    pointer = tmp_path / "MyScribe.location"
    existing = _library(tmp_path / "Library")
    pointer.write_text(json.dumps({launcher.REQUEST_KEY: str(existing)}), encoding="utf-8")
    layout = launcher.Layout(tmp_path / "home", payload, pointer=pointer, offer=existing)
    layout.logs_dir.mkdir(parents=True)
    launch = launcher.Launch(layout, 4299, False, lambda s, t: None)
    plans: list[list[str]] = []
    results = [{"data_dir": str(existing)}]

    def setup_plan(layout, report, *, unasked_only=True, named=(), on_start=None):
        plans.append(list(named))
        return {"contract": 5, "found": [], "questions": []}

    def run_streaming(command, *, env, cwd, on_line, input=None, on_start=None):
        if results:
            Path(env[launcher.SETUP_RESULT_VARIABLE]).write_text(json.dumps(results.pop(0)), encoding="utf-8")
        return 0

    monkeypatch.setattr(launcher, "setup_plan", setup_plan)
    monkeypatch.setattr(launcher, "run_streaming", run_streaming)
    monkeypatch.setattr(launcher, "run_prove", lambda *a, **k: 0)

    launcher.open_sitting(launch, lambda plan: {"library": str(existing)}, launch.report)

    assert plans[0] == [str(existing)], "the first plan did not list the folder chosen at install"
    assert json.loads(pointer.read_text(encoding="utf-8")) == {"data": str(existing)}
    assert launch.layout.data_dir == existing.absolute()


def test_a_sitting_that_started_a_new_library_instead_drops_the_request(tmp_path, payload, monkeypatch):
    pointer = tmp_path / "MyScribe.location"
    existing = _library(tmp_path / "Library")
    pointer.write_text(json.dumps({launcher.REQUEST_KEY: str(existing)}), encoding="utf-8")
    layout = launcher.Layout(tmp_path / "home", payload, pointer=pointer, offer=existing)
    layout.logs_dir.mkdir(parents=True)
    launch = launcher.Launch(layout, 4299, False, lambda s, t: None)
    monkeypatch.setattr(launcher, "setup_plan", lambda *a, **k: {"contract": 5, "found": [], "questions": []})
    monkeypatch.setattr(launcher, "run_streaming", lambda *a, **k: 0)
    monkeypatch.setattr(launcher, "run_prove", lambda *a, **k: 0)

    launcher.open_sitting(launch, lambda plan: {"library": "new"}, launch.report)

    assert json.loads(pointer.read_text(encoding="utf-8")) == {}
    assert launch.layout.data_dir == (tmp_path / "home").absolute() / "data"


def test_closing_the_sitting_keeps_the_request_for_the_next_start(tmp_path, payload, monkeypatch):
    pointer = tmp_path / "MyScribe.location"
    existing = _library(tmp_path / "Library")
    pointer.write_text(json.dumps({launcher.REQUEST_KEY: str(existing)}), encoding="utf-8")
    layout = launcher.Layout(tmp_path / "home", payload, pointer=pointer, offer=existing)
    layout.logs_dir.mkdir(parents=True)
    launch = launcher.Launch(layout, 4299, False, lambda s, t: None)
    monkeypatch.setattr(launcher, "setup_plan", lambda *a, **k: {"contract": 5, "found": [], "questions": []})

    launcher.open_sitting(launch, lambda plan: None, launch.report)

    assert json.loads(pointer.read_text(encoding="utf-8")) == {launcher.REQUEST_KEY: str(existing)}


def test_the_console_door_hands_the_offered_library_to_the_engine(tmp_path, payload, monkeypatch):
    existing = _library(tmp_path / "Library")
    layout = launcher.Layout(tmp_path / "home", payload, pointer=tmp_path / "MyScribe.location", offer=existing)
    layout.logs_dir.mkdir(parents=True)
    monkeypatch.setattr(launcher.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(launcher, "run_prove", lambda *a, **k: 0)
    commands: list[list[str]] = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda command, **k: commands.append(command) or 0)

    launcher.console_sitting(launcher.Launch(layout, 4299, False, lambda s, t: None), lambda s, t: None)

    assert commands[0][-2:] == ["--library", str(existing)]


# --- nothing is swapped under an install that is already there (AC #4) ----------------


def test_a_request_never_replaces_a_library_the_default_home_already_holds(
    tmp_path, payload, machine, started, capsys
):
    """An update of an install that never moved anything has no pointer file,
    so the page would be shown. The installer skips it there; this is the
    launcher's half, for a request that got through anyway."""
    _library(machine.default / "data")
    _request(machine, tmp_path / "elsewhere")

    assert _main(payload) == 0

    [layout] = started
    assert layout.data_dir == machine.default.absolute() / "data"
    assert not (tmp_path / "elsewhere").exists()
    assert launcher.REQUEST_KEY not in _pointer(machine)
    said = capsys.readouterr().out
    assert str(machine.default / "data") in said and str(tmp_path / "elsewhere") in said


def test_a_home_given_by_hand_ignores_the_request(tmp_path, payload, machine, started):
    _request(machine, tmp_path / "elsewhere")
    by_hand = tmp_path / "by-hand"

    assert _main(payload, "--home", str(by_hand)) == 0

    [layout] = started
    assert layout.data_dir == by_hand.absolute() / "data"
    assert not (tmp_path / "elsewhere").exists()
    assert _pointer(machine) == {launcher.REQUEST_KEY: str(tmp_path / "elsewhere")}


def test_a_request_inside_the_install_folder_is_refused_and_nothing_starts(tmp_path, payload, machine, started):
    install = tmp_path / "Programs" / "MyScribe"
    install.mkdir(parents=True)
    _request(machine, install / "library")

    assert _main(payload) == 0

    assert started == []
    assert not (install / "library").exists()
    said = (tmp_path / "local" / "MyScribe-launcher.log").read_text(encoding="utf-8")
    assert "belongs to the installer" in said


# --- the same question at a first start, on a machine with no installer page (AC #5) ---


def _locate(tmp_path, payload, machine, answers, platform="linux"):
    asked: list[str] = []
    reports: list[tuple[str, str]] = []
    queued = list(answers)

    def ask(message: str):
        asked.append(message)
        assert not machine.default.exists(), "the home was written under before the question"
        return queued.pop(0)

    settled = launcher.locate_library(
        ask, lambda s, t: reports.append((s, t)), payload, machine.pointer, machine.default,
        install=tmp_path / "Programs" / "MyScribe", volumes=[], platform=platform,
    )
    return settled, asked, reports


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_first_start_with_no_request_asks_the_library_question_with_the_same_default(
    tmp_path, payload, machine, platform
):
    chosen = tmp_path / "Volumes" / "Data" / "MyScribe"

    settled, asked, _reports = _locate(tmp_path, payload, machine, [str(chosen)], platform)

    assert settled == (chosen.resolve(), None)
    assert len(asked) == 1
    assert asked[0].startswith("Where should MyScribe keep your library?")
    assert str(machine.default) in asked[0]
    assert _pointer(machine) == {"data": str(chosen.resolve())}


def test_keeping_the_default_at_a_first_start_writes_nothing(tmp_path, payload, machine):
    settled, asked, _reports = _locate(tmp_path, payload, machine, [None])

    assert settled == (None, None) and len(asked) == 1
    assert not machine.pointer.exists()


def test_a_first_start_answer_that_holds_a_library_is_kept_as_a_request_for_the_engine(
    tmp_path, payload, machine
):
    existing = _library(tmp_path / "Library")

    settled, _asked, _reports = _locate(tmp_path, payload, machine, [str(existing)])

    assert settled == (None, existing.resolve())
    assert _pointer(machine) == {launcher.REQUEST_KEY: str(existing.resolve())}


def test_a_refused_answer_is_asked_again_with_its_sentence(tmp_path, payload, machine):
    chosen = tmp_path / "second"

    settled, asked, _reports = _locate(tmp_path, payload, machine, ["relative/path", str(chosen)])

    assert settled == (chosen.resolve(), None)
    assert len(asked) == 2 and "not a full path" in asked[1]


def test_a_request_the_installer_recorded_is_not_asked_again(tmp_path, payload, machine):
    chosen = tmp_path / "chosen"
    _request(machine, chosen)

    settled, asked, _reports = _locate(tmp_path, payload, machine, [])

    assert asked == [] and settled == (chosen.resolve(), None)


def test_a_door_where_nobody_is_at_the_screen_asks_nothing_and_says_so(tmp_path, payload, machine):
    reports: list[tuple[str, str]] = []

    settled = launcher.locate_library(None, lambda s, t: reports.append((s, t)), payload,
                                      machine.pointer, machine.default, volumes=[], platform="linux")

    assert settled == (None, None)
    assert any("nothing was asked" in text for state, text in reports)


def test_a_pointer_with_a_library_or_a_request_answers_the_question(tmp_path, payload, machine):
    _request(machine, tmp_path / "x")
    assert launcher.wants_location(machine.default, payload, machine.pointer, None, {}) is False
    machine.pointer.write_text(json.dumps({"data": str(tmp_path / "lib")}), encoding="utf-8")
    assert launcher.wants_location(machine.default, payload, machine.pointer, None, {}) is False


# --- what the installer writes is what the launcher reads (AC #7, the handoff) ---------


def test_the_pointer_the_installer_writes_reads_back_byte_for_byte(tmp_path, payload, machine, started):
    """The installer writes ASCII JSON - every backslash doubled, every
    character past 126 as \\uXXXX, CRLF line ends - so its encoding never
    matters. A BOM, should a later Inno write one, is read too."""
    chosen = tmp_path / "Bibliothe\u0301k \u00eb"
    machine.pointer.parent.mkdir(parents=True)
    escaped = "".join(c if 32 <= ord(c) <= 126 and c not in '\\"' else
                      ("\\" + c if c in '\\"' else f"\\u{ord(c):04x}") for c in str(chosen))
    machine.pointer.write_bytes(b"\xef\xbb\xbf{\r\n  \"library_request\": \"" + escaped.encode("ascii") + b"\"\r\n}\r\n")

    assert _main(payload) == 0

    [layout] = started
    assert layout.data_dir == chosen.resolve()
