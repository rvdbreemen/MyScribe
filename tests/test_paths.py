import os
import stat
import sys
from pathlib import Path

import pytest

from scribe import paths


def test_dirs_are_under_repo_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_DATA_DIR", str(tmp_path / "d"))
    import importlib; importlib.reload(paths)
    paths.ensure_dirs()
    assert paths.DB_PATH.parent == paths.DATA_DIR
    assert paths.MEDIA_DIR.is_dir() and paths.LOGS_DIR.is_dir()
    assert paths.WORK_DIR.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="mode bits are a POSIX answer; Windows has ACLs")
def test_a_data_dir_the_app_makes_is_private(tmp_path, monkeypatch):
    """The library holds transcripts, recordings and API keys: a fresh one
    starts at 0700, not the umask's 0755."""
    data = tmp_path / "fresh"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")

    paths.ensure_dirs()

    assert stat.S_IMODE(data.stat().st_mode) == 0o700


def _existing_data_dir(tmp_path, monkeypatch, mode):
    data = tmp_path / "existing"
    data.mkdir()
    os.chmod(data, mode)  # mkdir's mode goes through the umask; chmod does not
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    return data


@pytest.mark.skipif(os.name == "nt", reason="mode bits are a POSIX answer; Windows has ACLs")
@pytest.mark.parametrize("mode", [0o750, 0o770, 0o700])
def test_a_data_dir_opened_to_a_group_keeps_its_mode(tmp_path, monkeypatch, mode):
    """A group somebody let in on purpose is theirs: ensure_dirs must not
    tighten it back at every start."""
    # Linux, where a user's own group is private to them. Pinned, because on
    # a Mac runner tmp_path sits in group staff and is tightened further.
    monkeypatch.setattr(sys, "platform", "linux")
    data = _existing_data_dir(tmp_path, monkeypatch, mode)

    paths.ensure_dirs()

    assert stat.S_IMODE(data.stat().st_mode) == mode


@pytest.mark.skipif(os.name == "nt", reason="mode bits are a POSIX answer; Windows has ACLs")
@pytest.mark.parametrize("before, after", [(0o755, 0o750), (0o777, 0o770), (0o701, 0o700)])
def test_an_existing_data_dir_stops_letting_everybody_in(tmp_path, monkeypatch, before, after):
    """GitHub #42: every library made before this fix sits at the umask's
    0755, so media/ stays traversable and the recordings world-readable.
    The bits for "other" go; the owner's and the group's stay."""
    monkeypatch.setattr(sys, "platform", "linux")
    data = _existing_data_dir(tmp_path, monkeypatch, before)

    paths.ensure_dirs()

    assert stat.S_IMODE(data.stat().st_mode) == after


@pytest.mark.skipif(os.name == "nt", reason="mode bits are a POSIX answer; Windows has ACLs")
@pytest.mark.parametrize("before", [0o755, 0o750, 0o770, 0o777], ids=oct)
def test_a_mac_library_in_group_staff_stops_letting_every_account_in(tmp_path, monkeypatch, before):
    """GitHub #42: on macOS every local account has primary group staff and
    a new file takes its folder's group, so a 0750 library in staff is as
    open as a 0755 one. There the group bits go too."""
    monkeypatch.setattr(sys, "platform", "darwin")
    data = _existing_data_dir(tmp_path, monkeypatch, before)
    # chown to gid 20 needs root; call the directory's own group the shared one.
    monkeypatch.setattr(paths, "_DARWIN_SHARED_GROUPS", {data.stat().st_gid}, raising=False)

    paths.ensure_dirs()

    assert stat.S_IMODE(data.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="mode bits are a POSIX answer; Windows has ACLs")
@pytest.mark.parametrize("before, after", [(0o750, 0o750), (0o755, 0o750)])
def test_a_mac_library_given_its_own_group_keeps_it(tmp_path, monkeypatch, before, after):
    """A Mac library chgrp'ed to a group of its own was shared on purpose:
    only "other" goes, as on Linux."""
    monkeypatch.setattr(sys, "platform", "darwin")
    data = _existing_data_dir(tmp_path, monkeypatch, before)
    monkeypatch.setattr(paths, "_DARWIN_SHARED_GROUPS", {data.stat().st_gid + 1}, raising=False)

    paths.ensure_dirs()

    assert stat.S_IMODE(data.stat().st_mode) == after


