"""MyScribe's launcher: the program a user starts (ADR-011).

Stdlib only, because it runs before the environment it creates exists. It

1. finds the payload it was shipped with - ``app/`` (the source tree with
   ``pyproject.toml`` and ``uv.lock``) and ``bin/`` (uv, ffmpeg, ffprobe);
2. prepares the per-user home: a uv-managed Python, the environment, uv's
   cache, the data directory, ``.env`` and the launcher's log;
3. runs ``uv sync --frozen`` with the bundled uv when the shipped lock is not
   the one the environment was last synced from - the first run, and the
   first run after an update that changed a pin;
4. starts ``python -m scribe`` from that environment with the source tree as
   its working directory (so the runner children, ``sys.executable -m
   scribe.runner``, keep working unchanged), waits for ``/health`` and opens
   the browser;
5. stays up - a small window, or the console with ``--headless`` - until
   Quit, which stops the server and every runner child it started.

A second start while the app answers ``/health`` only opens the browser.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
# Under a name of its own: `platform` is a parameter in half the functions
# below, and `platform.machine()` inside one of them would be an AttributeError
# on a string.
import platform as platform_info
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Iterable

APP_NAME = "MyScribe"
HOST = "127.0.0.1"
DEFAULT_PORT = 4242
HOME_VARIABLE = "MYSCRIBE_HOME"
PAYLOAD_VARIABLE = "MYSCRIBE_PAYLOAD"
# What the app is told about this launcher, so a login entry can name the
# launcher and not the environment's python (TASK-089.21).
LAUNCHER_VARIABLE = "MYSCRIBE_LAUNCHER"
# And this launcher's pid, which /health reports as launcher_pid: on Windows
# the app's parent is uv's trampoline python.exe, not this process (TASK-117).
LAUNCHER_PID_VARIABLE = "MYSCRIBE_LAUNCHER_PID"
STAMP_NAME = ".myscribe-sync.json"
READY_TIMEOUT = 120.0
STOP_TIMEOUT = 15.0
APP_SIGTERM_WAIT = 75.0
"""How long the POSIX hard stop waits after the app's SIGTERM before its
SIGKILL (`AppProcess.stop`, `end_tree`). The app's stop cuts open reads off
after 3 s and lets an upload in flight finish (Robert, 2026-10-25) for up to
30 s; its lifespan then ends the runner's tree and joins
the supervisor for up to `STOP_JOIN_SECONDS` (20 s, ADR-032), the phone door
(its 2 s grace), the feeds (10 s) and the watcher (10 s): 72 s at worst, 3 s
inside this wait. Robert kept the 30 s and this 75 s on 2026-10-25.
A shorter wait SIGKILLed an app that had already ended its runner, and the
sentence after it said the job could keep running (TASK-112.09). The cost is
an app that hangs, which now gets this long before its SIGKILL instead of
`STOP_TIMEOUT`. `tests/test_launcher.py` adds the app's own timeouts up."""

IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


# --- where things live ------------------------------------------------------------


def default_home(platform: str = sys.platform, environ: dict | None = None) -> Path:
    """The per-user MyScribe folder as it ships. Never the install directory:
    Program Files, a ``.app`` and an AppImage are read-only or replaced on
    update."""
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def pointer_path(platform: str = sys.platform, environ: dict | None = None) -> Path:
    """Where the answer to "where should MyScribe keep everything?" is kept:
    ``MyScribe.location``, *beside* the default home and not inside it.

    Beside, so that a home somebody moved leaves nothing at all under the
    default one - an empty ``MyScribe`` folder on C: is exactly the "did it
    install twice?" that moving it was meant to avoid.

    A module-level function and not only a parameter, because
    ``build_parser()`` asks ``home_dir()`` for the ``--home`` help text before
    any argument is read: with a parameter alone, a test would read the
    pointer file of whoever is running it.
    """
    default = default_home(platform, environ)
    return default.with_name(default.name + ".location")


def read_pointer(path: Path) -> tuple[dict, str]:
    """The pointer's object, and what is wrong with it - ``""`` when nothing is.

    One reader for both callers, so ``home_dir`` and ``locate_home`` can never
    disagree about what a file says. No file at all is not a problem: it is
    what every machine that never moved its home looks like. A file that
    cannot be read, or that is not a JSON object, *is* a problem and is never
    quietly treated as absent - that would move somebody's library back to C:
    without a word.

    Unknown keys are kept and ignored: TASK-089.19 may add a second fact to
    this file, and nothing here pins that it holds one.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, ""
    except OSError as error:
        return {}, f"{path} cannot be read ({error})."
    except ValueError:
        return {}, f"{path} is not readable as JSON."
    if not isinstance(document, dict):
        return {}, f"{path} does not hold a JSON object."
    return document, ""


def home_dir(platform: str = sys.platform, environ: dict | None = None,
             pointer: Path | None = None) -> Path:
    """The home in force: ``MYSCRIBE_HOME``, then the pointer file, then the
    per-OS default. ``--home`` outranks all three, in ``main``.

    Pure, and it never raises: ``build_parser()`` calls it for a help string
    before any argument is read, so a refusal in here would also stop
    ``--version``, ``--smoke`` and ``--home X``. A pointer that is broken or
    names a folder that is gone is not answered here but in ``locate_home``,
    which has somebody to tell.
    """
    environ = os.environ if environ is None else environ
    if environ.get(HOME_VARIABLE):
        return Path(environ[HOME_VARIABLE])
    document, problem = read_pointer(pointer_path(platform, environ) if pointer is None else Path(pointer))
    named = document.get("home")
    if not problem and isinstance(named, str) and named.strip():
        return Path(named.strip())
    return default_home(platform, environ)


def home_source(home_flag: Path | None = None, platform: str = sys.platform,
                environ: dict | None = None, pointer: Path | None = None) -> str:
    """Which of the four sources supplied the home in force, in words.

    Separate from ``home_dir`` on purpose: that one returns a ``Path`` and two
    tests compare it to one, and ``--setup`` needs to say where the path came
    from as well as what it is.
    """
    environ = os.environ if environ is None else environ
    pointer = pointer_path(platform, environ) if pointer is None else Path(pointer)
    if home_flag is not None:
        return "the --home option"
    if environ.get(HOME_VARIABLE):
        return f"the {HOME_VARIABLE} variable"
    document, problem = read_pointer(pointer)
    named = document.get("home")
    if not problem and isinstance(named, str) and named.strip():
        return f"the pointer file {pointer}"
    return "the default for this computer"


def write_pointer(path: Path, home: Path) -> str:
    """Record where everything goes; return what went wrong, ``""`` when
    nothing did.

    Read first and merged, rather than overwritten: the launcher is not the
    only writer this file may get (ADR-015, TASK-089.19), and a writer that
    threw away what it did not understand would be the one that loses it.

    Never raises, for the same reason ``read_pointer`` does not: the one
    moment this is called is straight after somebody answered the question,
    and a file that cannot be read is usually a file that cannot be written
    either. A raise here would end a ``--windowed`` build with no console and
    no window, moments after the answer.
    """
    document, problem = read_pointer(path)
    document = {} if problem else document
    document["home"] = str(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    except OSError as error:
        return (f"{home} could not be saved in {path} ({error}). MyScribe uses it for this "
                f"sitting; to keep it, start MyScribe with --home {home} or set "
                f"{HOME_VARIABLE}.")
    return ""


# --- an adopted library: the pointer's second fact (TASK-089.19) -------------------


DATA_KEY = "data"
"""The pointer's second fact: the data directory of a library adopted in place.

Decided by the engine (`scribe.setup`), written here only when the engine's
result names it, and read before anything else exists. `"home"` moves
everything; `"data"` points at one library that lives somewhere else - a
clone's `data/`, for one - and moves nothing."""


def adopted_data(pointer: Path) -> tuple[Path | None, str]:
    """The adopted library the pointer names, and what is wrong with it.

    `(None, "")` when it names none, which is every install that never adopted
    one. A folder that is not there is a problem and never a fallback: starting
    on `<home>/data` instead would start a second, empty library, and to its
    owner that looks like every recording gone - the same rule `locate_home`
    keeps for the home.
    """
    document, problem = read_pointer(pointer)
    if problem:
        return None, ""  # `locate_home` has already said so, and answered it
    named = document.get(DATA_KEY)
    if not isinstance(named, str) or not named.strip():
        return None, ""
    data = Path(named.strip())
    if data.is_dir():
        return data, ""
    return None, (f"{pointer} says {APP_NAME}'s library is in {data}, and that folder is not there. "
                  "If it is on a drive that is not plugged in, quit, plug it in and start "
                  f"{APP_NAME} again: nothing has been moved and nothing is lost. {APP_NAME} will "
                  "not quietly start a second, empty library somewhere else.")


def write_pointer_data(path: Path, data: Path) -> str:
    """Record an adopted library; return what went wrong, ``""`` when nothing
    did. Merged like ``write_pointer``, so the home survives it and it
    survives the home."""
    document, problem = read_pointer(path)
    document = {} if problem else document
    document[DATA_KEY] = str(data)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    except OSError as error:
        return (f"The library at {data} could not be saved in {path} ({error}). MyScribe uses it "
                "for this sitting only; the next start opens the library in its own home.")
    return ""


SETUP_RESULT_VARIABLE = "MYSCRIBE_SETUP_RESULT"
"""Where `scribe.setup` says what an adoption decided (its RESULT_VARIABLE):
a file this launcher names for the setup child and reads when it has gone.
A file, because the console door never reads the child's stdout."""


def setup_result_file(layout: "Layout") -> Path:
    return layout.home / "setup-result.json"


def setup_environment(layout: "Layout") -> dict:
    """The app's environment, plus the one name the engine answers an
    adoption through."""
    environment = app_environment(layout)
    environment[SETUP_RESULT_VARIABLE] = str(setup_result_file(layout))
    return environment


def take_setup_result(layout: "Layout") -> dict:
    """What the last setup child left for this launcher, removed once read;
    {} when it left nothing, or nothing readable."""
    path = setup_result_file(layout)
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    try:
        path.unlink()
    except OSError:
        pass
    return found if isinstance(found, dict) else {}


def payload_dir(environ: dict | None = None) -> Path:
    """Where the build put ``app/`` and ``bin/``: inside the frozen bundle, or
    next to this file when run from a checkout's build output."""
    environ = os.environ if environ is None else environ
    if environ.get(PAYLOAD_VARIABLE):
        return Path(environ[PAYLOAD_VARIABLE])
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "payload"
    return Path(__file__).resolve().parent / "payload"


class Layout:
    """Every path the launcher touches, from two roots."""

    def __init__(self, home: Path, payload: Path, windows: bool = IS_WINDOWS,
                 data: Path | None = None, pointer: Path | None = None):
        # Absolute: the sync runs with the home as its working directory.
        self.home = Path(home).absolute()
        self.payload = Path(payload).absolute()
        self.windows = windows
        # TASK-089.19: an adopted library, named by the pointer's `"data"`
        # fact. None is `<home>/data`, as it always was.
        self.data = Path(data).absolute() if data is not None else None
        # The pointer file an adoption is recorded in; None when the home was
        # given by hand, which keeps everything under that home.
        self.pointer = Path(pointer) if pointer is not None else None

    app_dir = property(lambda self: self.payload / "app")
    bin_dir = property(lambda self: self.payload / "bin")
    lock_file = property(lambda self: self.app_dir / "uv.lock")
    env_dir = property(lambda self: self.home / "env")
    python_dir = property(lambda self: self.home / "python")
    cache_dir = property(lambda self: self.home / "cache")
    data_dir = property(lambda self: self.data if self.data is not None else self.home / "data")
    env_file = property(lambda self: self.home / ".env")
    logs_dir = property(lambda self: self.home / "logs")
    pycache_dir = property(lambda self: self.home / "pycache")
    tools_dir = property(lambda self: self.home / "bin")
    stamp_file = property(lambda self: self.env_dir / STAMP_NAME)

    @property
    def uv(self) -> Path:
        return self.tools_dir / ("uv.exe" if self.windows else "uv")

    @property
    def env_python(self) -> Path:
        if self.windows:
            return self.env_dir / "Scripts" / "python.exe"
        return self.env_dir / "bin" / "python"


def app_version(layout: Layout) -> str:
    """``scribe.__version__`` read as text - importing it would need the env."""
    init = layout.app_dir / "scribe" / "__init__.py"
    try:
        match = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
    except OSError:
        return "unknown"
    return match.group(1) if match else "unknown"


# --- the sync decision ------------------------------------------------------------


def lock_digest(layout: Layout) -> str:
    return hashlib.sha256(layout.lock_file.read_bytes()).hexdigest()


