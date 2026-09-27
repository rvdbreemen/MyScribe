"""install.py, the clone's door (TASK-089.17, ADR-015): the guard, the pinned
tools, the sync decision it shares with the launcher, the /health refusal,
the hand-over to scribe.setup - all without network and without the real uv.

install.py is stdlib-only and lives at the repository root, outside the
`scribe` package, so it is loaded from its file the way tests/test_launcher.py
loads the launcher. Every uv in this file is a recording script: the uv on
this machine's PATH is 0.5.9 and rewrites uv.lock, and a test that ran it
would be the reason a clean `git status` fails.
"""

from __future__ import annotations

import ast
import hashlib
import http.server
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import venv
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL_PATH = REPO / "install.py"
BUILD_PAYLOAD_PATH = REPO / "packaging" / "build_payload.py"
LAUNCHER_PATH = REPO / "packaging" / "launcher" / "myscribe_launcher.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_payload = _load("myscribe_build_payload", BUILD_PAYLOAD_PATH)
install = _load("myscribe_install", INSTALL_PATH)
launcher = _load("myscribe_launcher", LAUNCHER_PATH)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- a loopback server standing in for GitHub ------------------------------------


class _Files:
    """A server that answers `/<name>` with the bytes it was given, counts the
    requests, and can drop a connection without answering."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.drop = False
        files = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                files.requests.append(self.path)
                if files.drop:  # nothing sent; the socket closes behind this handler
                    self.close_connection = True
                    return
                body = files.files.get(self.path.lstrip("/"))
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/{name}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def files(monkeypatch):
    """The loopback server, with every proxy variable removed for the test:
    `urlopen` would otherwise send a 127.0.0.1 request to the proxy."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = _Files()
    yield server
    server.close()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """build_payload's cache under tmp_path: never the repository's packaging/.cache."""
    folder = tmp_path / "cache"
    monkeypatch.setattr(build_payload, "CACHE", folder)
    return folder


# --- criterion 10: build_payload.fetch caches atomically and recovers from a bad file


def test_a_cached_file_with_the_wrong_sum_is_deleted_and_fetched_again(files, cache):
    """Today (packaging/build_payload.py:49-58) a cached file whose sum is wrong
    is read, fails the check, and stays where it is: every later run fails on
    the same file. The fix throws it away and fetches once more."""
    good = b"the real archive"
    files.files["tool.zip"] = good
    cache.mkdir()
    (cache / _sha256(good)).write_bytes(b"truncated by a crash")

    data = build_payload.fetch(files.url("tool.zip"), _sha256(good))

    assert data == good
    assert (cache / _sha256(good)).read_bytes() == good
    assert files.requests == ["/tool.zip"]
    assert not list(cache.glob("*.part")), "a temporary file was left behind"


