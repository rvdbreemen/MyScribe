"""First-run setup: the four answers and what they write (TASK-040.06)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribe import db, models, paths, setup
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


def test_the_token_goes_where_the_app_reads_it(tmp_path, conn):
    env_file = tmp_path / ".env"
    env_file.write_text("# a comment\nHF_TOKEN=\nOPENAI_API_KEY=sk-keep\n", encoding="utf-8")

    setup.apply(setup.Answers(hf_token="hf_typed"), conn, env_file=env_file)

    text = env_file.read_text(encoding="utf-8")
    assert "HF_TOKEN=hf_typed" in text
    assert "OPENAI_API_KEY=sk-keep" in text, "the rest of the file must survive"
    assert diarize.hf_token(conn) == "hf_typed", "and the settings row too, for the app to read"


def test_a_second_run_replaces_the_token_rather_than_appending(tmp_path, conn):
    """Two HF_TOKEN= lines is a file where the last one silently wins, and the
    user has no way to see which they are using."""
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=first\n", encoding="utf-8")

    setup.apply(setup.Answers(hf_token="second"), conn, env_file=env_file)

    lines = [line for line in env_file.read_text(encoding="utf-8").splitlines() if line.startswith("HF_TOKEN=")]
    assert lines == ["HF_TOKEN=second"]


def token_lines(env_file: Path) -> list[str]:
    """Every line that defines HF_TOKEN, read the way the app reads the file."""
    text = env_file.read_text(encoding="utf-8-sig")
    return [line for line in text.splitlines() if line.partition("=")[0].strip() == "HF_TOKEN"]


def test_a_file_saved_with_a_bom_still_ends_up_with_one_token_line(tmp_path):
    """Notepad leaves a BOM. The app reads past it (utf-8-sig); the writer
    read plain utf-8, saw a first key it did not know, and added a second."""
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN=old\nOPENAI_API_KEY=sk-keep\n", encoding="utf-8-sig")

    setup.write_token("new", env_file=env_file)

    assert token_lines(env_file) == ["HF_TOKEN=new"]


def test_a_token_line_written_with_spaces_is_replaced_not_joined(tmp_path):
    """`HF_TOKEN = old` is a line the app reads, so it is a line the writer
    has to recognise."""
    env_file = tmp_path / ".env"
    env_file.write_text("HF_TOKEN = old\n", encoding="utf-8")

    setup.write_token("new", env_file=env_file)

    assert token_lines(env_file) == ["HF_TOKEN=new"]


def test_the_provider_and_tier_are_saved_where_settings_reads_them(tmp_path, conn):
    setup.apply(setup.Answers(provider="ollama", tier="max", diarize=False), conn, env_file=tmp_path / ".env")

    assert ai_ui.setting_get(conn, ai_ui.PROVIDER_SETTING) == "ollama"
    options = transcribe_dialog.read_defaults(conn)
    assert options.tier == "max" and options.diarize is False


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
