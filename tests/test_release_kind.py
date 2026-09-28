"""Which tags are published as a pre-release (TASK-100).

A beta such as v0.8.0b1 is built and published by the same workflow as a
stable release, but as a GitHub pre-release that does not become Latest: the
README's download link and the repository's website field point at
`releases/latest`, and a beta nobody has tested must not be what they hand
out. The decision is a small script so it can be tested here rather than as an
expression inside the workflow.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "packaging" / "release_kind.py"
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


def _module():
    spec = importlib.util.spec_from_file_location("release_kind", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["0.8.0b1", "0.8.0b12", "1.0.0rc1", "0.9.0a3", "0.8.0.dev2"])
def test_a_pre_release_version_is_a_pre_release(version):
    assert _module().is_prerelease(version) is True


@pytest.mark.parametrize("version", ["0.7.2", "0.8.0", "1.0.0", "10.20.30"])
def test_a_plain_version_is_a_release(version):
    assert _module().is_prerelease(version) is False


def test_the_script_answers_for_the_workflow():
    out = subprocess.run([sys.executable, str(SCRIPT), "0.8.0b1"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "true"
    out = subprocess.run([sys.executable, str(SCRIPT), "0.7.2"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "false"


def test_the_workflow_publishes_a_beta_as_a_pre_release_that_is_not_latest():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "packaging/release_kind.py" in text
    assert "prerelease: ${{ steps.kind.outputs.prerelease }}" in text
    assert "make_latest: ${{ steps.kind.outputs.prerelease == 'true' && 'false' || 'true' }}" in text
