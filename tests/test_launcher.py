"""The launcher (ADR-011): paths, the sync decision, the child environment,
one instance, and Quit reaching the runner children - all without network.

The launcher is stdlib-only and lives outside the `scribe` package, so it is
loaded from its file.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import socket
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

LAUNCHER_PATH = Path(__file__).resolve().parent.parent / "packaging" / "launcher" / "myscribe_launcher.py"
_spec = importlib.util.spec_from_file_location("myscribe_launcher", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def layout(tmp_path):
    payload = tmp_path / "payload"
    (payload / "app" / "scribe").mkdir(parents=True)
    (payload / "bin").mkdir()
    (payload / "app" / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (payload / "app" / "scribe" / "__init__.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    (payload / "app" / ".env.example").write_text("HF_TOKEN=\n", encoding="utf-8")
    return launcher.Layout(tmp_path / "home", payload)


def _fake_env_python(layout) -> None:
    layout.env_python.parent.mkdir(parents=True, exist_ok=True)
    layout.env_python.write_text("", encoding="utf-8")


# --- where things live ------------------------------------------------------------


def test_the_home_is_per_user_on_every_platform(tmp_path):
    assert launcher.home_dir("win32", {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}) == Path(
        r"C:\Users\x\AppData\Local") / "MyScribe"
    assert launcher.home_dir("darwin", {}) == Path.home() / "Library" / "Application Support" / "MyScribe"
    assert launcher.home_dir("linux", {"XDG_DATA_HOME": "/x/share"}) == Path("/x/share/MyScribe")
    assert launcher.home_dir("linux", {}) == Path.home() / ".local" / "share" / "MyScribe"
    assert launcher.home_dir("linux", {"MYSCRIBE_HOME": str(tmp_path)}) == tmp_path


def test_the_environment_python_is_where_uv_puts_it(tmp_path):
    assert launcher.Layout(tmp_path, tmp_path, windows=True).env_python == tmp_path / "env" / "Scripts" / "python.exe"
    assert launcher.Layout(tmp_path, tmp_path, windows=False).env_python == tmp_path / "env" / "bin" / "python"


def test_relative_roots_become_absolute(tmp_path, monkeypatch):
    """The sync runs with the home as its working directory, so a relative
    `--payload` would point uv's path at nothing (found on the first real run)."""
    monkeypatch.chdir(tmp_path)
    layout = launcher.Layout(Path("home"), Path("build/payload"))

    assert layout.home == tmp_path / "home"
    assert layout.payload == tmp_path / "build" / "payload"
    assert Path(launcher.sync_command(layout)[0]) == tmp_path / "home" / "bin" / Path(layout.uv).name


def test_the_version_is_read_from_the_shipped_source(layout):
    assert launcher.app_version(layout) == "9.8.7"


def test_first_run_copies_the_env_template_and_never_overwrites_it(layout):
    launcher.prepare_home(layout)
    assert layout.env_file.read_text(encoding="utf-8") == "HF_TOKEN=\n"
    assert layout.data_dir.is_dir() and layout.logs_dir.is_dir()

    layout.env_file.write_text("HF_TOKEN=hf_mine\n", encoding="utf-8")
    launcher.prepare_home(layout)
    assert layout.env_file.read_text(encoding="utf-8") == "HF_TOKEN=hf_mine\n"


# --- the sync decision ------------------------------------------------------------


def test_a_missing_environment_needs_a_sync(layout):
    assert launcher.needs_sync(layout) is True


def test_an_environment_synced_from_this_lock_does_not(layout):
    _fake_env_python(layout)
    launcher.write_stamp(layout)
    assert launcher.needs_sync(layout) is False


def test_a_changed_lock_needs_a_sync_again(layout):
    _fake_env_python(layout)
    launcher.write_stamp(layout)
    layout.lock_file.write_text("version = 1\n# torch bumped\n", encoding="utf-8")
    assert launcher.needs_sync(layout) is True


def test_a_stamp_without_an_environment_needs_a_sync(layout):
    _fake_env_python(layout)
    launcher.write_stamp(layout)
    layout.env_python.unlink()
    assert launcher.needs_sync(layout) is True


