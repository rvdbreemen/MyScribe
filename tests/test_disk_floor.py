"""A download refuses to start when the disk is nearly full (TASK-043).

The doctor has measured free space since the beginning, but its answer is
advice on a page: a feed that fans out into five hundred jobs will keep
downloading until a write fails, and a failed write leaves a partial file
behind. The floor belongs where the work starts, and it is the doctor's own
number - one constant, so the advice and the refusal can never disagree.

The refusal is deliberately its own error code. `DISK_FULL` means a write hit
a full volume; `DISK_LOW` means nothing was attempted.
"""

from __future__ import annotations

import shutil

import pytest

from scribe import doctor, jobs, paths, runner
from scribe.ingest import urls
from scribe.stages import url_stage

from tests.test_ingest_urls import (  # noqa: F401  (fixtures)
    FakeYdl,
    build_returning,
    conn,
    data_dir,
    make_ctx,
    single_info,
    url_job,
)


def _usage(free_gb: float):
    total = 1000 * 2**30
    free = int(free_gb * 2**30)
    return shutil._ntuple_diskusage(total=total, used=total - free, free=free)


def _with_free(monkeypatch, free_gb: float) -> list:
    """Fake the volume, and remember what was measured.

    The returned list is the point: every fake here used to be
    `lambda _path: ...`, which throws the argument away - so no test could
    observe *which* volume was probed, the one thing the guard exists to get
    right, and a regression that measured the system temp drive would have
    shipped green.
    """
    asked: list = []

    def usage(path):
        asked.append(path)
        return _usage(free_gb)

    monkeypatch.setattr(shutil, "disk_usage", usage)
    return asked


# --- the floor itself ----------------------------------------------------------


def test_the_guard_and_the_doctor_read_one_number(monkeypatch, data_dir):  # noqa: F811
    """AC1. Not "both say 10" - the same attribute, so moving it moves both."""
    _with_free(monkeypatch, doctor.DISK_FLOOR_GB - 0.5)
    assert doctor.check_disk_space().ok is False
    with pytest.raises(doctor.NotEnoughDisk):
        doctor.require_disk_headroom()

    monkeypatch.setattr(doctor, "DISK_FLOOR_GB", 1)
    _with_free(monkeypatch, 2)
    assert doctor.check_disk_space().ok is True
    doctor.require_disk_headroom()  # no raise


def test_the_refusal_names_the_free_space_and_the_floor(monkeypatch, data_dir):  # noqa: F811
    """AC2. A number a person can act on, not "disk error"."""
    _with_free(monkeypatch, 3.25)

    with pytest.raises(doctor.NotEnoughDisk) as caught:
        doctor.require_disk_headroom()

    message = str(caught.value)
    assert "3.2 GB" in message and str(doctor.DISK_FLOOR_GB) in message
    assert str(doctor.disk_probe_path()) in message


def test_the_volume_measured_is_the_one_the_data_lands_on(monkeypatch, data_dir):  # noqa: F811
    """AC1's other half. The message promises room "where the data lands", and
    on this machine the model cache is on another drive entirely - so probing
    anything but DATA_DIR would refuse the wrong downloads and allow the right
    ones. `data_dir` points paths.DATA_DIR at a tmp_path, and the guard has to
    follow it there.
    """
    asked = _with_free(monkeypatch, doctor.DISK_FLOOR_GB + 5)

    doctor.require_disk_headroom()

    assert asked == [paths.DATA_DIR]
    assert doctor.disk_probe_path() == paths.DATA_DIR


def test_the_floor_is_a_minimum_not_a_forbidden_number(monkeypatch, data_dir):  # noqa: F811
    """Exactly at the floor is allowed, on both sides of the one constant.
    Neither `<` -> `<=` in the guard nor `>=` -> `>` in the check was caught by
    anything: the tests sat at floor-0.5 and floor+0.001 and never on it."""
    _with_free(monkeypatch, doctor.DISK_FLOOR_GB)

    doctor.require_disk_headroom()  # no raise
    assert doctor.check_disk_space().ok is True


def test_a_volume_that_cannot_be_measured_does_not_refuse(monkeypatch, data_dir):  # noqa: F811
    """"We do not know" must not become "no imports on this machine". The
    doctor already reports an unmeasurable volume as a red check."""
    def boom(_path):
        raise OSError("no such device")

    monkeypatch.setattr(shutil, "disk_usage", boom)

    doctor.require_disk_headroom()  # no raise
    assert doctor.check_disk_space().ok is False


# --- where it bites ------------------------------------------------------------


def test_a_download_is_refused_before_a_byte_is_fetched(conn, data_dir, monkeypatch):  # noqa: F811
    """AC2 and AC3: the guard runs in the stage, so every job of a fan-out
    meets it on its own, and nothing is downloaded."""
    calls = []
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(single_info())))
    monkeypatch.setattr(urls, "probe", lambda *a, **k: calls.append("probe"))
    _with_free(monkeypatch, 1.0)
    ctx = make_ctx(conn, url_job(conn))

    with pytest.raises(doctor.NotEnoughDisk):
        url_stage.fetch(ctx)

    assert calls == []  # not even the probe: no network, no bytes


def test_a_download_runs_when_the_floor_is_met(conn, data_dir, monkeypatch):  # noqa: F811
    _with_free(monkeypatch, doctor.DISK_FLOOR_GB + 0.001)
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(single_info())))
    ctx = make_ctx(conn, url_job(conn))

    url_stage.fetch(ctx)

    assert ctx.state["downloaded"].path.is_file()


def test_the_board_tells_a_full_disk_from_a_failed_download(conn, data_dir, monkeypatch):  # noqa: F811
    """AC4. Two codes, because they ask for two different things: free up
    space, or look at the network."""
    assert runner._error_code(doctor.NotEnoughDisk("no room")) == "DISK_LOW"
    assert runner._error_code(urls.DownloadFailed("404")) == "DOWNLOAD_FAILED"
    assert runner._error_code(OSError(28, "No space left on device")) == "DISK_FULL"


def test_a_refused_job_carries_the_code_and_the_message(conn, data_dir, monkeypatch):  # noqa: F811
    """The whole path: a runner stage that refuses ends as a failed job whose
    row says why, which is what the board renders."""
    _with_free(monkeypatch, 0.5)
    job_id = url_job(conn)

    try:
        url_stage.fetch(make_ctx(conn, job_id))
    except doctor.NotEnoughDisk as exc:
        jobs.finish(conn, job_id, "failed", error_code=runner._error_code(exc), error_detail=str(exc))

    row = conn.execute("SELECT status, error_code, error_detail FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error_code"] == "DISK_LOW"
    assert "GB free" in row["error_detail"]
