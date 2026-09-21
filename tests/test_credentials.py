"""The one credential resolver (TASK-089.04).

Four copies of the Hugging Face lookup disagreed about where a token may
live, so a token saved in Settings was invisible to the doctor and to
`python -m scribe.models`. These tests pin the one order, the vocabulary each
consumer keeps, and the two manners that matter for a secret: a value is
never in an output, and a credential found somewhere is never copied
somewhere less safe.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from scribe import credentials, db, env, paths

# The marker every planted value carries. A test asserts it appears in no
# repr, str or JSON; a value that leaked would bring it along.
SENTINEL = "SENTINEL-never-printed"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


def planted_dotenv(path: Path, text: str):
    """The resolver's `.env` reader, pointed at a file this test wrote.

    Parsed with `env.parse`, which is what the real reader uses, so a test
    cannot agree with a syntax the app does not.
    """
    path.write_text(text, encoding="utf-8")
    return lambda _path=None: (env.parse(path.read_text(encoding="utf-8")), path)


def planted_registry(**values: str):
    """A registry reader over `HIVE__VARIABLE=value` keyword arguments.

    Answers the user hive before the machine hive whatever order they were
    written in, which is the order the real reader promises.
    """
    hives = ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE")

    def hits(name: str):
        return tuple(
            (hive, value)
            for hive in hives
            for spelled, value in values.items()
            if spelled == f"{hive}__{name}"
        )

    return hits


def put_setting(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


# --- the module keeps to itself -----------------------------------------------


def test_the_resolver_imports_nothing_from_the_llm_package_or_the_stages():
    """Both of them import it. A resolver that imported them back would be a
    cycle, and would drag torch into `python -m scribe.setup`."""
    source = Path(credentials.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    offenders = sorted(n for n in imported if n.startswith(("scribe.llm", "scribe.stages")))
    assert offenders == [], f"scribe/credentials.py imports {offenders}"


def test_the_table_looks_for_exactly_what_each_provider_looks_for():
    """One list of variable names per credential, not two that drift apart."""
    from scribe import llm
    from scribe.llm import base

    for name, provider in llm.PROVIDERS.items():
        if not provider.key_env_vars:
            continue  # Ollama: loopback, no credential to find
        credential = credentials.CREDENTIALS[name]
        assert credential.env_vars == provider.key_env_vars
        assert credential.setting_key == base.setting_key(name)


def test_the_hugging_face_credential_knows_the_row_the_app_writes():
    from scribe.stages import diarize

    assert credentials.HUGGINGFACE.setting_key == diarize.SETTING_TOKEN


# --- the order ----------------------------------------------------------------


def test_the_settings_row_outranks_everything_else(conn, tmp_path, monkeypatch):
    put_setting(conn, "hf_token", "from-the-row")
    monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(tmp_path / ".env", "HF_TOKEN=from-the-file\n"))

    resolved = credentials.resolve(
        conn,
        credentials.HUGGINGFACE,
        environ={"HF_TOKEN": "from-the-environment"},
        registry=planted_registry(HKEY_CURRENT_USER__HF_TOKEN="from-the-user-hive"),
    )

    assert resolved.value == "from-the-row"
    assert str(resolved.source) == "settings"


def test_the_environment_outranks_the_file_and_the_file_outranks_the_registry(conn, tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(dotenv, "HF_TOKEN=from-the-file\n"))
    registry = planted_registry(HKEY_CURRENT_USER__HF_TOKEN="from-the-user-hive")

    both = credentials.resolve(
        conn, credentials.HUGGINGFACE, environ={"HF_TOKEN": "from-the-environment"}, registry=registry
    )
    assert (both.value, str(both.source)) == ("from-the-environment", "HF_TOKEN (environment)")

    file_only = credentials.resolve(conn, credentials.HUGGINGFACE, environ={}, registry=registry)
    assert file_only.value == "from-the-file"
    assert str(file_only.source) == f"HF_TOKEN ({dotenv})", "the source names the file it read"


def test_the_user_hive_outranks_the_machine_hive(conn, monkeypatch):
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    resolved = credentials.resolve(
        conn,
        credentials.HUGGINGFACE,
        environ={},
        registry=planted_registry(
            HKEY_CURRENT_USER__HF_TOKEN="from-the-user-hive",
            HKEY_LOCAL_MACHINE__HF_TOKEN="from-the-machine-hive",
        ),
    )

    assert resolved.value == "from-the-user-hive"
    assert str(resolved.source) == "HF_TOKEN (Windows registry, HKEY_CURRENT_USER)"


def test_the_variable_order_holds_across_every_place_a_variable_can_live(conn, monkeypatch):
    """HF_TOKEN in the registry still beats HUGGINGFACE_TOKEN in the shell:
    the order fixed is the order of the *names*, not of the places."""
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    resolved = credentials.resolve(
        conn,
        credentials.HUGGINGFACE,
        environ={"HUGGINGFACE_TOKEN": "second-name"},
        registry=planted_registry(HKEY_LOCAL_MACHINE__HF_TOKEN="first-name"),
    )

    assert resolved.value == "first-name"


def test_the_hub_s_own_variable_comes_after_the_two_this_app_documents(conn, monkeypatch):
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    resolved = credentials.resolve(
        conn,
        credentials.HUGGINGFACE,
        environ={"HUGGING_FACE_HUB_TOKEN": "the-hub-s-own", "HUGGINGFACE_TOKEN": "documented"},
        registry=planted_registry(),
    )
    assert resolved.value == "documented"

    alone = credentials.resolve(
        conn, credentials.HUGGINGFACE, environ={"HUGGING_FACE_HUB_TOKEN": "the-hub-s-own"}, registry=planted_registry()
    )
    assert alone.value == "the-hub-s-own"
    assert str(alone.source) == "HUGGING_FACE_HUB_TOKEN (environment)"


def test_the_login_file_is_the_last_place_looked_and_it_is_read_in_place(conn, tmp_path, monkeypatch):
    login = tmp_path / "token"
    login.write_text("hf_from_the_login_file\n", encoding="utf-8")
    monkeypatch.setattr(credentials, "login_file", lambda: login)
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    resolved = credentials.resolve(conn, credentials.HUGGINGFACE, environ={}, registry=planted_registry())

    assert resolved.value == "hf_from_the_login_file"
    assert str(resolved.source) == f"the Hugging Face login file ({login})"

    beaten = credentials.resolve(
        conn, credentials.HUGGINGFACE, environ={"HUGGING_FACE_HUB_TOKEN": "a-variable"}, registry=planted_registry()
    )
    assert beaten.value == "a-variable"


def test_only_hugging_face_has_a_login_file(conn, tmp_path, monkeypatch):
    """An OpenAI key is not in Hugging Face's login file, and looking there
    for one would report a Hugging Face token as an OpenAI key."""
    login = tmp_path / "token"
    login.write_text("hf_from_the_login_file\n", encoding="utf-8")
    monkeypatch.setattr(credentials, "login_file", lambda: login)
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    resolved = credentials.resolve(conn, "openai", environ={}, registry=planted_registry())

    assert resolved.value is None


def test_a_name_the_dotenv_file_put_in_the_environment_is_reported_as_the_file(conn, tmp_path, monkeypatch):
    """`load_dotenv` copies the file into `os.environ`, and afterwards nothing
    in the environment says which names came from where. `env.applied()` does,
    and that is what "snapshotted before `.env` is applied" is reconstructed
    from."""
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(dotenv, "HF_TOKEN=from-the-file\n"))

    resolved = credentials.resolve(
        conn,
        credentials.HUGGINGFACE,
        environ={"HF_TOKEN": "from-the-file"},
        registry=planted_registry(),
        applied=frozenset({"HF_TOKEN"}),
    )

    assert str(resolved.source) == f"HF_TOKEN ({dotenv})"
    assert credentials.find_all(
        conn,
        environ={"HF_TOKEN": "from-the-file"},
        registry=planted_registry(),
        applied=frozenset({"HF_TOKEN"}),
    )[0].also_in == (), "one place, reported once - not as the environment as well"


# --- blank is missing, everywhere ---------------------------------------------


@pytest.mark.parametrize("where", ["settings", "environment", "dotenv", "registry", "login-file"])
def test_blank_or_whitespace_is_missing_in_every_source(where, conn, tmp_path, monkeypatch):
    """A shell's `export HF_TOKEN=`, a row cleared to spaces, a quoted empty
    line: Hugging Face treats "" as a token and answers 401, so an empty one
    has to read as no token at all."""
    blank = "   \t "
    environ: dict[str, str] = {}
    registry = planted_registry()
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    if where == "settings":
        put_setting(conn, "hf_token", blank)
    elif where == "environment":
        environ = {"HF_TOKEN": blank}
    elif where == "dotenv":
        monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(tmp_path / ".env", f'HF_TOKEN="{blank}"\n'))
    elif where == "registry":
        registry = planted_registry(HKEY_LOCAL_MACHINE__HF_TOKEN=blank)
    else:
        login = tmp_path / "token"
        login.write_text(blank + "\n", encoding="utf-8")
        monkeypatch.setattr(credentials, "login_file", lambda: login)

    resolved = credentials.resolve(conn, credentials.HUGGINGFACE, environ=environ, registry=registry)

    assert resolved.value is None, f"a blank {where} value is not a token"
    assert resolved.found is False
    assert credentials.find_all(conn, environ=environ, registry=registry)[0].found is False


@pytest.mark.parametrize("trouble", [PermissionError, UnicodeDecodeError])
def test_a_login_file_that_will_not_open_is_reported_and_not_raised(trouble, conn, tmp_path, monkeypatch):
    """The doctor asks this on somebody else's machine. A file whose
    permissions or encoding are wrong is a thing to say, not a traceback."""
    login = tmp_path / "token"
    login.write_text("anything", encoding="utf-8")
    monkeypatch.setattr(credentials, "login_file", lambda: login)
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    readable = Path.read_text

    def refuse(self, *args, **kwargs):
        if self != login:
            return readable(self, *args, **kwargs)
        if trouble is PermissionError:
            raise PermissionError(13, "Permission denied")
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(Path, "read_text", refuse)

    resolved = credentials.resolve(conn, credentials.HUGGINGFACE, environ={}, registry=planted_registry())
    assert resolved.value is None

    row = credentials.find_all(conn, environ={}, registry=planted_registry())[0]
    assert "not readable" in row.note
    assert str(login) in row.note


def test_a_database_without_a_setting_table_is_no_row_and_not_an_error(tmp_path, monkeypatch):
    """`python -m scribe.models` on a machine that has never started the app
    still has to answer about the environment."""
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))
    bare = sqlite3.connect(str(tmp_path / "bare.db"))
    try:
        resolved = credentials.resolve(
            bare, credentials.HUGGINGFACE, environ={"HF_TOKEN": "found"}, registry=planted_registry()
        )
    finally:
        bare.close()

    assert resolved.value == "found"


@pytest.mark.skipif(os.name != "nt", reason="one variable per name whatever the case is a Windows rule")
def test_on_windows_a_lowercase_name_in_the_dotenv_file_is_the_variable(conn, tmp_path, monkeypatch):
    """`hf_token=` in the file fills HF_TOKEN on Windows, and `load_dotenv`
    honours that - so a resolver that did not would report the file as holding
    nothing while the app read a token out of it."""
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(dotenv, "hf_token=spelled-in-lower-case\n"))

    resolved = credentials.resolve(conn, credentials.HUGGINGFACE, environ={}, registry=planted_registry())

    assert resolved.value == "spelled-in-lower-case"
    assert str(resolved.source) == f"HF_TOKEN ({dotenv})"


# --- the first place that has one is the last place looked at -----------------


def test_a_settings_row_is_answered_without_the_file_the_registry_or_the_hub(conn, monkeypatch):
    """The places are not equally cheap: reading the login file imports
    `huggingface_hub`, which ADR-001 keeps out of a command's start-up, and
    the doctor, `python -m scribe.models` and every render of the settings
    page ask this question. A row answers with one SELECT."""
    put_setting(conn, "hf_token", "from-the-row")

    def refuse(*args, **kwargs):
        raise AssertionError("looked further than the settings row")

    monkeypatch.setattr(credentials, "login_file", refuse)
    monkeypatch.setattr(credentials, "dotenv_values", refuse)

    resolved = credentials.resolve(conn, credentials.HUGGINGFACE, environ={}, registry=refuse)

    assert resolved.value == "from-the-row"


def test_a_variable_that_answers_stops_the_walk_at_that_variable(conn, monkeypatch):
    """HF_TOKEN in the shell is an answer, so HUGGINGFACE_TOKEN, the hub's own
    spelling and the login file are not looked for at all."""
    asked: list[str] = []
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    def no_login_file():
        raise AssertionError("the login file was read although a variable had answered")

    monkeypatch.setattr(credentials, "login_file", no_login_file)

    def registry(name: str):
        asked.append(name)
        return ()

    resolved = credentials.resolve(
        conn, credentials.HUGGINGFACE, environ={"HF_TOKEN": "from-the-environment"}, registry=registry
    )

    assert resolved.value == "from-the-environment"
    assert asked == ["HF_TOKEN"], "the registry was asked about a name that had already been answered"


def test_the_found_table_looks_everywhere_even_after_it_has_an_answer(conn, tmp_path, monkeypatch):
    """The stopping is `resolve`'s alone: the table's whole subject is the
    places that agree or disagree with the winner."""
    put_setting(conn, "hf_token", "from-the-row")
    login = tmp_path / "token"
    login.write_text("from-the-login-file\n", encoding="utf-8")
    monkeypatch.setattr(credentials, "login_file", lambda: login)
    monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(tmp_path / ".env", "HF_TOKEN=from-the-file\n"))

    row = credentials.find_all(conn, environ={}, registry=planted_registry())[0]

    assert row.source == "settings"
    assert row.also_in == (f"HF_TOKEN ({tmp_path / '.env'})", f"the Hugging Face login file ({login})")
    assert row.conflict is True


# --- a found credential stays where it was found ------------------------------


def test_a_token_in_the_login_file_is_never_copied_into_dotenv_or_a_row(conn, tmp_path, monkeypatch):
    """A login-file token can rotate. A copy in `.env` or in a settings row
    would go stale and then outrank the real one - and `.env` is the less
    protected of the two files."""
    login = tmp_path / "token"
    login.write_text("hf_from_the_login_file\n", encoding="utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("# nothing here\n", encoding="utf-8")
    before = dotenv.read_bytes()
    monkeypatch.setattr(credentials, "login_file", lambda: login)
    monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(dotenv, "# nothing here\n"))

    resolved = credentials.resolve(conn, credentials.HUGGINGFACE, environ={}, registry=planted_registry())
    assert resolved.value == "hf_from_the_login_file"

    assert dotenv.read_bytes() == before, "the `.env` file was written to"
    rows = conn.execute("SELECT key FROM setting WHERE key=?", ("hf_token",)).fetchall()
    assert rows == [], "the token was copied into a settings row"


# --- the found table says what it knows, and never a value --------------------


def test_the_found_table_names_the_other_places_and_whether_they_disagree(conn, tmp_path, monkeypatch):
    put_setting(conn, "hf_token", "same")
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(credentials, "dotenv_values", planted_dotenv(dotenv, "HF_TOKEN=same\n"))

    agreeing = credentials.find_all(conn, environ={"HF_TOKEN": "same"}, registry=planted_registry())[0]
    assert agreeing.found and agreeing.source == "settings"
    assert agreeing.also_in == ("HF_TOKEN (environment)", f"HF_TOKEN ({dotenv})")
    assert agreeing.conflict is False

    disagreeing = credentials.find_all(conn, environ={"HF_TOKEN": "different"}, registry=planted_registry())[0]
    assert disagreeing.conflict is True


def test_the_found_table_has_a_row_per_credential_and_says_when_there_is_none(conn, monkeypatch):
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    rows = credentials.find_all(conn, environ={}, registry=planted_registry())

    assert [row.credential for row in rows] == list(credentials.CREDENTIALS)
    assert all(row.found is False and row.source == "" and row.conflict is False for row in rows)


def test_no_output_of_the_found_table_can_hold_a_value(conn, tmp_path, monkeypatch):
    """Every source, planted at once, and then every way this row is shown.
    `find_all` carries no value at all, which is why this holds by
    construction rather than by a careful __repr__."""
    login = tmp_path / "token"
    login.write_text(f"{SENTINEL}-login-file\n", encoding="utf-8")
    monkeypatch.setattr(credentials, "login_file", lambda: login)
    monkeypatch.setattr(
        credentials,
        "dotenv_values",
        planted_dotenv(tmp_path / ".env", f"HF_TOKEN={SENTINEL}-dotenv\n"),
    )
    put_setting(conn, "hf_token", f"{SENTINEL}-settings")
    environ = {
        "HF_TOKEN": f"{SENTINEL}-environment",
        "HUGGING_FACE_HUB_TOKEN": f"{SENTINEL}-the-hub-s-variable",
    }
    registry = planted_registry(
        HKEY_CURRENT_USER__HF_TOKEN=f"{SENTINEL}-user-hive",
        HKEY_LOCAL_MACHINE__HF_TOKEN=f"{SENTINEL}-machine-hive",
    )

    rows = credentials.find_all(conn, environ=environ, registry=registry)
    shown = [
        repr(rows),
        str(rows),
        json.dumps([asdict(row) for row in rows]),
        *[repr(row) for row in rows],
        *[str(row) for row in rows],
    ]

    assert rows[0].found and rows[0].conflict, "every source was planted, with different values"
    for output in shown:
        assert SENTINEL not in output, "a credential value reached an output"


def test_the_resolved_value_is_not_in_its_repr(conn, monkeypatch):
    """`resolve` is the one thing that carries a value, and it ends up in
    tracebacks and in log lines like anything else."""
    monkeypatch.setattr(credentials, "dotenv_values", lambda path=None: ({}, Path("nowhere.env")))

    resolved = credentials.resolve(
        conn, credentials.HUGGINGFACE, environ={"HF_TOKEN": f"{SENTINEL}-environment"}, registry=planted_registry()
    )

    assert resolved.value == f"{SENTINEL}-environment"
    assert SENTINEL not in repr(resolved) and SENTINEL not in str(resolved)
    assert "HF_TOKEN (environment)" in repr(resolved)


# --- the vocabulary each consumer keeps ---------------------------------------


def test_the_short_source_is_the_one_the_settings_page_has_always_printed():
    """The settings page renders this string (`_settings_llm.html`). The
    fuller wording - the hive, the `.env` path - belongs to the found table."""
    assert credentials.short_source(None) == ""
    assert credentials.short_source(credentials.Source(credentials.SETTINGS)) == "settings"
    assert credentials.short_source(credentials.Source(credentials.ENVIRONMENT, "HF_TOKEN")) == "HF_TOKEN"
    assert credentials.short_source(credentials.Source(credentials.DOTENV, "HF_TOKEN", "/x/.env")) == "HF_TOKEN"
    for hive in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE", ""):
        source = credentials.Source(credentials.REGISTRY, "OPENROUTER_TOKEN", hive)
        assert credentials.short_source(source) == "OPENROUTER_TOKEN (Windows registry)"


def test_the_short_source_can_name_the_login_file_which_has_no_variable_name():
    """The one source with no variable name. Without this the settings page
    would say a token is in effect "from" nothing at all - and the whole point
    of this task is that it says where it found it. It cannot reach
    `base.api_key`: only Hugging Face has a login file, and it is not an LLM
    provider."""
    source = credentials.Source(credentials.LOGIN_FILE, where="/home/x/.cache/huggingface/token")

    assert credentials.short_source(source) == "the Hugging Face login file"


# --- the database a command outside the app can still look in -----------------


def test_the_library_database_is_opened_for_a_command_that_was_handed_none(
    tmp_path, monkeypatch, library_db_unstubbed
):
    """The seam TASK-089.04 exists for: `python -m scribe.models` and the
    doctor run outside the app, so without this they never see a token saved
    in Settings - which is exactly what the error message tells the user to
    do."""
    database = tmp_path / "library" / "myscribe.db"
    database.parent.mkdir()
    made = db.connect(database)
    db.migrate(made)
    put_setting(made, "hf_token", "saved-in-settings")
    made.close()
    monkeypatch.setattr(paths, "DB_PATH", database)

    with credentials.library_db() as conn:
        assert conn is not None
        assert credentials.resolve(conn, credentials.HUGGINGFACE, environ={}, registry=planted_registry()).value == (
            "saved-in-settings"
        )


def test_no_database_is_no_row_and_never_an_error(tmp_path, monkeypatch, library_db_unstubbed):
    """A machine that has never started the app still has to be able to ask
    about the environment."""
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "never-created" / "myscribe.db")

    with credentials.library_db() as conn:
        assert conn is None
        assert credentials.resolve(conn, credentials.HUGGINGFACE, environ={"HF_TOKEN": "e"}, registry=planted_registry()).value == "e"


def test_a_database_that_will_not_open_is_no_row_and_never_an_error(tmp_path, monkeypatch, library_db_unstubbed):
    """A file that is not a database, or one another process is holding: this
    is a question about a credential, not about the database."""
    not_a_database = tmp_path / "myscribe.db"
    not_a_database.write_bytes(b"this is not a database")
    monkeypatch.setattr(paths, "DB_PATH", not_a_database)

    with credentials.library_db() as conn:
        assert conn is None


# --- the machine's own credentials cannot reach a test ------------------------


def test_the_autouse_fixture_hides_this_machine_s_registry_and_login_file():
    """Without it, this machine's HKLM HF_TOKEN answers every test that asks
    whether a token is set, and "no token anywhere" goes green for the wrong
    reason."""
    assert credentials.registry_hits("HF_TOKEN") == ()
    assert credentials.registry_hits("OPENROUTER_TOKEN") == ()
    assert not credentials.login_file().exists()
    assert credentials.dotenv_values()[0] == {}
    with credentials.library_db() as conn:
        assert conn is None, "a test opened this machine's own library"


@pytest.mark.skipif(os.name != "nt", reason="there is no registry to read anywhere else")
def test_the_shipped_registry_enumerator_really_reads_this_machines_hives(registry_names_unstubbed):
    """The one test that runs `registry_names` itself, against the real hives.

    Everything else in the suite sees the stub from tests/conftest.py, which is
    there so this machine's four `OLLAMA_*` names cannot make `absent`
    unreachable - and that left 37 lines of enumeration, two hives and a prefix
    filter asserted by nothing: a version returning `()` unconditionally passed
    every file that names it. It is a leg the install offer stands on
    (ADR-017): no names found reads as "nobody configured an Ollama here".

    Read-only and names only. A prefix is taken from what the machine happens
    to hold rather than written down, so this asserts the filter without
    asserting that any particular variable is set, and no value is read, named
    or printed - not even in a failure message.
    """
    every = credentials.registry_names("")

    assert every, "the enumerator found no variable at all in either hive"
    assert all(isinstance(hive, str) and isinstance(name, str) for hive, name in every)
    assert all(hive in (credentials.HKCU, credentials.HKLM) for hive, _name in every)

    first = every[0][1]
    prefix = first[:3]
    narrowed = credentials.registry_names(prefix)

    assert first in [name for _hive, name in narrowed]
    assert all(name.upper().startswith(prefix.upper()) for _hive, name in narrowed)
    assert credentials.registry_names("ZZ_NOT_A_REAL_PREFIX_") == ()
