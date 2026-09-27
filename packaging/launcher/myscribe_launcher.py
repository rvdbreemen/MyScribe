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
STAMP_NAME = ".myscribe-sync.json"
READY_TIMEOUT = 120.0
STOP_TIMEOUT = 15.0

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


def prepare_home(layout: Layout) -> None:
    for folder in (layout.home, layout.data_dir, layout.logs_dir):
        folder.mkdir(parents=True, exist_ok=True)
    example = layout.app_dir / ".env.example"
    if not layout.env_file.exists() and example.exists():
        shutil.copyfile(example, layout.env_file)


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


def sync(layout: Layout, on_line: Callable[[str], None], base: dict | None = None) -> bool:
    """Sync the environment; write the stamp only when uv succeeded.

    The proxy named on the failure is the one the child was given, not this
    process's: a caller that hands `sync` a different environment would
    otherwise be told about the launcher's own.
    """
    env = sync_environment(layout, base)
    code = run_streaming(sync_command(layout), env=env, cwd=layout.home, on_line=on_line)
    if code != 0:
        on_line(f"uv sync failed with exit code {code}{proxy_note(env)}")
        return False
    write_stamp(layout)
    return True


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


def port_taken(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
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
        log = open(self.layout.logs_dir / "app.log", "ab")
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

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive():
                return False
            if running_instance(self.port, timeout=0.5):
                return True
            time.sleep(0.25)
        return False

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """SIGTERM to the group, so uvicorn shuts the supervisor down and the
        runners go with it; SIGKILL after ``timeout``. Windows has no signal
        that reaches a windowless process, so there it is taskkill on the tree;
        the next start's reconcile settles an interrupted job."""
        if self.proc is None or self.proc.poll() is not None:
            return
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
            )
            self.proc.wait(timeout)
            return
        try:
            group = os.getpgid(self.proc.pid)
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            self.proc.wait(timeout)
            return
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(group, signal.SIGKILL)  # stragglers: a runner mid-model-load
        except ProcessLookupError:
            pass
        self.proc.wait(timeout)


# --- what the launcher says, and where it is kept ---------------------------------


