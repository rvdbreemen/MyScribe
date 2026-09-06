"""Phase 3 Task 8: the settings page and the on-demand doctor.

Everything the page shows is either a row in `setting`, a directory on this
machine, or the result of a CPU check that runs when the page is asked for;
the GPU checks are a job the runner child does, and the page shows the last
result that job stored. No GPU, no models, no pipeline: the checks that would
load a model are replaced by fakes through `doctor.GPU_CHECKS`, the cache
directory is a tmp_path with a few fake `models--*` folders in it, and the
runner runs the doctor job in-process the way tests/test_runner.py runs the
fake one.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe import db, doctor, fsbrowse, jobs, paths, runner
from scribe.app import create_app
from scribe.web import settings as settings_ui


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    return data


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


@pytest.fixture
def client(db_path, data_dir):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def fake_checks(monkeypatch):
    """Two scripted CPU checks in place of the real ones: one green, one red
    with a fix hint. The real checks run ffmpeg and touch the disk; one test
    below keeps them, the rest do not need to wait for them."""
    results = [
        doctor.Check(name="python", ok=True, detail="3.12.4 at C:\\py\\python.exe"),
        doctor.Check(
            name="ffmpeg", ok=False, detail="not found",
            fix_hint="Install ffmpeg and put it on PATH (winget install Gyan.FFmpeg).",
        ),
    ]
    monkeypatch.setattr(doctor, "checks", lambda include_gpu=True: list(results))
    return results


@pytest.fixture
def hf_cache(tmp_path, monkeypatch):
    """A Hugging Face hub cache with two models this app uses, one it does
    not, and a repo somebody else left there."""
    cache = tmp_path / "hub"
    layouts = {
        "models--Systran--faster-whisper-tiny": {"blobs/aa": 100, "blobs/bb": 150, "snapshots/abc/config.json": 10},
        "models--pyannote--segmentation-3.0": {"blobs/cc": 1000, "snapshots/def/pytorch_model.bin": 5},
        "models--openai--whisper-large-v3": {"blobs/dd": 7},
        "models--BAAI--bge-reranker-v2-m3": {"blobs/ee": 9},
    }
    for repo, files in layouts.items():
        for rel, size in files.items():
            path = cache / repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
    (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: cache)
    return cache


HX = {"HX-Request": "true"}


def _setting(conn, key):
    row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


def _set(conn, key, value):
    with db.LOCK:
        conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def _jobs(conn):
    return conn.execute("SELECT * FROM job ORDER BY id").fetchall()


def _section(body: str, element_id: str) -> str:
    start = body.index(f'id="{element_id}"')
    end = body.find("<section", start + 1)
    return body[start:] if end == -1 else body[start:end]


# --- the page ------------------------------------------------------------------------


def test_settings_page_lists_the_seeded_default_language(client, conn, fake_checks):
    _set(conn, "default_language", "en")
    _set(conn, "default_tier", "max")
    _set(conn, "default_diarize", "0")

    resp = client.get("/settings")

    assert resp.status_code == 200
    body = resp.text
    assert "<html" in body
    assert 'href="/settings" aria-current="page"' in body
    assert '<option value="en" selected>English</option>' in body
    assert '<option value="nl" selected>' not in body
    assert 'name="tier" value="max" checked' in body
    assert 'name="tier" value="turbo" checked' not in body
    assert 'name="diarize" value="1" checked' not in body
    assert 'action="/settings"' in body and 'hx-post="/settings"' in body


def test_settings_page_defaults_to_auto_detect_turbo_and_speakers_when_nothing_is_stored(client, fake_checks):
    body = client.get("/settings").text

    # Auto-detect, not Dutch: a fixed default only ever mislabelled English
    # recordings, and detection gets Dutch right (see transcribe_dialog).
    assert '<option value="" selected>Auto-detect</option>' in body
    assert '<option value="nl">Dutch</option>' in body
    assert 'name="tier" value="turbo" checked' in body
    assert 'name="diarize" value="1" checked' in body


def test_settings_page_runs_the_cpu_checks_live(client):
    """The real checks, once: python, sqlite, ffmpeg and friends appear as
    rows with what they found."""
    body = client.get("/settings").text

    table = body[body.index('<table class="checks"'):]
    for name in ("python", "sqlite", "ffmpeg", "ffprobe", "data-dir", "disk-space", "database"):
        assert f'data-check="{name}"' in table
    assert "FTS5" in table


def test_settings_page_shows_a_failed_check_with_its_fix_hint(client, fake_checks):
    body = client.get("/settings").text

    row = re.search(r'<tr[^>]*data-check="ffmpeg"[^>]*>.*?</tr>', body, re.DOTALL)
    assert row, "no row for the ffmpeg check"
    assert 'class="badge fail"' in row.group(0)
    assert "not found" in row.group(0)
    assert "winget install Gyan.FFmpeg" in row.group(0)
    ok = re.search(r'<tr[^>]*data-check="python"[^>]*>.*?</tr>', body, re.DOTALL)
    assert ok and 'class="badge ok"' in ok.group(0)


def test_settings_page_says_the_gpu_checks_never_ran_until_a_result_is_stored(client, conn, fake_checks):
    body = client.get("/settings").text
    gpu = _section(body, "gpu-checks")
    assert "never" in gpu.lower()
    assert 'name="gpu" value="1"' in gpu  # the button that queues the job

    stored = [
        doctor.Check(name="gpu-runtime", ok=True, detail="torch 2.8.0 CUDA 12.8 on RTX 3080 (16.0 GB)"),
        doctor.Check(name="gpu-smoke", ok=False, detail="cudnn_ops64_9.dll missing", fix_hint="Reinstall the GPU stack."),
    ]
    doctor.store_last_run(conn, stored, job_id=7)

    gpu = _section(client.get("/settings").text, "gpu-checks")
    assert "RTX 3080" in gpu
    assert "cudnn_ops64_9.dll missing" in gpu
    assert "Reinstall the GPU stack." in gpu
    assert 'href="/jobs/7"' in gpu
    assert "never" not in gpu.lower()


def test_settings_page_lists_the_models_in_the_hf_cache(client, fake_checks, hf_cache):
    body = client.get("/settings").text

    models = _section(body, "models")
    assert "Systran/faster-whisper-tiny" in models
    assert "pyannote/segmentation-3.0" in models
    assert "openai/whisper-large-v3" not in models
    assert "BAAI" not in models
    assert str(hf_cache) in models
    assert "260 B" in models  # 100 + 150 + 10
    assert "1.0 kB" in models


def test_installed_models_reads_names_sizes_and_the_local_pyannote_dir(hf_cache, data_dir):
    local = data_dir / "models" / "pyannote"
    local.mkdir(parents=True)
    (local / "config.yaml").write_bytes(b"pipeline: yes\n")  # bytes: no newline translation
    (local / "model.bin").write_bytes(b"y" * 42)

    found = doctor.installed_models()

    by_name = {m.name: m for m in found}
    assert set(by_name) == {"Systran/faster-whisper-tiny", "pyannote/segmentation-3.0", "pyannote (local)"}
    assert by_name["Systran/faster-whisper-tiny"].size_bytes == 260
    assert by_name["Systran/faster-whisper-tiny"].source == "hub"
    assert by_name["pyannote/segmentation-3.0"].size_bytes == 1005
    assert by_name["pyannote (local)"].source == "local"
    assert by_name["pyannote (local)"].size_bytes == 42 + len("pipeline: yes\n")
    assert by_name["pyannote (local)"].path == local


def test_installed_models_without_a_cache_is_empty(tmp_path, monkeypatch, data_dir):
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "nope")

    assert doctor.installed_models() == []


def test_hf_cache_dir_honours_the_hub_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    assert doctor.hf_cache_dir() == Path.home() / ".cache" / "huggingface" / "hub"

    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    assert doctor.hf_cache_dir() == tmp_path / "home" / "hub"

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "cache"))
    assert doctor.hf_cache_dir() == tmp_path / "cache"


def test_settings_page_shows_the_media_store_usage(client, fake_checks, data_dir):
    store = data_dir / "media"
    (store / "ab").mkdir(parents=True)
    (store / "ab" / "abcd.wav").write_bytes(b"a" * 1500)
    (store / "ab" / "abce.m4a").write_bytes(b"b" * 500)
    (store / ".incoming").mkdir()
    (store / ".incoming" / "tmp").write_bytes(b"c" * 10)

    body = client.get("/settings").text

    storage = _section(body, "storage")
    assert str(store) in storage
    assert "3 files" in storage
    assert "2.0 kB" in storage
    assert "free" in storage


def test_store_usage_sums_files_and_reports_free_space(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 10)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"y" * 20)

    usage = settings_ui.store_usage(tmp_path)

    assert (usage["files"], usage["bytes"]) == (2, 30)
    assert usage["free"] > 0 and usage["total"] >= usage["free"]

    missing = settings_ui.store_usage(tmp_path / "nope")
    assert (missing["files"], missing["bytes"]) == (0, 0)


def test_settings_page_shows_the_browse_roots(client, conn, fake_checks, tmp_path):
    body = client.get("/settings").text
    folders = _section(body, "folders")
    assert 'name="fsbrowse_roots"' in folders
    for root in fsbrowse.ALLOWED_ROOTS:
        assert str(root) in folders

    _set(conn, "fsbrowse_roots", str(tmp_path))
    folders = _section(client.get("/settings").text, "folders")
    assert str(tmp_path) in folders


# --- saving the defaults ---------------------------------------------------------------


def test_saving_defaults_persists_to_setting_and_the_dialog_picks_them_up(client, conn, fake_checks):
    resp = client.post(
        "/settings",
        data={"language": "en", "tier": "max", "diarize": "0"},
        headers=HX,
    )

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    assert 'id="settings-defaults"' in body
    assert 'id="flash"' in body and 'hx-swap-oob="innerHTML:#flash"' in body
    assert '<option value="en" selected>English</option>' in body
    assert _setting(conn, "default_language") == "en"
    assert _setting(conn, "default_tier") == "max"
    assert _setting(conn, "default_diarize") == "0"

    dialog = client.get("/transcribe", headers=HX).text
    assert '<option value="en" selected>English</option>' in dialog
    assert 'name="tier" value="max" checked' in dialog
    assert 'name="diarize" value="1" checked' not in dialog


def test_saving_auto_detect_stores_an_empty_language(client, conn, fake_checks):
    client.post("/settings", data={"language": "", "tier": "turbo", "diarize": "1"}, headers=HX)

    assert _setting(conn, "default_language") == ""
    assert '<option value="" selected>Auto-detect</option>' in client.get("/transcribe", headers=HX).text
    assert "selected" not in re.search(r'<option value="nl"[^>]*>', client.get("/settings").text).group(0)


def test_a_plain_save_redirects_back_to_the_settings_page(client, conn, fake_checks):
    resp = client.post("/settings", data={"language": "de", "tier": "turbo"}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"
    assert _setting(conn, "default_language") == "de"


def test_saving_bad_defaults_is_a_400_and_stores_nothing(client, conn, fake_checks):
    assert client.post("/settings", data={"tier": "cheetah"}, headers=HX).status_code == 400
    assert client.post("/settings", data={"language": "xx"}, headers=HX).status_code == 400

    assert _setting(conn, "default_tier") is None
    assert _setting(conn, "default_language") is None


def test_saving_browse_roots_widens_them_and_a_blank_restores_the_default(client, conn, fake_checks, tmp_path):
    inside = tmp_path / "recordings"
    inside.mkdir()
    other = tmp_path / "more"
    other.mkdir()

    resp = client.post(
        "/settings",
        data={"tier": "turbo", "fsbrowse_roots": f"{inside}\n\n  {other}  \n"},
        headers=HX,
    )

    assert resp.status_code == 200
    assert _setting(conn, "fsbrowse_roots") == f"{inside}\n{other}"
    assert fsbrowse.allowed_roots(conn) == (inside, other)
    assert str(inside) in resp.text and str(other) in resp.text

    client.post("/settings", data={"tier": "turbo", "fsbrowse_roots": "   \n"}, headers=HX)

    assert _setting(conn, "fsbrowse_roots") is None
    assert fsbrowse.allowed_roots(conn) == fsbrowse.ALLOWED_ROOTS


def test_browse_roots_must_be_absolute_existing_directories(client, conn, fake_checks, tmp_path):
    (tmp_path / "file.wav").write_bytes(b"RIFF")
    bad = [
        "recordings",  # relative
        str(tmp_path / "nope"),  # missing
        str(tmp_path / "file.wav"),  # a file
    ]
    for line in bad:
        resp = client.post("/settings", data={"tier": "turbo", "fsbrowse_roots": line}, headers=HX)
        assert resp.status_code == 400, line
        assert line in resp.json()["detail"]
    assert _setting(conn, "fsbrowse_roots") is None


# --- the doctor ----------------------------------------------------------------------


def test_doctor_post_returns_the_cpu_table(client, fake_checks):
    resp = client.post("/settings/doctor", headers=HX)

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    assert 'id="doctor-panel"' in body
    assert '<table class="checks"' in body
    assert 'data-check="python"' in body and 'data-check="ffmpeg"' in body
    assert "winget install Gyan.FFmpeg" in body


def test_a_plain_doctor_post_redirects_to_the_settings_page(client, fake_checks):
    resp = client.post("/settings/doctor", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings"


def test_doctor_post_with_gpu_queues_one_doctor_job(client, conn, fake_checks):
    resp = client.post("/settings/doctor", data={"gpu": "1"}, headers=HX)

    assert resp.status_code == 200
    (job,) = _jobs(conn)
    assert job["type"] == "doctor"
    assert job["status"] == "queued"
    assert job["media_id"] is None
    assert json.loads(job["params_json"]) == {}
    assert f'href="/jobs/{job["id"]}"' in resp.text
    assert resp.headers["HX-Trigger"] == "jobs-changed"

    # Asking again while that one is still waiting does not queue a second.
    again = client.post("/settings/doctor", data={"gpu": "1"}, headers=HX)
    assert again.status_code == 200
    assert len(_jobs(conn)) == 1
    assert f'href="/jobs/{job["id"]}"' in again.text

    # Once it is over, a new request queues a new job.
    jobs.finish(conn, job["id"], "done")
    client.post("/settings/doctor", data={"gpu": "1"}, headers=HX)
    assert [j["status"] for j in _jobs(conn)] == ["done", "queued"]


def test_settings_page_points_at_a_doctor_job_that_is_still_running(client, conn, fake_checks):
    job_id = jobs.enqueue(conn, "doctor")

    gpu = _section(client.get("/settings").text, "gpu-checks")

    assert f'href="/jobs/{job_id}"' in gpu
    assert "queued" in gpu


# --- the doctor job ----------------------------------------------------------------


@pytest.fixture
def runner_db(db_path, data_dir, monkeypatch):
    """runner.main() opens paths.DB_PATH; point it at the test database.

    Depends on data_dir so it runs after it: data_dir points DB_PATH at a
    database that does not exist, and the last patch wins."""
    monkeypatch.setattr(paths, "DB_PATH", db_path)
    return db_path


def test_the_doctor_job_type_is_registered_with_one_gpu_stage():
    assert "doctor" in runner.STAGES
    assert [name for name, _ in runner.STAGES["doctor"]] == ["gpu-checks"]
    assert runner.STAGES["doctor"] is doctor.DOCTOR_STAGES
    assert doctor.JOB_TYPE == "doctor"


def test_a_fake_run_of_the_doctor_job_stores_the_result_in_setting(conn, runner_db, data_dir, monkeypatch):
    results = [
        doctor.Check(name="gpu-runtime", ok=True, detail="torch 2.8.0 CUDA 12.8 on RTX 3080 (16.0 GB)"),
        doctor.Check(name="gpu-smoke", ok=True, detail="large-v3-turbo: 61 words from 30s in 3.1s"),
    ]
    monkeypatch.setattr(doctor, "GPU_CHECKS", tuple((lambda r=r: r) for r in results))
    job_id = jobs.enqueue(conn, "doctor")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert row["stage"] == "gpu-checks"

    stored = json.loads(_setting(conn, "doctor_last"))
    assert stored["job_id"] == job_id
    assert stored["ts"] > 0
    assert [c["name"] for c in stored["checks"]] == ["gpu-runtime", "gpu-smoke"]
    assert all(c["ok"] for c in stored["checks"])
    assert "RTX 3080" in stored["checks"][0]["detail"]

    last = doctor.last_run(conn)
    assert last["job_id"] == job_id
    assert [c.name for c in last["checks"]] == ["gpu-runtime", "gpu-smoke"]
    assert isinstance(last["checks"][0], doctor.Check)


def test_a_failed_gpu_check_fails_the_doctor_job_but_keeps_the_result(conn, runner_db, data_dir, monkeypatch):
    results = [
        doctor.Check(name="gpu-runtime", ok=True, detail="torch 2.8.0 CUDA 12.8 on RTX 3080 (16.0 GB)"),
        doctor.Check(name="gpu-smoke", ok=False, detail="cudnn_ops64_9.dll missing", fix_hint="Reinstall the GPU stack."),
    ]
    monkeypatch.setattr(doctor, "GPU_CHECKS", tuple((lambda r=r: r) for r in results))
    job_id = jobs.enqueue(conn, "doctor")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error_code"] == "CHECK_FAILED"
    assert "gpu-smoke" in row["error_detail"]
    assert "cudnn_ops64_9.dll missing" in row["error_detail"]

    stored = json.loads(_setting(conn, "doctor_last"))
    assert [c["ok"] for c in stored["checks"]] == [True, False]


def test_the_stored_run_shows_up_on_the_settings_page(client, conn, runner_db, fake_checks, monkeypatch):
    monkeypatch.setattr(
        doctor, "GPU_CHECKS",
        (lambda: doctor.Check(name="gpu-smoke", ok=True, detail="large-v3-turbo: 61 words from 30s in 3.1s"),),
    )
    job_id = jobs.enqueue(conn, "doctor")
    jobs.claim_next(conn)
    assert runner.main([str(job_id)]) == 0

    gpu = _section(client.get("/settings").text, "gpu-checks")

    assert "61 words" in gpu
    assert f'href="/jobs/{job_id}"' in gpu
    assert 'class="badge ok"' in gpu


def test_last_run_tolerates_a_setting_edited_into_nonsense(conn):
    assert doctor.last_run(conn) is None
    _set(conn, "doctor_last", "not json")
    assert doctor.last_run(conn) is None
    _set(conn, "doctor_last", json.dumps({"checks": "nope"}))
    assert doctor.last_run(conn) is None


def test_the_settings_router_never_names_a_model_runtime():
    """ADR-001's grep: nothing under scribe/web/ mentions the speech or tensor
    runtimes, not even as a glob for the cache scan - that lives in doctor."""
    source = open(settings_ui.__file__, encoding="utf-8").read()
    for word in ("faster_whisper", "ctranslate2", "import torch", "pyannote"):
        assert word not in source, word
    assert os.path.basename(settings_ui.__file__) == "settings.py"


MODEL_RUNTIMES = ("torch", "torchaudio", "faster_whisper", "ctranslate2", "pyannote")


def test_the_web_process_never_imports_a_model_runtime(tmp_path):
    """ADR-001, checked where it counts rather than by grepping one file:
    the web process imports every stage module (jobs_ui -> scribe.stages,
    settings -> doctor -> stages.diarize), so a top-level `import torch`
    added to any stage would ship into the web process without the grep
    noticing. Build the app and serve its pages in a fresh interpreter -
    the e2e tests load faster-whisper into this one on purpose - and none
    of the runtimes may be in sys.modules afterwards."""
    script = textwrap.dedent(
        f"""
        import json, sys
        from fastapi.testclient import TestClient
        from scribe.app import create_app
        app = create_app(db_path={str(tmp_path / "web.db")!r}, start_supervisor=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            for path in ("/", "/jobs", "/transcribe", "/settings"):
                assert client.get(path).status_code == 200, path
        runtimes = {MODEL_RUNTIMES!r}
        print(json.dumps(sorted(m for m in sys.modules if m.split(".")[0] in runtimes)))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env={**os.environ, "SCRIBE_DATA_DIR": str(tmp_path / "data")},
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []


# --- the sidebar of categories --------------------------------------------------------


def test_the_settings_page_is_a_sidebar_of_categories_with_one_card_open(client):
    """It was one long scroll. Six categories now, one shown at a time; the
    switch is a radio group outside every self-refreshing partial, so a
    Save on the fourth card cannot put the page back on the first."""
    from scribe.web import settings as settings_ui

    body = client.get("/settings").text

    for key, label, hint in settings_ui.SECTIONS:
        assert f'id="ss-{key}" value="{key}"' in body, key
        assert f'for="ss-{key}"' in body and label in body and hint in body, key
        assert f'data-settings-section="{key}"' in body, key
    assert re.search(r'id="ss-defaults"[^>]*\schecked', body)
    assert len(re.findall(r'name="settings_section"[^>]*\schecked', body)) == 1
    # The radios are the page's, not any partial's.
    from scribe import web

    for partial in ("_settings_defaults", "_settings_llm", "_settings_watch", "_glossary", "_settings_presets", "_doctor_panel"):
        assert "settings_section" not in (web.TEMPLATES_DIR / f"{partial}.html").read_text(encoding="utf-8"), partial


def test_a_category_is_reachable_by_url_and_an_unknown_one_falls_back(client):
    from scribe.web import settings as settings_ui

    llm = client.get("/settings?section=llm").text
    assert re.search(r'id="ss-llm"[^>]*\schecked', llm)
    assert not re.search(r'id="ss-defaults"[^>]*\schecked', llm)

    odd = client.get("/settings?section=telepathy").text
    assert re.search(r'id="ss-defaults"[^>]*\schecked', odd)

    class _Req:
        query_params = {"section": "MACHINE"}

    assert settings_ui.opening_section(_Req()) == "machine"  # type: ignore[arg-type]


def test_every_form_that_persists_has_a_button_and_nothing_posts_on_change(client):
    """A setting that saves as you type is a setting you cannot back out of."""
    body = client.get("/settings").text

    forms = re.findall(r"<form\b[^>]*>(.*?)</form>", body, re.S)
    assert len(forms) >= 6
    for form in forms:
        assert re.search(r'<button[^>]*type="submit"', form), form[:120]
    assert 'hx-trigger="change' not in body
    assert "hx-trigger=\"keyup" not in body


def test_the_partials_refresh_inside_their_card_without_touching_the_radios(client, conn):
    """A Save posts and the partial comes back alone - no sidebar, no radios -
    so what the page swaps is the section and only the section."""
    resp = client.post("/settings", data={"language": "nl", "tier": "turbo", "diarize": "0"}, headers=HX)

    assert resp.status_code == 200
    assert 'id="settings-defaults"' in resp.text
    assert "settings_section" not in resp.text and "settings-nav" not in resp.text


# --- "Saved" on the button that saved -------------------------------------------------


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="needs node")
def test_the_pressed_save_button_turns_green_and_says_saved_after_the_swap(tmp_path):
    """htmx replaces the whole card on save, so the button a person pressed is
    gone by the time the answer arrives. app.js remembers the press and marks
    its successor - same form action, same label - in the new card."""
    from test_web_url_dialog import run_dom

    result = run_dom(
        tmp_path,
        r"""
    load(APP);
    function card(label) {
      const wrap = el('div', { class: 'settings-section' });
      const section = wrap.append(el('section', { id: 'settings-defaults' }));
      const form = section.append(el('form', { method: 'post', action: '/settings' }));
      form.append(el('button', { type: 'submit', class: 'primary' })).textContent = label;
      return { wrap: wrap, form: form, button: form.querySelector('button') };
    }
    const first = body.append(card('Save'));
    fire(document, 'click', event({ target: first.button }));

    /* The POST answered and htmx swapped the card for a fresh copy. */
    const fresh = card('Save');
    first.wrap.remove();
    body.append(fresh.wrap);
    fire(document.body, 'htmx:afterSwap', event({
      detail: { target: fresh.wrap.querySelector('section'), requestConfig: { verb: 'post' }, xhr: { status: 200 } }
    }));
    const marked = { text: fresh.button.textContent, saved: fresh.button.classList.contains('saved'), live: fresh.button.getAttribute('aria-live') };

    /* A failed save marks nothing. */
    const other = card('Save');
    fire(document, 'click', event({ target: other.button }));
    fire(document.body, 'htmx:afterSwap', event({
      detail: { target: other.wrap.querySelector('section'), requestConfig: { verb: 'post' }, xhr: { status: 400 } }
    }));
    done({ marked: marked, afterFailure: other.button.classList.contains('saved') });
""",
    )

    assert result["marked"] == {"text": "Saved \u2713", "saved": True, "live": "polite"}
    assert result["afterFailure"] is False


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="needs node")
def test_a_refused_save_marks_the_pressed_button_red_and_not_saved(tmp_path):
    """On a 4xx htmx swaps nothing, so the button that was pressed is still on
    the page; it turns red and says so, assertively."""
    from test_web_url_dialog import run_dom

    result = run_dom(
        tmp_path,
        r"""
    load(APP);
    const wrap = body.append(el('div', { class: 'settings-section' }));
    const section = wrap.append(el('section', { id: 'settings-defaults' }));
    const form = section.append(el('form', { method: 'post', action: '/settings' }));
    const button = form.append(el('button', { type: 'submit', class: 'primary' }));
    button.textContent = 'Save';
    fire(document, 'click', event({ target: button }));
    fire(document.body, 'htmx:responseError', event({
      detail: { elt: form, requestConfig: { verb: 'post' }, xhr: { status: 400 } }
    }));
    done({ text: button.textContent, notSaved: button.classList.contains('not-saved'), saved: button.classList.contains('saved'), live: button.getAttribute('aria-live') });
""",
    )

    assert result == {"text": "Not saved \u2717", "notSaved": True, "saved": False, "live": "assertive"}
