"""Install MyScribe from a clone, with one command on every OS:

    python install.py                 the environment, the questions, the proof
    python install.py --start         the same, then start the app
    python install.py --check         only the proof report

The clone's door, beside the release's launcher (ADR-011). It shares the
launcher's sequence and none of its code, decided by Robert on 2026-09-20
(ADR-015, G2): tools, sync, questions, proof, start, with the plumbing both
doors need - the lock digest, the stamp, the sync decision - written here a
second time and held to the launcher's by one contract test
(tests/test_install.py). What the launcher does not do at all, fetching the
tools and checking their sha256, is shared with packaging/build_payload.py.

It runs on whatever Python 3.9 or newer a person already has, before the
environment exists, so it is standard library only. In order:

1. Refuses a Python older than 3.9 and a machine outside the three the lock
   resolves for (pyproject.toml, [tool.uv] environments), in one sentence,
   before anything is downloaded.
2. When a sync is due: refuses it under a MyScribe that runs from this very
   checkout - `/health` says what it serves (G5) - and when the volume has no
   room for the environment.
3. Fetches the pinned uv of packaging/tools.json into .tools/bin, verified
   against its sha256, and never uses the uv on PATH: an older uv rewrites
   uv.lock wholesale. On Windows and Linux the pinned ffmpeg and ffprobe too,
   when none answers on PATH.
4. Runs `uv sync --frozen` into .venv with the dev group kept (`--no-dev`
   drops it, as a release does) and records the lock's digest in
   .venv/.myscribe-sync.json: a later run reads it and syncs nothing, and
   scripts/start.* compare it after every pull.
5. Hands this terminal to `python -m scribe.setup`, which asks only what is
   still open (ADR-015), runs `--prove`, and prints the start command.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
BUILD_PAYLOAD = REPO / "packaging" / "build_payload.py"
STAMP_NAME = ".myscribe-sync.json"
TOOLS_STAMP = ".tools.json"
HOST = "127.0.0.1"
DEFAULT_PORT = 4242
GB = 2 ** 30
MIN_PYTHON = (3, 9)
IS_WINDOWS = sys.platform == "win32"

# The three environments the lock resolves for (pyproject.toml, [tool.uv]),
# keyed by (sys.platform, platform.machine()) and named the way
# packaging/tools.json names its platforms. Anything else the lock refuses at
# lock time, and this file refuses before it downloads a byte.
ENVIRONMENTS = {
    ("win32", "AMD64"): "windows-x64",
    ("darwin", "arm64"): "macos-arm64",
    ("linux", "x86_64"): "linux-x64",
}


def say(step: str, text: str) -> None:
    """One line per thing done, the step named first so a transcript reads as
    the sequence it is."""
    print(f"{step:<7}{text}", flush=True)


# --- the guard: before anything is loaded or fetched ------------------------------


def guard(version: tuple | None = None, platform_name: str | None = None,
          machine: str | None = None) -> str | None:
    """One sentence when this Python or this machine cannot install MyScribe,
    None when it can. Asked first, before build_payload is even loaded: a
    machine outside the three environments never downloads anything."""
    version = tuple(sys.version_info[:3]) if version is None else tuple(version)
    platform_name = sys.platform if platform_name is None else platform_name
    machine = platform.machine() if machine is None else machine
    if tuple(version[:2]) < MIN_PYTHON:
        return (f"install.py needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer to run, and this is "
                f"{'.'.join(str(n) for n in version[:3])}; uv then makes the Python the app itself needs.")
    if (platform_name, machine) not in ENVIRONMENTS:
        built_for = ", ".join(f"{p} {m}" for p, m in ENVIRONMENTS)
        return (f"MyScribe is built for {built_for}, and this machine is {platform_name} {machine} "
                f"(pyproject.toml, [tool.uv] environments).")
    return None


def platform_key(platform_name: str | None = None, machine: str | None = None) -> str:
    """The packaging/tools.json name of this machine's platform."""
    platform_name = sys.platform if platform_name is None else platform_name
    machine = platform.machine() if machine is None else machine
    return ENVIRONMENTS[(platform_name, machine)]


