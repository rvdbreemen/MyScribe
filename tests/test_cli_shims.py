"""The ``myscribe`` command of an installed MyScribe (TASK-108, option C).

The project is not packaged, so no console script exists; an install gets a
small shim instead, next to the launcher: ``{app}\\bin\\myscribe.cmd`` on
Windows (packaging/windows/myscribe.cmd) and a POSIX ``myscribe`` in the
AppImage and the Mac bundle (packaging/posix/myscribe). Each one runs
``python -m scribe.cli`` with the Python of the environment the launcher
installed, which lives in MyScribe's home - found the way the launcher finds
it: ``MYSCRIBE_HOME``, then ``"home"`` in the pointer file, then the default.

These tests run the shims for real, against a layout built the way an
install lays it out: the program files under ``_internal/payload/app`` (the
frozen onedir), the environment under ``<home>/env``. The program files hold
a stand-in ``scribe/cli.py`` that prints what it was started with, so a test
can see the Python, the arguments and the environment the shim chose.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CMD = REPO / "packaging" / "windows" / "myscribe.cmd"
POSIX = REPO / "packaging" / "posix" / "myscribe"

# A stand-in for scribe.cli: says which python ran it, with what, from where.
PROBE = """\
import json, os, sys
print(json.dumps({"executable": sys.executable, "argv": sys.argv[1:],
                  "pythonpath": os.environ.get("PYTHONPATH"),
                  "pycache": os.environ.get("PYTHONPYCACHEPREFIX"),
                  "utf8": os.environ.get("PYTHONUTF8"),
                  "pythonhome": os.environ.get("PYTHONHOME")}))
sys.exit(int(os.environ.get("PROBE_EXIT", "0")))
"""


def program_files(app_root: Path, probe: bool = True) -> Path:
    """``<install>/_internal/payload/app`` with a scribe package in it."""
    app = app_root / "_internal" / "payload" / "app"
    (app / "scribe").mkdir(parents=True)
    (app / "scribe" / "__init__.py").write_text('__version__ = "0"\n', encoding="utf-8")
    if probe:
        (app / "scribe" / "cli.py").write_text(PROBE, encoding="utf-8")
    else:
        for name in ("cli.py", "mcp_server.py"):
            shutil.copy(REPO / "scribe" / name, app / "scribe" / name)
    return app


def clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"MYSCRIBE_HOME", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
                                "PYTHONPYCACHEPREFIX", "PROBE_EXIT"}}
    env.update(extra)
    return env


# --- Windows: myscribe.cmd ------------------------------------------------------------------

windows = pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe is Windows only")


def windows_env(home: Path) -> Path:
    """``<home>/env`` as a venv: this suite's Python, by its pyvenv.cfg."""
    scripts = home / "env" / "Scripts"
    scripts.mkdir(parents=True)
    venv = Path(sys.prefix)
    shutil.copy(venv / "pyvenv.cfg", home / "env" / "pyvenv.cfg")
    shutil.copy(Path(sys.executable), scripts / "python.exe")
    return scripts / "python.exe"


@pytest.fixture
def install(tmp_path) -> Path:
    """An install folder: bin\\myscribe.cmd and the program files beside it."""
    # Spaces, but no & or parentheses: with those in the path, cmd /c strips
    # the quotes around it before the shim ever runs (cmd /? on quotes).
    root = tmp_path / "Program Files here" / "MyScribe"
    (root / "bin").mkdir(parents=True)
    shutil.copy(CMD, root / "bin" / "myscribe.cmd")
    program_files(root)
    return root