def test_the_sync_is_frozen_to_the_lock_and_kept_in_the_home(layout):
    command = launcher.sync_command(layout)
    env = launcher.sync_environment(layout, {"PATH": "/usr/bin"})

    assert command[0] == str(layout.uv)
    assert command[1] == "sync" and "--frozen" in command and "--no-dev" in command
    assert command[command.index("--project") + 1] == str(layout.app_dir)
    assert env["UV_PROJECT_ENVIRONMENT"] == str(layout.env_dir)
    assert env["UV_PYTHON_INSTALL_DIR"] == str(layout.python_dir)
    assert env["UV_CACHE_DIR"] == str(layout.cache_dir)
    assert env["UV_NO_CONFIG"] == "1"


def _fake_uv(tmp_path, exit_code: int) -> list[str]:
    script = tmp_path / "fake_uv.py"
    script.write_text(f"print('Downloading torch'); print('Installed 3 packages'); raise SystemExit({exit_code})\n")
    return [sys.executable, str(script)]


def test_a_successful_sync_is_stamped(layout, tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 0))
    launcher.prepare_home(layout)
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is True
    assert lines == ["Downloading torch", "Installed 3 packages"]
    _fake_env_python(layout)
    assert launcher.needs_sync(layout) is False


def _no_proxy_configured(monkeypatch) -> None:
    """The sync failure line names a proxy when one is configured (TASK-089.05),
    so a test that pins that line exactly has to say which machine it is on."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def test_a_failed_sync_is_not_stamped(layout, tmp_path, monkeypatch):
    _no_proxy_configured(monkeypatch)
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 2))
    launcher.prepare_home(layout)
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is False
    assert lines[-1] == "uv sync failed with exit code 2"
    assert not layout.stamp_file.exists()


def test_a_failed_sync_names_a_proxy_that_is_configured(layout, tmp_path, monkeypatch):
    """Somebody behind a corporate proxy is otherwise told an exit code and
    nothing else. The host, never the `user:password@` a proxy URL can carry."""
    _no_proxy_configured(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.corp:3128")
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 2))
    launcher.prepare_home(layout)
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is False
    assert lines[-1] == (
        "uv sync failed with exit code 2; a proxy is configured at proxy.corp:3128, "
        "and the download did not get through it"
    )
    assert "secret" not in lines[-1]


def test_a_failed_sync_names_the_proxy_the_child_was_given(layout, tmp_path, monkeypatch):
    """`sync` builds the environment uv runs in, and the sentence has to be
    about that one: a caller that hands the child a different proxy would
    otherwise be told about the launcher's own."""
    _no_proxy_configured(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://launchers-own.corp:3128")
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 2))
    launcher.prepare_home(layout)
    lines: list[str] = []
    child = {**os.environ, "HTTPS_PROXY": "http://the-childs.corp:3128"}

    assert launcher.sync(layout, lines.append, child) is False
    assert lines[-1] == (
        "uv sync failed with exit code 2; a proxy is configured at the-childs.corp:3128, "
        "and the download did not get through it"
    )


# --- the child environment --------------------------------------------------------


def test_the_app_gets_its_home_the_bundled_tools_and_a_writable_pycache(layout):
    env = launcher.app_environment(layout, {"PATH": "/usr/bin", "PYTHONHOME": "/frozen"})

    assert env["SCRIBE_DATA_DIR"] == str(layout.data_dir)
    assert env["SCRIBE_ENV_FILE"] == str(layout.env_file)
    assert env["PATH"].split(os.pathsep)[0] == str(layout.tools_dir)
    assert env["PYTHONPYCACHEPREFIX"] == str(layout.pycache_dir)
    assert env["PYTHONPATH"] == str(layout.app_dir)
    assert "PYTHONHOME" not in env


def test_what_pyinstaller_leaks_does_not_reach_the_children():
    env = launcher.child_environment({
        "LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": "/opt/lib",
        "TCL_LIBRARY": "/tmp/_MEI123/tcl", "TK_LIBRARY": "/tmp/_MEI123/tk", "HOME": "/h",
    })
    assert env["LD_LIBRARY_PATH"] == "/opt/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "TCL_LIBRARY" not in env and "TK_LIBRARY" not in env
    assert env["HOME"] == "/h"


# --- one instance -----------------------------------------------------------------


def _serve(port: int, body: dict) -> http.server.HTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_running_myscribe_is_recognised_by_its_health_answer():
    port = _free_port()
    server = _serve(port, {"ok": True, "version": "0.1.0"})
    try:
        assert launcher.running_instance(port) is True
        assert launcher.port_taken(port) is True
    finally:
        server.shutdown()
        server.server_close()


