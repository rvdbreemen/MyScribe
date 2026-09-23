"""TASK-092: no quiet CPU fallback behind an NVIDIA card that CUDA cannot reach.

Robert's decision of 2026-09-22: a machine with NVIDIA hardware never switches
to the CPU by itself. The job is refused with a sentence that names the driver,
and one switch in Settings, off by default, says yes. A machine with no NVIDIA
hardware keeps transcribing on the CPU without a word, and so does a machine
whose card somebody hid with CUDA_VISIBLE_DEVICES.

Every test here runs the real `accel` rule and, where a job is involved, the
real `transcribe.load_model`. Only four things are replaced: whether CUDA can
reach a device (`accel.cuda_available`), whether the machine has a card
(`accel.nvidia_hardware_present`, with a witness so a bypassed seam cannot go
green), the DLL registration, and faster-whisper's constructor, which records
what it was asked to build instead of building it. This machine has a working
RTX 3080 and a VEN_10DE key in its registry; neither may decide a verdict here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scribe import accel, cuda_setup, db, jobs, media, models, paths, runner
from scribe.stages import diarize, transcribe

CLIP = Path(__file__).parent / "fixtures" / "clip30.wav"
REPO = Path(__file__).resolve().parent.parent


# --- the machine, as accel sees it ---------------------------------------------------


def machine(monkeypatch, *, card: bool, reachable: bool = False, hidden: str | None = None):
    """Set what `accel` sees. Returns the probe's answers, as a witness.

    The variable is cleared unless a test hides the card on purpose: conftest
    leaves `os.environ` alone, and a shell that had CUDA_VISIBLE_DEVICES set
    would otherwise pass the refusal tests for the wrong reason.
    """
    asked: list[bool] = []

    def probe():
        asked.append(card)
        return card

    monkeypatch.setattr(accel, "cuda_available", lambda: reachable)
    monkeypatch.setattr(accel, "nvidia_hardware_present", probe)
    monkeypatch.setattr(accel, "mlx_available", lambda: False)
    monkeypatch.setattr(accel, "mps_available", lambda: False)
    # A machine with an NVIDIA card is not a Mac, whatever runs the test: the
    # macOS CI runner found every refusal test passing on the exemption.
    monkeypatch.setattr(accel, "_on_macos", lambda: False)
    if hidden is None:
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    else:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", hidden)
    return asked


def fake_segment(start, end, text=" hello there."):
    word = SimpleNamespace(start=start, end=end, word=text, probability=0.9)
    return SimpleNamespace(
        start=start, end=end, text=text, words=[word], avg_logprob=-0.3,
        no_speech_prob=0.02, compression_ratio=1.4, temperature=0.0,
    )


def recording_whisper(monkeypatch):
    """faster-whisper's constructor, recorded rather than run.

    Returns the list of (name, device) it was asked to build. An empty list
    after a job is the proof that no model was started.
    """
    import faster_whisper

    built: list[tuple[str, str]] = []

    class Model:
        def __init__(self, name_or_directory, *, device, compute_type, **_kw):
            built.append((str(name_or_directory), device))

        def transcribe(self, audio, **_options):
            info = SimpleNamespace(
                duration=30.0, language="nl", language_probability=0.98, duration_after_vad=28.0,
            )
            return iter([fake_segment(0.0, 2.0)]), info

    monkeypatch.setattr(cuda_setup, "ensure_cuda_libs", lambda: None)
    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    monkeypatch.setattr(models, "local_dir", lambda name, device: None)
    return built


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A scratch library that runner.main() finds through the default paths."""
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    (data / "media").mkdir(parents=True)
    conn = db.connect(data / "myscribe.db")
    db.migrate(conn)
    yield conn
    conn.close()


def run_a_transcription(conn, monkeypatch):
    """One transcribe job through the runner child's own entry point.

    The stages before and after transcribe need ffmpeg and weights and are not
    what is under test: prepare is replaced by the fixture clip, and the job
    ends after transcribe. `transcribe.run` and `load_model` are the real ones.
    """
    monkeypatch.setitem(
        runner.STAGES,
        "transcribe",
        [
            ("prepare", lambda ctx: ctx.state.update(wav=CLIP)),
            ("transcribe", transcribe.run),
        ],
    )
    row = media.ingest_path(conn, CLIP, title="clip")
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"], params={})
    jobs.claim_next(conn)
    code = runner.main([str(job_id)])
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    runs = [dict(r) for r in conn.execute("SELECT * FROM run").fetchall()]
    return code, job, runs


def switch(conn, value: str | None):
    """Write the switch's row as Settings would, or leave it out."""
    conn.execute("DELETE FROM setting WHERE key=?", (accel.SETTING_CPU_FALLBACK,))
    if value is not None:
        conn.execute(
            "INSERT INTO setting(key, value) VALUES (?, ?)", (accel.SETTING_CPU_FALLBACK, value)
        )
    conn.commit()


# --- criterion 1: refused, with a sentence, and no model -------------------------------


