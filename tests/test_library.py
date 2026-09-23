"""An existing library, found and looked at without a write (TASK-089.19).

Every library here is built by the test in its own tmp_path with the app's own
schema and `tests/seed.py` - never a copy of anybody's `data/` (criterion 9).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from scribe import credentials, db, env, library, paths
from seed import seed_job, seed_media, seed_run

TWO_SPEAKERS = [
    {"start": 0.0, "end": 0.5, "text": "hallo", "speaker": "SPEAKER_00"},
    {"start": 0.5, "end": 1.0, "text": "daar", "speaker": "SPEAKER_01"},
]
NO_SPEAKERS = [{"start": 0.0, "end": 1.0, "text": "alleen"}]
"""A transcript without diarization. `seed_run`'s default words carry speaker
labels, so a recording that was never diarized has to be said out loud."""


def build(folder: Path, *, diarized: int = 0, plain: int = 0, asked: int = 0, private: int = 0,
          trashed: int = 0, running: bool = False, legacy: bool = False, version: int | None = None,
          schema: int | None = None) -> Path:
    """A library of the app's own schema, closed cleanly, with what the test
    asks for in it. Returns the folder.

    `schema` builds an older library: the app's own migrations up to that
    version and no further, which is what an old MyScribe left behind.
    `version` only stamps a number on a current schema - a newer MyScribe."""
    folder.mkdir(parents=True, exist_ok=True)
    conn = db.connect(folder / "myscribe.db")
    if schema is None:
        db.migrate(conn)
    else:
        for target in range(1, schema + 1):
            conn.executescript(db._MIGRATIONS[target - 1])
            conn.execute(f"PRAGMA user_version = {target:d}")
        conn.commit()
    for _ in range(diarized):
        seed_run(conn, seed_media(conn), words=TWO_SPEAKERS)
    for _ in range(plain):
        seed_run(conn, seed_media(conn), words=NO_SPEAKERS)
    for _ in range(asked):
        media = seed_media(conn)
        seed_run(conn, media, words=TWO_SPEAKERS)
        conn.execute(
            "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version, content, created_at)"
            " VALUES (?, 'speakers', 'openrouter', 'm', '1', '{}', 0)", (media,))
    for _ in range(private):
        media = seed_media(conn)
        seed_run(conn, media, words=TWO_SPEAKERS)
        conn.execute("UPDATE media SET private = 1 WHERE id = ?", (media,))
    for _ in range(trashed):
        media = seed_media(conn)
        seed_run(conn, media, words=TWO_SPEAKERS)
        conn.execute("UPDATE media SET trashed_at = 1 WHERE id = ?", (media,))
    if running:
        seed_job(conn, seed_media(conn), "running")
    if version is not None:
        conn.execute(f"PRAGMA user_version = {version:d}")
    conn.commit()
    conn.close()
    if legacy:
        (folder / "myscribe.db").rename(folder / "scribe.db")
    return folder


def listing(folder: Path) -> dict[str, tuple[int, int, str]]:
    """Every file under the folder: size, modification time and content hash.
    What "nothing next to it is created, renamed or modified" is checked by."""
    return {
        str(path.relative_to(folder)): (
            path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(folder.rglob("*")) if path.is_file()
    }


# --- criterion 2: looking changes nothing ------------------------------------------


def test_looking_at_a_cleanly_closed_library_leaves_every_file_as_it_was(tmp_path):
    folder = build(tmp_path / "lib", diarized=2, plain=1)
    before = listing(folder)
    assert set(before) == {"myscribe.db"}, "a cleanly closed library has no sidecars"

    found = library.inspect(folder)

    assert listing(folder) == before
    assert found.recordings == 3 and found.never_asked == 2


def test_looking_at_a_library_a_writer_holds_open_reads_the_log_and_touches_nothing(tmp_path):
    """The case `setup.read_only`'s plain `mode=ro` branch writes the
    wal-index in. Rows committed after the last checkpoint live only in the
    `-wal`, so a reader that did not see them would report an old library."""
    folder = build(tmp_path / "lib", diarized=1)
    writer = db.connect(folder / "myscribe.db")
    try:
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        for _ in range(3):
            seed_run(writer, seed_media(writer), words=TWO_SPEAKERS)
        before = listing(folder)
        assert "myscribe.db-wal" in before and before["myscribe.db-wal"][0] > 0

        found = library.inspect(folder)

        assert listing(folder) == before, "a file beside the database changed while it was looked at"
        assert found.recordings == 4, "the rows in the write-ahead log were not read"
        assert found.never_asked == 4
    finally:
        writer.close()


def test_looking_at_a_library_from_before_the_rename_does_not_rename_it(tmp_path):
    folder = build(tmp_path / "lib", plain=2, legacy=True)
    before = listing(folder)
    assert set(before) == {"scribe.db"}

    found = library.inspect(folder)

    assert listing(folder) == before
    assert found.legacy is True and found.database.name == "scribe.db"
    assert found.recordings == 2


# --- criterion 1 and 7: what is shown about a library ------------------------------


def test_the_numbers_shown_are_recordings_size_and_the_ones_never_asked(tmp_path):
    """Never asked means what the startup sweep would queue: diarized, no
    `speakers` answer, not in the bin, not pinned private."""
    folder = build(tmp_path / "lib", diarized=3, plain=2, asked=1, private=1, trashed=1)
    (folder / "media").mkdir()
    (folder / "media" / "a.wav").write_bytes(b"\0" * 5000)

    found = library.inspect(folder, "somewhere")

    assert found.recordings == 7, "the bin is not counted"
    assert found.never_asked == 3
    assert found.size == sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    assert found.size >= 5000
    assert found.user_version == db.SCHEMA_VERSION
    assert found.running == 0
    assert found.where == "somewhere"


def test_a_recording_under_a_folder_pinned_private_is_not_counted_as_one_to_send(tmp_path):
    """The sweep's own check: a folder pinned private makes everything under
    it private, and the startup sweep never offers such a recording."""
    folder = build(tmp_path / "lib", diarized=1)
    conn = db.connect(folder / "myscribe.db")
    try:
        pinned = conn.execute("INSERT INTO folder(name, private) VALUES ('mine', 1)").lastrowid
        seed_run(conn, seed_media(conn, folder_id=pinned), words=TWO_SPEAKERS)
        conn.commit()
    finally:
        conn.close()
    assert library.inspect(folder).never_asked == 1


def test_a_folder_with_no_database_is_no_library(tmp_path):
    (tmp_path / "empty").mkdir()
    assert library.inspect(tmp_path / "empty") is None
    assert library.inspect(tmp_path / "not-there") is None
    assert library.recordings_in(tmp_path / "not-there") == 0


def test_an_empty_migrated_database_is_not_a_library_anybody_would_miss(tmp_path):
    folder = build(tmp_path / "lib")
    assert library.recordings_in(folder) == 0


# --- criterion 1: where a library is looked for -------------------------------------


def test_the_per_user_home_is_the_launchers_own(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "launcher_for_library", Path(__file__).resolve().parent.parent / "packaging" / "launcher" / "myscribe_launcher.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    for platform, environ in (
        ("win32", {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}),
        ("darwin", {}),
        ("linux", {"XDG_DATA_HOME": "/x/share"}),
        ("linux", {}),
    ):
        assert library.default_home(platform, environ) == launcher.default_home(platform, environ)


def test_a_library_is_looked_for_in_the_checkout_the_home_and_every_layer(
    tmp_path, monkeypatch, machine_candidates_unstubbed
):
    repo = tmp_path / "repo"
    monkeypatch.setattr(env, "REPO_DIR", repo)
    monkeypatch.setattr(library, "default_home", lambda *a, **k: tmp_path / "home")
    monkeypatch.setenv("SCRIBE_DATA_DIR", str(tmp_path / "process"))
    dotenv = tmp_path / "x.env"
    monkeypatch.setattr(credentials, "dotenv_values",
                        lambda path=None: ({"SCRIBE_DATA_DIR": str(tmp_path / "dotenv")}, dotenv))
    monkeypatch.setattr(credentials, "registry_hits",
                        lambda name: (("HKEY_CURRENT_USER", str(tmp_path / "registry")),) if name == "SCRIBE_DATA_DIR" else ())

    listed = library.machine_candidates()

    assert [folder for folder, _where in listed] == [
        repo / "data", tmp_path / "home" / "data", tmp_path / "process", tmp_path / "dotenv", tmp_path / "registry",
    ]
    assert all(where for _folder, where in listed), "each one says where it was found"


def test_found_lists_each_library_once_and_never_the_target(tmp_path):
    one = build(tmp_path / "one", plain=1)
    two = build(tmp_path / "two", plain=2, legacy=True)
    target = build(tmp_path / "target")
    (tmp_path / "nothing").mkdir()
    candidates = [(one, "a"), (Path(str(one) + "/."), "a again"), (target, "the target"),
                  (tmp_path / "nothing", "empty"), (two, "b")]

    found = library.found(target, candidates=lambda: candidates)

    assert [(lib.data_dir, lib.where) for lib in found] == [(one, "a"), (two, "b")]
    assert found[1].legacy is True


def test_a_named_folder_is_read_like_a_found_one_and_listed_after_them(tmp_path):
    named = build(tmp_path / "named", diarized=2)
    found = library.found(tmp_path / "target", [named], candidates=lambda: [])
    assert [(lib.data_dir, lib.never_asked) for lib in found] == [(named, 2)]


# --- criteria 5 and 11: what is refused ------------------------------------------


def test_a_library_newer_than_this_app_is_refused_in_one_sentence(tmp_path):
    newer = library.inspect(build(tmp_path / "lib", version=db.SCHEMA_VERSION + 1))
    sentence = library.newer_than_this_app(newer)
    assert sentence.count(". ") == 0 and sentence.endswith(".")
    assert str(db.SCHEMA_VERSION + 1) in sentence and str(db.SCHEMA_VERSION) in sentence
    assert library.newer_than_this_app(library.inspect(build(tmp_path / "ok"))) == ""


def test_a_running_job_row_refuses_without_a_port(tmp_path):
    busy = library.inspect(build(tmp_path / "lib", running=True))
    assert busy.running == 1
    assert "Stop that MyScribe first" in library.job_running(busy)


def test_health_that_serves_it_or_does_not_say_refuses_and_another_library_does_not(tmp_path):
    lib = library.inspect(build(tmp_path / "lib"))
    assert library.served(lib, None, 4242) == ""
    assert library.served(lib, {"ok": True, "data_dir": str(tmp_path / "other")}, 4242) == ""
    assert "is serving" in library.served(lib, {"ok": True, "data_dir": str(tmp_path / "lib" / ".")}, 4242)
    assert "does not say" in library.served(lib, {"ok": True, "version": "0.5.1"}, 4242)


# --- adopting -----------------------------------------------------------------------


def test_adopting_renames_a_legacy_database_with_its_sidecars_and_migrates_it_in_place(tmp_path):
    folder = build(tmp_path / "lib", legacy=True, schema=5)
    conn = sqlite3.connect(folder / "scribe.db")
    conn.execute("INSERT INTO media(sha256, store_path, orig_name, title, size_bytes, created_at)"
                 " VALUES ('x', 'media/x.wav', 'x.wav', 'x', 1, 0)")
    conn.commit()
    conn.close()
    found = library.inspect(folder)

    assert library.adopt(found) == folder

    assert not (folder / "scribe.db").exists()
    conn = sqlite3.connect(folder / "myscribe.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    finally:
        conn.close()
    assert set(p.parent for p in folder.rglob("*")) == {folder}, "nothing was copied anywhere"