def test_another_program_on_the_port_is_not_mistaken_for_myscribe():
    port = _free_port()
    server = _serve(port, {"status": "something else"})
    try:
        assert launcher.running_instance(port) is False
        assert launcher.port_taken(port) is True
    finally:
        server.shutdown()
        server.server_close()


def test_a_free_port_is_neither_running_nor_taken():
    port = _free_port()
    assert launcher.running_instance(port) is False
    assert launcher.port_taken(port) is False


def test_a_second_launch_opens_the_browser_instead_of_starting_again(layout, monkeypatch):
    port = _free_port()
    server = _serve(port, {"ok": True})
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    reports: list[tuple[str, str]] = []
    try:
        launch = launcher.Launch(layout, port, True, lambda s, t: reports.append((s, t)))
        assert launch.run() is True
    finally:
        server.shutdown()
        server.server_close()

    assert opened == [f"http://127.0.0.1:{port}/"]
    assert launch.app is None
    assert reports[-1][0] == "done"
    assert not layout.home.exists()  # nothing was prepared, synced or started


# --- the app process: Quit reaches the runner children ----------------------------


FAKE_APP = textwrap.dedent('''
    """Stands in for `python -m scribe`: serves /health and, like the
    supervisor, starts a long-lived child the way a runner is started."""
    import http.server, json, os, subprocess, sys, pathlib
    port = int(sys.argv[sys.argv.index("--port") + 1])
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    pathlib.Path(os.environ["SCRIBE_DATA_DIR"], "child.pid").write_text(str(child.pid))
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        def log_message(self, *a): pass
    http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
''')


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import subprocess

        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0) until its parent reaps it; it is not running.
    try:
        with open(f"/proc/{pid}/status") as status:
            return "zombie" not in status.read()
    except OSError:
        import subprocess

        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
        return bool(state.strip()) and not state.strip().startswith("Z")


def test_quit_stops_the_server_and_the_runner_it_started(layout, monkeypatch):
    (layout.app_dir / "scribe" / "__main__.py").write_text(FAKE_APP, encoding="utf-8")
    monkeypatch.setattr(launcher.Layout, "env_python", property(lambda self: Path(sys.executable)))
    launcher.prepare_home(layout)
    port = _free_port()
    app = launcher.AppProcess(layout, port)

    app.start()
    try:
        # Generous on purpose: what this test proves is that Quit stops the
        # server *and* the runner it started, not how fast a cold machine can
        # start one. Thirty seconds was enough on a laptop and not on a hosted
        # macOS runner, where the first import of the app is slower.
        assert app.wait_ready(timeout=180) is True
        pid_file = layout.data_dir / "child.pid"
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        child = int(pid_file.read_text())
        assert _pid_alive(child)
    finally:
        app.stop(timeout=10)

    assert not app.alive()
    deadline = time.monotonic() + 10
    while _pid_alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _pid_alive(child)
    assert launcher.running_instance(port) is False


# --- the bundled tools run from the home ------------------------------------------


def _tool(layout, name: str, body: bytes) -> None:
    (layout.bin_dir / name).write_bytes(body)


def test_the_tools_are_installed_into_the_home_and_the_app_uses_them(layout):
    _tool(layout, "ffmpeg", b"ffmpeg v1")
    launcher.prepare_home(layout)

    launcher.install_tools(layout)

    assert (layout.tools_dir / "ffmpeg").read_bytes() == b"ffmpeg v1"
    assert os.access(layout.tools_dir / "ffmpeg", os.X_OK)
    assert launcher.app_environment(layout, {"PATH": "/usr/bin"})["PATH"].split(os.pathsep)[0] == str(layout.tools_dir)
    assert Path(launcher.sync_command(layout)[0]).parent == layout.tools_dir


def test_an_update_replaces_the_tools_and_an_unchanged_payload_leaves_them(layout):
    _tool(layout, "ffmpeg", b"ffmpeg v1")
    launcher.prepare_home(layout)
    launcher.install_tools(layout)
    installed = layout.tools_dir / "ffmpeg"
    os.utime(installed, (1, 1))

    launcher.install_tools(layout)
    assert installed.stat().st_mtime == 1  # same payload: not rewritten

    _tool(layout, "ffmpeg", b"ffmpeg v2")
    launcher.install_tools(layout)
    assert installed.read_bytes() == b"ffmpeg v2"


