"""The launcher's first-run sitting (TASK-089.15, ADR-015).

The dialog is drawn from `scribe.setup --plan` and its answers go back as one
JSON document on the child's stdin. Everything here is driven without a
window: `ask_setup`, `ask_failure` and `run_window` each do `import tkinter`
inside the function, which is the seam - a fake module in `sys.modules` holds
a whole sitting with no Tk root, no window and no display.

What that can and cannot say is worth being plain about. It proves which
widget was built, with which text, which variable and which command, and what
the sitting hands over when somebody presses a button. It says nothing about
pixels: whether the bar really moves, whether the window fits on a screen and
whether Tk shows at all under WSL are criterion 13's, and nobody has looked.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).resolve().parent.parent / "packaging" / "launcher" / "myscribe_launcher.py"
_spec = importlib.util.spec_from_file_location("myscribe_launcher", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)

SENTINEL = "hf_ThisIsNotARealToken_42"
"""The typed secret every test here looks for. Not a real credential: the
point is that it appears in no argv and in no line of the install log, so it
has to be a value a test can search for."""


@pytest.fixture(autouse=True)
def _never_the_real_home(tmp_path, monkeypatch):
    """No test here may reach the machine's own MyScribe folder.

    `home_dir()` falls through to `default_home()`, which reads LOCALAPPDATA on
    Windows and XDG_DATA_HOME or ~ elsewhere. An earlier round of this task
    created a folder in the real %LOCALAPPDATA% that way.
    """
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: tmp_path / "no-pointer.location")
    for name in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME", "MYSCRIBE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "elsewhere"))


@pytest.fixture
def layout(tmp_path):
    payload = tmp_path / "payload"
    (payload / "app" / "scribe").mkdir(parents=True)
    (payload / "bin").mkdir()
    (payload / "app" / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (payload / "app" / "scribe" / "__init__.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    (payload / "app" / "scribe" / "setup.py").write_text("CONTRACT = 7\n", encoding="utf-8")
    return launcher.Layout(tmp_path / "home", payload)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --- one plan, as `scribe.setup --plan` prints it ---------------------------------
#
# Shaped by hand rather than taken from a live engine: this file must run with
# no database, no Ollama and no network, and what it is testing is the drawing
# of the document, not the making of it. The keys are the fields of
# `scribe.setup.Question` (id, kind, text, choices, current, default, shown_if,
# if_skipped, answer_later), and tests/test_setup_plan.py is what holds the
# engine to them.


def a_plan(*questions, found=(), ollama="not installed", contract=7) -> dict:
    return {
        "contract": contract,
        "found": list(found),
        "ollama": {"note": ollama, "state": "absent"},
        "downloads": {"total_bytes": 0},
        "questions": list(questions),
    }


def a_secret(id="hf_token", shown_if=None) -> dict:
    return {
        "id": id, "kind": "secret",
        "text": "Hugging Face token, so that MyScribe can recognise speakers.",
        "choices": [], "current": "", "default": None, "shown_if": shown_if,
        "if_skipped": "Nothing is saved; a job that asks for speakers ends with a note.",
        "answer_later": "Settings > Transcription > Hugging Face token",
    }


def a_choice(id="llm_provider", current="", choices=(("ollama", "Ollama"), ("openrouter", "OpenRouter")),
             default=None) -> dict:
    return {
        "id": id, "kind": "choice", "text": "Who answers questions about a transcript?",
        "choices": [{"value": value, "label": label, "note": ""} for value, label in choices],
        "current": current, "default": default, "shown_if": None,
        "if_skipped": "No provider is chosen, so nothing is sent.",
        "answer_later": "Settings > AI providers",
    }


def a_yes_no(id="fetch_models", default="yes") -> dict:
    return {
        "id": id, "kind": "yes-no", "text": "Download the speech weights now (1.5 GiB)?",
        "choices": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}],
        "current": "", "default": default, "shown_if": None,
        "if_skipped": "The first transcription downloads them inside the job.",
        "answer_later": "python -m scribe.setup --fetch-models",
    }


A_FOUND_TOKEN = {
    "kind": "credential", "name": "huggingface", "label": "Hugging Face token",
    "found": True, "source": "HF_TOKEN in .env", "also_in": [], "conflict": False, "note": "",
}


# --- just enough tkinter ----------------------------------------------------------


class _Sitting:
    """What the fake tkinter was told to render, and what the person did."""

    def __init__(self):
        self.labels: list[str] = []
        self.options: list[tuple] = []  # (variable, value, text, command) per radio button
        self.radios: list[dict] = []
        self.entries: list = []
        self.skips: list = []  # the variable of each "Skip this question" box, in question order
        self.toggles: list[tuple] = []  # (variable, text) of every other checkbox
        self.buttons: dict = {}
        self.links: list[tuple] = []  # (url, callback)
        self.widgets: list = []
        self.bars: list = []
        self.window = None

    def pick(self, value: str) -> None:
        """Click the radio button carrying this value, as Tk would: the group's
        variable takes it and the button's command runs."""
        for variable, option, _text, command in self.options:
            if option == value:
                variable.set(value)
                if command is not None:
                    command()
                return
        raise AssertionError(f"no option {value!r} among {[o for _v, o, _t, _c in self.options]}")

    def type(self, text: str, which: int = 0) -> None:
        self.entries[which].typed = text

    def skip(self, which: int) -> None:
        """Tick the Skip of question `which`, in the order the plan listed
        them."""
        self.skips[which].set(True)

    def block_holding(self, needle: str):
        """The question block a sentence belongs to: the frame directly under
        the window that has this text somewhere inside it."""
        for widget in self.widgets:
            if needle in str(widget.kwargs.get("text", "")):
                found = widget
                while found.master is not None and found.master is not self.window:
                    found = found.master
                return found
        raise AssertionError(f"no widget carrying {needle!r}")