def test_a_card_that_cuda_cannot_reach_refuses_the_job_instead_of_running_on_the_cpu(
    library, monkeypatch
):
    asked = machine(monkeypatch, card=True, reachable=False)
    built = recording_whisper(monkeypatch)

    code, job, runs = run_a_transcription(library, monkeypatch)

    assert (code, job["status"], job["error_code"]) == (1, "failed", "GPU_UNREACHABLE"), (
        f"exit {code}, job {job['status']} {job['error_code']} {job['error_detail']!r}, "
        f"models built {built}, runs {[json.loads(r['params_json'])['device'] for r in runs]}"
    )
    assert built == [], "the refusal comes before any model is started"
    assert runs == [], "nothing half-written: no run row"
    assert asked == [True], "refused because the seam said there is a card"

    detail = job["error_detail"]
    assert "driver" in detail and "nvidia-smi" in detail
    assert accel.CPU_FALLBACK_LABEL in detail, "the sentence names the switch"
    from scribe.web import settings

    machine_label = dict((key, label) for key, label, _ in settings.SECTIONS)["machine"]
    assert f"Settings > {machine_label}" in detail, "and says where it is"


# --- criterion 2: no card, nothing changes ------------------------------------------------


@pytest.mark.parametrize("card", [False, True], ids=["no-nvidia-hardware", "card-behind-broken-driver"])
def test_the_same_seam_runs_a_cardless_machine_on_the_cpu_and_refuses_a_broken_card(
    library, monkeypatch, card
):
    """Both directions off one seam, so a change that refuses everything, or
    nothing, cannot pass."""
    asked = machine(monkeypatch, card=card, reachable=False)
    built = recording_whisper(monkeypatch)

    code, job, runs = run_a_transcription(library, monkeypatch)

    assert asked == [card]
    if card:
        assert (code, job["status"], job["error_code"]) == (1, "failed", "GPU_UNREACHABLE")
        assert built == [] and runs == []
    else:
        assert (code, job["status"], job["error_code"], job["error_detail"]) == (0, "done", None, None)
        assert built == [(transcribe.DEFAULT_MODEL, "cpu")]
        assert json.loads(runs[0]["params_json"])["device"] == "cpu"


def test_a_cardless_machine_is_told_nothing_extra_by_accel(monkeypatch):
    machine(monkeypatch, card=False, reachable=False)

    assert accel.transcription_backend() == "cpu"
    assert accel.diarization_device() == "cpu"
    assert accel.describe() == "transcription on cpu, diarization on cpu"


def test_a_mac_is_not_refused_whatever_the_probe_says(monkeypatch):
    """macOS has no CUDA and the probe answers True for an OS it does not know;
    the rule must not read that as a broken card. Mac behaviour is unchanged,
    and the mps question stays open (task text)."""
    asked = machine(monkeypatch, card=True, reachable=False)
    monkeypatch.setattr(accel, "_on_macos", lambda: True)

    assert accel.transcription_backend() == "cpu"
    assert accel.diarization_device() == "cpu"
    assert asked == [], "on macOS the probe is not asked at all"


# --- criterion 3: the switch --------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "refused"),
    [(None, True), ("0", True), ("yes", True), ("1", False)],
    ids=["no-row", "off", "anything-else", "on"],
)
def test_only_the_switch_turned_on_lets_a_job_run_on_the_cpu(library, monkeypatch, row, refused):
    """A missing row means off: no row, no fallback (ADR-016's shape). Exactly
    the value the form writes is on; anything else is off."""
    machine(monkeypatch, card=True, reachable=False)
    built = recording_whisper(monkeypatch)
    switch(library, row)

    code, job, runs = run_a_transcription(library, monkeypatch)

    if refused:
        assert (code, job["status"], job["error_code"]) == (1, "failed", "GPU_UNREACHABLE")
        assert built == [] and runs == []
    else:
        assert (code, job["status"]) == (0, "done"), job["error_detail"]
        assert built == [(transcribe.DEFAULT_MODEL, "cpu")]
        # The run says which backend made it, so a CPU transcript can be told
        # apart afterwards; the job's event says the same.
        assert json.loads(runs[0]["params_json"])["device"] == "cpu"
        event = library.execute(
            "SELECT payload_json FROM job_event WHERE job_id=? AND kind='transcribe'", (job["id"],)
        ).fetchone()
        assert json.loads(event["payload_json"])["device"] == "cpu"


def test_the_switch_reads_off_without_a_row(library):
    assert accel.cpu_fallback_allowed(library) is False
    switch(library, accel.CPU_FALLBACK_ON)
    assert accel.cpu_fallback_allowed(library) is True
    switch(library, "0")
    assert accel.cpu_fallback_allowed(library) is False