@pytest.mark.skipif(sys.platform != "darwin", reason="the quarantine attribute is macOS's")
def test_an_installed_tool_does_not_inherit_the_download_s_quarantine(layout):
    """A copy keeps com.apple.quarantine on macOS (measured: shutil.copyfile
    and copy2 both carry it), and Gatekeeper refuses an ad-hoc-signed
    quarantined ffmpeg; the launcher writes fresh files instead."""
    import subprocess

    _tool(layout, "ffmpeg", b"#!/bin/sh\necho ok\n")
    subprocess.run(["xattr", "-w", "com.apple.quarantine", "0081;66e1c000;Safari;",
                    str(layout.bin_dir / "ffmpeg")], check=True)
    launcher.prepare_home(layout)

    launcher.install_tools(layout)

    attrs = subprocess.run(["xattr", str(layout.tools_dir / "ffmpeg")], capture_output=True, text=True).stdout
    assert "com.apple.quarantine" not in attrs


# --- first run asks before it starts (TASK-040.06) ---------------------------------


def test_setup_is_needed_until_the_app_says_it_is_done(tmp_path):
    """The stamp is written by `scribe.setup`, not by the launcher: the
    launcher asks the questions, the app decides what answering them means."""
    layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")

    assert launcher.setup_needed(layout) is True

    stamp = launcher.setup_stamp(layout)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("{}", encoding="utf-8")
    assert launcher.setup_needed(layout) is False


def test_the_answers_are_handed_to_the_app_not_acted_on_here(tmp_path):
    """ADR-011: the launcher is frozen and stdlib-only. It cannot write a
    setting row or parse .env, and must not learn how - it runs the app's
    python the same way --doctor does."""
    layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")

    command = launcher.setup_command(
        layout,
        {"hf_token": "hf_x", "provider": "ollama", "tier": "max", "diarize": False, "fetch_models": True},
    )

    assert command[0] == str(layout.env_python)
    assert command[1:3] == ["-m", "scribe.setup"]
    assert "--hf-token" in command and "hf_x" in command
    assert "--provider" in command and "ollama" in command
    assert "--tier" in command and "max" in command
    assert "--no-diarize" in command and "--fetch-models" in command


def test_an_unanswered_question_is_not_passed_at_all(tmp_path):
    """Every answer may be left blank, and a blank must not arrive as an empty
    string that overwrites a setting the user already had."""
    layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")

    command = launcher.setup_command(layout, {"hf_token": "", "provider": "", "tier": "", "fetch_models": False})

    assert command == [str(layout.env_python), "-m", "scribe.setup"]


# --- the first run on a machine with no environment (TASK-089.01) -----------------


def _drive_first_run(layout, tmp_path, monkeypatch, *, answers, setup_command=None,
                     force_setup=False, at_login=False):
    """Run `first_run` on a layout with no environment, recording every effect.

    Three things are faked and the rest is the real path. The uv is the
    file's own `_fake_uv`, which prints and exits without building an
    environment; the app process is a stand-in, because nothing here can
    serve; and `scribe.setup` is a command the caller chooses, because the
    real one names the environment python the fake uv never created - it
    would raise FileNotFoundError after the fix too, for the wrong reason.
    What is real: prepare_home, install_tools, sync, run_setup and
    run_streaming, in the order `first_run` puts them.

    No Tk anywhere, which is what makes the sequence testable at all.
    """
    order: list[str] = []
    reports: list[tuple[str, str]] = []

    def _record(name, real):
        def wrapper(*args, **kwargs):
            order.append(name)
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 0))
    for name in ("prepare_home", "install_tools", "sync"):
        monkeypatch.setattr(launcher, name, _record(name, getattr(launcher, name)))
    # Recorded on `run_setup` and not on the faked command, so that a run shows
    # up even when no command was given: "nothing was applied" is then an
    # absence the test can see rather than one it has to infer.
    monkeypatch.setattr(launcher, "run_setup", _record("scribe.setup", launcher.run_setup))
    if setup_command is not None:
        monkeypatch.setattr(launcher, "setup_command", lambda _layout, _answers: setup_command)

    class _FakeApp:
        def __init__(self, _layout, _port, _base=None):
            self.proc = object()

        def start(self):
            order.append("app start")

        def wait_ready(self, timeout=None):
            return True

        def alive(self):
            return True

        def stop(self, timeout=None):
            pass

    monkeypatch.setattr(launcher, "AppProcess", _FakeApp)

    asked: list[int] = []

    def ask():
        asked.append(1)
        return answers

    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    started = launcher.first_run(launch, ask, launch.report, force_setup, at_login)
    return order, reports, asked, started


