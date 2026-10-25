"""The launcher (ADR-011): paths, the sync decision, the child environment,
one instance, and Quit reaching the runner children - all without network.

The launcher is stdlib-only and lives outside the `scribe` package, so it is
loaded from its file.
"""

from __future__ import annotations

import gc
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import sys
import textwrap
import threading
import time
import types
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


@pytest.fixture(autouse=True)
def _no_pointer_from_this_machine(tmp_path, monkeypatch):
    """No test here reads the pointer file of whoever is running it.

    `home_dir` consults `MyScribe.location` beside the default home, and two
    tests below compare `home_dir("darwin", {})` and `home_dir("linux", {})`
    against the real `Path.home()`. Without this they would be green on a
    machine that never moved its home and red for whoever did - the failure
    the design spec flagged for this file (spec §3.13). Autouse, so no future
    test has to remember; a test about the pointer points it somewhere of its
    own and wins, because monkeypatch applies in order.
    """
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: tmp_path / "no-pointer.location")
    # TASK-112.07: the instance lock and the login note sit beside the home,
    # and a `main()` without --home lands on the default one. Under the real
    # LOCALAPPDATA that is beside the MyScribe of whoever runs the tests, whose
    # launcher holds that lock: the result would depend on whether it runs.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


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


# --- what the launcher writes is its owner's alone (GitHub #42) -----------------------


POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="mode bits are a POSIX answer; Windows has ACLs")


@pytest.fixture
def umask_022():
    """The common umask, pinned: under a runner's 077 every folder is born
    0700 anyway and these tests would be green for a launcher that never
    asked for it."""
    old = os.umask(0o022)
    yield
    os.umask(old)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@POSIX_ONLY
def test_a_home_the_launcher_makes_is_its_owners_alone(layout, umask_022):
    """app.log holds uvicorn's access log - request paths, search terms - and
    tracebacks; .env is where the template asks for the tokens. Under a 0755
    home every account on the machine could read both."""
    launcher.prepare_home(layout)

    assert _mode(layout.home) == 0o700
    assert _mode(layout.logs_dir) == 0o700
    assert _mode(layout.data_dir) == 0o700
    assert _mode(layout.env_file) == 0o600


@POSIX_ONLY
def test_folders_that_already_exist_keep_their_mode(tmp_path, layout, umask_022):
    """A home or library the user picked was there first, and how open it is
    was their choice: only what the launcher makes is made private."""
    library = tmp_path / "library"
    library.mkdir()
    os.chmod(library, 0o750)
    layout.home.mkdir()
    os.chmod(layout.home, 0o755)
    adopted = launcher.Layout(layout.home, layout.payload, data=library)

    launcher.prepare_home(adopted)

    assert _mode(adopted.home) == 0o755
    assert _mode(library) == 0o750
    assert _mode(adopted.logs_dir) == 0o700


def _no_app(monkeypatch):
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: types.SimpleNamespace())


@POSIX_ONLY
def test_app_log_is_written_for_its_owner_only(layout, monkeypatch, umask_022):
    launcher.prepare_home(layout)
    _no_app(monkeypatch)

    launcher.AppProcess(layout, _free_port()).start()

    assert _mode(layout.logs_dir / "app.log") == 0o600


@POSIX_ONLY
def test_an_app_log_from_before_is_closed_and_kept(layout, monkeypatch, umask_022):
    """Every install before this fix has a 0644 app.log; the file's mode at
    creation does not reach it, so the launcher tightens it - what applog
    does for the app's own log."""
    launcher.prepare_home(layout)
    log = layout.logs_dir / "app.log"
    log.write_bytes(b"GET /search?q=diagnosis\n")
    os.chmod(log, 0o644)
    _no_app(monkeypatch)

    launcher.AppProcess(layout, _free_port()).start()

    assert _mode(log) == 0o600
    assert log.read_bytes() == b"GET /search?q=diagnosis\n"


@POSIX_ONLY
@pytest.mark.parametrize("existing", [False, True], ids=["new", "from-before"])
def test_the_launchers_own_logs_are_written_for_their_owner_only(tmp_path, layout, umask_022, existing):
    launcher.prepare_home(layout)
    pointer = tmp_path / "MyScribe.location"
    install = layout.logs_dir / launcher.INSTALL_LOG
    early = launcher.early_log_path(pointer)
    if existing:
        for path in (install, early):
            path.write_text("before\n", encoding="utf-8")
            os.chmod(path, 0o644)

    launcher.InstallLog(install).write("a line")
    launcher.write_early(["a line"], pointer)

    assert _mode(install) == 0o600
    assert _mode(early) == 0o600
    assert install.read_text(encoding="utf-8").splitlines()[-1] == "a line"


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


def _fake_uv(tmp_path, exit_code: int, marker: Path | None = None) -> list[str]:
    """A uv that prints and exits. With `marker` it leaves a file behind, which
    is how the tests below prove that no download was even started."""
    script = tmp_path / "fake_uv.py"
    touch = f"open({str(marker)!r}, 'w').close(); " if marker is not None else ""
    script.write_text(
        f"{touch}print('Downloading torch'); print('Installed 3 packages'); raise SystemExit({exit_code})\n"
    )
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


# What uv 0.12.13 printed on the machine of GitHub issue 16.
MINOR_LINK_FAILURE = [
    "Downloading cpython-3.12.14-windows-x86_64-none (download) (21.0MiB)",
    " Downloaded cpython-3.12.14-windows-x86_64-none (download)",
    "error: Failed to create Python minor version link directory",
    "  Caused by: The path cannot be traversed because it contains an untrusted mount point. (os error 448)",
]


def _flaky_uv(tmp_path, first: list[str], then: int = 0) -> tuple[list[str], Path]:
    """A uv that prints `first` and exits 2 on its first run, and exits `then`
    on every run after it. Each run's arguments are one JSON line in the log."""
    log = tmp_path / "uv_runs.jsonl"
    script = tmp_path / "flaky_uv.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys
        log = {str(log)!r}
        try:
            runs = sum(1 for _ in open(log, encoding="utf-8"))
        except FileNotFoundError:
            runs = 0
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(sys.argv[1:]) + "\\n")
        if runs == 0:
            print("\\n".join({first!r}))
            raise SystemExit(2)
        raise SystemExit({then})
    """))
    return [sys.executable, str(script)], log


def _runs(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _downloaded_python(layout, name: str = "cpython-3.12.14-windows-x86_64-none") -> Path:
    """The interpreter uv leaves behind when only its link step failed."""
    home = layout.python_dir / name
    executable = home / "python.exe" if layout.windows else home / "bin" / "python3"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("", encoding="utf-8")
    return executable


def test_a_sync_that_fails_only_on_the_minor_version_link_is_retried_with_the_downloaded_python(
        layout, tmp_path, monkeypatch):
    """GitHub issue 16: uv stopped after the download, on the junction it makes
    beside it. The interpreter is complete by then, and a sync that names it
    gets through (measured with uv 0.12.13 on 2026-10-19, ADR-027)."""
    command, log = _flaky_uv(tmp_path, MINOR_LINK_FAILURE)
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: command)
    launcher.prepare_home(layout)
    _downloaded_python(layout, "cpython-3.12.9-windows-x86_64-none")
    newest = _downloaded_python(layout)
    _downloaded_python(layout, "cpython-3.12-windows-x86_64-none")  # a link left behind
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is True
    runs = _runs(log)
    assert len(runs) == 2
    assert "--python" not in runs[0]
    assert runs[1][runs[1].index("--python") + 1] == str(newest)
    assert runs[1][:len(runs[0])] == runs[0]
    assert any("trying once more" in line for line in lines)
    _fake_env_python(layout)
    assert launcher.needs_sync(layout) is False


def test_any_other_sync_failure_is_not_retried(layout, tmp_path, monkeypatch):
    _no_proxy_configured(monkeypatch)
    command, log = _flaky_uv(tmp_path, ["error: Failed to fetch: `https://pypi.org/simple/torch/`"])
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: command)
    launcher.prepare_home(layout)
    _downloaded_python(layout)
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is False
    assert len(_runs(log)) == 1
    assert lines[-1] == "uv sync failed with exit code 2"


def test_the_link_failure_is_not_retried_without_a_downloaded_python(layout, tmp_path, monkeypatch):
    """Nothing to name with --python: a retry would only download again."""
    _no_proxy_configured(monkeypatch)
    command, log = _flaky_uv(tmp_path, MINOR_LINK_FAILURE)
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: command)
    launcher.prepare_home(layout)
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is False
    assert len(_runs(log)) == 1


def test_a_retry_that_fails_too_is_reported_once_and_not_repeated(layout, tmp_path, monkeypatch):
    _no_proxy_configured(monkeypatch)
    command, log = _flaky_uv(tmp_path, MINOR_LINK_FAILURE, then=2)
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: command)
    launcher.prepare_home(layout)
    _downloaded_python(layout)
    lines: list[str] = []

    assert launcher.sync(layout, lines.append) is False
    assert len(_runs(log)) == 2
    assert lines[-1] == "uv sync failed with exit code 2"
    assert sum(line.startswith("uv sync failed") for line in lines) == 1
    assert not layout.stamp_file.exists()


# What uv 0.12.13 printed on 2026-10-20 with HTTPS_PROXY at a closed port (TASK-113.02).
NETWORK_FAILURE = [
    "error: Failed to download https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20260901/cpython-3.12.14%2B20260901-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
    "  Caused by: error sending request for url (https://github.com/astral-sh/python-build-standalone/...)",
    "  Caused by: client error (Connect)",
    "  Caused by: tunnel error: failed to create underlying connection",
    "  Caused by: tcp connect error",
    "  Caused by: No connection could be made because the target machine actively refused it. (os error 10061)",
]


def _failed_install(layout, tmp_path, monkeypatch, printed: list[str]) -> list[str]:
    """The error lines `prepare` reports after a sync that printed `printed`."""
    _no_proxy_configured(monkeypatch)
    command, _log = _flaky_uv(tmp_path, printed, then=2)
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: command)
    monkeypatch.setattr(launcher, "enough_disk", lambda *_args, **_kwargs: True)
    reports: list[tuple[str, str]] = []

    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))

    assert launch.prepare() is False
    return [text for state, text in reports if state == "error"]


def test_a_sync_that_failed_on_the_network_points_at_the_internet_connection(
        layout, tmp_path, monkeypatch):
    errors = _failed_install(layout, tmp_path, monkeypatch, NETWORK_FAILURE)

    assert len(errors) == 1
    assert "internet connection" in errors[0]


def test_a_sync_that_failed_on_this_computer_does_not_blame_the_internet(
        layout, tmp_path, monkeypatch):
    """Issue 16: the link step failed on the file system, and the window said
    to check the internet connection - which was fine (TASK-113.02 AC #1)."""
    errors = _failed_install(layout, tmp_path, monkeypatch, MINOR_LINK_FAILURE)

    assert len(errors) == 1
    assert "internet" not in errors[0]
    assert "on this computer" in errors[0]
    assert str(layout.logs_dir / launcher.INSTALL_LOG) in errors[0]


@pytest.mark.parametrize("printed", [
    NETWORK_FAILURE,
    # an unknown host for the Python download, then for the package index
    ["error: Failed to download https://no-such-host.invalid/py/20260901/cpython-3.12.14.tar.gz",
     "  Caused by: dns error"],
    ["error: Failed to fetch: `https://no-such-host.invalid/simple/six/`",
     "  Caused by: error sending request for url (https://no-such-host.invalid/simple/six/)"],
], ids=["closed-proxy", "python-host", "index-host"])
def test_what_uv_printed_without_a_network_counts_as_the_network(layout, printed):
    assert "internet connection" in launcher.install_failure(layout, printed)


def test_a_configured_proxy_alone_does_not_make_a_failure_a_network_one(layout):
    said = MINOR_LINK_FAILURE + [f"uv sync failed with exit code 2{launcher.proxy_note({'HTTPS_PROXY': 'http://proxy:8080'})}"]

    assert "internet" not in launcher.install_failure(layout, said)


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


@pytest.mark.skipif(sys.platform == "win32", reason="TIME_WAIT blocks a plain bind on POSIX only")
def test_a_port_a_stopped_server_left_in_time_wait_is_free():
    """A server that closed a connection itself leaves it in TIME_WAIT on its
    own port for about a minute. A plain bind fails on that on Linux, and the
    launcher said "Port 4300 is used by another program" right after a stop,
    although uvicorn - which sets SO_REUSEADDR - would have bound (TASK-118)."""
    port = _free_port()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn binds
    listener.bind(("127.0.0.1", port))
    listener.listen()
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _ = listener.accept()
    accepted.close()  # the server closes first, so the TIME_WAIT is on its port
    client.recv(1)
    client.close()
    listener.close()

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


def _shipped_contract(layout, number: int) -> None:
    """A payload whose `scribe/setup.py` names one contract number. Not the
    engine: what the launcher reads is one line of text."""
    (layout.app_dir / "scribe" / "setup.py").write_text(
        f'"""Not the engine - only the line the launcher reads."""\n\nCONTRACT = {number}\n',
        encoding="utf-8")


def _stamp(layout, document) -> None:
    """Write the stamp `scribe.setup` leaves behind, or whatever stands in for
    one: a string is written as it is, so that a stamp nobody can read can be
    put there too."""
    stamp = launcher.setup_stamp(layout)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")


def test_the_sitting_appears_until_a_finished_sitting_of_this_contract_says_otherwise(layout):
    """The gate, and every way it fires (TASK-089.11, criteria #2 and #8).

    The stamp is written by `scribe.setup`, not by the launcher: the launcher
    asks the questions, the app decides what answering them means. What the
    launcher decides is whether to ask at all, and it decides it from that file
    plus one number out of the payload - as data, with no child process.

    It is deliberately not a question about this machine. A token removed after
    the sitting makes `--plan` open again and does not reopen the sitting: the
    gate is about what was asked, `--plan` is about the machine now (criterion
    #7), and the launcher cannot see a token in any case.

    Renamed from `test_setup_is_needed_until_the_app_says_it_is_done`, which
    pinned "the file exists" - true until this task and the whole of what it
    replaces.
    """
    _shipped_contract(layout, 7)

    assert launcher.setup_needed(layout) is True, "no stamp at all"

    _stamp(layout, "{ not json")
    assert launcher.setup_needed(layout) is True, "a stamp nobody can read"

    _stamp(layout, {})
    assert launcher.setup_needed(layout) is True, "no sitting ever ended, so none was held"

    _stamp(layout, [])
    assert launcher.setup_needed(layout) is True, "JSON, but not a document"

    _stamp(layout, json.dumps("a string"))  # valid JSON, so it gets past the parse
    assert launcher.setup_needed(layout) is True, "and neither is this"

    _stamp(layout, {"contract": "7", "ended": 1789930000.0, "questions": {}})
    assert launcher.setup_needed(layout) is True, "a contract that is not a number is no number"

    _stamp(layout, {"contract": 7, "questions": {}})
    assert launcher.setup_needed(layout) is True, "a contract and no `ended` is not this writer's"

    _stamp(layout, {"contract": 6, "ended": 1789930000.0, "questions": {"hf_token": "skipped"}})
    assert launcher.setup_needed(layout) is True, "finished, but before this payload's questions"

    _stamp(layout, {"contract": 7, "ended": 1789930000.0, "questions": {"hf_token": "skipped"}})
    assert launcher.setup_needed(layout) is False, "asked once - and a skip does not nag"


def test_a_contract_number_that_cannot_be_read_shows_the_sitting(layout):
    """Fail open, both ways round: asking twice costs a dialog somebody can
    close, and never asking is the failure the gate exists for."""
    _stamp(layout, {"contract": 7, "ended": 1789930000.0, "questions": {}})

    assert launcher.setup_contract(layout) is None, "this payload has no scribe/setup.py"
    assert launcher.setup_needed(layout) is True

    (layout.app_dir / "scribe" / "setup.py").write_text(
        'print(f"this engine speaks CONTRACT = 99")\n', encoding="utf-8")
    assert launcher.setup_contract(layout) is None, "the name inside a sentence is not the line"
    assert launcher.setup_needed(layout) is True


def test_the_gate_starts_no_child_to_find_out(layout, monkeypatch):
    """Two small reads, and never a `--plan`.

    A gate that asked the engine would pay for a Python child at every start.
    Measured on this machine on 2026-09-22 (median of 3 cold children against
    the median of 5 x 1000 in-process calls): 4332 ms for `--plan`, 4183 ms for
    the `--status` this replaces, and 0.78 ms for the gate - about 5500 times
    cheaper. So the number comes out of the payload as text and the states out
    of the stamp as JSON, and the sitting itself pays for the one `--plan`.
    """
    def never(*args, **kwargs):
        raise AssertionError("the gate started a child process")

    monkeypatch.setattr(launcher.subprocess, "Popen", never)
    monkeypatch.setattr(launcher.subprocess, "run", never)
    _shipped_contract(layout, 7)
    _stamp(layout, {"contract": 7, "ended": 1789930000.0, "questions": {}})

    assert launcher.setup_contract(layout) == 7
    assert launcher.setup_needed(layout) is False
    assert launcher.wants_setup(layout) is False


def test_the_contract_number_is_read_out_of_the_engine_that_ships(layout):
    """The text the launcher reads and the constant the engine uses cannot
    drift: the payload here is the real `scribe/setup.py`, copied the way a
    build copies it, and the number read out of it is the engine's own.

    The app is imported here and nowhere near the launcher: ADR-011 keeps the
    launcher stdlib-only, and this test is the one place allowed to hold both.
    """
    from scribe import setup

    engine = Path(setup.__file__).read_text(encoding="utf-8")
    (layout.app_dir / "scribe" / "setup.py").write_text(engine, encoding="utf-8")

    assert launcher.setup_contract(layout) == setup.CONTRACT


# --- the answers go on stdin, never on the argv (TASK-089.15, ADR-015) ------------
#
# The two tests below carried the answer flags until 2026-09-22. They are
# revised and not deleted: the claim each one makes still holds, and only the
# place the answers travel has moved. What they pinned was also the gate this
# task exists to remove - `scribe.setup` refuses `--hf-token` with exit 2 as
# its first statement, before it reads any other answer, so a sitting in which
# somebody typed a token lost the whole sitting: no setting row, no stamp, and
# the gate again at the next start. Measured on 2026-09-22 against the real
# engine: exit 2, an empty data directory, no setup.json.

SENTINEL = "hf_ThisIsNotARealToken_42"
"""A typed secret, as the argv and the log tests look for it. Not a real
credential and not one of this machine's: the point is that it appears
nowhere, so it has to be a value a test can search for."""


def _engine_records(layout, monkeypatch, record: Path, *, contract: int = 7, exit_code: int = 0,
                    says: tuple = ()) -> None:
    """A `scribe.setup` in the payload that writes down its argv and its stdin.

    It carries the `CONTRACT` line too, because the launcher reads that number
    out of this same file (`setup_contract`) - two stand-ins for one file would
    be one of them winning by write order.

    `env_python` is the python uv makes, which nothing here can build, so the
    property is pointed at the interpreter running the tests. What is under
    test is still real: a real child process, its real argv, and a real pipe
    carrying the document to it.
    """
    body = textwrap.dedent(
        f"""
        \"\"\"Not the engine: it records what the launcher gave it.\"\"\"

        CONTRACT = {contract}

        import json, sys
        from pathlib import Path

        Path({str(record)!r}).write_text(
            json.dumps({{"argv": sys.argv, "stdin": sys.stdin.read()}}), encoding="utf-8")
        for line in {list(says)!r}:
            print(line, flush=True)
        raise SystemExit({exit_code})
        """
    )
    (layout.app_dir / "scribe" / "setup.py").write_text(body, encoding="utf-8")
    monkeypatch.setattr(launcher.Layout, "env_python", property(lambda self: Path(sys.executable)))


def test_the_answers_are_handed_to_the_app_not_acted_on_here(tmp_path):
    """ADR-011: the launcher is frozen and stdlib-only. It cannot write a
    setting row or parse .env, and must not learn how - it runs the app's
    python the same way --doctor does.

    Revised on 2026-09-22 (TASK-089.15): the answers are one JSON document on
    the child's stdin, so the command carries the door and nothing else.
    """
    layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")

    command = launcher.setup_command(layout)

    assert command == [str(layout.env_python), "-m", "scribe.setup", "--apply-stdin"]


def test_an_unanswered_question_is_not_passed_at_all(tmp_path):
    """Every answer may be left blank, and a blank must not arrive as an empty
    string that overwrites a setting the user already had.

    Revised on 2026-09-22 (TASK-089.15): a question somebody skipped is null in
    the document, which the engine records as skipped and writes nothing for;
    a question that was never shown is not in the document at all.
    """
    layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")

    document = launcher.setup_document(layout, {"hf_token": None, "default_tier": "max"})

    assert document["answers"] == {"hf_token": None, "default_tier": "max"}
    assert "llm_provider" not in document["answers"]


def test_no_secret_reaches_the_child_s_argv_and_the_answers_arrive_on_its_stdin(
        layout, tmp_path, monkeypatch):
    """ADR-015's Must Not: a secret never travels on a command line.

    End to end and not on the shape of a list: a real child, its real argv and
    a real pipe. `--hf-token` is what this replaces, and `scribe.setup` refuses
    that flag with exit 2 before it reads anything else, so the flag did not
    only leak the token - it lost the sitting.
    """
    record = tmp_path / "child.json"
    _engine_records(layout, monkeypatch, record, contract=7, says=("saved: nothing",))

    code = launcher.run_setup(
        layout,
        {"hf_token": SENTINEL, "llm_provider": "openrouter", "default_tier": None},
        lambda _line: None,
    )

    assert code == 0
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert not [word for word in seen["argv"] if SENTINEL in word], seen["argv"]
    assert seen["argv"][1:] == ["--apply-stdin"]
    assert json.loads(seen["stdin"]) == {
        "contract": 7,
        "answers": {"hf_token": SENTINEL, "llm_provider": "openrouter", "default_tier": None},
    }


def test_a_contract_number_that_cannot_be_read_is_left_out_of_the_document(tmp_path):
    """An unreadable shipped `setup.py` must not become a claim the engine
    disagrees with: `--apply-stdin` answers a `contract` that is not its own
    with exit 2 and applies nothing, and a number nobody could read is not a
    disagreement - it is silence. The gate already asks again in that case
    (`setup_needed`), which is where it belongs."""
    layout = launcher.Layout(tmp_path / "home", tmp_path / "payload")
    assert launcher.setup_contract(layout) is None

    assert launcher.setup_document(layout, {"default_tier": "max"}) == {
        "answers": {"default_tier": "max"},
    }


def test_the_launcher_names_no_secret_flag_at_all():
    """ADR-015's own Verification line: `grep -n -- "--hf-token"
    packaging/launcher/myscribe_launcher.py` returned line 476 on 2026-09-20
    and nothing after TASK-089.15. Widened to the shape the record's
    Enforcement tripwire forbids under `packaging/launcher/**`, so a
    `--llm-key` cannot take the same route later."""
    source = LAUNCHER_PATH.read_text(encoding="utf-8")

    forbidden = re.findall(r"""["']--[a-z0-9-]*(?:token|key|secret|password)[a-z0-9-]*["']""", source)

    assert forbidden == []


# --- the first run on a machine with no environment (TASK-089.01) -----------------


SOMETHING_IS_OPEN = {
    "contract": 7,
    "found": [],
    "ollama": {"note": "not installed"},
    "downloads": {},
    "questions": [
        {"id": "default_tier", "kind": "choice", "text": "Transcription quality.",
         "choices": [{"value": "turbo", "label": "Turbo"}, {"value": "max", "label": "Maximum"}],
         "current": "turbo", "default": "turbo", "shown_if": None,
         "if_skipped": "Nothing is written.", "answer_later": "Settings > Transcription"},
    ],
}
"""One plan, small enough to read. The sitting tests live in
tests/test_launcher_sitting.py and draw richer ones; what this file needs is
something for `first_run` to hand to `ask`."""


def _drive_first_run(layout, tmp_path, monkeypatch, *, answers, setup_command=None,
                     force_setup=False, at_login=False, retry=None, plan=None, prove_code=0):
    """Run `first_run` on a layout with no environment, recording every effect.

    Four things are faked and the rest is the real path. The uv is the file's
    own `_fake_uv`, which prints and exits without building an environment;
    the app process is a stand-in, because nothing here can serve;
    `scribe.setup` is a command the caller chooses, because the real one names
    the environment python the fake uv never created; and the two children
    that only read - `--plan` and `--prove` - are answered from this file,
    because each one costs about four seconds of a real import. What is real:
    prepare_home, install_tools, sync, run_setup and run_streaming, in the
    order `first_run` puts them.

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
        monkeypatch.setattr(launcher, "setup_command", lambda _layout: setup_command)

    # `on_start` on both: every setup child is registered with the Launch, so
    # that Quit can stop that one process (ADR-017's M10).
    def _plan(_layout, _report, unasked_only=True, on_start=None):
        order.append("plan")
        planned.append(unasked_only)
        return SOMETHING_IS_OPEN if plan is None else plan

    def _prove(_layout, report, port, on_start=None):
        order.append("prove")
        proved.append(port)
        report("busy", f"prove: exit {prove_code}")
        return prove_code

    planned: list[bool] = []
    proved: list[int] = []
    monkeypatch.setattr(launcher, "setup_plan", _plan)
    monkeypatch.setattr(launcher, "run_prove", _prove)

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

    asked: list[dict] = []

    def ask(document):
        asked.append(document)
        return answers

    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    started = launcher.first_run(launch, ask, launch.report, force_setup, at_login, retry)
    return types.SimpleNamespace(order=order, reports=reports, asked=asked, started=started,
                                 planned=planned, proved=proved, port=launch.port, launch=launch)


def test_the_first_run_syncs_before_it_applies_the_answers(layout, tmp_path, monkeypatch):
    """The bug every release user walked into: the dialog came first and
    `scribe.setup` was run from an environment the sync had not made yet, so
    the Popen died on the worker thread with FileNotFoundError and the app was
    never started - 'Saving your answers...' for ever.

    The answers can only be applied by a python the sync creates, so the
    order is the fix (AC #1, #3).

    This list does not carry the location question of TASK-089.14, and that is
    not an oversight: where everything goes is settled in `main`, before there
    is a `Launch` to be a step of. It has to be - `prepare_home` is the first
    write under a home, and `run_window` builds its title, its "Open data
    folder" button and its log path from the layout before this worker starts.
    """
    run = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"default_tier": "turbo"},
        setup_command=[sys.executable, "-c", "print('setup: answers saved')"],
    )

    assert run.order == ["prepare_home", "install_tools", "sync", "plan", "scribe.setup",
                         "prove", "app start"]
    assert [document["contract"] for document in run.asked] == [7]
    assert run.started is True
    assert ("busy", "setup: answers saved") in run.reports  # the command really ran
    assert not [state for state, _ in run.reports if state == "error"]


def test_a_setup_that_exits_non_zero_is_reported_with_its_code_and_the_app_still_starts(
        layout, tmp_path, monkeypatch):
    """A failed setup is a bad first impression; a silent one is worse, and a
    windowed build has no stderr to fall back on. The answers are not worth
    the app: it starts anyway, and the questions are also in Settings (AC #4)."""
    run = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"default_tier": "max"},
        setup_command=[sys.executable, "-c", "raise SystemExit(3)"],
    )

    errors = [text for state, text in run.reports if state == "error"]
    assert len(errors) == 1 and errors[0] == launcher.GATED_MODEL
    assert run.order[-1] == "app start"
    assert run.started is True


def test_continue_without_starts_myscribe_anyway(layout, tmp_path, monkeypatch):
    """The second of the error state's two buttons, at the level where it
    means something: the answers are not worth the app. They can also be given
    in Settings, and a windowed build has no stderr for the person to read."""
    asked: list[str] = []
    run = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"default_tier": "max"},
        setup_command=[sys.executable, "-c", "raise SystemExit(4)"],
        retry=lambda sentence: asked.append(sentence) or False,
    )

    assert asked == [launcher.NO_ROOM], "the error state was shown, with the disk sentence"
    assert run.order[-1] == "app start"
    assert run.started is True


