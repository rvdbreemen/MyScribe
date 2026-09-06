"""Tests for the environment gate (plan Task 7)."""

import shutil

import pytest

from scribe import doctor


def _by_name(checks, name):
    for c in checks:
        if c.name == name:
            return c
    raise AssertionError(f"no check named {name!r} in {[c.name for c in checks]}")


def test_cpu_checks_all_pass_on_this_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(doctor.paths, "DB_PATH", tmp_path / "myscribe.db")
    monkeypatch.setattr(doctor.paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(doctor.paths, "LOGS_DIR", tmp_path / "logs")

    checks = [c for c in doctor.checks(include_gpu=False) if not c.optional]

    failed = [(c.name, c.detail) for c in checks if not c.ok]
    assert failed == [], f"CPU checks failed: {failed}"


def test_python_version_check_names_the_running_interpreter():
    check = _by_name(doctor.checks(include_gpu=False), "python")
    assert check.ok is True
    assert "3.12" in check.detail or "3.13" in check.detail


def test_sqlite_check_reports_version_and_fts5():
    check = _by_name(doctor.checks(include_gpu=False), "sqlite")
    assert check.ok is True
    assert "FTS5" in check.detail


def test_ffmpeg_check_parses_a_version_string():
    check = _by_name(doctor.checks(include_gpu=False), "ffmpeg")
    assert check.ok is True
    assert "ffmpeg version" in check.detail.lower()


def test_disk_floor_fails_when_floor_is_above_free_space(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.paths, "DATA_DIR", tmp_path)
    fake = shutil._ntuple_diskusage(total=100 * 2**30, used=99 * 2**30, free=1 * 2**30)
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda _p: fake)

    check = doctor.check_disk_space(floor_gb=10)

    assert check.ok is False
    assert "1.0 GB free" in check.detail
    assert "10" in check.fix_hint


def test_disk_floor_passes_when_free_space_clears_the_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.paths, "DATA_DIR", tmp_path)
    fake = shutil._ntuple_diskusage(total=100 * 2**30, used=10 * 2**30, free=90 * 2**30)
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda _p: fake)

    assert doctor.check_disk_space(floor_gb=10).ok is True


def test_database_check_migrates_to_current_schema_version(tmp_path, monkeypatch):
    db_path = tmp_path / "probe.db"
    monkeypatch.setattr(doctor.paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(doctor.paths, "DB_PATH", db_path)

    check = doctor.check_database()

    assert check.ok is True
    assert str(doctor.db.SCHEMA_VERSION) in check.detail
    assert db_path.exists()


def test_gpu_checks_are_registered_and_required():
    names = [fn.__name__ for fn in doctor.GPU_CHECKS]
    assert names == ["check_gpu_runtime", "check_gpu_smoke"]
    assert all(not c.optional for c in (doctor.check_gpu_runtime(),))


def test_include_gpu_false_omits_the_gpu_checks():
    names = [c.name for c in doctor.checks(include_gpu=False)]
    assert not any(n.startswith("gpu") for n in names)


def test_gpu_smoke_reports_a_missing_clip_instead_of_raising(tmp_path):
    check = doctor.gpu_smoke(clip=tmp_path / "nope.wav")
    assert check.ok is False
    assert "clip missing" in check.detail


def test_main_returns_zero_when_all_required_checks_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(doctor.paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(doctor.paths, "DB_PATH", tmp_path / "myscribe.db")
    monkeypatch.setattr(doctor.paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(doctor.paths, "LOGS_DIR", tmp_path / "logs")

    code = doctor.main(["--no-gpu"])

    out = capsys.readouterr().out
    assert code == 0
    assert "python" in out and "sqlite" in out


def test_main_returns_one_when_a_required_check_fails(monkeypatch, capsys):
    bad = doctor.Check(name="python", ok=False, detail="too old", fix_hint="install 3.12")
    monkeypatch.setattr(doctor, "checks", lambda include_gpu=True: [bad])

    assert doctor.main(["--no-gpu"]) == 1
    assert "install 3.12" in capsys.readouterr().out


def test_smoke_timings_never_land_under_the_transcribe_stage():
    """A 30s smoke runs warmup-dominated (~1x realtime) while real work runs
    ~14x; filing it as a transcribe timing would poison eta_seconds()."""
    source = (doctor.__file__).replace("doctor.py", "doctor.py")
    with open(source, encoding="utf-8") as fh:
        body = fh.read()
    assert 'record_stage_perf(conn, "smoke"' in body
    assert 'record_stage_perf(conn, "transcribe"' not in body
