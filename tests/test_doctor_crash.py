"""TASK-093 criterion 2: a doctor run that loses its process is not a blank terminal.

The doctor collects every result and renders once at the end, so a check that
kills the process takes the whole table with it. TASK-089.12 prints each
check's name as it starts, but only to a terminal: the three runs that measured
the crash had their output redirected and left 0 bytes behind.

Each test starts a real child process whose one check dies of an access
violation, the way TASK-093's model constructor does, and reads what the child
left on its two streams. The child's environment has PYTHONFAULTHANDLER
removed and no -X faulthandler, so any traceback in it was put there by the
doctor and not by the test.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

CHILD = r"""
import faulthandler
import sys

from scribe import doctor


def check_that_dies():
    if sys.argv[2] == "access-violation":
        # What TASK-093's constructor does: touch an address nobody owns. A
        # write through from_address, and not ctypes.string_at: that one is a
        # foreign call, which ctypes wraps and turns into an OSError on
        # Windows - a Python traceback that names the check with or without
        # the doctor's help. Measured: this line exits 139 under Git Bash,
        # the orchestrator's number, and prints "access violation".
        import ctypes

        ctypes.c_int.from_address(0).value = 1
    faulthandler._sigsegv()


doctor.CPU_CHECKS = (check_that_dies,)
doctor.GPU_CHECKS = ()
if sys.argv[1] == "terminal":
    doctor._stderr_is_a_terminal = lambda: True
raise SystemExit(doctor.main(["--no-gpu"]))
"""


def crash(tmp_path, how: str, death: str = "sigsegv") -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONFAULTHANDLER"}
    env["SCRIBE_DATA_DIR"] = str(tmp_path / "data")
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    env["SCRIBE_ENV_FILE"] = str(empty)
    return subprocess.run(
        [sys.executable, "-c", CHILD, how, death],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("death", ["sigsegv", "access-violation"])
def test_a_crash_in_a_redirected_run_still_names_the_check_it_died_in(tmp_path, death):
    """The case TASK-093 measured: output not on a terminal. Two deaths: the
    SIGSEGV faulthandler raises itself, and a real read of address 0, which on
    Windows is the access violation the orchestrator's runs died of."""
    child = crash(tmp_path, "redirected", death)

    assert child.returncode != 0, "the child really crashed"
    assert child.stdout == "", "no table: the process died before rendering"
    assert "check_that_dies" in child.stderr, (
        f"exit {child.returncode}; stderr was {len(child.stderr)} bytes: {child.stderr!r}"
    )


def test_a_crash_on_a_terminal_shows_the_progress_line_and_the_frame(tmp_path):
    """TASK-089.12's progress line was already there on a terminal; this is
    the crashing run that shows it, and the frame the doctor now adds."""
    child = crash(tmp_path, "terminal")

    assert child.returncode != 0
    lines = child.stderr.splitlines()
    assert lines and lines[0] == "checking check_that_dies", child.stderr
    assert "check_that_dies" in "\n".join(lines[1:]), child.stderr


def test_the_doctor_leaves_a_faulthandler_somebody_else_enabled_alone(monkeypatch, capsys):
    """pytest enables faulthandler on its own file; the doctor run in-process
    must not take it over, or a later crash in the suite writes nowhere."""
    import faulthandler

    from scribe import doctor

    assert faulthandler.is_enabled()
    called = []
    monkeypatch.setattr(faulthandler, "enable", lambda *a, **kw: called.append(kw))
    monkeypatch.setattr(doctor, "CPU_CHECKS", ())
    monkeypatch.setattr(doctor, "GPU_CHECKS", ())
    monkeypatch.setattr(doctor, "render", lambda results: "")

    doctor.main(["--no-gpu"])

    assert called == []
