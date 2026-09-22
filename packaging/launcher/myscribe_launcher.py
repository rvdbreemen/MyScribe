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

    def __init__(self, home: Path, payload: Path, windows: bool = IS_WINDOWS):
        # Absolute: the sync runs with the home as its working directory.
        self.home = Path(home).absolute()
        self.payload = Path(payload).absolute()
        self.windows = windows

    app_dir = property(lambda self: self.payload / "app")
    bin_dir = property(lambda self: self.payload / "bin")
    lock_file = property(lambda self: self.app_dir / "uv.lock")
    env_dir = property(lambda self: self.home / "env")
    python_dir = property(lambda self: self.home / "python")
    cache_dir = property(lambda self: self.home / "cache")
    data_dir = property(lambda self: self.home / "data")
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


def run_streaming(command: list[str], *, env: dict, cwd: Path | None, on_line: Callable[[str], None]) -> int:
    """Run ``command``, hand every output line to ``on_line``, return the exit code."""
    proc = subprocess.Popen(
        command, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
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
        lines.append(f"Up to {size['ollama_gb']:.1f} GB more if you later say yes to Ollama - its "
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


# --- the run ----------------------------------------------------------------------


class Launch:
    """The steps, in order, reporting through ``report(state, text)``.

    States: ``busy`` (a line of progress), ``status`` (a headline),
    ``running``, ``done`` (another instance serves), ``error``. Runs on a worker thread under the window; directly under
    ``--headless``.
    """

    def __init__(self, layout: Layout, port: int, open_browser: bool, report: Callable[[str, str], None]):
        self.layout = layout
        self.port = port
        self.open_browser = open_browser
        self.report = report
        self.app: AppProcess | None = None
        self.serving = False

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

    def stop(self) -> None:
        if self.app is not None:
            self.app.stop()


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


def setup_command(layout: Layout, answers: dict) -> list[str]:
    """`python -m scribe.setup` with the answers, in the app's environment.

    The launcher is frozen and stdlib-only (ADR-011): it cannot write a setting
    row or parse `.env` for itself, and should not learn how. It asks the
    questions and hands the answers to the app, the same way `--doctor` hands
    over a check.
    """
    command = [str(layout.env_python), "-m", "scribe.setup"]
    if answers.get("hf_token"):
        command += ["--hf-token", answers["hf_token"]]
    if answers.get("provider"):
        command += ["--provider", answers["provider"]]
    if answers.get("tier"):
        command += ["--tier", answers["tier"]]
    if answers.get("diarize") is not None:
        command += ["--diarize" if answers["diarize"] else "--no-diarize"]
    if answers.get("fetch_models"):
        command += ["--fetch-models"]
    return command


def run_setup(layout: Layout, answers: dict, on_line: Callable[[str], None]) -> int:
    """Apply the answers, streaming what happens - a 1.6 GB download has to
    look like something happening rather than a window that stopped.

    Returns the exit code, because the caller says it out loud: a setup that
    failed used to be a `False` nobody looked at (TASK-089.01, AC #4).
    """
    return run_streaming(
        setup_command(layout, answers),
        env=app_environment(layout),
        cwd=layout.app_dir,
        on_line=on_line,
    )


def first_run(launch: Launch, ask: Callable[[], dict | None], report: Callable[[str, str], None],
              force_setup: bool = False, at_login: bool = False) -> bool:
    """The whole sequence: prepare, sync, ask, apply, start.

    The order is the point. `scribe.setup` runs from the environment, and the
    environment is what the sync makes, so the sitting cannot come before it -
    asking first meant the Popen died on the worker thread with
    FileNotFoundError and the app was never started (TASK-089.01).

    Plain and Tk-free on purpose: `ask` and `report` are the only things that
    render, and nothing here decides an answer or writes one (ADR-011 keeps
    the launcher stdlib-only, ADR-015 keeps every front-end a renderer).

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
        answers = ask()
        if answers:
            report("status", "Saving your answers...")
            _apply_setup(launch.layout, answers, report)
    return launch.start_app()


def _apply_setup(layout: Layout, answers: dict, report: Callable[[str, str], None]) -> None:
    """Hand the answers to the app, and say so when that fails.

    Never raises and never falls silent: the app is worth more than the
    answers, which can also be given in Settings, and a windowed build has no
    stderr for the person to look at (AC #4).

    Every exception, and not only the `OSError` the Popen is known for: this
    runs on a worker thread of a windowed build, where anything that escapes
    is a window that stopped with nothing written anywhere. `KeyboardInterrupt`
    and `SystemExit` are not exceptions and still travel.
    """
    try:
        code = run_setup(layout, answers, lambda line: report("busy", line))
    except Exception as error:
        report("error", f"Saving your answers failed: {error}. MyScribe starts anyway; "
                        "the questions are also in Settings.")
        return
    if code != 0:
        report("error", f"Saving your answers failed with exit code {code}; the lines above say "
                        "why. MyScribe starts anyway; the questions are also in Settings.")


def run_headless(launch: Launch) -> int:
    if not launch.run():
        return 1
    if launch.app is None:  # another instance is serving
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


def ask_setup(root, layout: Layout) -> dict | None:
    """The four first-run questions, in one modal window.

    Four and no more. Each is something the app cannot work out for itself and
    would otherwise fail on later: the token speaker separation needs, who
    answers questions about a transcript, how big a model this machine should
    commit to, and whether to fetch the weights now while somebody is watching.

    Every answer may be left blank, and every one can be changed in Settings
    afterwards. A setup screen that must be completed before anything works is
    a worse first impression than one that can be skipped.

    Nothing is filled in for the two radio questions. The launcher renders
    questions and never decides one (ADR-015), and a preselected radio is a
    default, not an answer - "Save and start" would otherwise write
    `llm_provider = ollama` for somebody who chose nothing, which is what
    ADR-016 forbids and what TASK-089.25 came from. Both groups open on a skip
    that is visible ("Decide later", "Leave as it is") rather than on nothing
    at all, because a group with no filled circle reads as broken. The
    checkbox is the exception and stays a yes: a checkbox has no unanswered
    state, and this one writes no setting row - it only decides whether the
    download happens now or inside the first transcription. Robert ratified
    that on 2026-09-22 and chose the sentence above with it, so the intro
    names the two questions that have a skip instead of implying all four do.

    Returns the answers, or None when the person closed the window - which is
    "ask me next time", not "never".
    """
    import tkinter as tk

    win = tk.Toplevel(root)
    win.title(f"Set up {APP_NAME}")
    win.transient(root)
    win.grab_set()
    answers: dict = {}

    tk.Label(
        win,
        text=(
            "Two answers make speaker separation work, and two decide what this "
            "machine downloads. The provider and model questions can be left on "
            "their skip, which writes nothing; everything here can be changed "
            "later in Settings."
        ),
        wraplength=520, justify="left", anchor="w",
    ).grid(row=0, column=0, columnspan=2, sticky="we", padx=12, pady=(12, 8))

    tk.Label(win, text="Hugging Face token", anchor="w").grid(row=1, column=0, sticky="w", padx=12)
    token = tk.Entry(win, width=44, show="\u2022")
    token.grid(row=1, column=1, sticky="we", padx=12, pady=2)
    tk.Label(
        win,
        text="Speaker separation downloads a gated model. Accept its conditions at\n"
             "hf.co/pyannote/speaker-diarization-community-1 and paste a token here.",
        wraplength=520, justify="left", anchor="w", fg="#555",
    ).grid(row=2, column=0, columnspan=2, sticky="we", padx=12, pady=(0, 8))

    # Tk draws every button of a group with its "mixed" indicator while the
    # group's variable holds that button's -tristatevalue, and that option
    # defaults to the empty string - which is what a question nobody answered
    # holds here. Photographed on 2026-09-22 (Tk 8.6, Windows 11): with the
    # default, all seven circles open filled, so a dialog that fills nothing
    # in looks like one that filled everything in. A value no answer can take
    # turns it off; only the skip stays filled.
    not_an_answer = "no answer"

    # A row of its own, because the question is a sentence: in the label
    # column it would widen it from 137 to 255 px and push both radio groups
    # right, from x=12 to x=267 (measured on 2026-09-22).
    tk.Label(win, text="Who answers questions about a transcript?", anchor="w").grid(
        row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))
    provider = tk.StringVar(value="")
    providers = tk.Frame(win)
    providers.grid(row=4, column=0, columnspan=2, sticky="w", padx=12)
    for value, label in (("ollama", "Ollama, on this machine"), ("openrouter", "OpenRouter"),
                         ("openai", "OpenAI"), ("", "Decide later")):
        tk.Radiobutton(providers, text=label, variable=provider, value=value,
                       tristatevalue=not_an_answer).pack(side="left")

    tk.Label(win, text="Transcription model", anchor="w").grid(row=5, column=0, sticky="w", padx=12, pady=(8, 0))
    tier = tk.StringVar(value="")
    tiers = tk.Frame(win)
    tiers.grid(row=5, column=1, sticky="w", padx=12, pady=(8, 0))
    for value, label in (("turbo", "Turbo - fast"), ("max", "Maximaal - about four times slower"),
                         ("", "Leave as it is")):
        tk.Radiobutton(tiers, text=label, variable=tier, value=value,
                       tristatevalue=not_an_answer).pack(side="left")

    fetch = tk.BooleanVar(value=True)
    tk.Checkbutton(
        win, variable=fetch, anchor="w",
        text="Download the model weights now (about 1.6 GB; otherwise the first transcription waits for them)",
        wraplength=520, justify="left",
    ).grid(row=6, column=0, columnspan=2, sticky="we", padx=12, pady=(10, 4))

    def save() -> None:
        answers.update(
            hf_token=token.get().strip(),
            provider=provider.get(),
            tier=tier.get(),
            fetch_models=bool(fetch.get()),
        )
        win.destroy()

    row = tk.Frame(win)
    row.grid(row=7, column=0, columnspan=2, sticky="we", padx=12, pady=12)
    tk.Button(row, text="Save and start", command=save, default="active").pack(side="right")
    # "Ask me next time", and not "Skip for now": nothing is applied, the app
    # writes no stamp, and the sitting therefore returns at the next start -
    # which is what this function's docstring has said since 2026-09-19. The
    # other skip belongs to a single question and is the engine's, where it is
    # recorded as skipped and never asked by a start again (TASK-089.11).
    tk.Button(row, text="Ask me next time", command=win.destroy).pack(side="right", padx=8)

    win.columnconfigure(1, weight=1)
    root.wait_window(win)
    return answers or None


def run_window(layout: Layout, port: int, open_browser: bool, force_setup: bool = False,
               at_login: bool = False) -> int:
    import tkinter as tk
    from tkinter import scrolledtext

    events: "queue.Queue[tuple[str, str]]" = queue.Queue()
    launch = Launch(layout, port, open_browser, lambda state, text: events.put((state, text)))

    root = tk.Tk()
    root.title(f"{APP_NAME} {app_version(layout)}")
    root.geometry("560x320")
    status = tk.StringVar(value="Preparing...")
    tk.Label(root, textvariable=status, anchor="w", justify="left", wraplength=540).pack(fill="x", padx=10, pady=(10, 4))
    log = scrolledtext.ScrolledText(root, height=12, state="disabled", font=("TkFixedFont", 9))
    log.pack(fill="both", expand=True, padx=10)
    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=10, pady=10)
    open_button = tk.Button(buttons, text="Open MyScribe", state="disabled", command=lambda: webbrowser.open(launch.url()))
    open_button.pack(side="left")
    tk.Button(buttons, text="Open data folder", command=lambda: open_folder(layout.home)).pack(side="left", padx=8)

    def quit_app() -> None:
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

    def ask() -> dict | None:
        """Open the dialog on the Tk thread and wait for it, from the worker.

        Tk is not thread-safe, and `first_run` runs on a worker so the sync
        does not freeze the window; so the worker asks for the dialog and
        blocks on the queue until it closes.

        There is no timeout on that wait, because a person may sit at the
        questions for minutes. What there is instead is an answer on every
        path: a Quit between the schedule and the dispatch leaves the worker
        waiting, and that costs nothing - it is a daemon thread and the
        process ends with the mainloop.
        """
        answered: "queue.Queue[dict | None]" = queue.Queue()

        def sit() -> None:
            """On the Tk thread, and it always answers. An exception raised in
            here would otherwise go to Tk's own handler - stderr, which a
            windowed build has not got - and the worker would wait for ever
            with the app never started."""
            try:
                result = ask_setup(root, layout)
            except Exception:
                result = None
            answered.put(result)

        try:
            root.after(0, sit)
        except (tk.TclError, RuntimeError):
            # Quit during the sync: no window to ask in. Tk says so as a
            # TclError from a destroyed widget, and _tkinter as a RuntimeError
            # when the mainloop this thread would hand the call to is gone.
            return None
        return answered.get()

    def begin() -> None:
        """One worker thread for the whole sequence.

        The questions used to come first, so a token given here was in `.env`
        before anything read it. They now come after the sync, because the
        python that applies them is what the sync makes - and the runner
        children still inherit the environment the app was started with, which
        is started last either way (TASK-089.01).
        """
        threading.Thread(
            target=lambda: first_run(launch, ask, launch.report, force_setup, at_login),
            name="myscribe-launch",
            daemon=True,
        ).start()

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
    layout = Layout(home, payload)
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
        return run_headless(Launch(layout, args.port, not args.no_browser, console))
    return run_window(
        layout, args.port, not args.no_browser, force_setup=args.setup, at_login=args.at_login
    )


def smoke(layout: Layout, port: int) -> int:
    """Start the app exactly as a user's launch does, prove it serves, stop it."""
    app = AppProcess(layout, port)
    app.start()
    try:
        if not app.wait_ready():
            print(f"smoke: app did not answer {health_url(port)}", flush=True)
            print((layout.logs_dir / "app.log").read_text(encoding="utf-8", errors="replace")[-4000:])
            return 1
        with loopback_opener().open(f"http://{HOST}:{port}/", timeout=10) as response:
            ok = response.status == 200 and b"MyScribe" in response.read()
        print(f"smoke: /health ok, / {'ok' if ok else 'unexpected'}", flush=True)
        return 0 if ok else 1
    finally:
        app.stop()


if __name__ == "__main__":
    sys.exit(main())
