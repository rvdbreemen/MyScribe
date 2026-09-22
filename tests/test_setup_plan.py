"""The plan: what `--plan` prints, and that printing it changes nothing (TASK-089.09).

The engine's whole contract is here - `contract`, the found table, the Ollama
state, the downloads and the questions that are still open - because six other
tasks render exactly this document and none of them may have to guess its
shape.

Two things are asserted over the *whole* list rather than per question, so that
they stay true as questions are added: no question anywhere asks about a proxy,
and no value of a credential appears anywhere a sitting writes.

Every credential variable is deleted from `os.environ` per test. tests/conftest.py
deliberately leaves the environment alone - a live `-m gpu` run resolves this
machine's real key from it - so a file that must see a machine with nothing
configured says so itself. This machine really does hold HF_TOKEN,
HUGGINGFACE_TOKEN and OPENROUTER_TOKEN, and without that every question here
would be answered by the developer's environment.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys

import httpx2
import pytest

from scribe import accel, credentials, db, models, ollama_setup, paths, setup
from scribe.llm import base, ollama
from scribe.web import ai_ui, transcribe_dialog

SENTINEL = "myscribe-sentinel-0000-never-printed"
"""Planted where a credential lives and then looked for in everything a sitting
emits. Never a real token: a test that needed one could not be run twice."""


@pytest.fixture(autouse=True)
def no_credentials_in_the_environment(monkeypatch):
    """A machine with nothing configured, which is what a first run is about."""
    for cred in credentials.CREDENTIALS.values():
        for name in cred.env_vars + cred.legacy_env_vars:
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(name.lower(), raising=False)
    for name in credentials.PROXY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setattr(credentials, "system_proxies", lambda: {})


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A data directory and a library of this test's own.

    The same seams tests/test_setup.py's fixture takes, and for the same
    reason: `stamp_path()` reads `paths.DATA_DIR`, and a `main()`-level test
    would otherwise write `setup.json` into the developer's own library.

    Every constant `ensure_dirs` iterates, not only the two this file reads:
    they are computed at import from the unpatched DATA_DIR, so a `main()` that
    is not `--plan` would create `media`, `logs`, `work` and `models` in the
    real data directory (TASK-090's fence hides that, and these are the first
    tests in the suite to reach `ensure_dirs` through `main`).
    """
    empty = tmp_path / "env-for-test"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCRIBE_ENV_FILE", str(empty))
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    for name in ("MEDIA_DIR", "LOGS_DIR", "WORK_DIR", "MODELS_DIR"):
        monkeypatch.setattr(paths, name, data / name.removesuffix("_DIR").lower())
    conn = db.connect(data / "myscribe.db")
    db.migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def no_weights(tmp_path, monkeypatch):
    """A machine with no model weights and no diarization pipeline.

    Both doors, as tests/test_setup.py:139 puts it: `paths.MODELS_DIR` is
    computed at import, so moving DATA_DIR does not move it, and this machine
    has a real pipeline in the real one and the whisper weights in the real hub
    cache.
    """
    from scribe import doctor
    from scribe.stages import diarize

    monkeypatch.setattr(models, "root", lambda: tmp_path / "models")
    monkeypatch.setattr(diarize, "local_weights_dir", lambda: tmp_path / "models" / "pyannote")
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")


def question(document: dict, id: str) -> dict | None:
    return next((q for q in document["questions"] if q["id"] == id), None)


def ids(document: dict) -> list[str]:
    return [q["id"] for q in document["questions"]]


# --- the shape every front-end renders (#1) --------------------------------------


