"""packaging/windows/stop-myscribe.ps1: what the installer and the uninstaller
run before they replace MyScribe (TASK-112.04, ADR-028).

Real processes and real HTTP: a fake launcher is a copy of cmd.exe placed in a
temporary install folder, with a ping as its child - what a launcher's python
child is to `taskkill /T` - and a fake server answers /health the way a new
MyScribe, a 0.8.x one or a clone does.
"""
from __future__ import annotations

import http.server
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Windows installer's script")

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "stop-myscribe.ps1"
NOT_RUNNING, STOPPED, STILL_RUNNING = 0, 10, 1


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return False


def _orphan(argv: list[str]) -> int:
    """Started through a parent that exits, as the app is to the installer."""
    spawn = ("import subprocess, sys; p = subprocess.Popen(sys.argv[1:], "
             "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW, "
             "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid)")
    return int(subprocess.run([sys.executable, "-c", spawn, *argv], capture_output=True, text=True,
                              check=True).stdout.strip())


def _children(pid: int) -> list[int]:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}').ProcessId"],
                         capture_output=True, text=True).stdout
    return [int(n) for n in out.split()]


@pytest.fixture
def install(tmp_path):
    folder = tmp_path / "Programs" / "MyScribe"
    folder.mkdir(parents=True)
    shutil.copy(Path(r"C:\Windows\System32\cmd.exe"), folder / "MyScribe.exe")
    return folder


@pytest.fixture
def started():
    pids: list[int] = []
    yield pids
    _end(*pids)


def _end(*pids):
    for pid in pids:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)


def _launcher(install, started) -> tuple[int, int]:
    """A process running from the install folder, and its child."""
    pid = _orphan([str(install / "MyScribe.exe"), "/c", "ping -n 120 127.0.0.1 >nul"])
    started.append(pid)
    deadline = time.monotonic() + 10
    while not _children(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    return pid, _children(pid)[0]


def _app(started) -> int:
    pid = _orphan([sys.executable, "-c", "import time; time.sleep(120)"])
    started.append(pid)
    return pid


class _Server:
    def __init__(self, health: dict, on_stop=None):
        self.posted: list[str] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._answer(200, health)

            def do_POST(self):
                server.posted.append(self.path)
                if on_stop is None:  # 0.8.x: no such route
                    self._answer(404, {"detail": "Not Found"})
                    return
                self._answer(202, {"stopping": True})
                threading.Thread(target=on_stop, daemon=True).start()

            def log_message(self, *args):
                pass

        self.http = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()


def _run(install, port, tmp_path, wait=5) -> tuple[int, str]:
    log = tmp_path / "MyScribe-launcher.log"
    done = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-InstallDir", str(install), "-Port", str(port), "-Wait", str(wait), "-Log", str(log)],
        capture_output=True, text=True, timeout=120)
    return done.returncode, log.read_text(encoding="utf-8-sig") if log.exists() else ""


def test_a_running_myscribe_is_asked_through_its_stop_route(install, started, tmp_path):
    """A new MyScribe: the route, so its running job is requeued (ADR-028)."""
    launcher, _child = _launcher(install, started)
    app = _app(started)
    server = _Server({"ok": True, "pid": app, "launcher_pid": launcher},
                     on_stop=lambda: _end(app))
    try:
        code, said = _run(install, server.port, tmp_path)
    finally:
        server.close()

    assert code == STOPPED and server.posted == ["/api/shutdown"]
    assert _gone(app) and _gone(launcher)
    assert "asking it to stop" in said and "starts over" in said


def _descendants(pid: int) -> list[int]:
    """Every process below ``pid``, nearest first."""
    found, frontier = [], [pid]
    while frontier:
        below = [child for parent in frontier for child in _children(parent)]
        found += below
        frontier = below
    return found