def run_cmd(install: Path, *args: str, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    # From the install folder unless a test says otherwise: never from this
    # checkout, whose scribe/ a `python -m` without -P would import first.
    # /s and one pair of quotes around the whole line: how a shell runs a
    # quoted script path with quoted arguments (cmd /? on /s).
    line = subprocess.list2cmdline([str(install / "bin" / "myscribe.cmd"), *args])
    return subprocess.run(f'cmd.exe /d /s /c "{line}"', capture_output=True, text=True, env=env,
                          timeout=120, cwd=str(cwd or install))


def planted(folder: Path) -> Path:
    """A folder with a scribe package of its own, as a checkout or a
    download folder may have: `python -m` puts the working folder first."""
    (folder / "scribe").mkdir(parents=True)
    (folder / "scribe" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "scribe" / "cli.py").write_text("print('PLANTED')\n", encoding="utf-8")
    return folder


@windows
def test_the_cmd_shim_has_crlf_line_endings():
    """cmd.exe misreads labels and gotos in a file with bare LF endings."""
    data = CMD.read_bytes()
    assert b"\r\n" in data and data.count(b"\n") == data.count(b"\r\n")


@windows
def test_the_cmd_shim_runs_the_homes_python_with_the_install_s_program_files(install, tmp_path):
    home = tmp_path / "home with space"
    python = windows_env(home)
    done = run_cmd(install, "--port", "4420", "search", "two words", "50%",
                   env=clean_env(MYSCRIBE_HOME=str(home), PYTHONHOME="C:\\nowhere"))
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert Path(seen["executable"]).parent == python.parent
    assert seen["argv"] == ["--port", "4420", "search", "two words", "50%"]
    assert Path(seen["pythonpath"]).resolve() == (install / "_internal" / "payload" / "app").resolve()
    assert Path(seen["pycache"]) == home / "pycache"
    assert seen["utf8"] == "1" and seen["pythonhome"] is None


@windows
def test_the_cmd_shim_never_runs_a_scribe_from_the_working_folder(install, tmp_path):
    home = tmp_path / "home"
    windows_env(home)
    done = run_cmd(install, "status", env=clean_env(MYSCRIBE_HOME=str(home)),
                   cwd=planted(tmp_path / "somewhere"))
    assert done.returncode == 0, done.stderr
    assert "PLANTED" not in done.stdout
    assert json.loads(done.stdout)["argv"] == ["status"]


@windows
def test_the_cmd_shim_passes_the_exit_code_through(install, tmp_path):
    home = tmp_path / "home"
    windows_env(home)
    done = run_cmd(install, "wait", "1", env=clean_env(MYSCRIBE_HOME=str(home), PROBE_EXIT="7"))
    assert done.returncode == 7


@windows
@pytest.mark.parametrize("name", ["moved home (1)", "Héloïse"])
def test_the_cmd_shim_follows_the_pointer_file(install, tmp_path, name):
    """The launcher's pointer (``json.dumps``: escaped backslashes, ``\\u``
    for anything not ASCII) is read with PowerShell, not by cmd."""
    local = tmp_path / "local"
    local.mkdir()
    home = tmp_path / name
    windows_env(home)
    (local / "MyScribe.location").write_text(json.dumps({"home": str(home), "data": "D:\\x"}, indent=2),
                                             encoding="utf-8")
    done = run_cmd(install, "status", env=clean_env(LOCALAPPDATA=str(local)))
    assert done.returncode == 0, done.stderr
    assert Path(json.loads(done.stdout)["executable"]).parent == home / "env" / "Scripts"


@windows
def test_the_cmd_shim_uses_the_default_home_without_a_pointer(install, tmp_path):
    local = tmp_path / "local"
    windows_env(local / "MyScribe")
    done = run_cmd(install, "status", env=clean_env(LOCALAPPDATA=str(local)))
    assert done.returncode == 0, done.stderr
    assert Path(json.loads(done.stdout)["executable"]).parent == local / "MyScribe" / "env" / "Scripts"


@windows
def test_without_an_engine_the_cmd_shim_says_start_myscribe_first(install, tmp_path):
    done = run_cmd(install, "status", env=clean_env(MYSCRIBE_HOME=str(tmp_path / "empty")))
    assert done.returncode == 3
    assert "Start MyScribe once" in done.stderr and done.stdout == ""


@windows
def test_without_its_program_files_the_cmd_shim_says_so(tmp_path):
    bare = tmp_path / "bare"
    (bare / "bin").mkdir(parents=True)
    shutil.copy(CMD, bare / "bin" / "myscribe.cmd")
    done = run_cmd(bare, "status", env=clean_env(MYSCRIBE_HOME=str(tmp_path)))
    assert done.returncode == 1
    assert "Reinstall MyScribe" in done.stderr


@windows
def test_the_cmd_shim_runs_the_real_cli(tmp_path):
    root = tmp_path / "MyScribe"
    (root / "bin").mkdir(parents=True)
    shutil.copy(CMD, root / "bin" / "myscribe.cmd")
    program_files(root, probe=False)
    home = tmp_path / "home"
    windows_env(home)
    done = run_cmd(root, "--help", env=clean_env(MYSCRIBE_HOME=str(home)))
    assert done.returncode == 0, done.stderr
    assert "usage: myscribe" in done.stdout and "watch" in done.stdout and "doctor" in done.stdout


# --- POSIX: the myscribe sh script ------------------------------------------------------------

SH = shutil.which("sh")
posix = pytest.mark.skipif(SH is None, reason="no sh on this machine")

FAKE_PYTHON = """#!/bin/sh
printf '%s\\n' "python=$0" "pythonpath=$PYTHONPATH" "pycache=$PYTHONPYCACHEPREFIX" \
    "utf8=$PYTHONUTF8" "home=${PYTHONHOME-unset}"
for arg in "$@"; do printf 'arg=%s\\n' "$arg"; done
exit "${PROBE_EXIT:-0}"
"""


def posix_home(home: Path) -> Path:
    python = home / "env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(FAKE_PYTHON.encode("utf-8"))
    python.chmod(0o755)
    return python


@pytest.fixture
def bundle(tmp_path) -> Path:
    """usr/bin as the AppImage has it: the script beside _internal/."""
    usr_bin = tmp_path / "AppDir" / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    shutil.copy(POSIX, usr_bin / "myscribe-cli")
    program_files(usr_bin)
    return usr_bin


def default_parent(tmp_path: Path) -> Path:
    """The folder the script's default home sits in, with HOME=tmp_path and
    XDG_DATA_HOME=tmp_path/xdg: ~/Library/Application Support on a Mac, the
    XDG data folder elsewhere - the launcher's rule (myscribe_launcher.home_dir),
    which the script copies. Asked of `uname -s` through the same sh, the way
    the script asks it, so the test and the script cannot disagree about the OS."""
    system = subprocess.run([SH, "-c", "uname -s"], capture_output=True, text=True).stdout.strip()
    if system == "Darwin":
        return tmp_path / "Library" / "Application Support"
    return tmp_path / "xdg"


def run_sh(script: Path, *args: str, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([SH, str(script), *args], capture_output=True, text=True, env=env,
                          timeout=60, cwd=str(cwd or script.parent))


def seen(done) -> dict:
    lines = done.stdout.splitlines()
    out = dict(line.split("=", 1) for line in lines if not line.startswith("arg="))
    out["args"] = [line[4:] for line in lines if line.startswith("arg=")]
    return out


def as_posix(path: Path) -> str:
    return path.as_posix()


def test_the_posix_script_has_lf_line_endings():
    assert b"\r" not in POSIX.read_bytes()
    assert POSIX.read_bytes().startswith(b"#!/bin/sh\n")


@posix
def test_the_posix_script_runs_the_homes_python(bundle, tmp_path):
    home = tmp_path / "my home"
    posix_home(home)
    done = run_sh(bundle / "myscribe-cli", "search", "two words", "--json",
                  env=clean_env(MYSCRIBE_HOME=as_posix(home), PYTHONHOME="/nowhere"))
    assert done.returncode == 0, done.stderr
    got = seen(done)
    assert got["python"].endswith("my home/env/bin/python")
    assert got["args"] == ["-P", "-m", "scribe.cli", "search", "two words", "--json"]
    assert got["pythonpath"].endswith("usr/bin/_internal/payload/app")
    assert got["pycache"].endswith("my home/pycache")
    assert got["utf8"] == "1" and got["home"] == "unset"


@posix
def test_the_posix_script_runs_python_without_the_working_folder_on_its_path(bundle, tmp_path):
    """The real Python gets -P: `python -m` would otherwise import a scribe
    package from wherever the command is typed."""
    posix_home(tmp_path / "h")
    done = run_sh(bundle / "myscribe-cli", "status", env=clean_env(MYSCRIBE_HOME=as_posix(tmp_path / "h")),
                  cwd=planted(tmp_path / "somewhere"))
    assert seen(done)["args"][:3] == ["-P", "-m", "scribe.cli"]


@posix
def test_the_posix_script_passes_the_exit_code_through(bundle, tmp_path):
    posix_home(tmp_path / "h")
    done = run_sh(bundle / "myscribe-cli", "wait", "1",
                  env=clean_env(MYSCRIBE_HOME=as_posix(tmp_path / "h"), PROBE_EXIT="8"))
    assert done.returncode == 8


@posix
def test_the_posix_script_follows_the_pointer_file(bundle, tmp_path):
    data_home = default_parent(tmp_path)
    data_home.mkdir(parents=True)
    moved = tmp_path / "moved home"
    posix_home(moved)
    (data_home / "MyScribe.location").write_text(
        json.dumps({"home": as_posix(moved), "data": "/elsewhere"}, indent=2), encoding="utf-8")
    done = run_sh(bundle / "myscribe-cli", "status",
                  env=clean_env(XDG_DATA_HOME=as_posix(tmp_path / "xdg"), HOME=as_posix(tmp_path)))
    assert done.returncode == 0, done.stderr
    assert seen(done)["python"].endswith("moved home/env/bin/python")


@posix
def test_the_posix_script_refuses_a_pointer_it_cannot_read(bundle, tmp_path):
    data_home = default_parent(tmp_path)
    data_home.mkdir(parents=True)
    (data_home / "MyScribe.location").write_text(json.dumps({"home": "/home/h\u00e9"}, indent=2),
                                                 encoding="utf-8")
    done = run_sh(bundle / "myscribe-cli", "status",
                  env=clean_env(XDG_DATA_HOME=as_posix(tmp_path / "xdg"), HOME=as_posix(tmp_path)))
    assert done.returncode == 2
    assert "MYSCRIBE_HOME" in done.stderr


@posix
def test_the_posix_script_uses_the_default_home(bundle, tmp_path):
    data_home = default_parent(tmp_path)
    posix_home(data_home / "MyScribe")
    done = run_sh(bundle / "myscribe-cli", "status",
                  env=clean_env(XDG_DATA_HOME=as_posix(tmp_path / "xdg"), HOME=as_posix(tmp_path)))
    assert done.returncode == 0, done.stderr
    assert seen(done)["python"].endswith(f"{data_home.name}/MyScribe/env/bin/python")


@posix
def test_without_an_engine_the_posix_script_says_start_myscribe_first(bundle, tmp_path):
    done = run_sh(bundle / "myscribe-cli", "status", env=clean_env(MYSCRIBE_HOME=as_posix(tmp_path / "none")))
    assert done.returncode == 3
    assert "Start MyScribe once" in done.stderr


@posix
def test_the_posix_script_finds_the_mac_bundles_resources(tmp_path):
    resources = tmp_path / "MyScribe.app" / "Contents" / "Resources"
    resources.mkdir(parents=True)
    shutil.copy(POSIX, resources / "myscribe")
    app = resources / "payload" / "app"
    (app / "scribe").mkdir(parents=True)
    (app / "scribe" / "cli.py").write_text("", encoding="utf-8")
    posix_home(tmp_path / "h")
    done = run_sh(resources / "myscribe", "status", env=clean_env(MYSCRIBE_HOME=as_posix(tmp_path / "h")))
    assert done.returncode == 0, done.stderr
    assert seen(done)["pythonpath"].endswith("Contents/Resources/payload/app")


@posix
def test_without_its_program_files_the_posix_script_says_so(tmp_path):
    shutil.copy(POSIX, tmp_path / "myscribe")
    done = run_sh(tmp_path / "myscribe", "status", env=clean_env(MYSCRIBE_HOME=as_posix(tmp_path)))
    assert done.returncode == 1
    assert "Reinstall MyScribe" in done.stderr