@pytest.fixture
def client(library):
    from scribe.app import create_app

    app = create_app(db_path=paths.DB_PATH, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_settings_shows_the_switch_off_by_default_and_turns_it_on_and_off(client, library):
    page = client.get("/settings?section=machine")
    assert page.status_code == 200
    assert accel.CPU_FALLBACK_LABEL in page.text
    assert 'id="cpu-fallback"' in page.text
    assert "Off" in page.text.split('id="cpu-fallback"')[1].split("</section>")[0]

    on = client.post("/settings/cpu-fallback", data={"enabled": "1"}, follow_redirects=False)
    assert on.status_code == 303
    assert on.headers["location"].startswith("/settings?section=machine")
    rows = library.execute("SELECT value FROM setting WHERE key=?", (accel.SETTING_CPU_FALLBACK,)).fetchall()
    assert [r["value"] for r in rows] == [accel.CPU_FALLBACK_ON], "written as its own row"

    off = client.post(
        "/settings/cpu-fallback", data={"enabled": "0"}, headers={"HX-Request": "true"}
    )
    assert off.status_code == 200
    assert 'id="cpu-fallback"' in off.text
    rows = library.execute("SELECT value FROM setting WHERE key=?", (accel.SETTING_CPU_FALLBACK,)).fetchall()
    assert rows == [], "off removes the row, so off and never-asked are one state"


# --- criterion 4: the speaker pass follows the same rule -----------------------------------


def test_the_speaker_pass_is_refused_behind_an_unreachable_card_unless_the_switch_is_on(
    library, monkeypatch
):
    machine(monkeypatch, card=True, reachable=False)
    asked_device: list[str | None] = []

    def fake_diarize(wav, **kw):
        asked_device.append(kw.get("device"))
        return [], []

    monkeypatch.setattr(diarize, "diarize", fake_diarize)
    monkeypatch.setattr(diarize, "_persist", lambda ctx, run_id, embeddings: None)
    monkeypatch.setattr(diarize, "_note_run", lambda *a, **kw: None)
    job_id = jobs.enqueue(library, "transcribe")
    ctx = runner.RunnerContext(
        conn=library, job=dict(library.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()),
        params={}, report=lambda p: None, cancelled=lambda: False,
    )
    ctx.state.update(wav=CLIP, run_id=1)

    with pytest.raises(accel.GpuUnreachable):
        diarize.run(ctx)
    assert asked_device == [], "refused before the pipeline is asked for"

    with pytest.raises(accel.GpuUnreachable):
        accel.diarization_device()

    switch(library, accel.CPU_FALLBACK_ON)
    diarize.run(ctx)
    assert asked_device == ["cpu"]


# --- criterion 5: the runtime does not import the doctor -----------------------------------


def test_the_stages_and_accel_do_not_import_the_doctor():
    """The probe lives in accel now. runner.py still imports scribe.doctor for
    the doctor job type and two exception classes; what is pinned is that the
    transcription path asks accel and never needs the doctor to do it."""
    script = (
        "import sys; import scribe.accel, scribe.stages.transcribe, scribe.stages.diarize; "
        "print('scribe.doctor' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


# --- criterion 6: a hidden card is somebody's choice ---------------------------------------


@pytest.mark.parametrize("value", ["-1", ""])
def test_hiding_the_card_with_cuda_visible_devices_runs_on_the_cpu_without_the_switch(
    library, monkeypatch, value
):
    machine(monkeypatch, card=True, reachable=False, hidden=value)
    built = recording_whisper(monkeypatch)

    code, job, runs = run_a_transcription(library, monkeypatch)

    assert (code, job["status"]) == (0, "done"), job["error_detail"]
    assert built == [(transcribe.DEFAULT_MODEL, "cpu")]
    assert accel.diarization_device() == "cpu"


# --- criterion 7: a failed job, not a crash ------------------------------------------------


def test_the_refusal_is_exit_one_and_the_board_shows_its_sentence(library, monkeypatch, client):
    """Exit 1 is the runner's "failed" (0 done, 1 failed, 2 cancelled)."""
    machine(monkeypatch, card=True, reachable=False)
    recording_whisper(monkeypatch)

    code, job, _runs = run_a_transcription(library, monkeypatch)

    assert code == 1 and job["status"] == "failed" and job["finished_at"] is not None
    board = client.get("/jobs")
    assert "GPU_UNREACHABLE" in board.text
    assert "nvidia-smi" in board.text
    detail = client.get(f"/jobs/{job['id']}")
    assert accel.CPU_FALLBACK_LABEL in detail.text


def test_the_error_code_is_its_own_entry_not_the_runtime_catch_all():
    assert runner._error_code(accel.GpuUnreachable("x")) == "GPU_UNREACHABLE"


# --- the doctor says the refusal instead of dying of it ------------------------------------


def test_describe_says_the_refusal_in_words(monkeypatch):
    """The doctor's accel line runs under --no-gpu too; behind a broken driver
    it must print, not raise."""
    machine(monkeypatch, card=True, reachable=False)

    line = accel.describe()

    assert "refused" in line and "cannot reach" in line


def test_the_smoke_gives_the_refusal_its_own_hint(monkeypatch):
    from scribe import doctor

    machine(monkeypatch, card=True, reachable=False)
    recording_whisper(monkeypatch)

    check = doctor.gpu_smoke()

    assert check.ok is False
    assert "GpuUnreachable" in check.detail
    assert "cudnn" not in check.fix_hint.lower()
    assert "nvidia-smi" in check.fix_hint
