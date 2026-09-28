"""Task 6: FastAPI shell and JSON job API (supervisor off in tests)."""

import pathlib
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

import scribe
from scribe import db, env, jobs, paths
from scribe.app import create_app


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
def client(db_path):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _row(conn, job_id):
    return conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()


# --- /health ------------------------------------------------------------------


def test_health_ok_with_version(client):
    """The body, exactly: `ok` and the version the launcher reads, plus the
    two fields that say what this app serves (TASK-089.17, G5) - the source
    tree it runs from and the library it opened. Pinned as a whole so that a
    field cannot appear or go without this line moving."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "version": scribe.__version__,
        "app_dir": str(env.REPO_DIR),
        "data_dir": str(paths.DATA_DIR),
    }


# --- GET /api/jobs ------------------------------------------------------------


def test_enqueued_job_is_listed_with_queue_position_and_eta(client, conn):
    job_id = jobs.enqueue(conn, "fake", params={"scenario": "ok"})

    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    rows = resp.json()
    (row,) = [r for r in rows if r["id"] == job_id]
    assert row["status"] == "queued"
    assert row["queue_position"] == 1
    assert "eta_seconds" in row  # None without stage_perf history


def test_jobs_list_filters_by_status(client, conn):
    queued_id = jobs.enqueue(conn, "fake")
    failed_id = jobs.enqueue(conn, "fake")
    assert jobs.claim_next(conn)["id"] == queued_id
    jobs.finish(conn, queued_id, "failed", error_code="RUNTIME")

    resp = client.get("/api/jobs", params={"status": "failed"})
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [queued_id]

    resp = client.get("/api/jobs", params={"status": "queued"})
    assert [r["id"] for r in resp.json()] == [failed_id]


# --- GET /api/jobs/{id} -------------------------------------------------------


def test_job_detail_returns_row_and_last_100_events(client, conn):
    job_id = jobs.enqueue(conn, "fake")
    for i in range(105):
        jobs.emit(conn, job_id, "tick", n=i)

    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id
    assert body["status"] == "queued"
    events = body["events"]
    assert len(events) == 100
    assert events[0]["seq"] == 6  # last 100 of 105
    assert events[-1]["seq"] == 105
    assert events[-1]["payload"] == {"n": 104}


def test_job_detail_404_for_unknown_id(client):
    assert client.get("/api/jobs/4242").status_code == 404


# --- POST /api/jobs/{id}/cancel -----------------------------------------------


def test_cancel_queued_job_cancels_instantly(client, conn):
    job_id = jobs.enqueue(conn, "fake")

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert _row(conn, job_id)["status"] == "cancelled"


def test_cancel_404_for_unknown_id(client):
    assert client.post("/api/jobs/4242/cancel").status_code == 404


# --- POST /api/jobs/{id}/retry ------------------------------------------------


def test_retry_failed_job_creates_new_queued_row_with_retry_of(client, conn):
    job_id = jobs.enqueue(conn, "fake", params={"scenario": "boom"}, priority=3)
    jobs.claim_next(conn)
    jobs.finish(conn, job_id, "failed", error_code="RUNTIME", error_detail="boom")

    resp = client.post(f"/api/jobs/{job_id}/retry")
    assert resp.status_code == 200
    new = resp.json()
    assert new["id"] != job_id
    assert new["status"] == "queued"
    assert new["retry_of"] == job_id

    row = _row(conn, new["id"])
    assert row["params_json"] == _row(conn, job_id)["params_json"]
    assert row["priority"] == 3


def test_retry_refused_while_job_is_not_terminal(client, conn):
    job_id = jobs.enqueue(conn, "fake")
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409


# --- GET /api/jobs/{id}/events ------------------------------------------------


def test_events_tail_returns_after_seq_slice(client, conn):
    job_id = jobs.enqueue(conn, "fake")
    for i in range(5):
        jobs.emit(conn, job_id, "tick", n=i)

    resp = client.get(f"/api/jobs/{job_id}/events", params={"after": 3})
    assert resp.status_code == 200
    events = resp.json()
    assert [e["seq"] for e in events] == [4, 5]
    assert events[0]["payload"] == {"n": 3}


# --- lifespan: reconcile on boot ----------------------------------------------


def test_startup_reconciles_orphaned_running_jobs(db_path, conn):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    job_id = jobs.enqueue(conn, "fake")
    conn.execute(
        "UPDATE job SET status='running', pid=? WHERE id=?", (dead.pid, job_id)
    )
    conn.commit()

    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        resp = client.get(f"/api/jobs/{job_id}")
    assert resp.json()["status"] == "interrupted"


# --- __main__ arg parsing -----------------------------------------------------


def test_main_parser_defaults_to_port_4242_with_supervisor():
    from scribe.__main__ import build_parser

    args = build_parser().parse_args([])
    assert args.port == 4242
    assert args.no_supervisor is False

    args = build_parser().parse_args(["--port", "5000", "--no-supervisor"])
    assert args.port == 5000
    assert args.no_supervisor is True


def test_main_parser_can_keep_the_browser_closed():
    from scribe.__main__ import build_parser

    assert build_parser().parse_args([]).no_browser is False
    assert build_parser().parse_args(["--no-browser"]).no_browser is True


class _FakeServer:
    """What open_browser_when_ready looks at: uvicorn.Server's two flags."""

    def __init__(self):
        self.started = False
        self.should_exit = False


