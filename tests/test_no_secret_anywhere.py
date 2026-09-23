"""One secret, through every door: it never rides on argv and is never printed.

TASK-089 criterion 6. The rule is ADR-015's Must Not - a secret never goes on
a command line, into setup.json, into a log or into printed output - and until
this file it was checked door by door: the launcher's sitting in
tests/test_launcher_sitting.py, the engine's refusal in tests/test_setup.py,
the plan in tests/test_setup_plan.py. Each of those is right and none of them
sees the other two. This one plants one marker and follows it from the three
places a person can hand MyScribe a secret - `python install.py --answers`,
the frozen launcher's window, and `python -m scribe.setup --apply-stdin` -
to everything they emit.

Where the secret is allowed to end up is exactly two places: the stdin of the
engine, and the library's own settings row that the engine writes. Everything
else is searched.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from scribe import credentials, paths, setup

REPO = Path(__file__).resolve().parents[1]
SENTINEL = "myscribe-sentinel-4242-never-on-argv"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = _load("install_under_test", REPO / "install.py")
launcher = _load("launcher_under_test", REPO / "packaging" / "launcher" / "myscribe_launcher.py")


def _text_files_under(root: Path, skip: tuple[str, ...]) -> dict[Path, str]:
    """Every file under `root` that reads as text, except the ones named."""
    found = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith(skip):
            continue
        try:
            found[path] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return found


def _settings(db: Path) -> dict[str, str]:
    connection = sqlite3.connect(db)
    try:
        return dict(connection.execute("SELECT key, value FROM setting"))
    finally:
        connection.close()


def test_no_door_offers_a_flag_for_a_secret():
    """The three parsers, read from their source: no option names a token, a
    key, a secret or a password, except `--hf-token`, which exists only to be
    refused (TASK-089.09 criterion 5; brief G4)."""
    offered = {}
    for path in (REPO / "install.py", REPO / "packaging" / "launcher" / "myscribe_launcher.py",
                 REPO / "scribe" / "setup.py"):
        flags = re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', path.read_text(encoding="utf-8"))
        assert flags, f"no argparse options found in {path.name}: the reader is broken"
        offered[path.name] = [flag for flag in flags if re.search(r"token|key|secret|password", flag)]

    assert offered == {"install.py": [], "myscribe_launcher.py": [], "setup.py": ["--hf-token"]}


def test_the_refused_flag_does_not_repeat_what_it_was_given(capsys):
    assert setup.main(["--hf-token", SENTINEL]) == 2
    printed = capsys.readouterr()
    assert SENTINEL not in printed.out + printed.err


def test_one_secret_through_all_three_doors_rides_on_stdin_and_nowhere_else(
    tmp_path, monkeypatch, capsys
):
    answers = {"hf_token": SENTINEL, "llm_provider": "openrouter", "llm_key_openrouter": SENTINEL}

    # --- door 1: python install.py --answers FILE --------------------------------
    started: list[tuple[list[str], str | None]] = []

    def recorded_run(command, **kwargs):
        started.append(([str(part) for part in command], kwargs.get("input")))
        return install.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install.subprocess, "run", recorded_run)
    monkeypatch.setattr(install.subprocess, "call", lambda command, **kw: recorded_run(command, **kw).returncode)
    layout = install.Layout(tmp_path / "clone")
    install.sitting(layout, {}, json.dumps({"answers": answers}), unattended=False)

    # --- door 2: the frozen launcher's window, applying what was typed -----------
    streamed: list[tuple[list[str], str | None]] = []

    def recorded_stream(command, *, input=None, **kwargs):
        streamed.append(([str(part) for part in command], input))
        return 0

    monkeypatch.setattr(launcher, "run_streaming", recorded_stream)
    launcher_layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")
    said: list[str] = []
    launcher.run_setup(launcher_layout, answers, said.append)

    # --- door 3: the engine itself, which is what both of those hand it to -------
    for name in credentials.CREDENTIALS["openrouter"].env_vars + credentials.CREDENTIALS["huggingface"].env_vars:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(setup, "verify", lambda name, value: setup.Verdict(True, "accepted"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"contract": setup.CONTRACT, "answers": answers})))
    assert setup.main(["--apply-stdin"]) == 0

    # --- argv: nothing any door started carries it --------------------------------
    argvs = [argv for argv, _stdin in started + streamed]
    assert argvs, "no child was started: the seams above caught nothing"
    assert not [argv for argv in argvs if any(SENTINEL in part for part in argv)], argvs
    # ... and it did travel, on stdin, which is the one road it may take.
    assert all(SENTINEL in (stdin or "") for _argv, stdin in started + streamed)

    # --- printed output ------------------------------------------------------------
    printed = capsys.readouterr()
    assert SENTINEL not in printed.out + printed.err
    assert not [line for line in said if SENTINEL in line]

    # --- everything the engine left in the library, but its own settings rows -----
    written = _text_files_under(paths.DATA_DIR, skip=("myscribe.db",))
    assert setup.stamp_path() in written, "the stamp was not written: the sitting did not finish"
    assert not [path for path, text in written.items() if SENTINEL in text], sorted(written)
    # The one place a secret is meant to be kept, so the searches above are
    # not green because nothing was saved at all.
    assert SENTINEL in _settings(paths.DB_PATH).values()