def _fake_tkinter(sitting: _Sitting):
    """Only what the sitting uses, and deliberately not one call more.

    Anything the dialog grows that this does not know raises AttributeError,
    so a widget that stops being rendered fails loudly here rather than
    passing quietly.
    """

    class Variable:
        def __init__(self, master=None, value=None, **kwargs):
            self._value = value

        def get(self):
            return self._value

        def set(self, value):
            self._value = value

    class Widget:
        def __init__(self, master=None, **kwargs):
            self.master = master
            self.kwargs = kwargs
            self.visible = True
            self.bindings: dict = {}
            sitting.widgets.append(self)

        def grid(self, **kwargs):
            self.visible = True

        def grid_remove(self):
            self.visible = False

        def pack(self, **kwargs):
            self.visible = True

        def columnconfigure(self, *args, **kwargs):
            pass

        def bind(self, event, callback):
            self.bindings[event] = callback

        def configure(self, **kwargs):
            self.kwargs.update(kwargs)

    class Toplevel(Widget):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            sitting.window = self

        def title(self, text):
            pass

        def transient(self, other):
            pass

        def grab_set(self):
            pass

        def destroy(self):
            pass

    class Label(Widget):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            sitting.labels.append(kwargs.get("text", ""))

        def bind(self, event, callback):
            super().bind(event, callback)
            if self.kwargs.get("cursor") == "hand2":
                sitting.links.append((self.kwargs.get("text", ""), callback))

    class Entry(Widget):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            self.typed = ""
            sitting.entries.append(self)

        def get(self):
            return self.typed

    class Radiobutton(Widget):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            sitting.options.append((kwargs["variable"], kwargs["value"], kwargs.get("text", ""),
                                    kwargs.get("command")))
            sitting.radios.append(kwargs)

    class Checkbutton(Widget):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            sitting.labels.append(kwargs.get("text", ""))
            if kwargs.get("text") == "Skip this question":
                sitting.skips.append(kwargs["variable"])
            else:
                sitting.toggles.append((kwargs["variable"], kwargs.get("text", "")))

    class Button(Widget):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            sitting.buttons[kwargs["text"]] = kwargs["command"]

    return types.SimpleNamespace(
        Toplevel=Toplevel, Label=Label, Entry=Entry, Frame=Widget, Button=Button,
        Radiobutton=Radiobutton, Checkbutton=Checkbutton,
        StringVar=Variable, BooleanVar=Variable,
    )


def hold(monkeypatch, layout, plan, *, touch=None, press="Save and start"):
    """Hold one sitting on the fake tkinter and return (answers, sitting).

    `touch` is the person: it runs while the window is open, with the sitting
    in hand, before the button is pressed. Nothing touches anything by
    default.
    """
    sitting = _Sitting()
    monkeypatch.setitem(sys.modules, "tkinter", _fake_tkinter(sitting))

    class Root:
        def wait_window(self, window):
            if touch is not None:
                touch(sitting)
            if press is not None:
                sitting.buttons[press]()

    return launcher.ask_setup(Root(), layout, plan), sitting


# --- criterion 2: the dialog is drawn from the plan -------------------------------


def test_a_token_this_machine_already_has_is_not_asked_for_again(layout, monkeypatch):
    """The fixed form always drew the token field, so somebody whose token was
    already in `.env` saw an empty box, reasonably concluded it was missing and
    went to mint a second one. The plan is what decides: a found credential is
    not an open question, and the found block says where it is - the source,
    never the value (ADR-015)."""
    plan = a_plan(a_choice(), found=[A_FOUND_TOKEN])

    answers, sitting = hold(monkeypatch, layout, plan)

    assert sitting.entries == [], "nothing may ask for a token this machine already has"
    assert "hf_token" not in answers
    assert any("Hugging Face token: HF_TOKEN in .env" in text for text in sitting.labels)
    assert not [text for text in sitting.labels if SENTINEL in text]


def test_the_radios_open_on_the_stored_value_and_on_nothing_else(layout, monkeypatch):
    """`current` is what the library already says, and it is what the group
    opens on - so somebody on OpenRouter who reopens the sitting to add a token
    is not silently moved to Ollama by pressing Save.

    Never `default`: that is what the console's Enter takes, and a window that
    filled it in would be answering for somebody (ADR-016, TASK-089.25).
    """
    plan = a_plan(a_choice(current="openrouter", default="ollama"))

    _answers, sitting = hold(monkeypatch, layout, plan, press=None)

    assert sorted({variable.get() for variable, _v, _t, _c in sitting.options}) == ["openrouter"]


def test_a_group_whose_stored_value_is_empty_opens_on_nothing(layout, monkeypatch):
    """The plan where a window could answer on somebody's behalf: nothing is
    stored yet and the engine offers a `default`. The group opens on neither -
    a preselected default is an answer nobody gave (ADR-016, TASK-089.25).

    And it still does not draw every circle filled: Tk shows a group's "mixed"
    indicator on every button while the variable holds that button's
    -tristatevalue, which defaults to the empty string - exactly what an
    unanswered question holds (photographed 2026-09-22, Tk 8.6, Windows 11).
    """
    plan = a_plan(a_choice(current="", default="ollama"))

    _answers, sitting = hold(monkeypatch, layout, plan, press=None)

    assert {variable.get() for variable, _value, _text, _command in sitting.options} == {""}, (
        "a group with nothing stored opens on nothing, and never on the plan's default")
    answerable = {value for _variable, value, _text, _command in sitting.options}
    assert sitting.radios, "the group must have been drawn"
    for radio in sitting.radios:
        assert radio.get("tristatevalue", "") not in answerable


def test_every_question_carries_its_own_skip_and_what_skipping_costs(layout, monkeypatch):
    plan = a_plan(a_secret(), a_choice(), a_yes_no())

    _answers, sitting = hold(monkeypatch, layout, plan, press=None)

    assert len(sitting.skips) == 3, "one Skip per question, and not one for the whole sitting"
    for question in plan["questions"]:
        assert any(question["if_skipped"] in text for text in sitting.labels)
        assert any(question["answer_later"] in text for text in sitting.labels)


