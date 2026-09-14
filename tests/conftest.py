"""Shared pytest fixtures for the scribe test suite.

Task 1 keeps this minimal; the tmp-DB fixture and fake-runner helpers
arrive with the tasks that need them (db, jobs, supervisor).
"""

import pytest

import shutil
import threading

from scribe import applog, paths


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch):
    """Every test runs on a volume with room, unless it says otherwise.

    TASK-043 put a floor under the url stage: it refuses below
    `doctor.DISK_FLOOR_GB` free at `paths.DATA_DIR`, and DATA_DIR is a
    tmp_path here - so without this, the whole import suite would quietly
    depend on the free space of the machine's TEMP volume, and fail with
    DISK_LOW on a small disk while passing on a big one. A test about the
    floor patches `shutil.disk_usage` itself and wins, because monkeypatch
    applies it after this fixture.
    """
    plenty = shutil._ntuple_diskusage(total=1000 * 2**30, used=100 * 2**30, free=900 * 2**30)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: plenty)


@pytest.fixture(autouse=True)
def _own_log_dir(tmp_path, monkeypatch):
    """Every test writes its application log under its own tmp_path.

    applog resolves paths.LOGS_DIR at every call, so a test that patched
    DB_PATH and DATA_DIR but not LOGS_DIR - most of them, written before the
    log existed - would append to the developer's real data/logs/app.log.
    It happened: 190 lines from fake runner jobs were found there the day the
    log went in. Autouse, so no future fixture has to remember.
    """
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "applog")
    # runner.main and Supervisor._loop name the writer in-process; without
    # this the name leaks from one test into the next.
    monkeypatch.setattr(applog, "_default_proc", None)
    monkeypatch.setattr(applog, "_names", threading.local())