def test_a_write_that_dies_halfway_leaves_nothing_under_the_final_name(files, cache, monkeypatch):
    """Atomic: the bytes go to a temporary name and are renamed once complete.
    A crash halfway through must not leave a half file under the name the
    next run trusts - which is exactly how the file of the test above comes
    to exist."""
    good = b"the real archive, all of it"
    files.files["tool.zip"] = good
    written = []
    real_write = Path.write_bytes

    def dies_halfway(self, data):
        real_write(self, data[: len(data) // 2])
        written.append(self)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", dies_halfway)
    with pytest.raises(SystemExit):
        build_payload.fetch(files.url("tool.zip"), _sha256(good))

    assert written, "the write was never attempted"
    assert not (cache / _sha256(good)).exists(), "a half file sits under the final name"


def test_a_server_that_closes_the_connection_is_one_sentence(files, cache):
    """A network failure is a sentence naming the URL, not a traceback."""
    files.drop = True

    with pytest.raises(SystemExit) as refused:
        build_payload.fetch(files.url("tool.zip"), _sha256(b"whatever"))

    sentence = str(refused.value)
    assert files.url("tool.zip") in sentence
    assert "\n" not in sentence
    assert not (cache / _sha256(b"whatever")).exists()
    assert not list(cache.glob("*.part"))


def test_a_download_with_the_wrong_sum_is_refused_and_not_cached(files, cache):
    """The check that was already there, kept: a mismatch stops the build and
    leaves no file the next run could trust."""
    files.files["tool.zip"] = b"not what was pinned"

    with pytest.raises(SystemExit) as refused:
        build_payload.fetch(files.url("tool.zip"), _sha256(b"what was pinned"))

    assert "checksum mismatch" in str(refused.value)
    assert not list(cache.iterdir()) if cache.exists() else True


# --- a clone under tmp_path, with a uv and an engine that only record ---------------

UV_BYTES = b"not really uv 0.12.13\n"
FFMPEG_BYTES = b"not really ffmpeg\n"
FFPROBE_BYTES = b"not really ffprobe\n"
FOOTPRINT = {"environment": {"win32": {"unpacked_gb": 5.1}, "darwin": {"unpacked_gb": None},
                             "linux": {"unpacked_gb": None}}}
STEPS = ("health", "disk", "tools", "sync", "stamp", "env", "setup", "prove", "start")


def _zip(members: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _tools(files: _Files) -> dict:
    """packaging/tools.json with every URL pointing at the loopback server.
    Zips for every platform: the fake is about the pin and the sum, not the
    archive format, and `extract` picks the reader by the URL's suffix."""
    uv_members = {
        "windows-x64": {"uv.exe": "uv.exe"},
        "macos-arm64": {"uv-aarch64-apple-darwin/uv": "uv"},
        "linux-x64": {"uv-x86_64-unknown-linux-gnu/uv": "uv"},
    }
    tools: dict = {"uv": {"version": "0.12.13", "platforms": {}},
                   "ffmpeg": {"version": "8.1.2", "platforms": {}}}
    for key, members in uv_members.items():
        data = _zip({member: UV_BYTES for member in members})
        files.files[f"{key}-uv.zip"] = data
        tools["uv"]["platforms"][key] = {"url": files.url(f"{key}-uv.zip"), "sha256": _sha256(data),
                                         "members": members}
    for key, suffix in (("windows-x64", ".exe"), ("linux-x64", "")):
        members = {f"ff/bin/ffmpeg{suffix}": f"ffmpeg{suffix}", f"ff/bin/ffprobe{suffix}": f"ffprobe{suffix}",
                   "ff/LICENSE.txt": "../licenses/ffmpeg-LICENSE.txt"}
        data = _zip({f"ff/bin/ffmpeg{suffix}": FFMPEG_BYTES, f"ff/bin/ffprobe{suffix}": FFPROBE_BYTES,
                     "ff/LICENSE.txt": b"LGPL"})
        files.files[f"{key}-ffmpeg.zip"] = data
        tools["ffmpeg"]["platforms"][key] = {"url": files.url(f"{key}-ffmpeg.zip"), "sha256": _sha256(data),
                                             "members": members}
    tools["ffmpeg"]["platforms"]["macos-arm64"] = {"source_url": "https://ffmpeg.org/releases/ffmpeg.tar.xz",
                                                   "build": "packaging/build_ffmpeg_macos.sh"}
    return tools


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_checkout(repo: Path) -> None:
    """The files install.py reads from a clone, and nothing else."""
    (repo / "scribe").mkdir(parents=True)
    (repo / "scribe" / "__init__.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    (repo / "scribe" / "doctor.py").write_text("DISK_FLOOR_GB = 10\n", encoding="utf-8")
    (repo / "scribe" / "footprint.json").write_text(json.dumps(FOOTPRINT), encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / ".env.example").write_text("HF_TOKEN=\n# SCRIBE_DATA_DIR=\n", encoding="utf-8")


class _Clone:
    """A clone under tmp_path, driven through `install.main` with the uv and
    the environment's python replaced by scripts that write down their argv,
    their stdin and the environment they were given."""

    def __init__(self, tmp_path: Path, monkeypatch, files: _Files):
        self.repo = tmp_path / "clone"
        _write_checkout(self.repo)
        self.record = tmp_path / "records.jsonl"
        self.layout = install.Layout(self.repo)
        self.default_port = _free_port()
        self.marker = tmp_path / "uv-was-started"
        self.exit_file = tmp_path / "uv-exit"
        self.exit_file.write_text("0")

        # The fake uv: says its version, or "syncs" by making the env python.
        self.fake_uv = tmp_path / "fake_uv.py"
        self.fake_uv.write_text(textwrap.dedent(f"""
            import json, os, sys
            from pathlib import Path
            keep = ("UV_NO_CONFIG", "SCRIBE_DATA_DIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
                    "PYTHONPYCACHEPREFIX", "VIRTUAL_ENV", "PYTHONPATH")
            with open({str(self.record)!r}, "a", encoding="utf-8") as log:
                log.write(json.dumps({{"tool": "uv", "argv": sys.argv[1:], "cwd": os.getcwd(),
                                       "env_names": sorted(os.environ),
                                       "env": {{k: os.environ[k] for k in keep if k in os.environ}}}}) + "\\n")
            if "--version" in sys.argv:
                print("uv 0.12.13 (fake)")
                raise SystemExit(0)
            open({str(self.marker)!r}, "w").close()
            print("Downloading torch"); print("Installed 3 packages")
            exit_code = int(Path({str(self.exit_file)!r}).read_text() or 0)
            if exit_code == 0:
                python = Path({str(self.layout.env_python)!r})
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("")
            raise SystemExit(exit_code)
            """), encoding="utf-8")

        # The fake environment python: records what it was asked to run.
        self.fake_python = tmp_path / "fake_python.py"
        self.fake_python.write_text(textwrap.dedent(f"""
            import json, os, sys
            keep = ("UV_NO_CONFIG", "SCRIBE_DATA_DIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
                    "PYTHONPYCACHEPREFIX", "VIRTUAL_ENV", "PYTHONPATH")
            stdin = sys.stdin.read() if "--apply-stdin" in sys.argv else None
            with open({str(self.record)!r}, "a", encoding="utf-8") as log:
                log.write(json.dumps({{"tool": "python", "argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd(),
                                       "env_names": sorted(os.environ),
                                       "env": {{k: os.environ[k] for k in keep if k in os.environ}}}}) + "\\n")
            print("fake engine:", " ".join(sys.argv[1:]))
            """), encoding="utf-8")

        monkeypatch.setattr(install, "REPO", self.repo)
        monkeypatch.setattr(install, "DEFAULT_PORT", self.default_port)
        monkeypatch.setattr(install, "uv_command", lambda _layout: [sys.executable, str(self.fake_uv)])
        monkeypatch.setattr(install, "python_command", lambda _layout: [sys.executable, str(self.fake_python)])
        payload = install.payload_module()
        monkeypatch.setattr(payload, "TOOLS", _tools(files))
        monkeypatch.setattr(payload, "CACHE", tmp_path / "cache")
        # Nothing here may reach this machine: no home, no library, no .env.
        for name in ("LOCALAPPDATA", "MYSCRIBE_HOME", "XDG_DATA_HOME"):
            monkeypatch.setenv(name, str(tmp_path / "elsewhere"))
        for name in ("SCRIBE_DATA_DIR", "SCRIBE_ENV_FILE"):
            monkeypatch.delenv(name, raising=False)

    def uv_exits(self, code: int) -> None:
        self.exit_file.write_text(str(code))

    def synced(self) -> None:
        """An environment already synced from this lock, as a second run finds it."""
        self.layout.env_python.parent.mkdir(parents=True, exist_ok=True)
        self.layout.env_python.write_text("")
        install.write_stamp(self.layout)

    def records(self) -> list[dict]:
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines() if line]

    def ran(self) -> list[str]:
        return [f"{r['tool']} {' '.join(r['argv'])}" for r in self.records()]


@pytest.fixture
def clone(tmp_path, monkeypatch, files):
    return _Clone(tmp_path, monkeypatch, files)


def _steps(out: str) -> list[str]:
    """The step words of a transcript, first occurrence each, in order."""
    seen: list[str] = []
    for line in out.splitlines():
        step = line[:7].strip()
        if step in STEPS and step not in seen:
            seen.append(step)
    return seen


class _Health:
    """A loopback /health answering one canned body, standing in for a MyScribe."""

    def __init__(self, body: dict):
        canned = json.dumps(body).encode("utf-8")

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(canned)))
                self.end_headers()
                self.wfile.write(canned)

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def serve():
    servers = []

    def start(body: dict) -> int:
        server = _Health(body)
        servers.append(server)
        return server.port

    yield start
    for server in servers:
        server.close()


# --- criterion 2: the guard, in its first second ----------------------------------


def test_the_guard_refuses_an_older_python_in_one_sentence():
    sentence = install.guard(version=(3, 8, 10), platform_name="win32", machine="AMD64")
    assert sentence and "\n" not in sentence
    assert "3.9" in sentence and "3.8.10" in sentence


def test_the_guard_refuses_a_machine_outside_the_three_environments():
    for platform_name, machine in (("linux", "aarch64"), ("win32", "ARM64"), ("darwin", "x86_64"), ("freebsd13", "amd64")):
        sentence = install.guard(version=(3, 12, 0), platform_name=platform_name, machine=machine)
        assert sentence and "\n" not in sentence, (platform_name, machine)
        assert f"{platform_name} {machine}" in sentence
        assert "[tool.uv]" in sentence


def test_the_guard_accepts_the_three_environments_of_pyproject():
    """The tuples are the three markers of pyproject.toml's [tool.uv]
    environments, read here so that the guard and the lock agree."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    markers = re.findall(r"sys_platform == '(\w+)' and platform_machine == '(\w+)'", text)
    assert sorted(markers) == sorted(install.ENVIRONMENTS)
    for platform_name, machine in markers:
        assert install.guard(version=(3, 9, 0), platform_name=platform_name, machine=machine) is None
        assert install.platform_key(platform_name, machine) in build_payload.PLATFORMS


def _shim(tmp_path: Path, clone: Path, *, version=None, platform_name=None, machine=None, argv=()) -> subprocess.CompletedProcess:
    """Run a copy of install.py as a subprocess, with the interpreter's own
    version and platform replaced before the file runs."""
    shim = tmp_path / "shim.py"
    # The stdlib first, on the real platform: urllib.request imports a macOS
    # module when it sees `darwin` at import time, and it is the guard under
    # test here, not the interpreter's own platform-specific imports.
    lines = ["import platform, runpy, sys, urllib.request, shutil, subprocess"]
    if version is not None:
        lines.append(f"sys.version_info = {version!r}")
    if platform_name is not None:
        lines.append(f"sys.platform = {platform_name!r}")
    if machine is not None:
        lines.append(f"platform.machine = lambda: {machine!r}")
    lines.append(f"sys.argv = ['install.py', *{list(argv)!r}]")
    lines.append(f"runpy.run_path({str(clone / 'install.py')!r}, run_name='__main__')")
    shim.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return subprocess.run([sys.executable, str(shim)], capture_output=True, text=True, cwd=str(clone),
                          stdin=subprocess.DEVNULL, timeout=120)


def _copy_of_install(tmp_path: Path) -> Path:
    """install.py and build_payload.py copied beside a fake checkout, so that
    `REPO`, which install.py takes from its own location, is under tmp_path."""
    clone = tmp_path / "copy"
    _write_checkout(clone)
    (clone / "packaging").mkdir()
    shutil.copy2(INSTALL_PATH, clone / "install.py")
    shutil.copy2(BUILD_PAYLOAD_PATH, clone / "packaging" / "build_payload.py")
    shutil.copy2(REPO / "packaging" / "tools.json", clone / "packaging" / "tools.json")
    return clone


def test_a_refused_python_or_machine_exits_before_anything_is_fetched(tmp_path):
    """One line, exit 1, and neither .tools nor a download cache appears. The
    copy carries the real tools.json - nothing in it is reached."""
    clone = _copy_of_install(tmp_path)
    for kwargs in ({"version": (3, 8, 0, "final", 0)}, {"platform_name": "darwin", "machine": "x86_64"}):
        done = _shim(tmp_path, clone, **kwargs)
        assert done.returncode == 1, done.stdout + done.stderr
        assert len(done.stdout.strip().splitlines()) == 1, done.stdout
        assert done.stderr == ""
        assert not (clone / ".tools").exists()
        assert not (clone / "packaging" / ".cache").exists()


# --- criteria 3 and 4: the pinned tools -------------------------------------------


def test_the_pinned_uv_is_fetched_verified_and_placed_in_tools_bin(clone, files, capsys):
    """Only the uv of packaging/tools.json: fetched from its URL, checked by
    build_payload.fetch, unpacked into .tools/bin. The uv on PATH is never
    asked for: the sync command names the file in .tools/bin, and nothing in
    install.py looks a uv up on PATH."""
    assert install.main(["--non-interactive"]) == 0
    out = capsys.readouterr().out

    assert clone.layout.uv.read_bytes() == UV_BYTES
    assert f"/{install.platform_key()}-uv.zip" in files.requests
    shipped = _load("myscribe_install_unpatched", INSTALL_PATH)  # `uv_command` as it ships, not the fake
    assert shipped.sync_command(shipped.Layout(clone.repo))[0] == str(clone.repo / ".tools" / "bin" / clone.layout.uv.name)
    source = INSTALL_PATH.read_text(encoding="utf-8")
    assert 'which("uv")' not in source and "which('uv')" not in source
    # The line that names both versions: the pin and what the binary said.
    assert "uv 0.12.13 pinned" in out and "--version` says: uv 0.12.13 (fake)" in out


def test_a_uv_whose_sum_does_not_match_is_refused_and_never_run(clone, tmp_path):
    payload = install.payload_module()
    payload.TOOLS["uv"]["platforms"][install.platform_key()]["sha256"] = _sha256(b"another file")

    with pytest.raises(SystemExit) as refused:
        install.main(["--non-interactive"])

    assert "checksum mismatch" in str(refused.value)
    assert not clone.layout.uv.exists()
    assert clone.ran() == []


def test_ffmpeg_is_fetched_only_when_none_answers_on_path(clone, files, monkeypatch, capsys):
    key = install.platform_key()
    if key == "macos-arm64":
        pytest.skip("no pinned ffmpeg on macOS; the brew line has its own test")
    suffix = ".exe" if key == "windows-x64" else ""

    install.install_tools(clone.layout, key, which=lambda name: None)
    assert (clone.layout.tools_dir / f"ffmpeg{suffix}").read_bytes() == FFMPEG_BYTES
    assert (clone.layout.tools_dir / f"ffprobe{suffix}").read_bytes() == FFPROBE_BYTES
    assert (clone.repo / ".tools" / "licenses" / "ffmpeg-LICENSE.txt").exists()
    assert f"/{key}-ffmpeg.zip" in files.requests

    files.requests.clear()
    shutil.rmtree(clone.repo / ".tools")
    found = str(clone.repo / "somewhere" / f"ffmpeg{suffix}")
    install.install_tools(clone.layout, key, which=lambda name: found)
    assert "found on PATH" in capsys.readouterr().out
    assert not (clone.layout.tools_dir / f"ffmpeg{suffix}").exists()
    assert not any("ffmpeg" in path for path in files.requests)


def test_macos_gets_the_brew_line_and_no_download(clone, files, capsys):
    """tools.json has no macOS ffmpeg (the release builds its own), and
    MyScribe runs no package manager (ADR-017): one line gives the command."""
    layout = install.Layout(clone.repo, windows=False)
    install.install_tools(layout, "macos-arm64", which=lambda name: None)
    out = capsys.readouterr().out

    assert "brew install ffmpeg" in out
    assert (layout.tools_dir / "uv").read_bytes() == UV_BYTES
    assert not any("ffmpeg" in path for path in files.requests)
    assert not list(layout.tools_dir.glob("ffmpeg*"))


def test_tools_from_the_same_pin_are_not_fetched_twice_and_a_moved_pin_is(clone, files):
    key = install.platform_key()
    install.install_tools(clone.layout, key, which=lambda name: "/usr/bin/ffmpeg")
    first = list(files.requests)
    install.install_tools(clone.layout, key, which=lambda name: "/usr/bin/ffmpeg")
    assert files.requests == first, "a tool already in place was fetched again"

    payload = install.payload_module()
    moved = _zip({member: b"uv 0.13.0" for member in payload.TOOLS["uv"]["platforms"][key]["members"]})
    files.files[f"{key}-uv.zip"] = moved
    payload.TOOLS["uv"]["platforms"][key]["sha256"] = _sha256(moved)
    install.install_tools(clone.layout, key, which=lambda name: "/usr/bin/ffmpeg")
    assert clone.layout.uv.read_bytes() == b"uv 0.13.0"


# --- criteria 3, 5, 6, 12 and 14: the sync, its environment, and the order --------


def _uv_sync(clone: _Clone) -> dict:
    (record,) = [r for r in clone.records() if r["tool"] == "uv" and "sync" in r["argv"]]
    return record


def test_the_sync_is_frozen_with_uv_no_config_and_nothing_a_flag_did_not_ask_for(clone):
    assert install.main(["--non-interactive"]) == 0
    sync = _uv_sync(clone)

    assert sync["argv"][:2] == ["sync", "--frozen"]
    assert sync["cwd"] == str(clone.repo)
    assert sync["env"]["UV_NO_CONFIG"] == "1"
    for name in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "PYTHONPYCACHEPREFIX", "SCRIBE_DATA_DIR",
                 "VIRTUAL_ENV", "PYTHONPATH"):
        assert name not in sync["env_names"], name
    for record in clone.records():
        assert "SCRIBE_DATA_DIR" not in record["env_names"], record["argv"]


def test_the_dev_group_is_kept_by_default_and_left_out_with_no_dev(clone):
    assert install.main(["--non-interactive"]) == 0
    assert "--no-dev" not in _uv_sync(clone)["argv"]

    clone.record.unlink()
    clone.layout.stamp_file.unlink()
    assert install.main(["--non-interactive", "--no-dev"]) == 0
    assert "--no-dev" in _uv_sync(clone)["argv"]


def test_the_release_keeps_no_dev_and_only_managed_pythons():
    """The clone's default is the launcher's exception, and the other way
    round; tests/test_launcher.py pins the launcher's side, this line the
    two next to each other."""
    release = launcher.sync_command(launcher.Layout(Path("home"), Path("payload")))
    assert "--no-dev" in release and "only-managed" in release
    assert "--python-preference" not in install.sync_command(install.Layout(Path("clone")))


def test_data_dir_reaches_every_child_and_the_env_file(clone):
    library = clone.repo.parent / "library"
    assert install.main(["--non-interactive", "--data-dir", str(library)]) == 0

    for record in clone.records():
        assert record["env"].get("SCRIBE_DATA_DIR") == str(library), record["argv"]
    lines = clone.layout.env_file.read_text(encoding="utf-8").splitlines()
    assert lines.count(f"SCRIBE_DATA_DIR={library}") == 1
    assert "HF_TOKEN=" in lines, "the .env was not seeded from .env.example first"

    # Given twice, the line is still one line.
    assert install.main(["--non-interactive", "--data-dir", str(library)]) == 0
    lines = clone.layout.env_file.read_text(encoding="utf-8").splitlines()
    assert lines.count(f"SCRIBE_DATA_DIR={library}") == 1


def test_the_steps_run_in_the_launchers_order(clone, capsys):
    """health, disk, tools, sync, stamp, env, setup, prove, start - the order of
    `Launch.prepare` and `first_run` in the launcher, which is what "the same
    sequence" (ADR-015, G2) means. The children agree: the version line, the
    sync, the questions, the proof."""
    assert install.main(["--non-interactive"]) == 0
    out = capsys.readouterr().out

    assert _steps(out) == ["health", "disk", "tools", "sync", "stamp", "env", "setup", "prove", "start"]
    assert clone.ran() == [
        "uv --version",
        f"uv sync --frozen --project {clone.repo}",
        "python -m scribe.setup --apply-stdin",
        f"python -m scribe.setup --prove --port {clone.default_port}",
    ]


def test_a_failed_sync_writes_no_stamp_and_stops_with_uv_s_exit_code(clone, capsys):
    clone.uv_exits(2)
    assert install.main(["--non-interactive"]) == 2
    assert not clone.layout.stamp_file.exists()
    assert "uv sync failed with exit code 2" in capsys.readouterr().out
    assert not any(r["tool"] == "python" for r in clone.records())


def test_the_stamp_is_the_lock_digest_the_version_and_a_time(clone):
    assert install.main(["--non-interactive"]) == 0
    stamp = json.loads(clone.layout.stamp_file.read_text(encoding="utf-8"))
    assert stamp["lock_sha256"] == _sha256((clone.repo / "uv.lock").read_bytes())
    assert stamp["version"] == "9.8.7"
    assert isinstance(stamp["ts"], float)


def test_a_second_run_starts_no_uv_asks_bare_and_proves(clone, monkeypatch, capsys):
    """Idempotent: with the stamp matching, nothing is synced and nothing
    fetched again; the engine is called bare, so it asks only what is open
    (TASK-089.11) - the start scripts send people here after every pull."""
    assert install.main(["--non-interactive"]) == 0
    capsys.readouterr()  # the first run's transcript is not the one under test
    clone.record.unlink()
    clone.marker.unlink()
    monkeypatch.setattr(install, "at_a_terminal", lambda stream=None: True)

    assert install.main([]) == 0
    out = capsys.readouterr().out

    assert not clone.marker.exists() and not any(r["tool"] == "uv" for r in clone.records())
    assert clone.ran() == ["python -m scribe.setup", f"python -m scribe.setup --prove --port {clone.default_port}"]
    assert "nothing to sync" in out
    assert "health" not in _steps(out), "with nothing to sync there is nothing to refuse"


# --- criterion 7: room for the environment ----------------------------------------


def test_too_little_room_is_one_sentence_with_both_numbers_and_nothing_downloaded(clone, files, monkeypatch, capsys):
    """The Windows figure: 5.1 GB unpacked plus the 10 GB floor of scribe/doctor.py."""
    # Only the disk check is told it is on Windows. Patching sys.platform
    # for the figure moved the whole process: on macOS the guard then
    # refused an arm64 win32, and on Linux ssl went looking for the Windows
    # certificate store (TASK-089.24, the first CI runs off Windows).
    shipped = install.enough_disk
    monkeypatch.setattr(install, "enough_disk", lambda layout, _platform: shipped(layout, "win32"))
    small = shutil._ntuple_diskusage(total=100 * install.GB, used=97 * install.GB, free=3 * install.GB)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: small)

    assert install.main(["--non-interactive"]) == 1
    (line,) = [l for l in capsys.readouterr().out.splitlines() if l.startswith("disk")]

    assert "15.1 GB" in line and "3.0 GB" in line and "Nothing was downloaded" in line
    assert files.requests == [] and not (clone.repo / ".tools").exists()
    assert not clone.marker.exists() and clone.ran() == []


def test_a_footprint_nobody_measured_does_not_refuse_and_says_so(clone, monkeypatch, capsys):
    (clone.repo / "scribe" / "footprint.json").write_text(
        json.dumps({"environment": {sys.platform: {"unpacked_gb": None}}}), encoding="utf-8")
    small = shutil._ntuple_diskusage(total=100 * install.GB, used=97 * install.GB, free=3 * install.GB)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: small)

    assert install.main(["--non-interactive"]) == 0
    (line,) = [l for l in capsys.readouterr().out.splitlines() if l.startswith("disk")]
    assert "has not been measured" in line and "3.0 GB free" in line
    assert clone.marker.exists()


def test_the_floor_is_read_from_doctor_py_as_text(clone):
    assert install.disk_floor_gb(clone.layout) == 10
    (clone.repo / "scribe" / "doctor.py").write_text("DISK_FLOOR_GB = 25\n", encoding="utf-8")
    assert install.disk_floor_gb(clone.layout) == 25
    real = re.search(r"^DISK_FLOOR_GB = (\d+)$", (REPO / "scribe" / "doctor.py").read_text(encoding="utf-8"), re.M)
    assert install.disk_floor_gb(install.Layout(REPO)) == int(real.group(1))


# --- criterion 9: /health says what it serves, and doubt refuses ------------------


def test_a_myscribe_from_another_tree_does_not_block_the_sync(clone, serve):
    port = serve({"ok": True, "version": "9.9.9", "app_dir": str(clone.repo.parent / "elsewhere"),
                  "data_dir": str(clone.repo.parent / "elsewhere" / "data")})
    assert install.main(["--non-interactive", "--port", str(port)]) == 0
    assert clone.marker.exists()


def test_a_myscribe_from_this_checkout_refuses_the_sync(clone, serve, capsys):
    """Compared after normcase(realpath()): another spelling of this directory
    is this directory."""
    spelled = str(clone.repo).upper() if sys.platform == "win32" else str(clone.repo / "scribe" / "..")
    port = serve({"ok": True, "version": "9.9.9", "app_dir": spelled, "data_dir": str(clone.repo / "data")})

    assert install.main(["--non-interactive", "--port", str(port)]) == 1
    (line,) = [l for l in capsys.readouterr().out.splitlines() if l.startswith("health")]

    assert "stop it first, or use --no-sync" in line and str(port) in line
    assert not clone.marker.exists() and clone.ran() == []


def test_nothing_answering_does_not_block_the_sync(clone, capsys):
    assert install.health(clone.default_port) is None
    assert install.main(["--non-interactive"]) == 0
    assert clone.marker.exists()
    (line,) = [l for l in capsys.readouterr().out.splitlines() if l.startswith("health")]
    assert "nothing blocks a sync" in line


def test_an_answer_without_the_fields_is_doubt_and_doubt_refuses(clone, serve, capsys):
    """An older MyScribe answers `ok` and `version` only; it may be this
    checkout, and the sync is refused the same way."""
    port = serve({"ok": True, "version": "0.5.1"})
    assert install.main(["--non-interactive", "--port", str(port)]) == 1
    (line,) = [l for l in capsys.readouterr().out.splitlines() if l.startswith("health")]
    assert "stop it first, or use --no-sync" in line and "older" in line
    assert not clone.marker.exists()


def test_the_default_port_is_asked_besides_the_given_one(clone, serve, monkeypatch):
    """--port adds a port to the probe; the default is always asked."""
    port = serve({"ok": True, "version": "0.5.1"})
    monkeypatch.setattr(install, "DEFAULT_PORT", port)
    assert install.main(["--non-interactive", "--port", str(_free_port())]) == 1
    assert not clone.marker.exists()


def test_the_blind_spot_is_said_in_one_sentence(clone, capsys):
    assert install.main(["--non-interactive", "--port", "4299"]) == 0
    (line,) = [l for l in capsys.readouterr().out.splitlines() if l.startswith("health")]
    assert "any other port is not seen" in line and "--port 4299" in line
    assert f"port {clone.default_port}, 4299" in line


def test_no_sync_asks_no_port_and_runs_no_uv(clone, serve, capsys):
    """The refusal's own way out: with --no-sync the environment is used as
    it is, and a MyScribe that answers from this checkout is the proof's
    business (it queues the doctor job rather than load a model)."""
    clone.synced()
    clone.layout.stamp_file.unlink()  # a sync would be due
    port = serve({"ok": True, "version": "9.9.9", "app_dir": str(clone.repo), "data_dir": str(clone.repo / "data")})
    assert install.main(["--non-interactive", "--no-sync", "--port", str(port)]) == 0
    out = capsys.readouterr().out
    assert "health" not in _steps(out) and not clone.marker.exists()
    assert clone.ran() == ["python -m scribe.setup --apply-stdin", f"python -m scribe.setup --prove --port {port}"]


def test_sync_blocker_compares_paths_the_way_the_engine_does(tmp_path):
    """The engine's `_serves_this_library` compares data_dir after
    normcase(realpath()); this is the same comparison on app_dir."""
    repo = tmp_path / "Clone"
    repo.mkdir()
    assert install.sync_blocker({"ok": True, "app_dir": str(repo / "scribe" / "..")}, 4242, repo)
    assert install.sync_blocker(None, 4242, repo) is None
    assert install.sync_blocker({"ok": True, "app_dir": str(tmp_path / "Other")}, 4242, repo) is None
    assert "older" in install.sync_blocker({"ok": True, "version": "0.5.1"}, 4242, repo)
    assert "older" in install.sync_blocker({"ok": True, "app_dir": ""}, 4242, repo)


# --- criterion 11: the terminal is handed over, or one line says why not --------


def test_with_a_terminal_the_engine_gets_it_bare(clone, monkeypatch):
    monkeypatch.setattr(install, "at_a_terminal", lambda stream=None: True)
    assert install.main([]) == 0
    (setup,) = [r for r in clone.records() if r["argv"] == ["-m", "scribe.setup"]]
    assert setup["stdin"] is None and setup["cwd"] == str(clone.repo)


def test_without_a_terminal_one_line_names_winpty_and_the_run_goes_on(clone, monkeypatch, capsys):
    """stdin is the null device, which on Windows says isatty() True and has
    no console handle - the case Git Bash and a launcher child produce, and
    the reason the rule is GetConsoleMode and not isatty() alone."""
    with open(os.devnull) as devnull:
        monkeypatch.setattr(sys, "stdin", devnull)
        assert install.main([]) == 0
    out = capsys.readouterr().out

    (line,) = [l for l in out.splitlines() if l.startswith("setup") and "winpty" in l]
    assert "PowerShell" in line and "--answers" in line
    assert clone.ran()[-2:] == ["python -m scribe.setup --apply-stdin",
                                f"python -m scribe.setup --prove --port {clone.default_port}"]
    (setup,) = [r for r in clone.records() if "--apply-stdin" in r["argv"]]
    assert setup["stdin"] == "{}"


def test_at_a_terminal_follows_the_engines_rule():
    """The same answer as scribe.setup.at_a_terminal for the same streams: a
    pipe is not a terminal, and neither is the null device on Windows."""
    from scribe import setup

    with open(os.devnull) as devnull:
        assert install.at_a_terminal(devnull) == setup.at_a_terminal(devnull)
    assert install.at_a_terminal(io.StringIO()) is False


def _record_engine(clone_dir: Path, record: Path) -> None:
    """A `scribe.setup` in the checkout that writes down what it was given -
    for the one test that runs install.py as a real process against a real
    venv, where nothing can be monkeypatched."""
    (clone_dir / "scribe" / "setup.py").write_text(textwrap.dedent(f"""
        import json, sys
        with open({str(record)!r}, "a", encoding="utf-8") as log:
            log.write(json.dumps({{"argv": sys.argv[1:], "isatty": sys.stdin.isatty(),
                                   "stdin": sys.stdin.read() if "--apply-stdin" in sys.argv else None}}) + "\\n")
        print("fake engine ran")
        """), encoding="utf-8")


def test_a_real_process_with_devnull_stdin_takes_the_unattended_path(tmp_path, files):
    """End to end and nothing faked but the engine: a copy of install.py run
    by this interpreter, stdin the null device, a .venv made with the venv
    module whose python resolves `-m scribe.setup` to a recorder in the
    checkout. --no-sync, so no uv is started; the tools come from the
    loopback server."""
    clone_dir = _copy_of_install(tmp_path)
    (clone_dir / "packaging" / "tools.json").write_text(json.dumps(_tools(files)), encoding="utf-8")
    record = tmp_path / "real-records.jsonl"
    _record_engine(clone_dir, record)
    venv.EnvBuilder(with_pip=False, symlinks=False).create(clone_dir / ".venv")
    assert install.Layout(clone_dir).env_python.exists()
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("SCRIBE_") and name.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")}
    env.update(LOCALAPPDATA=str(tmp_path / "elsewhere"), MYSCRIBE_HOME=str(tmp_path / "elsewhere"))
    port = _free_port()

    done = subprocess.run([sys.executable, str(clone_dir / "install.py"), "--no-sync", "--port", str(port)],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=str(clone_dir),
                          env=env, timeout=180)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "winpty" in done.stdout and "PowerShell" in done.stdout
    records = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert [r["argv"] for r in records] == [["--apply-stdin"], ["--prove", "--port", str(port)]]
    assert records[0]["stdin"] == "{}"
    assert (clone_dir / ".tools" / "bin").is_dir()
    assert not (clone_dir / ".venv" / install.STAMP_NAME).exists(), "--no-sync must not write a stamp"


# --- criterion 12: the flags -------------------------------------------------------


def test_non_interactive_sends_an_empty_document(clone):
    assert install.main(["--non-interactive"]) == 0
    (setup,) = [r for r in clone.records() if "--apply-stdin" in r["argv"]]
    assert setup["stdin"] == "{}"


def test_answers_file_goes_over_apply_stdin_unchanged(clone, tmp_path):
    document = {"contract": 2, "answers": {"tier": "turbo", "fetch_models": None}}
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(document), encoding="utf-8")

    assert install.main(["--answers", str(answers)]) == 0
    (setup,) = [r for r in clone.records() if "--apply-stdin" in r["argv"]]
    assert json.loads(setup["stdin"]) == document
    assert setup["argv"] == ["-m", "scribe.setup", "--apply-stdin"], "no answer rides on the command line"


def test_an_answers_file_that_is_not_json_is_one_sentence_before_anything_runs(clone, tmp_path, capsys):
    answers = tmp_path / "answers.json"
    answers.write_text("tier: turbo", encoding="utf-8")
    assert install.main(["--answers", str(answers)]) == 1
    assert "not one JSON document" in capsys.readouterr().out
    assert clone.ran() == [] and not (clone.repo / ".tools").exists()


def test_check_runs_the_proof_and_nothing_else(clone, files):
    clone.synced()
    assert install.main(["--check", "--port", "4299"]) == 0
    assert clone.ran() == ["python -m scribe.setup --prove --port 4299"]
    assert files.requests == [] and not (clone.repo / ".tools").exists()
    assert not clone.layout.env_file.exists()


def test_check_without_an_environment_says_so(clone, capsys):
    assert install.main(["--check"]) == 1
    assert "run `python install.py` first" in capsys.readouterr().out
    assert clone.ran() == []


def _help_for(flag: str, capsys) -> str:
    """The wrapped help paragraph of one flag, as one line."""
    with pytest.raises(SystemExit):
        install.main(["--help"])
    text = capsys.readouterr().out
    options = " ".join(text[text.index("options:"):].split())
    start = options.index(flag)
    following = re.search(r" -[-\w]+ ", options[start + len(flag):])
    return options[start:start + len(flag) + (following.start() if following else len(options))]


def test_check_s_help_says_what_it_touches(capsys):
    check = _help_for("--check", capsys)
    assert "scratch directory" in check and "read-only" in check and "installs nothing" in check


def test_start_runs_the_app_after_the_proof_on_the_given_port(clone):
    port = _free_port()
    assert install.main(["--non-interactive", "--start", "--port", str(port)]) == 0
    assert clone.ran()[-2:] == [f"python -m scribe.setup --prove --port {port}", f"python -m scribe --port {port}"]


def test_start_does_not_start_a_second_app(clone, serve):
    port = serve({"ok": True, "version": "9.9.9", "app_dir": str(clone.repo.parent / "elsewhere"), "data_dir": "x"})
    assert install.main(["--non-interactive", "--start", "--port", str(port)]) == 0
    assert not any(r["argv"] == ["-m", "scribe", "--port", str(port)] for r in clone.records())


def test_without_start_the_start_command_is_printed(clone, capsys):
    assert install.main(["--non-interactive"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "-m scribe" in lines[-2] and "python install.py --start" in lines[-1]


# --- criterion 5: a .env that moves the library reaches setup, the proof and the start


def test_a_dotenv_that_moves_the_library_is_honoured_by_setup_prove_and_start(clone, monkeypatch):
    """The children carry no SCRIBE_DATA_DIR from install.py; the clone's own
    `.env` names the directory, and `env.bootstrap()` then `paths.refresh()`
    - the first lines `scribe.setup`, `--prove` and `python -m scribe` all
    execute - lands on it. Real `scribe.env` and `scribe.paths`, copied into
    the clone and run by this interpreter."""
    for name in ("env.py", "paths.py"):
        shutil.copy2(REPO / "scribe" / name, clone.repo / "scribe" / name)
    moved = clone.repo.parent / "moved-library"
    clone.layout.env_file.write_text(f"SCRIBE_DATA_DIR={moved}\n", encoding="utf-8")
    probe = clone.repo / "probe.py"
    probe.write_text(textwrap.dedent(f"""
        import json, os, sys
        before = os.environ.get("SCRIBE_DATA_DIR")
        from scribe import env, paths
        env.bootstrap()
        paths.refresh()
        with open({str(clone.record)!r}, "a", encoding="utf-8") as log:
            log.write(json.dumps({{"tool": "python", "argv": sys.argv[1:], "before": before,
                                   "data_dir": str(paths.DATA_DIR), "env_names": [], "env": {{}}}}) + "\\n")
        """), encoding="utf-8")
    monkeypatch.setattr(install, "python_command", lambda _layout: [sys.executable, str(probe)])
    clone.synced()
    port = _free_port()

    assert install.main(["--non-interactive", "--no-sync", "--start", "--port", str(port)]) == 0

    records = clone.records()
    assert [r["argv"][:2] for r in records] == [["-m", "scribe.setup"], ["-m", "scribe.setup"], ["-m", "scribe"]]
    for record in records:
        assert record["before"] is None, "install.py exported SCRIBE_DATA_DIR without --data-dir"
        assert record["data_dir"] == str(moved)


# --- criterion 17: the same sync decision as the launcher, from the same lock and stamp


def _both_doors(tmp_path: Path):
    payload = tmp_path / "payload"
    (payload / "app" / "scribe").mkdir(parents=True)
    (payload / "bin").mkdir()
    (payload / "app" / "scribe" / "__init__.py").write_text('__version__ = "9.8.7"\n', encoding="utf-8")
    release = launcher.Layout(tmp_path / "home", payload)
    clone_dir = tmp_path / "clone"
    _write_checkout(clone_dir)
    clone_layout = install.Layout(clone_dir)
    lock = b"version = 1\n[[package]]\nname = 'torch'\n"
    release.lock_file.write_bytes(lock)
    clone_layout.lock_file.write_bytes(lock)
    return release, clone_layout


def test_both_doors_make_the_same_sync_decision_from_the_same_lock_and_stamp(tmp_path):
    """The contract (ADR-015, G2): identical uv.lock bytes, the same stamp
    document written to both stamp paths, and one answer from each door in
    four states - no environment, no stamp, a match, a lock that moved."""
    release, clone_layout = _both_doors(tmp_path)

    assert launcher.needs_sync(release) is True and install.needs_sync(clone_layout) is True  # no environment

    for python in (release.env_python, clone_layout.env_python):
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("")
    assert launcher.needs_sync(release) is True and install.needs_sync(clone_layout) is True  # no stamp

    launcher.write_stamp(release)
    clone_layout.stamp_file.parent.mkdir(parents=True, exist_ok=True)
    clone_layout.stamp_file.write_bytes(release.stamp_file.read_bytes())  # the same document, both doors
    assert launcher.needs_sync(release) is False and install.needs_sync(clone_layout) is False  # a match

    moved = b"version = 1\n[[package]]\nname = 'torch'\n# bumped\n"
    release.lock_file.write_bytes(moved)
    clone_layout.lock_file.write_bytes(moved)
    assert launcher.needs_sync(release) is True and install.needs_sync(clone_layout) is True  # the lock moved

    install.write_stamp(clone_layout)
    launcher.write_stamp(release)
    mine = json.loads(clone_layout.stamp_file.read_text(encoding="utf-8"))
    theirs = json.loads(release.stamp_file.read_text(encoding="utf-8"))
    assert mine.keys() == theirs.keys() == {"lock_sha256", "version", "ts"}
    assert mine["lock_sha256"] == theirs["lock_sha256"] == launcher.lock_digest(release) == install.lock_digest(clone_layout)
    assert install.STAMP_NAME == launcher.STAMP_NAME
    assert launcher.needs_sync(release) is False and install.needs_sync(clone_layout) is False


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 9))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0] if node.level == 0 else f"<relative:{node.module}>")
    return roots


def test_the_three_stdlib_only_files_parse_as_3_9_and_import_only_the_stdlib():
    """Criterion 14: install.py, build_payload.py and the launcher all run
    before the environment exists. The one exception is named: build_payload
    imports its sibling fetch_models inside `build()`, which install.py never
    calls."""
    allowed = set(sys.stdlib_module_names)
    assert _import_roots(INSTALL_PATH) <= allowed, _import_roots(INSTALL_PATH) - allowed
    assert _import_roots(LAUNCHER_PATH) <= allowed, _import_roots(LAUNCHER_PATH) - allowed
    assert _import_roots(BUILD_PAYLOAD_PATH) - allowed == {"fetch_models"}


def test_install_py_imports_nothing_from_the_launcher_or_the_app():
    """ADR-015's Enforcement rules for install.py, read out of the record and
    applied here the way the judge applies them, plus the module names."""
    text = next(REPO.glob("docs/adr/ADR-015*.md")).read_text(encoding="utf-8")
    block = json.loads(text[text.index("## Enforcement"):].split("```json", 1)[1].split("```", 1)[0])
    rules = [rule["pattern"] for rule in block["forbid_import"] if rule["path_glob"] == "install.py"]
    assert rules, "ADR-015 no longer carries the install.py rule"
    source = INSTALL_PATH.read_text(encoding="utf-8")
    for pattern in rules:
        assert not re.search(pattern, source, re.M), pattern
    for rule in block["forbid_pattern"]:
        if "install.py" in rule["path_glob"]:
            assert not re.search(rule["pattern"], source), rule["message"]
    assert not ({"myscribe_launcher", "scribe", "packaging"} & _import_roots(INSTALL_PATH))
    assert install.BUILD_PAYLOAD == REPO / "packaging" / "build_payload.py"


# --- criterion 13: the start scripts compare the stamp ----------------------------


def _script_repo(root: Path, stamp_digest: str | None) -> Path:
    repo = root / "scripted"
    (repo / "scripts").mkdir(parents=True)
    for name in ("start.sh", "start.ps1"):
        shutil.copy2(REPO / "scripts" / name, repo / "scripts" / name)
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts" / "python.exe").write_bytes(b"MZ not an exe")
    real = str(sys.executable).replace("\\", "/")
    (repo / ".venv" / "bin" / "python").write_text(
        f'#!/bin/sh\nif [ "$1" = "-m" ]; then echo "would start: $*"; exit 0; fi\nexec "{real}" "$@"\n',
        encoding="utf-8")
    (repo / ".venv" / "bin" / "python").chmod(0o755)
    if stamp_digest is not None:
        (repo / ".venv" / install.STAMP_NAME).write_text(json.dumps({"lock_sha256": stamp_digest}), encoding="utf-8")
    return repo


def _bash(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """The bash PATH resolves to, by its full path: a bare `bash` goes through
    CreateProcess on Windows, which looks in System32 first and finds WSL's
    launcher there, and that one cannot open a `C:/` path."""
    script = str(repo / "scripts" / "start.sh").replace("\\", "/")  # a path bash reads as one
    return subprocess.run([shutil.which("bash"), script, *args], capture_output=True, text=True,
                          cwd=str(repo), stdin=subprocess.DEVNULL, timeout=120)


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash on PATH: start.sh is not driven here, only read")
def test_start_sh_sends_a_stale_environment_back_to_install_py(tmp_path):
    port = str(_free_port())
    stale = _script_repo(tmp_path / "stale", _sha256(b"another lock"))
    done = _bash(stale, "--port", port)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "run python install.py" in done.stdout + done.stderr
    assert "would start" not in done.stdout

    fresh = _script_repo(tmp_path / "fresh", _sha256((stale / "uv.lock").read_bytes()))
    done = _bash(fresh, "--port", port)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "would start: -m scribe --port " + port in done.stdout

    unstamped = _script_repo(tmp_path / "unstamped", None)
    done = _bash(unstamped, "--port", port)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "python install.py" in done.stdout + done.stderr and "would start" in done.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="no pwsh on PATH: start.ps1 is not driven here, only read")
@pytest.mark.skipif(sys.platform != "win32", reason="start.ps1 is the Windows start script (Test-NetConnection); macOS and Linux use start.sh, driven above")
def test_start_ps1_sends_a_stale_environment_back_to_install_py(tmp_path):
    def pwsh(repo: Path, port: int) -> subprocess.CompletedProcess:
        # `-File script --port N`: the remaining arguments bind to $Args.
        return subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", str(repo / "scripts" / "start.ps1"),
                               "--port", str(port)],
                              capture_output=True, text=True, cwd=str(repo), stdin=subprocess.DEVNULL, timeout=180)

    stale = _script_repo(tmp_path / "stale", _sha256(b"another lock"))
    done = pwsh(stale, _free_port())
    assert done.returncode == 1, done.stdout + done.stderr
    assert "run python install.py" in done.stdout + done.stderr

    # A matching stamp reaches the port check: something listens there, so
    # the script says so and exits 0 without starting anything.
    fresh = _script_repo(tmp_path / "fresh", _sha256((stale / "uv.lock").read_bytes()))
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        done = pwsh(fresh, taken.getsockname()[1])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "already answers" in done.stdout


def test_no_script_tells_anyone_to_pip_install_a_requirements_file():
    for script in sorted((REPO / "scripts").iterdir()):
        if script.suffix in (".sh", ".ps1"):
            text = script.read_text(encoding="utf-8")
            assert "requirements" not in text and "pip install" not in text, script.name
            assert "install.py" in text, script.name


# --- criteria 15 and 16: the README, the payload, no shortcut, the flag --------------


def test_install_py_is_not_in_the_payload():
    assert "install.py" not in build_payload.APP_PATHS


def test_the_readme_s_from_a_clone_section_is_install_py_and_the_start():
    # Moved from README.md to the installation guide on 2026-09-27.
    text = (REPO / "docs" / "installation.md").read_text(encoding="utf-8")
    section = text.split("## From a clone", 1)[1].split("\n## ", 1)[0]
    assert "python install.py" in section
    assert "uv sync" not in section
    assert "--data-dir" in section and "git clean -fdx" in section


def test_no_shortcut_no_desktop_entry_and_no_new_start_script():
    source = INSTALL_PATH.read_text(encoding="utf-8").lower()
    for word in ("desktop", ".lnk", "autostart", "shortcut"):
        assert word not in source, word
    scripts = sorted(p.name for p in (REPO / "scripts").iterdir() if p.is_file())
    assert [s for s in scripts if s.startswith("start")] == ["start.ps1", "start.sh"]
    assert not [s for s in scripts if "install" in s or s.endswith(".desktop")]


def test_data_dir_s_help_says_that_git_clean_deletes_a_library_inside_the_clone(capsys):
    flag = _help_for("--data-dir", capsys)
    assert "git clean -fdx" in flag and "inside the clone" in flag
