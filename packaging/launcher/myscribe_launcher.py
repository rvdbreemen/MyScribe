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
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Iterable

APP_NAME = "MyScribe"
HOST = "127.0.0.1"
DEFAULT_PORT = 4242
HOME_VARIABLE = "MYSCRIBE_HOME"
PAYLOAD_VARIABLE = "MYSCRIBE_PAYLOAD"
STAMP_NAME = ".myscribe-sync.json"
READY_TIMEOUT = 120.0
STOP_TIMEOUT = 15.0

IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


# --- where things live ------------------------------------------------------------


def home_dir(platform: str = sys.platform, environ: dict | None = None) -> Path:
    """The per-user MyScribe folder. Never the install directory: Program
    Files, a ``.app`` and an AppImage are read-only or replaced on update."""
    environ = os.environ if environ is None else environ
    if environ.get(HOME_VARIABLE):
        return Path(environ[HOME_VARIABLE])
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


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


def sync(layout: Layout, on_line: Callable[[str], None], base: dict | None = None) -> bool:
    """Sync the environment; write the stamp only when uv succeeded."""
    code = run_streaming(sync_command(layout), env=sync_environment(layout, base), cwd=layout.home, on_line=on_line)
    if code != 0:
        on_line(f"uv sync failed with exit code {code}")
        return False
    write_stamp(layout)
    return True


# --- one instance -----------------------------------------------------------------


def health_url(port: int) -> str:
    return f"http://{HOST}:{port}/health"


def running_instance(port: int, timeout: float = 1.0) -> bool:
    """Whether MyScribe already answers on ``port``."""
    try:
        with urllib.request.urlopen(health_url(port), timeout=timeout) as response:
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

    def url(self) -> str:
        return f"http://{HOST}:{self.port}/"

    def run(self) -> bool:
        if running_instance(self.port):
            self.report("done", f"MyScribe is already running at {self.url()}")
            if self.open_browser:
                webbrowser.open(self.url())
            return True
        if port_taken(self.port):
            self.report("error", f"Port {self.port} is used by another program; MyScribe cannot start.")
            return False
        prepare_home(self.layout)
        install_tools(self.layout)
        _release_frozen_dll_directory()
        if needs_sync(self.layout):
            self.report("status", "Installing the speech engine. The first start downloads about "
                                "3 GB on Windows and Linux and less on a Mac; later starts skip this.")
            if not sync(self.layout, lambda line: self.report("busy", line)):
                self.report("error", "Installing failed; the lines above say why. Check the "
                                     "internet connection and start MyScribe again.")
                return False
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

    def stop(self) -> None:
        if self.app is not None:
            self.app.stop()


def setup_stamp(layout: Layout) -> Path:
    """Written by `scribe.setup` when the questions have been answered."""
    return layout.data_dir / "setup.json"


def setup_needed(layout: Layout) -> bool:
    return not setup_stamp(layout).exists()


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


def run_setup(layout: Layout, answers: dict, on_line: Callable[[str], None]) -> bool:
    """Apply the answers, streaming what happens - a 1.6 GB download has to
    look like something happening rather than a window that stopped."""
    code = run_streaming(
        setup_command(layout, answers),
        env=app_environment(layout),
        cwd=layout.app_dir,
        on_line=on_line,
    )
    return code == 0


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