def test_each_job_gets_its_own_work_directory():
    # Scratch space per job, so one job's leftovers can never be handed to
    # another and finalize can delete a whole directory without thinking.
    assert paths.job_work_dir(42).parent == paths.WORK_DIR
    assert paths.job_work_dir(42) != paths.job_work_dir(43)


def test_the_work_dir_sweep_removes_scratch_only_for_jobs_that_are_done(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "WORK_DIR", tmp_path / "work")
    work = tmp_path / "work"
    for name in ("1", "2", "rec", "not-a-job"):
        (work / name).mkdir(parents=True)

    removed = paths.sweep_work_dirs(keep=lambda job_id: job_id == 2)

    assert removed == 1
    assert not (work / "1").exists(), "job 1's job no longer has work ahead of it"
    assert (work / "2").is_dir(), "job 2 still has work ahead of it"
    assert (work / "rec").is_dir() and (work / "not-a-job").is_dir(), "not a job's scratch"


def test_the_work_dir_sweep_survives_a_dir_that_will_not_delete(tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(paths, "WORK_DIR", tmp_path / "work")
    work = tmp_path / "work"
    (work / "1").mkdir(parents=True)
    (work / "2").mkdir(parents=True)
    real = shutil.rmtree

    # By name, not by `str(p).endswith("/1")`: on Windows the separator is a
    # backslash, the stand-in never raised, and the test went red there for a
    # sweep that was fine (GitHub #33).
    def flaky(p, *args, **kwargs):
        if Path(p).name == "1":
            raise OSError("busy")
        return real(p, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    assert paths.sweep_work_dirs(keep=lambda job_id: False) == 1
    assert (work / "1").is_dir() and not (work / "2").exists()


# --- the database's name ---------------------------------------------------------------


def test_the_database_carries_the_applications_name(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "myscribe.db")

    assert paths.DB_PATH.name == "myscribe.db"
    assert paths.LEGACY_DB_NAME == "scribe.db"


def test_a_database_from_before_the_rename_is_adopted_with_its_sidecars(tmp_path, monkeypatch):
    """The three files move together: a .db without its -wal is a database
    missing its last transactions."""
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "myscribe.db")
    for name, body in (("scribe.db", b"db"), ("scribe.db-wal", b"wal"), ("scribe.db-shm", b"shm"),
                       ("scribe.db.backup-20260903", b"old")):
        (tmp_path / name).write_bytes(body)

    assert paths.adopt_legacy_db() is True

    assert (tmp_path / "myscribe.db").read_bytes() == b"db"
    assert (tmp_path / "myscribe.db-wal").read_bytes() == b"wal"
    assert (tmp_path / "myscribe.db-shm").read_bytes() == b"shm"
    assert not (tmp_path / "scribe.db").exists() and not (tmp_path / "scribe.db-wal").exists()
    assert (tmp_path / "scribe.db.backup-20260903").exists(), "backups are left alone"
    # Idempotent: nothing left to adopt.
    assert paths.adopt_legacy_db() is False


def test_adoption_never_overwrites_a_database_that_already_has_the_new_name(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "myscribe.db")
    (tmp_path / "myscribe.db").write_bytes(b"current")
    (tmp_path / "scribe.db").write_bytes(b"stale")

    assert paths.adopt_legacy_db() is False

    assert (tmp_path / "myscribe.db").read_bytes() == b"current"
    assert (tmp_path / "scribe.db").exists()


def test_connect_adopts_before_opening_the_default_database(tmp_path, monkeypatch):
    from scribe import db

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "myscribe.db")
    seed = db.connect(tmp_path / "scribe.db")
    db.migrate(seed)
    seed.execute("INSERT INTO setting(key, value) VALUES ('default_language', 'nl')")
    seed.commit()
    seed.close()

    conn = db.connect()  # no path: the default, adopted first
    try:
        row = conn.execute("SELECT value FROM setting WHERE key='default_language'").fetchone()
        assert row["value"] == "nl"
    finally:
        conn.close()
    assert (tmp_path / "myscribe.db").exists() and not (tmp_path / "scribe.db").exists()