def test_browser_opens_once_the_server_reports_started(monkeypatch):
    import threading

    from scribe import __main__ as main_module

    opened: list[str] = []
    monkeypatch.setattr(main_module.webbrowser, "open", lambda url: opened.append(url))
    server = _FakeServer()

    waiter = main_module.open_browser_when_ready(server, "http://127.0.0.1:4242/", poll=0.01, timeout=5.0)

    assert isinstance(waiter, threading.Thread) and waiter.daemon
    threading.Event().wait(0.05)
    assert opened == []  # not before the bind
    server.started = True
    waiter.join(timeout=5.0)
    assert not waiter.is_alive()
    assert opened == ["http://127.0.0.1:4242/"]


def test_browser_stays_closed_when_the_server_never_starts(monkeypatch):
    from scribe import __main__ as main_module

    opened: list[str] = []
    monkeypatch.setattr(main_module.webbrowser, "open", lambda url: opened.append(url))

    never = _FakeServer()
    waiter = main_module.open_browser_when_ready(never, "http://127.0.0.1:4242/", poll=0.01, timeout=0.05)
    waiter.join(timeout=5.0)
    assert not waiter.is_alive() and opened == []

    # A server that gave up (bind failed, lifespan raised) sets should_exit
    # without ever having started; the browser is not opened onto nothing.
    gave_up = _FakeServer()
    gave_up.should_exit = True
    waiter = main_module.open_browser_when_ready(gave_up, "http://127.0.0.1:4242/", poll=0.01, timeout=5.0)
    waiter.join(timeout=5.0)
    assert not waiter.is_alive() and opened == []


# --- ETA reads stage_perf under the same key the runner writes it ---------------


def test_eta_for_a_default_job_uses_the_model_the_runner_files_under(client, conn):
    # Review finding: the runner filed stage_perf under the resolved model
    # ("large-v3-turbo" for a job with no model param) while this endpoint
    # looked it up under params["model"] (None), so every ETA past prepare
    # was permanently None.
    media_id = conn.execute(
        "INSERT INTO media(sha256, store_path, orig_name, title, size_bytes, created_at, duration) "
        "VALUES (?, 'p', 'o.wav', 'o', 1, 1.0, 600.0)",
        ("a" * 64,),
    ).lastrowid
    conn.commit()
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_id, params={})
    jobs.claim_next(conn)
    jobs.set_stage(conn, job_id, "transcribe", 0.2)
    for _ in range(3):
        jobs.record_stage_perf(conn, "transcribe", "large-v3-turbo", 600.0, 40.0)

    rows = client.get("/api/jobs").json()
    row = next(r for r in rows if r["id"] == job_id)

    assert row["eta_seconds"] is not None
    assert row["eta_seconds"] > 0


def test_the_version_is_the_same_number_in_both_places():
    """`scribe.__version__` is what the page and /health report; pyproject's
    is what the wheel and the lockfile carry. Nothing tied them together, so
    bumping one and forgetting the other was a silent drift - and the one a
    person sees in the header would be the one that went stale.
    """
    import re
    import tomllib

    import scribe

    root = pathlib.Path(__file__).resolve().parents[1]
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared["project"]["version"] == scribe.__version__
    # X.Y.Z, or X.Y.Z with a PEP 440 pre-release for a beta (0.8.0b1, TASK-100):
    # packaging/release_kind.py publishes that as a GitHub pre-release.
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?", scribe.__version__), scribe.__version__
