"""The library question in the engine: offered, skipped, adopted or refused
(TASK-089.19).

Every library is built by the test under its own tmp_path with the app's own
schema - never a copy of anybody's `data/` (criterion 9). `library.found` is
handed the candidates each test builds; tests/conftest.py makes sure nothing
on the machine running the suite is ever a candidate.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from scribe import credentials, db, env, library, paths, setup
from seed import seed_job, seed_media, seed_run
from test_library import NO_SPEAKERS, TWO_SPEAKERS, build, listing


@pytest.fixture(autouse=True)
def no_credentials_in_the_environment(monkeypatch):
    """A machine with nothing configured, as tests/test_setup_plan.py has it."""
    for cred in credentials.CREDENTIALS.values():
        for name in cred.env_vars + cred.legacy_env_vars:
            monkeypatch.delenv(name, raising=False)
    for name in credentials.PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "system_proxies", lambda: {})
    monkeypatch.delenv(setup.RESULT_VARIABLE, raising=False)


@pytest.fixture
def target(tmp_path, monkeypatch):
    """The data directory this engine was started on, empty."""
    data = tmp_path / "target"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    for name in ("MEDIA_DIR", "LOGS_DIR", "WORK_DIR", "MODELS_DIR"):
        monkeypatch.setattr(paths, name, data / name.removesuffix("_DIR").lower())
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv(env.PATH_VARIABLE, str(empty))
    return data


@pytest.fixture
def nobody_serves(monkeypatch):
    monkeypatch.setattr(setup, "_health", lambda port: None)


def _apply(document: dict, monkeypatch, capsys, *argv: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(document)))
    code = setup.main(["--apply-stdin", *argv])
    return code, capsys.readouterr().out


def _version(folder: Path) -> int:
    name = "myscribe.db" if (folder / "myscribe.db").exists() else "scribe.db"
    conn = sqlite3.connect(folder / name)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


# --- the writing door no longer orphans a library from before the rename ---------


def test_applying_answers_to_a_library_from_before_the_rename_adopts_it_rather_than_orphaning_it(
    target, monkeypatch, capsys, nobody_serves
):
    """Reproduced by the orchestrator while TASK-089.09 was built: `db.connect`
    adopts only when given no path, setup gives it one, and an empty
    `myscribe.db` then stood beside the old library for good."""
    build(target, plain=2, legacy=True)
    assert sorted(p.name for p in target.iterdir()) == ["scribe.db"]

    code, _out = _apply({"contract": setup.CONTRACT, "answers": {}}, monkeypatch, capsys)

    assert code == 0
    assert not (target / "scribe.db").exists(), "the old library was left beside a new, empty one"
    conn = sqlite3.connect(target / "myscribe.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 2
    finally:
        conn.close()


# --- the question in the plan (criteria 1, 4, 7, 10, 11) -------------------------


@pytest.fixture
def as_clone(monkeypatch):
    monkeypatch.setattr(setup, "_is_release", lambda: False)


@pytest.fixture
def as_release(monkeypatch):
    monkeypatch.setattr(setup, "_is_release", lambda: True)


def _found_at(monkeypatch, *folders: Path) -> None:
    monkeypatch.setattr(library, "machine_candidates",
                        lambda: [(folder, f"test place {index}") for index, folder in enumerate(folders)])


def _plan(capsys, *argv: str) -> dict:
    assert setup.main(["--plan", *argv]) == 0
    return json.loads(capsys.readouterr().out)


def _question(document: dict, id: str) -> dict | None:
    return next((q for q in document["questions"] if q["id"] == id), None)


def test_a_clone_that_finds_no_library_is_not_asked(target, as_clone, capsys):
    assert _question(_plan(capsys), "library") is None


def test_a_release_asks_even_when_nothing_is_found_because_it_cannot_see_a_clone(target, as_release, capsys):
    document = _plan(capsys)
    asked = _question(document, "library")
    assert document["questions"][0]["id"] == "library", "every other answer goes into the library it picks"
    assert [c["value"] for c in asked["choices"]] == ["new", "named"]
    assert asked["default"] == "new"
    folder = _question(document, "library_folder")
    assert folder["kind"] == "text" and folder["shown_if"] == {"question": "library", "equals": "named"}


def test_a_found_library_is_offered_with_what_choosing_it_means(target, as_clone, monkeypatch, capsys):
    found = build(target.parent / "clone" / "data", diarized=3, plain=1, asked=1)
    _found_at(monkeypatch, found)

    asked = _question(_plan(capsys), "library")

    choice = next(c for c in asked["choices"] if c["value"] == str(found))
    note = choice["note"]
    assert "5 recordings" in note and "bytes)" in note and "test place 0" in note          # criterion 1
    assert "migrates it in place" in note and "older MyScribe must not open it" in note    # criterion 4
    assert "3 diarized recordings have never been asked who is speaking" in note           # criterion 7
    assert "cloud provider later sends each of them once" in note
    assert "--port 4299" in note                                                           # criterion 11
    assert asked["default"] == "new", "Enter never adopts"
    assert asked["answer_later"] == library.CHANGE_LIBRARY                                 # criterion 10
    assert str(found) in asked["if_skipped"]


def test_a_library_newer_than_this_app_is_shown_refused(target, as_clone, monkeypatch, capsys):
    newer = build(target.parent / "newer", plain=1, version=db.SCHEMA_VERSION + 1)
    _found_at(monkeypatch, newer)
    note = next(c for c in _question(_plan(capsys), "library")["choices"] if c["value"] == str(newer))["note"]
    assert "newer MyScribe" in note and "cannot be chosen now" in note


def test_a_target_that_holds_recordings_is_never_offered_another(target, as_release, monkeypatch, capsys):
    build(target, plain=1)
    _found_at(monkeypatch, build(target.parent / "other", plain=4))
    assert _question(_plan(capsys), "library") is None


def test_planning_does_not_touch_a_found_library(target, as_clone, monkeypatch, capsys):
    found = build(target.parent / "clone", diarized=2, legacy=True)
    _found_at(monkeypatch, found)
    before = listing(found)
    _plan(capsys)
    assert listing(found) == before
    assert not target.exists(), "a plan created the target"


# --- criterion 3: skip starts fresh, and does not come back -----------------------


def test_skipping_starts_fresh_leaves_the_found_library_alone_and_says_where(
    target, as_clone, monkeypatch, capsys
):
    found = build(target.parent / "clone", diarized=2)
    _found_at(monkeypatch, found)
    before = listing(found)

    code, out = _apply({"contract": setup.CONTRACT, "answers": {"library": None}}, monkeypatch, capsys)

    assert code == 0
    assert listing(found) == before
    assert f"Left untouched, and still there: {found} (2 recordings)" in out
    assert setup.states()["library"] == "skipped"
    assert (target / "myscribe.db").exists(), "fresh means the target is the library now"
    again = _plan(capsys, "--unasked-only")
    assert _question(again, "library") is None, "a skip came back at the next start"
    assert _question(_plan(capsys), "library") is not None, "--setup still offers it while the target is empty"


def test_choosing_new_is_an_answer_and_changes_nothing_elsewhere(target, as_clone, monkeypatch, capsys):
    found = build(target.parent / "clone", plain=1)
    _found_at(monkeypatch, found)
    before = listing(found)
    code, _out = _apply({"contract": setup.CONTRACT, "answers": {"library": "new"}}, monkeypatch, capsys)
    assert code == 0 and listing(found) == before
    assert setup.states()["library"] == "answered"


# --- adopting ---------------------------------------------------------------------


def test_adopting_migrates_in_place_writes_nothing_else_and_names_the_library(
    target, as_clone, monkeypatch, capsys, nobody_serves, tmp_path
):
    found = build(target.parent / "clone", plain=2, schema=10)
    dotenv = tmp_path / "clone.env"
    dotenv.write_text("# mine\nHF_TOKEN=\n", encoding="utf-8")
    monkeypatch.setenv(env.PATH_VARIABLE, str(dotenv))

    code, out = _apply(
        {"contract": setup.CONTRACT, "answers": {"library": str(found), "llm_provider": "ollama"}},
        monkeypatch, capsys,
    )

    assert code == 0, out
    assert _version(found) == db.SCHEMA_VERSION
    assert sorted(p.name for p in found.iterdir()) == ["myscribe.db"], "copied, moved or left sidecars"
    assert not target.exists(), "the library being left was created or migrated"
    conn = sqlite3.connect(found / "myscribe.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM setting WHERE key='llm_provider'").fetchone()[0] == 0
    finally:
        conn.close()
    assert setup.OTHER_ANSWERS_WAIT in out
    # A clone: nobody is listening, so the documented manual route is taken.
    assert dotenv.read_text(encoding="utf-8").splitlines() == ["# mine", "HF_TOKEN=", f"SCRIBE_DATA_DIR={found}"]


def test_the_launcher_hears_the_adoption_and_dotenv_is_left_alone(
    target, as_release, monkeypatch, capsys, nobody_serves, tmp_path
):
    found = build(target.parent / "clone", plain=1)
    result = tmp_path / "result.json"
    monkeypatch.setenv(setup.RESULT_VARIABLE, str(result))

    code, _out = _apply({"contract": setup.CONTRACT, "answers": {"library": str(found)}}, monkeypatch, capsys)

    assert code == 0
    assert json.loads(result.read_text(encoding="utf-8")) == {"data_dir": str(found)}
    assert (tmp_path / "empty.env").read_text(encoding="utf-8") == ""


def test_in_a_release_with_no_launcher_listening_nothing_is_adopted(target, as_release, monkeypatch, capsys,
                                                                  nobody_serves, tmp_path):
    """Run by hand inside a release's environment, `.env` would be overridden
    by the SCRIBE_DATA_DIR the launcher forces: a migration with nothing
    pointing at the library afterwards. So it is refused before anything moves."""
    found = build(target.parent / "clone", plain=1, schema=10)
    code, out = _apply({"contract": setup.CONTRACT, "answers": {"library": str(found)}}, monkeypatch, capsys)
    assert code == 0 and "--setup" in out and "still open: library" in out
    assert _version(found) == 10
    assert (tmp_path / "empty.env").read_text(encoding="utf-8") == ""


def test_adopting_a_library_from_before_the_rename_goes_through_adopt_legacy_db(
    target, as_clone, monkeypatch, capsys, nobody_serves
):
    found = build(target.parent / "old", plain=1, legacy=True)
    code, _out = _apply({"contract": setup.CONTRACT, "answers": {"library": str(found)}}, monkeypatch, capsys)
    assert code == 0
    assert sorted(p.name for p in found.iterdir()) == ["myscribe.db"]


def test_a_named_folder_is_looked_at_and_offered_before_anybody_says_yes(
    target, as_release, monkeypatch, capsys, nobody_serves, tmp_path
):
    named = build(tmp_path / "somewhere" / "data", diarized=2, schema=12)
    result = tmp_path / "result.json"
    monkeypatch.setenv(setup.RESULT_VARIABLE, str(result))
    before = listing(named)

    code, out = _apply(
        {"contract": setup.CONTRACT, "answers": {"library": "named", "library_folder": str(named)}},
        monkeypatch, capsys,
    )

    assert code == 0
    assert listing(named) == before, "naming a folder changed it"
    assert json.loads(result.read_text(encoding="utf-8")) == {"library": str(named)}
    assert "2 diarized recordings have never been asked" in out

    offered = _question(_plan(capsys, "--library", str(named)), "library")
    assert str(named) in [c["value"] for c in offered["choices"]]
    assert listing(named) == before


def test_a_named_folder_that_holds_no_library_reopens_the_question(target, as_release, monkeypatch, capsys, tmp_path):
    (tmp_path / "nothing").mkdir()
    for folder, said in ((str(tmp_path / "nothing"), "no MyScribe library"),
                         ("relative/path", "not a full path"),
                         ("\\\\server\\share\\data", "network share")):
        code, out = _apply({"contract": setup.CONTRACT, "answers": {"library": "named", "library_folder": folder}},
                           monkeypatch, capsys)
        assert code == 0 and said in out and "still open: library" in out
    assert not target.exists()


# --- criteria 5 and 11: refusals, each leaving user_version where it was ----------


def _refused(target, monkeypatch, capsys, found: Path) -> str:
    before = _version(found)
    code, out = _apply({"contract": setup.CONTRACT, "answers": {"library": str(found)}}, monkeypatch, capsys)
    assert code == 0
    assert "still open: library" in out
    assert _version(found) == before, "the library was migrated anyway"
    assert not target.exists()
    return out


def test_a_library_newer_than_this_app_is_refused_with_one_sentence(target, as_clone, monkeypatch, capsys, nobody_serves):
    newer = build(target.parent / "newer", plain=1, version=db.SCHEMA_VERSION + 1)
    out = _refused(target, monkeypatch, capsys, newer)
    [sentence] = [line for line in out.splitlines() if "newer MyScribe" in line]
    assert sentence.endswith(".") and sentence.count(". ") == 0


def test_a_library_the_app_on_the_port_serves_is_refused(target, as_clone, monkeypatch, capsys):
    found = build(target.parent / "served", plain=1, schema=10)
    monkeypatch.setattr(setup, "_health", lambda port: {"ok": True, "version": "0.6", "data_dir": str(found)})
    assert "Stop that MyScribe first" in _refused(target, monkeypatch, capsys, found)


def test_an_older_myscribe_that_does_not_say_what_it_serves_is_doubt_and_refuses(target, as_clone, monkeypatch, capsys):
    found = build(target.parent / "doubt", plain=1, schema=10)
    monkeypatch.setattr(setup, "_health", lambda port: {"ok": True, "version": "0.5.1"})
    assert "does not say which library it serves" in _refused(target, monkeypatch, capsys, found)


def test_a_running_job_row_refuses_without_asking_a_port(target, as_clone, monkeypatch, capsys):
    found = build(target.parent / "busy", plain=1, running=True)
    conn = sqlite3.connect(found / "myscribe.db")
    conn.execute("PRAGMA user_version = 10")  # so that a migration would show
    conn.commit()
    conn.close()
    monkeypatch.setattr(setup, "_health", lambda port: None)
    assert "A job is running" in _refused(target, monkeypatch, capsys, found)


def test_the_port_asked_is_the_one_given(target, as_clone, monkeypatch, capsys):
    found = build(target.parent / "lib", plain=1)
    asked: list[int] = []
    monkeypatch.setattr(setup, "_health", lambda port: asked.append(port))
    _apply({"contract": setup.CONTRACT, "answers": {"library": str(found)}}, monkeypatch, capsys, "--port", "4299")
    assert asked == [4299]


# --- criterion 10: where it is answered later, and why not in Settings -----------


def test_settings_this_machine_says_how_to_use_another_library_beside_the_stores_path(target, tmp_path):
    import html

    from fastapi.testclient import TestClient

    from scribe.app import create_app

    database = tmp_path / "settings.db"
    conn = db.connect(database)
    db.migrate(conn)
    conn.close()
    with TestClient(create_app(db_path=database, start_supervisor=False), base_url="http://127.0.0.1") as client:
        body = client.get("/settings").text

    storage = body[body.index('id="storage"'):]
    storage = storage[:storage.index("</section>")]
    assert "Media store" in storage
    assert library.CHANGE_LIBRARY in html.unescape(storage)
    assert "--setup" in library.CHANGE_LIBRARY and "SCRIBE_DATA_DIR in .env" in library.CHANGE_LIBRARY
    assert "Not from Settings" in library.CHANGE_LIBRARY


# --- criterion 6: the first start of an adopted library sends nothing ------------


def test_the_first_start_of_an_adopted_back_catalogue_queues_no_llm_job(
    target, as_clone, monkeypatch, capsys, nobody_serves, tmp_path
):
    """A COPY of a library with N diarized, never-asked recordings - built
    here, never anybody's `data/` - an OpenRouter key in the environment, the
    provider question skipped. Before TASK-089.07 the lifespan's sweep queued
    one llm job per recording; this counts the rows."""
    from fastapi.testclient import TestClient

    from scribe.app import create_app

    n = 4
    found = build(tmp_path / "copy-of-a-library" / "data", diarized=n, plain=1)
    monkeypatch.setenv("OPENROUTER_TOKEN", "sk-or-v1-test-not-a-real-key")
    code, _out = _apply({"contract": setup.CONTRACT, "answers": {"library": str(found), "llm_provider": None}},
                        monkeypatch, capsys)
    assert code == 0

    # The launcher then starts the app with SCRIBE_DATA_DIR on that library.
    monkeypatch.setattr(paths, "DATA_DIR", found)
    monkeypatch.setattr(paths, "DB_PATH", found / "myscribe.db")
    for name in ("MEDIA_DIR", "LOGS_DIR", "WORK_DIR", "MODELS_DIR"):
        monkeypatch.setattr(paths, name, found / name.removesuffix("_DIR").lower())
    with TestClient(create_app(db_path=found / "myscribe.db", start_supervisor=False), base_url="http://127.0.0.1"):
        pass

    assert library.inspect(found).never_asked == n
    conn = sqlite3.connect(found / "myscribe.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM job WHERE type='llm'").fetchone()[0] == 0
    finally:
        conn.close()