def test_retry_holds_the_sitting_again_and_then_the_app_starts(layout, tmp_path, monkeypatch):
    """The first button, from the same place: the whole sitting runs again -
    plan, ask, apply - and MyScribe starts either way."""
    answers = iter([True, False])
    run = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"default_tier": "max"},
        setup_command=[sys.executable, "-c", "raise SystemExit(3)"],
        retry=lambda _sentence: next(answers),
    )

    assert run.order == ["prepare_home", "install_tools", "sync",
                         "plan", "scribe.setup", "plan", "scribe.setup",
                         "prove", "app start"]
    assert len(run.asked) == 2
    assert run.started is True


def test_a_setup_that_cannot_be_started_is_reported_not_swallowed(layout, tmp_path, monkeypatch):
    """The original failure, now visible: the command names a python that is
    not there, the Popen raises, and the person is told instead of watching a
    window that stopped (AC #4)."""
    run = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"default_tier": "max"},
        setup_command=[str(tmp_path / "no-such-python"), "-m", "scribe.setup"],
    )

    errors = [text for state, text in run.reports if state == "error"]
    assert len(errors) == 1 and "answers" in errors[0]
    assert run.order[-1] == "app start"
    assert run.started is True


def test_a_setup_that_fails_in_a_way_nobody_expected_is_reported_too(layout, tmp_path, monkeypatch):
    """The promise is 'never raises', not 'catches the Popen's OSError'.

    A malformed command raises ValueError and not OSError, and on the worker
    thread of a windowed build anything that escapes is the window that
    stopped with nothing written anywhere - the failure this task exists to
    end (AC #4).
    """
    run = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"default_tier": "max"},
        setup_command=[sys.executable, "-c", "print('never runs')\x00"],
    )

    errors = [text for state, text in run.reports if state == "error"]
    assert len(errors) == 1 and "answers" in errors[0]
    assert run.order[-1] == "app start"
    assert run.started is True


def test_skipping_the_questions_starts_the_app_and_runs_no_setup(layout, tmp_path, monkeypatch):
    """'Ask me next time' is what it says: no stamp is written here, nothing is
    applied, and the app starts - so the sitting returns at the next start."""
    run = _drive_first_run(
        layout, tmp_path, monkeypatch, answers=None, setup_command=None,
    )

    assert run.order == ["prepare_home", "install_tools", "sync", "plan", "app start"]
    assert "scribe.setup" not in run.order  # nothing was applied, and not by accident
    assert "prove" not in run.order, "nothing was applied, so there is nothing to prove"
    assert len(run.asked) == 1
    assert run.started is True
    assert not [state for state, _ in run.reports if state == "error"]


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
        started = launcher.first_run(launch, lambda document: asked.append(document), launch.report)
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
        started = launcher.first_run(launch, lambda document: asked.append(document), launch.report,
                                     force_setup=True)
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
    run = _drive_first_run(
        layout, tmp_path, monkeypatch, answers={"default_tier": "max"}, at_login=True,
    )

    assert run.asked == []
    assert run.order == ["prepare_home", "install_tools", "sync", "app start"]
    assert "plan" not in run.order, "not even the plan child: nobody is at the screen"
    assert run.started is True
    assert not [state for state, _ in run.reports if state == "error"]


def test_setup_reopens_the_sitting_although_the_stamp_is_there(layout, tmp_path, monkeypatch):
    """The other half of the same hop: `--setup` wins over an answered
    sitting, because somebody typed it."""
    _shipped_contract(layout, 7)
    _stamp(layout, {"contract": 7, "ended": 1789930000.0, "questions": {"hf_token": "answered"}})
    assert launcher.setup_needed(layout) is False, "a finished sitting, so only `--setup` opens it"

    run = _drive_first_run(
        layout, tmp_path, monkeypatch,
        answers={"default_tier": "max"},
        setup_command=[sys.executable, "-c", "print('setup: answers saved')"],
        force_setup=True,
    )

    assert len(run.asked) == 1
    assert run.order == ["prepare_home", "install_tools", "sync", "plan", "scribe.setup",
                         "prove", "app start"]
    assert run.planned == [False], "`--setup` asks for the whole plan, not only what was never put"
    assert run.started is True


def test_the_sequence_is_free_of_tk(layout):
    """ADR-011 and ADR-015: the launcher renders and hands over, and the
    sequencing decides nothing a front-end could. The real proof is that the
    tests above drive `first_run` end to end with no Tk root anywhere; this
    only guards the source against a `import tkinter` creeping back in."""
    import inspect

    assert "tkinter" not in inspect.getsource(launcher.first_run)


# --- the smoke walks the apply door too (TASK-089.15, criterion #12) --------------


def test_the_smoke_applies_an_empty_document_before_it_asks_for_health(
        layout, tmp_path, monkeypatch):
    """CI never walked the apply door: `--smoke` returned before the window,
    so the one thing a release could not have was proof that the answers
    reach the engine at all.

    The claim asserted is the narrow one - an empty document writes no setting
    row - and it is read off the engine's own "saved: nothing". `{}` does
    create the directories, migrate the database and write a stamp; the smoke
    is not proof that it touches nothing.
    """
    record = tmp_path / "child.json"
    _engine_records(layout, monkeypatch, record, says=("saved: nothing",))
    monkeypatch.setattr(launcher, "AppProcess", _StandInApp)
    layout.logs_dir.mkdir(parents=True)
    port = _free_port()
    server = _serve(port, {"ok": True, "app": "MyScribe"})
    try:
        code = launcher.smoke(layout, port)
    finally:
        server.shutdown()
        server.server_close()

    assert code == 0
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert seen["argv"][1:] == ["--apply-stdin"]
    assert json.loads(seen["stdin"]) == {"contract": 7, "answers": {}}
    written = (layout.logs_dir / launcher.SMOKE_LOG).read_text(encoding="utf-8")
    assert "saved: nothing" in written and "/health ok" in written


def test_a_smoke_whose_engine_wrote_a_row_fails(layout, tmp_path, monkeypatch):
    """The assertion has to be able to fail, or it is decoration: an engine
    that answers `{}` by writing something is what this looks for."""
    _engine_records(layout, monkeypatch, tmp_path / "child.json", says=("saved: llm_provider",))
    monkeypatch.setattr(launcher, "AppProcess", _StandInApp)
    layout.logs_dir.mkdir(parents=True)

    assert launcher.smoke(layout, _free_port()) == 1
    assert "may have been written" in (layout.logs_dir / launcher.SMOKE_LOG).read_text(encoding="utf-8")


def test_a_failed_smoke_is_printed_by_the_build(tmp_path, capsys):
    """The Windows binary is built windowed and has no console, so its `print`
    returns without a word: a failed smoke there said only "exit 1" until the
    launcher started keeping this file."""
    sys.path.insert(0, str(LAUNCHER_PATH.parent.parent))
    try:
        import build_release
    finally:
        sys.path.pop(0)
    home = tmp_path / "smoke-home"
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "launcher-smoke.log").write_text("smoke: /health ok\n", encoding="utf-8")

    printed = build_release.smoke_log(home)

    assert "smoke: /health ok" in printed
    assert "not written" in build_release.smoke_log(tmp_path / "never-made")


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


def test_a_start_at_login_leaves_a_due_sitting_for_the_next_start_by_hand(layout):
    """Nobody is at the screen at login, and a modal that holds up the watch
    folders is the opposite of what the login entry is for. A sitting that is
    due is not cancelled - it waits for the next start somebody makes by hand.

    `--setup` still wins over `--at-login`: somebody typed it.
    """
    _shipped_contract(layout, 7)

    assert launcher.wants_setup(layout) is True
    assert launcher.wants_setup(layout, at_login=True) is False
    assert launcher.wants_setup(layout, force=True, at_login=True) is True

    _stamp(layout, {"contract": 7, "ended": 1789930000.0, "questions": {"hf_token": "skipped"}})

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

    def _capture(layout, port, open_browser, force_setup=False, at_login=False, **_window_only):
        seen.update(open_browser=open_browser, force_setup=force_setup, at_login=at_login)
        return 0

    monkeypatch.setattr(launcher, "run_window", _capture)
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)  # the window door, on any runner

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
    # Its own pid as well: on Windows the app's parent is uv's trampoline
    # python.exe, so the app cannot find the launcher by asking (TASK-117).
    assert env["MYSCRIBE_LAUNCHER_PID"] == str(os.getpid())


def test_a_launcher_that_is_not_frozen_names_no_path_at_all(layout):
    """Running from the source tree, `sys.executable` is a python in somebody's
    venv, not a launcher. Registering that would be the stale-environment bug;
    no variable at all is what makes the app fall back to the clone's own
    start script."""
    assert launcher.this_launcher() is None
    env = launcher.app_environment(layout, {"PATH": "/usr/bin"})
    assert "MYSCRIBE_LAUNCHER" not in env and "MYSCRIBE_LAUNCHER_PID" not in env


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


# --- where everything goes, and whether it fits (TASK-089.14) ----------------------


def _usage(free_gb: float):
    """A `shutil.disk_usage` answer with `free_gb` free.

    The same private constructor `tests/conftest.py` builds its `_plenty_of_disk`
    from; a test that patches `shutil.disk_usage` itself wins over that autouse
    fixture, because monkeypatch applies in order.
    """
    total = int(500 * 2**30)
    free = int(free_gb * 2**30)
    return shutil._ntuple_diskusage(total=total, used=total - free, free=free)


def _shipped_install_numbers(layout, *, environment_gb=4.0, weights_bytes=2 * 2**30, floor_gb=10) -> None:
    """The three files the total is read out of, small and explicit.

    The real `scribe/models.json` and the real floor have their own drift
    tests; these are about the arithmetic and the refusal.
    """
    scribe = layout.app_dir / "scribe"
    scribe.mkdir(parents=True, exist_ok=True)
    (scribe / "footprint.json").write_text(json.dumps({
        "environment": {
            key: {"download_gb": environment_gb, "unpacked_gb": environment_gb,
                  "measured": "estimate", "date": "2026-09-22", "source": "a test"}
            for key in ("win32", "darwin", "linux")
        },
        "ollama": {"installer_bytes": {"win32": 2**30, "darwin": 2**28, "linux": None},
                   "model_bytes": 3 * 2**30, "model": "qwen3.5:4b",
                   "measured": "read from the release page", "date": "2026-09-20", "source": "a test"},
    }), encoding="utf-8")
    (scribe / "models.json").write_text(json.dumps({
        "acme/weights": {"revision": "x", "tier": "turbo", "license": "mit", "credit": "",
                         "files": {"model.bin": {"sha256": "x", "size": weights_bytes}}},
    }), encoding="utf-8")
    (scribe / "doctor.py").write_text(f"DISK_FLOOR_GB = {floor_gb}\n", encoding="utf-8")


class _StandInApp:
    """An app process that starts, answers and stops without existing."""

    def __init__(self, _layout, _port, _base=None):
        self.proc = object()

    def start(self):
        pass

    def wait_ready(self, timeout=None):
        return True

    def alive(self):
        return True

    def stop(self, timeout=None):
        pass


