"""A data directory that only `.env` names is the one the commands use (TASK-089.03).

`python -m scribe` always read `.env` before it imported anything. Setup,
models and the doctor import scribe.paths at module level, which fixes DATA_DIR
before their main() has read the file - so a library moved through `.env`, the
documented way (.env.example), was the app's library and not theirs. Setup
wrote its token row, its stamp and the weights into ./data, and the app,
reading the other directory, behaved as if setup had never run.

These run each command as a user does, in a child process, because the fault
lives in import order and an in-process call cannot show it. The child runs
from a COPY of the package: setup and the doctor open and migrate the database
they find, and from the repository that is the developer's real library.
"""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scribe import env, models, paths

REPO = Path(__file__).resolve().parent.parent

# Not handed to the child. A token would send the doctor's diarization check
# to the network, and nothing a failing assertion prints may come from one.
WITHHELD = {"HF_TOKEN", "HUGGINGFACE_TOKEN", "PYTHONPATH", "PYTHONSAFEPATH"}


@pytest.fixture
def clone(tmp_path) -> Path:
    """A copy of the package, to run the commands from."""
    root = tmp_path / "clone"
    shutil.copytree(REPO / "scribe", root / "scribe", ignore=shutil.ignore_patterns("__pycache__"))
    return root


def run(clone: Path, library: Path, *command: str) -> subprocess.CompletedProcess:
    """``python -m <command>`` in ``clone``, with SCRIBE_DATA_DIR in a scratch
    env file and nowhere in the child's environment."""
    env_file = clone.parent / "scratch.env"
    env_file.write_text(f"SCRIBE_DATA_DIR={library}\n", encoding="utf-8")
    child = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in WITHHELD and not key.upper().startswith("SCRIBE_")
    }
    child["SCRIBE_ENV_FILE"] = str(env_file)
    # This machine has the whisper weights in the real hub cache, and a hit
    # there answers "have" without looking under the data directory at all.
    child["HF_HUB_CACHE"] = str(clone.parent / "empty-hub")

    def python(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, *args], cwd=clone, env=child, capture_output=True, text=True, timeout=300
        )

    # The belt to the copy's braces: if `scribe` ever resolved to the
    # repository from here, the command below would migrate the real library.
    found = python("-c", "import scribe; print(scribe.__file__)").stdout.strip()
    assert Path(found).parent == clone / "scribe", f"the child would run {found}"
    return python("-m", *command)


def test_setup_uses_the_directory_only_the_env_file_names(clone, tmp_path):
    library = tmp_path / "moved library"

    done = run(clone, library, "scribe.setup", "--status")

    assert done.returncode == 0, done.stderr
    assert (library / "myscribe.db").exists(), "the settings rows belong in the library the app reads"
    assert not (clone / "data").exists(), "and nothing in the default one"
    at = {row["repo"]: row["at"] for row in json.loads(done.stdout)["models"]}
    assert at[models.DIARIZE] == str(library / "models" / "pyannote")


def test_the_doctor_checks_the_directory_only_the_env_file_names(clone, tmp_path):
    """On the lines, not the exit code: a machine without ffmpeg fails the
    gate and still has to be asked about the right directory."""
    library = tmp_path / "moved library"

    done = run(clone, library, "scribe.doctor", "--no-gpu")

    # `[OK  ] data-dir  ...`: the mark is six characters whatever it says.
    report = {line[7:].split()[0]: line for line in done.stdout.splitlines() if line.startswith("[")}
    assert str(library) in report["data-dir"], done.stdout
    assert str(library / "myscribe.db") in report["database"], done.stdout
    assert not (clone / "data").exists()


def test_models_finds_weights_under_the_directory_only_the_env_file_names(clone, tmp_path):
    library = tmp_path / "moved library"
    pipeline = models.catalogue()[models.DIARIZE]
    for rel, pin in pipeline.files.items():
        weight = library / "models" / pipeline.folder / rel
        weight.parent.mkdir(parents=True, exist_ok=True)
        with weight.open("wb") as handle:
            handle.truncate(int(pin["size"]))  # status asks existence and size, not the hash

    done = run(clone, library, "scribe.models")

    assert done.returncode == 0, done.stderr
    row = next(line for line in done.stdout.splitlines() if models.DIARIZE in line)
    assert row.startswith("[   have]"), done.stdout


# --- the wiring, without a child process --------------------------------------------

COMMANDS = ["scribe.setup", "scribe.models", "scribe.doctor"]


@pytest.fixture
def calls(tmp_path, monkeypatch) -> list[str]:
    """What the module asked of scribe.env and scribe.paths, in order.

    The environment is a throwaway copy that names an env file nobody wrote,
    so a test that goes wrong reads no developer's `.env` and leaks no key. A
    copy rather than an empty one: the modules these import for the first time
    ask for the home directory.
    """
    seen: list[str] = []
    monkeypatch.setattr(os, "environ", {**os.environ, "SCRIBE_ENV_FILE": str(tmp_path / "nobody.env")})
    monkeypatch.setattr(env, "bootstrap", lambda: seen.append("bootstrap") or {}, raising=False)
    monkeypatch.setattr(env, "load_dotenv", lambda *a, **k: seen.append("load_dotenv") or {})
    monkeypatch.setattr(paths, "refresh", lambda: seen.append("refresh"), raising=False)
    return seen


@pytest.mark.parametrize("command", COMMANDS)
def test_run_as_a_command_it_reads_env_and_moves_the_paths_before_main(command, calls, monkeypatch, capsys):
    """`--help` makes main() print and leave, so whatever was recorded by
    then ran before it."""
    monkeypatch.setattr(sys, "argv", [command, "--help"])

    with pytest.raises(SystemExit):
        runpy.run_module(command, run_name="__main__", alter_sys=True)

    assert calls[:2] == ["bootstrap", "refresh"]
    assert f"usage: {command}" in capsys.readouterr().out


@pytest.mark.parametrize("command", COMMANDS)
def test_imported_as_a_library_it_reads_no_file(command, calls):
    """The doctor is a library too (scribe/runner.py imports it) and so is
    models (setup does). An import that read `.env` would read it inside the
    web process, and inside every test that imports either."""
    runpy.run_module(command, run_name=command.replace(".", "_") + "_as_a_library", alter_sys=True)

    assert calls == []
