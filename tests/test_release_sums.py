"""What the publish job puts in SHA256SUMS (TASK-102.07).

The build uploads each artifact with a `.sha256` beside it, and the publish
step used to hash everything it downloaded - so SHA256SUMS also listed the
three `.sha256` files, and a plain `shasum -c SHA256SUMS` failed on the files
a person had not downloaded with the artifact (the outside macOS walk of
0.8.0, report section 7). The test runs the step's own script, read from the
workflow, so it cannot drift from what the release does.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"

BASH = shutil.which("bash")
"""By its full path, never the bare name: Windows' CreateProcess looks in
System32 before PATH, so `bash` there starts the WSL stub - which ran this test
in Linux on a laptop with a distribution and failed on a hosted runner without
one - while `shutil.which` follows PATH to Git Bash."""


def _a_working_bash() -> bool:
    if BASH is None or shutil.which("sha256sum") is None:
        return False
    try:
        probe = subprocess.run([BASH, "-c", "echo ok | sha256sum"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _a_working_bash(), reason="needs a working bash with sha256sum")


def _sums_script() -> str:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    (step,) = [step for step in jobs["publish"]["steps"] if step.get("name") == "SHA256SUMS"]
    return step["run"]


def _publish(tmp_path: Path, names: list[str]) -> list[str]:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for name in names:
        (artifacts / name).write_bytes(name.encode())
    subprocess.run([BASH, "-e", "-c", _sums_script()], cwd=tmp_path, check=True,
                   capture_output=True)
    return [line.split()[-1].lstrip("*")
            for line in (artifacts / "SHA256SUMS").read_text(encoding="utf-8").splitlines()]


def test_the_sums_list_the_artifacts_and_not_their_sha256_files(tmp_path):
    listed = _publish(tmp_path, ["MyScribe-9.9.9-macos-arm64.dmg", "MyScribe-9.9.9-macos-arm64.dmg.sha256",
                                 "MyScribe-9.9.9-windows-x64.exe", "MyScribe-9.9.9-windows-x64.exe.sha256"])
    assert listed == ["MyScribe-9.9.9-macos-arm64.dmg", "MyScribe-9.9.9-windows-x64.exe"]


def test_the_sums_check_out_against_the_artifacts(tmp_path):
    _publish(tmp_path, ["MyScribe-9.9.9-linux-x64.AppImage", "MyScribe-9.9.9-linux-x64.AppImage.sha256"])
    result = subprocess.run([BASH, "-c", "sha256sum -c SHA256SUMS"], cwd=tmp_path / "artifacts",
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