def ask_setup(root, layout: Layout) -> dict | None:
    """The four first-run questions, in one modal window.

    Four and no more. Each is something the app cannot work out for itself and
    would otherwise fail on later: the token speaker separation needs, who
    answers questions about a transcript, how big a model this machine should
    commit to, and whether to fetch the weights now while somebody is watching.

    Every answer may be left blank, and every one can be changed in Settings
    afterwards. A setup screen that must be completed before anything works is
    a worse first impression than one that can be skipped.

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
            "machine downloads. All of them can be changed later in Settings."
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

    tk.Label(win, text="Answers about a transcript", anchor="w").grid(row=3, column=0, sticky="w", padx=12)
    provider = tk.StringVar(value="ollama")
    providers = tk.Frame(win)
    providers.grid(row=3, column=1, sticky="w", padx=12)
    for value, label in (("ollama", "Ollama, on this machine"), ("openrouter", "OpenRouter"), ("openai", "OpenAI")):
        tk.Radiobutton(providers, text=label, variable=provider, value=value).pack(side="left")

    tk.Label(win, text="Transcription model", anchor="w").grid(row=4, column=0, sticky="w", padx=12, pady=(8, 0))
    tier = tk.StringVar(value="turbo")
    tiers = tk.Frame(win)
    tiers.grid(row=4, column=1, sticky="w", padx=12, pady=(8, 0))
    tk.Radiobutton(tiers, text="Turbo - fast", variable=tier, value="turbo").pack(side="left")
    tk.Radiobutton(tiers, text="Maximaal - about four times slower", variable=tier, value="max").pack(side="left")

    fetch = tk.BooleanVar(value=True)
    tk.Checkbutton(
        win, variable=fetch, anchor="w",
        text="Download the model weights now (about 1.6 GB; otherwise the first transcription waits for them)",
        wraplength=520, justify="left",
    ).grid(row=5, column=0, columnspan=2, sticky="we", padx=12, pady=(10, 4))

    def save() -> None:
        answers.update(
            hf_token=token.get().strip(),
            provider=provider.get(),
            tier=tier.get(),
            fetch_models=bool(fetch.get()),
        )
        win.destroy()

    row = tk.Frame(win)
    row.grid(row=6, column=0, columnspan=2, sticky="we", padx=12, pady=12)
    tk.Button(row, text="Save and start", command=save, default="active").pack(side="right")
    tk.Button(row, text="Skip for now", command=win.destroy).pack(side="right", padx=8)

    win.columnconfigure(1, weight=1)
    root.wait_window(win)
    return answers or None


def run_window(layout: Layout, port: int, open_browser: bool, force_setup: bool = False) -> int:
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

    def begin() -> None:
        """Ask first, then launch. The questions come before the app starts so
        a token given here is in `.env` before anything reads it - the runner
        children inherit the environment the app was started with."""
        if setup_needed(layout) or force_setup:
            answers = ask_setup(root, layout)
            if answers:
                status.set("Saving your answers...")
                threading.Thread(
                    target=lambda: (
                        run_setup(layout, answers, lambda line: events.put(("busy", line))),
                        launch.run(),
                    ),
                    name="myscribe-setup",
                    daemon=True,
                ).start()
                return
        threading.Thread(target=launch.run, name="myscribe-launch", daemon=True).start()

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


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    layout = Layout(args.home or home_dir(), args.payload or payload_dir())
    if args.version:
        print(app_version(layout))
        return 0

    def console(state: str, text: str) -> None:
        print(text, flush=True)

    if args.sync_only or args.doctor is not None or args.smoke:
        prepare_home(layout)
        install_tools(layout)
        _release_frozen_dll_directory()
        if needs_sync(layout) and not sync(layout, lambda line: print(line, flush=True)):
            return 1
        if args.doctor is not None:
            command = [str(layout.env_python), "-m", "scribe.doctor", *args.doctor]
            return subprocess.call(command, cwd=str(layout.app_dir), env=app_environment(layout))
        if args.smoke:
            return smoke(layout, args.port)
        return 0

    if args.headless:
        return run_headless(Launch(layout, args.port, not args.no_browser, console))
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return run_headless(Launch(layout, args.port, not args.no_browser, console))
    return run_window(layout, args.port, not args.no_browser, force_setup=args.setup)


def smoke(layout: Layout, port: int) -> int:
    """Start the app exactly as a user's launch does, prove it serves, stop it."""
    app = AppProcess(layout, port)
    app.start()
    try:
        if not app.wait_ready():
            print(f"smoke: app did not answer {health_url(port)}", flush=True)
            print((layout.logs_dir / "app.log").read_text(encoding="utf-8", errors="replace")[-4000:])
            return 1
        with urllib.request.urlopen(f"http://{HOST}:{port}/", timeout=10) as response:
            ok = response.status == 200 and b"MyScribe" in response.read()
        print(f"smoke: /health ok, / {'ok' if ok else 'unexpected'}", flush=True)
        return 0 if ok else 1
    finally:
        app.stop()


if __name__ == "__main__":
    sys.exit(main())
