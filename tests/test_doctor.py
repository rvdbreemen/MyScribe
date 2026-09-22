"""Tests for the environment gate (plan Task 7)."""

import json
import re
import shutil
import sqlite3
import sys
import types
import urllib.error
from pathlib import Path

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
    monkeypatch.setattr(doctor, "checks", lambda include_gpu=True, on_start=None: [bad])

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


# --- a machine with no NVIDIA card, and the two cases that must stay red (TASK-089.12) ---


def _tmp_library(monkeypatch, tmp_path):
    """A library of this test's own, so nothing here touches the real one."""
    monkeypatch.setattr(doctor.paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(doctor.paths, "DB_PATH", tmp_path / "myscribe.db")
    monkeypatch.setattr(doctor.paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(doctor.paths, "LOGS_DIR", tmp_path / "logs")


def _fake_torch(monkeypatch, cuda, device=False):
    """`torch` as `check_gpu_runtime` asks about it, and nothing more.

    A stand-in module rather than the real one: the branches under test are a
    CPU-only build and a CUDA build that reaches no device, and this machine
    can only ever be the third case, a healthy card. Nothing is loaded and no
    card is touched.
    """
    monkeypatch.setattr(doctor.cuda_setup, "ensure_cuda_libs", lambda: None)
    module = types.ModuleType("torch")
    module.__version__ = "2.10.0+cu128" if cuda else "2.10.0+cpu"
    module.version = types.SimpleNamespace(cuda=cuda)
    module.cuda = types.SimpleNamespace(is_available=lambda: device)
    monkeypatch.setitem(sys.modules, "torch", module)
    return module


def _hardware(monkeypatch, present):
    """Replace the hardware question - the seam this task exists to build.

    The test may not use `CUDA_VISIBLE_DEVICES` for this: on a machine with a
    card the variable is indistinguishable from a broken driver, which is the
    case that must stay red. It is cleared instead, because conftest
    deliberately leaves `os.environ` alone and a shell that had it set would
    defeat the branch silently.

    Returns the list of answers the probe gave, so a test can assert it was
    asked at all. Without that witness these tests pass for the wrong reason on
    exactly one machine each: a bypassed seam looks green here, where the real
    probe answers True, and green again on the card-less machine criterion 9
    wants, where it answers False. `raising=False` is deliberately absent -
    the attribute exists, and a renamed seam must be an error, not a stub
    parked on the module beside the function that ignores it.
    """
    asked = []

    def probe():
        asked.append(present)
        return present

    monkeypatch.setattr(doctor, "nvidia_hardware_present", probe)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    return asked


def test_a_machine_with_no_nvidia_card_reads_as_information_not_a_failure(monkeypatch):
    """CPU transcription is a supported mode (README.md:46), so a laptop that
    never had a driver must not fail the gate - and must not be told to run
    nvidia-smi, a tool it does not have, for a driver it does not want."""
    _fake_torch(monkeypatch, cuda="12.8", device=False)
    asked = _hardware(monkeypatch, present=False)

    check = doctor.check_gpu_runtime()

    assert check.ok is True
    assert check.optional is False, "still a required check; on this machine it simply passes"
    assert "no NVIDIA GPU on this machine; transcription on cpu" in check.detail
    assert not check.fix_hint, "nothing to fix: this machine is behaving as designed"
    assert asked == [False], "the verdict must come from the seam, not from the machine running the test"


def test_the_whole_command_exits_zero_on_a_machine_with_no_card(tmp_path, monkeypatch, capsys):
    """Criterion 1 end to end. The smoke is stubbed: what it would do on a cold
    machine is download 1.6 GB and transcribe, which is criterion 9's question
    and not this one's."""
    _tmp_library(monkeypatch, tmp_path)
    _fake_torch(monkeypatch, cuda="12.8", device=False)
    _hardware(monkeypatch, present=False)
    monkeypatch.setattr(
        doctor,
        "GPU_CHECKS",
        (
            doctor.check_gpu_runtime,
            lambda: doctor.Check(name="gpu-smoke", ok=True, detail="stubbed; no clip is transcribed here"),
        ),
    )

    code = doctor.main([])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "[OK  ] gpu-runtime" in out
    assert "nvidia-smi" not in out


def test_a_card_behind_a_broken_driver_stays_a_required_failure(monkeypatch):
    """The case this branch was written for. Softening it would tell Robert's
    machine "transcription on cpu" with exit 0 the day his driver broke, and he
    would never learn that the card had stopped working."""
    _fake_torch(monkeypatch, cuda="12.8", device=False)
    asked = _hardware(monkeypatch, present=True)

    check = doctor.check_gpu_runtime()

    assert check.ok is False and check.optional is False
    assert "cannot reach a device" in check.detail
    assert "nvidia-smi" in check.fix_hint
    assert asked == [True], "red because the seam said there is a card, not because this machine has one"


@pytest.mark.parametrize("value", ["-1", ""])
def test_a_card_hidden_by_the_environment_stays_red_and_the_detail_names_it(monkeypatch, value):
    """Membership, not truthiness: an empty value hides a card as surely as -1.
    The probe says "no hardware" here and the check stays red anyway, which is
    the point - a hidden device is not a machine without one."""
    _fake_torch(monkeypatch, cuda="12.8", device=False)
    _hardware(monkeypatch, present=False)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", value)

    check = doctor.check_gpu_runtime()

    assert check.ok is False and check.optional is False
    assert "CUDA_VISIBLE_DEVICES" in check.detail
    assert "nvidia-smi" in check.fix_hint


def test_a_cpu_only_torch_build_stays_a_required_failure_and_says_uv_sync(monkeypatch):
    """ADR-012's Must, and the guard against a fix that passes every case: no
    hardware and no device, and it is still red, because a CPU-only build means
    the lock was not honoured. Only the hint moves - the venv has no pip."""
    _fake_torch(monkeypatch, cuda=None, device=False)
    monkeypatch.setattr(doctor.accel, "is_apple_silicon", lambda: False)
    _hardware(monkeypatch, present=False)

    check = doctor.check_gpu_runtime()

    assert check.ok is False and check.optional is False
    assert "CPU-only build" in check.detail
    assert "uv sync" in check.fix_hint
    assert not re.search(r"\bpip\b", check.fix_hint)


# --- the hardware probe: any doubt counts as hardware present ------------------------


def _fake_winreg(monkeypatch, subkeys=(), fail=False):
    """A winreg for the PCI enumeration, so the rule can be tested off Windows."""

    class Key:
        def __init__(self, names):
            self.names = names

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(root, path):
        if fail:
            raise OSError(5, "Access is denied")
        return Key(list(subkeys))

    module = types.ModuleType("winreg")
    module.HKEY_LOCAL_MACHINE = object()
    module.OpenKey = open_key
    module.QueryInfoKey = lambda key: (len(key.names), 0, 0)
    module.EnumKey = lambda key, index: key.names[index]
    monkeypatch.setitem(sys.modules, "winreg", module)
    return module


def _no_nvidia_smi(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor, "_NVIDIA_SMI_LOCATIONS", ())


def test_the_driver_tool_off_the_path_still_counts_as_a_card(monkeypatch, tmp_path):
    """The second half of the nvidia-smi signal: a machine whose PATH does not
    carry the driver's tool, but whose disk does. It is only ever looked for,
    never run - a broken driver's nvidia-smi exits non-zero with the card still
    in the slot. Without this test the whole `_NVIDIA_SMI_LOCATIONS` arm can be
    deleted and every other probe test stays green, and that arm is the only
    signal left on a machine whose card the OS has stopped enumerating."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    _fake_winreg(monkeypatch, subkeys=["VEN_8086&DEV_A0E0"])

    installed = tmp_path / "nvidia-smi.exe"
    installed.write_text("", encoding="utf-8")
    monkeypatch.setattr(doctor, "_NVIDIA_SMI_LOCATIONS", (installed,))

    assert doctor.nvidia_hardware_present() is True

    # A location that is merely listed proves nothing; the registry decides.
    monkeypatch.setattr(doctor, "_NVIDIA_SMI_LOCATIONS", (tmp_path / "not-installed.exe",))

    assert doctor.nvidia_hardware_present() is False


def test_a_registry_that_will_not_answer_counts_as_hardware_present(monkeypatch):
    """Any doubt is hardware present, in the code and not in a comment. An
    access-denied read must never be told apart from "enumerated, none found"."""
    monkeypatch.setattr(sys, "platform", "win32")
    _no_nvidia_smi(monkeypatch)
    _fake_winreg(monkeypatch, fail=True)

    assert doctor.nvidia_hardware_present() is True


def test_the_registry_finds_a_card_by_its_pci_vendor_id(monkeypatch):
    """Vendor 10DE, which is the ground truth and needs no driver: a card whose
    driver was never installed is still enumerated under Enum\\PCI."""
    monkeypatch.setattr(sys, "platform", "win32")
    _no_nvidia_smi(monkeypatch)
    _fake_winreg(monkeypatch, subkeys=["VEN_8086&DEV_A0E0", "VEN_10DE&DEV_2216&SUBSYS_38821462"])

    assert doctor.nvidia_hardware_present() is True


def test_a_registry_with_no_nvidia_vendor_key_is_a_machine_without_a_card(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    _no_nvidia_smi(monkeypatch)
    _fake_winreg(monkeypatch, subkeys=["VEN_8086&DEV_A0E0", "VEN_1022&DEV_1450"])

    assert doctor.nvidia_hardware_present() is False


def test_nvidia_smi_on_path_is_enough_on_any_os(monkeypatch):
    """The driver's own tool being installed is evidence of a card, whatever
    the registry or sysfs would say next."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        doctor.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None
    )

    assert doctor.nvidia_hardware_present() is True


def test_a_sysfs_that_is_not_there_counts_as_hardware_present(monkeypatch, tmp_path):
    """A container without /sys, a kernel that lists nothing: learned nothing
    is not "no card"."""
    monkeypatch.setattr(sys, "platform", "linux")
    _no_nvidia_smi(monkeypatch)
    monkeypatch.setattr(doctor, "_SYSFS_PCI_DEVICES", tmp_path / "absent")

    assert doctor.nvidia_hardware_present() is True

    empty = tmp_path / "devices"
    empty.mkdir()
    monkeypatch.setattr(doctor, "_SYSFS_PCI_DEVICES", empty)

    assert doctor.nvidia_hardware_present() is True


def test_sysfs_reads_the_pci_vendor_of_every_device(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    _no_nvidia_smi(monkeypatch)
    # Named without the colons a real sysfs uses: Windows cannot create those,
    # and what is under test is the vendor file, not the directory's spelling.
    devices = tmp_path / "devices"
    (devices / "0000.00.02.0").mkdir(parents=True)
    (devices / "0000.00.02.0" / "vendor").write_text("0x8086\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_SYSFS_PCI_DEVICES", devices)

    assert doctor.nvidia_hardware_present() is False

    (devices / "0000.01.00.0").mkdir()
    (devices / "0000.01.00.0" / "vendor").write_text("0x10de\n", encoding="utf-8")

    assert doctor.nvidia_hardware_present() is True


def test_an_operating_system_this_probe_does_not_know_counts_as_hardware_present(monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    _no_nvidia_smi(monkeypatch)

    assert doctor.nvidia_hardware_present() is True


# --- hints that name a tool this machine has ----------------------------------------


@pytest.mark.parametrize(
    "platform,command",
    [
        ("win32", "winget install Gyan.FFmpeg"),
        ("darwin", "brew install ffmpeg"),
        ("linux", "apt install ffmpeg"),
    ],
)
def test_the_ffmpeg_hint_names_this_machines_package_manager(monkeypatch, platform, command):
    """One hint for three package managers was wrong on two of them; the
    commands are README.md:45's own."""
    monkeypatch.setattr(doctor, "_run", lambda cmd: (False, ""))
    monkeypatch.setattr(sys, "platform", platform)

    check = doctor.check_ffmpeg()

    assert check.ok is False
    assert command in check.fix_hint


def _refuse_a_database(*args, **kwargs):
    raise sqlite3.OperationalError("unable to open database file")


def test_no_fix_hint_this_machine_can_print_names_pip(tmp_path, monkeypatch):
    """uv owns the environment (ADR-012) and the venv has no pip, so a hint
    that says "pip install" is advice nobody here can follow.

    Asserted over rendered hints and not over the source: pip is a substring of
    pipeline, a word this module uses about itself, and a green check's hint is
    the empty string - collecting those would prove nothing, so every check in
    the module that can produce a hint is driven into a failing branch, one
    branch per check and all four of gpu-runtime's, since that is the check
    this task changes.

    Collected as (case, hint) pairs and not counted. A round-number floor let
    two hints quietly become the empty string, which in the test whose job is
    hints that exist is the one thing it may not allow; and the three
    gpu-runtime branches all report the name "gpu-runtime", so the case has to
    be named here or the failure message cannot say which one went missing.
    """
    from scribe import models
    from scribe.stages import diarize

    monkeypatch.setattr(doctor, "_run", lambda cmd: (False, ""))
    monkeypatch.setattr(doctor.urls, "installed_version", lambda: None)
    monkeypatch.setattr(
        models,
        "status",
        lambda **_: [
            {"here": False, "bytes": 2**30, "repo": "pyannote/x", "gated": True, "wanted": True}
        ],
    )
    monkeypatch.setattr(diarize, "local_weights_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(doctor, "_diarization_token", lambda: None)
    monkeypatch.setattr(doctor.db, "connect", _refuse_a_database)
    _tmp_library(monkeypatch, tmp_path)

    hints = []
    # The three cheapest failures to stage, each in a context of its own so the
    # fake interpreter, the old SQLite and the unwritable directory do not
    # follow the checks below into their own branches.
    with monkeypatch.context() as an_older_python:
        an_older_python.setattr(sys, "version_info", types.SimpleNamespace(major=3, minor=11, micro=9))
        hints.append(("python", doctor.check_python().fix_hint))
    with monkeypatch.context() as an_older_sqlite:
        an_older_sqlite.setattr(doctor.sqlite3, "sqlite_version_info", (3, 34, 0))
        hints.append(("sqlite", doctor.check_sqlite().fix_hint))
    with monkeypatch.context() as a_directory_that_refuses:

        def refuse(*args, **kwargs):
            raise OSError(13, "Permission denied")

        a_directory_that_refuses.setattr(doctor.tempfile, "NamedTemporaryFile", refuse)
        hints.append(("data-dir", doctor.check_data_dir_writable().fix_hint))

    hints += [
        ("ffmpeg", doctor.check_ffmpeg().fix_hint),
        ("ffprobe", doctor.check_ffprobe().fix_hint),
        ("yt-dlp", doctor.check_ytdlp().fix_hint),
        ("disk-space", doctor.check_disk_space(floor_gb=10**6).fix_hint),
        ("database", doctor.check_database().fix_hint),
        ("diarization", doctor.check_diarization().fix_hint),
        ("models", doctor.check_models().fix_hint),
        ("ollama", doctor.check_ollama().fix_hint),
        ("gpu-smoke", doctor.gpu_smoke(clip=tmp_path / "nope.wav").fix_hint),
    ]
    monkeypatch.setattr(doctor.accel, "is_apple_silicon", lambda: False)
    for case, cuda, present in (
        ("gpu-runtime: a card behind a broken driver", "12.8", True),
        ("gpu-runtime: no NVIDIA hardware", "12.8", False),
        ("gpu-runtime: a CPU-only build", None, False),
    ):
        _fake_torch(monkeypatch, cuda=cuda, device=False)
        _hardware(monkeypatch, present=present)
        hints.append((case, doctor.check_gpu_runtime().fix_hint))
    monkeypatch.setattr(doctor.accel, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(doctor.accel, "mlx_available", lambda: False)
    monkeypatch.setattr(doctor.accel, "mps_available", lambda: False)
    _fake_torch(monkeypatch, cuda=None, device=False)
    hints.append(("gpu-runtime: Apple Silicon without MLX", doctor.check_gpu_runtime().fix_hint))

    assert [case for case, hint in hints if hint is None] == [], "a None hint becomes a null in --json"
    assert [case for case, hint in hints if not hint] == ["gpu-runtime: no NVIDIA hardware"], (
        f"a failing check owes the reader something to do; the one blank is the machine "
        f"that is behaving as designed: {hints}"
    )
    assert [case for case, hint in hints if re.search(r"\bpip\b", hint)] == []


def test_a_healthy_apple_silicon_leaves_an_empty_hint_and_never_a_null(monkeypatch):
    """The one green check that still fills in the hint field, and `--json`
    prints every field of every check - so "" and not None, because a null is a
    shape each reader has to special-case. Nothing on this machine can reach
    the branch, so only a test keeps it from drifting back."""
    monkeypatch.setattr(doctor.accel, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(doctor.accel, "mlx_available", lambda: True)
    monkeypatch.setattr(doctor.accel, "mps_available", lambda: True)
    _fake_torch(monkeypatch, cuda=None, device=False)

    check = doctor.check_gpu_runtime()

    assert check.ok is True
    assert check.fix_hint == ""


# --- the closing line says what a SKIP costs ----------------------------------------


def test_the_closing_line_says_what_each_skipped_check_costs():
    """"2 optional check(s) not wired yet" read as unfinished developer work.
    What it means is that speaker separation will not run on this machine."""
    results = [
        doctor.Check(name="python", ok=True, detail="3.12.7"),
        doctor.Check(name="diarization", ok=False, optional=True, detail="no token"),
        doctor.Check(name="models", ok=False, optional=True, detail="1.6 GB still to download"),
    ]

    printed = doctor.render(results)
    closing = printed.splitlines()[-1]

    assert closing.startswith("All required checks passed")
    assert "speaker separation not set up" in closing
    assert "weights not downloaded" in closing
    assert "not wired yet" not in printed


def test_a_machine_that_failed_a_check_is_still_told_why_one_was_skipped():
    """The explanation hung off the passing branch only, so a machine without
    ffmpeg learned nothing about diarization - the run where it matters most."""
    results = [
        doctor.Check(name="ffmpeg", ok=False, detail="not found", fix_hint="install it"),
        doctor.Check(name="diarization", ok=False, optional=True, detail="no token"),
    ]

    closing = doctor.render(results).splitlines()[-1]

    assert "1 required check(s) failed" in closing
    assert "speaker separation not set up" in closing


def test_a_skipped_check_with_no_entry_falls_back_to_its_name():
    """A check added tomorrow must not crash the closing line."""
    results = [doctor.Check(name="something-new", ok=False, optional=True, detail="absent")]

    assert "something-new" in doctor.render(results).splitlines()[-1]


def test_the_words_not_wired_yet_are_gone():
    source = Path(doctor.__file__).read_text(encoding="utf-8")

    assert "not wired yet" not in source


# --- a program can read the answer ---------------------------------------------------


def test_json_prints_one_object_per_check_and_nothing_else(tmp_path, monkeypatch, capsys):
    _tmp_library(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_diarization_token", lambda: None)

    code = doctor.main(["--no-gpu", "--json"])

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == 0
    names = [row["name"] for row in payload]
    assert "python" in names and "sqlite" in names and "ollama" in names
    for row in payload:
        assert set(row) == {"name", "ok", "optional", "detail", "fix_hint"}
        assert isinstance(row["ok"], bool) and isinstance(row["optional"], bool)
        assert isinstance(row["fix_hint"], str), "a null is a shape a reader has to special-case"


@pytest.mark.parametrize("argv", [["--no-gpu"], ["--no-gpu", "--json"]])
def test_the_exit_code_is_the_same_whichever_shape_is_printed(monkeypatch, capsys, argv):
    """--json is a second spelling of the same answer, not a second answer."""
    bad = doctor.Check(name="python", ok=False, detail="too old", fix_hint="install 3.12")
    monkeypatch.setattr(doctor, "checks", lambda include_gpu=True, on_start=None: [bad])

    assert doctor.main(argv) == 1

    passed = doctor.Check(name="python", ok=True, detail="3.12.7")
    skipped = doctor.Check(name="ollama", ok=False, optional=True, detail="not installed on this machine")
    monkeypatch.setattr(doctor, "checks", lambda include_gpu=True, on_start=None: [passed, skipped])
    capsys.readouterr()

    assert doctor.main(argv) == 0


# --- a cold run is not a blank terminal ---------------------------------------------


def test_each_check_is_named_as_it_starts_on_a_terminal(tmp_path, monkeypatch, capsys):
    """A cold gpu-smoke downloads weights and transcribes a clip; a terminal
    that prints nothing until the last check has finished reads as a hang."""
    _tmp_library(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_diarization_token", lambda: None)
    monkeypatch.setattr(doctor, "_stderr_is_a_terminal", lambda: True)

    code = doctor.main(["--no-gpu"])

    captured = capsys.readouterr()
    assert code == 0
    assert "checking python" in captured.err
    assert "checking ollama" in captured.err
    assert "checking python" not in captured.out, "progress is on stderr; stdout stays the report"
    assert captured.out.startswith("[OK  ] python")


def test_nothing_is_printed_when_stderr_is_not_a_terminal(tmp_path, monkeypatch, capsys):
    """A redirected run - CI, a log file - gets the report and no narration."""
    _tmp_library(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_diarization_token", lambda: None)

    doctor.main(["--no-gpu"])

    assert "checking " not in capsys.readouterr().err


def test_json_output_carries_no_progress_even_on_a_terminal(tmp_path, monkeypatch, capsys):
    """The one shape a program parses must stay parseable."""
    _tmp_library(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_diarization_token", lambda: None)
    monkeypatch.setattr(doctor, "_stderr_is_a_terminal", lambda: True)

    doctor.main(["--no-gpu", "--json"])

    captured = capsys.readouterr()
    assert "checking " not in captured.err
    json.loads(captured.out)


def test_every_registered_check_is_labelled_with_the_name_it_reports(tmp_path, monkeypatch):
    """The label is printed before the check runs, so it cannot come from the
    result. One that drifted would announce one name and report another."""
    _tmp_library(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor, "_diarization_token", lambda: None)

    assert set(doctor.CHECK_LABELS) == set(doctor.CPU_CHECKS + doctor.GPU_CHECKS)

    for fn in doctor.CPU_CHECKS:
        assert fn().name == doctor.CHECK_LABELS[fn]

    # After the loop, because `check_accelerators` imports the real torch on
    # its way to `accel.describe()`. The version is asserted so a fake that
    # failed to take is a red test rather than a test that quietly asked the
    # card in the machine.
    _fake_torch(monkeypatch, cuda="12.8", device=False)
    _hardware(monkeypatch, present=False)
    runtime = doctor.check_gpu_runtime()
    assert "2.10.0+cu128" in runtime.detail, "the real torch answered; this test touched the card"
    assert runtime.name == doctor.CHECK_LABELS[doctor.check_gpu_runtime]
    # The smoke without a card: gpu_smoke returns on a missing clip before the
    # transcribe stage is even imported.
    assert doctor.gpu_smoke(clip=tmp_path / "nope.wav").name == doctor.CHECK_LABELS[doctor.check_gpu_smoke]


# --- the weights this machine can actually load (TASK-089.16) ---------------------


def _not_apple_silicon(monkeypatch, tmp_path):
    from scribe import accel, models

    monkeypatch.setattr(accel, "mlx_available", lambda: False)
    monkeypatch.setattr(accel, "transcription_backend", lambda: "cuda")
    monkeypatch.setattr(models, "root", lambda: tmp_path / "models")
    return models


def test_a_windows_machine_is_never_asked_to_download_apple_weights(monkeypatch, tmp_path):
    """The headline of TASK-089.16, measured on Robert's machine on 2026-09-22:
    the card read "1.6 GB still to download: whisper-large-v3-turbo (1.6 GB)"
    while the gpu-smoke two lines below transcribed happily. The missing entry
    was the MLX conversion, which nothing on Windows can open, and the short
    name had stripped the `mlx-community/` that said so."""
    models = _not_apple_silicon(monkeypatch, tmp_path)
    monkeypatch.setattr(models, "hub_snapshot", lambda model: None)

    check = doctor.check_models()

    assert check.ok is False, "the weights really are absent here"
    assert "mlx" not in check.detail.lower(), check.detail
    named = check.detail.split(": ", 1)[1]
    assert len(named.split(", ")) == 2, f"one Whisper repository and the pipeline: {named}"


def test_weights_the_hub_cache_already_holds_are_present(monkeypatch, tmp_path):
    """The other half of the same run: the CT2 weights that transcribe on this
    machine were in the hub cache all along."""
    models = _not_apple_silicon(monkeypatch, tmp_path)
    wanted = {model.repo for model in models.wanted_here(backend="cuda")}
    monkeypatch.setattr(
        models, "hub_snapshot", lambda model: tmp_path / "hub" if model.repo in wanted else None
    )

    check = doctor.check_models()

    assert check.ok is True, check.detail
    assert check.detail == "2 model(s) present"


def test_401_and_403_are_not_the_same_answer_about_a_token(monkeypatch):
    """They shared one sentence - "the conditions are not accepted for this
    token" - and it sent somebody with an expired token to a conditions page
    they had already agreed to."""
    said = {}
    for code in (401, 403):
        def refuse(*_args, code=code, **_kwargs):
            raise urllib.error.HTTPError("https://huggingface.co/x", code, "no", {}, None)

        monkeypatch.setattr(doctor.urllib.request, "urlopen", refuse)
        reachable, why = doctor._gated_repo_reachable("pyannote/demo", "a-token")

        assert reachable is False
        said[code] = why

    assert said[401] != said[403]
    assert "401" in said[401] and "expired" in said[401]
    assert "403" in said[403] and "pyannote/demo" in said[403]