# --- the layout ------------------------------------------------------------------


class Layout:
    """Every path install.py touches, from one root: the clone."""

    def __init__(self, repo: Path, windows: bool = IS_WINDOWS):
        self.repo = Path(repo).absolute()
        self.windows = windows

    lock_file = property(lambda self: self.repo / "uv.lock")
    env_dir = property(lambda self: self.repo / ".venv")
    env_file = property(lambda self: self.repo / ".env")
    tools_dir = property(lambda self: self.repo / ".tools" / "bin")
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
    init = layout.repo / "scribe" / "__init__.py"
    try:
        match = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
    except OSError:
        return "unknown"
    return match.group(1) if match else "unknown"


# --- the sync decision, the launcher's rule written a second time (ADR-015, G2) ---


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


def sync_command(layout: Layout, dev: bool = True) -> list[str]:
    """`uv sync --frozen` into the clone's own .venv. The dev group stays
    unless asked otherwise: a clone is where the tests are run."""
    command = uv_command(layout) + ["sync", "--frozen", "--project", str(layout.repo)]
    if not dev:
        command.append("--no-dev")
    return command


def child_environment(base: dict | None = None, data_dir: Path | None = None) -> dict:
    """What every child gets: this process's environment, less what would
    point a python at another environment, plus nothing a flag did not ask
    for. No cache, Python or bytecode directory: a clone's belong to uv's and
    python's defaults. `SCRIBE_DATA_DIR` only for `--data-dir`, so the clone's
    own `.env`, which every command reads first, keeps the last word.
    """
    env = dict(os.environ if base is None else base)
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    if data_dir is not None:
        env["SCRIBE_DATA_DIR"] = str(data_dir)
    return env


def sync_environment(base: dict | None = None, data_dir: Path | None = None) -> dict:
    env = child_environment(base, data_dir)
    # A user's own uv.toml must not change what gets installed.
    env["UV_NO_CONFIG"] = "1"
    return env


# --- the tools, shared with packaging/build_payload.py -----------------------------

_payload = None


def payload_module():
    """packaging/build_payload.py, loaded from its file and kept.

    By path and not `from packaging import build_payload`: on a machine with
    the PyPI `packaging` library installed - any machine with pip - that
    import resolves to the library and not to this repository's directory,
    and says nothing.
    """
    global _payload
    if _payload is None:
        spec = importlib.util.spec_from_file_location("myscribe_build_payload", str(BUILD_PAYLOAD))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _payload = module
    return _payload


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _in_place(spec: dict, layout: Layout) -> bool:
    """Every member of ``spec`` that lands in the tools directory is there.
    (The ffmpeg licence climbs one level to .tools/licenses and is not one.)"""
    return all((layout.tools_dir / target).exists()
               for target in spec["members"].values() if not target.startswith(".."))