INSTALL_LOG = "launcher.log"
SMOKE_LOG = "launcher-smoke.log"
REDACTED = "***"


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
            with open(self.path, "a", encoding="utf-8") as handle:
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
            self.report("done", f"MyScribe is already running at {self.url()}")
            if self.open_browser:
                webbrowser.open(self.url())
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
            if not sync(self.layout, lambda line: self.report("busy", line)):
                self.report("error", "Installing failed; the lines above say why. Check the "
                                     "internet connection and start MyScribe again.")
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
            webbrowser.open(self.url())
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
        if self.installing:
            self.report("status", QUIT_REFUSED)
            return
        self.stop_setup()
        if self.app is not None:
            self.app.stop()

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
    """
    lines: list[str] = []
    for row in plan.get("found") or []:
        label = str(row.get("label") or row.get("name") or "")
        if row.get("kind") == "proxy":
            lines.append(f"{label}: {row.get('host', '')} ({row.get('source', '')})")
            continue
        lines.append(f"{label}: {row.get('source') if row.get('found') else 'not found'}")
        if row.get("also_in"):
            note = "also " + ", ".join(str(place) for place in row["also_in"])
            if row.get("conflict"):
                note += " - which defines a different value, not used"
            lines.append(f"    {note}")
    ollama = plan.get("ollama") or {}
    if ollama.get("note"):
        lines.append(f"Ollama: {ollama['note']}")
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
            report("status", "MyScribe is already running; quit it first and start again "
                             "with --setup to answer the questions.")
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


def run_headless(launch: Launch, force_setup: bool = False, at_login: bool = False) -> int:
    """The console door, and every build without Tk.

    It goes through `first_run` with no `ask`, so that a first start here is
    asked the same questions a window is - by the engine, with the terminal
    handed to it. Until TASK-089.15 this door ran `launch.run()`, which asks
    nothing at all: `--setup` on a Mac or in WSL did nothing visible.
    """
    if not first_run(launch, None, launch.report, force_setup, at_login):
        return 1
    if launch.serving or launch.app is None:  # another instance is serving
        return 0
    try:
        while launch.app.alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        launch.stop()
    return 0


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


SITTING_INTRO = (
    "What MyScribe already found on this machine is below. The next steps ask what is still "
    "open, one at a time; each has a Skip that writes nothing, and everything can be changed "
    "later in Settings."
)
NOTHING_IS_OPEN = "Nothing is open: everything MyScribe asks about is answered or already here."


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

                picked = filedialog.askdirectory(parent=win, mustexist=True)
                if picked:
                    entry.delete(0, "end")
                    entry.insert(0, picked)
                    touched(name)

            tk.Button(block, text="Browse...", command=browse).pack(anchor="w")
        elif kind != "yes-no":
            values[name] = tk.StringVar(value=opening_value(question))
            for choice in question.get("choices") or []:
                tk.Radiobutton(block, text=str(choice.get("label") or choice.get("value")),
                               variable=values[name], value=str(choice.get("value")),
                               tristatevalue=not_an_answer, anchor="w", justify="left",
                               command=lambda name=name: touched(name)).pack(fill="x")
            notes = [f"{choice.get('label') or choice.get('value')}: {choice['note']}"
                     for choice in question.get("choices") or [] if choice.get("note")]
            if notes:
                tk.Label(block, text="\n".join(notes), wraplength=520, justify="left",
                         anchor="w", fg="#555").pack(fill="x", pady=(6, 0))

        # What Skip costs, and where the question is answered later: the
        # engine's own sentences, smaller and under the question, where the
        # Skip button below them is the thing they explain.
        tk.Label(block, text=f"If you skip: {question.get('if_skipped', '')}\n"
                             f"Later: {question.get('answer_later', '')}",
                 wraplength=520, justify="left", anchor="w", fg="#666",
                 font=("TkDefaultFont", 8)).pack(fill="x", pady=(10, 0))

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
                f"• {question.get('text', '')}  →  {shown_answer(question, now[question['id']])}"
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


def run_window(layout: Layout, port: int, open_browser: bool, force_setup: bool = False,
               at_login: bool = False) -> int:
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

    def quit_app() -> None:
        """Quit stops the setup child too, and that one process only.

        `launch.stop()` terminates the setup child and tree-kills the app; the
        two are deliberately different, so that no tree kill can pass over an
        Ollama a third-party installer has just started (ADR-017's M10). While
        that installer runs, Quit is refused with a sentence and the window
        stays (TASK-089.18, criterion 14).
        """
        why = launch.quit_refused()
        if why:
            status.set(why)
            append(why)
            return
        status.set("Stopping MyScribe...")
        root.update_idletasks()
        launch.stop()
        root.destroy()

    tk.Button(buttons, text="Quit", command=quit_app).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", quit_app)

    def append(line: str) -> None:
        log.configure(state="normal")
        log.insert("end", line + "\n")
        log.see("end")
        log.configure(state="disabled")

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
        if launch.app is not None and launch.app.proc is not None and not launch.app.alive():
            status.set(f"MyScribe stopped; see {layout.logs_dir / 'app.log'}")
        root.after(200, pump)

    def on_tk(work: Callable):
        """Run `work` on the Tk thread and wait for what it gives back.

        Tk is not thread-safe, and the sequence runs on a worker so the sync
        does not freeze the window; so the worker asks for the dialog and
        blocks on a queue until it closes.

        There is no timeout on that wait, because a person may sit at the
        questions for minutes. What there is instead is an answer on every
        path: a Quit between the schedule and the dispatch leaves the worker
        waiting, and that costs nothing - it is a daemon thread and the
        process ends with the mainloop.
        """
        answered: "queue.Queue" = queue.Queue()

        def run() -> None:
            """On the Tk thread, and it always answers. An exception raised in
            here would otherwise go to Tk's own handler - stderr, which a
            windowed build has not got - and the worker would wait for ever
            with the app never started."""
            try:
                result = work()
            except Exception:
                result = None
            answered.put(result)

        try:
            root.after(0, run)
        except (tk.TclError, RuntimeError):
            # Quit during the sync: no window to ask in. Tk says so as a
            # TclError from a destroyed widget, and _tkinter as a RuntimeError
            # when the mainloop this thread would hand the call to is gone.
            return None
        return answered.get()

    def ask(plan: dict) -> dict | None:
        return on_tk(lambda: ask_setup(root, layout, plan))

    def retry(sentence: str) -> bool:
        return bool(on_tk(lambda: ask_failure(root, sentence)))

    sitting = threading.Lock()

    def in_a_worker(work: Callable, name: str) -> None:
        def guarded() -> None:
            if not sitting.acquire(blocking=False):
                return  # one sitting at a time: two dialogs would fight over grab_set
            try:
                work()
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
            lambda: open_sitting(launch, ask, launch.report, force_setup=True, retry=retry),
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
        in_a_worker(
            lambda: first_run(launch, ask, launch.report, force_setup, at_login, retry),
            "myscribe-launch",
        )

    root.after(50, begin)
    root.after(200, pump)
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

    problems: list[str] = []

    def console(state: str, text: str) -> None:
        if state == "error":
            problems.append(text)
        print(text, flush=True)

    quiet = args.sync_only or args.doctor is not None or args.smoke
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
        return 1
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
        return 1
    layout = Layout(home, payload, data=data, pointer=pointer)
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
        if needs_sync(layout):
            if not sync(layout, lambda line: print(line, flush=True)):
                return 1
        if args.doctor is not None:
            command = [str(layout.env_python), "-m", "scribe.doctor", *args.doctor]
            return subprocess.call(command, cwd=str(layout.app_dir), env=app_environment(layout))
        if args.smoke:
            return smoke(layout, args.port)
        return 0

    if not windowed:
        return run_headless(
            Launch(layout, args.port, not args.no_browser, console),
            force_setup=args.setup, at_login=args.at_login,
        )
    return run_window(
        layout, args.port, not args.no_browser, force_setup=args.setup, at_login=args.at_login
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
