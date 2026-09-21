"""Tests for the environment gate (plan Task 7)."""

import shutil
import sqlite3

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


# --- diarization readiness (TASK-040.06) -------------------------------------------


def test_a_local_pipeline_directory_is_enough(tmp_path, monkeypatch):
    """The route that needs no token and no network, and the one a bundled
    package uses: `diarize.local_weights_dir()` with a config.yaml in it."""
    from scribe.stages import diarize

    local = tmp_path / "pyannote"
    local.mkdir()
    (local / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
    monkeypatch.setattr(diarize, "local_weights_dir", lambda: local)

    check = doctor.check_diarization()

    assert check.ok and "local pipeline" in check.detail


def test_no_token_and_no_pipeline_is_reported_not_passed(tmp_path, monkeypatch):
    """The false green this check exists for: on 2026-09-18 the card said every
    required check passed on a machine whose diarize stage could not start."""
    from scribe.stages import diarize

    monkeypatch.setattr(diarize, "local_weights_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(diarize, "hf_token", lambda conn=None: None)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)

    check = doctor.check_diarization()

    assert not check.ok
    assert check.optional, "transcription still works; this must not fail the install"
    assert "hf.co/pyannote" in check.fix_hint and str(tmp_path / "absent") in check.fix_hint


def test_a_token_the_hub_refuses_names_the_conditions(tmp_path, monkeypatch):
    """401 and 403 are different fixes - an unknown token, and a known one that
    has not accepted the gate - and the message must not blame the network."""
    from scribe.stages import diarize

    monkeypatch.setattr(diarize, "local_weights_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(diarize, "hf_token", lambda conn=None: "hf_pretend")
    monkeypatch.setattr(doctor, "_gated_repo_reachable", lambda repo, token: (False, "HTTP 403 - the conditions are not accepted for this token"))

    check = doctor.check_diarization()

    assert not check.ok and "403" in check.detail


def test_the_web_process_never_runs_it():
    """ADR-001: it imports the diarize stage and reaches the network, neither of
    which belongs in a request that renders the settings page."""
    assert doctor.check_diarization not in doctor.WEB_SAFE_CHECKS


# --- the ollama line (TASK-089.06) --------------------------------------------------


def _absent(monkeypatch):
    """A machine with no Ollama, without asking this one - which has it."""
    from scribe import ollama_setup

    monkeypatch.setattr(
        ollama_setup, "state", lambda **kwargs: ollama_setup.State(state=ollama_setup.ABSENT)
    )
    return ollama_setup


def test_the_ollama_check_reports_the_state_in_words_and_is_always_optional(monkeypatch):
    """Four states with four different fixes, and one sentence each. "Not
    installed" and "installed but stopped" used to be the same sentence, which
    is the whole reason this task exists."""
    ollama_setup = _absent(monkeypatch)

    check = doctor.check_ollama()

    assert check.name == "ollama"
    assert check.optional is True
    assert check.ok is False
    assert check.detail == "not installed on this machine"

    monkeypatch.setattr(
        ollama_setup,
        "state",
        lambda **kwargs: ollama_setup.State(
            state=ollama_setup.READY, version="0.34.2", chat_models=("gemma4:12b",)
        ),
    )
    ready = doctor.check_ollama()

    assert ready.ok is True
    assert ready.optional is True
    assert "0.34.2" in ready.detail and "gemma4:12b" in ready.detail


def test_only_a_ready_ollama_is_a_tick_and_only_an_absent_one_is_told_to_install(monkeypatch):
    """Two rules, and they are the same sentence read twice.

    `[OK  ] ollama  installed at C:\\...\\ollama.EXE but not answering` marked a
    machine that does not work as working, on the one command this project
    tells you to run before blaming the code. `present` is the right question
    for the install offer (ADR-017) and the wrong one for the mark, which means
    "is this working".

    And the hint may not follow the mark: `render()` prints `fix_hint` for
    every check that is not ok, so flipping the mark without it would tell
    somebody whose Ollama is merely stopped to install one - advice ADR-017
    forbids acting on. Absent is the only state with an install in its future.
    """
    from scribe import ollama_setup

    monkeypatch.setattr(
        ollama_setup,
        "state",
        lambda **kwargs: ollama_setup.State(
            state=ollama_setup.INSTALLED_NOT_RUNNING, binary=r"C:\Ollama\ollama.EXE"
        ),
    )
    stopped = doctor.check_ollama()

    assert stopped.ok is False and stopped.optional is True
    assert "start it" in stopped.detail
    assert "ollama.com" not in (stopped.fix_hint or "")
    printed = doctor.render([stopped])
    assert "[SKIP] ollama" in printed and "ollama.com" not in printed

    absent = _absent(monkeypatch)
    assert "ollama.com" in doctor.render([doctor.check_ollama()])

    monkeypatch.setattr(
        absent,
        "state",
        lambda **kwargs: ollama_setup.State(
            state=ollama_setup.RUNNING_NO_CHAT_MODEL, version="0.34.2"
        ),
    )
    no_model = doctor.check_ollama()

    assert no_model.ok is False
    assert "ollama pull" in no_model.detail  # its own fix, and not an install
    assert "ollama.com" not in doctor.render([no_model])


def test_the_doctor_passes_on_a_machine_with_no_ollama(tmp_path, monkeypatch, capsys):
    """It never fails the gate: a machine without Ollama is not broken, it just
    has no local AI - the same standing yt-dlp has."""
    _absent(monkeypatch)
    monkeypatch.setattr(doctor.paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(doctor.paths, "DB_PATH", tmp_path / "myscribe.db")
    monkeypatch.setattr(doctor.paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(doctor.paths, "LOGS_DIR", tmp_path / "logs")

    code = doctor.main(["--no-gpu"])

    out = capsys.readouterr().out
    assert code == 0
    assert "[SKIP] ollama" in out
    assert "not installed on this machine" in out


def test_the_web_process_never_asks_about_ollama():
    """Deliberate, not an oversight. The loopback GET itself would be safe -
    the settings page already makes it through `provider_rows` - but
    `chat_models()` falls back to one POST /api/show *per model* on a daemon
    too old to report capabilities, and a page render that quietly became nine
    requests is what this tuple exists to prevent. The doctor's own command
    still runs it.
    """
    assert doctor.check_ollama in doctor.CPU_CHECKS
    assert doctor.check_ollama not in doctor.WEB_SAFE_CHECKS


def test_the_token_this_check_looks_for_includes_the_one_saved_in_settings(
    tmp_path, monkeypatch, library_db_unstubbed
):
    """The loop TASK-089.04 closes: this check's own fix hint says to store
    the token as the `hf_token` setting, and until now the check called
    `hf_token(None)` and so could never see the row it had just recommended.

    `_diarization_token` and not `check_diarization`: with a token found the
    check asks the hub whether the conditions are accepted, and what this test
    is about is the lookup, not the network.
    """
    from scribe import credentials, db

    database = tmp_path / "myscribe.db"
    conn = db.connect(database)
    db.migrate(conn)
    conn.execute("INSERT INTO setting(key, value) VALUES ('hf_token', 'saved-in-settings')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(doctor.paths, "DB_PATH", database)
    for name in credentials.HUGGINGFACE.env_vars + credentials.HUGGINGFACE.legacy_env_vars:
        monkeypatch.delenv(name, raising=False)

    assert doctor._diarization_token() == "saved-in-settings"


def test_a_database_that_will_not_open_does_not_hide_a_token_in_the_environment(monkeypatch):
    """The check used to fall back to the environment outside its own `try`.
    A broken database must not turn a machine with HF_TOKEN plainly set into
    "no local pipeline and no Hugging Face token"."""
    from scribe import credentials

    def refuse():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(credentials, "library_db", refuse)
    monkeypatch.setenv("HF_TOKEN", "hf_from_the_environment")

    assert doctor._diarization_token() == "hf_from_the_environment"