def install_tools(layout: Layout, key: str, which=shutil.which) -> None:
    """The pinned uv into .tools/bin, and ffmpeg with ffprobe where none answers.

    Each download is fetched and checked against its sha256 by
    build_payload.fetch, and unpacked by build_payload.extract - the same two
    functions that build a release payload (ADR-011). A tool already in place
    from the same pin is left alone; a pin that moved in tools.json is
    fetched again, which is what the stamp beside them is for.

    macOS has no pinned download - the release builds its own ffmpeg - so
    there the line is the command. MyScribe does not run brew: ADR-017 allows
    only a pinned, sha256-verified artifact, and a package manager is neither.
    """
    payload = payload_module()
    tools = payload.TOOLS
    stamp_path = layout.tools_dir / TOOLS_STAMP
    fetched = _read_json(stamp_path)

    uv = tools["uv"]["platforms"][key]
    if fetched.get("uv") == uv["sha256"] and _in_place(uv, layout):
        say("tools", f"uv {tools['uv']['version']} already in {layout.tools_dir}")
    else:
        say("tools", f"fetching uv {tools['uv']['version']}, pinned in packaging/tools.json")
        payload.extract(payload.fetch(uv["url"], uv["sha256"]), uv["url"], uv["members"], layout.tools_dir)
        fetched["uv"] = uv["sha256"]
        stamp_path.write_text(json.dumps(fetched), encoding="utf-8")

    ff = tools["ffmpeg"]["platforms"].get(key) or {}
    found = [which(name) for name in ("ffmpeg", "ffprobe")]
    if "url" in ff and fetched.get("ffmpeg") == ff["sha256"] and _in_place(ff, layout):
        say("tools", f"ffmpeg {tools['ffmpeg']['version']} already in {layout.tools_dir}")
    elif all(found):
        say("tools", f"ffmpeg and ffprobe found on PATH: {found[0]}")
    elif "url" in ff:
        say("tools", f"no ffmpeg on PATH; fetching ffmpeg {tools['ffmpeg']['version']}, pinned in packaging/tools.json")
        payload.extract(payload.fetch(ff["url"], ff["sha256"]), ff["url"], ff["members"], layout.tools_dir)
        fetched["ffmpeg"] = ff["sha256"]
        stamp_path.write_text(json.dumps(fetched), encoding="utf-8")
    else:
        say("tools", "no ffmpeg on PATH and no pinned download for this platform: brew install ffmpeg")