def test_too_little_room_refuses_the_sync_with_both_numbers(layout, tmp_path, monkeypatch):
    """Red first (AC #6): today the sync starts anyway.

    The first start downloads gigabytes into a home the user chose; a volume
    that cannot hold the install plus the 10 GB the app keeps free is a failure
    that should be a sentence before the download, not a disk-full in the
    middle of it.

    Nothing under the home either, which is the second red: `install_tools`
    copies uv, ffmpeg and ffprobe - hundreds of MB - and a volume that is
    genuinely out of room answers that with an ENOSPC nobody wrote a sentence
    for. The refusal names the nearest folder that exists, because on a first
    run the home does not yet.
    """
    _shipped_install_numbers(layout)
    marker = tmp_path / "uv-ran.txt"
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 0, marker))
    monkeypatch.setattr(launcher.shutil, "disk_usage", lambda _path: _usage(3))
    reports: list[tuple[str, str]] = []

    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    prepared = launch.prepare()

    assert prepared is False
    assert not marker.exists(), "the download was started on a volume that cannot hold it"
    assert not layout.home.exists(), "the tools were unpacked into a home that cannot hold them"
    errors = [text for state, text in reports if state == "error"]
    assert len(errors) == 1
    assert "16" in errors[0] and "3.0 GB" in errors[0]  # needed, and free
    assert str(launcher.disk_probe_path(layout)) in errors[0]


def test_the_location_is_asked_before_anything_is_written_or_downloaded(layout, tmp_path, monkeypatch):
    """Red first (AC #1): today nothing asks, and everything lands on the
    system volume.

    The assertion that matters sits inside the asker: at the moment the
    question is put, neither home exists on disk and the fake uv has not run.
    Asserting afterwards would prove nothing about the order.
    """
    default = tmp_path / "default-home"
    chosen = tmp_path / "second-drive" / "MyScribe"
    pointer = tmp_path / "default-home.location"
    marker = tmp_path / "uv-ran.txt"
    monkeypatch.delenv(launcher.HOME_VARIABLE, raising=False)
    # Belt as well as braces, and the belt is the one that holds on a red run:
    # patching `default_home` fences nothing while `default_home` is the name
    # the change is about to add, and the first version of this test wrote a
    # whole home into this machine's real %LOCALAPPDATA%. LOCALAPPDATA needs no
    # seam. It is Windows-only - macOS's default home has no such hook - and
    # this is the platform the test runs main() on.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(launcher, "default_home", lambda *a, **k: default, raising=False)
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: pointer, raising=False)
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 0, marker))
    monkeypatch.setattr(launcher, "AppProcess", _StandInApp)
    asked: list[str] = []

    def ask(message: str):
        asked.append(message)
        assert not default.exists(), "the default home was written under before the question"
        assert not chosen.exists(), "the chosen home was written under before the question"
        assert not marker.exists(), "the download had already started"
        return str(chosen)

    monkeypatch.setattr(launcher, "ask_location", ask, raising=False)

    def window(layout_, port, open_browser, force_setup=False, at_login=False, **_window_only):
        """`run_window` without Tk: the same worker sequence it starts."""
        launch = launcher.Launch(layout_, port, open_browser, lambda s, t: None)
        return 0 if launcher.first_run(launch, lambda: None, launch.report) else 1

    monkeypatch.setattr(launcher, "run_window", window)
    # The window door is the subject, so the test says there is one. Left to
    # the machine, a Linux runner without Tk took the console door, which
    # this test never replaced, and waited on the stand-in app for good
    # (TASK-089.24: the first CI run of this file on Linux hung here).
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)

    code = launcher.main(["--payload", str(layout.payload), "--port", str(_free_port()), "--no-browser"])

    assert code == 0
    assert len(asked) == 1
    assert marker.exists(), "the sync never ran, so this proves nothing about the order"
    assert (chosen / "env" / launcher.STAMP_NAME).exists()  # the sync ran on the chosen home
    assert not default.exists(), "the default home was used after all"
    assert json.loads(pointer.read_text(encoding="utf-8"))["home"] == str(chosen.resolve())


def _door(tmp_path, payload, *, answers=(), pointer=None, install=None, environ=None, ask=True):
    """`locate_home` as one of the doors, with a home, a pointer and an
    environment of its own.

    Windows is the platform throughout, because the default home is then
    `LOCALAPPDATA` and a test can put that under `tmp_path`; asking on the real
    default home would read whatever this machine has in it. The volume list
    is empty so that the question is about the arithmetic and not about the
    drives that happen to be in this machine.
    """
    environ = {"LOCALAPPDATA": str(tmp_path / "local")} if environ is None else environ
    pointer = (tmp_path / "local" / "MyScribe.location") if pointer is None else pointer
    reports: list[tuple[str, str]] = []
    asked: list[str] = []
    queued = list(answers)

    def asker(message: str):
        asked.append(message)
        assert queued, "the question was asked more often than this test has answers"
        return queued.pop(0)

    home = launcher.locate_home(
        None, asker if ask else None, lambda state, text: reports.append((state, text)),
        payload, platform="win32", environ=environ, pointer=pointer, install=install, volumes=[],
    )
    return home, reports, asked


# --- the numbers come out of the payload (AC #2, AC #3) ----------------------------


def test_the_weights_are_the_catalogue_s_own_bytes_per_platform(layout):
    """The launcher's total and `models.wanted_here` cannot drift apart: the
    payload here is the real `scribe/models.json`, copied the way a build
    copies it, and the bytes are added up per platform on both sides.

    The app is imported here and nowhere near the launcher: ADR-011 keeps the
    launcher stdlib-only, and a drift test is the one place allowed to hold
    both - the licence `test_the_contract_number_is_read_out_of_the_engine_that_ships`
    already takes.
    """
    from scribe import models

    catalogue = Path(models.__file__).resolve().parent / "models.json"
    (layout.app_dir / "scribe" / "models.json").write_text(
        catalogue.read_text(encoding="utf-8"), encoding="utf-8")

    def wanted(backend: str) -> int:
        return sum(m.bytes_total for m in models.wanted_here(backend=backend, tier=models.DEFAULT_TIER))

    assert launcher.weights_bytes(layout, "win32", "AMD64") == wanted(models.NOT_MLX)
    assert launcher.weights_bytes(layout, "linux", "x86_64") == wanted(models.NOT_MLX)
    assert launcher.weights_bytes(layout, "darwin", "arm64") == wanted("mlx")
    # An Intel Mac loads no mlx conversion, which is what `mlx_here` is for.
    assert launcher.weights_bytes(layout, "darwin", "x86_64") == wanted(models.NOT_MLX)
    assert wanted("mlx") != wanted(models.NOT_MLX), "both platforms fetch the same bytes; this proves nothing"


def test_the_floor_is_the_one_the_app_itself_enforces(layout):
    """The 10 GB the question adds to the total is not a second number: it is
    read as text out of the `doctor.py` that ships, so moving it moves both."""
    from scribe import doctor

    (layout.app_dir / "scribe" / "doctor.py").write_text(
        Path(doctor.__file__).resolve().read_text(encoding="utf-8"), encoding="utf-8")

    assert launcher.disk_floor_gb(layout) == doctor.DISK_FLOOR_GB


def test_a_payload_without_the_numbers_still_asks(layout, tmp_path):
    """`footprint.json` is new, and a payload built before it was committed
    does not carry it. A question that cannot add up is still worth asking: it
    says which part it could not read, and nothing is refused on a number
    nobody could find."""
    text = launcher.location_question(layout, tmp_path / "MyScribe.location", volumes=[], platform="win32")

    assert "nobody has measured" in text
    assert launcher.install_size(layout, "win32", "AMD64")["needed_gb"] == 0
    assert launcher.enough_disk(layout, lambda state, said: pytest.fail(f"refused: {said}")) is True


def test_the_question_shows_the_default_the_total_and_every_volume(layout, tmp_path, monkeypatch):
    """What the question is for: where it would go, what it costs, and what
    each disk has - on a machine with two volumes, which is the case this
    whole task exists for (AC #2)."""
    _shipped_install_numbers(layout)
    free = {Path("C:/"): 9.4, Path("D:/"): 412.0}
    # Any other disk - the one the default home is on, which is C: only on
    # Windows and "/" on the macOS runner (TASK-089.24) - has plenty.
    monkeypatch.setattr(launcher.shutil, "disk_usage", lambda path: _usage(free.get(Path(path), 500.0)))

    text = launcher.location_question(
        layout, tmp_path / "MyScribe.location", volumes=[Path("C:/"), Path("D:/")],
        platform="win32", machine="AMD64")

    assert str(layout.home) in text
    assert "9.4 GB free" in text and "412.0 GB free" in text
    assert "2.0 GB of speech models" in text and "measured" in text
    assert "about 4.0 GB of speech engine" in text
    assert "about 16 GB free" in text and "10 GB" in text  # the install, and the floor it adds
    assert "4.0 GB more if you later say yes to Ollama" in text and "qwen3.5:4b" in text


def test_the_question_always_shows_the_disk_the_default_home_is_on(layout, tmp_path, monkeypatch):
    """Whatever the enumerator found - nothing at all, on a platform where it
    is nobody's measurement - the question still says what the disk it is
    about to fill has left."""
    monkeypatch.setattr(launcher.shutil, "disk_usage", lambda _path: _usage(77.0))

    text = launcher.location_question(layout, tmp_path / "MyScribe.location",
                                      volumes=[], platform="win32")

    assert f"{Path(layout.home.anchor)}  77.0 GB free" in text


def test_the_question_says_where_the_answer_can_be_given_later(layout, tmp_path):
    """There is no Settings page for this one, and the text says so with its
    reason: the folder holds the environment the running app runs from (AC #10)."""
    pointer = tmp_path / "MyScribe.location"

    text = launcher.location_question(layout, pointer, volumes=[], platform="win32")

    assert "MYSCRIBE_HOME" in text and "--home PATH" in text and str(pointer) in text
    assert "no Settings page" in text and "runs from" in text
    assert "does not move an install that is already there" in text
    assert "network share" in text and "mapped drive letter" in text


def test_setup_prints_the_home_in_force_and_where_it_came_from(layout, tmp_path, monkeypatch, capsys):
    """`--setup` is where somebody looks after the fact (AC #10)."""
    monkeypatch.setattr(launcher, "run_window", lambda *args, **kwargs: 0)
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)  # the window door, on any runner
    home = tmp_path / "chosen"

    assert launcher.main(["--setup", "--home", str(home), "--payload", str(layout.payload)]) == 0

    printed = capsys.readouterr().out
    assert str(home) in printed and "the --home option" in printed
    assert "MYSCRIBE_HOME" in printed and "--home PATH" in printed and "no Settings page" in printed


def test_every_source_of_the_home_can_be_named(tmp_path):
    """`--setup` says which of the four it was, and `home_dir` keeps returning
    a plain Path - two tests above compare it to one."""
    pointer = tmp_path / "MyScribe.location"
    environ = {"LOCALAPPDATA": str(tmp_path / "local")}

    assert launcher.home_source(tmp_path / "typed", "win32", environ, pointer) == "the --home option"
    assert launcher.home_source(None, "win32", {**environ, "MYSCRIBE_HOME": "x"}, pointer) == (
        "the MYSCRIBE_HOME variable")
    # Both at once, or the order of the two branches is free: `locate_home`
    # takes `--home` first, and a `--setup` that named the other one would be
    # a wrong sentence on the screen somebody reads when they are lost.
    assert launcher.home_source(tmp_path / "typed", "win32", {**environ, "MYSCRIBE_HOME": "x"},
                                pointer) == "the --home option"
    assert launcher.home_source(None, "win32", environ, pointer) == "the default for this computer"
    pointer.write_text(json.dumps({"home": str(tmp_path / "pointed")}), encoding="utf-8")
    assert launcher.home_source(None, "win32", environ, pointer) == f"the pointer file {pointer}"


def test_the_windows_welcome_text_carries_no_size_in_gigabytes():
    """The installer's welcome text cannot read `footprint.json`, so a number
    in it is one that nobody updates - and it already disagreed with the only
    dated estimate there is. It is gone, and this keeps it gone (AC #2)."""
    text = (LAUNCHER_PATH.parent.parent / "windows" / "myscribe.iss").read_text(encoding="utf-8-sig")
    welcome = [line for line in text.splitlines() if line.startswith("WelcomeLabel2=")]

    assert welcome, "WelcomeLabel2 is gone; the welcome text this test is about has moved"
    assert not re.search(r"\d+([.,]\d+)?\s*GB", welcome[0])


# --- the pointer file (AC #4) ------------------------------------------------------


def test_no_pointer_file_is_the_default_home(tmp_path):
    assert launcher.home_dir("win32", {"LOCALAPPDATA": str(tmp_path)},
                             pointer=tmp_path / "nothing.location") == tmp_path / "MyScribe"


def test_a_pointer_file_moves_the_home(tmp_path):
    pointer = tmp_path / "MyScribe.location"
    pointer.write_text(json.dumps({"home": r"D:\MyScribe", "data": r"E:\Library"}), encoding="utf-8")

    # The second key is not this task's, and nothing here pins that the file
    # holds one fact: TASK-089.19 may add one (ADR-015, Open Questions).
    assert launcher.home_dir("win32", {"LOCALAPPDATA": str(tmp_path)}, pointer=pointer) == Path(r"D:\MyScribe")


def test_the_pointer_sits_beside_the_default_home_and_never_inside_it(tmp_path):
    """Beside it, so a home somebody moved leaves nothing at all under the
    default one - an empty MyScribe folder on C: is exactly the "did it
    install twice?" that moving it was meant to avoid."""
    default = launcher.default_home("win32", {"LOCALAPPDATA": str(tmp_path)})

    assert default == tmp_path / "MyScribe"
    assert default.with_name(default.name + ".location") == tmp_path / "MyScribe.location"


def test_the_variable_and_the_option_still_outrank_the_pointer(tmp_path, layout):
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"home": str(tmp_path / "pointed")}), encoding="utf-8")
    environ = {"LOCALAPPDATA": str(tmp_path / "local")}

    assert launcher.home_dir("win32", environ, pointer=pointer) == tmp_path / "pointed"
    assert launcher.home_dir("win32", {**environ, "MYSCRIBE_HOME": str(tmp_path / "variable")},
                             pointer=pointer) == tmp_path / "variable"
    home, _reports, asked = _door(tmp_path, layout.payload, pointer=pointer,
                                  environ={**environ, "MYSCRIBE_HOME": str(tmp_path / "variable")})
    assert home == tmp_path / "variable" and asked == []


def test_the_home_help_text_reads_the_pointer_a_test_can_move(tmp_path, monkeypatch):
    """`build_parser()` asks `home_dir()` for the `--home` help text before any
    argument is read. The path comes from the module-level `pointer_path`, and
    that is what lets an autouse fixture keep this file away from the pointer
    of whoever is running it (spec section 3.13)."""
    pointer = tmp_path / "MyScribe.location"
    pointer.write_text(json.dumps({"home": str(tmp_path / "elsewhere")}), encoding="utf-8")
    monkeypatch.setattr(launcher, "pointer_path", lambda *args, **kwargs: pointer)

    # Whitespace out of both sides: argparse wraps the help column with
    # textwrap's defaults, which break a long path in the middle.
    printed = "".join(launcher.build_parser().format_help().split())
    assert "".join(str(tmp_path / "elsewhere").split()) in printed


def test_a_pointer_that_is_not_a_json_object_says_so_and_asks_again(tmp_path, layout):
    """Never a silent fall-back to the default: that would open an empty
    library and look to its owner as though the recordings were gone (AC #4)."""
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("[]", encoding="utf-8")
    chosen = tmp_path / "second-drive" / "MyScribe"

    home, reports, asked = _door(tmp_path, layout.payload, answers=[str(chosen)], pointer=pointer)

    assert home == chosen.resolve()
    assert len(asked) == 1 and str(pointer) in asked[0]
    assert [state for state, _ in reports] == ["error"]
    assert "does not hold a JSON object" in reports[0][1]


def test_a_pointer_that_cannot_be_read_asks_again_and_a_failed_save_says_so(tmp_path, layout):
    """A file that is there and unreadable - a permission, a half-written file,
    a folder where the file should be - is a problem and never an absence.

    Driven through the door and not against `read_pointer`, because the
    interesting half is what happens after the answer: a pointer that cannot
    be read is usually one that cannot be written either, and the save happens
    moments after the person answered. A raise there would kill a `--windowed`
    build with no console and no window, and lose the answer as well. It says
    so and carries on with the folder they chose (AC #4)."""
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.mkdir(parents=True)  # a folder where the file should be
    chosen = tmp_path / "second-drive" / "MyScribe"

    home, reports, asked = _door(tmp_path, layout.payload, answers=[str(chosen)], pointer=pointer)

    assert home == chosen.resolve()
    assert len(asked) == 1 and str(pointer) in asked[0]
    errors = [text for state, text in reports if state == "error"]
    assert "cannot be read" in errors[0]
    assert str(pointer) in errors[1] and "could not be saved" in errors[1]


def test_a_pointer_that_names_no_home_is_still_a_first_run(tmp_path, layout):
    """The file is there and it answers a different question: TASK-089.19 may
    write its own fact into it before anybody was ever asked where everything
    goes. What settles it is whether a home is named, never whether the file
    exists - the trap this criterion wrote itself against (AC #4)."""
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"asked": "2026-09-22"}), encoding="utf-8")
    default = tmp_path / "local" / "MyScribe"
    chosen = tmp_path / "second-drive" / "MyScribe"

    assert launcher.wants_location(default, layout.payload, pointer) is True
    home, _reports, asked = _door(tmp_path, layout.payload, answers=[str(chosen)], pointer=pointer)

    assert home == chosen.resolve() and len(asked) == 1


def test_a_key_the_launcher_did_not_write_survives_the_answer(tmp_path, layout):
    """TASK-089.19 criterion 8 may add a second fact to this file, and the
    writer merges rather than overwrites so that it is not the one that loses
    it (AC #4). The read side is covered where the pointer moves the home;
    this is the write side."""
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"home": str(tmp_path / "gone"), "asked": "2026-09-22"}),
                       encoding="utf-8")
    chosen = tmp_path / "second-drive" / "MyScribe"

    home, _reports, asked = _door(tmp_path, layout.payload, answers=[str(chosen)], pointer=pointer)

    assert home == chosen.resolve() and len(asked) == 1
    assert json.loads(pointer.read_text(encoding="utf-8")) == {
        "home": str(chosen.resolve()), "asked": "2026-09-22"}


def test_a_pointer_naming_a_folder_that_is_gone_asks_again(tmp_path, layout):
    """An unplugged drive. One sentence, two ways on - plug it in, or say where
    everything goes now - and never the default (AC #4)."""
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"home": r"D:\gone"}), encoding="utf-8")
    chosen = tmp_path / "second-drive" / "MyScribe"

    home, reports, asked = _door(tmp_path, layout.payload, answers=[str(chosen)], pointer=pointer)

    assert home == chosen.resolve()
    assert "not there" in reports[0][1] and "nothing is lost" in reports[0][1]
    assert r"D:\gone" in asked[0]
    assert json.loads(pointer.read_text(encoding="utf-8"))["home"] == str(chosen.resolve())


def test_a_pointer_that_is_gone_stops_a_door_that_cannot_ask(tmp_path, layout):
    """At login, on the console and under `--sync-only` there is nobody to ask,
    and starting a second, empty library on C: is the one answer that must not
    happen. It stops, and says why (AC #4)."""
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"home": r"D:\gone"}), encoding="utf-8")

    home, reports, asked = _door(tmp_path, layout.payload, pointer=pointer, ask=False)

    assert home is None and asked == []
    assert "cannot start" in reports[-1][1] and "second, empty library" in reports[-1][1]


