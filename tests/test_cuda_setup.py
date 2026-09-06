"""Unit tests for CUDA DLL directory registration (plan Task 8).

These never touch the GPU: they check the registration contract only.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from scribe import cuda_setup


def _reset():
    cuda_setup._applied.clear()


def test_returns_existing_directories_and_is_idempotent():
    _reset()
    try:
        first = cuda_setup.ensure_cuda_libs()
        assert all(os.path.isdir(d) for d in first), f"non-existent dir registered: {first}"

        second = cuda_setup.ensure_cuda_libs()
        assert second == [], "second call must add nothing"
    finally:
        _reset()


def test_does_not_raise_when_nothing_cuda_is_installed(monkeypatch, tmp_path):
    _reset()
    try:
        monkeypatch.setattr(cuda_setup, "_site_packages", lambda: tmp_path)
        assert cuda_setup.ensure_cuda_libs() == []
    finally:
        _reset()


def test_does_not_raise_without_a_site_packages_on_path(monkeypatch):
    _reset()
    try:
        monkeypatch.setattr(cuda_setup, "_site_packages", lambda: None)
        assert cuda_setup.candidate_dirs() == []
        assert cuda_setup.ensure_cuda_libs() == []
    finally:
        _reset()


def test_registered_directories_are_prepended_to_path(monkeypatch):
    _reset()
    try:
        fake = os.path.dirname(os.path.abspath(__file__))
        monkeypatch.setattr(cuda_setup, "candidate_dirs", lambda: [type("P", (), {"__str__": lambda s: fake})()])
        monkeypatch.setenv("PATH", "C:\\preexisting")

        added = cuda_setup.ensure_cuda_libs()

        assert added == [fake]
        assert os.environ["PATH"].startswith(fake)
        assert "C:\\preexisting" in os.environ["PATH"]
    finally:
        _reset()


# --- how a DLL failure is reported ------------------------------------------------


def _error_mode_in_a_child(code: str) -> int:
    """Run `code` in a fresh interpreter and return its process error mode.

    A child, because the error mode is process-wide: setting it here would
    leak into every later test in this run.
    """
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    assert out.returncode == 0, f"child failed: {out.stderr[-500:]}"
    return int(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(sys.platform != "win32", reason="the loader's message box is a Windows thing")
def test_a_failing_dll_is_reported_to_python_instead_of_to_a_message_box():
    """torchcodec 0.16 cannot load against torch 2.8 here (ADR-005), and
    pyannote imports it anyway inside a try/except it handles perfectly well.
    Windows still pops a modal 'Entry Point Not Found' box first, and a runner
    child has nobody in front of it to click OK - so a handled condition became
    a job that never ended. Observed 2026-09-03 on
    libtorchcodec_image.dll/torch_get_const_data_ptr."""
    mode = _error_mode_in_a_child(
        "import ctypes;"
        # Force the condition that actually failed: a child at mode 0. Without
        # this the shell hands down SEM_FAILCRITICALERRORS and the assertion
        # below holds whether or not silence_loader_dialogs did anything.
        " ctypes.windll.kernel32.SetErrorMode(0);"
        " from scribe import cuda_setup;"
        " cuda_setup.silence_loader_dialogs();"
        " print(ctypes.windll.kernel32.GetErrorMode())"
    )

    assert mode & 0x0001, "SEM_FAILCRITICALERRORS not set"
    assert mode & 0x8000, "SEM_NOOPENFILEERRORBOX not set"


@pytest.mark.skipif(sys.platform != "win32", reason="the loader's message box is a Windows thing")
def test_the_runner_silences_the_loader_before_it_does_anything_else():
    """Bad argv, so main() bails at its first check. The mode must be set even
    then: anything that runs before it could be the thing that pops the box."""
    mode = _error_mode_in_a_child(
        "import ctypes;"
        " ctypes.windll.kernel32.SetErrorMode(0);"  # the condition that failed
        " from scribe import runner;"
        " runner.main(['not-a-job-id']);"
        " print(ctypes.windll.kernel32.GetErrorMode())"
    )

    assert mode & 0x0001 and mode & 0x8000
