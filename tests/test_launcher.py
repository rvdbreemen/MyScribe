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
import re
import shutil
import socket
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

    def _capture(layout, port, open_browser, force_setup=False, at_login=False):
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

    def window(layout_, port, open_browser, force_setup=False, at_login=False):
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
    assert code == 1
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

    assert code == 1
    assert len(shown) == 1
    assert "not there" in shown[0] and "cannot start" in shown[0]


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
    launch.app = types.SimpleNamespace(stop=lambda timeout=None: stopped.append("app"))
    launch.installing = True

    assert launch.quit_refused() == launcher.QUIT_REFUSED
    launch.stop()

    assert child.terminated is False and stopped == []
    assert reports == [("status", launcher.QUIT_REFUSED)]

    launch.installing = False
    assert launch.quit_refused() == ""
    launch.stop()
    assert child.terminated is True and stopped == ["app"]