def test_closing_the_question_over_a_broken_pointer_starts_nothing(tmp_path, layout):
    """Skipping means "keep today's location", and today's location is the one
    the pointer names. With that folder gone there is nothing to keep."""
    pointer = tmp_path / "local" / "MyScribe.location"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"home": r"D:\gone"}), encoding="utf-8")

    home, reports, asked = _door(tmp_path, layout.payload, answers=[None], pointer=pointer)

    assert home is None and len(asked) == 1
    assert "still names a folder that is not there" in reports[-1][1]


# --- asked once, and never about an install that is already there (AC #5, #7) ------


def test_skipping_keeps_todays_location_and_writes_no_pointer(tmp_path, layout):
    home, reports, asked = _door(tmp_path, layout.payload, answers=[None])

    assert home == tmp_path / "local" / "MyScribe"
    assert not (tmp_path / "local" / "MyScribe.location").exists()
    assert len(asked) == 1
    assert not [state for state, _ in reports if state == "error"]


def test_a_home_that_already_holds_an_environment_is_never_asked_about(tmp_path, layout):
    """Moving an install is out of scope, so the question is not asked where
    one is: it would be a promise this code does not keep (AC #5, AC #7)."""
    default = tmp_path / "local" / "MyScribe"
    installed = launcher.Layout(default, layout.payload)
    installed.env_python.parent.mkdir(parents=True)
    installed.env_python.write_text("", encoding="utf-8")
    pointer = tmp_path / "local" / "MyScribe.location"

    assert launcher.wants_location(default, layout.payload, pointer) is False
    home, _reports, asked = _door(tmp_path, layout.payload)
    assert home == default and asked == []


def test_a_first_run_with_nothing_anywhere_is_the_one_case_that_asks(tmp_path, layout):
    pointer = tmp_path / "local" / "MyScribe.location"
    default = tmp_path / "local" / "MyScribe"

    assert launcher.wants_location(default, layout.payload, pointer) is True
    assert launcher.wants_location(default, layout.payload, pointer, home_flag=Path("x")) is False
    assert launcher.wants_location(default, layout.payload, pointer, environ={"MYSCRIBE_HOME": "x"}) is False


# --- what a folder may not be (AC #9) ----------------------------------------------


def test_a_relative_path_is_refused(tmp_path):
    refusal = launcher.refuse_location("MyScribe", install=None, payload=tmp_path / "payload")

    assert "not a full path" in refusal


def test_a_path_that_climbs_back_into_the_install_directory_is_refused(tmp_path):
    """`..` is why both sides are resolved before they are compared: the string
    a person typed says nothing about where it lands."""
    install = tmp_path / "Programs" / "MyScribe"
    install.mkdir(parents=True)

    refusal = launcher.refuse_location(str(install / "sub" / ".." / "data"), install=install)

    assert "belongs to the installer" in refusal
    assert not (install / "data").exists(), "a refused folder was created anyway"


def test_a_folder_whose_name_only_starts_the_same_is_not_refused(tmp_path):
    """`D:\\MyScribeData` is not inside `D:\\MyScribe`. Compared part by part
    and never as strings, which is the bug this test exists to stop."""
    install = tmp_path / "Programs" / "MyScribe"
    install.mkdir(parents=True)

    assert launcher.refuse_location(str(tmp_path / "Programs" / "MyScribeData"), install=install) == ""


def test_a_link_into_the_install_directory_is_refused_too(tmp_path):
    """`resolve()` follows it, which is the only reason this one is covered. A
    junction whose target is moved *after* the check is not, and neither is a
    case-insensitive network mount.

    A plain symlink needs a privilege Windows does not hand out by default
    (WinError 1314 here on 2026-09-22), so this machine proves the point with
    an NTFS junction instead, which any account may create and `resolve()`
    follows the same way. Elsewhere the symlink is the real thing.
    """
    import subprocess

    install = tmp_path / "Programs" / "MyScribe"
    install.mkdir(parents=True)
    link = tmp_path / "looks-innocent"
    try:
        link.symlink_to(install, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        if not sys.platform == "win32":
            pytest.skip(f"this machine cannot create a symlink: {error}")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(install)],
                              capture_output=True, text=True)
        if made.returncode != 0 or not link.exists():
            pytest.skip(f"this machine makes neither a symlink nor a junction: {made.stderr.strip()}")

    # Through the link and one folder down, so this fails both when the link
    # is not followed and when "inside" is read as "is".
    assert "belongs to the installer" in launcher.refuse_location(str(link / "data"), install=install)
    assert not (install / "data").exists(), "a refused folder was created anyway"


def test_a_folder_inside_the_install_directory_is_refused(tmp_path):
    """ADR-011's Must Not, and on Windows the installer owns that folder: the
    next uninstall would take the library with it, while the installer's own
    welcome text promises it is left alone."""
    install = tmp_path / "Programs" / "MyScribe"
    install.mkdir(parents=True)

    refusal = launcher.refuse_location(str(install / "data"), install=install)

    assert "belongs to the installer" in refusal and "uninstall" in refusal


def test_a_folder_inside_the_payload_is_refused(tmp_path):
    payload = tmp_path / "Programs" / "MyScribe" / "payload"
    payload.mkdir(parents=True)

    assert "belongs to the installer" in launcher.refuse_location(str(payload / "home"), payload=payload)


def test_a_unc_path_is_refused_with_the_reason_that_a_mapped_drive_hides(tmp_path):
    """The library is SQLite in WAL mode (ADR-013), and SQLite's own
    documentation says WAL does not work over a network filesystem. Nothing on
    the network is touched to find that out - the refusal is made before any
    filesystem call, which is why it comes before `resolve()`."""
    refusal = launcher.refuse_location(r"\\server\share\MyScribe", install=None, payload=None)

    assert "network share" in refusal and "write-ahead log" in refusal
    assert "mapped drive letter" in refusal and "cannot tell one from a real disk" in refusal
    assert "network share" in launcher.refuse_location("//server/share/MyScribe")


def test_a_folder_that_is_not_there_yet_is_accepted_and_not_created(tmp_path):
    """The check creates nothing. A path with a typo in it is either refused or
    corrected, and either way it must not leave an empty tree behind on a disk
    the person never meant to touch - written while the question is still
    open, which is the worst moment to be writing anything."""
    typo = tmp_path / "MyScrbie" / "MyScribe"

    assert launcher.refuse_location(str(typo)) == ""
    assert not (tmp_path / "MyScrbie").exists()


def test_a_folder_that_cannot_be_written_is_refused(tmp_path):
    """Refused with what the operating system said, because "choose another
    folder" without a reason is the sort of dialog people photograph."""
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")

    refusal = launcher.refuse_location(str(blocker / "MyScribe"))

    assert "cannot write in" in refusal


def test_a_refused_answer_writes_no_pointer_and_asks_again(tmp_path, layout):
    """One sentence, and the question comes back with it on top (AC #9)."""
    install = tmp_path / "Programs" / "MyScribe"
    install.mkdir(parents=True)
    pointer = tmp_path / "local" / "MyScribe.location"
    chosen = tmp_path / "second-drive" / "MyScribe"

    home, reports, asked = _door(tmp_path, layout.payload, install=install,
                                 answers=[str(install / "data"), str(chosen)])

    assert home == chosen.resolve()
    assert len(asked) == 2 and "belongs to the installer" in asked[1]
    assert [state for state, _ in reports] == ["error"]
    assert json.loads(pointer.read_text(encoding="utf-8")) == {"home": str(chosen.resolve())}


def test_no_pointer_is_written_while_the_question_is_still_open(tmp_path, layout):
    """Only an asker that looks between the two answers can see this: the
    second, accepted answer overwrites a pointer the first one should never
    have left, so the file at the end says nothing about the file in between.

    What it guards is not tidiness. Refuse `<install>\\data`, then close the
    window: the home falls back to the default while the pointer names the
    folder ADR-011 forbids, and the next start puts `env/` and `data/` inside
    the directory the next uninstall deletes (AC #9)."""
    install = tmp_path / "Programs" / "MyScribe"
    install.mkdir(parents=True)
    pointer = tmp_path / "local" / "MyScribe.location"
    chosen = tmp_path / "second-drive" / "MyScribe"
    answers = [str(install / "data"), str(chosen)]
    seen: list[bool] = []

    def asker(_message: str):
        seen.append(pointer.exists())
        return answers.pop(0)

    home = launcher.locate_home(
        None, asker, lambda state, text: None, layout.payload, platform="win32",
        environ={"LOCALAPPDATA": str(tmp_path / "local")}, pointer=pointer, install=install,
        volumes=[],
    )

    assert home == chosen.resolve()
    assert seen == [False, False], "a refused answer was written to the pointer file"


# --- the free-space check on the doors that ask nothing (AC #6) --------------------


def _headless_machine(layout, tmp_path, monkeypatch, *, free_gb: float) -> Path:
    """A machine with no pointer, no environment and `free_gb` free."""
    _shipped_install_numbers(layout)
    marker = tmp_path / "uv-ran.txt"
    monkeypatch.delenv(launcher.HOME_VARIABLE, raising=False)
    monkeypatch.setattr(launcher, "default_home", lambda *a, **k: tmp_path / "default")
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: tmp_path / "default.location")
    monkeypatch.setattr(launcher, "uv_command", lambda _layout: _fake_uv(tmp_path, 0, marker))
    monkeypatch.setattr(launcher.shutil, "disk_usage", lambda _path: _usage(free_gb))
    return marker


def test_a_console_start_asks_nothing_says_so_and_is_still_refused_the_sync(
        layout, tmp_path, monkeypatch, capsys):
    """Nobody is at the screen, so nothing is asked - and the check still runs,
    because the volume does not care who started the launcher (AC #6)."""
    marker = _headless_machine(layout, tmp_path, monkeypatch, free_gb=3)

    code = launcher.main(["--headless", "--no-browser", "--payload", str(layout.payload),
                          "--port", str(_free_port())])

    printed = capsys.readouterr().out
    assert code == 0, "a full volume is a configuration problem, not a crash (TASK-112.02)"
    assert not marker.exists(), "the download was started on a volume that cannot hold it"
    assert "nothing was asked" in printed and "MYSCRIBE_HOME" in printed
    assert "not enough room" in printed and "3.0 GB free" in printed


def test_sync_only_is_refused_the_same_way(layout, tmp_path, monkeypatch, capsys):
    """The one-job doors go through the same check: `--sync-only` is exactly
    the download this refuses (AC #6)."""
    marker = _headless_machine(layout, tmp_path, monkeypatch, free_gb=3)

    code = launcher.main(["--sync-only", "--payload", str(layout.payload)])

    assert code == 1 and not marker.exists()
    assert "not enough room" in capsys.readouterr().out


def test_enough_room_lets_the_sync_start(layout, tmp_path, monkeypatch, capsys):
    """The other half of the refusal: with room, nothing is in the way - a
    check that refused everything would pass the test above as well."""
    marker = _headless_machine(layout, tmp_path, monkeypatch, free_gb=400)

    code = launcher.main(["--sync-only", "--payload", str(layout.payload)])

    assert code == 0 and marker.exists()
    assert "not enough room" not in capsys.readouterr().out


def test_a_volume_that_cannot_be_measured_does_not_refuse(layout, monkeypatch):
    """"We do not know" must not become "nothing may be installed here": the
    rule `doctor.require_disk_headroom` already follows, stated in
    `enough_disk` and until now held by nothing."""
    _shipped_install_numbers(layout)

    def unmeasurable(_path):
        raise OSError(1, "no such device")

    monkeypatch.setattr(launcher.shutil, "disk_usage", unmeasurable)
    reports: list[tuple[str, str]] = []

    assert launcher.enough_disk(layout, lambda state, text: reports.append((state, text))) is True
    assert reports == []


def test_the_working_room_is_asked_of_a_person_and_not_of_ci(layout, monkeypatch):
    """`--smoke`, `--sync-only` and `--doctor` install and then stop, so the
    room MyScribe keeps free to RUN with a library is not theirs to demand.

    The numbers are the ones that made this a decision rather than a tidy-up:
    on Windows the floor is 10 of the 16.6 GB needed, and a hosted Windows
    runner arrives with less than that with no step to clear any (the Linux
    job has one; Windows and macOS do not). Robert chose this on 2026-09-22.

    Both directions in one test on purpose: a change that drops the floor for
    everybody passes the CI half and fails the person's half here.
    """
    _shipped_install_numbers(layout)
    size = launcher.install_size(layout, "win32", "AMD64")
    assert size["floor_gb"] > 0, "a floor of zero would make this test vacuous"
    # Between the two: enough for the install, not enough for the working room.
    between = size["needed_gb"] - size["floor_gb"] / 2
    monkeypatch.setattr(launcher.shutil, "disk_usage", lambda _path: _usage(between))

    refused: list[str] = []
    person = launcher.enough_disk(layout, lambda state, text: refused.append(text))
    ci = launcher.enough_disk(layout, lambda state, text: refused.append(text), floor=False)

    assert person is False, "a person installing this is still asked for the working room"
    assert ci is True, "CI is asked only for the install it actually makes"
    assert len(refused) == 1 and "not enough room" in refused[0]


def test_a_footprint_whose_numbers_are_not_numbers_still_asks(layout):
    """`footprint()` promises never to raise, and the promise is worth what the
    arithmetic below it is: a hand-edited or half-written file whose
    `download_gb` is a string would otherwise be a TypeError with no console
    and no window behind it."""
    _shipped_install_numbers(layout)
    (layout.app_dir / "scribe" / "footprint.json").write_text(json.dumps({
        "environment": {"win32": {"download_gb": "3.2", "unpacked_gb": None}},
        "ollama": {"installer_bytes": {"win32": "lots"}, "model_bytes": "3 GB"},
    }), encoding="utf-8")

    size = launcher.install_size(layout, "win32", "AMD64")

    assert size["environment_gb"] is None and size["ollama_gb"] is None
    assert size["complete"] is False
    assert size["needed_gb"] == pytest.approx(size["weights_gb"] + size["floor_gb"])


def test_a_start_at_login_over_a_broken_pointer_says_so_in_a_window(layout, tmp_path, monkeypatch):
    """There is no console behind `--windowed`: `sys.stdout` is None and
    `print` returns without a word.

    The scenario is ordinary - the home is on an external drive that is not
    plugged in when the machine boots, and the login entry fires - and the one
    sentence explaining it would otherwise go nowhere at all (AC #4)."""
    pointer = tmp_path / "default.location"
    pointer.write_text(json.dumps({"home": str(tmp_path / "gone")}), encoding="utf-8")
    monkeypatch.delenv(launcher.HOME_VARIABLE, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(launcher, "default_home", lambda *a, **k: tmp_path / "default")
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: pointer)
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)
    shown: list[str] = []
    monkeypatch.setattr(launcher, "show_error", lambda text: shown.append(text), raising=False)

    code = launcher.main(["--at-login", "--no-browser", "--payload", str(layout.payload),
                          "--port", str(_free_port())])

    assert code == 0, "an unplugged drive is a configuration problem, not a crash (TASK-112.02)"
    assert len(shown) == 1
    assert "not there" in shown[0] and "cannot start" in shown[0]
    assert "not there" in (tmp_path / "default-launcher.log").read_text(encoding="utf-8")


def test_the_volume_measured_is_the_one_the_doctor_measures(tmp_path, layout):
    """The launcher's refusal and the app's own floor must never be about two
    different drives. The doctor measures the data directory, or its parent
    while it does not exist; this walks further up, because before a first run
    the home does not exist either and `disk_usage` raises on a path that is
    not there."""
    deep = launcher.Layout(tmp_path / "not-there" / "MyScribe", layout.payload)

    assert launcher.disk_probe_path(deep) == tmp_path
    launcher.prepare_home(deep)
    assert launcher.disk_probe_path(deep) == deep.data_dir


# --- Quit while a third-party installer runs (TASK-089.18, criterion 14; ADR-017) ---------


class _SetupChild:
    """The setup child as Quit sees it: alive until terminated."""

    def __init__(self):
        self.terminated = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


def test_the_installer_event_line_is_read_and_nothing_else_is_mistaken_for_it():
    assert launcher.installer_line('{"event": "installer", "running": true}') is True
    assert launcher.installer_line('  {"event": "installer", "running": false}') is False
    assert launcher.installer_line('{"event": "progress", "repo": "x", "percent": 3}') is None
    assert launcher.installer_line('{"event": "installer"}') is None
    assert launcher.installer_line("Ollama's installer is running") is None
    assert launcher.installer_line("{not json") is None


def test_quit_is_refused_with_a_sentence_while_the_installer_runs_and_stops_the_child_afterwards(layout, monkeypatch):
    """The launcher's half of criterion 14. While the engine says the
    third-party installer is running, `stop_setup` terminates nothing and says
    why; once the engine says it has finished, the same Quit stops that one
    process, as before."""
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    child = _SetupChild()
    seen: list[tuple[bool, bool]] = []

    def run_setup(_layout, answers, on_line, on_start=None):
        on_start(child)
        on_line('{"event": "installer", "running": true}')
        assert launch.installing is True
        launch.stop_setup()
        seen.append((child.terminated, launch.installing))
        on_line("a line of prose while it runs")
        on_line('{"event": "installer", "running": false}')
        assert launch.installing is False
        launch.stop_setup()
        seen.append((child.terminated, launch.installing))
        return 0

    monkeypatch.setattr(launcher, "run_setup", run_setup)

    code, reopen = launch.apply({})

    assert code == 0 and reopen == []
    assert seen == [(False, True), (True, False)]
    refused = [text for state, text in reports if state == "status" and text == launcher.QUIT_REFUSED]
    assert refused == [launcher.QUIT_REFUSED], "one sentence, said once, when Quit was refused"
    assert "installer" in launcher.QUIT_REFUSED.lower() and "quit" in launcher.QUIT_REFUSED.lower()
    assert not any("clean" in text.lower() for _, text in reports), "nothing promises Quit is clean (M10)"
    assert not any('"event"' in text for state, text in reports if state == "busy"), "the event line is not a log line"


def test_stop_while_installing_reaches_neither_the_child_nor_the_app(layout):
    reports: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, _free_port(), False, lambda s, t: reports.append((s, t)))
    child = _SetupChild()
    launch.keep_setup_child(child)
    stopped: list[str] = []
    # An app that runs and does not answer a stop route: the hard stop (TASK-112.09).
    launch.app = types.SimpleNamespace(stop=lambda timeout=None: stopped.append("app"), alive=lambda: True)
    launch.installing = True

    assert launch.quit_refused() == launcher.QUIT_REFUSED
    launch.stop()

    assert child.terminated is False and stopped == []
    assert reports == [("status", launcher.QUIT_REFUSED)]

    launch.installing = False
    assert launch.quit_refused() == ""
    launch.stop()
    assert child.terminated is True and stopped == ["app"]


def test_the_ollama_figure_in_the_location_question_is_an_estimate_not_a_ceiling(layout, tmp_path, monkeypatch):
    """TASK-095: the offer follows Ollama's newest release, so the launcher's
    installer size is an estimate from one past release and the next one may
    be bigger. "Up to" promised a ceiling nobody can know; the sentence says
    "about" and carries footprint.json's own label."""
    _shipped_install_numbers(layout)
    monkeypatch.setattr(launcher.shutil, "disk_usage", lambda path: _usage(500.0))

    text = launcher.location_question(layout, tmp_path / "MyScribe.location", volumes=[Path("C:/")],
                                      platform="win32", machine="AMD64")

    line = next(l for l in text.splitlines() if "say yes to Ollama" in l)
    assert line.startswith("About 4.0 GB more"), line
    assert "Up to" not in text


# --- TASK-102.08: the sync-only door says what it did -----------------------------