def test_the_first_run_syncs_before_it_applies_the_answers(layout, tmp_path, monkeypatch):
    """The bug every release user walked into: the dialog came first and
    `scribe.setup` was run from an environment the sync had not made yet, so
    the Popen died on the worker thread with FileNotFoundError and the app was
    never started - 'Saving your answers...' for ever.

    The answers can only be applied by a python the sync creates, so the
    order is the fix (AC #1, #3).
    """
    order, reports, asked, started = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"hf_token": "", "provider": "ollama", "tier": "turbo", "fetch_models": False},
        setup_command=[sys.executable, "-c", "print('setup: answers saved')"],
    )

    assert order == ["prepare_home", "install_tools", "sync", "scribe.setup", "app start"]
    assert asked == [1]
    assert started is True
    assert ("busy", "setup: answers saved") in reports  # the command really ran
    assert not [state for state, _ in reports if state == "error"]


def test_a_setup_that_exits_non_zero_is_reported_with_its_code_and_the_app_still_starts(
        layout, tmp_path, monkeypatch):
    """A failed setup is a bad first impression; a silent one is worse, and a
    windowed build has no stderr to fall back on. The answers are not worth
    the app: it starts anyway, and the questions are also in Settings (AC #4)."""
    order, reports, _asked, started = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"provider": "ollama"},
        setup_command=[sys.executable, "-c", "raise SystemExit(3)"],
    )

    errors = [text for state, text in reports if state == "error"]
    assert len(errors) == 1 and "3" in errors[0]
    assert order[-1] == "app start"
    assert started is True


def test_a_setup_that_cannot_be_started_is_reported_not_swallowed(layout, tmp_path, monkeypatch):
    """The original failure, now visible: the command names a python that is
    not there, the Popen raises, and the person is told instead of watching a
    window that stopped (AC #4)."""
    order, reports, _asked, started = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"provider": "ollama"},
        setup_command=[str(tmp_path / "no-such-python"), "-m", "scribe.setup"],
    )

    errors = [text for state, text in reports if state == "error"]
    assert len(errors) == 1 and "answers" in errors[0]
    assert order[-1] == "app start"
    assert started is True


def test_a_setup_that_fails_in_a_way_nobody_expected_is_reported_too(layout, tmp_path, monkeypatch):
    """The promise is 'never raises', not 'catches the Popen's OSError'.

    A malformed command raises ValueError and not OSError, and on the worker
    thread of a windowed build anything that escapes is the window that
    stopped with nothing written anywhere - the failure this task exists to
    end (AC #4).
    """
    order, reports, _asked, started = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"provider": "ollama"},
        setup_command=[sys.executable, "-c", "print('never runs')\x00"],
    )

    errors = [text for state, text in reports if state == "error"]
    assert len(errors) == 1 and "answers" in errors[0]
    assert order[-1] == "app start"
    assert started is True


def test_skipping_the_questions_starts_the_app_and_runs_no_setup(layout, tmp_path, monkeypatch):
    """'Skip for now' is 'ask me next time', not 'never': no stamp is written
    here, nothing is applied, and the app starts."""
    order, reports, asked, started = _drive_first_run(
        layout, tmp_path, monkeypatch, answers=None, setup_command=None,
    )

    assert order == ["prepare_home", "install_tools", "sync", "app start"]
    assert "scribe.setup" not in order  # nothing was applied, and not by accident
    assert asked == [1]
    assert started is True
    assert not [state for state, _ in reports if state == "error"]


def test_a_myscribe_that_already_serves_is_not_asked_the_questions_again(layout, tmp_path, monkeypatch):
    """A deliberate change: the sitting now comes after the sync, and the
    already-serving branch returns before either. Nothing is prepared, nothing
    is asked - the same answer `Launch.run` has always given that case."""
    port = _free_port()
    server = _serve(port, {"ok": True})
    asked: list[int] = []
    reports: list[tuple[str, str]] = []
    try:
        launch = launcher.Launch(layout, port, False, lambda s, t: reports.append((s, t)))
        started = launcher.first_run(launch, lambda: asked.append(1), launch.report)
    finally:
        server.shutdown()
        server.server_close()

    assert started is True
    assert asked == []
    assert launch.app is None
    assert not layout.home.exists()
    assert reports[-1][0] == "done"


