"""The application log: one file, every process, nothing lost, nothing secret.

These are the promises applog.py makes in its docstring, each turned into a
test that would fail if the promise were broken rather than one that checks
the code is shaped a certain way.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import threading

import pytest

from scribe import applog, paths


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setattr(paths, "LOGS_DIR", logs)
    monkeypatch.setattr(applog, "_default_proc", None)
    monkeypatch.setattr(applog, "_names", threading.local())
    return logs


def lines_of(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# --- writing --------------------------------------------------------------------------------


def test_a_line_carries_the_when_who_and_what(logs_dir):
    applog.configure("web")

    applog.log("record.start", session="abc", bytes=12)

    (line,) = lines_of(applog.path())
    assert line["event"] == "record.start"
    assert line["proc"] == "web"
    assert line["pid"] == os.getpid()
    assert line["level"] == "info"
    assert line["session"] == "abc" and line["bytes"] == 12
    assert isinstance(line["ts"], float)


def test_secrets_are_redacted_by_field_name_not_by_guessing_at_values(logs_dir):
    """A provider key passes through Settings and through request forms; a log
    that writes fields by name is a new place for it to land."""
    applog.log(
        "settings.saved",
        api_key="sk-live-1234",
        Authorization="Bearer x",
        hf_token="hf_abc",
        password2="hunter2",
        nested={"openai_key": "sk-2", "model": "gpt"},
        model="openrouter/auto",
    )

    (line,) = lines_of(applog.path())
    for name in ("api_key", "Authorization", "hf_token", "password2"):
        assert line[name] == applog.REDACTED, name
    assert line["nested"] == {"openai_key": applog.REDACTED, "model": "gpt"}
    assert line["model"] == "openrouter/auto"
    assert "sk-live-1234" not in applog.path().read_text(encoding="utf-8")


def test_an_unknown_level_becomes_info_and_long_values_are_cut(logs_dir):
    applog.log("x", level="shout", detail="a" * 5000)

    (line,) = lines_of(applog.path())
    assert line["level"] == "info"
    assert len(line["detail"]) == 2000


def test_the_log_never_raises_when_it_cannot_write(logs_dir, monkeypatch, capsys):
    monkeypatch.setattr(applog, "_append", lambda line: (_ for _ in ()).throw(OSError("disk")))

    applog.log("anything")  # must not propagate

    assert "could not write anything" in capsys.readouterr().err


# --- rotation -------------------------------------------------------------------------------


def test_the_file_is_capped_and_one_previous_file_is_kept(logs_dir, monkeypatch):
    """Append-only within a file, never unbounded on disk: at the cap the file
    is renamed to .1 and a fresh one started; a second rotation replaces .1."""
    monkeypatch.setattr(applog, "MAX_BYTES", 600)

    for i in range(40):
        applog.log("fill", n=i, pad="x" * 40)

    current, previous = applog.path(), applog.path().with_name("app.log.1")
    assert current.exists() and previous.exists()
    assert current.stat().st_size <= 600
    assert previous.stat().st_size <= 600
    assert not applog.path().with_name("app.log.2").exists()
    # Every line in both files is whole: a rename moves lines, it never cuts one.
    for path in (current, previous):
        for line in path.read_text(encoding="utf-8").splitlines():
            json.loads(line)
    # And nothing was truncated away silently: the newest line is in `current`.
    assert lines_of(current)[-1]["n"] == 39


# --- two processes, one file ---------------------------------------------------------------


_WRITER = r"""
import sys
from scribe import applog, paths
from pathlib import Path
paths.LOGS_DIR = Path(sys.argv[1])
applog.configure(sys.argv[2])
for i in range(int(sys.argv[3])):
    applog.log("burst", i=i, who=sys.argv[2], filler="f" * 120)