def needs_sync(layout: Layout) -> bool:
    """True unless the environment exists and was synced from this very lock."""
    if not layout.env_python.exists():
        return True
    try:
        stamp = json.loads(layout.stamp_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return stamp.get("lock_sha256") != lock_digest(layout)


def write_stamp(layout: Layout) -> None:
    stamp = {"lock_sha256": lock_digest(layout), "version": app_version(layout), "ts": time.time()}
    layout.stamp_file.parent.mkdir(parents=True, exist_ok=True)
    layout.stamp_file.write_text(json.dumps(stamp), encoding="utf-8")


def uv_command(layout: Layout) -> list[str]:
    return [str(layout.uv)]


def sync_command(layout: Layout) -> list[str]:
    return uv_command(layout) + [
        "sync",
        "--frozen",
        "--no-dev",
        "--project", str(layout.app_dir),
        "--python-preference", "only-managed",
        "--color", "never",
    ]


def child_environment(base: dict | None = None) -> dict:
    """``base`` without what a frozen launcher leaks into its children.

    PyInstaller points ``LD_LIBRARY_PATH`` at its own bundle on Linux (saving
    the original as ``..._ORIG``) and sets Tcl/Tk variables for its tkinter;
    a Python or ffmpeg started with those picks up the launcher's libraries.
    """
    env = dict(os.environ if base is None else base)
    for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        original = env.pop(name + "_ORIG", None)
        if original is not None:
            env[name] = original
        elif getattr(sys, "frozen", False):
            env.pop(name, None)
    for name in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    return env


def sync_environment(layout: Layout, base: dict | None = None) -> dict:
    env = child_environment(base)
    env.update(
        UV_PROJECT_ENVIRONMENT=str(layout.env_dir),
        UV_PYTHON_INSTALL_DIR=str(layout.python_dir),
        UV_CACHE_DIR=str(layout.cache_dir),
        # A user's own uv.toml must not change what gets installed.
        UV_NO_CONFIG="1",
        UV_PYTHON_DOWNLOADS="automatic",
    )
    return env


def launcher_path(executable: str, platform: str, environ: dict) -> Path:
    """The path that will still start this launcher at the next login.

    Which path that is differs per OS, and only the Windows answer has been
    seen to be true here:

    * macOS - the ``.app`` bundle, not the binary inside it.
    * Linux - the value of ``APPIMAGE``: inside an AppImage the running
      executable sits under a temporary mount that is gone after exit, so an
      entry naming it breaks at the next login. Without it (an unpacked
      build), the executable itself.
    * Windows - the executable itself.

    The macOS and Linux answers are the platforms' own documentation and
    nobody's measurement (design spec §3.10).
    """
    if platform == "darwin":
        for parent in Path(executable).parents:
            if parent.suffix == ".app":
                return parent
    elif platform.startswith("linux"):
        appimage = environ.get("APPIMAGE")
        if appimage:
            return Path(appimage)
    return Path(executable)


def this_launcher() -> Path | None:
    """Where this launcher is, or None when it is not a frozen launcher at all.

    Running from the source tree, ``sys.executable`` is a python in somebody's
    venv. Naming that in a login entry is exactly the stale-environment bug a
    release entry exists to avoid, so nothing is handed down and the app falls
    back to the clone's own start script.
    """
    if not getattr(sys, "frozen", False):
        return None
    return launcher_path(sys.executable, sys.platform, os.environ)


def app_environment(layout: Layout, base: dict | None = None) -> dict:
    env = child_environment(base)
    env.update(
        SCRIBE_DATA_DIR=str(layout.data_dir),
        SCRIBE_ENV_FILE=str(layout.env_file),
        # The source tree may be read-only; bytecode goes to the home instead.
        PYTHONPYCACHEPREFIX=str(layout.pycache_dir),
        PYTHONPATH=str(layout.app_dir),
        PYTHONUTF8="1",
    )
    # Where the launcher is, so Settings can register it as a login entry and
    # tell a release from a clone (TASK-089.21). Absent when nothing was
    # frozen: there is then no launcher path that means anything.
    launcher = this_launcher()
    if launcher is not None:
        env[LAUNCHER_VARIABLE] = str(launcher)
        env[LAUNCHER_PID_VARIABLE] = str(os.getpid())
    # The bundled ffmpeg and ffprobe first. A Mac app started from Finder gets
    # a PATH without Homebrew, so without this there would be no ffmpeg at all.
    env["PATH"] = os.pathsep.join(p for p in (str(layout.tools_dir), env.get("PATH", "")) if p)
    return env


def _payload_tool_hashes(layout: Layout) -> dict[str, str]:
    """``{name: sha256}`` of the shipped ``bin/``, from the build's manifest."""
    try:
        files = json.loads((layout.payload / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
        found = {name[len("bin/"):]: digest for name, digest in files.items() if name.startswith("bin/")}
        if found:
            return found
    except (OSError, ValueError, KeyError):
        pass
    return {
        tool.name: hashlib.sha256(tool.read_bytes()).hexdigest()
        for tool in sorted(layout.bin_dir.iterdir()) if tool.is_file()
    }


def install_tools(layout: Layout) -> None:
    """Put uv, ffmpeg and ffprobe in the home and run them from there.

    Written as fresh files rather than copied: on macOS a copy keeps the
    download's com.apple.quarantine attribute (shutil.copyfile and copy2
    both carry it), and Gatekeeper refuses to run a quarantined binary that
    is only ad-hoc signed, as the self-built ffmpeg is. Skipped while the
    stamp matches the shipped hashes, so an update replaces them once.
    """
    wanted = _payload_tool_hashes(layout)
    stamp = layout.tools_dir / ".tools.json"
    try:
        current = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    if current == wanted and all((layout.tools_dir / name).exists() for name in wanted):
        return
    layout.tools_dir.mkdir(parents=True, exist_ok=True)
    for name in wanted:
        target = layout.tools_dir / name
        fresh = target.with_name(name + ".new")
        fresh.write_bytes((layout.bin_dir / name).read_bytes())
        fresh.chmod(0o755)
        os.replace(fresh, target)
    stamp.write_text(json.dumps(wanted), encoding="utf-8")


def _owner_only(path, flags: int) -> int:
    """An ``open()`` opener for the files the launcher writes under the home.

    app.log holds uvicorn's access log - request paths, search terms - and
    tracebacks, and ``.env`` is where the template asks for the tokens. At
    the umask's 0644 any account that can reach the folder reads them - a
    home on another drive, a home folder left open (GitHub #42) - so they
    are made 0600, and a file from before this fix is closed
    as it is opened - what scribe.applog does for the app's own log. A
    chmod that fails stops nothing.
    """
    fd = os.open(path, flags, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            os.fchmod(fd, 0o600)
    except OSError:
        pass
    return fd


# POSIX only: Windows has ACLs, not mode bits, and keeps the plain open().
OWNER_ONLY = None if IS_WINDOWS else _owner_only
# GitHub #42: a folder the launcher makes is its owner's alone. On Windows a
# current Python turns mode 0o700 into a protected ACL that drops what the
# folder would inherit (measured on 3.12.9), so there the default stays.
PRIVATE_DIR = 0o777 if IS_WINDOWS else 0o700


def prepare_home(layout: Layout) -> None:
    # The mode reaches only a folder this call creates: a home or library the
    # user picked was there first, and how open it is was their choice.
    for folder in (layout.home, layout.data_dir, layout.logs_dir):
        folder.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
    example = layout.app_dir / ".env.example"
    if not layout.env_file.exists() and example.exists():
        with open(example, "rb") as source, open(layout.env_file, "wb", opener=OWNER_ONLY) as target:
            shutil.copyfileobj(source, target)


def _release_frozen_dll_directory() -> None:
    """PyInstaller's bootloader calls SetDllDirectory on its bundle, and child
    processes inherit it; reset it so uv and Python load their own DLLs."""
    if IS_WINDOWS and getattr(sys, "frozen", False):
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)


def run_streaming(command: list[str], *, env: dict, cwd: Path | None, on_line: Callable[[str], None],
                  input: str | None = None,
                  on_start: Callable[["subprocess.Popen"], None] | None = None) -> int:
    """Run ``command``, hand every output line to ``on_line``, return the exit code.

    ``input`` is one document written to the child's stdin, which is then
    closed: ``scribe.setup --apply-stdin`` reads until EOF, so a stdin left
    open is a child that never starts. The document is a few hundred bytes,
    well inside the pipe buffer, so writing it before the first read cannot
    deadlock. Without ``input`` the child gets no stdin at all, as before -
    which is what makes ``scribe.setup``'s own "nobody is at a terminal" test
    look at more than ``isatty()`` on Windows, where NUL answers yes.

    ``on_start`` is handed the process, for a caller that has to be able to
    stop it: Quit stops the setup child alone, and never its tree (ADR-017).
    """
    proc = subprocess.Popen(
        command, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        creationflags=NO_WINDOW,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    if on_start is not None:
        on_start(proc)
    if input is not None and proc.stdin is not None:
        try:
            proc.stdin.write(input)
        except OSError:
            pass  # a child that died before it read; its exit code says so
        finally:
            proc.stdin.close()
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip())
    return proc.wait()


# --- the proxy this machine has configured ----------------------------------------
#
# The launcher says the same sentence about a proxy as the app does, and has
# to have its own copy of it: it may import nothing from `scribe` (ADR-011).
# `scribe/credentials.py` holds the other copy, and tests/test_proxy.py feeds
# both the same environment and compares what they say - the duplication is
# deliberate, and that test is what stops it drifting.
#
# This copy reads the environment only, which is what a child process
# inherits; the app's copy also looks at the Windows system proxy, because the
# setup engine shows a table and this prints one line.

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
NOT_A_HOST = "an address that could not be read"
HOST_CHARACTERS = re.compile(r"^[A-Za-z0-9._:*-]+$")


def proxy_host(environ: dict | None = None) -> str:
    """The host of the first proxy this machine has configured, or "".

    Only the parsed hostname and port are ever returned - never the value and
    never the authority, because both of those carry the `user:password@` that
    somebody may have put in a proxy URL. A bare `host:3128` parses as a
    *scheme*, so a value with no scheme is retried as the authority it is. A
    candidate with a path, a query or a fragment is refused: `urlsplit` ends
    the authority at the first `/`, `?` or `#`, so a password containing one
    would otherwise be read as a port and reported.
    """
    found = os.environ if environ is None else environ
    for name in PROXY_VARIABLES:
        for spelling in (name, name.lower()):
            value = (found.get(spelling) or "").strip()
            if not value:
                continue
            for candidate in ((value,) if "://" in value else (value, f"//{value}")):
                try:
                    parts = urllib.parse.urlsplit(candidate)
                    if parts.path not in ("", "/") or parts.query or parts.fragment:
                        continue
                    host, port = parts.hostname, parts.port
                except ValueError:  # a port that is not a number, or brackets that do not close
                    continue
                if host and HOST_CHARACTERS.match(host):
                    shown = f"[{host}]" if ":" in host else host  # an IPv6 address keeps its brackets
                    return f"{shown}:{port}" if port else shown
            return NOT_A_HOST
    return ""


def proxy_note(environ: dict | None = None) -> str:
    """The clause a failed download adds when a proxy is configured, or "".

    Word for word `scribe.credentials.proxy_note`'s: somebody behind a
    corporate proxy whose `uv sync` failed is otherwise told an exit code and
    nothing else.
    """
    host = proxy_host(environ)
    return f"; a proxy is configured at {host}, and the download did not get through it" if host else ""


def loopback_opener() -> urllib.request.OpenerDirector:
    """A urllib opener that ignores whatever proxy this machine has configured.

    Measured on 2026-09-21 (`docs/superpowers/specs/2026-09-20-installer-evidence/`
    `probe_product_proxy.output-2026-09-21.txt`): with HTTP_PROXY and
    HTTPS_PROXY at a closed port and no NO_PROXY, `running_instance` answered
    False after 1.08 s for an app that was answering - a second launch would
    have started a second app on a taken port.

    Built per call and never installed: `install_opener` is module-global
    state, and this process has requests that must keep using the proxy.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# What uv prints when it downloaded Python but could not make the junction
# beside it (GitHub issue 16, ADR-027). uv has no switch to skip that step.
MINOR_LINK_FAILURE = "Failed to create Python minor version link directory"


def downloaded_python(layout: Layout) -> Path | None:
    """The newest interpreter uv unpacked under the python dir, or None.

    Only a ``cpython-X.Y.Z-...`` folder counts: ``cpython-X.Y-...`` is the
    minor version link itself (or what is left of it), and a ``+`` variant
    such as freethreaded is not what the lock was made for.
    """
    found = []
    for folder in layout.python_dir.glob("cpython-*"):
        match = re.fullmatch(r"cpython-(\d+)\.(\d+)\.(\d+)-[^+]+", folder.name)
        executable = folder / "python.exe" if layout.windows else folder / "bin" / "python3"
        if match and executable.is_file():
            found.append((tuple(int(part) for part in match.groups()), executable))
    return max(found)[1] if found else None


def sync(layout: Layout, on_line: Callable[[str], None], base: dict | None = None) -> bool:
    """Sync the environment; write the stamp only when uv succeeded.

    The proxy named on the failure is the one the child was given, not this
    process's: a caller that hands `sync` a different environment would
    otherwise be told about the launcher's own.

    A sync that failed only on the minor version link is run once more with
    ``--python`` naming the interpreter uv already unpacked: uv makes that
    link only on the download path, and a venv does without it (ADR-027).
    """
    env = sync_environment(layout, base)
    seen: list[str] = []

    def line(text: str) -> None:
        seen.append(text)
        on_line(text)

    code = run_streaming(sync_command(layout), env=env, cwd=layout.home, on_line=line)
    python = downloaded_python(layout) if code != 0 and any(MINOR_LINK_FAILURE in text for text in seen) else None
    if python is not None:
        on_line("uv could not make its Python folder link; trying once more with the "
                f"Python it already downloaded ({python.parent.name})")
        code = run_streaming(sync_command(layout) + ["--python", str(python)],
                             env=env, cwd=layout.home, on_line=on_line)
    if code != 0:
        on_line(f"uv sync failed with exit code {code}{proxy_note(env)}")
        return False
    write_stamp(layout)
    return True


NETWORK_FAILURES = ("Failed to download", "Failed to fetch", "error sending request",
                    "dns error", "tcp connect error", "certificate", "timed out")
"""What uv 0.12.13 prints when the network is the trouble. Captured on
2026-10-20 with a closed proxy, an unknown host for the Python download and
one for the package index (TASK-113.02). Not "proxy": `proxy_note` adds that
word to every failed sync on a machine with a proxy, network or not."""


def install_failure(layout: Layout, said: Iterable[str]) -> str:
    """The sentence after a failed sync: the internet only when uv blamed it.

    Issue 16 failed on the file system and was told to check the internet
    connection, which was fine.
    """
    if any(marker in text for text in said for marker in NETWORK_FAILURES):
        return ("Installing failed; the lines above say why. Check the internet connection "
                "and start MyScribe again.")
    return (f"Installing failed on this computer; the lines above say why, and they are also in "
            f"{layout.logs_dir / INSTALL_LOG}.")


# --- what this install costs, and whether it fits ---------------------------------
#
# Every number comes out of the payload as data, because the launcher may
# import nothing from the app (ADR-011): the weights out of `models.json` as
# real bytes, the rest out of `footprint.json` as estimates that carry their
# date, and the app's own disk floor out of `doctor.py` as text - the way
# `app_version` reads the version and `setup_contract` the contract number.

GB = 2 ** 30
TIER = "turbo"
"""Which weights an install fetches by default. `scribe.models.DEFAULT_TIER`
is the same word; the drift test below is what keeps them the same."""

DRIVE_FIXED = 3  # DRIVE_FIXED from winbase.h, for GetDriveTypeW


def this_machine() -> str:
    """The processor this is running on - ``arm64`` on an Apple Silicon Mac."""
    return platform_info.machine()


def platform_key(platform: str) -> str:
    """How ``footprint.json`` spells this platform. Anything that is not
    Windows or macOS is read as Linux, which is what the rest of this file
    already assumes."""
    if platform == "win32":
        return "win32"
    if platform == "darwin":
        return "darwin"
    return "linux"


def mlx_here(platform: str, machine: str) -> bool:
    """Whether this machine loads the Apple conversions of the weights.

    `scribe.models.backend_here` answers this with `accel.mlx_available()`,
    which the launcher cannot call. It does not have to: mlx-whisper exists on
    Apple Silicon alone, so the platform and the processor answer it.
    """
    return platform == "darwin" and machine.lower() in ("arm64", "aarch64")


def _mapping(value) -> dict:
    """``value`` when it is an object, and an empty one when it is anything
    else. A file that says something unexpected is read as a file that says
    nothing, never as a crash before the first window."""
    return value if isinstance(value, dict) else {}


def _number(value) -> float | None:
    """``value`` when it is a number, None when it is anything else.

    What ``_mapping`` does one level up: the objects were made safe to read
    and the scalars inside them went straight into arithmetic, so a
    hand-edited ``"3.2"`` was still a TypeError with no window behind it.
    ``True`` is an int in Python and is not a size, so it is refused too."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def footprint(layout: Layout) -> dict:
    """``scribe/footprint.json`` as it ships, or ``{}`` when it cannot be read.

    Never raises: a question that cannot add every number up is still worth
    asking, and it says which part it could not read rather than showing a
    total that is quietly too low.
    """
    try:
        return _mapping(json.loads((layout.app_dir / "scribe" / "footprint.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def weights_bytes(layout: Layout, platform: str, machine: str) -> int:
    """What the speech weights cost this machine, from the catalogue that ships.

    Real bytes and not an estimate: ``models.json`` carries a size per file
    since TASK-089.16. Which rows this machine downloads is decided here the
    way ``scribe.models.wanted_here`` decides it - a row without a ``backends``
    key (the diarization pipeline) is fetched everywhere, the mlx conversions
    only on Apple Silicon, the rest everywhere else - and a drift test holds
    the two answers against each other per platform.
    """
    try:
        rows = json.loads((layout.app_dir / "scribe" / "models.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    mlx = mlx_here(platform, machine)
    total = 0
    for name, spec in rows.items():
        if name.startswith("_") or not isinstance(spec, dict):
            continue
        backends = spec.get("backends")
        if backends and ("mlx" in backends) != mlx:
            continue
        if spec.get("tier") not in (None, TIER):
            continue
        total += sum(int(f.get("size") or 0) for f in (spec.get("files") or {}).values())
    return total


def disk_floor_gb(layout: Layout) -> int:
    """``scribe.doctor.DISK_FLOOR_GB`` read as text (ADR-011).

    The number lives in the file whose change should move it, which is the
    reason TASK-089.11 reads the contract number the same way. 0 when it
    cannot be read: a refusal built on a number nobody could find is a refusal
    nobody can explain.
    """
    source = layout.app_dir / "scribe" / "doctor.py"
    try:
        match = re.search(r"^DISK_FLOOR_GB\s*=\s*(\d+)\s*$", source.read_text(encoding="utf-8"), re.M)
    except OSError:
        return 0
    return int(match.group(1)) if match else 0


def install_size(layout: Layout, platform: str | None = None, machine: str | None = None) -> dict:
    """What this install costs, in GB, with every part that could be read.

    Two kinds of number, and the question says which is which: the weights are
    measured bytes, the environment is a dated estimate from another machine.
    A part that could not be read is ``None`` and is left out of the totals,
    so a missing ``footprint.json`` makes the question smaller and never makes
    the refusal below wrong.

    ``needed_gb`` is what the volume must have free: the unpacked install plus
    the floor the app already enforces. An install that only just fits leaves
    a machine that refuses its first URL import (``doctor.DISK_FLOOR_GB``).
    """
    platform = sys.platform if platform is None else platform
    machine = this_machine() if machine is None else machine
    paper = footprint(layout)
    environment = _mapping(_mapping(paper.get("environment")).get(platform_key(platform)))
    ollama = _mapping(paper.get("ollama"))
    weights = weights_bytes(layout, platform, machine) / GB
    download = _number(environment.get("download_gb"))
    unpacked = _number(environment.get("unpacked_gb"))
    floor = disk_floor_gb(layout)
    installer = _number(_mapping(ollama.get("installer_bytes")).get(platform_key(platform)))
    model = _number(ollama.get("model_bytes"))
    return {
        # The weights are their own download, so their bytes are their size on
        # disk as well - no archive is unpacked.
        "weights_gb": weights,
        "environment_gb": download,
        "environment_note": ", ".join(part for part in (environment.get("measured"), environment.get("date")) if part),
        "floor_gb": floor,
        "download_gb": weights + (download or 0),
        "needed_gb": weights + (unpacked or 0) + floor,
        "complete": bool(download and unpacked),
        "ollama_gb": ((installer or 0) + model) / GB if model else None,
        "ollama_model": ollama.get("model") or "",
        "ollama_note": ", ".join(part for part in (ollama.get("measured"), ollama.get("date")) if part),
    }


def download_note(layout: Layout) -> str:
    """What the first start is about to fetch, from the payload's own numbers.

    A sentence and not a literal: the "about 3 GB" this replaced was written
    once and agreed with nothing afterwards.
    """
    size = install_size(layout)
    if not size["complete"]:
        return ("The first start downloads the speech engine and its models and shows its "
                "progress; how much that is has not been measured on this platform.")
    return f"The first start downloads about {size['download_gb']:.0f} GB and shows its progress."


def disk_probe_path(layout: Layout) -> Path:
    """Where free space is measured: the data directory, or the nearest parent
    that exists.

    ``scribe.doctor.disk_probe_path`` measures the same place for the app's own
    floor, so the launcher's refusal and the doctor's can never be about two
    different volumes. It walks further up than the doctor's one level,
    because before a first run neither ``D:\\MyScribe\\data`` nor
    ``D:\\MyScribe`` exists and ``shutil.disk_usage`` raises on a path that is
    not there.
    """
    for candidate in (layout.data_dir, *layout.data_dir.parents):
        if candidate.exists():
            return candidate
    return layout.data_dir


def free_gb(path: Path) -> float | None:
    """Free GB at ``path``, or None when it cannot be measured.

    Through the module, never ``from shutil import disk_usage``: the test
    suite replaces ``shutil.disk_usage`` for every test, and a name bound at
    import would not see it.
    """
    try:
        return shutil.disk_usage(path).free / GB
    except OSError:
        return None


def local_volumes(platform: str | None = None) -> list[Path]:
    """The volumes the question shows free space for.

    Only the Windows answer has been seen to be true here, the way
    ``launcher_path`` says of its own: ``GetLogicalDrives`` names the letters
    in use and ``GetDriveTypeW`` keeps the fixed ones, so a DVD drive, a card
    reader and a network drive are left out. macOS is the root plus what is
    mounted under ``/Volumes``, Linux the root plus ``/mnt`` and ``/media``,
    both deduplicated by device - the platforms' convention and nobody's
    measurement.

    Never raises, and an empty list is an answer: ``location_question`` adds
    the volume the default home is on, so a question with no disk space on it
    is not a thing that can happen.
    """
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            mask = kernel32.GetLogicalDrives()
            letters = (f"{chr(ord('A') + index)}:\\" for index in range(26) if mask & (1 << index))
            return [Path(root) for root in letters if kernel32.GetDriveTypeW(root) == DRIVE_FIXED]
        except (AttributeError, OSError):
            return []
    roots = [Path("/")]
    for base in (Path("/Volumes"),) if platform == "darwin" else (Path("/mnt"), Path("/media")):
        try:
            roots += sorted(entry for entry in base.iterdir() if entry.is_dir())
        except OSError:
            continue
    seen: set = set()
    found = []
    for root in roots:
        try:
            device = root.stat().st_dev
        except OSError:
            continue
        if device not in seen:
            seen.add(device)
            found.append(root)
    return found


def enough_disk(layout: Layout, report: Callable[[str, str], None], *, floor: bool = True) -> bool:
    """False, with both numbers, when this volume cannot hold the install.

    Checked before the sync and not after it: a disk that runs out halfway
    through a 3 GB download leaves a half-built environment and a person who
    has to work out what happened. A volume that cannot be measured does not
    refuse - "we do not know" must not become "nothing may be installed here",
    which is the rule ``doctor.require_disk_headroom`` already follows.

    ``floor=False`` drops ``doctor.DISK_FLOOR_GB`` from what is required. That
    figure is the room MyScribe keeps free to *run* with a library of
    recordings, and it is right for a person installing this. It is wrong for
    ``--smoke``, which syncs, answers ``/health``, serves a page and quits with
    no library at all: on Windows the floor is 10 of the 16.6 GB, and a hosted
    runner arrives with less than that (the Linux job already clears disk to
    get near it, and no such step exists for Windows). So CI is asked for the
    install it actually makes, 6.6 GB, and a person is still asked for all of
    it. Decided by Robert on 2026-09-22, knowing the cost: a future smoke that
    does need the working room would no longer be covered here.
    """
    size = install_size(layout)
    needed = size["needed_gb"]
    if not floor:
        needed = needed - size["floor_gb"] if needed else needed
    probe = disk_probe_path(layout)
    free = free_gb(probe)
    if not needed or free is None or free >= needed:
        return True
    floor = size["floor_gb"]
    keeps = (f", plus the {floor} GB MyScribe keeps free for your recordings" if floor else "")
    report("error", f"There is not enough room: MyScribe needs about {needed:.0f} GB for this "
                    f"install{keeps}, and {probe} has {free:.1f} GB free. Nothing was downloaded. "
                    "Free up space, or start MyScribe again and put it on another drive.")
    return False


# --- where everything goes: the one question the launcher asks itself --------------
#
# Every other question belongs to `scribe.setup` and is asked by a front-end
# that only renders it (ADR-015). This one cannot: `env/`, `python/`, `cache/`,
# `data/` and the models all hang off the home, so where they go has to be
# settled before the environment that would answer it exists.


def wants_location(default: Path, payload: Path, pointer: Path,
                   home_flag: Path | None = None, environ: dict | None = None) -> bool:
    """Whether to ask where everything goes - a pure predicate, like ``wants_setup``.

    Only on a first run, and only when nobody has already said where: a
    pointer file, ``MYSCRIBE_HOME`` and ``--home`` are all answers. A home that
    already holds an environment is an install, and an install is not moved by
    a question (AC #7): moving one means quitting, moving the folder by hand
    and writing its new path into the pointer file.
    """
    environ = os.environ if environ is None else environ
    if home_flag is not None or environ.get(HOME_VARIABLE):
        return False
    # Whether the file names a home, never whether the file is there: another
    # writer may put its own fact in it (ADR-015, TASK-089.19) before anybody
    # was ever asked this, and a file that answers a different question is no
    # answer to this one.
    document, problem = read_pointer(pointer)
    named = document.get("home")
    if not problem and isinstance(named, str) and named.strip():
        return False
    return not Layout(default, payload).env_python.exists()


def install_dir() -> Path | None:
    """The folder this launcher was installed into, or None when nothing was
    frozen.

    On Windows the installer owns it - ``%LOCALAPPDATA%\\Programs\\MyScribe``
    (``packaging/windows/myscribe.iss``) - and an uninstall takes it with
    everything inside it. On macOS the install *is* the ``.app`` bundle, so
    that is what a home must stay out of, not the whole of ``/Applications``.
    """
    launcher = this_launcher()
    if launcher is None:
        return None
    return launcher if launcher.suffix == ".app" else launcher.parent


def _inside(child: Path, parent: Path) -> bool:
    """Is ``child`` ``parent`` itself, or under it?

    Compared part by part after ``normcase`` and never as strings:
    ``D:\\MyScribeData`` starts with ``D:\\MyScribe`` and is not inside it.
    ``child`` is resolved by the caller and ``parent`` here, so a symlink or a
    junction that points into the install directory is caught.
    """
    try:
        wanted = [os.path.normcase(part) for part in parent.resolve().parts]
    except OSError:
        return False
    return [os.path.normcase(part) for part in child.parts][:len(wanted)] == wanted


def refuse_location(chosen: str, install: Path | None = None, payload: Path | None = None) -> str:
    """Why this folder cannot hold MyScribe, or ``""`` when it can.

    The order is the point, because ``resolve()`` would mask two of them: a UNC
    path is absolute and resolves to itself, and a path that cannot be
    resolved at all would raise before anything read it. So: a share first,
    then a path that is not absolute, then the two folders ADR-011 forbids,
    and only then the one check that touches the disk - which leaves it as it
    found it: a question that is still open may not create folders.

    Covered: a relative path, ``..``, a symlink or junction into the install
    directory (``resolve`` follows it), a UNC path, the install directory, the
    payload, and a folder that cannot be written. Not covered, and the share
    refusal says so: a mapped drive letter, which is a share with a letter in
    front of it and looks like a disk from here.
    """
    text = str(chosen).strip().strip('"')
    if not text:
        return "Nothing was typed. Choose a folder, or keep the default."
    if text.startswith("\\\\") or text.startswith("//"):
        return ("A folder on a network share cannot hold the library: MyScribe keeps it in "
                "SQLite with a write-ahead log, and SQLite's own documentation says that does "
                "not work over a network filesystem. Choose a folder on a disk in this machine. "
                "A mapped drive letter is the same share with a letter in front of it, and "
                "MyScribe cannot tell one from a real disk - so do not use one either.")
    path = Path(text)
    if not path.is_absolute():
        return (f"{text} is not a full path. Give the whole path, the drive or the mount point "
                "included, for example D:\\MyScribe or /Volumes/Data/MyScribe.")
    try:
        resolved = path.resolve()
    except OSError as error:
        return f"{text} cannot be read as a folder ({error})."
    for forbidden in (install, payload):
        if forbidden is not None and _inside(resolved, forbidden):
            return (f"{resolved} is inside {forbidden}, which belongs to the installer. The next "
                    "update or uninstall deletes that folder and everything in it, and your "
                    "recordings with it. Choose a folder outside it.")
    # In the nearest folder that already exists, and nothing is created: a
    # path with a typo in it would otherwise leave an empty tree behind on a
    # disk nobody meant to touch, written while the question is still open.
    # `exists` and not `is_dir`, so a file where a folder should be stops the
    # walk and fails the probe, which is the answer.
    here = next((candidate for candidate in (resolved, *resolved.parents) if candidate.exists()),
                resolved)
    probe = here / ".myscribe-write-test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        return (f"MyScribe cannot write in {resolved} ({error}). Choose a folder you can write "
                "to, or make that one writable first.")
    return ""


ROUTES_LATER = (
    "There is no Settings page for this one: the folder holds the environment MyScribe itself "
    "runs from, so the running app cannot move it. It can be answered later in three ways: set "
    "{variable}, start MyScribe with --home PATH, or quit, move the folder by hand and write its "
    "new path into {pointer}. Nothing is ever moved for you."
)


def location_question(layout: Layout, pointer: Path, volumes: Iterable[Path] | None = None,
                      platform: str | None = None, machine: str | None = None) -> str:
    """The question itself, as text: the default home, the free space per
    volume, what this install downloads and the room it needs.

    ``layout.home`` is the default home, and the volumes are handed in so a
    test can put two of them on a machine that has one.
    """
    platform = sys.platform if platform is None else platform
    size = install_size(layout, platform, machine)
    volumes = local_volumes(platform) if volumes is None else list(volumes)
    # The disk the default home is on is always one of them, whatever the
    # enumerator found: a question about disk space with no disk space under
    # it would be the one rendering nobody ever sees in a test.
    anchor = Path(layout.home.anchor) if layout.home.anchor else None
    if anchor is not None and anchor not in volumes:
        volumes.append(anchor)
    lines = [
        f"Where should {APP_NAME} keep everything?",
        "",
        f"{APP_NAME} keeps the speech engine, the models, your recordings and its database in "
        f"one folder. The default is {layout.home}.",
        "",
    ]
    parts = [f"{size['weights_gb']:.1f} GB of speech models for this machine (measured, from the "
             "catalogue that ships)"]
    if size["environment_gb"]:
        parts.insert(0, f"about {size['environment_gb']:.1f} GB of speech engine "
                        f"({size['environment_note']})")
    else:
        parts.insert(0, "the speech engine, whose size nobody has measured on this platform")
    lines.append("This install downloads " + ", and ".join(parts) + ".")
    floor = size["floor_gb"]
    needs = f"It needs about {size['needed_gb']:.0f} GB free where it lands"
    lines.append(needs + (f" - the install unpacked, plus the {floor} GB {APP_NAME} keeps free so "
                          "that importing a recording is never refused for want of room."
                          if floor else "."))
    if size["ollama_gb"]:
        # "About", not "up to": the offer installs Ollama's newest release,
        # whose size is only known when it is fetched (TASK-095), so this is
        # an estimate from one past release and its note says so.
        lines.append(f"About {size['ollama_gb']:.1f} GB more if you later say yes to Ollama - its "
                     f"installer and the {size['ollama_model']} model ({size['ollama_note']}).")
    lines.append("")
    lines.append("Free space now:")
    for volume in volumes:
        free = free_gb(volume)
        lines.append(f"  {volume}  {free:.1f} GB free" if free is not None
                     else f"  {volume}  could not be measured")
    lines += [
        "",
        f"Keep the default, or give a folder on another disk. {APP_NAME} does not move an "
        "install that is already there: this question is asked once, before anything is "
        "downloaded.",
        "",
        ROUTES_LATER.format(variable=HOME_VARIABLE, pointer=pointer),
        "",
        "Not a folder on a network share: the library is SQLite with a write-ahead log, which "
        "does not work over a network filesystem. A mapped drive letter hides the same problem "
        f"and {APP_NAME} cannot detect it.",
    ]
    return "\n".join(lines)


def location_note(home: Path, pointer: Path, source: str) -> str:
    """What ``--setup`` says about where everything is, and how to move it."""
    return (f"{APP_NAME} keeps everything in {home}, which came from {source}. "
            + ROUTES_LATER.format(variable=HOME_VARIABLE, pointer=pointer))


def locate_home(home_flag: Path | None, ask: Callable[[str], str | None] | None,
                report: Callable[[str, str], None], payload: Path,
                platform: str | None = None, environ: dict | None = None,
                pointer: Path | None = None, install: Path | None = None,
                volumes: Iterable[Path] | None = None) -> Path | None:
    """Where everything goes, settled before anything is written or downloaded.

    Returns the home, or None when the launcher must stop - which is what a
    pointer naming a folder that is not there does on a door that cannot ask.
    Falling back to the default home there would build a second, empty library
    on C: and look to its owner as though the recordings were gone.

    ``ask`` is None for every door where nobody is at the screen: the console,
    a start at login, ``--sync-only``, ``--doctor`` and ``--smoke``. They keep
    today's location, say in one line how to change it, and are still refused
    a sync that does not fit.
    """
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    pointer = pointer_path(platform, environ) if pointer is None else Path(pointer)
    default = default_home(platform, environ)
    install = install_dir() if install is None else install

    if home_flag is not None:
        return Path(home_flag)
    if environ.get(HOME_VARIABLE):
        return Path(environ[HOME_VARIABLE])

    document, problem = read_pointer(pointer)
    named = document.get("home")
    if not problem and isinstance(named, str) and named.strip():
        home = Path(named.strip())
        if home.exists():
            return home
        problem = (f"{pointer} says {APP_NAME} keeps everything in {home}, and that folder is not "
                   "there. If it is on a drive that is not plugged in, quit, plug it in and start "
                   f"{APP_NAME} again: nothing has been moved and nothing is lost.")

    if not problem and not wants_location(default, payload, pointer, home_flag, environ):
        return default  # answered already, or an install nothing here moves

    if ask is None:
        if problem:
            report("error", f"{problem} {APP_NAME} cannot start until {pointer} names a folder "
                            "that is there; it will not quietly start a second, empty library "
                            "somewhere else.")
            return None
        report("status", f"{APP_NAME} keeps everything in {default}; nothing was asked, because "
                         "nobody is at the screen. "
                         + ROUTES_LATER.format(variable=HOME_VARIABLE, pointer=pointer))
        return default

    if problem:
        # Said out loud as well as shown on the question: the console and the
        # log are where somebody looks afterwards for why they were asked.
        report("error", problem)
    question = location_question(Layout(default, payload), pointer, volumes, platform)
    message = f"{problem}\n\n{question}" if problem else question
    while True:
        answer = ask(message)
        if answer is None:
            if problem:
                report("error", f"Nothing was chosen, and {pointer} still names a folder that is "
                                f"not there. {APP_NAME} cannot start.")
                return None
            return default
        refusal = refuse_location(str(answer), install=install, payload=payload)
        if refusal:
            report("error", refusal)
            message = f"{refusal}\n\n{question}"
            continue
        home = Path(str(answer).strip().strip('"')).resolve()
        # A pointer that could not be written is worth one sentence and not a
        # stop: the folder they chose is usable now, and the sentence says how
        # to keep it for the next start.
        saved = write_pointer(pointer, home)
        if saved:
            report("error", saved)
        return home


# --- one instance -----------------------------------------------------------------


def health_url(port: int) -> str:
    return f"http://{HOST}:{port}/health"


def running_instance(port: int, timeout: float = 1.0) -> bool:
    """Whether MyScribe already answers on ``port``."""
    try:
        with loopback_opener().open(health_url(port), timeout=timeout) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("ok"))
    except (OSError, ValueError):
        return False


# TASK-112.07: the /health probe and the bind test below leave a window
# between them and the app's own bind. A login entry and a start by hand in the
# same seconds both passed the probe, both ran `uv sync` into one environment
# and both started the app. One lock per home closes that window; the probe
# still runs after it, because an app whose launcher was killed outlives it.


def instance_lock_path(home: Path) -> Path:
    """``<home>.lock``, beside the home and not in it.

    Not in it, because on a first start the home does not exist yet and must
    not be made before the disk and the location are checked - and the lock
    has to be taken before any of that. Beside the default home that is next
    to ``MyScribe.location``; beside a home given with ``--home X`` it is
    ``X.lock``, so a test or a second install never waits on the one the
    login entry started.
    """
    return beside_home(home, ".lock")


def beside_home(home: Path, suffix: str) -> Path:
    """``<home><suffix>`` beside the home, or ``<root>/MyScribe<suffix>`` in a
    home that is a drive root: a root has no name to add to and nothing
    beside it, and `refuse_location` allows one (review of TASK-112.07)."""
    home = Path(home)
    if not home.name:
        return home / f"{APP_NAME}{suffix}"
    return home.with_name(home.name + suffix)


class InstanceLock:
    """An exclusive, non-blocking lock on byte 0 of one file, held while this
    launcher has a MyScribe starting or running, and released by the OS if it
    dies.

    After byte 0 the holder writes its port, so that a second start with
    another ``--port`` opens the one that runs instead of waiting on its own
    (`held_port`). After it and not on it: a Windows lock is mandatory, and
    another process cannot even read the byte it covers.

    Never unlinked: on POSIX a second launcher that opened the old file and a
    third that made a new one would each hold a lock of their own. The handle
    is not inheritable (PEP 446), so the app child never keeps it.

    A window lets go of it while it stays open and takes it again for its
    Setup button (`window_turn`), so `acquire` and `release` may be called
    from two threads and again by a holder: both are guarded, and a second
    `acquire` by the holder is a yes - a second handle in this process would
    find its own lock held, per handle on Windows and per open file on POSIX.
    """

    def __init__(self, path: Path, port: int | None = None):
        self.path = Path(path)
        self.port = port
        self.handle = None
        self.problem = ""
        self.guard = threading.Lock()

    def acquire(self) -> bool:
        """True when this launcher holds the lock - or could not take one at
        all, which starts as before the lock existed rather than not at all;
        False when another process holds it."""
        with self.guard:
            if self.handle is not None:
                return True
            return self._acquire()

    def _acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b")
        except OSError as error:
            self.problem = f"{self.path} could not be opened ({error}); starting without the lock."
            return True
        try:
            handle.seek(0)
            if IS_WINDOWS:
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # never LK_LOCK: it retries 10 s
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        except OSError as error:
            handle.close()
            if IS_WINDOWS:  # msvcrt says "held" as a plain OSError (EACCES or EDEADLOCK)
                return False
            self.problem = f"{self.path} could not be locked ({error}); starting without the lock."
            return True
        self.handle = handle
        if self.port is not None:
            try:  # "a+b" appends: after the truncate that is byte 0, then the port
                handle.truncate(0)
                handle.write(b" " + str(self.port).encode("ascii"))
                handle.flush()
            except OSError:
                pass  # a second start then waits on its own port, as before
        return True

    def release(self) -> None:
        """Explicitly, and not by closing alone: Windows does not promise when
        a lock goes on close, and `main()` may run again in the same process."""
        with self.guard:
            handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            if IS_WINDOWS:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            handle.close()


def held_port(path: Path) -> int | None:
    """The port the holder of the lock at `path` wrote after byte 0, or None
    when there is none to read: no file, an older holder, or one that has not
    written it yet."""
    try:
        with open(path, "rb") as handle:
            handle.seek(1)
            text = handle.read(16).decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() and 0 < int(text) < 65536 else None


ALREADY_STARTING = ("MyScribe is already starting; installing or updating can take a few minutes. "
                    "It will be at {url} when it is ready.")
STILL_STARTING = "MyScribe is still starting and has not answered yet. Go to {url} in a few minutes."
ONE_JOB_LOCKED = ("Another MyScribe is already running or installing in {home}; "
                  "quit it first, then try again.")
DOCTOR_LOCKED = ("MyScribe is installing or updating in {home}; run the doctor again "
                 "when that is done.")


def another_start(port: int, open_browser: bool, report: Callable[[str, str], None],
                  windowed: bool) -> int:
    """A start that found the lock held: open what the other one serves.

    Exit 0 on every path, `APP_DIED`'s contract: a restart would find the same
    lock. Behind a window there is no console, so a first start that is still
    installing is answered at once with a window of its own - not with a
    double-click that does nothing for minutes.
    """
    url = f"http://{HOST}:{port}/"
    if not running_instance(port):
        sentence = ALREADY_STARTING.format(url=url)
        if windowed:
            report("status", sentence)
            show_error(sentence)
            return 0
        report("status", sentence)
        deadline = time.monotonic() + READY_TIMEOUT
        while not running_instance(port, timeout=0.5):
            if time.monotonic() >= deadline:
                report("status", STILL_STARTING.format(url=url))
                return 0
            time.sleep(0.5)
    if open_browser:
        open_in_browser(url, report)
    report("done", f"MyScribe is already running at {url}")
    return 0


# AC 3 of TASK-112.07: a start at login has nobody at the screen, and the
# sentence it ends on is a line in a log nobody reads. It is kept in a note
# beside the home, which the next start somebody makes by hand says once and
# removes. A note and not the log read back: ADR-014 keeps logs observation.

# "On its own" and not "at login": the Windows installer starts the same
# `--at-login --headless` after an update (myscribe.iss, ssPostInstall).
LOGIN_PROBLEM = ("The last time MyScribe started on its own, at login or after an update, "
                 "it could not start: {problem}")


def login_note_path(home: Path) -> Path:
    return beside_home(home, "-at-login.txt")


def keep_login_problem(home: Path, problem: str) -> None:
    try:
        login_note_path(home).write_text(problem, encoding="utf-8")
    except OSError:
        pass  # the log has it too; a note that cannot be written stops nothing


def take_login_problem(home: Path) -> str:
    """The sentence for the last login's problem, once; "" when there was none."""
    note = login_note_path(home)
    try:
        problem = note.read_text(encoding="utf-8").strip()
        note.unlink()
    except OSError:
        return ""
    return LOGIN_PROBLEM.format(problem=problem) if problem else ""


def drop_login_problem(home: Path) -> None:
    """A start at login that serves, or finds MyScribe serving, makes the
    last one's problem history: the next start by hand must not say it."""
    try:
        login_note_path(home).unlink()
    except OSError:
        pass  # none there, the usual case


# --- stopping the one that runs (TASK-112.03) ----------------------------------


STOP_WAIT = 80.0
"""How long `--stop` waits for the app to end through its own stop route.

The route's way out nests inside this: uvicorn cuts the open reads after 3 s
and lets an upload finish for up to 30 s (`APP_SIGTERM_WAIT` adds the whole
stop up to 72 s; on Windows the hard stop after this wait is taskkill /F),
the lifespan's supervisor ends the runner's tree and requeues its job
before the feeds and the watcher stop, joining for `STOP_JOIN_SECONDS` (20 s,
`scribe/supervisor.py`). Past this the hard stop runs, inside which
`AppProcess.stop` gives the app another `APP_SIGTERM_WAIT`. The launcher's own
Quit waits as long (`Launch.stop_wait`); `LAUNCHER_STOP_WAIT` adds up what
that stop may take when `--stop` signals a launcher."""

HEALTH_TIMEOUT = 2.0
"""How long one read of /health may take (`read_health`)."""

ROUTE_TIMEOUT = 10.0
"""How long the stop route may take to answer (`ask_to_stop`)."""

# A stop claims no new job but may wait up to 30 s for an upload (ADR-032), and
# the running job can finish inside that wait; the sentences leave room for it
# (review of TASK-112.09: a real run saw job 1 end done 3.2 s into the stop).
STOPPED = ("MyScribe stopped. A job that was running is back at the front of the queue and "
           "starts over the next time MyScribe starts, unless it finished before MyScribe stopped.")

STOPPING = "Stopping MyScribe..."
STOPPING_JOB = ("Stopping MyScribe. Job {job} goes back to the front of the queue and starts over "
                "the next time MyScribe starts, unless it finishes first.")
STOPPING_IDLE = "Stopping MyScribe. No job is running."
STOPPED_JOB = ("MyScribe stopped. Job {job} is back at the front of the queue and starts over the "
               "next time MyScribe starts, unless it finished before MyScribe stopped.")
STOPPED_IDLE = "MyScribe stopped. No job was running."
ENDED = ("MyScribe was ended. A job that was running is marked interrupted; Retry on the "
         "jobs page starts it over.")
ENDED_KILLED = ("MyScribe was ended. A job that was running can keep running after MyScribe has closed "
                "and finish by itself; if it does not, Retry on the jobs page starts it over.")
ENDED_ASKED = ("MyScribe was ended after it had been asked to stop. A job that was running may have "
               "finished first; if not, it is back at the front of the queue if MyScribe got that far, "
               "and otherwise it is marked interrupted and Retry on the jobs page starts it over.")
ENDED_ASKED_KILLED = ("MyScribe was ended after it had been asked to stop. A job that was running may "
                      "have finished first; if not, it is back at the front of the queue if MyScribe got "
                      "that far, and otherwise it can keep running after MyScribe has closed and finish "
                      "by itself, and if it does not, Retry on the jobs page starts it over.")
"""What Quit, Ctrl+C, a SIGTERM and `--stop` say before and after a stop
(TASK-112.09, criterion 4): the route's answer names the running job, so the
sentence before the stop can name it too. The ENDED sentences are the hard
stop's, see `ended`."""


def ended(killed: bool | None = False, asked: bool = False) -> str:
    """The hard stop's sentence for this OS and this stop.

    On Windows the hard stop ends the app's tree, runner and all, and
    reconcile marks the job interrupted. On macOS and Linux it signals the
    app's process group only, and the runner has a session of its own. An app
    that lives through that SIGTERM ends the runner itself (ADR-032), so the
    job is interrupted there too. ``killed`` is True when the app had to be
    SIGKILLed, and None when this process cannot see it (`--stop` ending a
    launcher, whose own hard stop does that): then the runner may have run on
    and written its verdict (ADR-028, measured in WSL on 2026-10-21), and
    "marked interrupted" would not be sure to be true.

    ``asked`` is True when the stop route may have been reached before the
    hard stop (`route_reached`): that app may have requeued the job before it
    hung, or not got that far, so the sentence names both outcomes."""
    sure = IS_WINDOWS or killed is False
    if asked:
        return ENDED_ASKED if sure else ENDED_ASKED_KILLED
    return ENDED if sure else ENDED_KILLED


def route_reached(error: Exception | None) -> bool:
    """Whether the stop route may have got the request, given what
    `ask_to_stop` raised (None: it answered). A timeout may be a route that
    set the app's stop flag and then answered too late (`ROUTE_TIMEOUT`); a
    refused connection or a broken answer never got that far."""
    if error is None:
        return True
    reason = getattr(error, "reason", None)  # urllib wraps what the socket raised
    return isinstance(error, TimeoutError) or isinstance(reason, TimeoutError)


def read_health(port: int, timeout: float = HEALTH_TIMEOUT) -> dict | None:
    """What /health says, or None when nothing that is MyScribe answers."""
    try:
        with loopback_opener().open(health_url(port), timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) and body.get("ok") else None


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid still runs.

    Never `os.kill(pid, 0)` on Windows: there it is TerminateProcess with exit
    code 0, so the question would be the stop. A handle that waits for
    nothing answers it instead; one this account may not open is a process
    that runs (ctypes is standard library, ADR-011)."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it is there
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return not pid_alive(pid)


LAUNCHER_STOP_WAIT = (HEALTH_TIMEOUT + ROUTE_TIMEOUT + STOP_WAIT  # Launch.stop's route, asked again
                      + STOP_TIMEOUT  # Launch.stop_setup
                      + APP_SIGTERM_WAIT + STOP_TIMEOUT  # AppProcess.stop: SIGTERM, wait, SIGKILL, wait
                      + 10)
"""How long a launcher gets after SIGTERM before it is killed. Its own stop
(`Launch.stop`, TASK-112.09) reads /health, asks the stop route again and
waits up to `STOP_WAIT` for the app, stops a setup child, and only then runs
`AppProcess.stop`, which may take `APP_SIGTERM_WAIT` and a `STOP_TIMEOUT` over an app that hangs -
the app the hard stop is for. Killing it sooner exits it non-zero, the restart
signal of `APP_DIED`. A launcher that ignores SIGTERM costs `--stop` this long
before its SIGKILL."""


def end_tree(pid: int, grace: float = APP_SIGTERM_WAIT) -> bool:
    """End a process and what it started, the way `AppProcess.stop` does.

    Given the launcher's pid, POSIX sends it SIGTERM alone: its own handler
    stops the app's process group and exits 0, so a supervisor sees a clean
    stop and does not start the server `--stop` just ended (TASK-112.02).
    Given the app's pid, its session is ended - it leads one when a launcher
    started it - or just the process when it does not.

    True when SIGTERM was not enough and SIGKILL was sent (POSIX only): an
    app killed that way may have left its runner running (`ended`)."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return False
    try:
        kill, target = (os.killpg, pid) if os.getpgid(pid) == pid else (os.kill, pid)
        kill(target, signal.SIGTERM)
        if not wait_gone(pid, grace):
            kill(target, signal.SIGKILL)
            return True
    except (ProcessLookupError, PermissionError):
        pass  # gone, or a macOS group of unreaped members (EPERM)
    return False


def stop_running(port: int, report: Callable[[str, str], None], wait: float = STOP_WAIT) -> int:
    """`MyScribe --stop`: ask the MyScribe on ``port`` to stop, and end it
    when it does not (TASK-112.03, ADR-028).

    The route first, because only it puts the running job back in the queue;
    the hard stop leaves that job to reconcile, which marks it interrupted,
    and the sentence says so. Waits for the app's pid and not for the port:
    uvicorn closes the listener before the lifespan has stopped the runner.
    0 when nothing runs any more, 1 when something still does.
    """
    health = read_health(port)
    if health is None:
        report("status", f"MyScribe is not running at http://{HOST}:{port}/; nothing to stop.")
        return 0
    pid = health.get("pid")
    if not isinstance(pid, int):
        report("error", "This MyScribe is older than --stop. Quit it from its window, or end "
                        "MyScribe in Task Manager or Activity Monitor.")
        return 1
    try:
        ask_to_stop(port)
    except (OSError, ValueError) as error:
        asked = route_reached(error)
        report("busy", f"The stop route did not answer ({error}); ending MyScribe instead.")
    else:
        asked = True
        report("busy", f"Asked MyScribe to stop; waiting up to {wait:g} s for it.")
        if wait_gone(pid, wait):
            report("status", STOPPED)
            return 0
        report("busy", f"MyScribe did not stop within {wait:g} s; ending it.")
    launcher = health.get("launcher_pid")
    killed: bool | None = False
    if isinstance(launcher, int) and pid_alive(launcher):
        end_tree(launcher, grace=LAUNCHER_STOP_WAIT)
        killed = None  # the launcher's own hard stop ends the app, out of sight here
    if pid_alive(pid) and end_tree(pid):  # no launcher, or one that went without its app
        killed = True
    if wait_gone(pid, STOP_TIMEOUT):
        report("status", ended(killed, asked=asked))
        return 0
    report("error", f"MyScribe (process {pid}) is still running and could not be ended.")
    return 1


def ask_to_stop(port: int) -> dict:
    """POST the stop route of the MyScribe on ``port`` and give back its
    answer: ``{"stopping", "pid", "running_job"}`` (`scribe/shutdown.py`).
    Raises OSError or ValueError when the route does not answer.

    The half of a stop that `--stop` and the launcher's own Quit share; what
    each does when it fails is its own (TASK-112.09)."""
    request = urllib.request.Request(f"http://{HOST}:{port}/api/shutdown", data=b"", method="POST")
    with loopback_opener().open(request, timeout=ROUTE_TIMEOUT) as response:
        body = json.loads(response.read().decode("utf-8") or "{}")
    return body if isinstance(body, dict) else {}


def port_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Bound the way uvicorn binds. Without SO_REUSEADDR a POSIX bind fails
        # for about a minute after a server on the port stopped, on the
        # connections it left in TIME_WAIT, and a start right after a stop said
        # the port was taken (TASK-118). A listening socket still refuses the
        # bind. Not on Windows, where the option lets a bind take a port that
        # is in use.
        if not IS_WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
        except OSError:
            return True
    return False


# --- the app process --------------------------------------------------------------


class AppProcess:
    """``python -m scribe`` from the environment, in a process group of its
    own so Quit reaches the runner children it spawns."""

    def __init__(self, layout: Layout, port: int, base: dict | None = None):
        self.layout = layout
        self.port = port
        self.base = base
        self.proc: subprocess.Popen | None = None

    def command(self) -> list[str]:
        return [str(self.layout.env_python), "-m", "scribe", "--no-browser", "--port", str(self.port)]

    def start(self) -> None:
        log = open(self.layout.logs_dir / "app.log", "ab", opener=OWNER_ONLY)
        try:
            kwargs: dict = {}
            if IS_WINDOWS:
                kwargs["creationflags"] = NO_WINDOW | NEW_GROUP
            else:
                kwargs["start_new_session"] = True
            self.proc = subprocess.Popen(
                self.command(), cwd=str(self.layout.app_dir),
                env=app_environment(self.layout, self.base),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kwargs,
            )
        finally:
            log.close()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self) -> int | None:
        """What the app ended with, or None while it runs or before it started."""
        return None if self.proc is None else self.proc.poll()

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive():
                return False
            if running_instance(self.port, timeout=0.5):
                return True
            time.sleep(0.25)
        return False

    def stop(self, timeout: float = APP_SIGTERM_WAIT) -> bool:
        """SIGTERM to the group, so uvicorn shuts the supervisor down and the
        supervisor ends the runner's tree (ADR-032); SIGKILL after ``timeout``,
        which by default outlasts the app's whole shutdown (`APP_SIGTERM_WAIT`).
        The runner has a session of its own, so that SIGKILL never reaches it.
        Windows has no signal that reaches a windowless process, so there it
        is taskkill on the tree; the next start's reconcile settles an
        interrupted job.

        True when the app outlived its SIGTERM and was SIGKILLed (POSIX only),
        so that `ended` can say its runner may have run on."""
        if self.proc is None or self.proc.poll() is not None:
            return False
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
            )
            self.proc.wait(timeout)
            return False
        try:
            group = os.getpgid(self.proc.pid)
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            self.proc.wait(timeout)
            return False
        killed = False
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            killed = True
        try:
            os.killpg(group, signal.SIGKILL)  # stragglers: a runner mid-model-load
        except (ProcessLookupError, PermissionError):
            # Gone, or - macOS - a group of exited members not yet reaped,
            # which answers EPERM rather than ESRCH.
            pass
        self.proc.wait(min(timeout, STOP_TIMEOUT))  # a SIGKILL is not waited out
        return killed


# --- what the launcher says, and where it is kept ---------------------------------


INSTALL_LOG = "launcher.log"
SMOKE_LOG = "launcher-smoke.log"
SYNCED = "The speech engine is installed and up to date."
UP_TO_DATE = "The speech engine was already up to date; nothing was downloaded."
REDACTED = "***"


def early_log_path(pointer: Path | None = None) -> Path:
    """Where the lines said before a home exists are kept: beside the pointer
    file, as ``MyScribe-launcher.log`` next to ``MyScribe.location``.

    Beside it for the reason the pointer is there: its folder exists whether
    or not the home does, and nothing has to be made under a default home a
    moved MyScribe no longer uses. Behind ``--windowed`` and at login there is
    no stdout, so without this the drive that was not plugged in ended the
    launcher without a word anywhere (TASK-112.02).
    """
    pointer = pointer_path() if pointer is None else pointer
    return pointer.with_name(pointer.stem + "-launcher.log")


def write_early(lines: Iterable[str], pointer: Path | None = None) -> None:
    """Append to the early log, each line with the time it was written.

    Timestamped where launcher.log is not: this file is not rewritten per
    install, and the next login's lines land under the last one's."""
    log = InstallLog(early_log_path(pointer))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    for line in lines:
        log.write(f"{stamp} {line}")


class InstallLog:
    """Every line the launcher reported, appended to a file under the home.

    Opened lazily, because the first lines are said before the home exists -
    and never raises: a windowed build has no stderr, so a log that could not
    be written must not be the thing that stops the install.

    Secrets are redacted by value. The launcher knows which answers are
    secrets because the plan says so (`kind == "secret"`), and an answer is
    hidden here the moment the sitting hands it over - before the child that
    might echo it has even started. ADR-015's Must Not is the rule: a secret
    goes to its settings row and to nowhere else, a log included.
    """

    def __init__(self, path: Path):
        self.path = path
        self.secrets: list[str] = []
        self.pending: list[str] = []

    def hide(self, value: str) -> None:
        """Never redact the empty string: `"".replace("", "***")` puts the
        mark between every character of every line."""
        if value and value not in self.secrets:
            self.secrets.append(value)

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        return text

    def write(self, text: str) -> None:
        """Append one line, or hold it until there is somewhere to put it.

        Nothing is created here. The home may still turn out to be one that
        cannot hold the install, or one a MyScribe that already serves owns,
        and a log file is not a reason to build a folder under either.
        `prepare_home` makes the folder, and the first write after that
        carries everything said before it, in order.
        """
        self.pending.append(text)
        if not self.path.parent.exists():
            return
        try:
            with open(self.path, "a", encoding="utf-8", opener=OWNER_ONLY) as handle:
                handle.writelines(line + "\n" for line in self.pending)
        except OSError:
            return  # the lines wait; a log that cannot be written stops nothing
        self.pending.clear()


def progress_line(line: str) -> dict | None:
    """The engine's JSON progress line, or None for a line of prose.

    `scribe.setup`'s `Progress` prints one JSON object per whole percent when
    its stdout is a pipe, which is what the launcher gives it. Those drive the
    bar and never reach the log: there are a hundred of them per repository,
    and as log lines they push everything that says something out of the
    window.
    """
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        event = json.loads(text)
    except ValueError:
        return None
    if not isinstance(event, dict) or event.get("event") != "progress":
        return None
    return event


def installer_line(line: str) -> bool | None:
    """The engine's line saying a third-party installer started (True) or
    ended (False), or None for anything else.

    `scribe.setup` prints `{"event": "installer", "running": true}` before it
    hands Ollama's installer to the operating system and the same with false
    after it has returned, on a pipe. Between the two the launcher refuses
    Quit (ADR-017, M10): the tree kill Quit uses on the app, or even the
    single terminate() it uses on the setup child, would leave a third-party
    installer half-way through writing somebody's Program Files.
    """
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        event = json.loads(text)
    except ValueError:
        return None
    if not isinstance(event, dict) or event.get("event") != "installer":
        return None
    running = event.get("running")
    return running if isinstance(running, bool) else None


INSTALLER_RUNNING = "Ollama's installer is running. Quit waits until it has finished."
INSTALLER_DONE = "Ollama's installer has finished."
QUIT_REFUSED = (
    "Ollama's installer is still running, so MyScribe cannot quit yet: stopping a third-party "
    "installer half-way is the one thing it must not do (ADR-017). Quit again once it has finished."
)
"""What Quit says while the installer runs. It promises nothing about what Quit
does to an Ollama the installer has started: that is unmeasured (TASK-089.18,
criterion 13), and until it is measured no sentence here says Quit is clean."""


def progress_headline(event: dict) -> str:
    """What the headline says while a download runs: the repository by name,
    because "Saving your answers..." for a whole download is what this
    replaces."""
    repo = str(event.get("repo") or "the model weights")
    percent = event.get("percent")
    percent = percent if isinstance(percent, (int, float)) else 0
    return f"Downloading {repo} - {int(percent)}%"


# --- what the launcher says about the app it started ---------------------------------


BROWSER_OPENED = "Opening MyScribe in your browser. If no browser window appears, go to {url}"
BROWSER_REFUSED = "No browser could be opened. Go to {url} in your browser."


def open_in_browser(url: str, report: Callable[[str, str], None]) -> None:
    """Open the app in the default browser, and say where it is either way.

    `webbrowser.open` answering True is not a confirmation: on a Mac whose
    default browser was not running it returned True and opened nothing, and
    neither the window nor launcher.log said what to do (the outside walk of
    0.8.3, TASK-107.05). So the address is said every time, and a False is
    said as what it is.
    """
    try:
        opened = webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a broken browser registration is a False too
        opened = False
    if opened:
        report("busy", BROWSER_OPENED.format(url=url))
    else:
        report("status", BROWSER_REFUSED.format(url=url))


CHOICE_WAITING = "A choice is waiting in the Set up MyScribe window: Retry, or Continue without."
"""Said in the main window while the failure dialog waits (TASK-107.03): the
status line otherwise stayed on 'Saving your answers...', which from outside
read as a hang."""


# --- the run ----------------------------------------------------------------------


class Launch:
    """The steps, in order, reporting through ``report(state, text)``.

    States: ``busy`` (a line of progress), ``status`` (a headline),
    ``progress`` (one JSON line of a download, for the bar), ``running``,
    ``done`` (another instance serves), ``error``. Runs on a worker thread
    under the window; directly under ``--headless``.
    """

    def __init__(self, layout: Layout, port: int, open_browser: bool, report: Callable[[str, str], None]):
        self.layout = layout
        self.port = port
        self.open_browser = open_browser
        self.say = report
        self.log = InstallLog(layout.logs_dir / INSTALL_LOG)
        self.app: AppProcess | None = None
        self.setup_child: subprocess.Popen | None = None
        self.serving = False
        # True between the engine's two installer event lines (`installer_line`):
        # while it stands, Quit is refused with `QUIT_REFUSED` (ADR-017, M10).
        self.installing = False
        # What the last apply told this launcher through the result file
        # (TASK-089.19): `data_dir` after an adoption, `library` for a folder
        # somebody named that the engine has looked at.
        self.result: dict = {}
        # How long Quit waits for the app to end through its stop route before
        # the hard stop (TASK-112.09); `STOP_WAIT`, as for `--stop`.
        self.stop_wait = STOP_WAIT
        self.route_asked = False  # set by stop_through_route, read by stop
        # The last error said, for the note a start at login leaves (TASK-112.07).
        self.problem = ""

    def report(self, state: str, text: str) -> None:
        """One tee, so that every line the launcher says is also in the file.

        Redacted first and for both, because ADR-015 forbids a secret on the
        screen as firmly as in a log. The progress lines are the one state
        that is not kept: they are a bar, not a sentence, and there are
        hundreds of them (TASK-089.15, criteria 5 and 7).

        Out of reach: the lines said before a home is settled - `locate_home`
        reports through `main`'s console, because there is no Layout yet to
        build a path from.
        """
        text = self.log.redact(text)
        if state == "error":
            self.problem = text
        if state != "progress":
            self.log.write(text)
        self.say(state, text)

    def remember(self, plan: dict, answers: dict) -> None:
        """Hide every secret this sitting collected, by value, before the
        child that might echo one has started."""
        for question in plan.get("questions") or []:
            if question.get("kind") == "secret":
                value = answers.get(question.get("id"))
                if isinstance(value, str):
                    self.log.hide(value)

    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    def prepare(self) -> bool:
        """Everything that has to exist before the app can be started; False
        means there is nothing to start.

        Split from ``run`` so the first-run sitting can sit between the sync
        and the start: the answers are applied by a python that only exists
        once uv has made it (TASK-089.01). ``serving`` says another instance
        already answers, which is a good end and not a start.
        """
        if running_instance(self.port):
            # The browser first: "done" is the line the window closes on.
            if self.open_browser:
                open_in_browser(self.url(), self.report)
            self.report("done", f"MyScribe is already running at {self.url()}")
            self.serving = True
            return True
        if port_taken(self.port):
            self.report("error", f"Port {self.port} is used by another program; MyScribe cannot start.")
            return False
        # Before anything is written and not only before the download: this is
        # the one place every door with a window passes through, and
        # `install_tools` copies hundreds of MB of uv and ffmpeg into the home.
        # A volume that cannot hold the install is a sentence here rather than
        # a half-built environment and a disk-full nobody can read
        # (TASK-089.14). Neither `needs_sync` nor the probe needs the home to
        # exist: the probe walks up to the nearest folder that does.
        if needs_sync(self.layout) and not enough_disk(self.layout, self.report):
            return False
        prepare_home(self.layout)
        install_tools(self.layout)
        _release_frozen_dll_directory()
        if needs_sync(self.layout):
            self.report("status", "Installing the speech engine. " + download_note(self.layout)
                        + " Later starts skip this.")
            said: list[str] = []

            def line(text: str) -> None:
                said.append(text)
                self.report("busy", text)

            if not sync(self.layout, line):
                self.report("error", install_failure(self.layout, said))
                return False
        return True

    def start_app(self) -> bool:
        self.report("status", "Starting MyScribe...")
        self.app = AppProcess(self.layout, self.port)
        self.app.start()
        if not self.app.wait_ready():
            self.report("error", f"MyScribe did not start; see {self.layout.logs_dir / 'app.log'}")
            self.app.stop()
            return False
        self.report("running", f"MyScribe is running at {self.url()}")
        if self.open_browser:
            open_in_browser(self.url(), self.report)
        return True

    def run(self) -> bool:
        if not self.prepare():
            return False
        if self.serving:
            return True
        return self.start_app()

    def watching(self, run: Callable, *args, **kwargs):
        """Run one setup child with its handle kept, so Quit can stop it.

        ADR-017's Must is about the setup child, and there are three of them:
        ``--plan``, ``--apply-stdin`` and ``--prove``. Only the download used
        to be registered, so Quit during the four-second plan, or during the
        proof - which on a first run may load a model, because nothing answers
        the port yet - left a python running behind a launcher that had gone.

        The handle is let go afterwards, so a later Quit cannot terminate a pid
        the system has since given to somebody else.
        """
        try:
            return run(*args, on_start=self.keep_setup_child, **kwargs)
        finally:
            self.setup_child = None

    def keep_setup_child(self, child: subprocess.Popen) -> None:
        self.setup_child = child

    def stop_setup(self, timeout: float = STOP_TIMEOUT) -> None:
        """Quit during a download stops the setup child - that one process.

        ``terminate()``, and never the tree kill ``AppProcess.stop`` uses on
        the app: a tree kill here could pass over an Ollama that a third-party
        installer had just started, and ADR-017's Must is that MyScribe never
        stops software it did not start.

        What a single-process stop can orphan is said in those words: at most
        a version probe, which ends by itself within the 15 s timeout of
        ``scribe/doctor.py``'s ``_run``. Under the launcher the setup child
        has no other long-lived children - the downloads and the Ollama pull
        are in-process HTTP, and the doctor's checks are called in-process.
        TASK-089.18 measures what Quit may promise around a freshly installed
        Ollama; until it has, nothing here says Quit is clean.
        """
        if self.installing:
            self.report("status", QUIT_REFUSED)
            return
        child = self.setup_child
        if child is None or child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout)
        except subprocess.TimeoutExpired:
            pass  # a child that will not go is left; killing its tree is the thing forbidden

    def quit_refused(self) -> str:
        """The sentence Quit shows instead of quitting, or "" when it may quit.
        The window and the console door ask this before `stop`."""
        return QUIT_REFUSED if self.installing else ""

    def stop(self) -> None:
        """Quit, Ctrl+C and a SIGTERM to the launcher: the setup child, then
        the app - through its stop route first (ADR-028, TASK-112.09).

        Only the route puts a running job back at the front of the queue; the
        hard stop leaves it to reconcile, which marks it interrupted. On
        macOS and Linux the hard stop reaches the runner, which has a session
        of its own, only through the app's SIGTERM (ADR-032); an app it has to
        SIGKILL may leave it running. So the route is asked, and the launcher waits for
        the pid /health names - on Windows the venv's python.exe is a
        redirector, so that is not ``proc.pid``. `AppProcess.stop` is the
        fallback when the route does not answer or the app outlives
        `stop_wait`. What happens to the job is said before and after.
        """
        if self.installing:
            self.report("status", QUIT_REFUSED)
            return
        self.stop_setup()
        app = self.app
        if app is None or not app.alive():
            return
        self.report("status", STOPPING)
        self.route_asked = False
        if self.stop_through_route(app):
            return
        killed = app.stop()
        self.report("status", ended(killed is True, asked=self.route_asked))

    def stop_through_route(self, app: "AppProcess") -> bool:
        """Ask the app's stop route and wait for it; True when it has ended.
        Sets `route_asked` when the route may have got the request, so that
        the hard stop after it says what that may have done to the job."""
        health = read_health(self.port)
        pid = health.get("pid") if health else None
        if not isinstance(pid, int):
            self.report("busy", "MyScribe does not answer its stop route; ending it instead.")
            return False
        try:
            answer = ask_to_stop(self.port)
        except (OSError, ValueError) as error:
            self.route_asked = route_reached(error)
            self.report("busy", f"The stop route did not answer ({error}); ending MyScribe instead.")
            return False
        self.route_asked = True
        job = answer.get("running_job")
        self.report("status", STOPPING_JOB.format(job=job) if job is not None else STOPPING_IDLE)
        self.report("busy", f"Waiting up to {self.stop_wait:g} s for MyScribe to stop.")
        deadline = time.monotonic() + self.stop_wait
        while time.monotonic() < deadline:
            # `alive()` reaps our own child, so a POSIX app that has ended is
            # not a zombie that `pid_alive` still counts.
            if not app.alive() and not pid_alive(pid):
                self.report("status", STOPPED_JOB.format(job=job) if job is not None else STOPPED_IDLE)
                return True
            time.sleep(0.2)
        self.report("busy", f"MyScribe did not stop within {self.stop_wait:g} s; ending it.")
        return False

    def adopt(self, data: str) -> None:
        """The engine adopted a library: record it in the pointer file - the
        engine decided it, this only writes it down (ADR-015) - and use it
        from here on (TASK-089.19, criterion 8).

        A home given by hand keeps everything under it, so there the library
        is used for this start and the sentence says how to keep it.
        """
        where = Path(data)
        if self.layout.pointer is None:
            self.report("error", f"{APP_NAME} uses the library at {where} for this start only: the home was "
                                 f"given by hand (--home or {HOME_VARIABLE}), which keeps everything under "
                                 "it. Start without it to keep this library.")
        else:
            problem = write_pointer_data(self.layout.pointer, where)
            if problem:
                self.report("error", problem)
        self.layout = Layout(self.layout.home, self.layout.payload, self.layout.windows,
                             data=where, pointer=self.layout.pointer)
        self.report("status", f"{APP_NAME} now uses the library at {where}.")

    def apply(self, answers: dict) -> tuple[int, list[str]]:
        """Hand the answers to the engine, returning its exit code and what it
        says is still open.

        The child's handle is kept while it runs, so Quit can stop it
        (``watching``).
        """
        reopen: list[str] = []

        def on_line(line: str) -> None:
            running = installer_line(line)
            if running is not None:
                # Read here and turned into a headline; the JSON is not a log
                # line. Quit reads the flag (TASK-089.18, criterion 14).
                self.installing = running
                self.report("status", INSTALLER_RUNNING if running else INSTALLER_DONE)
                return
            if progress_line(line) is not None:
                self.report("progress", line)
                return
            # The one place the launcher reads the engine's prose, and it is
            # commented at both ends: `report["reopen"]` is printed as
            # "still open: ...". Making it a JSON line would change the
            # contract, which belongs to TASK-089.09 (ADR-015).
            if line.startswith(REOPEN_PREFIX):
                reopen.extend(part.strip() for part in line[len(REOPEN_PREFIX):].split(",") if part.strip())
            self.report("busy", line)

        take_setup_result(self.layout)  # nothing left over from an earlier child
        try:
            code = self.watching(run_setup, self.layout, answers, on_line)
        finally:
            # A child that died between the two event lines must not leave
            # Quit refused for good.
            self.installing = False
        self.result = take_setup_result(self.layout)
        return code, reopen


def setup_stamp(layout: Layout) -> Path:
    """Written by `scribe.setup` when a sitting ends: the contract it spoke,
    when it ended, and one state per question id."""
    return layout.data_dir / "setup.json"


def setup_contract(layout: Layout) -> int | None:
    """``scribe.setup.CONTRACT`` read as text, the way ``app_version`` reads the
    version - importing it would need the environment (ADR-011).

    Anchored at the start of a line, because the name also appears inside
    sentences the engine prints: a regex that matched one of those would
    compare the stamp against a number out of an f-string. None means the
    number could not be read, which the gate answers by asking again.
    """
    source = layout.app_dir / "scribe" / "setup.py"
    try:
        match = re.search(r"^CONTRACT\s*=\s*(\d+)\s*$", source.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return int(match.group(1)) if match else None


def setup_needed(layout: Layout) -> bool:
    """Does the first-run sitting appear? The stamp and one number, as data.

    Reading this has to be cheap, because it is read at every start: measured
    here on 2026-09-22, a cold `--plan` child costs 4332 ms and this costs
    0.78 ms. So nothing is imported from the app and no child is started - the
    launcher learns a number and some states, and nothing about what any of the
    questions are (ADR-015). The sitting that follows pays for the one `--plan`
    it draws itself from (TASK-089.15).

    It asks when there is no stamp, when the stamp cannot be read, when it
    carries no `ended` - which every finished sitting writes, so something else
    left it - and when its contract is lower than the payload's, which is how
    somebody who finished an older setup is asked the questions this version
    added, once. Anything it cannot work out asks: a dialog can be closed, and
    never asking is the failure this gate exists for.

    It is deliberately not a question about this machine. A token removed after
    the sitting makes `--plan` list it open again and does not reopen the
    sitting: the gate is about what was asked, `--plan` is about the machine
    now, and the doctor reports the difference.
    """
    try:
        stamp = json.loads(setup_stamp(layout).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(stamp, dict) or stamp.get("ended") is None:
        return True
    shipped = setup_contract(layout)
    if shipped is None:
        return True
    spoke = stamp.get("contract")
    return not isinstance(spoke, int) or spoke < shipped


def wants_setup(layout: Layout, force: bool = False, at_login: bool = False) -> bool:
    """Whether to open the first-run sitting before starting the app.

    A pure predicate so it can be asked without a Tk window - the gate it
    replaces was an `if` inside one, and no test could reach it.

    At login nobody is at the screen, and a modal dialog that holds the watch
    folders up is the opposite of what the login entry exists for. A sitting
    that is due is not cancelled: it waits for the next start somebody makes
    by hand. `--setup` still wins, because somebody typed it - except against
    a MyScribe that already serves, which `first_run` answers in words
    instead of with the sitting.
    """
    if force:
        return True
    return setup_needed(layout) and not at_login


def setup_command(layout: Layout) -> list[str]:
    """`python -m scribe.setup --apply-stdin`, in the app's environment.

    The launcher is frozen and stdlib-only (ADR-011): it cannot write a setting
    row or parse `.env` for itself, and should not learn how. It asks the
    questions and hands the answers to the app, the same way `--doctor` hands
    over a check.

    No answer is on this list and none ever will be. A secret on a command line
    can be read by anything that can list this machine's processes (ADR-015),
    and the token flag this replaced cost more than the token: `scribe.setup`
    refuses it with exit 2 before it reads any other answer, so a sitting in
    which somebody typed a token lost the whole sitting - no setting row, no
    stamp, and the gate again at every start. ADR-015's Verification is the
    grep that keeps that flag out of this file.
    """
    return [str(layout.env_python), "-m", "scribe.setup", "--apply-stdin"]


def setup_document(layout: Layout, answers: dict) -> dict:
    """The one JSON document the sitting hands over, `{contract, answers}`.

    The keys are the question ids of `--plan`, which is what the engine reads
    them back as: a value is an answer, `None` is a skip that writes nothing
    and is recorded as skipped, and an id that is absent was never shown.

    `contract` is left out when the shipped `setup.py` could not be read. The
    engine answers a number that is not its own with exit 2 and applies
    nothing, and a number nobody could read is not a disagreement - it is
    silence, which `setup_needed` already answers by asking again.
    """
    document: dict = {"answers": dict(answers)}
    contract = setup_contract(layout)
    if contract is not None:
        document["contract"] = contract
    return document


def run_setup(layout: Layout, answers: dict, on_line: Callable[[str], None],
              on_start: Callable[["subprocess.Popen"], None] | None = None) -> int:
    """Apply the answers, streaming what happens - a download of gigabytes has
    to look like something happening rather than a window that stopped.

    Returns the exit code, because the caller says it out loud: a setup that
    failed used to be a `False` nobody looked at (TASK-089.01, AC #4).
    """
    return run_streaming(
        setup_command(layout),
        env=setup_environment(layout),
        cwd=layout.app_dir,
        on_line=on_line,
        input=json.dumps(setup_document(layout, answers)),
        on_start=on_start,
    )


# --- the plan the sitting is drawn from -------------------------------------------


REOPEN_PREFIX = "still open: "
"""How `scribe.setup` prints what it could not finish. Read here and nowhere
else; the comment in `Launch.apply` says why it is prose and not a JSON line."""

CONDITIONS_URL = "https://hf.co/pyannote/speaker-diarization-community-1"
"""Where the gated model's conditions are accepted. A launcher constant keyed
to the `hf_token` question and not a field of the contract: `Question` has no
url, and adding one would bump CONTRACT, which reopens everybody's gate and
belongs to TASK-089.09. A mild tension with ADR-015, recorded rather than
hidden."""

CONDITIONS_FOR = "hf_token"


def plan_command(layout: Layout, *, unasked_only: bool, named: Iterable[str] = ()) -> list[str]:
    """`--plan`, with `--unasked-only` for the gate and without it by hand.

    ADR-015's Must: a plan made for a start lists the questions that are open
    and were never put; one asked for with `--setup` or the Setup button lists
    all, with current values - which is how a question somebody skipped is
    asked again when they go looking for it, and never by a start.
    """
    command = [str(layout.env_python), "-m", "scribe.setup", "--plan"]
    if unasked_only:
        command.append("--unasked-only")
    for folder in named:  # a folder the engine looked at and asked to list (TASK-089.19)
        command += ["--library", str(folder)]
    return command


def setup_plan(layout: Layout, report: Callable[[str, str], None], *,
               unasked_only: bool = True, named: Iterable[str] = (),
               on_start: Callable[["subprocess.Popen"], None] | None = None) -> dict | None:
    """What this machine has and what is still open, as the engine sees it.

    One child and one JSON document; None when it could not be had, which is a
    sitting that does not open rather than one drawn from a guess. Measured on
    2026-09-22: a cold `--plan` child costs 4332 ms, which is why the gate
    (`setup_needed`) reads a stamp instead and only a sitting that is really
    opening pays for this.
    """
    lines: list[str] = []
    try:
        code = run_streaming(plan_command(layout, unasked_only=unasked_only, named=named),
                             env=app_environment(layout), cwd=layout.app_dir,
                             on_line=lines.append, on_start=on_start)
    except Exception as error:
        if not layout.env_python.exists():
            # Issue 16: the Setup button after a failed install. Nothing starts
            # anyway, and WinError 2 says nothing to the person reading it.
            report("error", "The speech engine is not installed yet, so the setup questions "
                            "cannot be asked. Start MyScribe again to install it.")
            return None
        report("error", f"The setup questions could not be read: {error}. MyScribe starts anyway; "
                        "the questions are also in Settings.")
        return None
    text = "\n".join(lines)
    # The child's stderr shares this pipe, so the document is read out of the
    # middle of whatever else was said: from the first brace, and only as far
    # as that object goes. `raw_decode` and not `loads`, because a line printed
    # after the plan - a warning, an "Exception ignored in:" at shutdown - is
    # as likely as one printed before it, and `loads` on the whole tail would
    # make either of them a sitting that silently does not open.
    document = None
    start = text.find("{")
    while start >= 0 and document is None:
        try:
            found = json.JSONDecoder().raw_decode(text, start)[0]
        except ValueError:
            found = None
        # A line of the child's stderr can itself be a JSON object, and the
        # first one that decodes would otherwise become the plan - a sitting
        # that says nothing is open, and a stamp for questions nobody was
        # asked. `questions` is in every contract-2 plan and is the key this
        # file already reads, so it is what tells them apart.
        document = found if isinstance(found, dict) and "questions" in found else None
        if document is None:
            start = text.find("{", start + 1)
    if code != 0 or not isinstance(document, dict):
        report("error", f"The setup questions could not be read (exit {code}). MyScribe starts "
                        "anyway; the questions are also in Settings.")
        return None
    return document


def found_lines(plan: dict) -> list[str]:
    """The "Found on this machine" block, sources only and never a value.

    The same rows `scribe.setup --plan` prints for a terminal, drawn here for
    a window: a credential says where it was found, a proxy says its host, and
    Ollama says what the engine made of it. Nobody should be shown an empty
    token field for a token this machine already has and go and mint a second
    one - that is the failure this block exists to end.

    One line per thing found, and one for everything that was not (TASK-098).
    The other places a token also sits are a count: which one is used is the
    line, and the full list is in Settings and in `--plan`. A place that
    defines a different value is the exception and keeps its words, because
    that is a warning. A login entry that is off is left out: it is the
    start_at_login question, and a step that says "not found" about something
    the next step asks reads as a contradiction.
    """
    lines: list[str] = []
    missing: list[str] = []
    for row in plan.get("found") or []:
        label = str(row.get("label") or row.get("name") or "")
        if row.get("kind") == "proxy":
            lines.append(f"{label}: {row.get('host', '')} ({row.get('source', '')})")
            continue
        if row.get("kind") == "login":
            if row.get("found"):
                lines.append(f"{label}: on")
            continue
        if not row.get("found"):
            missing.append(label)
            continue
        line = f"{label}: {row.get('source')}"
        also = [str(place) for place in row.get("also_in") or []]
        if also and row.get("conflict"):
            line += f" - also in {', '.join(also)}, which defines a different value, not used"
        elif also:
            line += f", and {len(also)} other place{'' if len(also) == 1 else 's'}"
        lines.append(line)
    ollama = plan.get("ollama") or {}
    if ollama.get("note"):
        lines.append(f"Ollama: {ollama['note']}")
    if missing:
        lines.append("Not found: " + ", ".join(missing))
    return lines


def shown(question: dict, answers: dict) -> bool:
    """Whether a question is on screen, from `shown_if` and nothing else.

    ADR-015's Must Not: `shown_if` is the only condition a front-end
    interprets. Everything else about which questions exist was decided by the
    engine before this document was printed.
    """
    condition = question.get("shown_if")
    if not isinstance(condition, dict):
        return True
    return answers.get(condition.get("question")) == condition.get("equals")


# --- what a failure says ----------------------------------------------------------


GATED_MODEL = (
    "The speaker model is gated, and the token MyScribe has does not open it. Accept the "
    f"conditions at {CONDITIONS_URL} with the account the token belongs to, then try again. "
    "Everything else was saved."
)
CONTRACT_MISMATCH = (
    "The answers were refused: this MyScribe's setup engine and the questions that were asked "
    "do not agree, or a file that ships pinned did not match. Nothing was saved. Retry asks the "
    "questions again; if it happens twice, install MyScribe again."
)
NO_ROOM = (
    "There was not enough room on the disk to finish the download. Free some space and try "
    "again, or move MyScribe with the folder question - what was saved is saved."
)
SETUP_FAILURES = {2: CONTRACT_MISMATCH, 3: GATED_MODEL, 4: NO_ROOM}
"""By the exit code `scribe/models.py` gives each kind (`EXIT_CODES`). 2 is not
the refused token flag any more: the launcher builds no such flag, so the only
2 it can provoke is a contract or a pinned-file mismatch."""


def setup_failure(code: int, reopen: Iterable[str] = ()) -> str:
    """The sentence a failed sitting shows, or "" when nothing failed.

    A `reopen` is a failure with a zero exit code: the engine saved what it
    could and says which questions it could not finish, and a sitting that
    ended on one is not one to write off in a grey log line.
    """
    still_open = [str(name) for name in reopen]
    if code == 0 and not still_open:
        return ""
    if code in SETUP_FAILURES:
        sentence = SETUP_FAILURES[code]
    elif code != 0:
        sentence = (f"Saving your answers failed with exit code {code}; the lines above say why. "
                    "Retry asks the questions again.")
    else:
        sentence = "Not everything could be saved."
    if still_open:
        sentence += " Still open: " + ", ".join(still_open) + "."
    return sentence


# --- the proof that ends a sitting ------------------------------------------------


def prove_command(layout: Layout, port: int) -> list[str]:
    """`--prove` on the port this launcher serves on.

    The port is always passed, and it is what keeps the card the runner's
    (ADR-001): while MyScribe answers there, the engine queues the doctor job
    instead of loading a model in the setup child (TASK-089.13).
    """
    return [str(layout.env_python), "-m", "scribe.setup", "--prove", "--port", str(port)]


def run_prove(layout: Layout, report: Callable[[str, str], None], port: int,
              on_start: Callable[["subprocess.Popen"], None] | None = None) -> int:
    """Measure what the sitting produced and say it, whatever it comes back
    with.

    Its exit code is information and never the error state of a failed apply.
    Transcription is a required line and reads "not tested" while the app
    answers (TASK-089.13, step 14), so a proof run from the Setup button exits
    1 by design - rendering that as a failure would end every such sitting in
    "Retry".
    """
    report("status", "Checking what this machine can do...")
    try:
        code = run_streaming(prove_command(layout, port), env=app_environment(layout),
                             cwd=layout.app_dir, on_line=lambda line: report("busy", line),
                             on_start=on_start)
    except Exception as error:
        report("busy", f"The check could not be run: {error}")
        return 1
    report("busy", "The report above is what this machine measured; nothing was changed by it.")
    return code


NOTHING_WAS_ASKED = (
    "The first-run questions were not asked: this is not a terminal and there is no window. "
    "Start MyScribe from a terminal to answer them, or pipe one JSON document of answers to "
    "`python -m scribe.setup --apply-stdin` in the environment MyScribe installed."
)


def console_sitting(launch: Launch, report: Callable[[str, str], None]) -> None:
    """The headless door: the terminal is handed to the engine.

    With a TTY, `python -m scribe.setup` inherits stdin and stdout and asks
    for itself, with `getpass` for a secret - no document, no pipe, and no
    second asker to keep in step with the first (ADR-015). Without one,
    nothing is asked and no child is started.

    `isatty()` is the cheap half of that test and not the whole of it: on
    Windows `subprocess.DEVNULL` is NUL, NUL is a character device, and
    `isatty()` answers True for it. Who is really at a terminal is the
    engine's decision and is made in `scribe/setup.py`'s `at_a_terminal`,
    which asks the console handle; a child started here on that footing
    prints the engine's own "nothing was asked" line and writes no stamp.
    The launcher does not double that test: ADR-011 keeps it stdlib-only and
    ADR-015 keeps the deciding in one place.

    What a console sitting says goes to the terminal, which is its log; the
    tee of `Launch.report` covers the lines that pass through the launcher.
    """
    if not sys.stdin.isatty():
        report("status", NOTHING_WAS_ASKED)
        return
    # A second pass only after an adoption (TASK-089.19): the adopted library
    # holds its own answers, and this engine asks for them against it.
    for _ in range(2):
        take_setup_result(launch.layout)
        try:
            subprocess.call([str(launch.layout.env_python), "-m", "scribe.setup"],
                            cwd=str(launch.layout.app_dir), env=setup_environment(launch.layout))
        except Exception as error:
            report("error", f"The setup questions could not be asked: {error}. MyScribe starts anyway; "
                            "the questions are also in Settings.")
            return
        adopted = take_setup_result(launch.layout).get("data_dir")
        if not adopted:
            break
        launch.adopt(str(adopted))
    launch.watching(run_prove, launch.layout, report, launch.port)


def open_sitting(launch: Launch, ask: Callable[[dict], dict | None],
                 report: Callable[[str, str], None], *, force_setup: bool = False,
                 retry: Callable[[str], bool] | None = None) -> None:
    """One sitting: plan, ask, apply, prove - and again from the top on Retry.

    `ask` gets the plan and gives back one answer per question id, or None for
    "ask me next time" and for a window somebody closed. It always gives back
    a dict for Save, even when every question in it was skipped, because that
    sitting was held: the engine writes the stamp, and the gate does not open
    it again at the next start (TASK-089.11).
    """
    applied = False
    named: list[str] = []
    while True:
        plan = launch.watching(setup_plan, launch.layout, report, unasked_only=not force_setup,
                               **({"named": named} if named else {}))
        if plan is None:
            return
        answers = ask(plan)
        if answers is None:
            return  # "ask me next time": nothing applied, no stamp, asked again
        launch.remember(plan, answers)
        report("status", "Saving your answers...")
        try:
            code, reopen = launch.apply(answers)
        except Exception as error:
            report("error", f"Saving your answers failed: {error}. MyScribe starts anyway; "
                            "the questions are also in Settings.")
            break
        applied = True
        # TASK-089.19: the engine's result, never a question id, decides
        # these two. An adopted library holds its own answers, so the sitting
        # goes on there; a named folder is planned again with it listed.
        if launch.result.get("data_dir"):
            launch.adopt(str(launch.result["data_dir"]))
            named = []
            continue
        if launch.result.get("library"):
            named = [str(launch.result["library"])]
            continue
        sentence = setup_failure(code, reopen)
        if not sentence:
            break
        report("error", sentence)
        if retry is None or not retry(sentence):
            break
    if applied:
        launch.watching(run_prove, launch.layout, report, launch.port)


SETUP_WHILE_RUNNING = ("MyScribe is already running; quit it first and start again "
                       "with --setup to answer the questions.")


def first_run(launch: Launch, ask: Callable[[dict], dict | None] | None,
              report: Callable[[str, str], None],
              force_setup: bool = False, at_login: bool = False,
              retry: Callable[[str], bool] | None = None) -> bool:
    """The whole sequence: tools, sync, plan, sitting, apply, prove, start.

    Where everything goes is the step before this one and is settled in
    `main`, because `prepare_home` is the first write under a home and
    `run_window` builds its title, its "Open data folder" button and its log
    path from the layout before this runs (TASK-089.14).

    The rest of the order is the point. `scribe.setup` runs from the
    environment, and the environment is what the sync makes, so the sitting
    cannot come before it - asking first meant the Popen died on the worker
    thread with FileNotFoundError and the app was never started (TASK-089.01).

    Plain and Tk-free on purpose: `ask`, `retry` and `report` are the only
    things that render, and nothing here decides an answer or writes one
    (ADR-011 keeps the launcher stdlib-only, ADR-015 keeps every front-end a
    renderer). `ask` is None for a door with no dialog - the console, and a
    build without Tk - where the terminal goes to the engine instead, the same
    convention `locate_home` uses. `retry` is what a failed apply asks; with
    none, the sentence is reported and MyScribe starts anyway.

    `--setup` against a MyScribe that already serves is answered in words and
    not with the sitting. That is a deliberate change from the order this file
    had until TASK-089.01: the sitting now comes after the sync, and the
    serving branch comes before it, so opening it there would mean a dialog
    beside a window `run_window`'s pump destroys three seconds after the
    `done` state. Quit first, then `--setup`.
    """
    if not launch.prepare():
        return False
    if launch.serving:  # another instance answers; nothing to set up or start
        if force_setup:
            # Short on purpose: the window closes three seconds after `done`.
            report("status", SETUP_WHILE_RUNNING)
        return True
    if wants_setup(launch.layout, force=force_setup, at_login=at_login):
        # `ask` is None on every door with no dialog - the console, and a build
        # without Tk - and there the terminal goes to the engine, the way
        # `locate_home` reads a None `ask` as "nobody is at the screen".
        if ask is None:
            console_sitting(launch, report)
        else:
            open_sitting(launch, ask, report, force_setup=force_setup, retry=retry)
    return launch.start_app()


APP_DIED = 70
"""The one non-zero exit of a door that serves: the app child died under it.

A supervisor that restarts on a non-zero exit - launchd's KeepAlive with
SuccessfulExit false, systemd's Restart=on-failure - reads the launcher's code
and nothing else, so the contract is that only a crash asks for a restart.
A clean stop, another instance already serving and a configuration problem
(no library drive, a busy port, a sync that failed offline, an app that never
answered) end with 0 and say why in the log: a restart cannot plug a drive in,
and under launchd it would retry a multi-GB sync every ten seconds. 70 is
EX_SOFTWARE from sysexits.h, clear of the 1 Python itself exits with on an
uncaught exception (TASK-112.02)."""

# --- a crashed app is started again (TASK-112.10, ADR-037) -----------------------

RESTART_LIMIT = 5
RESTART_WINDOW = 300.0
RESTART_DELAY = 5.0
"""At most `RESTART_LIMIT` restarts in any `RESTART_WINDOW` seconds, each after
`RESTART_DELAY` seconds: the systemd unit's `StartLimitBurst=5`,
`StartLimitIntervalSec=300` and `RestartSec=5` (Robert, 2026-10-25). A crash after
hours of serving finds its budget back; a crash that repeats at every start
is given up on within a minute or so instead of being retried for ever."""

SUPERVISED_UNIT = "myscribe.service"
SUPERVISED_LABEL = "io.github.rvdbreemen.myscribe"
"""The names `scribe/autostart.py` gives the systemd user unit (UNIT_NAME) and
the LaunchAgent (LABEL). Spelled again rather than imported, like `APP_DIED`:
the app is not importable from the launcher. A test holds them to its own."""

CRASHED_JOB = ("A job that was running finishes by itself if its runner outlived the crash; "
               "otherwise MyScribe marks it interrupted as it starts, and Retry on the jobs page "
               "starts it over.")
RESTARTING = ("Starting MyScribe again in {delay:g} s (restart {count} of at most {limit} in "
              "{minutes:g} minutes). " + CRASHED_JOB)
GAVE_UP = ("MyScribe stopped unexpectedly again after {limit} restarts in {minutes:g} minutes, so it "
           "is not started again; see {log}. Start MyScribe by hand to try once more.")
"""What the console door says about a crash it restarts and the one it gives
up on. The job half is reconcile's (ADR-013, `scribe/supervisor.py`): a
running row whose runner is dead becomes interrupted at the next start, one
whose runner lives on is left to write its own verdict."""


def os_supervisor(environ: dict | None = None, cgroup: Path | None = Path("/proc/self/cgroup")) -> str:
    """"systemd" or "launchd" when MyScribe's own login entry started this
    launcher and restarts it on `APP_DIED`, "" when nothing does.

    Known by name, never by a variable being there at all. launchd puts the
    job's label in XPC_SERVICE_NAME, but a shell in Terminal carries the
    variable too (`0`, or Terminal's own name). systemd sets INVOCATION_ID,
    but so does every other unit, a terminal server that runs as one
    included; the cgroup it puts the unit's processes in is named after the
    unit. The XDG autostart file and the Windows Run value watch nothing.
    """
    environ = os.environ if environ is None else environ
    if environ.get("XPC_SERVICE_NAME") == SUPERVISED_LABEL:
        return "launchd"
    if cgroup is not None:
        try:
            lines = cgroup.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines:
            if line.rpartition(":")[2].rstrip("/").rpartition("/")[2] == SUPERVISED_UNIT:
                return "systemd"
    return ""


class RestartBudget:
    """`limit` restarts in any `window` seconds; `spend` says whether one more fits."""

    def __init__(self, limit: int | None = None, window: float | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.limit = RESTART_LIMIT if limit is None else limit
        self.window = RESTART_WINDOW if window is None else window
        self.clock = clock
        self.spent: list[float] = []

    def spend(self) -> bool:
        now = self.clock()
        self.spent = [then for then in self.spent if now - then < self.window]
        if len(self.spent) >= self.limit:
            return False
        self.spent.append(now)
        return True


def run_headless(launch: Launch, force_setup: bool = False, at_login: bool = False) -> int:
    """The console door, and every build without Tk; the exit code is
    `APP_DIED`'s contract.

    It goes through `first_run` with no `ask`, so that a first start here is
    asked the same questions a window is - by the engine, with the terminal
    handed to it. Until TASK-089.15 this door ran `launch.run()`, which asks
    nothing at all: `--setup` on a Mac or in WSL did nothing visible.

    An app that never answered at start counts as a configuration problem and
    not as a crash: restarting it repeats the same failure.

    An app that dies under a launcher no OS supervisor watches is started
    again, up to `RESTART_LIMIT` times in `RESTART_WINDOW` (ADR-037,
    TASK-112.10): the Windows Run value has nobody else to do it. A restart
    that does not answer is spent like a crash. Under MyScribe's own systemd
    unit or LaunchAgent (`os_supervisor`) the launcher exits `APP_DIED` at
    once, as before, and the OS restarts it; both at once would be two
    restarts for one crash. An exit of 0 - the stop route, from Settings or
    `--stop` - and Ctrl+C or SIGTERM, also during a restart, are never
    restarted (ADR-032).
    """
    started = first_run(launch, None, launch.report, force_setup, at_login)
    if at_login and (launch.serving or (started and launch.app is not None)):
        drop_login_problem(launch.layout.home)  # it serves now: the old problem is history
    if not started or launch.serving or launch.app is None:
        # A busy port or a running instance is found before the home is made;
        # on a fresh machine launcher.log has no folder yet to hold the line.
        if launch.log.pending:
            write_early(launch.log.pending)
        if at_login and launch.problem and not launch.serving:
            # Nobody reads that log; the next start by hand says it (TASK-112.07).
            keep_login_problem(launch.layout.home, launch.problem)
        return 0
    interrupted = gave_up = False
    supervisor = os_supervisor()
    budget = RestartBudget()
    app_log = lambda: launch.layout.logs_dir / "app.log"  # noqa: E731  (only after a crash)
    try:
        # One handler and one `finally` over the restarts too: a SIGTERM in
        # the delay or while a restarted app comes up is a stop, not a crash.
        with stop_on_sigterm():
            answered = True
            while True:
                while launch.app.alive():
                    time.sleep(0.5)
                code = launch.app.exit_code()
                if (not code and answered) or supervisor:
                    break
                if answered:  # `start_app` has said it for one that did not answer
                    launch.report("error", f"MyScribe stopped unexpectedly (exit code {code}); see {app_log()}.")
                if not budget.spend():
                    gave_up = True
                    break
                launch.report("status", RESTARTING.format(delay=RESTART_DELAY, count=len(budget.spent),
                                                          limit=budget.limit, minutes=budget.window / 60))
                time.sleep(RESTART_DELAY)
                # One that does not answer is stopped - on POSIX its SIGTERM may
                # end it with 0 - and the loop spends again.
                answered = launch.start_app()
    except KeyboardInterrupt:
        interrupted = True
    finally:
        with signals_held():
            launch.stop()  # says what happened to the running job (TASK-112.09)
    if interrupted:
        return 0
    if gave_up:
        launch.report("error", GAVE_UP.format(limit=budget.limit, minutes=budget.window / 60, log=app_log()))
        if at_login:
            keep_login_problem(launch.layout.home, launch.problem)  # the next start by hand says it
        return APP_DIED
    code = launch.app.exit_code()
    if code:
        launch.report("error", f"MyScribe stopped unexpectedly (exit code {code}); see {app_log()}.")
        return APP_DIED
    launch.report("status", "MyScribe stopped.")
    return 0


@contextlib.contextmanager
def stop_on_sigterm(then: Callable[[], None] | None = None):
    """SIGTERM ends the launcher the way Ctrl+C or Quit does: through
    `launch.stop()`, which takes the app's process group with it.

    The app runs in a session of its own so that Quit reaches its runners;
    the price was that `pkill MyScribe` - SIGTERM, whose default ends Python
    on the spot - left it serving (TASK-102.03, the outside macOS walk of
    0.8.0). Without ``then`` the signal becomes a KeyboardInterrupt, which the
    headless loop already handles; the window passes its own Quit. SIGKILL
    and a crash cannot be caught here and still leave the app running. The
    handler that was there is put back on the way out.
    """
    def handler(_signum, _frame):
        if then is None:
            raise KeyboardInterrupt
        then()

    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread: nothing to install
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextlib.contextmanager
def signals_held():
    """While a stop runs, a second Ctrl+C or SIGTERM does nothing.

    The stop waits for the route's requeue, which takes seconds and not the
    milliseconds the hard stop did (TASK-112.09). A second signal in that time
    - somebody pressing Ctrl+C again, a supervisor repeating itself - used to
    land on the default handler, which ends the launcher with the app halfway
    through its stop, or as a KeyboardInterrupt that broke the wait and
    skipped the fallback. The stop has its own deadline and its own hard stop
    after it; SIGKILL is still there for whoever cannot wait. The handlers
    that were there are put back on the way out.
    """
    held = {}
    for number in (signal.SIGTERM, signal.SIGINT):
        try:
            held[number] = signal.signal(number, lambda _signum, _frame: None)
        except ValueError:  # not the main thread: nothing to install
            pass
    try:
        yield
    finally:
        for number, previous in held.items():
            signal.signal(number, previous)


def ask_location(message: str) -> str | None:
    """The location question, in a window of its own.

    Its own root, and not the launcher's: this is answered in ``main``, before
    there is a home to build a window from. ``run_window`` puts the home in its
    title, in its "Open data folder" button and in the path it names when the
    app stops, all before the worker thread starts - so a home settled on that
    thread would leave three widgets pointing at a folder nothing uses.

    Returns the folder, or None for "keep the default", which is also what
    closing the window means. "Use this folder" over an empty box returns the
    empty string and not None, so that it is answered with "nothing was typed"
    and the question again, rather than silently meaning the default. Nothing
    is refused here: the refusals are ``refuse_location``'s and this window
    shows them and asks again.
    """
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.title(f"Where should {APP_NAME} keep everything?")
    chosen: dict = {}

    tk.Label(root, text=message, justify="left", anchor="w").pack(
        fill="both", expand=True, padx=12, pady=12)

    entry = tk.Entry(root, width=60)
    entry.pack(fill="x", padx=12)

    def browse() -> None:
        picked = filedialog.askdirectory(parent=root, mustexist=False)
        if picked:
            entry.delete(0, "end")
            entry.insert(0, picked)

    def use() -> None:
        chosen["home"] = entry.get().strip()
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=12, pady=12)
    tk.Button(buttons, text="Browse...", command=browse).pack(side="left")
    tk.Button(buttons, text="Use this folder", command=use, default="active").pack(side="right")
    tk.Button(buttons, text="Keep the default", command=root.destroy).pack(side="right", padx=8)

    root.mainloop()
    try:
        # Closing the window with its X ends the mainloop without destroying
        # the interpreter, and `run_window` creates a second Tk in this same
        # process a moment later.
        root.destroy()
    except tk.TclError:
        pass
    # Not `or None`: that turned an empty box into "keep the default" and made
    # the "nothing was typed" refusal unreachable from the one door that has a
    # window.
    return chosen["home"] if "home" in chosen else None


def link_label(parent, url: str, **kwargs):
    """A URL as something to click, for the doors that have a window.

    A plain ``tk.Label`` with a link's pointer and ``webbrowser.open`` bound to
    a click. Not ``tkinter.ttk`` and not a text widget: the launcher is frozen
    with ``--windowed``, where a submodule only one path imports is missing
    exactly where nobody can see it.
    """
    import tkinter as tk

    label = tk.Label(parent, text=url, fg="#0645ad", cursor="hand2", anchor="w",
                     justify="left", **kwargs)
    label.bind("<Button-1>", lambda _event: webbrowser.open(url))
    return label


SITTING_INTRO = "What MyScribe found here is not asked again. The next steps ask the rest, one at a time."
NOTHING_IS_OPEN = "Nothing is open: everything MyScribe asks about is answered or already here."

SKIP_PREFIX = "If you skip: "
"""The one line under a question about Skip (TASK-098, criterion 4) is this
and the first sentence of the engine's `if_skipped` for that question; the
rest of it, and `answer_later`, wait behind `SKIP_DETAILS`. No sentence of
the launcher's own: a fixed "Skip writes nothing" was false on the library
step, where a skip starts a new library (ADR-015: the engine owns the text;
Robert, 2026-10-24)."""
SKIP_DETAILS = "Details"


def details_toggle(details) -> Callable:
    """The click on "Details": the first shows the engine's sentences under
    the skip line, the next hides them again."""
    shown = [False]

    def toggle(_event=None) -> None:
        if shown[0]:
            details.pack_forget()
        else:
            details.pack(fill="x", pady=(4, 0))
        shown[0] = not shown[0]

    return toggle


def sentences(text: str) -> list[str]:
    """A text of the engine's cut into its sentences: after a full stop, a
    question mark or an exclamation mark that white space follows. A path or
    a version has no space after its dots and stays whole, and a full stop
    after a lone letter is an initial, as in a profile `C:\\Users\\J. Smith`."""
    return [sentence for sentence in re.split(r"(?<![\s\\/][A-Za-z]\.)(?<=[.?!])\s+", str(text).strip()) if sentence]


def asking_sentence(text: str) -> str:
    """The sentence of a question's text that asks it: the first one ending
    in a question mark, or else the first. The summary names a question by
    this, because some texts are a paragraph - the Ollama offer carries a URL,
    a size, a sha256 and a command line - and repeating all of it made the
    summary the tallest step of the sitting (TASK-098)."""
    said = sentences(text) or [""]
    return next((sentence for sentence in said if sentence.endswith("?")), said[0])


def skip_texts(question: dict) -> tuple[str, str]:
    """What a step says about Skip: the line on screen, and what waits behind
    "Details". The line is the first sentence of the question's `if_skipped`;
    the details are the rest of it and `answer_later`. Either is "" when the
    engine gave nothing for it, and then nothing is drawn for it."""
    first, *rest = sentences(question.get("if_skipped") or "") or [""]
    later = str(question.get("answer_later") or "").strip()
    behind = [" ".join(rest)] if rest else []
    if later:
        behind.append(f"Later: {later}")
    return (SKIP_PREFIX + first if first else "", "\n".join(behind))


def ask_setup(root, layout: Layout, plan: dict) -> dict | None:
    """The sitting, drawn from ``scribe.setup --plan`` and from nothing else.

    The launcher renders and never decides (ADR-015): which questions exist,
    what each one says, what skipping it costs and where it can be answered
    later all come out of the document. ``shown_if`` is the one condition
    interpreted here - "show this while the answer to question X is Y", which
    is how the key for a cloud provider appears under the provider that needs
    it.

    What it replaces was a fixed form of four questions that could not see the
    machine. It always drew the token field, so somebody whose token was
    already in ``.env`` was shown an empty box, reasonably concluded it was
    missing and went to mint a second one; and its provider and tier radios
    were a tuple written into this file, which agreed with the app's list only
    by hand.

    Radios open on ``current``, the stored value, and never on ``default``: a
    preselected default is an answer nobody gave, which ADR-016 forbids and
    TASK-089.25 came from. A yes-no question is the exception and opens on the
    plan's ``default`` - a checkbox has no unanswered state, and the one this
    asks writes no setting row; Robert ratified that reading on 2026-09-22.

    Returns one answer per question that was on screen - a value, or None for
    a question that was skipped - or None for "Ask me next time" and for a
    window somebody closed. A sitting in which every question was skipped is
    still a dict, because it was held: the engine stamps it and no start opens
    it again (TASK-089.11).
    """
    import tkinter as tk
    from tkinter import messagebox

    win = tk.Toplevel(root)
    win.title(f"Set up {APP_NAME}")
    win.transient(root)
    win.grab_set()
    win.minsize(560, 360)
    answers: dict = {}
    saved: list[bool] = []
    questions = [question for question in (plan.get("questions") or []) if question.get("id")]

    # One step per screen (TASK-098). Every step is a frame in `pages`, and
    # exactly one of them is gridded at a time. Until 2026-09-27 all of this
    # was one form, taller than a 1080p screen, with "Save and start" below
    # its bottom edge: the only way out was the close box, which is "ask me
    # next time", and a library somebody had just named was never taken over.
    pages = tk.Frame(win)
    pages.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 0))

    intro = tk.Frame(pages, name="intro")
    tk.Label(intro, text=SITTING_INTRO, wraplength=520, justify="left", anchor="w").pack(
        fill="x", pady=(0, 8))
    found = found_lines(plan)
    if found:
        tk.Label(intro, text="Found on this machine", anchor="w", justify="left").pack(fill="x")
        tk.Label(intro, text="\n".join(found), anchor="w", justify="left", wraplength=520,
                 fg="#555").pack(fill="x", padx=12, pady=(0, 8))
    if not questions:
        tk.Label(intro, text=NOTHING_IS_OPEN, wraplength=520, justify="left", anchor="w").pack(fill="x")

    # Tk draws every button of a group with its "mixed" indicator while the
    # group's variable holds that button's -tristatevalue, and that option
    # defaults to the empty string - which is what a question nobody answered
    # holds here. Photographed on 2026-09-22 (Tk 8.6, Windows 11): with the
    # default, every circle opens filled, so a dialog that filled nothing in
    # looks like one that filled everything in (TASK-089.25).
    not_an_answer = "no answer"

    blocks: list = []
    values: dict = {}
    skips: dict = {}
    entries: dict = {}

    def answer_of(question: dict):
        """What one question would hand over now: a value, or None for a skip
        and for a control nobody touched - nothing typed is nothing to write.

        Those two are the same record and the difference is not kept: the
        engine writes every None as ``skipped``, which is one of the states a
        later start does not ask again. So pressing "Save and start" past a
        radio group nobody touched accepts that question's `if_skipped`
        consequence, and the Setup button is what re-opens it. The console
        door of the same engine makes a skip explicit by typing `s`; contract 2
        has no third state for "on screen, untouched", and adding one is a
        CONTRACT bump and TASK-089.09's.
        """
        name = question["id"]
        if skips[name].get():
            return None
        if question.get("kind") in ("secret", "text"):
            return entries[name].get().strip() or None
        if question.get("kind") == "yes-no":
            return "yes" if values[name].get() else "no"
        return values[name].get() or None

    def given() -> dict:
        return {question["id"]: answer_of(question) for question, _block in blocks}

    def touched(name: str) -> None:
        """Answering a question takes back a Skip pressed on it earlier."""
        skips[name].set(False)

    for question in questions:
        name = question["id"]
        kind = question.get("kind") or "choice"
        block = tk.Frame(pages, name=f"q_{name}")
        blocks.append((question, block))
        skips[name] = tk.BooleanVar(value=False)

        if kind == "yes-no":
            values[name] = tk.BooleanVar(value=question.get("default") == "yes")
            tk.Checkbutton(block, variable=values[name], text=question.get("text", ""),
                           wraplength=500, justify="left", anchor="w",
                           command=lambda name=name: touched(name)).pack(fill="x")
        else:
            tk.Label(block, text=question.get("text", ""), wraplength=520, justify="left",
                     anchor="w").pack(fill="x", pady=(0, 6))

        if kind == "secret":
            entries[name] = tk.Entry(block, width=44, show="•")
            entries[name].bind("<KeyRelease>", lambda _event, name=name: touched(name))
            entries[name].pack(fill="x")
            if name == CONDITIONS_FOR:
                tk.Label(block, text="Accept the model's conditions with the same account at",
                         anchor="w", justify="left", fg="#555").pack(fill="x")
                link_label(block, CONDITIONS_URL).pack(fill="x")
        elif kind == "text":
            # A folder, typed or browsed (TASK-089.19; TASK-089.20's watch
            # folder is the same kind).
            entries[name] = tk.Entry(block, width=44)
            if question.get("current"):
                entries[name].insert(0, str(question["current"]))
            entries[name].bind("<KeyRelease>", lambda _event, name=name: touched(name))
            entries[name].pack(fill="x")

            def browse(entry=entries[name], name=name) -> None:
                from tkinter import filedialog

                # The home folder, not wherever the OS dialog last was: on macOS
                # that is often Downloads, and Downloads is the folder that
                # became 882 jobs on a Mac (TASK-107.01).
                picked = filedialog.askdirectory(parent=win, mustexist=True, initialdir=str(Path.home()))
                if picked:
                    entry.delete(0, "end")
                    entry.insert(0, picked)
                    touched(name)

            tk.Button(block, text="Browse...", command=browse).pack(anchor="w")
        elif kind != "yes-no":
            values[name] = tk.StringVar(value=opening_value(question))
            for choice in question.get("choices") or []:
                # Wrapped: a library found at a long path is a label as wide
                # as the path, and it made the window 1536 pixels wide.
                tk.Radiobutton(block, text=str(choice.get("label") or choice.get("value")),
                               variable=values[name], value=str(choice.get("value")),
                               tristatevalue=not_an_answer, anchor="w", justify="left",
                               wraplength=500,
                               command=lambda name=name: touched(name)).pack(fill="x")
            notes = [f"{choice.get('label') or choice.get('value')}: {choice['note']}"
                     for choice in question.get("choices") or [] if choice.get("note")]
            if notes:
                tk.Label(block, text="\n".join(notes), wraplength=520, justify="left",
                         anchor="w", fg="#555").pack(fill="x", pady=(6, 0))

        # What Skip costs, and where the question is answered later: the
        # first sentence of the engine's own `if_skipped` on screen, the rest
        # behind "Details" (TASK-098). Shown whole they were two to eight
        # lines under every question.
        line, behind = skip_texts(question)
        skip_row = tk.Frame(block)
        skip_row.pack(fill="x", pady=(10, 0))
        if line:
            # Wrapped: the library step's first sentence names a path.
            tk.Label(skip_row, text=line, anchor="w", justify="left", fg="#666", wraplength=440,
                     font=("TkDefaultFont", 8)).pack(side="left")
        if behind:
            details = tk.Label(block, text=behind, wraplength=520, justify="left", anchor="w",
                               fg="#666", font=("TkDefaultFont", 8))
            more = tk.Label(skip_row, text=SKIP_DETAILS, fg="#0645ad", cursor="hand2",
                            font=("TkDefaultFont", 8, "underline"))
            more.pack(side="left", anchor="n", padx=(6, 0))
            more.bind("<Button-1>", details_toggle(details))

    summary = tk.Frame(pages, name="summary")
    tk.Label(summary, text="Ready. This is what will be saved:", anchor="w",
             justify="left").pack(fill="x", pady=(0, 6))
    summary_lines = tk.Label(summary, text="", anchor="w", justify="left", wraplength=520)
    summary_lines.pack(fill="x")
    tk.Label(summary, text="Back changes an answer. Everything can be changed later in Settings "
                           "or with the Setup button.",
             anchor="w", justify="left", wraplength=520, fg="#555").pack(fill="x", pady=(8, 0))

    def shown_answer(question: dict, value) -> str:
        """One line of the summary. A secret is never shown back."""
        if skips[question["id"]].get():
            return "skipped"
        if value is None:
            return "not answered (saved as skipped)"
        if question.get("kind") == "secret":
            return "given"
        for choice in question.get("choices") or []:
            if str(choice.get("value")) == str(value):
                return str(choice.get("label") or value)
        return str(value)

    def sequence() -> list:
        """The steps as they stand now: `shown_if` decides which questions
        are among them (ADR-015's Must Not: no other condition)."""
        now = given()
        return (["intro"] + [question["id"] for question, _block in blocks if shown(question, now)]
                + ["summary"])

    frames = {"intro": intro, "summary": summary, **{q["id"]: block for q, block in blocks}}
    current: list[str] = ["intro"]

    nav = tk.Frame(win)
    nav.grid(row=1, column=0, sticky="we", padx=12, pady=12)
    later_button = tk.Button(nav, text="Ask me next time", command=win.destroy)
    save_button = tk.Button(nav, text="Save and start", command=lambda: save(), default="active")
    next_button = tk.Button(nav, text="Next", command=lambda: step(+1), default="active")
    skip_button = tk.Button(nav, text="Skip", command=lambda: skip())
    back_button = tk.Button(nav, text="Back", command=lambda: step(-1))

    def show(page: str) -> None:
        current[0] = page
        for key, frame in frames.items():
            if key == page:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if page == "summary":
            now = given()
            summary_lines.configure(text="\n".join(
                f"• {asking_sentence(question.get('text', ''))}  →  {shown_answer(question, now[question['id']])}"
                for question, _block in blocks if shown(question, now)) or NOTHING_IS_OPEN)
        # "Ask me next time", and not "Skip for now": nothing is applied, the
        # app writes no stamp, and the sitting therefore returns at the next
        # start. The other skip belongs to a single question and is the
        # engine's, where it is recorded as skipped and never asked by a start
        # again (TASK-089.11).
        later_button.pack(side="left")
        for button in (save_button, next_button, skip_button, back_button):
            button.pack_forget()
        if page == "summary":
            save_button.pack(side="right")
        else:
            next_button.pack(side="right")
        if page not in ("intro", "summary"):
            skip_button.pack(side="right", padx=8)
        if page != "intro":
            back_button.pack(side="right", padx=(0, 8) if page == "summary" else 0)

    def step(direction: int) -> None:
        order = sequence()
        here = order.index(current[0]) if current[0] in order else 0
        show(order[max(0, min(len(order) - 1, here + direction))])

    def skip() -> None:
        if current[0] in skips:
            skips[current[0]].set(True)
        step(+1)

    def save() -> None:
        """Everything that was a step, and nothing that was not: a question
        ``shown_if`` left out was never put, and an id the document does not
        carry is one the engine never mentions in the stamp."""
        now = given()
        answers.update({question["id"]: now[question["id"]]
                        for question, _block in blocks if shown(question, now)})
        saved.append(True)
        win.destroy()

    def close() -> None:
        """The close box asks first: closing is "ask me next time", and on
        2026-09-27 it silently dropped a library that had just been named."""
        if messagebox.askyesno(
            f"Close {APP_NAME} setup?",
            "Nothing you chose here is saved, and MyScribe asks these questions again at the "
            "next start. Close anyway?",
            parent=win,
        ):
            win.destroy()

    win.protocol("WM_DELETE_WINDOW", close)
    win.columnconfigure(0, weight=1)
    win.rowconfigure(0, weight=1)
    show("intro")
    root.wait_window(win)
    # Not `answers or None`: a sitting in which every question was skipped is
    # an empty dict and is still a sitting, and turning it into None would
    # leave the stamp unwritten and the gate open at every start (criterion #4).
    return answers if saved else None


def opening_value(question: dict) -> str:
    """Which radio a group opens on: the stored value, when it is one of the
    answers offered. Nothing otherwise - and never the plan's ``default``,
    which is what the console's Enter takes and not something a window may
    fill in on somebody's behalf."""
    current = str(question.get("current") or "")
    offered = {str(choice.get("value")) for choice in question.get("choices") or []}
    return current if current in offered else ""


def ask_failure(root, sentence: str) -> bool:
    """A setup that failed, said in a window with something to do about it.

    Two buttons, because there are exactly two things to do: hold the sitting
    again, or start MyScribe without what failed. Returns True for Retry.

    What this replaces was one grey line in a scrolling log - "Saving your
    answers failed with exit code 3" - under a headline that by then already
    said "MyScribe is running".
    """
    import tkinter as tk

    win = tk.Toplevel(root)
    win.title(f"Set up {APP_NAME}")
    win.transient(root)
    win.grab_set()
    # In front, so the choice is seen rather than found (TASK-107.03).
    win.lift()
    win.focus_force()
    again: list[bool] = []

    tk.Label(win, text=sentence, wraplength=520, justify="left", anchor="w").pack(
        fill="both", expand=True, padx=12, pady=12)
    if CONDITIONS_URL in sentence:
        link_label(win, CONDITIONS_URL).pack(fill="x", padx=12)

    def retry() -> None:
        again.append(True)
        win.destroy()

    row = tk.Frame(win)
    row.pack(fill="x", padx=12, pady=12)
    tk.Button(row, text="Retry", command=retry, default="active").pack(side="right")
    tk.Button(row, text="Continue without", command=win.destroy).pack(side="right", padx=8)

    root.wait_window(win)
    return bool(again)


def app_running(launch: Launch) -> bool:
    app = launch.app
    return app is not None and app.proc is not None and app.alive()


def window_turn(lock: InstanceLock | None, launch: Launch, work: Callable[[], object],
                report: Callable[[str, str], None]) -> None:
    """One turn of the window's worker - the start, or a Setup sitting -
    under the instance lock (review of TASK-112.07).

    The window stays open after a failed start and after the app stopped. It
    used to keep the lock for as long as it was open, so the next double-click
    said "already starting" and started nothing. Now the lock is let go of
    when no MyScribe of this window runs (here, and in `after_the_app`); a
    turn takes it again first, and is refused when another start has it.
    """
    if lock is not None and not lock.acquire():
        report("status", ONE_JOB_LOCKED.format(home=launch.layout.home))
        return
    try:
        work()
    finally:
        if lock is not None and not app_running(launch):
            lock.release()


def after_the_app(launch: Launch, lock: InstanceLock | None, busy: bool) -> str:
    """What the window says of an app that stopped under it; "" while it runs
    and before there is one. The lock goes with it, unless a turn is `busy`,
    which lets go itself when it ends."""
    app = launch.app
    if app is None or app.proc is None or app.alive():
        return ""
    if lock is not None and not busy:
        lock.release()
    # 0 is a stop somebody asked for, from Settings or --stop (TASK-112.03):
    # not a crash, and app.log has nothing to say.
    return STOPPED if app.exit_code() == 0 else f"MyScribe stopped; see {launch.layout.logs_dir / 'app.log'}"


def run_window(layout: Layout, port: int, open_browser: bool, force_setup: bool = False,
               at_login: bool = False, notice: str = "", lock: InstanceLock | None = None) -> int:
    import tkinter as tk
    from tkinter import scrolledtext, ttk

    events: "queue.Queue[tuple[str, str]]" = queue.Queue()
    launch = Launch(layout, port, open_browser, lambda state, text: events.put((state, text)))

    root = tk.Tk()
    root.title(f"{APP_NAME} {app_version(layout)}")
    root.geometry("560x360")
    status = tk.StringVar(value="Preparing...")
    tk.Label(root, textvariable=status, anchor="w", justify="left", wraplength=540).pack(fill="x", padx=10, pady=(10, 4))
    # A bar for the downloads, and it stays out of the way until there is one:
    # the engine prints one JSON line per whole percent, and before
    # TASK-089.15 each of those became a log line of its own - a hundred per
    # repository - under a headline that said "Saving your answers..."
    # throughout the whole install.
    bar = ttk.Progressbar(root, orient="horizontal", mode="determinate", maximum=100)
    log = scrolledtext.ScrolledText(root, height=12, state="disabled", font=("TkFixedFont", 9))
    log.pack(fill="both", expand=True, padx=10)
    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=10, pady=10)
    open_button = tk.Button(buttons, text="Open MyScribe", state="disabled", command=lambda: webbrowser.open(launch.url()))
    open_button.pack(side="left")
    tk.Button(buttons, text="Open data folder", command=lambda: open_folder(layout.home)).pack(side="left", padx=8)

    stopping = threading.Event()

    def quit_app() -> None:
        """Quit stops the setup child too, and that one process only.

        `launch.stop()` terminates the setup child and stops the app through
        its stop route, the hard stop after (TASK-112.09); the two are
        deliberately different, so that no tree kill can pass over an Ollama
        a third-party installer has just started (ADR-017's M10). While that
        installer runs, Quit is refused with a sentence and the window stays
        (TASK-089.18, criterion 14).

        The stop runs on a worker, because it waits for the running job to be
        put back in the queue - seconds - and the window has to keep saying
        what happens meanwhile. The worker calls nothing in Tk (TASK-101):
        what it says reaches the window through `events`, and the window
        closes from here once it is done. A second Quit, or a SIGTERM, while
        it runs starts nothing.
        """
        if stopping.is_set():
            return
        why = launch.quit_refused()
        if why:
            status.set(why)
            append(why)
            return
        stopping.set()
        status.set(STOPPING)
        stopped = threading.Event()

        def stop_in_a_worker() -> None:
            try:
                launch.stop()
            except Exception:
                leave_a_trace("while stopping", traceback.format_exc())
            finally:
                stopped.set()

        threading.Thread(target=stop_in_a_worker, name="myscribe-stop", daemon=True).start()

        def close_when_stopped() -> None:
            if stopped.is_set():
                root.after(1500, root.destroy)  # long enough to read the last line
            else:
                root.after(200, close_when_stopped)

        root.after(200, close_when_stopped)

    tk.Button(buttons, text="Quit", command=quit_app).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", quit_app)

    def append(line: str) -> None:
        log.configure(state="normal")
        log.insert("end", line + "\n")
        log.see("end")
        log.configure(state="disabled")

    def leave_a_trace(where: str, trace: str) -> None:
        """A windowed build has no stderr, so an exception nobody catches is
        written to launcher.log, and the window says where it went
        (TASK-101: Jim's walk found a hang that left no line anywhere)."""
        launch.log.write(launch.log.redact(trace.rstrip("\n")))
        launch.report("error", f"MyScribe hit an error ({where}); see {layout.logs_dir / INSTALL_LOG}")

    root.report_callback_exception = lambda kind, value, tb: leave_a_trace(
        "in the window", "".join(traceback.format_exception(kind, value, tb)))

    # What a worker needs done on the Tk thread, as (work, where to answer).
    # A plain queue and not `root.after`: TASK-101. Python 3.12's _tkinter
    # takes Tcl 9 - which the macOS build carries - for unthreaded, and then
    # a Tk call from a worker is not handed to the Tk thread but runs in the
    # worker, where `after` puts its timer in a list nobody services. With no
    # Tk call left on any worker, how _tkinter judges Tcl does not matter.
    requests: "queue.Queue[tuple[Callable, queue.Queue]]" = queue.Queue()

    def pump() -> None:
        try:
            while True:
                state, text = events.get_nowait()
                if state == "progress":
                    # Never appended: a bar is what these are for, and as log
                    # lines they push everything that says something out of
                    # the window.
                    event = progress_line(text)
                    if event is not None:
                        if not bar.winfo_ismapped():
                            bar.pack(fill="x", padx=10, pady=(0, 6), before=log)
                        bar["value"] = event.get("percent", 0)
                        status.set(progress_headline(event))
                    continue
                if bar.winfo_ismapped():
                    bar.pack_forget()
                if state == "busy":
                    append(text)
                else:
                    status.set(text)
                    append(text)
                if state in ("running", "done"):
                    open_button.configure(state="normal")
                if state == "done":
                    root.after(3000, root.destroy)
        except queue.Empty:
            pass
        # Requests after the events, not before (TASK-107.03): a request is a
        # dialog that runs right here, inside this tick, and whatever the
        # worker said before asking for it - "a choice is waiting" - has to be
        # on screen while it is open, not after it closed.
        while True:
            try:
                work, answer = requests.get_nowait()
            except queue.Empty:
                break
            try:
                result = work()
            except Exception:
                leave_a_trace("in the setup window", traceback.format_exc())
                result = None
            answer.put(result)
        # Under `turning`, so a turn cannot take the lock between the look and
        # the letting go; while one runs it lets go itself (TASK-112.07 review).
        # Not under `sitting`: a Setup click that met the pump there was dropped.
        free = turning.acquire(blocking=False)
        try:
            stopped = after_the_app(launch, lock, busy=not free)
        finally:
            if free:
                turning.release()
        # The lock goes whenever the app has ended, a Quit's stop too; but the
        # line is not said then: the stop says how it ended (TASK-112.09).
        if stopped and not stopping.is_set():
            status.set(stopped)
        root.after(200, pump)

    def on_tk(work: Callable):
        """Run `work` on the Tk thread and wait for what it gives back.

        Tk is not thread-safe, and the sequence runs on a worker so the sync
        does not freeze the window; so the worker puts the dialog on
        `requests`, which `pump` runs on the Tk thread, and blocks until it
        closes. The worker itself calls nothing in Tk (TASK-101).

        There is no timeout on that wait, because a person may sit at the
        questions for minutes. `pump` answers on every path, an exception
        included; a Quit before it gets there leaves the worker waiting, and
        that costs nothing - it is a daemon thread and the process ends with
        the mainloop.
        """
        answered: "queue.Queue" = queue.Queue()
        requests.put((work, answered))
        return answered.get()

    def ask(plan: dict) -> dict | None:
        return on_tk(lambda: ask_setup(root, layout, plan))

    def retry(sentence: str) -> bool:
        def waiting_then_ask() -> bool:
            # On the Tk thread, in the same work that opens the dialog, so the
            # main window says a choice is waiting before the dialog appears -
            # whatever order pump drains its two queues in (PR #9 review,
            # finding 3). It used to stay on "Saving your answers..." (TASK-107.03).
            status.set(CHOICE_WAITING)
            append(CHOICE_WAITING)
            return ask_failure(root, sentence)

        return bool(on_tk(waiting_then_ask))

    sitting = threading.Lock()
    # A turn holds this for its whole run; the pump only looks while it is
    # free, and never makes a turn give up - it waits a pump tick at most.
    turning = threading.Lock()

    def take_a_turn(work: Callable[[], object]) -> None:
        with turning:
            window_turn(lock, launch, work, launch.report)

    def in_a_worker(work: Callable, name: str) -> None:
        def guarded() -> None:
            if not sitting.acquire(blocking=False):
                return  # one sitting at a time: two dialogs would fight over grab_set
            try:
                work()
            except Exception:
                leave_a_trace(f"in {name}", traceback.format_exc())
            finally:
                sitting.release()

        threading.Thread(target=guarded, name=name, daemon=True).start()

    def open_setup() -> None:
        """The Setup button: the whole plan, so a question recorded as skipped
        is asked again - which is the one route by which somebody who skipped
        on purpose can find it (TASK-089.11 records the skip, this reopens it).

        While MyScribe serves, the sitting still runs: the port goes to
        `--prove`, so the engine queues the doctor job instead of loading a
        model in the setup child (ADR-001, TASK-089.13).
        """
        in_a_worker(
            lambda: take_a_turn(
                lambda: open_sitting(launch, ask, launch.report, force_setup=True, retry=retry)),
            "myscribe-setup",
        )

    tk.Button(buttons, text="Setup", command=open_setup).pack(side="left", padx=8)

    def begin() -> None:
        """One worker thread for the whole sequence.

        The questions used to come first, so a token given here was in `.env`
        before anything read it. They now come after the sync, because the
        python that applies them is what the sync makes - and the runner
        children still inherit the environment the app was started with, which
        is started last either way (TASK-089.01).
        """
        if notice:  # the last login's problem, before this start's own lines
            launch.report("status", notice)
        in_a_worker(
            lambda: take_a_turn(
                lambda: first_run(launch, ask, launch.report, force_setup, at_login, retry)),
            "myscribe-launch",
        )

    root.after(50, begin)
    root.after(200, pump)
    with stop_on_sigterm(then=quit_app):  # TASK-102.03: a pkill is a Quit
        root.mainloop()
    return 0


def open_folder(path: Path) -> None:
    if IS_WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --- entry point ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="myscribe", description="Start MyScribe.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--headless", action="store_true", help="no window; progress on the console")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--at-login",
        action="store_true",
        help="started by the login entry: no first-run questions, nobody is at the screen",
    )
    parser.add_argument("--home", type=Path, help=f"the per-user folder (default: {home_dir()})")
    parser.add_argument("--payload", type=Path, help="the shipped app/ and bin/ (default: inside the launcher)")
    parser.add_argument("--sync-only", action="store_true", help="install or update the environment, then exit")
    parser.add_argument("--doctor", nargs=argparse.REMAINDER, help="run python -m scribe.doctor [ARGS] in the environment")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="ask the first-run questions again (they are also in Settings)",
    )
    parser.add_argument("--smoke", action="store_true", help="start the app, check /health and /, stop it (CI)")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--stop", action="store_true",
                        help="stop the MyScribe that runs on --port; its running job starts over "
                             "at the next start")
    return parser


def show_error(message: str) -> None:
    """One sentence in a window, for the doors that have a screen and no console.

    Plain widgets and not ``tkinter.messagebox``: ``run_window`` and
    ``ask_location`` already prove that ``tkinter`` itself survives the freeze,
    and a submodule only this path imports would be missing exactly where
    nobody could see it - behind ``--windowed``, where failing to show the
    window is as silent as the ``print`` this replaces.

    Tk is imported in here and never at the top of the file, the way
    ``ask_location`` does it: ``test_the_sequence_is_free_of_tk`` holds that
    line, and a build without Tk must still reach ``main``. A window that
    cannot be put up is not worth an exception - whoever has a console was
    told the same sentence there.
    """
    try:
        import tkinter as tk

        root = tk.Tk()
        root.title(APP_NAME)
        tk.Label(root, text=message, justify="left", anchor="w", wraplength=520).pack(
            fill="both", expand=True, padx=12, pady=12)
        tk.Button(root, text="Close", command=root.destroy, default="active").pack(pady=(0, 12))
        root.mainloop()
    except Exception:  # every way Tk has of not being there, including TclError
        pass


def tkinter_present() -> bool:
    """Whether this build has Tk at all; without it every door is the console."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    payload = args.payload or payload_dir()
    if args.version:  # before the location, which must not open a window for a version
        print(app_version(Layout(args.home or home_dir(), payload)))
        return 0
    if args.stop:  # needs no home: the port names the MyScribe to stop
        def said(state: str, text: str) -> None:
            print(text, flush=True)
            write_early([text])  # behind --windowed there is no stdout to read

        return stop_running(args.port, said)

    problems: list[str] = []
    before_home = [True]

    def console(state: str, text: str) -> None:
        if state == "error":
            problems.append(text)
            if before_home[0]:  # no launcher.log to say it in yet (TASK-112.02)
                write_early([text])
        print(text, flush=True)

    quiet = args.sync_only or args.doctor is not None or args.smoke
    # `APP_DIED`'s contract is for the doors that serve. The one-job doors are
    # read as pass or fail by CI and by scripts, and nothing restarts them.
    stopped = 1 if quiet else 0
    windowed = not quiet and not args.headless and tkinter_present()
    # Where everything goes is settled here, before a Layout exists: nothing
    # may be written under any home, and nothing downloaded, until it is
    # answered (TASK-089.14). Only a door with a window and somebody in front
    # of it asks; the rest keep today's location and say how to change it.
    home = locate_home(args.home, ask_location if windowed and not args.at_login else None,
                       console, payload)
    if home is None:
        # `console` is `print`, and behind PyInstaller's --windowed there is no
        # stdout to print to: CPython's `print` returns without a word. A start
        # at login over a drive that is not plugged in would exit 1 in complete
        # silence, so the last sentence gets a window of its own.
        if windowed and problems:
            show_error(problems[-1])
        return stopped
    # TASK-089.19: an adopted library, the pointer's second fact. Not under a
    # home given by hand: `--home` and MYSCRIBE_HOME mean "everything here".
    data, missing, pointer = None, "", None
    if args.home is None and not os.environ.get(HOME_VARIABLE):
        pointer = pointer_path()
        data, missing = adopted_data(pointer)
    if missing:
        console("error", missing)
        if windowed:
            show_error(missing)
        return stopped
    layout = Layout(home, payload, data=data, pointer=pointer)
    before_home[0] = False
    # One launcher per home from here on (TASK-112.07): before the /health
    # probe in `Launch.prepare`, before the sync, before the app.
    lock = InstanceLock(instance_lock_path(layout.home), port=args.port)
    if not lock.acquire():
        if args.doctor is not None:
            # The doctor is what you run while MyScribe runs, to ask why; it
            # runs beside it, but never syncs into an environment another
            # start is installing or updating (TASK-112.07 review).
            if needs_sync(layout):
                console("error", DOCTOR_LOCKED.format(home=layout.home))
                return 1
            return run_doctor(args, layout)
        if quiet:
            console("error", ONE_JOB_LOCKED.format(home=layout.home))
            return 1
        # The port of the one that holds it, which a different --port would
        # otherwise wait two minutes beside (TASK-112.07 review).
        port = held_port(lock.path) or args.port
        code = another_start(port, not args.no_browser, console, windowed)
        if args.setup:  # as `first_run` answers --setup against one that serves
            console("status", SETUP_WHILE_RUNNING)
        return code
    if lock.problem:
        console("status", lock.problem)
    try:
        return run_doors(args, layout, console, quiet, windowed, lock)
    finally:
        lock.release()


def run_doctor(args: argparse.Namespace, layout: Layout) -> int:
    _release_frozen_dll_directory()
    command = [str(layout.env_python), "-m", "scribe.doctor", *args.doctor]
    return subprocess.call(command, cwd=str(layout.app_dir), env=app_environment(layout))


def run_doors(args: argparse.Namespace, layout: Layout, console: Callable[[str, str], None],
              quiet: bool, windowed: bool, lock: InstanceLock | None = None) -> int:
    """`main` past the home and the lock: the one-job doors, the console and
    the window. The window lets go of the lock while it runs no MyScribe,
    which is why it is handed on."""
    if args.setup:  # where everything is, and the three ways to move it (ADR-015)
        pointer = pointer_path()
        console("status", location_note(layout.home, pointer, home_source(args.home, pointer=pointer)))

    if quiet:
        # `floor=False`: --smoke, --sync-only and --doctor install and then
        # stop; none of them keeps a library, so the room the app keeps free
        # to run with one is not theirs to demand (TASK-089.14).
        if needs_sync(layout) and not enough_disk(layout, console, floor=False):
            return 1  # before the tools are unpacked into it, as in `Launch.prepare`
        prepare_home(layout)
        install_tools(layout)
        _release_frozen_dll_directory()
        say = lambda line: print(line, flush=True)  # noqa: E731
        if args.sync_only:
            # TASK-102.08: this door is what CI and a scripted install run,
            # and it used to leave no line in the home and, when nothing was
            # due, none on the console either.
            record = InstallLog(layout.logs_dir / INSTALL_LOG)

            def say(line: str) -> None:
                print(line, flush=True)
                record.write(line)

        if needs_sync(layout):
            if not sync(layout, say):
                return 1
            if args.sync_only:
                say(SYNCED)
        elif args.sync_only:
            say(UP_TO_DATE)
        if args.doctor is not None:
            # The lock was for the sync: a doctor that loads a model for
            # minutes must not hold up the start at login.
            if lock is not None:
                lock.release()
            return run_doctor(args, layout)
        if args.smoke:
            return smoke(layout, args.port)
        return 0

    # The last login's problem, said once by the first start made by hand
    # (TASK-112.07). Taken under the lock, so two starts never both say it.
    notice = "" if args.at_login else take_login_problem(layout.home)
    if not windowed:
        launch = Launch(layout, args.port, not args.no_browser, console)
        if notice:
            launch.report("status", notice)
        return run_headless(launch, force_setup=args.setup, at_login=args.at_login)
    extra = {"notice": notice} if notice else {}
    return run_window(
        layout, args.port, not args.no_browser, force_setup=args.setup, at_login=args.at_login,
        lock=lock, **extra,
    )


SMOKE_WROTE_NOTHING = "saved: nothing"
"""What `scribe.setup` prints for a document that answered no question. Read
rather than assumed: it is the engine's own word for "no setting row"."""


def smoke(layout: Layout, port: int) -> int:
    """The apply door and the app, walked by CI with nobody at a screen.

    `{}` goes over stdin first, which is the same pipe a real sitting uses -
    until TASK-089.15 nothing in CI ever walked it, because `--smoke` returned
    before the window. The narrow claim, and only that one: an empty document
    writes no setting row, read off the engine's own "saved: nothing". It does
    create the directories, migrate the database and write a stamp, so this is
    not proof that `{}` touches nothing.

    Everything said here is also written to `<home>/logs/launcher-smoke.log`,
    which `packaging/build_release.py` prints when the smoke fails: the
    Windows binary is windowed and has no console, so a failure there says
    nothing at all without the file.
    """
    record = InstallLog(layout.logs_dir / SMOKE_LOG)
    said: list[str] = []

    def say(line: str) -> None:
        said.append(line)
        record.write(line)
        print(line, flush=True)

    try:
        code = run_setup(layout, {}, say)
    except Exception as error:
        say(f"smoke: the answers could not be applied: {error}")
        return 1
    if code != 0:
        say(f"smoke: applying an empty document exited {code}")
        return 1
    if not any(line.strip() == SMOKE_WROTE_NOTHING for line in said):
        say("smoke: an empty document did not report 'saved: nothing'; a setting row may have "
            "been written, which an answer nobody gave must never do")
        return 1
    say("smoke: an empty document wrote no setting row")

    app = AppProcess(layout, port)
    app.start()
    try:
        if not app.wait_ready():
            say(f"smoke: app did not answer {health_url(port)}")
            say((layout.logs_dir / "app.log").read_text(encoding="utf-8", errors="replace")[-4000:])
            return 1
        with loopback_opener().open(f"http://{HOST}:{port}/", timeout=10) as response:
            ok = response.status == 200 and b"MyScribe" in response.read()
        say(f"smoke: /health ok, / {'ok' if ok else 'unexpected'}")
        return 0 if ok else 1
    finally:
        app.stop()


if __name__ == "__main__":
    sys.exit(main())