def test_shown_if_is_the_one_condition_the_launcher_interprets(layout, monkeypatch):
    """The key for a cloud provider appears under the provider that needs it,
    and only while that is the answer (ADR-015's Must Not: no other condition)."""
    key = a_secret(id="llm_key_openrouter", shown_if={"question": "llm_provider", "equals": "openrouter"})
    plan = a_plan(a_choice(), key)
    seen: list[bool] = []

    def touch(sitting):
        seen.append(sitting.block_holding(key["if_skipped"]).visible)
        sitting.pick("openrouter")
        seen.append(sitting.block_holding(key["if_skipped"]).visible)
        sitting.type(SENTINEL)
        sitting.pick("ollama")
        seen.append(sitting.block_holding(key["if_skipped"]).visible)

    answers, _sitting = hold(monkeypatch, layout, plan, touch=touch)

    assert seen == [False, True, False], "hidden, revealed by its provider, hidden again"
    assert answers == {"llm_provider": "ollama"}, "a question nobody was shown is not in the document"


def test_a_hidden_question_that_is_revealed_again_is_answered(layout, monkeypatch):
    key = a_secret(id="llm_key_openrouter", shown_if={"question": "llm_provider", "equals": "openrouter"})

    def touch(sitting):
        sitting.pick("openrouter")
        sitting.type(SENTINEL)

    answers, _sitting = hold(monkeypatch, layout, a_plan(a_choice(), key), touch=touch)

    assert answers == {"llm_provider": "openrouter", "llm_key_openrouter": SENTINEL}


def test_the_plan_with_nothing_open_still_shows_what_was_found(layout, monkeypatch):
    answers, sitting = hold(monkeypatch, layout, a_plan(found=[A_FOUND_TOKEN]))

    assert answers == {}
    assert launcher.NOTHING_IS_OPEN in sitting.labels
    assert any("Hugging Face token: HF_TOKEN in .env" in text for text in sitting.labels)


def test_the_found_block_names_a_source_and_never_a_value():
    """A pure read of the same rows, so the rule can be asserted over a
    conflicting credential too: two places, one used, and neither shown."""
    rows = [
        dict(A_FOUND_TOKEN, also_in=["the registry"], conflict=True),
        {"kind": "credential", "name": "openai", "label": "OpenAI key", "found": False,
         "source": "", "also_in": [], "conflict": False, "note": ""},
        {"kind": "proxy", "name": "HTTPS_PROXY", "label": "HTTPS_PROXY", "found": True,
         "source": "the environment", "also_in": [], "conflict": False, "note": "",
         "host": "proxy.example:8080"},
    ]

    lines = launcher.found_lines(a_plan(found=rows, ollama="running, 3 models"))

    assert lines == [
        "Hugging Face token: HF_TOKEN in .env",
        "    also the registry - which defines a different value, not used",
        "OpenAI key: not found",
        "HTTPS_PROXY: proxy.example:8080 (the environment)",
        "Ollama: running, 3 models",
    ]


# --- criterion 4: Save stamps the sitting, "Ask me next time" does not ------------


def test_save_with_every_question_skipped_is_still_a_sitting(layout, monkeypatch):
    """The one that must not regress: `answers or None` turned a sitting in
    which everything was skipped into "ask me next time", so no child ran, the
    engine wrote no stamp and the gate opened again at every start."""
    def touch(sitting):
        for variable in sitting.skips:
            variable.set(True)

    answers, _sitting = hold(monkeypatch, layout, a_plan(a_secret(), a_choice()), touch=touch)

    assert answers == {"hf_token": None, "llm_provider": None}, "skipped, and recorded as skipped"


def test_ask_me_next_time_hands_over_nothing_at_all(layout, monkeypatch):
    answers, sitting = hold(monkeypatch, layout, a_plan(a_choice()), press="Ask me next time")

    assert answers is None
    assert set(sitting.buttons) == {"Save and start", "Ask me next time"}


def test_a_closed_window_is_ask_me_next_time(layout, monkeypatch):
    answers, _sitting = hold(monkeypatch, layout, a_plan(a_choice()), press=None)

    assert answers is None


def test_a_skipped_sitting_still_runs_the_child_and_lets_the_engine_stamp(layout, monkeypatch, tmp_path):
    """The other half of the same claim, one level up: an empty answer set is
    still applied, because it is the engine that writes the stamp and a start
    that never runs the child is a gate that never closes (TASK-089.11)."""
    record = tmp_path / "child.json"
    _engine_records(layout, monkeypatch, record)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))

    code, reopen = launch.apply({})

    assert code == 0 and reopen == []
    assert json.loads(record.read_text(encoding="utf-8"))["stdin"]
    assert json.loads(json.loads(record.read_text(encoding="utf-8"))["stdin"]) == {
        "contract": 7, "answers": {}}


def test_default_diarize_is_in_no_document_and_no_argv(layout, monkeypatch):
    """W6, and ADR-015's Must Not: setup never writes `default_diarize` by
    itself, and the launcher has no flag left that could ask it to. The
    scripted `--diarize/--no-diarize` is the engine's own door and is not this
    one's."""
    plan = a_plan(a_secret(), a_choice(), a_yes_no())

    answers, _sitting = hold(monkeypatch, layout, plan)
    document = launcher.setup_document(layout, answers)
    command = launcher.setup_command(layout)

    assert "default_diarize" not in json.dumps(document)
    assert "--diarize" not in command and "--no-diarize" not in command
    assert [question["id"] for question in plan["questions"]] == list(answers)


def test_a_yes_no_question_opens_on_the_plan_s_default(layout, monkeypatch):
    """The one place the plan's `default` is rendered rather than `current`: a
    checkbox has no unanswered state, and the question it asks writes no
    setting row - it decides only whether the download happens now, while
    somebody is watching, or inside the first transcription. Robert ratified
    that reading on 2026-09-22 (TASK-089.25) and it is kept here."""
    answers, _sitting = hold(monkeypatch, layout, a_plan(a_yes_no(default="yes")))
    assert answers == {"fetch_models": "yes"}

    answers, _sitting = hold(monkeypatch, layout, a_plan(a_yes_no(default="no")))
    assert answers == {"fetch_models": "no"}


# --- criterion 5: the progress lines drive a bar, not the log ---------------------


@pytest.mark.parametrize("line", [
    '{"event": "progress", "repo": "openai/whisper", "percent": 42, "bytes": 1, "total": 2}',
    '  {"event":"progress","repo":"r","percent":0}  ',
])
def test_a_progress_line_is_recognised(line):
    assert launcher.progress_line(line) is not None