"""


def test_two_processes_appending_at_once_interleave_nothing(logs_dir, tmp_path):
    """open(path, "a") is not an atomic append on Windows: the C runtime seeks
    then writes, and two writers can splice a line. One os.write on an
    O_APPEND descriptor is what the kernel does append atomically. Two
    processes, a thousand lines each, every one parsed back whole."""
    script = tmp_path / "writer.py"
    script.write_text(_WRITER, encoding="utf-8")
    count = 1000
    repo = Path(__file__).resolve().parent.parent
    # A script run by path puts its own directory first on sys.path, not the
    # cwd, so the package has to be named explicitly for the child to find it.
    env = {**os.environ, "PYTHONPATH": str(repo)}
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(logs_dir), name, str(count)],
            cwd=str(repo), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for name in ("a", "b")
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode("utf-8", "replace")

    raw = applog.path().read_bytes()
    lines = raw.split(b"\n")
    assert lines[-1] == b""  # ends on a newline: no half line
    parsed = [json.loads(line) for line in lines[:-1]]
    assert len(parsed) == 2 * count
    for who in ("a", "b"):
        seen = sorted(line["i"] for line in parsed if line["who"] == who)
        assert seen == list(range(count)), f"writer {who} lost or spliced a line"


# --- reading ---------------------------------------------------------------------------------


def test_tail_from_zero_is_the_last_lines_and_the_offset_continues_from_there(logs_dir):
    for i in range(500):
        applog.log("n", i=i)

    lines, offset = applog.tail(0, limit=300)

    assert [line["i"] for line in lines] == list(range(200, 500))
    assert offset == applog.path().stat().st_size

    applog.log("n", i=500)
    more, offset2 = applog.tail(offset)
    assert [line["i"] for line in more] == [500]
    assert offset2 > offset
    # Nothing new: nothing returned, offset unchanged.
    assert applog.tail(offset2) == ([], offset2)


def test_tail_leaves_a_half_written_line_for_the_next_call(logs_dir):
    applog.log("whole", i=1)
    with open(applog.path(), "ab") as fh:
        fh.write(b'{"event": "half"')  # a writer mid-line

    lines, offset = applog.tail(0)

    assert [line["event"] for line in lines] == ["whole"]
    assert offset == applog.path().stat().st_size - len(b'{"event": "half"')


def test_tail_of_a_missing_file_is_empty_not_an_error(logs_dir):
    assert applog.tail(0) == ([], 0)


def test_tail_survives_a_line_that_is_not_json(logs_dir):
    applog.log("ok")
    with open(applog.path(), "ab") as fh:
        fh.write(b"garbage from somewhere\n")

    lines, _ = applog.tail(0)

    assert lines[0]["event"] == "ok"
    assert lines[1]["event"] == "unparseable" and "garbage" in lines[1]["raw"]


# --- review #3 -------------------------------------------------------------------------------


def test_the_supervisor_thread_names_itself_without_renaming_the_web_process(logs_dir):
    """CR-001. The supervisor is a thread in the web process (ADR-001). A
    module-global name set by its loop turned every web request's line into
    proc="supervisor" for as long as the app ran."""
    import threading

    applog.configure("web")
    done = threading.Event()

    def supervisor_loop():
        applog.configure("supervisor", this_thread_only=True)
        applog.log("job.claimed", job=1)
        done.set()

    threading.Thread(target=supervisor_loop).start()
    assert done.wait(5)
    applog.log("record.start", session="s")

    by_event = {line["event"]: line["proc"] for line in lines_of(applog.path())}
    assert by_event == {"job.claimed": "supervisor", "record.start": "web"}


def test_a_url_query_string_and_a_cookies_path_never_reach_the_file(logs_dir):
    """CR-005. A signed share link keeps its credential in the query string and
    is logged under the innocent name `url`; the path of an exported
    cookies.txt is the address of a secret."""
    applog.log(
        "job.enqueued",
        params={"url": "https://x.example/v?token=SECRET123&sig=abc", "cookies_file": "C:/me/cookies.txt"},
        url="https://plain.example/path#frag",
    )

    (line,) = lines_of(applog.path())
    assert line["params"]["url"] == "https://x.example/v?" + applog.REDACTED
    assert line["params"]["cookies_file"] == applog.PRESENT
    assert line["url"] == "https://plain.example/path#frag"
    raw = applog.path().read_text(encoding="utf-8")
    assert "SECRET123" not in raw and "cookies.txt" not in raw


def test_a_tail_that_fell_far_behind_gets_the_newest_lines_and_a_gap(logs_dir, monkeypatch):
    """CR-002. 60,000 lines measured at 1.7 s of Python before rendering; a
    page back from sleep must not freeze a worker on its catch-up."""
    monkeypatch.setattr(applog, "MAX_TAIL_BYTES", 4000)
    applog.log("first")
    _, offset = applog.tail(0)
    for i in range(400):
        applog.log("n", i=i, pad="x" * 40)

    lines, new_offset = applog.tail(offset)

    assert new_offset == applog.path().stat().st_size
    assert lines and lines[-1]["i"] == 399
    assert lines[0]["i"] > 0, "the whole backlog was read"
    for line in lines:
        assert line["event"] == "n"  # every returned line is whole
    # And the limit applies on the catch-up branch too.
    monkeypatch.setattr(applog, "MAX_TAIL_BYTES", 10**9)
    capped, _ = applog.tail(offset, limit=5)
    assert [line["i"] for line in capped] == [395, 396, 397, 398, 399]


def test_writers_hold_the_cross_process_mutex_while_deciding_to_rotate(logs_dir):
    """CR-020. The rotation check and the rename happen under the same mutex
    as the write, so a second process cannot open the old file after the
    first has just renamed it. Pinned on the order of operations in
    _append, which the two-process test cannot see."""
    import inspect

    source = inspect.getsource(applog._append)
    assert source.index("_acquire(mutex)") < source.index("_rotate_if_needed(")
    assert source.index("_acquire(mutex)") < source.index("with _lock:")