def test_a_sync_only_run_says_it_synced_and_logs_it(layout, tmp_path, monkeypatch, capsys):
    """The outside macOS walk of 0.8.0: `--sync-only` printed only uv's own
    line, or nothing at all, and wrote no launcher.log - so a CI run of the
    shipped app left no record in the home."""
    _headless_machine(layout, tmp_path, monkeypatch, free_gb=400)

    code = launcher.main(["--sync-only", "--payload", str(layout.payload)])

    home = launcher.Layout(tmp_path / "default", layout.payload)
    assert code == 0
    assert launcher.SYNCED in capsys.readouterr().out
    assert launcher.SYNCED in (home.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8")


def test_a_sync_only_run_on_an_up_to_date_home_says_so(layout, tmp_path, monkeypatch, capsys):
    _headless_machine(layout, tmp_path, monkeypatch, free_gb=400)
    monkeypatch.setattr(launcher, "needs_sync", lambda _layout: False)
    monkeypatch.setattr(launcher, "install_tools", lambda _layout: None)

    code = launcher.main(["--sync-only", "--payload", str(layout.payload)])

    home = launcher.Layout(tmp_path / "default", layout.payload)
    assert code == 0
    assert launcher.UP_TO_DATE in capsys.readouterr().out
    assert launcher.UP_TO_DATE in (home.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8")


# --- TASK-102.03: stopping the launcher stops the app -----------------------------

import signal as _signal  # noqa: E402
import subprocess as _subprocess  # noqa: E402


class _ServingLaunch:
    """A launch whose app serves until stopped, or until a deadline so a
    test without the fix ends instead of hanging."""

    def __init__(self, deadline: float = 5.0):
        self.serving = False
        self.stopped_at: float | None = None
        until = time.monotonic() + deadline
        launch = self
        self.app = types.SimpleNamespace(
            alive=lambda: launch.stopped_at is None and time.monotonic() < until,
            exit_code=lambda: 0)

    def report(self, state, text):
        pass

    def stop(self):
        self.stopped_at = time.monotonic()


def test_a_sigterm_ends_the_headless_launcher_through_its_own_stop(monkeypatch):
    """The outside macOS walk of 0.8.0: after `pkill MyScribe` the app kept
    serving. Ctrl+C was handled; SIGTERM, which pkill sends, was not."""
    monkeypatch.setattr(launcher, "first_run", lambda *a, **k: True)
    unhandled = []
    previous = _signal.signal(_signal.SIGTERM, lambda *a: unhandled.append(a))  # keeps pytest alive
    try:
        launch = _ServingLaunch()
        threading.Timer(0.3, _signal.raise_signal, args=(_signal.SIGTERM,)).start()
        started = time.monotonic()

        assert launcher.run_headless(launch) == 0

        assert unhandled == [], "the launcher left SIGTERM to whoever handled it before"
        assert launch.stopped_at is not None and launch.stopped_at - started < 2.0
    finally:
        _signal.signal(_signal.SIGTERM, previous)


def test_the_headless_launcher_puts_back_the_sigterm_handler_it_found(monkeypatch):
    monkeypatch.setattr(launcher, "first_run", lambda *a, **k: True)
    mine = lambda *a: None  # noqa: E731
    previous = _signal.signal(_signal.SIGTERM, mine)
    try:
        launcher.run_headless(_ServingLaunch(deadline=0.2))
        assert _signal.getsignal(_signal.SIGTERM) is mine
    finally:
        _signal.signal(_signal.SIGTERM, previous)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and process groups")
def test_a_killed_launcher_takes_its_real_app_with_it(tmp_path):
    """A real launcher-side process, a real child in a session of its own (as
    `AppProcess` starts it) and a real SIGTERM, as `pkill` sends it."""
    script = tmp_path / "launcher_side.py"
    pid_file = tmp_path / "child.pid"
    script.write_text(textwrap.dedent(f"""
        import importlib.util, subprocess, sys, types
        spec = importlib.util.spec_from_file_location("myscribe_launcher", {str(LAUNCHER_PATH)!r})
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 start_new_session=True)
        open({str(pid_file)!r}, "w").write(str(child.pid))
        app = types.SimpleNamespace(alive=lambda: child.poll() is None, proc=child)
        def stop():
            launcher.AppProcess.stop(types.SimpleNamespace(proc=child), timeout=5)
        launch = types.SimpleNamespace(serving=False, app=app, report=lambda *a: None, stop=stop)
        launcher.first_run = lambda *a, **k: True
        sys.exit(launcher.run_headless(launch))
    """), encoding="utf-8")
    side = _subprocess.Popen([sys.executable, str(script)])
    deadline = time.monotonic() + 20
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    child = int(pid_file.read_text())
    time.sleep(0.5)

    side.send_signal(_signal.SIGTERM)
    side.wait(15)

    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


# --- TASK-107.05: a browser that does not open is said ---------------------------


def test_opening_the_browser_always_says_where_to_go(monkeypatch):
    """The outside walk of 0.8.3: webbrowser.open returned True and opened
    nothing (the default browser was not running), and neither the window nor
    the log said what to do. The address is said every time, because True is
    not a confirmation."""
    said = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: True)

    launcher.open_in_browser("http://127.0.0.1:4242/", lambda state, text: said.append((state, text)))

    assert said == [("busy", launcher.BROWSER_OPENED.format(url="http://127.0.0.1:4242/"))]


def test_a_browser_that_refuses_is_said_with_the_address(monkeypatch):
    said = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: False)

    launcher.open_in_browser("http://127.0.0.1:4242/", lambda state, text: said.append((state, text)))

    assert said == [("status", launcher.BROWSER_REFUSED.format(url="http://127.0.0.1:4242/"))]


# --- TASK-112.02: one exit-code contract ------------------------------------------
#
# A supervisor that restarts on a non-zero exit (launchd's KeepAlive with
# SuccessfulExit false, systemd's Restart=on-failure) reads this contract:
# a crash asks for a restart, and nothing else does.


def _app_child(code: str):
    """A child in a session of its own, as `AppProcess.start` makes the app.

    Never in pytest's: `AppProcess.stop` sends SIGTERM and then SIGKILL to the
    child's whole process group, and on POSIX a child that shares the group
    takes pytest - and on CI the runner - down with it."""
    return _subprocess.Popen([sys.executable, "-c", code], start_new_session=True)


def _sleeping_child(seconds: int = 60):
    return _app_child(f"import time; time.sleep({seconds})")


def _launch_over(layout, child, monkeypatch):
    """A real Launch whose app is `child`, past everything `first_run` does."""
    layout.logs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(launcher, "first_run", lambda *a, **k: True)
    launch = launcher.Launch(layout, _free_port(), False, lambda state, text: None)
    launch.app = launcher.AppProcess(layout, launch.port)
    launch.app.proc = child
    return launch


def _launcher_log(layout) -> str:
    return (layout.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8")


def test_an_app_that_dies_ends_the_headless_launcher_with_its_own_code(layout, monkeypatch):
    """The crash the research found: the child dies, and the launcher said 0.

    Under an OS supervisor, which restarts on this code itself; with none the
    launcher restarts the app first (TASK-112.10, ADR-037, tests below)."""
    child = _sleeping_child()
    launch = _launch_over(layout, child, monkeypatch)
    monkeypatch.setattr(launcher, "os_supervisor", lambda *a, **k: "systemd")
    threading.Timer(0.3, child.kill).start()

    code = launcher.run_headless(launch)

    assert code == launcher.APP_DIED and code != 0
    said = _launcher_log(layout)
    assert "stopped unexpectedly" in said and "app.log" in said


def test_an_app_that_ends_by_itself_without_an_error_ends_the_launcher_with_zero(layout, monkeypatch):
    """The other half: a non-zero code only for a crash, or a stop route
    (TASK-112.03) would be restarted by the mechanism it was meant to end."""
    child = _app_child("pass")
    launch = _launch_over(layout, child, monkeypatch)

    assert launcher.run_headless(launch) == 0
    assert "MyScribe stopped." in _launcher_log(layout)


def test_a_sigterm_is_a_clean_stop_and_says_so(layout, monkeypatch):
    child = _sleeping_child()
    launch = _launch_over(layout, child, monkeypatch)
    unhandled = []
    previous = _signal.signal(_signal.SIGTERM, lambda *a: unhandled.append(a))  # keeps pytest alive
    try:
        threading.Timer(0.3, _signal.raise_signal, args=(_signal.SIGTERM,)).start()
        code = launcher.run_headless(launch)
    finally:
        _signal.signal(_signal.SIGTERM, previous)
        if child.poll() is None:
            child.kill()

    assert code == 0
    # TASK-112.09: the stop says what happened to the running job; this app
    # has no stop route to answer, so it is the hard stop's sentence.
    assert launcher.STOPPING in _launcher_log(layout) and launcher.ended() in _launcher_log(layout)


def _port_busy(layout, monkeypatch, held):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((launcher.HOST, 0))
    sock.listen(1)
    held.append(sock)
    return sock.getsockname()[1], "is used by another program"


def _already_serving(layout, monkeypatch, held):
    monkeypatch.setattr(launcher, "running_instance", lambda *a, **k: True)
    return _free_port(), "already running"


def _offline_sync(layout, monkeypatch, held):
    monkeypatch.setattr(launcher, "needs_sync", lambda _layout: True)
    monkeypatch.setattr(launcher, "enough_disk", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "install_tools", lambda _layout: None)

    def sync(_layout, line):
        line("error: Failed to fetch: `https://pypi.org/simple/six/`")
        return False

    monkeypatch.setattr(launcher, "sync", sync)
    return _free_port(), "Check the internet connection"


@pytest.mark.parametrize("problem", [_port_busy, _already_serving, _offline_sync],
                         ids=["port-busy", "already-serving", "offline-sync"])
def test_a_configuration_problem_ends_with_zero_and_is_in_the_log(layout, monkeypatch, problem):
    """A restart cannot plug a drive in or free a port; under launchd it would
    retry a multi-GB sync every ten seconds, on battery."""
    layout.logs_dir.mkdir(parents=True)
    held: list = []
    try:
        port, sentence = problem(layout, monkeypatch, held)
        launch = launcher.Launch(layout, port, False, lambda state, text: None)

        code = launcher.run_headless(launch)
    finally:
        for sock in held:
            sock.close()

    assert code == 0
    assert sentence in _launcher_log(layout)


def _unplugged(tmp_path, monkeypatch):
    pointer = tmp_path / "MyScribe.location"
    pointer.write_text(json.dumps({"home": str(tmp_path / "unplugged")}), encoding="utf-8")
    monkeypatch.delenv(launcher.HOME_VARIABLE, raising=False)
    monkeypatch.setattr(launcher, "default_home", lambda *a, **k: tmp_path / "MyScribe")
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: pointer)
    return tmp_path / "MyScribe-launcher.log"


def test_a_headless_start_over_an_unplugged_drive_ends_with_zero_and_leaves_a_log(
        layout, tmp_path, monkeypatch):
    """Before a home there is no launcher.log, and behind --windowed no
    stdout: the one sentence went nowhere. It goes beside the pointer, whose
    folder is there whenever the pointer is."""
    early = _unplugged(tmp_path, monkeypatch)

    code = launcher.main(["--headless", "--at-login", "--no-browser",
                          "--payload", str(layout.payload), "--port", str(_free_port())])

    assert code == 0
    said = early.read_text(encoding="utf-8")
    assert "unplugged" in said and "not there" in said
    assert not (tmp_path / "unplugged").exists() and not (tmp_path / "MyScribe").exists()


def test_a_one_job_door_over_an_unplugged_drive_still_fails(layout, tmp_path, monkeypatch):
    """--sync-only, --doctor and --smoke are read as pass or fail by CI and by
    scripts; nothing supervises them, so they keep their 1."""
    _unplugged(tmp_path, monkeypatch)

    assert launcher.main(["--sync-only", "--payload", str(layout.payload)]) == 1


def test_a_fresh_home_keeps_what_it_could_not_hold_in_the_early_log(layout, tmp_path, monkeypatch):
    """A busy port is found before the home is made, so launcher.log has no
    folder to go in yet; the sentence must not be lost with the process."""
    early = _unplugged(tmp_path, monkeypatch)
    held: list = []
    try:
        port, sentence = _port_busy(layout, monkeypatch, held)
        launch = launcher.Launch(layout, port, False, lambda state, text: None)
        code = launcher.run_headless(launch)
    finally:
        for sock in held:
            sock.close()

    assert code == 0 and not layout.home.exists()
    assert sentence in early.read_text(encoding="utf-8")


# --- TASK-112.03: MyScribe --stop -------------------------------------------------


class _FakeServer:
    """/health naming a real process as the app, and a stop route that ends it
    the way the real one does - or, with ``obeys=False``, answers and ignores
    the ask, the server the hard stop is for."""

    def __init__(self, app_pid: int, on_stop, obeys: bool = True, launcher_pid: int | None = None,
                 running_job: int | None = None, port: int = 0, answer_after: float = 0.0):
        self.posted: list[str] = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._answer(200, {"ok": True, "pid": app_pid, "launcher_pid": launcher_pid})

            def do_POST(self):
                fake.posted.append(self.path)
                time.sleep(answer_after)  # a route that is reached but slow to answer
                self._answer(202, {"stopping": True, "pid": app_pid, "running_job": running_job})
                if obeys:
                    threading.Thread(target=on_stop, daemon=True).start()

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _orphan_app() -> int:
    """A sleeping process that is nobody's child here, as the app is to a
    `--stop` started from another shell: started through a parent that exits,
    in a session of its own, so its exit is reaped by the system and not left
    as a zombie that still answers `kill(pid, 0)`."""
    spawn = ("import subprocess, sys; flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0); "
             "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
             "start_new_session=sys.platform != 'win32', creationflags=flags, "
             "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
             "print(p.pid)")
    return int(_subprocess.run([sys.executable, "-c", spawn], capture_output=True, text=True,
                               check=True).stdout.strip())


def _end(pid: int) -> None:
    try:
        os.kill(pid, _signal.SIGTERM)  # TerminateProcess on Windows
    except OSError:
        pass


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not launcher.pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _stop_with(pid, obeys, wait=10.0):
    said: list[tuple[str, str]] = []
    fake = _FakeServer(pid, lambda: (fake.close(), _end(pid)), obeys=obeys)
    try:
        code = launcher.stop_running(fake.port, lambda state, text: said.append((state, text)),
                                     wait=wait)
    finally:
        if not obeys:
            fake.close()
    return code, said, fake


def test_stop_asks_the_running_server_and_waits_for_its_process(monkeypatch):
    pid = _orphan_app()
    hard: list = []
    monkeypatch.setattr(launcher, "end_tree", lambda target: hard.append(target))
    try:
        code, said, fake = _stop_with(pid, True)

        assert code == 0 and fake.posted == ["/api/shutdown"]
        assert not launcher.pid_alive(pid), "--stop returned while the app still ran"
        assert hard == [], "the hard stop ran although the server stopped itself"
        assert "starts over" in said[-1][1]
    finally:
        _end(pid)


def test_stop_ends_a_server_that_does_not_stop_and_says_what_that_costs():
    pid = _orphan_app()
    try:
        code, said, fake = _stop_with(pid, False, wait=1.0)

        assert code == 0 and fake.posted == ["/api/shutdown"]
        assert _gone(pid), "the hard stop left the app running"
        # The route was reached, so the app may have requeued the job before
        # it hung: "marked interrupted" alone would not be sure to be true.
        assert ("status", launcher.ENDED_ASKED) in said, said
    finally:
        _end(pid)


def test_stop_with_nothing_running_says_so_and_succeeds():
    said: list = []
    assert launcher.stop_running(_free_port(), lambda state, text: said.append(text)) == 0
    assert "not running" in said[-1]


def test_asking_whether_a_process_lives_does_not_end_it():
    """On Windows `os.kill(pid, 0)` is TerminateProcess with exit code 0: the
    obvious liveness check would be the stop itself."""
    child = _sleeping_child()
    try:
        assert launcher.pid_alive(child.pid)
        time.sleep(0.2)
        assert child.poll() is None, "asking killed it"
    finally:
        child.kill()
        child.wait(10)
    assert not launcher.pid_alive(child.pid)


def test_the_stop_flag_reaches_stop_running(monkeypatch, tmp_path):
    asked: list = []
    monkeypatch.setattr(launcher, "pointer_path", lambda *a, **k: tmp_path / "MyScribe.location")
    monkeypatch.setattr(launcher, "stop_running", lambda port, report, **k: asked.append(port) or 0)

    assert launcher.main(["--stop", "--port", "4299"]) == 0
    assert asked == [4299]


_FAKE_LAUNCHER = """
import os, signal, subprocess, sys, time
marker = sys.argv[1]
app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                       start_new_session=sys.platform != "win32",
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def stop(*_):  # what run_headless does on SIGTERM: end the app's group, exit 0
    os.killpg(app.pid, signal.SIGTERM)
    app.wait(10)
    open(marker, "w").write("0")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print(os.getpid(), app.pid, flush=True)
time.sleep(60)
"""


