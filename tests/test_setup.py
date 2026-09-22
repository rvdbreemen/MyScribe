"""First-run setup: the four answers and what they write (TASK-040.06)."""

from __future__ import annotations

import getpass
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from scribe import credentials, db, models, paths, setup
from scribe.stages import diarize
from scribe.web import ai_ui, transcribe_dialog


@pytest.fixture
def conn(tmp_path, monkeypatch):
    # `needed()` loads `.env`, and this machine has a real one beside the
    # repository: without pointing that at a temporary file the test would be
    # answering about the developer's token rather than about a fresh install.
    empty = tmp_path / "env-for-test"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("SCRIBE_ENV_FILE", str(empty))
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "test.db")
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


def token_lines(env_file: Path) -> list[str]:
    """Every line that defines HF_TOKEN, read the way the app reads the file."""
    text = env_file.read_text(encoding="utf-8-sig")
    return [line for line in text.splitlines() if line.partition("=")[0].strip() == "HF_TOKEN"]


def test_the_token_goes_to_the_row_the_app_reads_and_to_no_second_place(tmp_path, conn):
    """A typed secret has one store: the settings row (ADR-015, TASK-089.09).

    Until 2026-09-22 it was written to `.env` as well, and TASK-040.06's
    criterion #2 recorded that as the point ("writes it to the per-user .env").
    The reason it gave - the first run happens before there is a browser to
    type into - is still true and no longer decides: the row is what Settings
    can show and clear, and what every lookup reads first through the resolver
    of TASK-089.04, so a copy in `.env` would be a second copy of a secret in
    the less protected of the two files, going stale the moment either moves.

    What `write_token` did is `env.write_env`'s since TASK-089.03, and the two
    cases this file used to pin - a file with a BOM, a line written
    `HF_TOKEN = old` - are pinned against that function in tests/test_env.py.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\nHF_TOKEN=\nOPENAI_API_KEY=sk-keep\n", encoding="utf-8")

    setup.apply(setup.Answers(hf_token="hf_typed"), conn, env_file=env_file)

    assert diarize.hf_token(conn) == "hf_typed", "the row the app reads"
    assert token_lines(env_file) == ["HF_TOKEN="], "and the file is left exactly as it was"
    assert "OPENAI_API_KEY=sk-keep" in env_file.read_text(encoding="utf-8")


def test_the_provider_and_tier_are_saved_where_settings_reads_them(tmp_path, conn):
    setup.apply(setup.Answers(provider="ollama", tier="max", diarize=False), conn, env_file=tmp_path / ".env")

    assert ai_ui.setting_get(conn, ai_ui.PROVIDER_SETTING) == "ollama"
    options = transcribe_dialog.read_defaults(conn)
    assert options.tier == "max" and options.diarize is False


def _setting_rows(conn) -> list[tuple]:
    """Every settings row there is, so a test can say "nothing changed"."""
    return [tuple(row) for row in conn.execute("SELECT key, value FROM setting ORDER BY key")]


def test_a_sitting_where_every_question_was_skipped_writes_nothing(tmp_path, conn):
    """The engine's side of ADR-016 and ADR-015: the launcher hands over a
    sitting in which nobody answered anything, and that must leave the machine
    exactly as it was - no provider row, no transcription defaults, no `.env` -
    while still ending the sitting, or it would be put again at every start.

    Nothing here was changed to make it pass; it pins what `apply` already
    does, because TASK-089.25 makes the launcher rely on it.
    """
    defaults = transcribe_dialog.read_defaults(conn)
    before = _setting_rows(conn)
    env_file = tmp_path / ".env"

    report = setup.apply(setup.Answers(), conn, env_file=env_file)

    assert report["wrote"] == []
    assert _setting_rows(conn) == before
    assert transcribe_dialog.read_defaults(conn) == defaults
    assert not env_file.exists(), "a token nobody typed writes no file"
    assert setup.done() is True, "asked once, not at every start"


def test_an_unknown_provider_is_refused_before_anything_is_written(tmp_path, conn):
    with pytest.raises(ValueError):
        setup.apply(setup.Answers(provider="not-a-provider"), conn, env_file=tmp_path / ".env")

    assert not setup.stamp_path().exists(), "a refused answer must not look finished"


def test_finishing_writes_the_stamp_so_it_is_asked_once(tmp_path, conn):
    assert setup.done() is False

    setup.apply(setup.Answers(provider="ollama", tier="turbo"), conn, env_file=tmp_path / ".env")

    assert setup.done() is True
    assert json.loads(setup.stamp_path().read_text(encoding="utf-8"))["provider"] == "ollama"


def test_what_is_still_needed_is_about_this_machine_now(tmp_path, conn, monkeypatch):
    """Not "has setup run" - a token can be removed and weights deleted, and
    the honest answer then is that they are missing again."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    # Both, and they are two different doors: `paths.MODELS_DIR` is a module
    # constant computed at import, so moving DATA_DIR does not move it, and
    # this machine has a real pipeline in the real one.
    monkeypatch.setattr(models, "root", lambda: tmp_path / "models")
    monkeypatch.setattr(diarize, "local_weights_dir", lambda: tmp_path / "models" / "pyannote")
    # And the hub cache, which `present` now consults: this machine has the
    # whisper weights there, and the question is about a fresh install.
    from scribe import doctor

    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")

    state = setup.needed(conn)

    assert state["hf_token"] is False
    assert state["to_download"] > 0
    assert state["diarization_possible"] is False