def test_a_myscribe_whose_parent_is_uvs_trampoline_is_asked_through_its_route(install, started, tmp_path):
    """The real tree on Windows: the launcher runs from the install folder, its
    child is uv's trampoline env/Scripts/python.exe in the home, and the app is
    below that. A 0.9.0 app names the trampoline as launcher_pid, which is not
    in the install folder; the script must still see the launcher above it and
    ask the route, or a running job is ended instead of requeued (TASK-117)."""
    sleeper = tmp_path / "app.py"
    sleeper.write_text("import time\ntime.sleep(120)\n")
    launcher = _orphan([str(install / "MyScribe.exe"), "/c", sys.executable, str(sleeper)])
    started.append(launcher)
    deadline = time.monotonic() + 10
    while not _descendants(launcher) and time.monotonic() < deadline:
        time.sleep(0.2)
    below = _descendants(launcher)
    trampoline, app = below[0], below[-1]
    server = _Server({"ok": True, "pid": app, "launcher_pid": trampoline},
                     on_stop=lambda: _end(app))
    try:
        code, said = _run(install, server.port, tmp_path)
    finally:
        server.close()

    assert code == STOPPED and server.posted == ["/api/shutdown"], said
    assert "asking it to stop" in said
    assert _gone(app) and _gone(launcher)


def test_a_0_8_myscribe_without_a_stop_route_is_ended_with_its_children(install, started, tmp_path):
    """0.8.x has no route and no pid in /health; its launcher runs from the
    install folder, and its python is that launcher's child."""
    launcher, child = _launcher(install, started)
    server = _Server({"ok": True, "version": "0.8.6"})
    try:
        code, said = _run(install, server.port, tmp_path)
    finally:
        server.close()

    assert code == STOPPED and server.posted == []
    assert _gone(launcher) and _gone(child), "taskkill /T left the launcher's child running"
    assert f"process {launcher}" in said


def test_a_route_that_does_not_stop_it_in_time_ends_it(install, started, tmp_path):
    launcher, child = _launcher(install, started)
    app = _app(started)
    server = _Server({"ok": True, "pid": app, "launcher_pid": launcher}, on_stop=lambda: None)
    try:
        code, said = _run(install, server.port, tmp_path, wait=1)
    finally:
        server.close()

    assert code == STOPPED and server.posted == ["/api/shutdown"]
    assert _gone(launcher) and _gone(child)
    assert "did not stop within 1 s" in said


def test_a_server_that_is_not_this_install_is_left_alone(install, started, tmp_path):
    """A clone on the same port: its launcher, if any, is not in this install
    folder, and an installer has no business stopping it."""
    app = _app(started)
    server = _Server({"ok": True, "pid": app, "launcher_pid": None}, on_stop=lambda: _end(app))
    try:
        code, _said = _run(install, server.port, tmp_path)
    finally:
        server.close()

    assert code == NOT_RUNNING and server.posted == []
    assert _alive(app)


def test_nothing_running_is_nothing_to_stop(install, tmp_path):
    code, said = _run(install, _free_port(), tmp_path)
    assert code == NOT_RUNNING and said == ""


def test_a_process_already_gone_when_it_is_ended_does_not_fail_the_stop(install, started, tmp_path):
    """Two processes from the install folder, one the other's child: ending
    the parent with its tree takes the child too, and the second taskkill
    finds nothing. Windows PowerShell 5.1 turned that taskkill's "not found"
    on stderr into a terminating error under ErrorActionPreference=Stop, and
    the script exited 1 - "still running" - for a MyScribe that had stopped."""
    shutil.copy(Path(r"C:\Windows\System32\PING.EXE"), install / "helper.exe")
    parent = _orphan([str(install / "MyScribe.exe"), "/c", str(install / "helper.exe"), "-n", "120", "127.0.0.1"])
    started.append(parent)
    deadline = time.monotonic() + 10
    while not _children(parent) and time.monotonic() < deadline:
        time.sleep(0.2)
    child = _children(parent)[0]

    code, said = _run(install, _free_port(), tmp_path)

    assert code == STOPPED, said
    assert _gone(parent) and _gone(child)
