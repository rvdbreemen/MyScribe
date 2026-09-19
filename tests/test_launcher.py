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


def test_a_failed_sync_is_not_stamped(layout, tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 2))
    launcher.prepare_home(layout)
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is False
    assert lines[-1] == "uv sync failed with exit code 2"
    assert not layout.stamp_file.exists()


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