def test_the_weights_are_only_fetched_when_asked(tmp_path, conn, monkeypatch):
    called = []
    monkeypatch.setattr(models, "ensure", lambda *a, **k: called.append(a) or [])

    setup.apply(setup.Answers(provider="ollama"), conn, env_file=tmp_path / ".env")
    assert called == []

    setup.apply(setup.Answers(provider="ollama", fetch_models=True), conn, env_file=tmp_path / ".env")
    assert len(called) == 1


# --- the engine: what a sitting refuses, and what it must not write (TASK-089.09) ---
#
# The marker below is planted where a credential would be and then looked for
# in everything a sitting emits. It is never a real token: a test that needs
# one would be a test nobody can run twice.

SENTINEL = "myscribe-sentinel-0000-never-printed"


def _rows(db_path: Path) -> dict[str, str]:
    """Every settings row, on a connection of its own.

    `main()` writes through a connection the test does not hold, so a read on
    the fixture's could be answered from before that write and prove nothing.
    """
    fresh = sqlite3.connect(db_path)
    try:
        return {key: value for key, value in fresh.execute("SELECT key, value FROM setting")}
    finally:
        fresh.close()


class _NotATerminal:
    """Stdin that nobody is sitting at. `isatty` is the whole interface the
    engine asks about, and an unattended install is the case."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


def test_a_token_on_the_command_line_is_refused_and_nothing_is_written(tmp_path, conn, capsys):
    """A secret never rides on a command line (ADR-015): the process list of a
    machine shows it for as long as the child lives, and the shell keeps it in
    its history afterwards. The flag stays recognised so that the refusal is a
    sentence somebody can act on rather than an argparse usage error."""
    code = setup.main(["--hf-token", SENTINEL])

    printed = capsys.readouterr()
    assert code == 2
    assert SENTINEL not in printed.out and SENTINEL not in printed.err
    assert _rows(paths.DB_PATH) == {}, "a refused flag writes no row"
    assert not setup.stamp_path().exists(), "and does not look like a finished sitting"


def test_a_bad_provider_is_refused_before_the_token_is_written(tmp_path, conn):
    """The token used to be written first and the provider checked after, so a
    typing mistake in `--provider` left half a sitting applied: a token in the
    library and no provider, with an exit code that said nothing was saved.

    Two layers close it, and one alone would leave it half closed: argparse's
    `choices` refuses the flag before `main` does anything at all - which is
    what raises here, with its own usage code 2 - and `apply` still validates
    every answer ahead of the first write, for a document argparse never sees.
    """
    with pytest.raises(SystemExit) as refused:
        setup.main(["--hf-token", SENTINEL, "--provider", "not-a-provider"])

    assert refused.value.code == 2
    assert _rows(paths.DB_PATH) == {}, "nothing is written until every answer has been checked"
    assert not setup.stamp_path().exists()


def test_a_bad_provider_in_a_document_is_refused_before_any_write(tmp_path, conn, monkeypatch, capsys):
    """The other entry point: argparse never sees an `--apply-stdin` document,
    so the check that matters is the one inside `apply`.

    The credential check is stubbed to accept, and that is what makes this bite
    rather than tidiness. Measured on a copy on 2026-09-22 by moving
    `_validate` below the token write: with the real check in the way, the Hub
    answered 401 for this sentinel, the token was not saved for *that* reason,
    and the test stayed green while the ordering it is about was broken. It
    also means the suite asks huggingface.co nothing.
    """
    monkeypatch.setattr(setup, "verify", lambda name, value: setup.Verdict(True))
    document = json.dumps({"contract": setup.CONTRACT, "answers": {
        "hf_token": SENTINEL, "llm_provider": "not-a-provider", "default_tier": "max"}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    code = setup.main(["--apply-stdin"])

    assert code == 1
    assert SENTINEL not in capsys.readouterr().out
    assert _rows(paths.DB_PATH) == {}, "not the token, and not the tier either"
    assert not setup.stamp_path().exists()


def test_a_tier_answer_alone_does_not_write_the_speakers_default(tmp_path, conn):
    """`default_diarize` is "the last options submitted", rewritten by every
    upload. Setup writing it as a side effect of saving the tier puts a choice
    nobody made in a row seven other call sites rely on (ADR-015, Must Not).

    Read with SQL and not through `read_defaults`, which substitutes "1" for a
    row that is not there - so the absence this is about would be invisible.
    """
    assert "default_diarize" not in _rows(paths.DB_PATH)

    setup.apply(setup.Answers(tier="max"), conn, env_file=tmp_path / ".env")

    after = _rows(paths.DB_PATH)
    assert after.get("default_tier") == "max", "the answer that was given is saved"
    assert "default_diarize" not in after, "and the one that was not, is not"


def test_a_bare_run_with_nobody_at_the_terminal_asks_nothing_and_stamps_nothing(
        tmp_path, conn, capsys, monkeypatch):
    """Run with no answers and no terminal - a service, a CI step, a pipe - the
    engine has nobody to ask. Stamping "done" there is the failure that costs
    the most: the launcher never asks again, and the questions were never put.
    """
    monkeypatch.setattr(sys, "stdin", _NotATerminal())
    monkeypatch.setattr(getpass, "getpass", _refuse_to_ask)

    code = setup.main([])

    printed = capsys.readouterr().out
    assert code == 0
    assert not setup.stamp_path().exists(), "nothing was asked, so nothing was answered"
    assert "Found on this machine" in printed, "it still says what it found"
    assert "Still open" in printed, "and what it would have asked"


def _refuse_to_ask(*args, **kwargs):
    """`getpass` on a machine with no terminal: reaching it at all is the bug."""
    raise AssertionError("getpass was called with nobody at the terminal")


@pytest.mark.parametrize("provider,label", [("openrouter", "OpenRouter"), ("openai", "OpenAI")])
def test_a_cloud_provider_saved_without_a_key_says_what_that_costs(
        tmp_path, conn, capsys, monkeypatch, provider, label):
    """Saving `openrouter` with no key anywhere exited 0 and said "saved:
    provider". The first Summary then failed as a job on the board, which is
    the confirmed gap requirement 2 of the installer exists to close.

    The variables are deleted here and not in conftest: `os.environ` is
    deliberately left alone there, so a test that must see no key says so
    itself - this machine really does hold an OPENROUTER_TOKEN, and without
    this the question would be answered by the developer's environment.
    """
    for name in credentials.CREDENTIALS[provider].env_vars:
        monkeypatch.delenv(name, raising=False)

    code = setup.main(["--provider", provider])

    printed = capsys.readouterr().out
    assert code == 0
    assert f"no-key card until {label} has a key" in printed, "what will happen is said in words"
    assert "Settings > AI providers > Save key" in printed, "and where to put one"


def test_a_key_the_service_refused_leaves_the_provider_with_no_key_and_says_so(
        tmp_path, conn, capsys, monkeypatch):
    """The likelier route to a provider with no key is a mistyped one.

    The card was decided on the answer that was *offered* rather than on what
    was written, so a key the service refused - not saved, its question
    reopened - still counted as a key and the sentence was never printed. The
    run then said "saved: provider" and "still open: llm_key_openrouter" and
    left somebody to work out for themselves that Summary would fail
    (review, 2026-09-22).
    """
    for name in credentials.CREDENTIALS["openrouter"].env_vars:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        setup, "verify",
        lambda name, value: setup.Verdict(False, "the key was not recognised", refused=True))
    document = json.dumps({"contract": setup.CONTRACT, "answers": {
        "llm_provider": "openrouter", "llm_key_openrouter": SENTINEL}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(document))

    code = setup.main(["--apply-stdin"])

    printed = capsys.readouterr().out
    assert code == 0
    assert credentials.OPENROUTER.setting_key not in _rows(paths.DB_PATH), "a refused key is not saved"
    assert "no-key card until OpenRouter has a key" in printed
    assert "still open: llm_key_openrouter" in printed
    assert SENTINEL not in printed