def _orphan_launcher(tmp_path) -> tuple[int, int, Path]:
    """A launcher and its app, both nobody's child here, as `--stop` from
    another shell finds them in production."""
    script = tmp_path / "fake_launcher.py"
    script.write_text(_FAKE_LAUNCHER, encoding="utf-8")
    marker = tmp_path / "launcher-exit.txt"
    out = tmp_path / "pids.txt"
    spawn = ("import subprocess, sys; flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0); "
             f"subprocess.Popen([sys.executable, {str(script)!r}, {str(marker)!r}], "
             "start_new_session=sys.platform != 'win32', creationflags=flags, "
             f"stdin=subprocess.DEVNULL, stdout=open({str(out)!r}, 'w'), stderr=subprocess.DEVNULL)")
    _subprocess.run([sys.executable, "-c", spawn], check=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if out.exists() and len(out.read_text().split()) == 2:
            break
        time.sleep(0.1)
    launcher_pid, app_pid = (int(n) for n in out.read_text().split())
    return launcher_pid, app_pid, marker


def test_the_hard_stop_ends_the_launcher_so_it_exits_clean_and_takes_its_app(tmp_path):
    """Production's hard stop: /health names the launcher. Ending the
    launcher - not the app under it - is what keeps a supervisor from
    reading a crash and starting the server again (TASK-112.02)."""
    launcher_pid, app_pid, marker = _orphan_launcher(tmp_path)
    said: list = []
    fake = _FakeServer(app_pid, lambda: None, obeys=False, launcher_pid=launcher_pid)
    try:
        code = launcher.stop_running(fake.port, lambda state, text: said.append(text), wait=1.0)
    finally:
        fake.close()
        for pid in (app_pid, launcher_pid):
            _end(pid)

    assert code == 0
    assert _gone(app_pid) and _gone(launcher_pid)
    if sys.platform != "win32":  # taskkill /F gives a Windows launcher no handler to run
        assert marker.read_text() == "0", "the launcher did not stop through its own handler"
    # --stop cannot see whether the launcher's own hard stop had to kill the
    # app, so on macOS and Linux it says the runner may have run on. The route
    # answered before the app hung, so the job may be back in the queue too.
    assert (launcher.ENDED_ASKED if sys.platform == "win32"
            else launcher.ENDED_ASKED_KILLED) in said, said


@pytest.mark.parametrize(("windows", "killed", "asked", "sentence"), [
    (True, False, False, "ENDED"), (True, True, False, "ENDED"), (True, None, False, "ENDED"),
    (False, False, False, "ENDED"), (False, True, False, "ENDED_KILLED"),
    (False, None, False, "ENDED_KILLED"),
    (True, False, True, "ENDED_ASKED"), (True, True, True, "ENDED_ASKED"),
    (True, None, True, "ENDED_ASKED"), (False, False, True, "ENDED_ASKED"),
    (False, True, True, "ENDED_ASKED_KILLED"), (False, None, True, "ENDED_ASKED_KILLED"),
])
def test_the_hard_stops_sentence_promises_interrupted_only_where_it_is_sure(
        monkeypatch, windows, killed, asked, sentence):
    """Windows ends the tree, runner and all. On macOS and Linux only an app
    that lived through its SIGTERM is sure to have ended its runner (ADR-032);
    a SIGKILLed one, or one this process cannot see, may not have. An app
    whose stop route was reached may have requeued the job before it hung, so
    then the sentence names both outcomes."""
    monkeypatch.setattr(launcher, "IS_WINDOWS", windows)
    assert launcher.ended(killed, asked=asked) == getattr(launcher, sentence)


def test_the_asked_sentences_name_the_requeue_and_the_interrupt():
    for sentence in (launcher.ENDED_ASKED, launcher.ENDED_ASKED_KILLED):
        assert "back at the front of the queue" in sentence and "Retry on the jobs page" in sentence
    assert "marked interrupted" in launcher.ENDED_ASKED
    assert "keep running after MyScribe has closed" in launcher.ENDED_ASKED_KILLED
    assert "marked interrupted" not in launcher.ENDED_ASKED_KILLED


def test_the_stop_sentences_allow_for_a_job_that_finishes_first():
    """Review of TASK-112.09: the stop route names the running job, but once
    no new job is claimed the app may still wait up to 30 s for an upload
    (ADR-032), and the job can finish inside that window - the builder's real
    run saw job 1 end done 3.2 s into the stop. A sentence that says it goes
    back, or is back, must leave room for that."""
    assert launcher.STOPPING_JOB.format(job=7).endswith("unless it finishes first.")
    for sentence in (launcher.STOPPED_JOB.format(job=7), launcher.STOPPED):
        assert sentence.endswith("unless it finished before MyScribe stopped."), sentence
    for sentence in (launcher.ENDED_ASKED, launcher.ENDED_ASKED_KILLED):
        assert "A job that was running may have finished first; if not," in sentence, sentence


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("obeys", [True, False], ids=["obeys-sigterm", "ignores-sigterm"])
def test_the_hard_stop_says_whether_it_had_to_kill_the_app(layout, obeys):
    """ADR-032: an app that lives through its SIGTERM ends its runner itself;
    one that had to be SIGKILLed may not have, and its runner - a session of
    its own - can run on. `AppProcess.stop` and `end_tree` tell the two apart
    so that the sentence after a hard stop can (TASK-112.09)."""
    code = "import time; time.sleep(60)" if obeys else (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)")
    app = launcher.AppProcess(layout, _free_port())
    app.proc = _app_child(code)
    time.sleep(0.5)  # the handler is in place
    try:
        assert app.stop(timeout=2) is (not obeys)
    finally:
        if app.proc.poll() is None:
            app.proc.kill()
    pid = _orphan_app() if obeys else int(_subprocess.run([sys.executable, "-c", (
        "import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', " + repr(code) + "], "
        "start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL); print(p.pid)")], capture_output=True, text=True,
        check=True).stdout.strip())
    time.sleep(0.5)
    try:
        assert launcher.end_tree(pid, grace=2) is (not obeys)
    finally:
        _end(pid)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_a_group_of_only_zombies_does_not_make_stop_raise(layout, monkeypatch):
    """macOS answers killpg on a group whose members have all exited but are
    not yet reaped with EPERM, not ESRCH - and the SIGKILL for stragglers is
    sent after the app has gone. That surfaced as a PermissionError out of
    Quit on a hosted macOS runner."""
    child = _subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                              start_new_session=True)
    app = launcher.AppProcess(layout, _free_port())
    app.proc = child
    real_killpg = os.killpg

    def killpg(group, sig):
        if sig == _signal.SIGKILL:
            raise PermissionError(1, "Operation not permitted")
        real_killpg(group, sig)

    monkeypatch.setattr(launcher.os, "killpg", killpg)

    app.stop(timeout=10)

    assert child.poll() is not None


# --- TASK-112.09: Quit, Ctrl+C and SIGTERM stop through the stop route --------------
#
# ADR-028: every stop goes through the stop route, because only it puts the
# running job back at the front of the queue. Before this, Quit and a signal to
# the launcher went straight to `AppProcess.stop`: on Windows taskkill, which
# leaves the job to reconcile as interrupted; on POSIX a signal to the app's
# group, which the runner - in a session of its own - outlives.


def _route_over(layout, monkeypatch, running_job=7, obeys=True, delay=0.0):
    """A real Launch whose app is a sleeping child, behind a stop route that
    ends that child the way the real one ends the app - after ``delay``,
    which stands in for the supervisor's requeue join."""
    child = _sleeping_child()
    launch = _launch_over(layout, child, monkeypatch)
    said: list[tuple[str, str]] = []
    launch.say = lambda state, text: said.append((state, text))

    def the_route_stops_the_app():
        time.sleep(delay)
        child.terminate()

    fake = _FakeServer(child.pid, the_route_stops_the_app, obeys=obeys, running_job=running_job,
                       port=launch.port)
    hard: list = []
    real_stop = launcher.AppProcess.stop

    def recorded(self, *args, **kwargs):
        hard.append(self.proc.pid)
        return real_stop(self, *args, **kwargs)

    monkeypatch.setattr(launcher.AppProcess, "stop", recorded)
    return launch, child, fake, said, hard


def _cleanup(child, fake):
    fake.close()
    if child.poll() is None:
        child.kill()
        child.wait(10)


def test_quit_asks_the_stop_route_first_and_waits_for_the_apps_pid(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch, delay=1.0)
    try:
        launch.stop()

        assert fake.posted == ["/api/shutdown"], "Quit did not ask the stop route"
        assert child.poll() is not None, "Quit returned while the app still ran"
        assert hard == [], "the hard stop ran although the app stopped itself"
    finally:
        _cleanup(child, fake)


def test_quit_says_what_happens_to_the_running_job_before_and_after(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch, running_job=7)
    try:
        launch.stop()
    finally:
        _cleanup(child, fake)

    texts = [text for _state, text in said]
    before = [i for i, text in enumerate(texts) if "Job 7 goes back to the front of the queue" in text]
    after = [i for i, text in enumerate(texts) if text.startswith("MyScribe stopped. Job 7 is back")]
    assert before and after and before[0] < after[0], texts
    assert said[-1][0] == "status", "the last word is a headline, so the window shows it"
    assert "Job 7 goes back" in _launcher_log(layout), "the console's lines are kept in launcher.log"


def test_quit_with_no_job_running_says_so(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch, running_job=None)
    try:
        launch.stop()
    finally:
        _cleanup(child, fake)

    assert said[-1] == ("status", launcher.STOPPED_IDLE)


def test_quit_falls_back_to_the_hard_stop_when_the_app_outlives_the_wait(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch, obeys=False)
    launch.stop_wait = 0.5
    try:
        launch.stop()

        assert fake.posted == ["/api/shutdown"]
        assert hard == [child.pid], "an app that did not stop through its route was left running"
        assert child.poll() is not None
        # The route was reached: the job may be back in the queue already.
        assert said[-1] == ("status", launcher.ENDED_ASKED), said[-1]
    finally:
        _cleanup(child, fake)


def test_quit_falls_back_to_the_hard_stop_when_the_route_does_not_answer(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch)
    fake.close()  # an app that does not answer: hung, or still starting
    try:
        launch.stop()

        assert fake.posted == []
        assert hard == [child.pid]
        assert child.poll() is not None
        assert said[-1] == ("status", launcher.ended())
    finally:
        _cleanup(child, fake)


def test_quit_on_a_route_that_is_reached_but_too_slow_to_answer_says_both_outcomes(
        layout, monkeypatch):
    """`ROUTE_TIMEOUT` ran out on a route that did get the request: the app may
    have set its stop flag and requeue the job, so the sentence after the hard
    stop must not promise *interrupted* alone."""
    child = _sleeping_child()
    launch = _launch_over(layout, child, monkeypatch)
    said: list[tuple[str, str]] = []
    launch.say = lambda state, text: said.append((state, text))
    fake = _FakeServer(child.pid, lambda: None, obeys=False, port=launch.port, answer_after=2.0)
    monkeypatch.setattr(launcher, "ROUTE_TIMEOUT", 0.5)
    try:
        launch.stop()

        assert fake.posted == ["/api/shutdown"]
        assert child.poll() is not None
        assert said[-1] == ("status", launcher.ENDED_ASKED), said[-1]
    finally:
        _cleanup(child, fake)


def test_quit_on_an_app_that_already_ended_says_nothing_and_asks_nothing(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch)
    child.kill()
    child.wait(10)
    try:
        launch.stop()
    finally:
        _cleanup(child, fake)

    assert fake.posted == [] and said == []


def test_a_sigterm_to_the_headless_launcher_goes_through_the_stop_route(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch)
    unhandled = []
    previous = _signal.signal(_signal.SIGTERM, lambda *a: unhandled.append(a))  # keeps pytest alive
    try:
        threading.Timer(0.3, _signal.raise_signal, args=(_signal.SIGTERM,)).start()
        code = launcher.run_headless(launch)
    finally:
        _signal.signal(_signal.SIGTERM, previous)
        _cleanup(child, fake)

    assert code == 0
    assert fake.posted == ["/api/shutdown"] and hard == []
    assert "Job 7 is back at the front of the queue" in _launcher_log(layout)


def test_ctrl_c_on_the_headless_launcher_goes_through_the_stop_route(layout, monkeypatch):
    launch, child, fake, said, hard = _route_over(layout, monkeypatch)
    previous = _signal.signal(_signal.SIGINT, _signal.default_int_handler)
    try:
        threading.Timer(0.3, _signal.raise_signal, args=(_signal.SIGINT,)).start()
        code = launcher.run_headless(launch)
    finally:
        _signal.signal(_signal.SIGINT, previous)
        _cleanup(child, fake)

    assert code == 0
    assert fake.posted == ["/api/shutdown"] and hard == []
    assert "Job 7 is back at the front of the queue" in _launcher_log(layout)


def test_a_second_signal_while_the_stop_runs_does_not_cut_it_short(layout, monkeypatch):
    """The stop now waits for the requeue - seconds, not milliseconds - and a
    second Ctrl+C or SIGTERM in that time (an impatient person, a service
    manager) must neither end the launcher on whatever handler was there
    before nor break the wait and leave the app running."""
    launch, child, fake, said, hard = _route_over(layout, monkeypatch, delay=1.5)
    unhandled = []
    previous = _signal.signal(_signal.SIGTERM, lambda *a: unhandled.append(a))  # keeps pytest alive
    timers = [threading.Timer(0.3, _signal.raise_signal, args=(_signal.SIGTERM,)),
              threading.Timer(1.0, _signal.raise_signal, args=(_signal.SIGTERM,))]
    try:
        for timer in timers:
            timer.start()
        code = launcher.run_headless(launch)
        time.sleep(0.2)
    finally:
        for timer in timers:  # a stop that returned early must not leave a signal for pytest
            timer.cancel()
            timer.join()
        _signal.signal(_signal.SIGTERM, previous)
        _cleanup(child, fake)

    assert code == 0
    assert unhandled == [], "the second SIGTERM fell through to whoever handled it before"
    assert fake.posted == ["/api/shutdown"] and hard == []
    assert "Job 7 is back at the front of the queue" in _launcher_log(layout)


def test_the_hard_stop_gives_a_launcher_longer_than_its_own_stop_can_take(layout, monkeypatch):
    """`--stop` SIGTERMs the launcher when its own route wait ran out, and the
    launcher's stop then reads /health, asks the route and waits again before
    its hard stop. Killing it before that is over exits it non-zero, which is
    `APP_DIED`'s restart signal. The budget is what this stop really waits:
    the timeouts its own requests pass, measured, plus its `stop_wait`, the
    setup child's timeout and the hard stop's two."""
    import inspect

    hard_stop = inspect.signature(launcher.AppProcess.stop).parameters["timeout"].default
    setup = inspect.signature(launcher.Launch.stop_setup).parameters["timeout"].default
    launch, child, fake, said, hard = _route_over(layout, monkeypatch, obeys=False)
    stop_wait = launch.stop_wait
    launch.stop_wait = 0.5
    timeouts: list[float] = []
    real_opener = launcher.loopback_opener

    def spying_opener():
        opener = real_opener()
        real_open = opener.open

        def open_(fullurl, data=None, timeout=None):
            timeouts.append(timeout)
            return real_open(fullurl, data, timeout)

        opener.open = open_
        return opener

    monkeypatch.setattr(launcher, "loopback_opener", spying_opener)
    try:
        launch.stop()
    finally:
        _cleanup(child, fake)

    assert fake.posted == ["/api/shutdown"] and hard == [child.pid]
    assert len(timeouts) == 2 and all(isinstance(t, (int, float)) for t in timeouts), timeouts
    worst = sum(timeouts) + stop_wait + setup + hard_stop + launcher.STOP_TIMEOUT  # SIGTERM, SIGKILL
    assert launcher.LAUNCHER_STOP_WAIT > worst, (
        f"--stop kills a launcher after {launcher.LAUNCHER_STOP_WAIT:g} s; its own stop may take {worst:g} s")


def test_the_posix_hard_stop_waits_out_the_apps_whole_shutdown_before_it_kills():
    """`AppProcess.stop` and `end_tree` SIGKILL an app that is still there after
    their SIGTERM wait, and then say its runner may run on. The app's lifespan
    ends the runner first and then joins the phone door, the feeds and the
    watcher, so a wait shorter than that whole shutdown SIGKILLs an app that
    has already ended its runner, and the sentence after it is not true. The
    whole shutdown includes the uploads it lets finish (TASK-112.09)."""
    import inspect

    from scribe import __main__ as scribe_main
    from scribe import supervisor
    from scribe.ingest import feeds, watching
    from scribe.web import phone_door

    def default(function, name="timeout"):
        return inspect.signature(function).parameters[name].default

    from scribe.web import stopping

    # Open reads are cut after SHUTDOWN_GRACE_SECONDS and uploads exempt, cut
    # at the bound (Robert, 2026-10-25, both), both counted from the stop. The door closes inside the lifespan with the
    # app's deadline, so a phone upload is given what is left of it and at
    # least the door's grace.
    assert stopping.UPLOAD_FINISH_SECONDS >= scribe_main.SHUTDOWN_GRACE_SECONDS
    # The supervisor's step is its join, not the kill inside it: Supervisor.stop
    # joins for max(timeout, STOP_JOIN_SECONDS) and the lifespan waits that out.
    assert default(supervisor.Supervisor.stop) <= supervisor.STOP_JOIN_SECONDS
    shutdown = (stopping.UPLOAD_FINISH_SECONDS  # uploads finish; the rest was cut at the grace
                + supervisor.STOP_JOIN_SECONDS  # the supervisor ends the runner's tree, joined
                + phone_door.GRACE_SECONDS  # the door, its uploads' deadline passed
                + default(feeds.FeedWatcher.stop) + default(watching.Watcher.stop))
    # The wait for a file still being stored is counted from the lifespan's
    # end and runs alongside the joins, so it adds nothing while it is shorter.
    from scribe import shutdown as shutdown_module

    joins = shutdown - stopping.UPLOAD_FINISH_SECONDS
    assert shutdown_module.INGEST_FINISH_SECONDS <= joins
    assert default(launcher.AppProcess.stop) > shutdown, (
        f"AppProcess.stop SIGKILLs after {default(launcher.AppProcess.stop):g} s; "
        f"the app's shutdown may take {shutdown:g} s")
    assert default(launcher.end_tree, "grace") > shutdown
    # On Windows the hard stop after STOP_WAIT is taskkill /F: an upload
    # still finishing inside the app's stop would be killed with it.
    assert launcher.STOP_WAIT > shutdown, (
        f"the route's wait ends after {launcher.STOP_WAIT:g} s; the app's shutdown may take {shutdown:g} s")


@pytest.mark.parametrize(("windows", "killed"), [(True, False), (True, True), (False, False), (False, True)],
                         ids=["windows", "windows-killed", "posix-sigterm", "posix-sigkill"])
def test_the_hard_stops_sentence_says_what_this_os_does_with_the_running_job(
        layout, monkeypatch, windows, killed):
    """The hard stop on Windows ends the app's tree, runner and all, so
    reconcile marks the job interrupted. On macOS and Linux it signals the
    app's group only; an app that lives through that SIGTERM ends its runner
    itself (ADR-032), so that job is interrupted too. One that had to be
    SIGKILLed may not have, and its runner - a session of its own - can run on
    and finish the job (ADR-028, measured in WSL). Only that sentence leaves
    *interrupted* out."""
    launch, child, fake, said, hard = _route_over(layout, monkeypatch)
    fake.close()  # the route does not answer: the hard stop runs

    def hard_stop(self, *args, **kwargs):
        child.kill()
        child.wait(10)
        return killed

    monkeypatch.setattr(launcher.AppProcess, "stop", hard_stop)
    monkeypatch.setattr(launcher, "IS_WINDOWS", windows)
    try:
        launch.stop()
    finally:
        _cleanup(child, fake)

    state, text = said[-1]
    assert state == "status" and "Retry on the jobs page" in text, text
    if windows or not killed:
        assert "is marked interrupted" in text and "keep running" not in text, text
    else:
        assert "keep running after MyScribe has closed" in text, text
        assert "is marked interrupted" not in text, text


REPO = LAUNCHER_PATH.parents[2]


def test_quit_requeues_a_running_job_and_leaves_no_runner(layout, monkeypatch):
    """Criteria 2 and 3 end to end: a real `python -m scribe`, its real
    supervisor and a real runner in a session of its own, running the slow
    fake job, and the launcher's own stop. Before TASK-112.09 the row stayed
    `running` for reconcile to mark interrupted (Windows) and the runner ran
    on as an orphan (POSIX). Not skipped anywhere: macOS is shown by CI."""
    from scribe import db as scribe_db
    from scribe import jobs as scribe_jobs

    monkeypatch.setattr(launcher.Layout, "app_dir", property(lambda self: REPO))
    monkeypatch.setattr(launcher.Layout, "env_python", property(lambda self: Path(sys.executable)))
    launcher.prepare_home(layout)
    port = _free_port()
    said: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, port, False, lambda state, text: said.append((state, text)))
    launch.app = launcher.AppProcess(layout, port, base=dict(os.environ, SCRIBE_FAKE_STAGES="1"))
    launch.app.start()
    conn = None
    try:
        assert launch.app.wait_ready(timeout=180), "the app did not start"
        app_pid = launcher.read_health(port)["pid"]
        conn = scribe_db.connect(layout.data_dir / "myscribe.db")
        slow = scribe_jobs.enqueue(conn, "fake", params={"scenario": "slow"})
        behind = scribe_jobs.enqueue(conn, "fake", params={"scenario": "ok"})
        deadline = time.monotonic() + 60
        row = None
        while time.monotonic() < deadline:
            row = conn.execute("SELECT status, pid FROM job WHERE id=?", (slow,)).fetchone()
            if row["status"] == "running" and row["pid"]:
                break
            time.sleep(0.1)
        assert row["status"] == "running" and row["pid"], "the slow job never started"
        runner = row["pid"]

        launch.stop()

        assert not launcher.pid_alive(app_pid), "the stop returned while the app still ran"
        assert not _pid_alive(runner), "the runner outlived the app"
        rows = {r["id"]: r for r in conn.execute(
            "SELECT id, status, queue_seq FROM job WHERE id IN (?, ?)", (slow, behind))}
        assert rows[slow]["status"] == "queued", f"the stopped job is {rows[slow]['status']}"
        assert rows[slow]["queue_seq"] < rows[behind]["queue_seq"], "not back at the front"
        assert any(f"Job {slow} is back" in text for _state, text in said), said
        time.sleep(3)
        assert not _pid_alive(runner), "the runner outlived the app"
    finally:
        if conn is not None:
            conn.close()
        launch.app.stop(timeout=10)