def test_setup_against_a_myscribe_that_already_serves_is_said_out_loud(layout):
    """`--setup` is not dropped in silence when MyScribe is already running.

    Before the sequence was split, the dialog came first and the answers were
    applied beside the serving app. The sitting now sits behind the sync and
    the serving branch returns before it, so the flag would go nowhere, and a
    flag somebody typed that does nothing is the silence this task exists to
    end. Asking there anyway is not a two-line change: `prepare` has already
    reported `done`, and `run_window`'s pump destroys the window three seconds
    after that state, so the dialog would be pulled away under the person's
    hands. 'Quit it first' is the answer, and it is said.
    """
    port = _free_port()
    server = _serve(port, {"ok": True})
    asked: list[int] = []
    reports: list[tuple[str, str]] = []
    try:
        launch = launcher.Launch(layout, port, False, lambda s, t: reports.append((s, t)))
        started = launcher.first_run(launch, lambda: asked.append(1), launch.report, force_setup=True)
    finally:
        server.shutdown()
        server.server_close()

    assert started is True
    assert asked == []
    assert [text for _state, text in reports if "--setup" in text]


def test_a_start_at_login_asks_nothing_all_the_way_through_the_sequence(layout, tmp_path, monkeypatch):
    """`--at-login` has to survive the whole way down to the gate.

    `wants_setup` is pinned on its own and `main` -> `run_window` is pinned on
    its own; this is the hop between them. Dropping the two arguments here
    leaves both of those tests green and opens the modal sitting at login
    again, which is what TASK-089.21 landed to prevent.
    """
    order, reports, asked, started = _drive_first_run(
        layout, tmp_path, monkeypatch, answers={"provider": "ollama"}, at_login=True,
    )

    assert asked == []
    assert order == ["prepare_home", "install_tools", "sync", "app start"]
    assert started is True
    assert not [state for state, _ in reports if state == "error"]


def test_setup_reopens_the_sitting_although_the_stamp_is_there(layout, tmp_path, monkeypatch):
    """The other half of the same hop: `--setup` wins over an answered
    sitting, because somebody typed it."""
    stamp = launcher.setup_stamp(layout)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("{}", encoding="utf-8")

    order, _reports, asked, started = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"provider": "ollama"},
        setup_command=[sys.executable, "-c", "print('setup: answers saved')"],
        force_setup=True,
    )

    assert asked == [1]
    assert order == ["prepare_home", "install_tools", "sync", "scribe.setup", "app start"]
    assert started is True


def test_the_sequence_is_free_of_tk(layout):
    """ADR-011 and ADR-015: the launcher renders and hands over, and the
    sequencing decides nothing a front-end could. The real proof is that the
    tests above drive `first_run` end to end with no Tk root anywhere; this
    only guards the source against a `import tkinter` creeping back in."""
    import inspect

    assert "tkinter" not in inspect.getsource(launcher.first_run)


# --- the smoke test has to find the executable (release run 35435965404) -----------