def test_a_plan_says_what_was_found_what_ollama_is_and_what_is_open(library, no_weights, capsys):
    """The contract, key by key. Nothing called `setup.main` before this task,
    so this is the first test of the engine at the level its doors use it."""
    assert setup.main(["--plan"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["contract"] == setup.CONTRACT
    assert set(document) == {"contract", "found", "ollama", "downloads", "questions"}

    kinds = {row["kind"] for row in document["found"]}
    assert kinds <= {"credential", "proxy"}, "one table, and a discriminator that says which"
    for row in document["found"]:
        assert set(row) >= {"kind", "name", "found", "source", "also_in", "conflict"}
    assert [row["name"] for row in document["found"] if row["kind"] == "credential"] == [
        "huggingface", "openrouter", "openai",
    ]

    assert set(document["ollama"]) == {
        "state", "present", "binary", "version", "chat_models", "variables", "note",
    }
    assert set(document["downloads"]) == {"catalogue", "backend", "note", "entries", "total_bytes"}
    assert document["downloads"]["backend"] in ("mlx", setup.NOT_MLX)

    assert document["questions"], "a machine with nothing configured has questions"
    for asked in document["questions"]:
        assert set(asked) == {
            "id", "kind", "text", "choices", "current", "default",
            "shown_if", "if_skipped", "answer_later",
        }
        assert asked["text"] and asked["if_skipped"] and asked["answer_later"]
        assert asked["kind"] in ("secret", "choice", "yes-no")


def test_a_plan_is_the_same_document_the_engine_hands_a_caller(library, no_weights, capsys):
    """`--plan` prints what `plan()` returns and adds nothing of its own, so a
    front-end and a test are looking at one thing."""
    setup.main(["--plan"])

    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(json.dumps(setup.plan(library)))


def test_a_start_is_asked_only_what_the_last_sitting_never_put(library, no_weights):
    """`unasked_only` is what a start asks with, against the `--setup` that
    asks everything. The states come out of the stamp (TASK-089.11), and
    answered and skipped both count as put: a question somebody skipped on
    purpose must not come back at every start."""
    setup.stamp_path().write_text(json.dumps({
        "contract": setup.CONTRACT,
        "ended": 1789930000.0,
        "questions": {"hf_token": "answered", "llm_provider": "skipped"},
    }), encoding="utf-8")
    everything = ids(setup.plan(library))
    assert {"hf_token", "llm_provider"} <= set(everything), "both are open on this machine"

    asked_at_a_start = ids(setup.plan(library, unasked_only=True))

    assert asked_at_a_start == [i for i in everything if i not in ("hf_token", "llm_provider")]
    assert ids(setup.plan(library)) == everything, "and `--setup` still asks all of them"


def test_a_document_from_another_contract_is_refused_before_any_write(
        library, no_weights, monkeypatch, capsys):
    """The number both documents carry is read, not only written.

    Launcher and app ship in one payload (ADR-015), so a document claiming
    another number is a mismatched install rather than an old client - and the
    honest answer to one is the usage code and an untouched library, not a
    best-effort apply of keys that may no longer mean what they say.
    """
    document = json.dumps({"contract": setup.CONTRACT + 1, "answers": {"default_tier": "max"}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 2

    assert _rows(library) == {}, "nothing of a document nobody can read is applied"
    assert not setup.stamp_path().exists()
    assert str(setup.CONTRACT) in capsys.readouterr().err


def test_a_document_that_claims_no_contract_is_applied(library, no_weights, monkeypatch):
    """Absent is not a disagreement: a front-end that says nothing about the
    contract is taken at the word of the engine it is talking to."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"answers": {"default_tier": "max"}})))

    assert setup.main(["--apply-stdin"]) == 0

    assert _rows(library)["default_tier"] == "max"


# --- a plan writes nothing (#2) ----------------------------------------------------


def snapshot(folder) -> dict:
    """Every file under `folder`, with its size and its mtime in nanoseconds."""
    return {
        str(path.relative_to(folder)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def test_a_plan_creates_nothing_in_an_empty_data_directory(tmp_path, monkeypatch, no_weights, capsys):
    """Asking what is open must not make the thing it is asking about.

    `main()` created the directories and migrated the database before it
    answered, so a question about a machine changed it - and on a machine where
    the answer is "nothing is set up yet" it made a library nobody asked for.
    """
    empty = tmp_path / "env-for-test"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCRIBE_ENV_FILE", str(empty))
    data = tmp_path / "scratch"
    data.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")

    assert setup.main(["--plan"]) == 0

    assert json.loads(capsys.readouterr().out)["contract"] == setup.CONTRACT
    assert list(data.iterdir()) == [], "the directory is as empty as it was"


def test_a_plan_moves_no_byte_of_a_library_it_reads(tmp_path, monkeypatch, no_weights, capsys):
    """A copy of a real library, read and not touched.

    The trap this is about is measurable and silent: a plain `mode=ro` open of
    a cleanly closed WAL database *creates* a `-shm` of 32,768 bytes and an
    empty `-wal` beside it and leaves them there (measured here on 2026-09-22,
    SQLite 3.45.3, as spec section 0 measured it on 2026-09-20). Only
    `mode=ro&immutable=1` creates nothing, and an implementation that forgets
    it passes every other test in this file.
    """
    empty = tmp_path / "env-for-test"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCRIBE_ENV_FILE", str(empty))
    made = db.connect(tmp_path / "source.db")
    db.migrate(made)
    ai_ui.setting_put(made, ai_ui.PROVIDER_SETTING, "ollama")
    made.close()

    data = tmp_path / "copy"
    data.mkdir()
    shutil.copy2(tmp_path / "source.db", data / "myscribe.db")
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    before = snapshot(data)

    assert setup.main(["--plan"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert "llm_provider" not in ids(document), "it really did read the library"
    assert snapshot(data) == before, "no file created, none grown, none touched"


def test_a_plan_reads_what_a_writer_has_not_checkpointed_yet(tmp_path, monkeypatch, no_weights, capsys):
    """The other half of `read_only`'s branch: a library with a live `-wal`.

    `immutable=1` promises SQLite the file will not change, so it may ignore
    the write-ahead log - and what sits in the log is everything since the last
    checkpoint. Measured here on 2026-09-22 (SQLite 3.45.3): with a committed
    provider row still in the `-wal`, an immutable open reads the *previous*
    value while a plain `mode=ro` open reads the current one. That is the case
    the launcher plans in - the app is running and holds the library in WAL
    mode - so a plan that took the shortcut would re-ask what was just chosen.
    """
    empty = tmp_path / "env-for-test"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCRIBE_ENV_FILE", str(empty))
    data = tmp_path / "running"
    data.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    writer = db.connect(data / "myscribe.db")
    db.migrate(writer)
    transcribe_dialog.save_tier(writer, "turbo")
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    transcribe_dialog.save_tier(writer, "max")
    try:  # left open on purpose: this is a plan made while the app runs
        before = snapshot(data)

        assert setup.main(["--plan"]) == 0

        document = json.loads(capsys.readouterr().out)
        after = snapshot(data)
    finally:
        writer.close()

    assert question(document, "default_tier")["current"] == "max", (
        "what the writer has committed, not what it last checkpointed")
    assert set(after) == set(before), "and the plan still made no file of its own"


def test_an_error_while_a_plan_reads_comes_back_as_itself(tmp_path, monkeypatch, no_weights):
    """A library that raises while it is being read must raise that.

    `read_only`'s `except sqlite3.Error` enclosed its own `yield`, so an error
    from inside the `with` body was caught by the handler, which yielded a
    second time - and `contextlib` turns that into `RuntimeError: generator
    didn't stop after throw()`, hiding what actually went wrong and skipping
    the close (review, 2026-09-22).
    """
    library = tmp_path / "myscribe.db"
    db.connect(library).close()

    with pytest.raises(sqlite3.OperationalError) as raised:
        with setup.read_only(library):
            raise sqlite3.OperationalError("database disk image is malformed")

    assert "malformed" in str(raised.value)


def test_a_file_that_is_not_a_library_still_answers_with_the_questions(
        tmp_path, monkeypatch, no_weights, capsys):
    """A plan is asked on machines whose library may be anything at all, and
    an unreadable one is not a reason to refuse to say what is open: every
    question is simply still open. What it must not do is raise."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "myscribe.db").write_bytes(b"not a database at all")
    empty = tmp_path / "env-for-test"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCRIBE_ENV_FILE", str(empty))
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")

    assert setup.main(["--plan"]) == 0

    assert "llm_provider" in ids(json.loads(capsys.readouterr().out))


# --- the key question (#16) --------------------------------------------------------


def plant(source, name, value, *, conn, monkeypatch, tmp_path):
    """One credential, in one of the four places the resolver reads."""
    cred = credentials.CREDENTIALS[name]
    if source == "settings row":
        ai_ui.setting_put(conn, cred.setting_key, value)
    elif source == "environment":
        monkeypatch.setenv(cred.env_vars[0], value)
    elif source == "dotenv":
        monkeypatch.setattr(
            credentials, "dotenv_values", lambda path=None: ({cred.env_vars[0]: value}, tmp_path / "planted.env")
        )
    elif source == "registry":
        monkeypatch.setattr(
            credentials,
            "registry_hits",
            lambda asked, wanted=cred.env_vars[0]: ((credentials.HKCU, value),) if asked == wanted else (),
        )
    else:  # pragma: no cover - a source nobody planted is a broken test
        raise AssertionError(source)


@pytest.mark.parametrize("provider", ["openrouter", "openai"])
def test_the_key_question_is_open_when_no_source_has_one(library, no_weights, provider):
    """Requirement 2's asker. Until this task there was no key question at all:
    `--provider openrouter` exited 0 with "saved: provider" and the first
    Summary became a failed job on the board."""
    asked = question(setup.plan(library), f"llm_key_{provider}")

    assert asked is not None
    assert asked["kind"] == "secret"
    assert asked["shown_if"] == {"question": "llm_provider", "equals": provider}, (
        "it is shown under the provider choice it belongs to"
    )
    assert asked["default"] is None, "skip is the default for every secret"
    assert "Settings > AI providers > Save key" in asked["answer_later"]


@pytest.mark.parametrize("provider", ["openrouter", "openai"])
@pytest.mark.parametrize("source", ["settings row", "environment", "dotenv", "registry"])
def test_a_key_found_in_any_source_closes_the_question_and_names_where(
        library, no_weights, monkeypatch, tmp_path, provider, source):
    """Detect before asking, over every place the resolver looks."""
    plant(source, provider, SENTINEL, conn=library, monkeypatch=monkeypatch, tmp_path=tmp_path)

    document = setup.plan(library)

    assert question(document, f"llm_key_{provider}") is None
    row = next(r for r in document["found"] if r["name"] == provider)
    assert row["found"] is True
    assert row["source"], "and the table says where it was found"
    assert SENTINEL not in json.dumps(document), "which is never the value itself"


def test_ollama_has_no_key_question(library, no_weights):
    """A loopback provider has no credential, so there is nothing to ask."""
    ai_ui.setting_put(library, ai_ui.PROVIDER_SETTING, "ollama")

    assert [i for i in ids(setup.plan(library)) if i.startswith("llm_key_")] == []


def test_a_stored_cloud_provider_asks_only_for_its_own_key(library, no_weights):
    """A re-run on a machine that has chosen: one key question, and it is not
    conditioned on a question nobody is shown."""
    ai_ui.setting_put(library, ai_ui.PROVIDER_SETTING, "openai")

    document = setup.plan(library)

    assert [i for i in ids(document) if i.startswith("llm_key_")] == ["llm_key_openai"]
    assert question(document, "llm_key_openai")["shown_if"] is None


# --- the provider question (#19) ---------------------------------------------------


def serving(monkeypatch, daemon):
    """Point every Ollama client at a stand-in daemon, the house way."""
    def factory(*, base_url, timeout):
        return httpx2.Client(base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(daemon))

    monkeypatch.setattr(ollama, "default_client_factory", factory)


class Daemon:
    """An Ollama that answers `/api/tags` and `/api/version`, and remembers
    everything it was asked."""

    def __init__(self, *names, version="0.34.2"):
        self.names = list(names)
        self.version = version
        self.asked: list[tuple[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.asked.append((request.method, request.url.path))
        if request.url.path == "/api/tags":
            rows = [
                {"name": name, "model": name, "size": 1, "details": {},
                 "capabilities": ["completion", "tools"]}
                for name in self.names
            ]
            return httpx2.Response(200, json={"models": rows})
        if request.url.path == "/api/version":
            return httpx2.Response(200, json={"version": self.version})
        return httpx2.Response(404, json={})


def test_the_provider_question_is_open_only_while_no_row_says_who(library, no_weights):
    """A re-run shows the stored value and never resets it (ADR-016)."""
    assert question(setup.plan(library), "llm_provider") is not None

    ai_ui.setting_put(library, ai_ui.PROVIDER_SETTING, "openrouter")

    assert question(setup.plan(library), "llm_provider") is None
    assert ai_ui.setting_get(library, ai_ui.PROVIDER_SETTING) == "openrouter"


def test_ollama_is_the_default_only_when_it_is_ready(library, no_weights, monkeypatch, ollama_state_unstubbed):
    serving(monkeypatch, Daemon("qwen3.5:4b"))

    assert question(setup.plan(library), "llm_provider")["default"] == "ollama"


@pytest.mark.parametrize("names", [(), ])
def test_an_ollama_without_a_chat_model_is_not_the_default(
        library, no_weights, monkeypatch, ollama_state_unstubbed, names):
    """Running, but nothing to answer with: the default is to decide later,
    which writes no row."""
    serving(monkeypatch, Daemon(*names))

    assert question(setup.plan(library), "llm_provider")["default"] == setup.LATER


def test_no_ollama_at_all_leaves_the_default_at_decide_later(library, no_weights):
    """tests/conftest.py's stub reports `absent`, which is this machine's one
    unreachable state and so the honest stand-in."""
    assert question(setup.plan(library), "llm_provider")["default"] == setup.LATER


def test_no_cloud_provider_is_ever_the_enter_default(library, no_weights, monkeypatch, ollama_state_unstubbed):
    """Asserted over every state Ollama can be in, so that it stays true."""
    for daemon in (Daemon("qwen3.5:4b"), Daemon(), None):
        if daemon is not None:
            serving(monkeypatch, daemon)
        asked = question(setup.plan(library), "llm_provider")
        assert asked["default"] in ("ollama", setup.LATER)
        cloud = [c for c in asked["choices"] if c["value"] in ("openai", "openrouter")]
        assert len(cloud) == 2
        for choice in cloud:
            assert "leaves this machine" in choice["note"] or "off this machine" in choice["note"]
            assert "pinned private are always refused" in choice["note"]


@pytest.mark.parametrize("answer", [setup.LATER, None])
def test_deciding_later_or_skipping_leaves_the_row_absent(library, no_weights, monkeypatch, answer, capsys):
    """No row means no provider (ADR-016), so both of these have to write none."""
    document = json.dumps({"contract": setup.CONTRACT, "answers": {"llm_provider": answer}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 0

    assert ai_ui.setting_get(library, ai_ui.PROVIDER_SETTING) is None


# --- no question about a proxy (#20) -----------------------------------------------


def test_no_question_anywhere_asks_about_a_proxy(library, no_weights, monkeypatch):
    """Over the whole list and not one id, so it stays true as questions are
    added: a proxy is detected and shown, never asked about (TASK-089.05)."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://proxy.corp:3128")
    monkeypatch.setattr(credentials, "system_proxies", lambda: {"http": "http://system.proxy:8080"})

    document = setup.plan(library)

    assert [row for row in document["found"] if row["kind"] == "proxy"], "it is detected"
    for asked in document["questions"]:
        haystack = json.dumps(asked).lower()
        assert "proxy" not in haystack, f"{asked['id']} mentions a proxy"


def test_a_proxy_answer_in_a_document_writes_nothing(library, no_weights, monkeypatch):
    """A front-end that invents an answer gets nothing for it."""
    before = _rows(library)
    document = json.dumps({
        "contract": setup.CONTRACT,
        "answers": {"http_proxy": "http://proxy.corp:3128", "proxy": "yes"},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 0

    assert _rows(library) == before


def test_a_found_proxy_is_shown_as_host_and_port_only(library, no_weights, monkeypatch):
    """The userinfo of a proxy URL is a secret too."""
    monkeypatch.setenv("HTTPS_PROXY", "http://someone:hunter2@proxy.corp:3128")

    rows = [row for row in setup.plan(library)["found"] if row["kind"] == "proxy"]

    assert [row["host"] for row in rows] == ["proxy.corp:3128"]
    assert "hunter2" not in json.dumps(rows)


def _rows(conn) -> dict:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM setting")}


# --- the model question for an Ollama that was already here (#18) -------------------


@pytest.fixture
def ollama_chosen(library):
    ai_ui.setting_put(library, ai_ui.PROVIDER_SETTING, "ollama")
    return library


def test_the_model_question_is_open_for_a_ready_ollama_without_the_default(
        ollama_chosen, no_weights, monkeypatch, ollama_state_unstubbed):
    """Its default is skip: never save a model nobody chose (TASK-054)."""
    serving(monkeypatch, Daemon("gemma4:12b", "llama3.2:3b"))

    asked = question(setup.plan(ollama_chosen), "llm_model_ollama")

    assert asked is not None
    assert [c["value"] for c in asked["choices"]] == ["gemma4:12b", "llama3.2:3b"]
    assert asked["default"] is None
    assert "Settings > AI providers > model" in asked["answer_later"]


def test_the_model_question_stays_out_when_another_provider_answers(
        library, no_weights, monkeypatch, ollama_state_unstubbed):
    ai_ui.setting_put(library, ai_ui.PROVIDER_SETTING, "openrouter")
    serving(monkeypatch, Daemon("gemma4:12b"))

    assert question(setup.plan(library), "llm_model_ollama") is None


def test_the_model_question_stays_out_when_ollama_is_not_running(ollama_chosen, no_weights):
    """The conftest stub reports `absent`; an Ollama that is not answering is
    never asked about, and never started."""
    assert question(setup.plan(ollama_chosen), "llm_model_ollama") is None


def test_the_model_question_stays_out_when_a_model_was_already_chosen(
        ollama_chosen, no_weights, monkeypatch, ollama_state_unstubbed):
    serving(monkeypatch, Daemon("gemma4:12b"))
    ai_ui.setting_put(ollama_chosen, ai_ui.MODEL_SETTING_PREFIX + "ollama", "gemma4:12b")

    assert question(setup.plan(ollama_chosen), "llm_model_ollama") is None


def test_the_model_question_stays_out_when_the_default_is_pulled(
        ollama_chosen, no_weights, monkeypatch, ollama_state_unstubbed):
    """Nothing to decide: MyScribe already asks for a model this Ollama has."""
    serving(monkeypatch, Daemon(ollama.OllamaProvider.default_model, "gemma4:12b"))

    assert question(setup.plan(ollama_chosen), "llm_model_ollama") is None


def test_a_sitting_asks_ollama_nothing_beyond_what_detection_reads(
        ollama_chosen, no_weights, monkeypatch, ollama_state_unstubbed):
    """An Ollama that was already here is left alone, in every state (R2): the
    plan reads, and installs, pulls, starts and reconfigures nothing."""
    daemon = Daemon("gemma4:12b")
    serving(monkeypatch, daemon)

    setup.plan(ollama_chosen)

    assert {method for method, _ in daemon.asked} == {"GET"}
    assert set(path for _, path in daemon.asked) <= {"/api/tags", "/api/version"}


def test_answering_the_model_question_writes_only_myscribe_s_own_row(
        ollama_chosen, no_weights, monkeypatch, ollama_state_unstubbed):
    daemon = Daemon("gemma4:12b")
    serving(monkeypatch, daemon)
    document = json.dumps({"contract": setup.CONTRACT, "answers": {"llm_model_ollama": "gemma4:12b"}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 0

    assert ai_ui.setting_get(ollama_chosen, ai_ui.MODEL_SETTING_PREFIX + "ollama") == "gemma4:12b"
    assert [path for method, path in daemon.asked if method != "GET"] == []


# --- the downloads (#4) -------------------------------------------------------------


@pytest.mark.parametrize("backend", ["cuda", "cpu"])
def test_weights_that_do_not_load_here_are_no_download(library, no_weights, monkeypatch, backend):
    """Until TASK-089.16 re-pins the catalogue per platform, the MLX entry is
    in it and loads on Apple Silicon only. It is named, it is excluded from the
    offer and the total, and it carries no byte count - a size shown in a
    column headed "download" is how "about 1.6 GB" ended up in a dialog
    whatever was picked."""
    monkeypatch.setattr(accel, "mlx_available", lambda: False)
    monkeypatch.setattr(accel, "transcription_backend", lambda: backend)

    offer = setup.plan(library)["downloads"]

    mlx = next(row for row in offer["entries"] if row["repo"].startswith("mlx-community/"))
    assert mlx["loads_here"] is False
    assert mlx["note"] == setup.NOT_LOADED_HERE
    assert mlx["bytes"] is None
    assert offer["total_bytes"] == sum(
        row["bytes"] for row in offer["entries"] if row["loads_here"] and not row["here"]
    )
    assert "not pinned yet" in offer["note"], "and one line says what this platform does instead"
    assert offer["catalogue"] == setup.CATALOGUE_TODAY


def test_on_an_mlx_machine_the_mlx_weights_are_the_download(library, no_weights, monkeypatch):
    """An Apple Silicon machine with mlx-whisper importable: the real probe is
    asked there, and the entry that loads is the one offered."""
    monkeypatch.setattr(accel, "mlx_available", lambda: True)
    monkeypatch.setattr(accel, "transcription_backend", lambda: "mlx")

    offer = setup.plan(library)["downloads"]

    mlx = next(row for row in offer["entries"] if row["repo"].startswith("mlx-community/"))
    assert mlx["loads_here"] is True and mlx["bytes"] > 0
    assert offer["total_bytes"] >= mlx["bytes"]
    assert offer["note"] == "", "nothing is unpinned here"


# --- no value, anywhere (#7) ---------------------------------------------------------


@pytest.mark.parametrize("name", ["huggingface", "openrouter", "openai"])
@pytest.mark.parametrize("source", ["settings row", "environment", "dotenv", "registry"])
def test_no_credential_value_reaches_anything_a_sitting_emits(
        library, no_weights, monkeypatch, tmp_path, capsys, name, source):
    """The marker is planted, and then looked for in everything that leaves
    this process: the plan, stdout, stderr and the stamp.

    Those four are the whole surface. Setup writes no log of its own, and the
    install log is the launcher's tee of this child's output, so stdout plus
    stderr is what that log can hold; what the launcher does with the document
    it sends is TASK-089.15's.
    """
    plant(source, name, SENTINEL, conn=library, monkeypatch=monkeypatch, tmp_path=tmp_path)

    setup.main(["--plan"])
    planned = capsys.readouterr()
    assert SENTINEL not in planned.out and SENTINEL not in planned.err

    document = json.dumps({"contract": setup.CONTRACT, "answers": {"default_tier": "turbo"}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))
    assert setup.main(["--apply-stdin"]) == 0

    applied = capsys.readouterr()
    assert SENTINEL not in applied.out and SENTINEL not in applied.err
    assert SENTINEL not in setup.stamp_path().read_text(encoding="utf-8")


def test_a_typed_secret_is_written_to_its_row_and_to_no_file(library, no_weights, monkeypatch, capsys):
    """The one place a typed secret goes is the row Settings can clear."""
    document = json.dumps({
        "contract": setup.CONTRACT,
        "answers": {"hf_token": SENTINEL, "llm_key_openrouter": SENTINEL + "-key"},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))
    monkeypatch.setattr(setup, "verify", lambda name, value: setup.Verdict(True))

    assert setup.main(["--apply-stdin"]) == 0

    rows = _rows(library)
    assert rows[credentials.HUGGINGFACE.setting_key] == SENTINEL
    assert rows[credentials.OPENROUTER.setting_key] == SENTINEL + "-key"
    printed = capsys.readouterr()
    assert SENTINEL not in printed.out and SENTINEL not in printed.err

    # The library is where a typed secret lives, so it is the one file allowed
    # to hold it - a copy of a library carries its keys, as it already did for
    # one typed in Settings (ADR-015, Consequences). Every other file under the
    # data directory, the stamp included, must not.
    elsewhere = [
        path.name
        for path in paths.DATA_DIR.rglob("*")
        if path.is_file()
        and not path.name.startswith(paths.DB_PATH.name)
        and SENTINEL in path.read_bytes().decode("utf-8", "ignore")
    ]
    assert elsewhere == []
    assert SENTINEL not in setup.stamp_path().read_text(encoding="utf-8")
    # And not only this marker: what the stamp holds per question is one of
    # four states, so there is no field a value could arrive in (TASK-089.11).
    assert set(stamp_document()["questions"].values()) <= set(setup.STATES)
    assert stamp_document()["questions"]["hf_token"] == "answered"


# --- a sitting where nothing was answered (#14) ---------------------------------------


def test_an_all_skipped_sitting_writes_no_row_and_stamps_every_id(
        library, no_weights, monkeypatch, capsys, tmp_path):
    """Every question skipped is the unattended install, and it has to leave
    the machine exactly as it was while still ending the sitting - or it would
    be put again at every start."""
    env_file = tmp_path / "env-for-test"
    before_rows, before_env = _rows(library), env_file.read_bytes()
    open_now = ids(setup.plan(library))
    assert open_now, "there is something to skip"
    document = json.dumps({"contract": setup.CONTRACT, "answers": {i: None for i in open_now}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 0

    assert _rows(library) == before_rows
    assert env_file.read_bytes() == before_env
    assert stamp_document()["questions"] == {asked: "skipped" for asked in open_now}
    assert "saved: nothing" in capsys.readouterr().out


# --- the speakers default has one writer, and it is the flag (#17) ---------------------


@pytest.mark.parametrize("flag,written", [("--diarize", "1"), ("--no-diarize", "0")])
def test_the_typed_flag_is_the_one_writer_of_the_speakers_default(library, no_weights, flag, written):
    """Somebody who types `--no-diarize` is choosing, not guarding: the flag
    shipped in v0.5.0 and v0.5.1 and an unattended install on a machine that
    will never separate speakers is the case that wants it (Robert, 2026-09-20,
    brief G3)."""
    assert setup.main([flag]) == 0

    assert _rows(library)[transcribe_dialog.SETTING_DIARIZE] == written


@pytest.mark.parametrize("run", ["tier", "empty", "document", "all-skipped"])
def test_no_other_path_writes_the_speakers_default(library, no_weights, monkeypatch, run):
    """Every other way in, including a document that carries a diarize answer:
    setup never switches the speakers default off or on by itself (W6)."""
    if run == "tier":
        setup.main(["--tier", "max"])
    elif run == "empty":
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"contract": setup.CONTRACT, "answers": {}})))
        setup.main(["--apply-stdin"])
    elif run == "document":
        answers = {"default_diarize": True, "diarize": "yes", "default_tier": "turbo"}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"contract": setup.CONTRACT, "answers": answers})))
        setup.main(["--apply-stdin"])
    else:
        open_now = ids(setup.plan(library))
        monkeypatch.setattr(sys, "stdin", io.StringIO(
            json.dumps({"contract": setup.CONTRACT, "answers": {i: None for i in open_now}})))
        setup.main(["--apply-stdin"])

    assert transcribe_dialog.SETTING_DIARIZE not in _rows(library)


# --- progress, and the exit codes (#13, #11) -------------------------------------------


class _Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_progress_on_a_pipe_is_one_json_line_per_whole_percent():
    """A line per chunk would be thousands for one file; a front-end parses
    these."""
    pipe = _Pipe()
    progress = setup.Progress(pipe)
    total = 1000
    for done in range(total + 1):  # byte by byte: 1001 calls, 101 whole percents
        progress("pyannote/speaker-diarization-community-1", done, total)

    lines = pipe.getvalue().splitlines()
    assert len(lines) == 101
    percents = [json.loads(line)["percent"] for line in lines]
    assert percents == sorted(set(percents)) == list(range(101))
    assert all(json.loads(line)["repo"] for line in lines)


def test_progress_on_a_terminal_is_a_bar_with_speed_and_what_is_left():
    terminal = _Terminal()
    progress = setup.Progress(terminal)

    progress("pyannote/speaker-diarization-community-1", 500, 1000)

    drawn = terminal.getvalue()
    assert "50%" in drawn and "/s" in drawn and "left" in drawn
    assert not drawn.startswith("{"), "a bar, not a JSON line"


@pytest.mark.parametrize("reason,code", [("token", 3), ("mismatch", 2), ("other", 1)])
def test_a_failed_download_keeps_the_exit_code_it_always_had(
        library, no_weights, monkeypatch, reason, code):
    def raises(*args, **kwargs):
        raise models.ModelError("the download failed", reason=reason)

    monkeypatch.setattr(models, "ensure", raises)

    assert setup.main(["--fetch-models"]) == code


def test_a_failed_download_still_ends_the_sitting(library, no_weights, monkeypatch):
    """The questions were put; a download that failed is a thing to try again,
    not a reason to ask all of them a second time at the next start. So the
    stamp is written - and the download stands in it as open rather than as
    answered, because nothing was fetched and `--plan` offers it again."""
    def raises(*args, **kwargs):
        raise models.ModelError("the download failed", reason="other")

    monkeypatch.setattr(models, "ensure", raises)

    assert setup.main(["--tier", "max", "--fetch-models"]) == 1

    assert stamp_document()["questions"] == {"default_tier": "answered", "fetch_models": "open"}


def test_a_download_that_fails_at_the_console_costs_what_it_costs_anywhere_else(
        library, no_weights, monkeypatch, capsys):
    """The console door and the flag door end the same way.

    `_sitting` applied its own answers, outside the one place in `main` that
    turns a failure into a sentence and an exit code - so the likeliest first
    run of all (Enter through the questions, which accepts the download, with
    no token to fetch the gated weights with) ended in a traceback and exit 1
    where every other door says what happened and exits 3 (review, 2026-09-22).
    """
    def raises(*args, **kwargs):
        raise models.ModelError("the weights are gated and no token was given", reason="token")

    monkeypatch.setattr(models, "ensure", raises)
    monkeypatch.setattr(setup, "at_a_terminal", lambda stream=None: True)
    monkeypatch.setattr(setup, "ask", lambda questions, **kwargs: {"fetch_models": "yes"})

    assert setup.main([]) == 3, "the code a gated download has always had"

    assert "the weights are gated" in capsys.readouterr().out, "in words, not a traceback"
    assert setup.stamp_path().exists(), "the questions were still put and answered"


def test_a_sitting_at_a_terminal_saves_what_was_answered(library, no_weights, monkeypatch, capsys):
    """The other end of the same door: what was typed at the console is written
    by the one path that writes every other door's answers, and says so."""
    monkeypatch.setattr(setup, "at_a_terminal", lambda stream=None: True)
    monkeypatch.setattr(
        setup, "ask",
        lambda questions, **kwargs: {"default_tier": "max", "llm_provider": setup.LATER})

    assert setup.main([]) == 0

    assert _rows(library)["default_tier"] == "max"
    assert ai_ui.PROVIDER_SETTING not in _rows(library), "and deciding later writes no row"
    assert "saved: defaults" in capsys.readouterr().out
    assert stamp_document()["questions"] == {"default_tier": "answered"}


# --- a typed credential is checked before it is saved (#6) ------------------------------


def transport(handler):
    return httpx2.MockTransport(handler)


def test_a_token_the_hub_does_not_know_is_not_saved(library, no_weights, monkeypatch, capsys):
    """401 reads "token not recognised"; the question comes back in `reopen`."""
    monkeypatch.setattr(
        setup, "verify", lambda name, value: setup.Verdict(False, "token not recognised", refused=True)
    )
    document = json.dumps({"contract": setup.CONTRACT, "answers": {"hf_token": SENTINEL}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 0

    assert credentials.HUGGINGFACE.setting_key not in _rows(library)
    printed = capsys.readouterr().out
    assert "token not recognised" in printed and "still open: hf_token" in printed
    assert SENTINEL not in printed


def test_the_hub_is_asked_once_with_a_head_and_401_reads_as_the_token(monkeypatch):
    asked: list[tuple[str, str]] = []

    def hub(request: httpx2.Request) -> httpx2.Response:
        asked.append((request.method, str(request.url)))
        assert request.headers["Authorization"] == "Bearer a-token"
        return httpx2.Response(401)

    verdict = setup.verify_huggingface("a-token", transport=transport(hub))

    assert verdict.refused is True and verdict.why == "token not recognised"
    assert len(asked) == 1 and asked[0][0] == "HEAD"
    assert "speaker-diarization" in asked[0][1]


def test_a_403_from_the_hub_reads_as_the_conditions_and_names_the_url():
    verdict = setup.verify_huggingface("a-token", transport=transport(lambda r: httpx2.Response(403)))

    assert verdict.refused is True
    assert "conditions not accepted" in verdict.why
    assert "https://hf.co/" in verdict.why, "with the page where they are accepted"


def test_a_hub_that_serves_the_file_accepts_the_token():
    verdict = setup.verify_huggingface("a-token", transport=transport(lambda r: httpx2.Response(200)))

    assert verdict.ok is True and verdict.refused is False


def test_a_hub_that_cannot_be_reached_is_no_verdict_about_the_token():
    def offline(request):
        raise httpx2.ConnectError("no route to host")

    verdict = setup.verify_huggingface("a-token", transport=transport(offline))

    assert verdict.ok is False and verdict.refused is False, "not saying the token is bad"


def test_openrouter_is_asked_for_free_and_401_refuses_the_key():
    """The check established in the grill of 2026-09-20: an authenticated
    request to the key endpoint costs nothing and answers 401 for a key that is
    missing, invalid or disabled. Never yet run with a real key - that run is
    the proof still owed (ADR-015, Open Questions)."""
    seen: list[str] = []

    def openrouter(request: httpx2.Request) -> httpx2.Response:
        seen.append(f"{request.method} {request.url}")
        assert request.headers["Authorization"] == "Bearer a-key"
        return httpx2.Response(401, json={"error": {"message": "No auth credentials found"}})

    verdict = setup.verify_openrouter("a-key", transport=transport(openrouter))

    assert verdict.refused is True
    assert seen == [f"GET {setup.OPENROUTER_KEY_URL}"]


def test_a_live_openrouter_key_is_accepted():
    answer = {"data": {"label": "a key", "usage": 0, "limit": None, "is_free_tier": True}}
    verdict = setup.verify_openrouter("a-key", transport=transport(lambda r: httpx2.Response(200, json=answer)))

    assert verdict.ok is True


def test_an_openai_key_is_checked_with_the_authenticated_model_list():
    """The same call the settings page already makes, so a rejected key raises
    before anything is written."""
    class Refuses:
        def models(self):
            raise base.AuthError("openai: the key was rejected")

        def close(self):
            pass

    assert setup.verify_openai("a-key", provider=Refuses()).refused is True

    class Answers:
        def models(self):
            return ["gpt-4o-mini"]

        def close(self):
            pass

    assert setup.verify_openai("a-key", provider=Answers()).ok is True


# --- the console asker, over the same list (#10) -----------------------------------
#
# What a real terminal adds is that `getpass` reads from the tty without
# echoing; that half needs a person at a PowerShell or cmd window and is
# recorded in the task as not run. Everything else about the asker is here.


def test_enter_takes_the_shown_default_and_s_skips(library, no_weights, monkeypatch):
    """One keystroke per question is the whole point of a default."""
    asked = [
        setup.Question("default_tier", "choice", "Quality?", [], "turbo", "turbo", None, "nothing", "Settings"),
        setup.Question("fetch_models", "yes-no", "Download now?", [], "", "yes", None, "later", "--fetch-models"),
        setup.Question("llm_provider", "choice", "Who answers?", [], "", setup.LATER, None, "nothing", "Settings"),
    ]
    typed = iter(["", "s", "  ollama  "])
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt="": pytest.fail("no secret was asked"))

    given = setup.ask([q.__dict__ for q in asked], out=io.StringIO(), read=lambda: next(typed))

    assert given == {"default_tier": "turbo", "fetch_models": None, "llm_provider": "ollama"}


def test_a_secret_is_read_without_being_echoed(library, no_weights, monkeypatch):
    """A typed token must not reach the terminal's scrollback, so it is read
    with `getpass` and never with `input`."""
    asked = setup.Question(
        "hf_token", "secret", "Token?", [], "", None, None, "nothing is saved", "Settings")
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt="": SENTINEL)
    out = io.StringIO()

    given = setup.ask([asked.__dict__], out=out, read=lambda: pytest.fail("a secret went through input()"))

    assert given == {"hf_token": SENTINEL}
    assert SENTINEL not in out.getvalue(), "and nothing echoes it afterwards"


def test_an_empty_answer_to_a_question_with_no_default_is_a_skip(library, no_weights, monkeypatch):
    """Skip is the default for every secret: nothing typed writes nothing."""
    asked = setup.Question(
        "hf_token", "secret", "Token?", [], "", None, None, "nothing is saved", "Settings")
    monkeypatch.setattr(setup.getpass, "getpass", lambda prompt="": "")

    assert setup.ask([asked.__dict__], out=io.StringIO()) == {"hf_token": None}


def test_an_answer_that_is_not_on_the_list_is_asked_again(library, no_weights):
    """A typing mistake is a question asked again, not an answer.

    The asker took any non-empty word, so `Max` reached `_validate` as a tier
    and `Ollama` as a provider - one raised, and for `llm_model_ollama`, which
    nothing validates, a typo was written to the row as a model Ollama has
    never heard of. The list a front-end renders is the list the console
    accepts (review, 2026-09-22).
    """
    asked = setup.Question(
        "llm_model_ollama", "choice", "Which model?",
        [{"value": "llama3.2:3b", "label": "llama3.2:3b", "note": ""}],
        "", None, None, "nothing is saved", "Settings")
    typed = iter(["gemma", "llama3.2:3b"])
    out = io.StringIO()

    given = setup.ask([asked.__dict__], out=out, read=lambda: next(typed))

    assert given == {"llm_model_ollama": "llama3.2:3b"}
    assert "gemma" not in json.dumps(given), "the mistake is not the answer"
    assert "not one of" in out.getvalue(), "and it says why it is asking again"


def test_an_answer_that_stays_unlisted_takes_the_shown_default(library, no_weights):
    """Asking again is bounded: a stream that never gives a listed value - a
    script feeding a terminal, a stuck key - falls back to what Enter would
    have done rather than spinning for ever."""
    asked = setup.Question(
        "default_tier", "choice", "Quality?",
        [{"value": tier, "label": tier, "note": ""} for tier in setup.TIERS],
        "", "turbo", None, "nothing is saved", "Settings")

    out = io.StringIO()

    given = setup.ask([asked.__dict__], out=out, read=lambda: "best")

    assert given == {"default_tier": "turbo"}
    assert out.getvalue().count("not one of") == setup.ASKS_AGAIN, "asked again, and bounded"
    assert "taking turbo" in out.getvalue(), "and it says what it took"


def test_a_listed_value_is_taken_however_it_was_capitalised(library, no_weights):
    """`Ollama` is the label the dialogs show; the value is `ollama`. Somebody
    reading one and typing it at the console has answered the question."""
    asked = setup.Question(
        "llm_provider", "choice", "Who answers?",
        [{"value": "ollama", "label": "Ollama", "note": ""}],
        "", setup.LATER, None, "nothing is saved", "Settings")

    given = setup.ask([asked.__dict__], out=io.StringIO(), read=lambda: "Ollama")

    assert given == {"llm_provider": "ollama"}, "normalised to what the engine writes"


def test_the_null_device_is_not_a_terminal():
    """`isatty()` alone is not the question on Windows, and the case is the one
    that matters: a launcher hands this child `subprocess.DEVNULL`, which is
    NUL, which is a character device and answers True. Measured 2026-09-22:
    `python -m scribe.setup < /dev/null` walked into the asker and died on
    EOFError, having printed a question nobody could answer."""
    import os

    with open(os.devnull) as nothing:
        if sys.platform == "win32":
            assert nothing.isatty() is True, "which is exactly why isatty is not enough here"
        assert setup.at_a_terminal(nothing) is False


def test_a_child_given_no_stdin_asks_nothing_and_stamps_nothing(
        library, no_weights, monkeypatch, capsys):
    """The launcher's own case, end to end through `main`."""
    import getpass as real_getpass
    import os

    monkeypatch.setattr(real_getpass, "getpass", lambda *a, **k: pytest.fail("getpass with nobody there"))
    with open(os.devnull) as nothing:
        monkeypatch.setattr(sys, "stdin", nothing)

        assert setup.main([]) == 0

    printed = capsys.readouterr().out
    assert "Found on this machine" in printed and "Still open" in printed
    assert "--apply-stdin" in printed, "and how to answer instead"
    assert not setup.stamp_path().exists()


def test_the_door_really_checks_a_typed_token_before_it_saves_it(
        library, no_weights, monkeypatch, capsys):
    """The wiring itself, with nothing stubbed but the socket.

    Every other test of a refusal replaces `setup.verify`, which would leave
    the path a real sitting takes - `main` -> `apply(check=verify)` ->
    `CHECKS[name]` -> the checker - asserted by nothing, and a name that did
    not match a key in `CHECKS` would save an unchecked credential in silence.
    """
    asked: list[str] = []

    def hub(request: httpx2.Request) -> httpx2.Response:
        asked.append(f"{request.method} {request.url.path}")
        return httpx2.Response(403)

    monkeypatch.setattr(setup, "_client", lambda transport: httpx2.Client(
        transport=httpx2.MockTransport(hub), timeout=5.0))
    document = json.dumps({"contract": setup.CONTRACT, "answers": {"hf_token": SENTINEL}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 0

    assert [line.split()[0] for line in asked] == ["HEAD"], "one announced request, and no more"
    assert credentials.HUGGINGFACE.setting_key not in _rows(library), "a refused token is not saved"
    printed = capsys.readouterr().out
    assert "conditions not accepted" in printed and "still open: hf_token" in printed
    assert SENTINEL not in printed


# --- the stamp, the gate and the migration (TASK-089.11) ------------------------------


def stamp_document() -> dict:
    """The stamp as it was written. Every assertion about it reads the file, so
    that the shape the launcher's gate parses is the shape under test."""
    return json.loads(setup.stamp_path().read_text(encoding="utf-8"))


def test_a_finished_sitting_records_one_state_per_question(library, no_weights, monkeypatch):
    """The stamp says what happened to each question and nothing about what was
    chosen: the gate reads it, and the gate has no business knowing anybody's
    provider.

    All four states in one sitting - a tier answered, a provider skipped, a key
    the service refused and so still open, and a Hugging Face token that was
    already on this machine and therefore never asked.
    """
    monkeypatch.setenv("HF_TOKEN", SENTINEL)
    monkeypatch.setattr(
        setup, "verify", lambda name, value: setup.Verdict(False, "key not recognised", refused=True))
    document = json.dumps({"contract": setup.CONTRACT, "answers": {
        "default_tier": "max",
        "llm_provider": None,
        "llm_key_openrouter": "a-key-the-service-refuses",
    }})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    assert setup.main(["--apply-stdin"]) == 0

    written = stamp_document()
    assert set(written) == {"contract", "ended", "questions"}
    assert written["contract"] == setup.CONTRACT
    assert written["ended"] > 0, "a finished sitting says when it ended; nothing else writes this file"
    assert written["questions"] == {
        "default_tier": "answered",
        "llm_provider": "skipped",
        "llm_key_openrouter": "open",
        "hf_token": "not_needed",
    }


def test_a_stamp_that_could_not_be_written_leaves_the_one_that_was_there(
        library, no_weights, monkeypatch):
    """The stamp is written in one piece - a temp file, then `os.replace`
    (spec section 2).

    Under this contract the file is the only record of what somebody skipped on
    purpose and of what the 0.5.x migration carried over, so a write that fails
    half-way does not cost one extra sitting: it loses those states for good. A
    truncating write is exactly that failure - it empties the file before it
    has the new text to put there.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"contract": setup.CONTRACT, "answers": {"hf_token": None, "default_tier": "max"}})))
    assert setup.main(["--apply-stdin"]) == 0
    first = stamp_document()
    assert first["questions"]["hf_token"] == "skipped", "what the next sitting must not lose"

    refused: list[str] = []

    def refuse(source, target):
        refused.append(str(target))
        raise OSError("the disk said no")

    monkeypatch.setattr(os, "replace", refuse)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"contract": setup.CONTRACT, "answers": {"llm_provider": "ollama"}})))

    with contextlib.suppress(OSError):
        setup.main(["--apply-stdin"])

    assert stamp_document() == first, "the sitting nobody could write left the one before it whole"
    assert refused, "and it was `os.replace` that failed: the stamp is put there in one step"


def test_a_credential_this_machine_has_does_not_overwrite_the_answer_that_saved_it(
        library, no_weights, monkeypatch):
    """`not_needed` is what is known about a question nobody was asked, so it
    fills a gap and never closes one.

    Across two sittings that is the whole of it. The first answers the token;
    by the second, the row it wrote is a source `find_all` reports, so the only
    thing keeping `answered` in the stamp is that `not_needed` is written with
    `setdefault`. Within one sitting the loop order would hide that.
    """
    monkeypatch.setattr(setup, "verify", lambda name, value: setup.Verdict(True))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"contract": setup.CONTRACT, "answers": {"hf_token": "a-token-this-test-made-up"}})))
    assert setup.main(["--apply-stdin"]) == 0
    assert stamp_document()["questions"]["hf_token"] == "answered"

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"contract": setup.CONTRACT, "answers": {"default_tier": "max"}})))
    assert setup.main(["--apply-stdin"]) == 0

    found = {row.credential for row in credentials.find_all(library) if row.found}
    assert credentials.HUGGINGFACE.name in found, "the row the first sitting wrote is a source by now"
    assert stamp_document()["questions"]["hf_token"] == "answered", \
        "an answer is not downgraded to `not_needed` by the row that answering it wrote"


def test_a_stamp_from_before_the_contract_gets_the_new_questions_once(
        library, no_weights, monkeypatch):
    """0.5.x wrote four fields and no question ids, so it is read as contract 1:
    a non-empty `provider` answered question 5, a non-empty `tier` 10,
    `hf_token` true 4 and a non-empty `fetched` 11 (spec section 2).

    The sitting an upgrade opens therefore asks what this version added and
    nothing that was already answered, those answers are carried into the new
    stamp, and it is asked once. A plan writes nothing, so neither the provider
    nor the tier is reset on the way.
    """
    setup.stamp_path().write_text(
        json.dumps({"provider": "ollama", "tier": "turbo", "hf_token": True, "fetched": []}),
        encoding="utf-8")
    before = _rows(library)
    everything = ids(setup.plan(library))
    assert {"hf_token", "llm_provider", "llm_key_openrouter", "fetch_models"} <= set(everything)

    asked_at_a_start = ids(setup.plan(library, unasked_only=True))

    assert "hf_token" not in asked_at_a_start, "true in the old stamp, so it was answered"
    assert "llm_provider" not in asked_at_a_start, "and so was the provider"
    assert "llm_key_openrouter" in asked_at_a_start, "the key question is what 0.5.x never put"
    assert "fetch_models" in asked_at_a_start, "an empty `fetched` answered nothing"
    assert ids(setup.plan(library)) == everything, "and `--setup` still asks all of them"
    assert _rows(library) == before, "nothing was reset: a plan writes nothing at all"

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"contract": setup.CONTRACT, "answers": {}})))
    assert setup.main(["--apply-stdin"]) == 0

    carried = stamp_document()
    assert carried["contract"] == setup.CONTRACT and carried["ended"] > 0
    assert carried["questions"]["llm_provider"] == "answered", "what 0.5.x answered is carried over"
    assert carried["questions"]["hf_token"] == "answered"
    assert carried["questions"]["default_tier"] == "answered"


def test_a_question_skipped_on_purpose_is_recorded_and_does_not_nag(
        library, no_weights, monkeypatch):
    """Skip on one question is not "ask me next time" on the whole sitting: it
    is an answer of its own - "I know, and no" - so the next start honours it
    while `--setup` still lists it (criterion 4). The rest of the sitting ends
    all the same: what nobody put is still asked."""
    open_now = ids(setup.plan(library))
    assert "hf_token" in open_now
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"contract": setup.CONTRACT, "answers": {"hf_token": None}})))

    assert setup.main(["--apply-stdin"]) == 0

    assert stamp_document()["questions"] == {"hf_token": "skipped"}
    assert "hf_token" in ids(setup.plan(library)), "and still there for `--setup`"
    assert ids(setup.plan(library, unasked_only=True)) == [
        asked for asked in ids(setup.plan(library)) if asked != "hf_token"]


def test_a_question_a_later_version_adds_is_asked_exactly_once(
        library, no_weights, monkeypatch, capsys):
    """The upgrade case at the engine's end.

    The stamp of a finished sitting carries the number the engine spoke then;
    this version adds a question and raises it, so the launcher sees two
    numbers that differ and pays for one `--plan --unasked-only`. This is what
    that plan answers: the one question this machine was never asked, and after
    it has been put, nothing. The comparison itself is the launcher's and is
    pinned there (tests/test_launcher.py, the gate).
    """
    before = setup.Question(
        "hf_token", "secret", "Hugging Face token.", [], "", None, None, "Nothing is saved.",
        "Settings > Transcription > Hugging Face token")
    added = setup.Question(
        "watch_folder", "choice", "Which folder should MyScribe watch?",
        [{"value": "none", "label": "None", "note": ""}], "", None, None,
        "Nothing is watched.", "Settings > Watch folders")
    monkeypatch.setattr(setup, "_questions", lambda conn, state, offer: [before, added])
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"contract": setup.CONTRACT, "answers": {"hf_token": None}})))
    assert setup.main(["--apply-stdin"]) == 0
    capsys.readouterr()  # what the sitting said; the plan below is read on its own
    stamped_at = stamp_document()["contract"]

    # The later version: one question more, and the number raised for it.
    monkeypatch.setattr(setup, "CONTRACT", stamped_at + 1)

    assert setup.main(["--plan", "--unasked-only"]) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["contract"] > stamped_at, "the two numbers the launcher compares"
    assert [q["id"] for q in printed["questions"]] == ["watch_folder"]
    assert ids(setup.plan(library)) == ["hf_token", "watch_folder"], "`--setup` still asks both"

    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"contract": setup.CONTRACT, "answers": {"watch_folder": None}})))
    assert setup.main(["--apply-stdin"]) == 0

    assert ids(setup.plan(library, unasked_only=True)) == [], "put once, and once is enough"
    assert stamp_document()["contract"] == stamped_at + 1, "and the stamp speaks the new number"


def test_a_token_removed_after_the_sitting_does_not_put_the_question_again(
        library, no_weights, monkeypatch):
    """Deliberate, and criterion 7: the gate is about what was asked, `--plan`
    is about the machine now. A token that was answered and later removed is
    open on this machine again and `--plan` says so - but a start does not put
    the question a second time, because it was put."""
    monkeypatch.setattr(setup, "verify", lambda name, value: setup.Verdict(True))
    monkeypatch.setattr(sys, "stdin", io.StringIO(
        json.dumps({"contract": setup.CONTRACT, "answers": {"hf_token": "a-token-this-test-typed"}})))
    assert setup.main(["--apply-stdin"]) == 0
    assert stamp_document()["questions"]["hf_token"] == "answered"

    library.execute("DELETE FROM setting WHERE key = ?", (credentials.HUGGINGFACE.setting_key,))
    library.commit()

    assert "hf_token" in ids(setup.plan(library)), "open on this machine again, and said so"
    assert "hf_token" not in ids(setup.plan(library, unasked_only=True)), "but not put again"
    assert setup.states()["hf_token"] == "answered", "the stamp records what was asked, not what is"


LATER_ROUTES = {
    "hf_token": ("POST", "/settings/hf-token"),
    "llm_provider": ("POST", "/settings/llm"),
    "llm_key_openrouter": ("POST", "/settings/llm/{provider}/key"),
    "llm_key_openai": ("POST", "/settings/llm/{provider}/key"),
    "llm_model_ollama": ("POST", "/settings/llm"),
    "default_tier": ("POST", "/settings"),
}
"""Per question, the Settings route that takes its answer later.

Criterion 4 asks that the route a question's `answer_later` line names exists,
and the honest reading of "exists" is the handler that writes the row - not a
sentence with the word Settings in it. `fetch_models` has no Settings
counterpart and names a command instead; the location (TASK-089.14) and the
library (TASK-089.19) are not in `_questions` yet and have no row here.
"""


@pytest.mark.parametrize("asked_id", sorted(LATER_ROUTES))
def test_the_route_a_question_names_for_later_is_a_route_the_app_has(asked_id):
    """One per route: the page really has the handler the sentence points at."""
    from scribe.web import settings as settings_page

    method, path = LATER_ROUTES[asked_id]
    have = {(verb, route.path) for route in settings_page.router.routes for verb in route.methods}

    assert (method, path) in have


def test_every_open_question_says_where_its_answer_can_be_given_later(library, no_weights):
    """And no question escapes the table: one added without an answer_later
    route is a question that can only be answered in the sitting that asked it,
    which is what criterion 4 exists to prevent."""
    for asked in setup.plan(library)["questions"]:
        assert asked["answer_later"], asked["id"]
        if asked["id"] == "fetch_models":
            assert asked["answer_later"] == "python -m scribe.setup --fetch-models"
            continue
        assert asked["id"] in LATER_ROUTES, "a new question needs its route in the table"
        assert "Settings" in asked["answer_later"]


def test_the_download_question_names_a_flag_the_engine_really_takes(library, no_weights, monkeypatch):
    """The one question with no Settings counterpart: its `answer_later` names
    `python -m scribe.setup --fetch-models`, so the flag has to be one the
    engine takes and acts on."""
    fetched: list = []

    def ensure(wanted=None, token=None, on_progress=None):
        fetched.append(wanted)
        return []

    monkeypatch.setattr(models, "ensure", ensure)

    assert setup.main(["--fetch-models"]) == 0

    assert fetched == [None], "the flag reached the download"
    assert stamp_document()["questions"]["fetch_models"] == "answered"


def test_the_ollama_model_question_names_the_settings_route_that_writes_its_row(
        ollama_chosen, no_weights, monkeypatch, ollama_state_unstubbed):
    """The seventh route, which only a machine with a ready Ollama is asked."""
    serving(monkeypatch, Daemon("gemma4:12b"))

    asked = question(setup.plan(ollama_chosen), "llm_model_ollama")

    assert asked is not None and "Settings" in asked["answer_later"]
    assert LATER_ROUTES["llm_model_ollama"] == ("POST", "/settings/llm")