def uv_says(layout: Layout, env: dict) -> str:
    """The first line of `uv --version` from the uv in .tools/bin, or ''."""
    try:
        done = subprocess.run(uv_command(layout) + ["--version"], capture_output=True, text=True,
                              timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = (done.stdout or done.stderr or "").strip().splitlines()
    return lines[0] if lines else ""


# --- room for the environment ------------------------------------------------------


def footprint_gb(layout: Layout, platform_name: str) -> float | None:
    """The unpacked environment for this platform out of scribe/footprint.json,
    or None where it says null: nobody has measured it, and the file says so
    rather than lend another platform's figure."""
    paper = _read_json(layout.repo / "scribe" / "footprint.json")
    entry = paper.get("environment", {}).get(platform_name)
    value = entry.get("unpacked_gb") if isinstance(entry, dict) else None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def disk_floor_gb(layout: Layout) -> int:
    """``scribe.doctor.DISK_FLOOR_GB`` read as text; 0 when it cannot be read."""
    try:
        source = (layout.repo / "scribe" / "doctor.py").read_text(encoding="utf-8")
    except OSError:
        return 0
    match = re.search(r"^DISK_FLOOR_GB\s*=\s*(\d+)\s*$", source, re.M)
    return int(match.group(1)) if match else 0


def free_gb(path: Path) -> float | None:
    try:
        return shutil.disk_usage(path).free / GB
    except OSError:
        return None


def enough_disk(layout: Layout, platform_name: str) -> bool:
    """False, with both numbers, when the clone's volume cannot hold the
    environment (M3). Measured where .venv lands, before the download. A
    figure nobody measured does not refuse: "we do not know" must not become
    "nothing may be installed here"."""
    unpacked = footprint_gb(layout, platform_name)
    floor = disk_floor_gb(layout)
    free = free_gb(layout.repo)
    if unpacked is None:
        say("disk", f"the environment's size on {platform_name} has not been measured (scribe/footprint.json); "
                    f"{free:.1f} GB free at {layout.repo}, checked against nothing" if free is not None else
                    f"the environment's size on {platform_name} has not been measured, and the free space "
                    f"at {layout.repo} could not be read")
        return True
    needed = unpacked + floor
    if free is None:
        say("disk", f"about {needed:.1f} GB needed; the free space at {layout.repo} could not be read")
        return True
    if free >= needed:
        say("disk", f"about {needed:.1f} GB needed ({unpacked} GB for the environment, an estimate, plus the "
                    f"{floor} GB MyScribe keeps free for recordings); {free:.1f} GB free at {layout.repo}")
        return True
    say("disk", f"There is not enough room: MyScribe needs about {needed:.1f} GB here ({unpacked} GB for the "
                f"environment plus the {floor} GB it keeps free for recordings), and {layout.repo} has "
                f"{free:.1f} GB free. Nothing was downloaded.")
    return False


# --- who is serving, and from where (G5) ------------------------------------------


def health(port: int, timeout: float = 1.0) -> dict | None:
    """GET /health on the loopback, or None when nothing answers.

    Past whatever proxy this machine has configured: with HTTP_PROXY set and
    no NO_PROXY, a loopback request goes to the proxy and times out, and the
    launcher's own check answered "no app" about an app that was serving
    (measured for TASK-089.05).
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{HOST}:{port}/health", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def same_tree(a: str, b: Path) -> bool:
    """One directory reached by two spellings is one directory."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(str(b)))


def sync_blocker(answer: dict | None, port: int, repo: Path) -> str | None:
    """The sentence that refuses the sync, or None when it may go ahead.

    Decided by Robert on 2026-09-20 (G5): `/health` says what it serves. A
    MyScribe running from this checkout has .venv's interpreter and extension
    modules mapped, and on Windows a sync cannot delete a mapped file - every
    unlink answers WinError 5 and a package directory is left half replaced
    (measured for TASK-089.17, criterion 8). One running from another tree
    holds nothing of ours, and nothing answering blocks nothing. An answer
    without `app_dir` is an older MyScribe that may well be this checkout:
    doubt, and doubt refuses.
    """
    if answer is None:
        return None
    app_dir = answer.get("app_dir")
    if not isinstance(app_dir, str) or not app_dir.strip():
        return (f"a MyScribe answers on port {port} without saying where it runs from (an older "
                f"version), so it may be this checkout: stop it first, or use --no-sync.")
    if same_tree(app_dir, repo):
        return f"MyScribe is running from this checkout on port {port}: stop it first, or use --no-sync."
    return None


def blind_spot(ports: list[int]) -> str:
    """What the probe cannot see, said rather than implied."""
    asked = ", ".join(str(port) for port in ports)
    return (f"asked /health on port {asked}; a MyScribe on any other port is not seen - "
            f"this repository's own `--port 4299` is such a case.")


# --- the children ----------------------------------------------------------------


def run(command: list[str], env: dict, cwd: Path, stdin_text: str | None = None) -> int:
    """Run ``command`` on this terminal, or with ``stdin_text`` as its whole
    stdin (`scribe.setup --apply-stdin` reads until EOF). The exit code."""
    try:
        if stdin_text is None:
            return subprocess.call(command, cwd=str(cwd), env=env)
        return subprocess.run(command, cwd=str(cwd), env=env, input=stdin_text,
                              text=True, encoding="utf-8").returncode
    except OSError as error:
        raise SystemExit(f"could not run {command[0]}: {error}") from None


def python_command(layout: Layout) -> list[str]:
    return [str(layout.env_python)]


def setup_command(layout: Layout, apply_stdin: bool = False) -> list[str]:
    """`python -m scribe.setup`: bare, it asks on this terminal; with
    `--apply-stdin` it reads one JSON document of answers. No answer is ever
    on this list (ADR-015)."""
    return python_command(layout) + ["-m", "scribe.setup"] + (["--apply-stdin"] if apply_stdin else [])


def prove_command(layout: Layout, port: int) -> list[str]:
    return python_command(layout) + ["-m", "scribe.setup", "--prove", "--port", str(port)]


def start_command(layout: Layout, port: int) -> list[str]:
    return python_command(layout) + ["-m", "scribe", "--port", str(port)]


def at_a_terminal(stream=None) -> bool:
    """Is somebody at a terminal, or is this a child with no stdin?

    The same rule as `scribe/setup.py`'s `at_a_terminal`, so the two doors
    agree: `isatty()` alone is not the question on Windows, where NUL is a
    character device and answers True; a console handle answers
    `GetConsoleMode` and NUL does not. Git Bash's mintty gives a native
    Windows Python no console handle either, and that is the case the line
    below names.
    """
    stream = stream if stream is not None else sys.stdin
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform != "win32":
        return True
    try:
        fileno = stream.fileno()
    except Exception:  # noqa: BLE001 - a stream without one is a test's, and honest
        return True
    try:
        import ctypes
        import msvcrt

        mode = ctypes.c_ulong()
        handle = msvcrt.get_osfhandle(fileno)
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:  # noqa: BLE001 - no verdict is the quiet path, not the hanging one
        return False


NO_TERMINAL = ("nobody is at a terminal here, so nothing is asked and the questions stay open. To answer "
               "them, run this from PowerShell or a Terminal - in Git Bash, `winpty python install.py` - "
               "or pass --answers FILE.")


def sitting(layout: Layout, env: dict, answers_text: str | None, unattended: bool) -> int:
    """The questions: this terminal handed to the engine, or a document on its
    stdin. Nothing here decides an answer or writes one (ADR-015)."""
    if answers_text is not None:
        say("setup", "applying the answers in the file (scribe.setup --apply-stdin)")
        return run(setup_command(layout, apply_stdin=True), env, layout.repo, stdin_text=answers_text)
    if unattended:
        say("setup", "--non-interactive: nothing is asked; `python -m scribe.setup` asks what is still open")
        return run(setup_command(layout, apply_stdin=True), env, layout.repo, stdin_text="{}")
    if not at_a_terminal():
        say("setup", NO_TERMINAL)
        return run(setup_command(layout, apply_stdin=True), env, layout.repo, stdin_text="{}")
    say("setup", "handing this terminal to " + " ".join(setup_command(layout)))
    return run(setup_command(layout), env, layout.repo)


# --- .env -------------------------------------------------------------------------


def set_env_line(path: Path, name: str, value: str) -> None:
    """Leave exactly one uncommented ``name=value`` line in ``path``."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() != name]
    kept.append(f"{name}={value}")
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def seed_env_file(layout: Layout, data_dir: Path | None) -> None:
    """`.env` from `.env.example` when there is none (0600 on POSIX: it will
    hold keys), and the `--data-dir` line, so the choice outlives this run."""
    example = layout.repo / ".env.example"
    if not layout.env_file.exists() and example.exists():
        layout.env_file.write_bytes(example.read_bytes())
        if os.name == "posix":
            os.chmod(layout.env_file, 0o600)
        say("env", f"{layout.env_file} seeded from .env.example")
    if data_dir is not None:
        set_env_line(layout.env_file, "SCRIBE_DATA_DIR", str(data_dir))
        say("env", f"SCRIBE_DATA_DIR={data_dir} written to {layout.env_file}")


# --- the command -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python install.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--start", action="store_true", help="start MyScribe once the proof has run")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"the port a running MyScribe is asked on besides {DEFAULT_PORT}, and the one --start uses",
    )
    parser.add_argument(
        "--data-dir", type=Path, metavar="DIR",
        help="where the library lives - recordings, database, logs, model weights; the default is data/ "
             "inside the clone. Written to .env, so it holds for every later start. A library inside "
             "the clone is deleted by `git clean -fdx`; put it outside to keep it.",
    )
    parser.add_argument("--no-dev", action="store_true",
                        help="leave the dev group (pytest and friends) out of .venv, as a release does")
    parser.add_argument("--no-sync", action="store_true", help="run no uv: use .venv as it is")
    parser.add_argument("--non-interactive", action="store_true",
                        help="ask nothing; the questions stay open for `python -m scribe.setup`")
    parser.add_argument(
        "--answers", type=Path, metavar="FILE",
        help="one JSON document of answers for scribe.setup, in the shape `--plan` describes, "
             "handed to it on stdin",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="only the proof report (`scribe.setup --prove`): it measures this machine, creates and "
             "removes one scratch directory, reads the library read-only, and installs nothing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    refusal = guard()
    if refusal:
        print(refusal)
        return 1
    key = platform_key()
    layout = Layout(REPO)
    data_dir = args.data_dir.absolute() if args.data_dir else None
    env = child_environment(data_dir=data_dir)

    if args.check:
        if not layout.env_python.exists():
            print(f"no environment yet at {layout.env_dir}: run `python install.py` first")
            return 1
        return run(prove_command(layout, args.port), env, layout.repo)

    answers_text = None
    if args.answers:
        try:
            answers_text = args.answers.read_text(encoding="utf-8")
            if not isinstance(json.loads(answers_text), dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as error:
            print(f"--answers {args.answers} is not one JSON document of answers: {error}")
            return 1

    print(f"MyScribe {app_version(layout)} from {layout.repo}")
    print(f"python {'.'.join(str(n) for n in sys.version_info[:3])} on {sys.platform} {platform.machine()} ({key})")

    syncing = not args.no_sync and needs_sync(layout)
    if syncing:
        ports = [DEFAULT_PORT] + ([args.port] if args.port != DEFAULT_PORT else [])
        for port in ports:
            blocker = sync_blocker(health(port), port, layout.repo)
            if blocker:
                say("health", blocker)
                return 1
        say("health", "nothing blocks a sync: " + blind_spot(ports))
        if not enough_disk(layout, sys.platform):
            return 1
    elif args.no_sync:
        say("sync", "--no-sync: .venv is used as it is")
    else:
        say("sync", f"{layout.env_dir} was synced from this uv.lock ({lock_digest(layout)[:12]}); nothing to sync")

    install_tools(layout, key)
    if syncing:
        pinned = payload_module().TOOLS["uv"]["version"]
        said = uv_says(layout, sync_environment(env)) or "nothing it could run"
        say("tools", f"uv {pinned} pinned; `{layout.uv} --version` says: {said}")
        say("sync", f"uv sync --frozen into {layout.env_dir}, the dev group {'left out' if args.no_dev else 'kept'}")
        code = run(sync_command(layout, dev=not args.no_dev), sync_environment(env), layout.repo)
        if code != 0:
            say("sync", f"uv sync failed with exit code {code}; the lines above say why.")
            return code
        write_stamp(layout)
        say("stamp", f"{layout.stamp_file} holds the lock's digest; later runs and scripts/start.* read it")
    if not layout.env_python.exists():
        say("sync", f"no python at {layout.env_python}: run `python install.py` without --no-sync")
        return 1

    seed_env_file(layout, data_dir)
    code = sitting(layout, env, answers_text, args.non_interactive)
    if code != 0:
        say("setup", f"scribe.setup exited with {code}; the lines above say why. Settings has the same questions.")

    say("prove", "measuring what this machine can do (scribe.setup --prove)")
    code = run(prove_command(layout, args.port), env, layout.repo)
    say("prove", f"the report above is what this machine measured (exit code {code}); it changed nothing")

    if args.start:
        if health(args.port) is not None:
            say("start", f"MyScribe already answers at http://{HOST}:{args.port}/")
            return 0
        say("start", " ".join(start_command(layout, args.port)))
        return run(start_command(layout, args.port), env, layout.repo)
    say("start", "MyScribe is installed. Start it with:")
    print(f"        {' '.join(start_command(layout, args.port))}")
    print("        or `python install.py --start`; scripts/start.ps1 and scripts/start.sh do the same "
          "and check this stamp")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(130)
