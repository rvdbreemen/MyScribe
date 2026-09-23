"""`scripts/task093_crash_census.py`: the harness Robert runs for TASK-093.

Tested against stand-in children only - a process that dies of an access
violation, one that fails, one that succeeds - and never against the doctor:
the real runs load a 1.6 GB model twenty times and are Robert's to make.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "task093_crash_census.py"


@pytest.fixture(scope="module")
def census():
    spec = importlib.util.spec_from_file_location("task093_crash_census", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def env_for(census, tmp_path, hidden=True):
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    return census.fenced_env(tmp_path / "data", empty, hidden=hidden)


def test_a_child_that_dies_of_an_access_violation_is_recorded_with_its_frame(census, tmp_path):
    crash = (
        "import ctypes\n"
        "def inside_the_constructor():\n"
        "    ctypes.c_int.from_address(0).value = 1\n"
        "inside_the_constructor()\n"
    )
    record = census.run_one("stand-in", 1, [sys.executable, "-c", crash], env_for(census, tmp_path), 120)

    assert record["exit"] != 0 and not record["timed_out"] and record["crashed"] is True
    assert record["stdout_bytes"] == 0
    assert record["fault"].startswith(("Windows fatal exception", "Fatal Python error")), record
    assert "inside_the_constructor" in record["fault"]
    assert "1/ 1 crashed" in census.summarise([record])
    assert "inside_the_constructor" in census.summarise([record])


def test_a_child_that_fails_or_succeeds_is_not_a_crash(census, tmp_path):
    env = env_for(census, tmp_path)
    failed = census.run_one(
        "stand-in", 1, [sys.executable, "-c", "print('1 required check(s) failed.'); raise SystemExit(1)"], env, 60
    )
    passed = census.run_one("stand-in", 2, [sys.executable, "-c", "print('All required checks passed.')"], env, 60)

    assert (failed["exit"], failed["fault"], failed["last_line"]) == (1, "", "1 required check(s) failed.")
    assert (passed["exit"], passed["fault"], passed["exit_hex"]) == (0, "", "0x00000000")
    assert "0/ 2 crashed" in census.summarise([failed, passed])


def test_a_fault_block_without_a_crash_exit_is_not_counted_as_a_crash(census, tmp_path):
    """faulthandler can print a block for an exception native code then
    handles; the doctor still exits 1 with its table. The count is exit codes."""
    noise = (
        "import sys\n"
        "print('1 required check(s) failed.')\n"
        "print('Windows fatal exception: code 0x8001010d', file=sys.stderr)\n"
        "raise SystemExit(1)\n"
    )
    record = census.run_one("stand-in", 1, [sys.executable, "-c", noise], env_for(census, tmp_path), 60)

    assert record["fault"].startswith("Windows fatal exception")
    assert record["crashed"] is False
    assert "0/ 1 crashed" in census.summarise([record])


def test_a_hung_child_is_recorded_as_a_timeout_not_lost(census, tmp_path):
    record = census.run_one(
        "stand-in", 1, [sys.executable, "-c", "import time; time.sleep(30)"], env_for(census, tmp_path), 1
    )
    assert record["timed_out"] is True and record["exit"] is None and record["crashed"] is False


def test_the_fence_hides_the_card_moves_the_library_and_leaves_the_model_cache(census, tmp_path, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.delenv("HF_HOME", raising=False)

    hidden = env_for(census, tmp_path, hidden=True)
    visible = env_for(census, tmp_path, hidden=False)

    assert hidden["CUDA_VISIBLE_DEVICES"] == "-1"
    assert "CUDA_VISIBLE_DEVICES" not in visible, "a visible variant must not inherit a hidden card"
    assert hidden["SCRIBE_DATA_DIR"] == str(tmp_path / "data")
    assert Path(hidden["SCRIBE_ENV_FILE"]).read_text(encoding="utf-8") == ""
    assert hidden["PYTHONFAULTHANDLER"] == "1"
    assert "HF_HOME" not in hidden, "a fenced model cache would download 1.6 GB per run"


def test_the_plan_is_twenty_full_runs_and_every_reduced_variant(census):
    todo = census.plan("all", 20, 10, "python")

    full = [t for t in todo if t[0] == "full"]
    assert len(full) == 20 and all(t[2] == ["python", "-m", "scribe.doctor"] and t[3] for t in full)
    assert {t[0] for t in todo} - {"full"} == set(census.REDUCED)
    assert len(todo) == 20 + 10 * len(census.REDUCED)


def test_every_reduced_script_is_valid_python(census):
    for name in census.REDUCED:
        compile(census.reduced_script(name), name, "exec")