def test_the_hard_stop_leaves_no_runner_and_says_the_job_is_interrupted(layout, monkeypatch):
    """ADR-032 end to end: the launcher's hard stop, the one that runs when
    the stop route does not answer, on a real `python -m scribe` running the
    slow fake job. On Windows taskkill ends the tree, as before. On macOS and
    Linux it SIGTERMs the app's group only, and before TASK-112.09 the runner,
    a session of its own, ran on and wrote `done`. Now the app ends it in its
    lifespan, the row waits for reconcile, and the sentence says interrupted."""
    from scribe import db as scribe_db
    from scribe import jobs as scribe_jobs

    monkeypatch.setattr(launcher.Layout, "app_dir", property(lambda self: REPO))
    monkeypatch.setattr(launcher.Layout, "env_python", property(lambda self: Path(sys.executable)))
    launcher.prepare_home(layout)
    port = _free_port()
    said: list[tuple[str, str]] = []
    launch = launcher.Launch(layout, port, False, lambda state, text: said.append((state, text)))
    launch.app = launcher.AppProcess(layout, port, base=dict(os.environ, SCRIBE_FAKE_STAGES="1"))
    launch.stop_through_route = lambda app: False  # the route does not answer
    launch.app.start()
    conn = None
    try:
        assert launch.app.wait_ready(timeout=180), "the app did not start"
        app_pid = launcher.read_health(port)["pid"]
        conn = scribe_db.connect(layout.data_dir / "myscribe.db")
        slow = scribe_jobs.enqueue(conn, "fake", params={"scenario": "slow"})
        deadline = time.monotonic() + 60
        row = None
        while time.monotonic() < deadline:
            row = conn.execute("SELECT status, pid, stage FROM job WHERE id=?", (slow,)).fetchone()
            if row["status"] == "running" and row["pid"] and row["stage"] == "one":
                break
            time.sleep(0.1)
        assert row["status"] == "running" and row["pid"], "the slow job never started"
        runner = row["pid"]
        stopped = time.monotonic()

        launch.stop()

        # taskkill /F returns before Windows has torn the tree down: a moment.
        assert launcher.wait_gone(app_pid, 5), "the hard stop left the app running"
        assert launcher.wait_gone(runner, 5), "the runner outlived the app"
        assert said[-1] == ("status", launcher.ENDED), said[-1]
        time.sleep(max(0.0, stopped + 13 - time.monotonic()))  # past the slow stage's 10 s
        status = conn.execute("SELECT status FROM job WHERE id=?", (slow,)).fetchone()["status"]
        assert status == "running", f"the job became {status} after the app had gone"
    finally:
        if conn is not None:
            conn.close()
        launch.app.stop(timeout=10)


# --- TASK-112.07: one MyScribe per user -------------------------------------------
#
# The lock is written out in plain stdlib here, not taken through the
# launcher: what is pinned is the contract a second process sees - a lock on
# byte 0 of `<home>.lock`, beside the home - and not the launcher's own word
# for it.

_HOLD_LOCK = textwrap.dedent("""
    import os, sys, time
    handle = open(sys.argv[1], "a+b")
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("busy", flush=True)
        sys.exit(0)
    if len(sys.argv) > 3:  # the holder's port, after the locked byte
        handle.truncate(0)
        handle.write(b" " + sys.argv[3].encode())
        handle.flush()
    print("held", flush=True)
    time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 60)
""")


def _hold_lock(path: Path):
    """Another process that holds the lock at `path` until it is killed."""
    child = _subprocess.Popen([sys.executable, "-c", _HOLD_LOCK, str(path)],
                              stdout=_subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


def _let_go(child) -> None:
    child.kill()
    child.wait()
    child.stdout.close()


def _try_lock(path: Path) -> str:
    """Whether a fresh process can take the lock at `path` right now."""
    done = _subprocess.run([sys.executable, "-c", _HOLD_LOCK, str(path), "0"],
                           capture_output=True, text=True, timeout=30)
    return done.stdout.strip()


@pytest.fixture
def a_second_start(layout, monkeypatch):
    """A start that must not get as far as the install: the sync, the tools
    and the app are recorded, never run."""
    did: list[str] = []
    monkeypatch.setattr(launcher, "enough_disk", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "install_tools", lambda _layout: did.append("tools"))
    monkeypatch.setattr(launcher, "sync", lambda _layout, line: did.append("sync") or False)
    monkeypatch.setattr(launcher.AppProcess, "start", lambda self: did.append("app"))
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    opened: list[str] = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)
    return types.SimpleNamespace(did=did, opened=opened, lock=layout.home.with_name("home.lock"))


def _argv(layout, port, *extra) -> list[str]:
    return [*extra, "--home", str(layout.home), "--payload", str(layout.payload), "--port", str(port)]


def test_a_second_start_while_the_first_still_installs_waits_and_opens_the_browser(
        layout, a_second_start, capsys, monkeypatch):
    """AC #1, and the race the research named: the login entry is syncing,
    nothing answers yet, and a start by hand used to pass the probe and run a
    second `uv sync` into the same environment.

    The first one starts serving when the second has looked and found nothing,
    not a second after the test began: on a slow runner `main` took longer
    than that to reach its first look, which then found it serving (CI run
    40137186180, Windows). The probe itself stays the real one.

    It starts serving on the second "no", the first look inside the wait
    loop, so the loop has to sleep and look again. The answers prove the
    second start waited; the strings and the URL alone did not, since a
    `main` without the loop said and opened the same."""
    port = _free_port()
    holder = _hold_lock(a_second_start.lock)
    server: list = []
    answers: list = []
    probe = launcher.running_instance

    def running_instance(port_, *args, **kwargs):
        answer = probe(port_, *args, **kwargs)
        answers.append(answer)
        if answers.count(False) == 2 and not server:
            server.append(_serve(port, {"ok": True}))
        return answer

    monkeypatch.setattr(launcher, "running_instance", running_instance)
    try:
        code = launcher.main(_argv(layout, port, "--headless"))
    finally:
        _let_go(holder)
        for each in server:
            each.shutdown()
            each.server_close()

    assert code == 0
    assert answers == [False, False, True], f"the second start did not wait: {answers}"
    assert a_second_start.did == [], "a second start installed or started something"
    assert a_second_start.opened == [f"http://127.0.0.1:{port}/"]
    said = capsys.readouterr().out
    assert "already starting" in said and "already running" in said
    assert not layout.home.exists()


def test_a_second_start_that_finds_it_serving_opens_the_browser_at_once(layout, a_second_start):
    port = _free_port()
    holder = _hold_lock(a_second_start.lock)
    server = _serve(port, {"ok": True})
    try:
        started = time.monotonic()
        code = launcher.main(_argv(layout, port, "--headless"))
        took = time.monotonic() - started
    finally:
        _let_go(holder)
        server.shutdown()
        server.server_close()

    assert code == 0 and a_second_start.did == []
    assert a_second_start.opened == [f"http://127.0.0.1:{port}/"]
    assert took < 5


def test_a_second_start_gives_up_waiting_with_zero_and_says_where_to_look(
        layout, a_second_start, monkeypatch, capsys):
    """Exit 0 either way, `APP_DIED`'s contract: a restart would only find
    the same lock again."""
    monkeypatch.setattr(launcher, "READY_TIMEOUT", 1.0)
    port = _free_port()
    holder = _hold_lock(a_second_start.lock)
    try:
        code = launcher.main(_argv(layout, port, "--headless"))
    finally:
        _let_go(holder)

    assert code == 0 and a_second_start.did == [] and a_second_start.opened == []
    said = capsys.readouterr().out
    assert "already starting" in said and f"http://127.0.0.1:{port}/" in said


def test_a_second_start_with_a_window_says_so_in_a_window(layout, a_second_start, monkeypatch):
    """Behind --windowed there is no console: the sentence gets a window of
    its own, at once, instead of a double-click that does nothing for two
    minutes."""
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)
    monkeypatch.setattr(launcher, "run_window", lambda *a, **k: pytest.fail("a second window opened"))
    shown: list[str] = []
    monkeypatch.setattr(launcher, "show_error", shown.append)
    holder = _hold_lock(a_second_start.lock)
    try:
        code = launcher.main(_argv(layout, _free_port()))
    finally:
        _let_go(holder)

    assert code == 0 and a_second_start.did == []
    assert len(shown) == 1 and "already starting" in shown[0]


@pytest.mark.parametrize("door", ["--sync-only", "--smoke"])
def test_a_one_job_door_that_finds_the_lock_fails_without_syncing(layout, a_second_start, door, capsys):
    """Two `uv sync` runs into one environment is the damage the lock is for;
    these doors are read as pass or fail, so they say 1."""
    holder = _hold_lock(a_second_start.lock)
    try:
        code = launcher.main(_argv(layout, _free_port(), door))
    finally:
        _let_go(holder)

    assert code == 1 and a_second_start.did == []
    assert "already" in capsys.readouterr().out


def test_the_doctor_takes_no_lock(layout, a_second_start, monkeypatch):
    """The doctor is what you run while MyScribe runs, to ask why."""
    _fake_env_python(layout)
    launcher.write_stamp(layout)
    called: list = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **k: called.append(a) or 0)
    holder = _hold_lock(a_second_start.lock)
    try:
        # Last: --doctor takes everything after it as the doctor's own arguments.
        assert launcher.main([*_argv(layout, _free_port()), "--doctor"]) == 0
    finally:
        _let_go(holder)
    assert called


def test_the_lock_is_held_while_the_launcher_runs_and_free_after(layout, monkeypatch):
    """Released on every way out, so a second `main()` in this process - and
    the next start after a stop - does not find its own lock."""
    seen: list[str] = []

    def headless(launch, force_setup=False, at_login=False):
        seen.append(_try_lock(launch.layout.home.with_name("home.lock")))
        return 0

    monkeypatch.setattr(launcher, "run_headless", headless)
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)

    assert launcher.main(_argv(layout, _free_port(), "--headless")) == 0
    assert launcher.main(_argv(layout, _free_port(), "--headless")) == 0
    assert seen == ["busy", "busy"]
    assert _try_lock(layout.home.with_name("home.lock")) == "held"


def test_the_app_child_does_not_keep_the_lock(layout, monkeypatch, tmp_path):
    """The app outlives a launcher killed hard (its own process group). If it
    inherited the lock, the next start would wait on an orphan for good; it
    has to find it through /health instead."""
    path = tmp_path / "instance.lock"
    lock = launcher.InstanceLock(path)
    assert lock.acquire()
    layout.logs_dir.mkdir(parents=True)
    app = launcher.AppProcess(layout, _free_port())
    monkeypatch.setattr(app, "command", lambda: [sys.executable, "-c", "import time; time.sleep(30)"])
    app.start()
    try:
        assert _try_lock(path) == "busy"
        lock.release()
        assert _try_lock(path) == "held", "the app child kept the launcher's lock"
    finally:
        lock.release()
        app.proc.kill()
        app.proc.wait()


_FAKE_APP = textwrap.dedent("""
    # `python -m scribe` as the launcher starts it, and nothing more: it notes
    # that it was started, takes a second, then binds the port, answers
    # /health for a few seconds and ends with 0.
    import http.server, json, os, sys, threading, time
    from pathlib import Path

    port = int(sys.argv[sys.argv.index("--port") + 1])
    with open(Path(__file__).resolve().parents[3] / "app-starts.txt", "a") as marker:
        marker.write(str(os.getpid()) + chr(10))
    time.sleep(1.0)

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    http.server.HTTPServer.allow_reuse_address = False  # a taken port refuses it, as uvicorn
    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Health)
    except OSError:
        sys.exit(3)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(4)
""")

_RUN_LAUNCHER = textwrap.dedent("""
    # The launcher file itself, with one seam: the environment's python is
    # this python, so no uv sync is needed to start the stand-in app.
    import importlib.util, sys, time
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("myscribe_launcher", sys.argv[1])
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    launcher.Layout.env_python = property(lambda self: Path(sys.executable))
    go = Path(sys.argv[2])
    while not go.exists():
        time.sleep(0.005)
    sys.exit(launcher.main(sys.argv[3:]))
""")


def test_two_launchers_started_at_once_start_one_app(layout, tmp_path, monkeypatch):
    """AC #2: a login entry and a start by hand in the same instant. Before the
    lock both passed the probe, and both went on to the sync and the app."""
    (layout.app_dir / "scribe" / "__main__.py").write_text(_FAKE_APP, encoding="utf-8")
    # The stamp the sync would write, so neither launcher syncs: the sync is
    # not the subject, and `env_python` is this python in both children.
    layout.stamp_file.parent.mkdir(parents=True)
    layout.stamp_file.write_text(json.dumps({"lock_sha256": launcher.lock_digest(layout)}),
                                 encoding="utf-8")
    runner = tmp_path / "run_launcher.py"
    runner.write_text(_RUN_LAUNCHER, encoding="utf-8")
    go = tmp_path / "go"
    port = _free_port()
    monkeypatch.delenv(launcher.HOME_VARIABLE, raising=False)
    argv = [sys.executable, str(runner), str(LAUNCHER_PATH), str(go),
            *_argv(layout, port, "--headless", "--at-login", "--no-browser")]
    both = [_subprocess.Popen(argv, stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True)
            for _ in range(2)]
    try:
        time.sleep(1.5)  # both imported and waiting at the line
        go.write_text("")
        outputs = [child.communicate(timeout=90)[0] for child in both]
    finally:
        for child in both:
            if child.poll() is None:
                child.kill()
                child.communicate()

    starts = (tmp_path / "app-starts.txt").read_text().split()
    assert len(starts) == 1, f"{len(starts)} apps were started:\n" + "\n---\n".join(outputs)
    assert [child.returncode for child in both] == [0, 0], outputs
    assert sum("is running at" in out for out in outputs) == 1, outputs
    assert sum("already" in out for out in outputs) == 1, outputs