def test_the_smoke_test_looks_inside_the_onedir(tmp_path):
    """PyInstaller's onedir is a folder named after the app with the
    executable inside it, which `inno` and `appimage` both already knew. The
    first version read that name as the executable, so the Linux job tried to
    run a directory: "PermissionError: [Errno 13] Permission denied", after
    building a perfectly good 121 MB AppImage."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
    import build_release

    frozen = tmp_path / "frozen"
    (frozen / "MyScribe").mkdir(parents=True)

    assert build_release._frozen_binary(frozen, "linux-x64") == frozen / "MyScribe" / "MyScribe"
    assert build_release._frozen_binary(frozen, "windows-x64") == frozen / "MyScribe" / "MyScribe.exe"

    (frozen / "MyScribe.app").mkdir()
    mac = build_release._frozen_binary(frozen, "macos-arm64")
    assert mac == frozen / "MyScribe.app" / "Contents" / "MacOS" / "MyScribe"


# --- a start at login (TASK-089.21) -----------------------------------------------


def test_a_start_at_login_leaves_a_due_sitting_for_the_next_start_by_hand(tmp_path):
    """Nobody is at the screen at login, and a modal that holds up the watch
    folders is the opposite of what the login entry is for. A sitting that is
    due is not cancelled - it waits for the next start somebody makes by hand.

    `--setup` still wins over `--at-login`: somebody typed it.
    """
    layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")

    assert launcher.wants_setup(layout) is True
    assert launcher.wants_setup(layout, at_login=True) is False
    assert launcher.wants_setup(layout, force=True, at_login=True) is True

    stamp = launcher.setup_stamp(layout)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("{}", encoding="utf-8")

    assert launcher.wants_setup(layout) is False
    assert launcher.wants_setup(layout, force=True) is True
    assert launcher.wants_setup(layout, at_login=True) is False


def test_the_launcher_takes_at_login_on_the_command_line():
    """What the login entry passes. `--no-browser` was already there; the entry
    carries both, so a login opens no tab and asks no questions."""
    args = launcher.build_parser().parse_args(["--at-login", "--no-browser"])

    assert args.at_login is True
    assert args.no_browser is True
    assert launcher.build_parser().parse_args([]).at_login is False


def test_the_flag_reaches_the_window_and_not_only_the_parser(tmp_path, monkeypatch):
    """The one seam between `--at-login` and the predicate that reads it.

    A parser test cannot see it: deleting the argument from main()'s call left
    every other test in this file green, and a login would have opened the
    first-run sitting again. `run_window` itself needs Tk, so what is asserted
    is the call.
    """
    seen = {}

    def _capture(layout, port, open_browser, force_setup=False, at_login=False):
        seen.update(open_browser=open_browser, force_setup=force_setup, at_login=at_login)
        return 0

    monkeypatch.setattr(launcher, "run_window", _capture)

    assert launcher.main(["--at-login", "--no-browser", "--home", str(tmp_path)]) == 0
    assert seen == {"open_browser": False, "force_setup": False, "at_login": True}

    assert launcher.main(["--home", str(tmp_path)]) == 0
    assert seen == {"open_browser": True, "force_setup": False, "at_login": False}


def test_the_app_is_told_where_the_launcher_is(layout, monkeypatch):
    """The app cannot otherwise tell a release from a clone, and a login entry
    that named the environment's python would run a stale environment after the
    next update - only the launcher re-syncs."""
    monkeypatch.setattr(launcher, "this_launcher", lambda: Path(r"C:\Program Files\MyScribe\MyScribe.exe"))

    env = launcher.app_environment(layout, {"PATH": "/usr/bin"})

    assert env["MYSCRIBE_LAUNCHER"] == r"C:\Program Files\MyScribe\MyScribe.exe"


def test_a_launcher_that_is_not_frozen_names_no_path_at_all(layout):
    """Running from the source tree, `sys.executable` is a python in somebody's
    venv, not a launcher. Registering that would be the stale-environment bug;
    no variable at all is what makes the app fall back to the clone's own
    start script."""
    assert launcher.this_launcher() is None
    assert "MYSCRIBE_LAUNCHER" not in launcher.app_environment(layout, {"PATH": "/usr/bin"})


def test_the_stable_path_a_login_entry_should_name_per_platform():
    """Unverified on macOS and Linux: which path survives to the next login is
    the platform's own documentation, not a measurement anybody made here.

    macOS: the `.app` bundle, not the binary inside it. Linux: `APPIMAGE`,
    because inside an AppImage the running executable sits under a temporary
    mount that is gone after exit. Windows: the executable itself.
    """
    bundle = "/Applications/MyScribe.app/Contents/MacOS/MyScribe"
    assert launcher.launcher_path(bundle, "darwin", {}) == Path("/Applications/MyScribe.app")

    assert launcher.launcher_path("/tmp/.mount_x/MyScribe", "linux", {"APPIMAGE": "/home/r/MyScribe.AppImage"}) == Path(
        "/home/r/MyScribe.AppImage"
    )
    assert launcher.launcher_path("/opt/myscribe/MyScribe", "linux", {}) == Path("/opt/myscribe/MyScribe")

    assert launcher.launcher_path(r"C:\Program Files\MyScribe\MyScribe.exe", "win32", {}) == Path(
        r"C:\Program Files\MyScribe\MyScribe.exe"
    )