@pytest.mark.parametrize("line", [
    "downloading openai/whisper",
    "",
    "{not json at all",
    '{"event": "done", "repo": "r"}',
    '["event", "progress"]',
    '{"percent": 42}',
])
def test_a_line_of_prose_is_not_a_progress_line(line):
    assert launcher.progress_line(line) is None


def test_the_headline_names_the_repository_being_downloaded():
    event = launcher.progress_line('{"event": "progress", "repo": "openai/whisper", "percent": 100}')
    assert launcher.progress_headline(event) == "Downloading openai/whisper - 100%"


def test_a_hundred_progress_lines_are_a_bar_and_never_a_hundred_log_lines(layout, monkeypatch, tmp_path):
    """The measured shape of the failure: the engine prints one JSON line per
    whole percent per repository, so a 1.6 GB install is hundreds of them. As
    log lines they push everything that says something out of the window."""
    record = tmp_path / "child.json"
    says = [json.dumps({"event": "progress", "repo": "openai/whisper", "percent": percent})
            for percent in range(101)]
    says.insert(50, "fetching openai/whisper")
    says.append("saved: nothing")
    _engine_records(layout, monkeypatch, record, says=says)
    layout.logs_dir.mkdir(parents=True)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))

    code, _reopen = launch.apply({})

    assert code == 0
    progress = [text for state, text in reports if state == "progress"]
    prose = [text for state, text in reports if state == "busy"]
    assert len(progress) == 101
    assert prose == ["fetching openai/whisper", "saved: nothing"]
    assert launcher.progress_headline(launcher.progress_line(progress[-1])) == \
        "Downloading openai/whisper - 100%"
    written = (layout.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8").splitlines()
    assert written == prose, "a bar is what those lines are for; the log keeps the sentences"


# --- criterion 6: a failure is a state with two things to do ----------------------


@pytest.mark.parametrize("code,expected", [
    (3, launcher.GATED_MODEL),
    (2, launcher.CONTRACT_MISMATCH),
    (4, launcher.NO_ROOM),
])
def test_each_exit_code_has_its_own_sentence(code, expected):
    assert launcher.setup_failure(code) == expected


def test_an_exit_code_nobody_named_still_says_something_useful():
    sentence = launcher.setup_failure(9)
    assert "9" in sentence and "Retry" in sentence


def test_a_reopen_is_a_failure_although_the_child_exited_zero():
    sentence = launcher.setup_failure(0, ["hf_token"])
    assert "hf_token" in sentence


def test_a_sitting_that_worked_says_nothing():
    assert launcher.setup_failure(0) == ""


def test_the_token_refusal_is_not_among_the_sentences():
    """Exit 2 is overloaded in `scribe.setup`: the `--hf-token` refusal, an
    argparse usage error, a contract mismatch and models' `mismatch`. After
    this task the launcher can no longer provoke the first - it builds no such
    flag - so the sentence must name the two it can."""
    assert "token" not in launcher.CONTRACT_MISMATCH.lower()
    # The flag itself, as ADR-015's Enforcement matches it: a string literal.
    # The launcher's prose still names it, because that is the history the
    # comments there exist to record.
    assert re.findall(r"""["']--[a-z0-9-]*(?:token|key|secret|password)[a-z0-9-]*["']""",
                      LAUNCHER_PATH.read_text(encoding="utf-8")) == []


def test_the_error_state_has_retry_and_continue_without(monkeypatch):
    sitting = _Sitting()
    monkeypatch.setitem(sys.modules, "tkinter", _fake_tkinter(sitting))

    class Root:
        def wait_window(self, window):
            pass

    assert launcher.ask_failure(Root(), launcher.GATED_MODEL) is False
    assert set(sitting.buttons) == {"Retry", "Continue without"}
    assert launcher.GATED_MODEL in sitting.labels
    assert [url for url, _callback in sitting.links] == [launcher.CONDITIONS_URL]


def test_retry_says_so(monkeypatch):
    sitting = _Sitting()
    monkeypatch.setitem(sys.modules, "tkinter", _fake_tkinter(sitting))

    class Root:
        def wait_window(self, window):
            sitting.buttons["Retry"]()

    assert launcher.ask_failure(Root(), launcher.NO_ROOM) is True
    assert [url for url, _callback in sitting.links] == [], "no conditions link on a full disk"


def test_retry_holds_the_whole_sitting_again(layout, monkeypatch, tmp_path):
    """Retry runs the sitting from the top - plan, ask, apply - and Continue
    without ends it. That it then starts MyScribe is `first_run`'s half and is
    asserted there (tests/test_launcher.py, the failed-setup tests: the app
    starts whatever the answers did)."""
    record = tmp_path / "child.json"
    _engine_records(layout, monkeypatch, record, exit_code=3)
    layout.logs_dir.mkdir(parents=True)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    monkeypatch.setattr(launcher, "setup_plan", lambda *a, **k: a_plan(a_choice()))
    monkeypatch.setattr(launcher, "run_prove", lambda *a, **k: 0)
    held: list[int] = []
    again = iter([True, False])

    launcher.open_sitting(launch, lambda plan: held.append(1) or {}, launch.report,
                          retry=lambda _sentence: next(again))

    assert held == [1, 1], "Retry held the sitting a second time"
    assert [text for state, text in reports if state == "error"] == [launcher.GATED_MODEL] * 2


# --- criterion 7: every reported line is teed, with the secret gone ---------------


def test_the_typed_secret_is_in_no_argv_and_in_no_line_of_the_install_log(
        layout, monkeypatch, tmp_path):
    """ADR-015's Must Not, measured end to end: a real child, its real argv,
    and the file this task creates.

    The sentinel goes in as an answer, and the child echoes it back on its own
    stdout the way a badly behaved engine would - so this also proves the
    redaction, not only that the launcher never says it itself.
    """
    record = tmp_path / "child.json"
    _engine_records(layout, monkeypatch, record, says=[f"the token was {SENTINEL}", "saved: nothing"])
    layout.logs_dir.mkdir(parents=True)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    plan = a_plan(a_secret())

    launch.remember(plan, {"hf_token": SENTINEL})
    code, _reopen = launch.apply({"hf_token": SENTINEL})

    assert code == 0
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert not [word for word in seen["argv"] if SENTINEL in word], seen["argv"]
    assert SENTINEL in seen["stdin"], "the answer did reach the engine - on stdin"
    written = (layout.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8")
    assert SENTINEL not in written
    assert launcher.REDACTED in written
    assert not [text for _state, text in reports if SENTINEL in text], "nor on the screen"


def test_every_reported_line_is_in_the_file_in_order(layout):
    layout.logs_dir.mkdir(parents=True)
    launch = launcher.Launch(layout, _free_port(), False, lambda _s, _t: None)

    for state, text in (("status", "one"), ("busy", "two"), ("error", "three"), ("running", "four")):
        launch.report(state, text)

    assert (layout.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8").splitlines() == \
        ["one", "two", "three", "four"]


def test_nothing_is_created_under_a_home_that_was_only_looked_at(layout):
    """A MyScribe that already serves and a volume with too little room both
    end before `prepare_home`, and a log file is not a reason to build a
    folder under either. The lines wait and are written once there is
    somewhere to put them."""
    launch = launcher.Launch(layout, _free_port(), False, lambda _s, _t: None)

    launch.report("status", "before the home exists")

    assert not layout.home.exists()
    launcher.prepare_home(layout)
    launch.report("status", "and after")
    assert (layout.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8").splitlines() == \
        ["before the home exists", "and after"]


def test_an_empty_secret_does_not_mark_every_character(layout):
    """`"".replace("", "***")` puts the mark between every character of every
    line, which would make the whole log unreadable for every sitting in which
    a secret was skipped."""
    log = launcher.InstallLog(layout.logs_dir / launcher.INSTALL_LOG)
    log.hide("")

    assert log.redact("a plain line") == "a plain line"


def test_a_log_that_cannot_be_written_stops_nothing(layout, monkeypatch):
    layout.logs_dir.mkdir(parents=True)
    launch = launcher.Launch(layout, _free_port(), False, lambda _s, _t: None)

    def refuse(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr("builtins.open", refuse)
    launch.report("status", "said anyway")  # must not raise


# --- criterion 8: the Setup button, and the proof that ends a sitting -------------


def test_the_setup_route_asks_for_the_whole_plan(layout, monkeypatch):
    """A question recorded as skipped is asked again in a sitting somebody
    opened on purpose, and never by a start: `--unasked-only` is the
    difference, and ADR-015's Must says which door gets which."""
    asked: list[bool] = []
    monkeypatch.setattr(
        launcher, "setup_plan",
        lambda _layout, _report, unasked_only=True, on_start=None: asked.append(unasked_only) or None)
    launch = launcher.Launch(layout, _free_port(), False, lambda _s, _t: None)

    launcher.open_sitting(launch, lambda plan: None, launch.report, force_setup=True)
    launcher.open_sitting(launch, lambda plan: None, launch.report, force_setup=False)

    assert asked == [False, True]


def test_a_plan_is_read_although_the_child_said_something_around_it(layout, monkeypatch, tmp_path):
    """The child's stderr shares this pipe. A warning printed after the
    document is as likely as one before it - `warnings`, or an "Exception
    ignored in:" at interpreter shutdown - and either used to end the sitting
    before it opened, reported as "could not be read (exit 0)", which reads as
    a contradiction. The plan is read out of the middle.

    And out of the middle means the plan and not the first thing that decodes:
    one of the lines here is itself a JSON object, and taking that one would
    open a sitting saying nothing is open and stamp questions nobody was
    asked."""
    plan = a_plan(a_choice())
    script = tmp_path / "noisy_plan.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys
        print("warning: a {{brace}} in the prose before it", file=sys.stderr, flush=True)
        print(json.dumps({{"level": "warning", "text": "and a whole JSON object"}}),
              file=sys.stderr, flush=True)
        print(json.dumps({plan!r}, indent=2), flush=True)
        print("Exception ignored in: <_io.TextIOWrapper>", file=sys.stderr, flush=True)
        """), encoding="utf-8")
    monkeypatch.setattr(launcher, "plan_command",
                        lambda _layout, **_kwargs: [sys.executable, str(script)])
    reports: list[tuple[str, str]] = []

    read = launcher.setup_plan(layout, lambda state, text: reports.append((state, text)))

    assert read == plan
    assert [state for state, _text in reports] == [], "a plan that was read says nothing"


def test_the_plan_command_carries_unasked_only_only_when_it_should(layout):
    assert launcher.plan_command(layout, unasked_only=True) == [
        str(layout.env_python), "-m", "scribe.setup", "--plan", "--unasked-only"]
    assert launcher.plan_command(layout, unasked_only=False) == [
        str(layout.env_python), "-m", "scribe.setup", "--plan"]


def test_the_proof_is_told_which_port_myscribe_answers_on(layout):
    """ADR-001: the card stays the runner's. While MyScribe serves, the engine
    queues the doctor job instead of loading a model in the setup child - and
    it can only know that if it is told the port this launcher uses, which is
    not always 4242 (`--port 4299`).

    The other half of that claim is the engine's and is measured there:
    tests/test_setup_prove.py::test_an_app_serving_this_library_gets_the_doctor_job
    and ::test_the_gpu_runtime_line_waits_on_the_same_gate, which drive a model
    loader that raises.
    """
    assert launcher.prove_command(layout, 4299) == [
        str(layout.env_python), "-m", "scribe.setup", "--prove", "--port", "4299"]


def test_a_proof_that_exits_non_zero_is_a_report_and_not_a_failure(layout, monkeypatch, tmp_path):
    """Transcription is a required line and reads "not tested" while the app
    answers, so `--prove` exits 1 by design on exactly the route the Setup
    button takes (TASK-089.13, step 14). If that flowed into the error state,
    every sitting opened from the button would end in "Retry"."""
    record = tmp_path / "child.json"
    _engine_records(layout, monkeypatch, record, exit_code=1, says=["transcription  not tested (app running)"])
    layout.logs_dir.mkdir(parents=True)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    monkeypatch.setattr(launcher, "setup_plan", lambda *a, **k: a_plan())
    monkeypatch.setattr(launcher, "run_setup", lambda *a, **k: 0)
    retried: list[str] = []

    launcher.open_sitting(launch, lambda plan: {}, launch.report, force_setup=True,
                          retry=lambda sentence: retried.append(sentence) or True)

    assert retried == [], "a proof is information; only a failed apply asks to retry"
    assert [text for state, text in reports if state == "error"] == []
    assert any("not tested (app running)" in text for _state, text in reports)


def test_the_window_has_a_setup_button_beside_the_other_three(layout, monkeypatch):
    """And pressing it asks for the whole plan. The label alone would pass over
    a button wired to `force_setup=False`, which is the start's plan and leaves
    a question somebody skipped unasked for ever (TASK-089.11 records the skip;
    this button is the one route back to it)."""
    window = _run_window(layout, monkeypatch)

    assert set(window.buttons) == {"Open MyScribe", "Open data folder", "Setup", "Quit"}
    assert set(window.protocols) == {"WM_DELETE_WINDOW"}

    window.buttons["Setup"]()

    # The button hands the sitting to a worker thread, so wait for it rather
    # than read the list at once.
    assert _wait_for(lambda: window.sittings, 10), "the Setup button opened no sitting"
    assert window.sittings[0]["force_setup"] is True


# --- criterion 9: the console door ------------------------------------------------


def test_a_terminal_is_handed_to_the_engine_whole(layout, monkeypatch):
    """No document and no pipe: `python -m scribe.setup` with stdin and stdout
    inherited, so the engine's own asker runs with `getpass` for a secret.
    Two askers kept in step is what ADR-015 exists to prevent."""
    monkeypatch.setattr(launcher.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    started: list[list[str]] = []
    monkeypatch.setattr(launcher.subprocess, "call",
                        lambda command, **kwargs: started.append(list(command)) or 0)
    monkeypatch.setattr(launcher, "run_prove", lambda *a, **k: 0)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))

    launcher.console_sitting(launch, launch.report)

    assert started == [[str(layout.env_python), "-m", "scribe.setup"]]
    assert "--apply-stdin" not in started[0]


def test_without_a_terminal_one_line_says_so_and_no_child_is_started(layout, monkeypatch):
    monkeypatch.setattr(launcher.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    started: list = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **k: started.append(a) or 0)
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))

    launcher.console_sitting(launch, launch.report)

    assert started == []
    assert [text for _state, text in reports] == [launcher.NOTHING_WAS_ASKED]
    assert "--apply-stdin" in launcher.NOTHING_WAS_ASKED


# --- criterion 10: Quit stops the setup child, and that one process ---------------


def test_quit_during_a_download_stops_the_setup_child_alone(layout, monkeypatch, tmp_path):
    """ADR-017's M10. Quit terminates the setup child - `terminate()`, one
    process - and never the tree kill the app gets, so that no tree kill can
    pass over an Ollama a third-party installer has just started.

    What a single-process stop can orphan is said in those words: at most a
    version probe, which ends by itself within the 15 s timeout of
    `scribe/doctor.py`'s `_run`. The grandchild here is that probe, and the
    test polls to the ceiling rather than sleeping it.

    The slow test of this file, and deliberately so: the claim is about what
    is still running fifteen seconds later.
    """
    pids = tmp_path / "pids.json"
    child = tmp_path / "child.py"
    child.write_text(textwrap.dedent(f"""
        import json, subprocess, sys, time
        probe = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
        json.dump({{"child": __import__("os").getpid(), "probe": probe.pid}},
                  open({str(pids)!r}, "w"))
        print("downloading", flush=True)
        time.sleep(60)
        """), encoding="utf-8")
    monkeypatch.setattr(launcher, "setup_command", lambda _layout: [sys.executable, str(child)])
    layout.logs_dir.mkdir(parents=True)
    killed: list = []
    real_run = launcher.subprocess.run
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda command, **kwargs: killed.append(list(command)) or real_run(command, **kwargs))
    launch = launcher.Launch(layout, _free_port(), False, lambda _s, _t: None)

    worker = threading.Thread(target=lambda: launch.apply({}), daemon=True)
    worker.start()
    _wait_for(lambda: pids.exists(), 30)
    seen = json.loads(pids.read_text(encoding="utf-8"))

    launch.stop()
    worker.join(timeout=30)

    assert not worker.is_alive(), "the setup child was not stopped"
    assert not [command for command in killed if command and command[0] == "taskkill"], (
        "a tree kill here could pass over an Ollama the installer just started (ADR-017)")
    assert not _alive(seen["child"]), "the child itself goes at once"
    assert _wait_for(lambda: not _alive(seen["probe"]), 15), (
        "a probe left behind must end by itself inside doctor._run's 15 s ceiling")


def _a_child_that_waits(tmp_path, name: str) -> tuple[Path, Path]:
    """A real python child that says its pid and then does nothing for a
    minute, so that a test can Quit while it runs."""
    pid_file = tmp_path / f"{name}.pid"
    script = tmp_path / f"{name}.py"
    script.write_text(textwrap.dedent(f"""
        import os, time
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        print("working", flush=True)
        time.sleep(60)
        """), encoding="utf-8")
    return script, pid_file


def _quit_while(launch, work, pid_file) -> tuple[int, bool]:
    """Run `work` on a worker, Quit once its child has said its pid, and give
    back that pid and whether the worker came home."""
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    assert _wait_for(lambda: pid_file.exists(), 30), "the child never started"
    pid = int(pid_file.read_text(encoding="utf-8"))
    launch.stop()
    worker.join(timeout=30)
    return pid, not worker.is_alive()


def test_quit_while_the_plan_is_being_read_stops_that_child_too(layout, monkeypatch, tmp_path):
    """`--plan` is a setup child as much as `--apply-stdin` is, and ADR-017's
    Must is about the setup child and not about the download.

    A cold plan child costs about four seconds (measured 2026-09-22, in
    `setup_plan`'s own docstring), and Quit inside those four seconds used to
    leave it running: only `Launch.apply` registered its child.
    """
    script, pid_file = _a_child_that_waits(tmp_path, "plan")
    monkeypatch.setattr(launcher, "plan_command",
                        lambda _layout, **_kwargs: [sys.executable, str(script)])
    layout.logs_dir.mkdir(parents=True)
    killed = _watch_for_a_tree_kill(monkeypatch)
    launch = launcher.Launch(layout, _free_port(), False, lambda _s, _t: None)

    pid, came_home = _quit_while(
        launch, lambda: launcher.open_sitting(launch, lambda _plan: None, launch.report), pid_file)

    assert came_home, "the plan child was not stopped"
    assert killed == [], "a tree kill here could pass over an Ollama (ADR-017)"
    assert not _alive(pid)


def test_quit_during_the_machine_check_stops_the_proof_child_too(layout, monkeypatch, tmp_path):
    """The proof child is the one that matters most. On a first run nothing
    answers the port yet, so the engine's own gate lets that child load a model
    (`scribe/setup.py` `_gate`: no answer on the port means `may_load`) - which
    is what the proof is for. A Quit that could not reach it therefore left a
    python holding the card behind a launcher that had already gone.

    That gate is not broken and is not this file's to test: while MyScribe
    answers, the same child queues the doctor job instead (TASK-089.13).
    """
    script, pid_file = _a_child_that_waits(tmp_path, "prove")
    monkeypatch.setattr(launcher, "prove_command",
                        lambda _layout, _port: [sys.executable, str(script)])
    monkeypatch.setattr(launcher, "setup_plan", lambda *a, **k: a_plan())
    monkeypatch.setattr(launcher, "run_setup", lambda *a, **k: 0)
    layout.logs_dir.mkdir(parents=True)
    killed = _watch_for_a_tree_kill(monkeypatch)
    launch = launcher.Launch(layout, _free_port(), False, lambda _s, _t: None)

    pid, came_home = _quit_while(
        launch, lambda: launcher.open_sitting(launch, lambda _plan: {}, launch.report), pid_file)

    assert came_home, "the proof child was not stopped"
    assert killed == [], "a tree kill here could pass over an Ollama (ADR-017)"
    assert not _alive(pid)


def _watch_for_a_tree_kill(monkeypatch) -> list:
    """Every `taskkill` the launcher runs while a test holds this list."""
    seen: list = []
    real_run = launcher.subprocess.run

    def recorded(command, **kwargs):
        if command and command[0] == "taskkill":
            seen.append(list(command))
        return real_run(command, **kwargs)

    monkeypatch.setattr(launcher.subprocess, "run", recorded)
    return seen


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_for(condition, seconds: float) -> bool:
    """Poll rather than sleep: this file already stalls intermittently on
    Windows, and a test that sleeps its whole ceiling makes that worse."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.25)
    return condition()


# --- criterion 11: the link is clickable, and the literals are gone ---------------


def test_the_conditions_link_opens_its_url_when_clicked(layout, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    def touch(sitting):
        url, click = sitting.links[0]
        assert url == launcher.CONDITIONS_URL
        click(None)

    hold(monkeypatch, layout, a_plan(a_secret()), touch=touch)

    assert opened == [launcher.CONDITIONS_URL]


def test_the_link_is_drawn_as_one(monkeypatch):
    """A plain Label with a link's colour and pointer, and `webbrowser.open`
    bound to a click - the old one was neither clickable nor selectable."""
    sitting = _Sitting()
    monkeypatch.setitem(sys.modules, "tkinter", _fake_tkinter(sitting))

    made = launcher.link_label(None, "https://example.test")

    assert made.kwargs["cursor"] == "hand2"
    assert made.kwargs["fg"] == "#0645ad"
    assert "<Button-1>" in made.bindings


def test_the_hard_coded_provider_tuple_and_the_size_literal_are_gone():
    """Both were written into this file and agreed with nothing: the provider
    list was a tuple beside the app's own, and "1.6 GB" was a number nobody
    measured. The sizes now come from the plan's questions and from
    `download_note`."""
    source = LAUNCHER_PATH.read_text(encoding="utf-8")

    assert "1.6 GB" not in source
    assert "openrouter" not in source, "the provider list is the app's, and it is in the plan"
    assert "Maximaal" not in source and "Turbo - fast" not in source
    # `TIER = "turbo"` stays and is not this tuple: it is which weights an
    # install fetches by default, used by the disk estimate of TASK-089.14,
    # and the drift test beside it is what keeps it the same word as
    # `scribe.models.DEFAULT_TIER`.
    assert 'TIER = "turbo"' in source


# --- the window, on the same seam -------------------------------------------------


class _Window(_Sitting):
    """A `run_window` held open without Tk: the same recorder, plus the queue
    of `after` callbacks and the widgets only the window builds."""

    def __init__(self):
        super().__init__()
        self.status = None
        self.appended: list[str] = []
        self.scheduled: list = []
        self.bar = None
        self.protocols: dict = {}
        self.destroyed = False


def _fake_window_tkinter(window: _Window):
    base = _fake_tkinter(window)

    class Root(base.Toplevel):
        def geometry(self, spec):
            pass

        def protocol(self, name, callback):
            window.protocols[name] = callback

        def after(self, delay, callback=None):
            if callback is not None:
                window.scheduled.append(callback)

        def update_idletasks(self):
            pass

        def mainloop(self):
            """Drain what is scheduled until the sequence is done.

            A real mainloop runs until Quit, and `pump` reschedules itself for
            ever, so the stopping rule here is the launcher's own worker
            thread: keep pumping while it is alive, then a few rounds more to
            flush what it last put on the queue. Bounded at 30 s, because a
            test that hangs is worse than a test that fails.
            """
            deadline = time.monotonic() + 30
            after_the_worker = 0
            while after_the_worker < 5 and time.monotonic() < deadline:
                due, window.scheduled = window.scheduled, []
                for callback in due:
                    callback()
                working = [thread for thread in threading.enumerate()
                           if thread.name.startswith("myscribe-") and thread.is_alive()]
                after_the_worker = 0 if working else after_the_worker + 1
                time.sleep(0.02)

        def destroy(self):
            window.destroyed = True

    class Text(base.Label):
        def insert(self, where, text):
            window.appended.append(text.rstrip("\n"))

        def see(self, where):
            pass

    class Bar(base.Label):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            self.value = 0
            self.mapped = False
            window.bar = self

        def __setitem__(self, key, value):
            self.value = value

        def winfo_ismapped(self):
            return self.mapped

        def pack(self, **kwargs):
            self.mapped = True

        def pack_forget(self):
            self.mapped = False

    class Label(base.Label):
        def __init__(self, master=None, **kwargs):
            super().__init__(master, **kwargs)
            if "textvariable" in kwargs:
                window.status = kwargs["textvariable"]

    fake = types.SimpleNamespace(**vars(base))
    fake.Tk = Root
    fake.Label = Label
    fake.scrolledtext = types.SimpleNamespace(ScrolledText=Text)
    fake.ttk = types.SimpleNamespace(Progressbar=Bar)
    fake.TclError = RuntimeError
    return fake


def _run_window(layout, monkeypatch):
    """Build the window, let its pump run, and give the recorder back.

    `first_run` is replaced: what is under test here is the window, and a real
    sequence on a worker thread would make this a race.
    """
    window = _Window()
    fake = _fake_window_tkinter(window)
    monkeypatch.setitem(sys.modules, "tkinter", fake)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake.ttk)
    monkeypatch.setitem(sys.modules, "tkinter.scrolledtext", fake.scrolledtext)
    monkeypatch.setattr(launcher, "first_run", lambda *a, **k: True)
    window.sittings = []  # the keywords of every open_sitting a button asked for
    monkeypatch.setattr(launcher, "open_sitting",
                        lambda *a, **k: window.sittings.append(k))

    launcher.run_window(layout, _free_port(), False)
    return window


def test_a_progress_event_moves_the_bar_and_never_the_log(layout, monkeypatch):
    """What the harness can say: the window builds a bar, gives it the
    percent, puts the repository in the headline and appends nothing. What it
    cannot say is whether the bar moves on a screen - that is criterion 13's,
    and nobody has looked."""
    window = _Window()
    fake = _fake_window_tkinter(window)
    monkeypatch.setitem(sys.modules, "tkinter", fake)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake.ttk)
    monkeypatch.setitem(sys.modules, "tkinter.scrolledtext", fake.scrolledtext)
    monkeypatch.setattr(launcher, "open_sitting", lambda *a, **k: None)

    def say_some(launch, _ask, report, *args, **kwargs):
        for percent in (0, 50, 100):
            report("progress", json.dumps(
                {"event": "progress", "repo": "openai/whisper", "percent": percent}))
        report("busy", "saved: nothing")
        return True

    monkeypatch.setattr(launcher, "first_run", say_some)

    launcher.run_window(layout, _free_port(), False)

    assert window.bar is not None and window.bar.value == 100
    assert window.appended == ["saved: nothing"], "a hundred bar updates are not a hundred lines"
    assert window.status.get() == "Downloading openai/whisper - 100%"


# --- the engine stand-in ----------------------------------------------------------


def _engine_records(layout, monkeypatch, record: Path, *, contract: int = 7, exit_code: int = 0,
                    says=()) -> None:
    """A `scribe.setup` in the payload that writes down its argv and its stdin.

    `env_python` is the python uv makes, which nothing here can build, so the
    property is pointed at the interpreter running the tests. What is under
    test stays real: a real child process, a real argv and a real pipe.
    """
    body = textwrap.dedent(
        f"""
        \"\"\"Not the engine: it records what the launcher gave it.\"\"\"

        CONTRACT = {contract}

        import json, sys
        from pathlib import Path

        Path({str(record)!r}).write_text(
            json.dumps({{"argv": sys.argv, "stdin": sys.stdin.read()}}), encoding="utf-8")
        for line in {list(says)!r}:
            print(line, flush=True)
        raise SystemExit({exit_code})
        """
    )
    (layout.app_dir / "scribe" / "setup.py").write_text(body, encoding="utf-8")
    monkeypatch.setattr(launcher.Layout, "env_python", property(lambda self: Path(sys.executable)))


# --- criterion 1: the whole first run, in one order -------------------------------


def test_the_first_run_is_one_sequence_in_one_order(layout, tmp_path, monkeypatch):
    """Location, tools, sync, plan, sitting, apply, prove, start - and every
    one of them an injected or replaceable callable, so the order is a list a
    test can read rather than a shape buried in a window.

    Driven through `main()` and not through `first_run`, because two of the
    steps are not `first_run`'s: where everything goes is settled in `main`
    before a Layout exists (TASK-089.14), and the window is what turns the
    rest into a worker thread. `--home` and the four home variables are both
    set: the fixture stops the pointer being read, and `default_home` would
    otherwise reach the real per-user folder.
    """
    order: list[str] = []
    window = _Window()
    fake = _fake_window_tkinter(window)
    monkeypatch.setitem(sys.modules, "tkinter", fake)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", fake.ttk)
    monkeypatch.setitem(sys.modules, "tkinter.scrolledtext", fake.scrolledtext)
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)

    def step(name, answer):
        def recorded(*_args, **_kwargs):
            order.append(name)
            return answer() if callable(answer) else answer

        return recorded

    monkeypatch.setattr(launcher, "locate_home", step("location", lambda: layout.home))
    monkeypatch.setattr(launcher, "install_tools", step("tools", None))
    monkeypatch.setattr(launcher, "sync", step("sync", True))
    monkeypatch.setattr(launcher, "setup_plan", step("plan", lambda: a_plan(a_choice())))
    monkeypatch.setattr(launcher, "ask_setup", step("sitting", dict))
    monkeypatch.setattr(launcher, "run_setup", step("apply", 0))
    monkeypatch.setattr(launcher, "run_prove", step("prove", 0))

    class _App:
        def __init__(self, _layout, _port, _base=None):
            self.proc = object()

        def start(self):
            order.append("start")

        def wait_ready(self, timeout=None):
            return True

        def alive(self):
            return True

        def stop(self, timeout=None):
            pass

    monkeypatch.setattr(launcher, "AppProcess", _App)

    assert launcher.main(["--home", str(layout.home), "--payload", str(layout.payload),
                          "--port", str(_free_port()), "--no-browser"]) == 0

    assert order == ["location", "tools", "sync", "plan", "sitting", "apply", "prove", "start"]