def _busy_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((launcher.HOST, 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def test_a_busy_port_at_login_is_in_the_log_and_shown_once_at_the_next_start_by_hand(
        layout, monkeypatch, capsys):
    """AC #3. The login entry is headless and nobody reads its log; the next
    start somebody makes by hand says what went wrong, once. A second start at
    login does not use the note up: nobody saw that one either."""
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    layout.logs_dir.mkdir(parents=True)  # a home that has started before
    sock, port = _busy_port()
    try:
        assert launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser")) == 0
        logged = (layout.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8")
        assert f"Port {port} is used by another program" in logged
        assert launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser")) == 0
        capsys.readouterr()

        launcher.main(_argv(layout, port, "--headless", "--no-browser"))
        first = capsys.readouterr().out
        launcher.main(_argv(layout, port, "--headless", "--no-browser"))
        second = capsys.readouterr().out
    finally:
        sock.close()

    assert "The last time MyScribe started on its own" in first
    assert f"Port {port} is used by another program" in first.split("started on its own")[1]
    assert "started on its own" not in second, "the note was shown twice"
    assert f"Port {port} is used by another program" in second  # today's problem, said as today
    assert (layout.logs_dir / launcher.INSTALL_LOG).read_text(encoding="utf-8").count(
        "started on its own") == 1


def test_a_login_problem_is_shown_in_the_window_of_the_next_start(layout, monkeypatch):
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    sock, port = _busy_port()
    try:
        assert launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser")) == 0
    finally:
        sock.close()
    seen: list = []
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)
    monkeypatch.setattr(launcher, "run_window", lambda *a, **k: seen.append(k.get("notice")) or 0)

    assert launcher.main(_argv(layout, port, "--no-browser")) == 0
    assert launcher.main(_argv(layout, port, "--no-browser")) == 0

    assert len(seen) == 2 and seen[1] is None
    assert "started on its own" in seen[0] and f"Port {port}" in seen[0]


def test_setup_against_a_running_myscribe_still_says_how_to_reach_the_questions(
        layout, a_second_start, capsys):
    """The lock sends --setup to the second-start path; the sentence
    `first_run` gives a --setup against one that serves must survive that."""
    port = _free_port()
    holder = _hold_lock(a_second_start.lock)
    server = _serve(port, {"ok": True})
    try:
        code = launcher.main(_argv(layout, port, "--headless", "--setup", "--no-browser"))
    finally:
        _let_go(holder)
        server.shutdown()
        server.server_close()

    assert code == 0 and a_second_start.did == []
    assert "quit it first" in capsys.readouterr().out


# --- TASK-112.07, review of 0e8833c ----------------------------------------------


class _App:
    """An AppProcess as the window and the headless door look at it."""

    def __init__(self, alive: bool, code: int = 0, for_seconds: float | None = None):
        self.proc = object()
        self.running = alive
        self.code = code
        self.until = None if for_seconds is None else time.monotonic() + for_seconds

    def alive(self) -> bool:
        if self.until is not None and time.monotonic() >= self.until:
            self.running = False
        return self.running

    def exit_code(self) -> int:
        return self.code

    def stop(self) -> None:
        self.running = False


def _tk_or_skip():
    """Tk, or a skip where it has no display. Called with pytest's capture off:
    under fd capture on Windows, Tcl's init intermittently fails to read
    init.tcl (7 of 8 runs of these four cases had one), and in none of 14
    runs without it."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"no display for Tk here: {error}")
    root.destroy()
    return tk


@pytest.mark.parametrize("end", ["failed", "stopped", "crashed", "running"])
def test_a_window_holds_the_lock_only_while_its_myscribe_runs(layout, monkeypatch, capsys, end):
    """Review finding 1. A window stays open after a failed start and after
    the app stopped, and it used to keep the lock for as long as it was open:
    the next double-click said "already starting" and started nothing. The
    real window, its pump and its worker; only `first_run` and the mainloop
    are stand-ins.

    The stand-in loop runs until the lock was let go of - or, for a MyScribe
    that keeps running, until the worker's turn ended and the pump has looked
    five more times - and not for a fixed two seconds. A worker that stalled held the
    lock past those two seconds on macOS (CI run 40137186180). One stall we
    found: the stand-in never sets _tkinter's "in the main loop" flag, so a
    StringVar of an earlier window that the garbage collector frees on the
    worker waits a second for it and then raises "main thread is not in main
    loop". Those are freed here first, on this thread."""
    with capsys.disabled():
        tk = _tk_or_skip()
    gc.collect()
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)
    let_go = threading.Event()
    release = launcher.InstanceLock.release

    def release_and_say(self):
        release(self)
        let_go.set()

    monkeypatch.setattr(launcher.InstanceLock, "release", release_and_say)
    # The worker's turn ended, and not a thread that has not started yet.
    turned = threading.Event()
    turn = launcher.window_turn

    def window_turn(*args, **kwargs):
        try:
            turn(*args, **kwargs)
        finally:
            turned.set()

    monkeypatch.setattr(launcher, "window_turn", window_turn)

    def first_run(launch, *args, **kwargs):
        if end == "failed":
            launch.report("error", "Port 1 is used by another program; MyScribe cannot start.")
            return False
        # Alive when the start ends, and stopping under the open window half
        # a second later: that is the pump's to see, not the worker's.
        launch.app = _App(alive=True, code=1 if end == "crashed" else 0,
                          for_seconds=None if end == "running" else 0.5)
        launch.report("running", "MyScribe is running.")
        return True

    monkeypatch.setattr(launcher, "first_run", first_run)
    seen: list[str] = []

    def mainloop(self, n=0):
        deadline = time.monotonic() + 30.0
        settled = None  # when the turn of a MyScribe that keeps running ended
        while time.monotonic() < deadline:
            if end != "running" and let_go.is_set():
                break
            if end == "running" and settled is None and turned.is_set():
                settled = time.monotonic()
            if settled is not None and time.monotonic() > settled + 1.0:  # five pump ticks
                break
            self.update()
            time.sleep(0.02)
        seen.append(_try_lock(layout.home.with_name("home.lock")))
        self.destroy()

    monkeypatch.setattr(tk.Tk, "mainloop", mainloop)

    with capsys.disabled():
        assert launcher.main(_argv(layout, _free_port(), "--no-browser")) == 0
    gc.collect()  # this window's StringVars, here and not on the next test's worker
    assert seen == ["busy" if end == "running" else "held"]


def test_a_window_turn_takes_the_lock_again_or_refuses(layout, tmp_path):
    """The Tk-free half of finding 1: what the window's worker and its Setup
    button run. A turn retakes a lock the window let go of, keeps it while an
    app runs, lets go when none does, and refuses when another start has it."""
    path = tmp_path / "turn.lock"
    lock = launcher.InstanceLock(path)
    assert lock.acquire()
    said: list[str] = []
    report = lambda state, text: said.append(text)  # noqa: E731
    launch = types.SimpleNamespace(app=None, layout=layout)
    try:
        launcher.window_turn(lock, launch, lambda: None, report)
        assert _try_lock(path) == "held", "a turn that started nothing kept the lock"

        ran: list[str] = []

        def start():
            ran.append("start")
            launch.app = _App(alive=True)

        launcher.window_turn(lock, launch, start, report)
        assert ran == ["start"] and _try_lock(path) == "busy"
        assert launcher.after_the_app(launch, lock, busy=False) == ""
        assert _try_lock(path) == "busy", "let go of while the app runs"

        launch.app.running = False
        assert launcher.after_the_app(launch, lock, busy=True) == launcher.STOPPED
        assert _try_lock(path) == "busy", "let go of while a sitting runs"
        assert launcher.after_the_app(launch, lock, busy=False) == launcher.STOPPED
        assert _try_lock(path) == "held"

        launch.app = None
        holder = _hold_lock(path)
        try:
            launcher.window_turn(lock, launch, lambda: ran.append("sitting"), report)
        finally:
            _let_go(holder)
        assert ran == ["start"], "a sitting ran while another start held the lock"
        assert "Another MyScribe" in said[-1]
    finally:
        lock.release()


def test_the_lock_and_the_note_of_a_home_at_a_drive_root_are_in_it():
    """Review finding 2: `Path("E:/").with_name(...)` raises ValueError, and a
    drive root is a home `refuse_location` allows."""
    root = Path(Path.cwd().anchor)
    assert launcher.instance_lock_path(root) == root / "MyScribe.lock"
    assert launcher.login_note_path(root) == root / "MyScribe-at-login.txt"
    assert launcher.instance_lock_path(root / "Files") == root / "Files.lock"


def test_a_home_at_a_drive_root_gets_past_the_lock(layout, monkeypatch):
    """`main` itself, with the home settled on a root: before the fix every
    start there died with a traceback in `instance_lock_path`. The lock and
    the doors are stand-ins, so nothing is written to a real drive root."""
    root = Path(Path.cwd().anchor)
    monkeypatch.setattr(launcher, "locate_home", lambda *a, **k: root)
    taken: list[Path] = []

    class Lock:
        problem = ""

        def __init__(self, path, *args, **kwargs):
            taken.append(Path(path))

        def acquire(self):
            return True

        def release(self):
            pass

    monkeypatch.setattr(launcher, "InstanceLock", Lock)
    monkeypatch.setattr(launcher, "run_doors", lambda *a, **k: 0)

    assert launcher.main(["--headless", "--home", str(root), "--payload", str(layout.payload)]) == 0
    assert taken == [root / "MyScribe.lock"]


def test_a_login_start_that_serves_clears_the_last_login_problem(layout, monkeypatch):
    """Review finding 3: Monday's failed login, Tuesday's good one, and
    Friday's start by hand still said Monday's problem."""
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    layout.logs_dir.mkdir(parents=True)
    sock, port = _busy_port()
    try:
        assert launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser")) == 0
    finally:
        sock.close()
    assert launcher.login_note_path(layout.home).exists()

    monkeypatch.setattr(launcher, "first_run",
                        lambda launch, *a, **k: setattr(launch, "app", _App(alive=False)) or True)
    assert launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser")) == 0
    assert not launcher.login_note_path(layout.home).exists(), "a login that served left the old note"


def test_a_login_start_that_finds_myscribe_serving_clears_the_note(layout, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    layout.logs_dir.mkdir(parents=True)
    sock, port = _busy_port()
    try:
        assert launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser")) == 0
    finally:
        sock.close()
    server = _serve(port, {"ok": True})
    try:
        assert launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser")) == 0
    finally:
        server.shutdown()
        server.server_close()
    capsys.readouterr()
    sock, port = _busy_port()
    try:
        launcher.main(_argv(layout, port, "--headless", "--no-browser"))
    finally:
        sock.close()
    assert "could not start:" not in capsys.readouterr().out


def test_the_note_does_not_say_login_for_a_start_after_an_update(layout, monkeypatch):
    """The Windows installer starts `--at-login --headless` after an update;
    that is no login, so the note says what both are."""
    monkeypatch.setattr(launcher, "tkinter_present", lambda: False)
    sock, port = _busy_port()
    try:
        launcher.main(_argv(layout, port, "--headless", "--at-login", "--no-browser"))
    finally:
        sock.close()
    note = launcher.take_login_problem(layout.home)
    assert "last login" not in note
    assert "on its own" in note and "after an update" in note


def test_a_doctor_beside_an_install_refuses_rather_than_syncs(layout, a_second_start, capsys):
    """Review finding 4: the doctor took no lock, and its door still synced -
    a second `uv sync` into the environment a first start was installing."""
    holder = _hold_lock(a_second_start.lock)
    try:
        code = launcher.main([*_argv(layout, _free_port()), "--doctor"])
    finally:
        _let_go(holder)

    assert code == 1 and a_second_start.did == []
    assert "installing or updating" in capsys.readouterr().out


def test_a_doctor_beside_a_running_myscribe_runs_and_touches_nothing(layout, a_second_start, monkeypatch):
    """The doctor is what you run while MyScribe runs, to ask why: with the
    environment synced it runs, and leaves the tools and the home alone."""
    _fake_env_python(layout)
    launcher.write_stamp(layout)
    called: list = []
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **k: called.append(a) or 0)
    holder = _hold_lock(a_second_start.lock)
    try:
        assert launcher.main([*_argv(layout, _free_port()), "--doctor"]) == 0
    finally:
        _let_go(holder)
    assert called and a_second_start.did == []


def test_a_doctor_holds_the_lock_for_its_sync_and_not_for_its_checks(layout, a_second_start, monkeypatch):
    """A doctor that loads a model for minutes must not hold up a login start."""
    seen: list[str] = []

    def sync(_layout, line):
        seen.append("sync " + _try_lock(a_second_start.lock))
        _fake_env_python(_layout)
        launcher.write_stamp(_layout)
        return True

    monkeypatch.setattr(launcher, "sync", sync)
    monkeypatch.setattr(launcher.subprocess, "call",
                        lambda *a, **k: seen.append("doctor " + _try_lock(a_second_start.lock)) or 0)

    assert launcher.main([*_argv(layout, _free_port()), "--doctor"]) == 0
    assert seen == ["sync busy", "doctor held"]


def _hold_lock_on(path: Path, port: int):
    """`_hold_lock`, by a holder that also wrote its port after byte 0."""
    child = _subprocess.Popen([sys.executable, "-c", _HOLD_LOCK, str(path), "60", str(port)],
                              stdout=_subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "held"
    return child


def test_a_second_start_on_another_port_opens_the_one_that_runs(layout, a_second_start, monkeypatch):
    """Review finding 5: the lock is per home and the wait was per port. A
    second start with another --port waited two minutes on the wrong one."""
    monkeypatch.setattr(launcher, "READY_TIMEOUT", 3.0)
    running, asked = _free_port(), _free_port()
    holder = _hold_lock_on(a_second_start.lock, running)
    server = _serve(running, {"ok": True})
    try:
        started = time.monotonic()
        code = launcher.main(_argv(layout, asked, "--headless"))
        took = time.monotonic() - started
    finally:
        _let_go(holder)
        server.shutdown()
        server.server_close()

    assert code == 0 and a_second_start.did == []
    assert a_second_start.opened == [f"http://127.0.0.1:{running}/"]
    assert took < 2.5


def test_the_launcher_that_holds_the_lock_writes_its_port_where_others_can_read_it(tmp_path):
    """After byte 0, which is locked - on Windows a lock another process may
    not even read through."""
    path = tmp_path / "port.lock"
    lock = launcher.InstanceLock(path, port=4330)
    assert lock.acquire()
    try:
        read = _subprocess.run(
            [sys.executable, "-c",
             "import sys; f = open(sys.argv[1], 'rb'); f.seek(1); print(f.read().decode().strip())",
             str(path)],
            capture_output=True, text=True, timeout=30)
        assert read.stdout.strip() == "4330", read.stderr
        assert launcher.held_port(path) == 4330
    finally:
        lock.release()
    assert launcher.held_port(tmp_path / "none.lock") is None


def test_a_setup_click_during_the_pump_still_opens_the_sitting(layout, monkeypatch, capsys):
    """The pump looks at the app under a gate of its own. When it took the
    window's `sitting` lock for that, a Setup click that landed in a pump
    tick found `sitting` taken and was dropped without a word. Made
    deterministic: the click is invoked from inside the pump's look, which
    then takes 0.3 s."""
    with capsys.disabled():
        tk = _tk_or_skip()
    monkeypatch.setattr(launcher, "tkinter_present", lambda: True)

    def first_run(launch, *args, **kwargs):
        launch.app = _App(alive=True)
        return True

    monkeypatch.setattr(launcher, "first_run", first_run)
    sat: list[str] = []
    monkeypatch.setattr(launcher, "open_sitting", lambda *a, **k: sat.append("sitting"))
    root_of: list = []
    clicked: list[str] = []
    real_look = launcher.after_the_app

    def look(launch, lock, busy):
        if root_of and not clicked:
            clicked.append("setup")
            setup = next(widget for frame in root_of[0].winfo_children()
                         for widget in frame.winfo_children()
                         if isinstance(widget, tk.Button) and widget.cget("text") == "Setup")
            setup.invoke()
            time.sleep(0.3)
        return real_look(launch, lock, busy)

    monkeypatch.setattr(launcher, "after_the_app", look)

    def mainloop(self, n=0):
        root_of.append(self)
        until = time.monotonic() + 2.0
        while time.monotonic() < until:
            self.update()
            time.sleep(0.02)
        self.destroy()

    monkeypatch.setattr(tk.Tk, "mainloop", mainloop)

    with capsys.disabled():
        assert launcher.main(_argv(layout, _free_port(), "--no-browser")) == 0
    assert clicked == ["setup"]
    assert sat == ["sitting"], "the Setup click was dropped"


# --- TASK-112.10: the launcher restarts a crashed app, a bounded number of times --
#
# ADR-037. Only where no OS supervisor watches the launcher: the Windows Run
# value, a start by hand on the console and the XDG autostart file. Under the
# systemd unit or the LaunchAgent the launcher exits `APP_DIED` at once, as
# before, and the OS restarts it.


def test_the_launchd_job_is_known_by_its_own_label_and_nothing_else():
    """launchd names the job it started in XPC_SERVICE_NAME. A shell in
    Terminal has the variable too, as `0` or as Terminal's own name, so its
    presence alone would switch the restart off for a start by hand."""
    label = launcher.SUPERVISED_LABEL
    assert launcher.os_supervisor({"XPC_SERVICE_NAME": label}, cgroup=None) == "launchd"
    for other in ("0", "application.com.apple.Terminal.1234", "", label + ".other"):
        assert launcher.os_supervisor({"XPC_SERVICE_NAME": other}, cgroup=None) == ""
    assert launcher.os_supervisor({}, cgroup=None) == ""


def test_the_systemd_unit_is_known_by_its_own_cgroup_and_not_by_invocation_id(tmp_path):
    """systemd puts a unit's processes in a cgroup named after the unit. Not
    INVOCATION_ID: a shell in a terminal that runs as a user service (GNOME's
    terminal server) can carry one, and so can anything another unit starts."""
    cgroup = tmp_path / "cgroup"
    unit = launcher.SUPERVISED_UNIT
    cgroup.write_text(f"0::/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}\n", encoding="utf-8")
    assert launcher.os_supervisor({}, cgroup=cgroup) == "systemd"

    for path in ("/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-myscribe-4242.scope",
                 "/user.slice/user-1000.slice/user@1000.service/app.slice/gnome-terminal-server.service",
                 f"/user.slice/user-1000.slice/user@1000.service/app.slice/not-{unit}"):
        cgroup.write_text(f"0::{path}\n", encoding="utf-8")
        assert launcher.os_supervisor({"INVOCATION_ID": "0123abcd"}, cgroup=cgroup) == ""
    assert launcher.os_supervisor({}, cgroup=tmp_path / "missing") == ""


def test_the_restart_budget_is_a_number_of_restarts_in_a_sliding_window():
    now = [1000.0]
    budget = launcher.RestartBudget(limit=3, window=600.0, clock=lambda: now[0])
    for _ in range(3):  # at 1000, 1100 and 1200
        assert budget.spend() is True
        now[0] += 100.0
    assert budget.spend() is False, "the fourth crash inside the window is not restarted"
    now[0] = 1599.0
    assert budget.spend() is False
    now[0] = 1601.0  # the restart at 1000 is now older than the window
    assert budget.spend() is True
    assert budget.spend() is False


def _restarting(layout, monkeypatch, first, then, supervisor=""):
    """A Launch over ``first`` whose `start_app` starts the children ``then``
    makes, one per restart, and records each start."""
    launch = _launch_over(layout, first, monkeypatch)
    monkeypatch.setattr(launcher, "os_supervisor", lambda *a, **k: supervisor)
    monkeypatch.setattr(launcher, "RESTART_DELAY", 0.0)
    started = []

    def start_app():
        child = then(len(started))
        started.append(child)
        launch.app = launcher.AppProcess(layout, launch.port)
        launch.app.proc = child
        return True

    launch.start_app = start_app
    return launch, started


def _exits(code: int):
    return _app_child(f"import sys; sys.exit({code})")


def test_a_crashed_app_is_started_again_and_a_stop_after_that_is_not(layout, monkeypatch):
    """AC #2: a crash (non-zero) is restarted; the restarted app ending with 0
    - the stop route, from Settings or `--stop` - ends the launcher with 0."""
    launch, started = _restarting(layout, monkeypatch, _exits(3), lambda n: _exits(0))

    code = launcher.run_headless(launch)

    assert code == 0
    assert len(started) == 1
    said = _launcher_log(layout)
    assert "stopped unexpectedly (exit code 3)" in said
    assert launcher.RESTARTING.format(delay=0, count=1, limit=launcher.RESTART_LIMIT,
                                      minutes=launcher.RESTART_WINDOW / 60) in said
    assert launcher.CRASHED_JOB in said, "what happens to a running job is said"
    assert "MyScribe stopped." in said


def test_after_the_last_restart_the_launcher_gives_up_with_a_sentence(layout, monkeypatch):
    """AC #2: up to `RESTART_LIMIT` restarts in the window, then one sentence
    and `APP_DIED`."""
    monkeypatch.setattr(launcher, "RESTART_LIMIT", 2)
    launch, started = _restarting(layout, monkeypatch, _exits(3), lambda n: _exits(3))

    code = launcher.run_headless(launch)

    assert code == launcher.APP_DIED
    assert len(started) == 2
    gave_up = launcher.GAVE_UP.format(limit=2, minutes=launcher.RESTART_WINDOW / 60,
                                      log=layout.logs_dir / "app.log")
    assert _launcher_log(layout).count(gave_up) == 1
    assert launch.problem == gave_up


def test_a_start_at_login_that_gave_up_is_said_by_the_next_start_by_hand(layout, monkeypatch):
    """Nobody reads launcher.log after a login; TASK-112.07's note carries it."""
    monkeypatch.setattr(launcher, "RESTART_LIMIT", 1)
    launch, _ = _restarting(layout, monkeypatch, _exits(3), lambda n: _exits(3))

    assert launcher.run_headless(launch, at_login=True) == launcher.APP_DIED
    assert launcher.GAVE_UP.format(limit=1, minutes=launcher.RESTART_WINDOW / 60,
                                   log=layout.logs_dir / "app.log") in launcher.take_login_problem(layout.home)


def test_a_restart_that_never_answers_is_spent_like_a_crash(layout, monkeypatch):
    """`start_app` stops an app that did not answer and says so; that counts
    against the budget, so a broken environment is not retried for ever."""
    monkeypatch.setattr(launcher, "RESTART_LIMIT", 2)
    launch = _launch_over(layout, _exits(3), monkeypatch)
    monkeypatch.setattr(launcher, "os_supervisor", lambda *a, **k: "")
    monkeypatch.setattr(launcher, "RESTART_DELAY", 0.0)
    tries = []

    def start_app():
        tries.append(1)
        launch.app = launcher.AppProcess(layout, launch.port)
        # 0: on POSIX the SIGTERM `start_app` stops it with may end it cleanly.
        launch.app.proc = _exits(0)
        launch.app.proc.wait()
        return False

    launch.start_app = start_app

    assert launcher.run_headless(launch) == launcher.APP_DIED
    assert len(tries) == 2


def test_under_an_os_supervisor_the_launcher_never_restarts_itself(layout, monkeypatch):
    """AC #3: the systemd unit restarts on 70 and the LaunchAgent on any
    non-zero exit; a restart here as well would be two for one crash."""
    for supervisor in ("systemd", "launchd"):
        launch, started = _restarting(layout, monkeypatch, _exits(3), lambda n: _exits(0),
                                      supervisor=supervisor)

        assert launcher.run_headless(launch) == launcher.APP_DIED, supervisor
        assert started == [], supervisor


def test_a_clean_end_is_never_restarted(layout, monkeypatch):
    """AC #2: exit 0 is a stop somebody asked for (ADR-032)."""
    launch, started = _restarting(layout, monkeypatch, _exits(0), lambda n: _exits(0))

    assert launcher.run_headless(launch) == 0
    assert started == []


def test_ctrl_c_while_a_restart_starts_stops_it_and_starts_nothing_more(layout, monkeypatch):
    """AC #2: a deliberate stop during the restart is a stop, not a crash.
    The app the restart started is stopped through `launch.stop`."""
    launch = _launch_over(layout, _exits(3), monkeypatch)
    monkeypatch.setattr(launcher, "os_supervisor", lambda *a, **k: "")
    monkeypatch.setattr(launcher, "RESTART_DELAY", 0.0)
    started, stopped = [], []

    def start_app():
        started.append(1)
        launch.app = launcher.AppProcess(layout, launch.port)
        launch.app.proc = _sleeping_child()
        raise KeyboardInterrupt  # Ctrl+C while it waits for /health

    def stop():
        stopped.append(launch.app.alive())
        launch.app.proc.kill()
        launch.app.proc.wait()

    launch.start_app = start_app
    launch.stop = stop

    assert launcher.run_headless(launch) == 0
    assert started == [1] and stopped == [True]


def test_a_sigterm_during_the_restart_delay_starts_nothing(layout, monkeypatch):
    launch, started = _restarting(layout, monkeypatch, _exits(3), lambda n: _exits(0))
    monkeypatch.setattr(launcher, "RESTART_DELAY", 3.0)
    unhandled = []
    previous = _signal.signal(_signal.SIGTERM, lambda *a: unhandled.append(a))  # keeps pytest alive
    try:
        threading.Timer(1.0, _signal.raise_signal, args=(_signal.SIGTERM,)).start()
        code = launcher.run_headless(launch)
    finally:
        _signal.signal(_signal.SIGTERM, previous)

    assert code == 0
    assert started == []
