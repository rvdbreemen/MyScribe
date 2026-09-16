"""Phase 3 Task 5: the jobs dashboard, the job detail page and its SSE stream.

A seeded board - one running job, one queued, one failed - seen through the
dashboard's three sections, the fragment's adaptive poll interval, the detail
page's error card and event list, and the event stream: replay with ids,
resume after Last-Event-ID, a tail that follows a job to its end. No GPU, no
models, no pipeline: rows come from tests/seed.py and jobs.emit, and the
requests go through FastAPI's TestClient.

The TestClient runs the app to completion and buffers the body, so a stream
that never ends would hang a test. The tail test finishes its job from a
second thread on a second connection - WAL lets both write - and reads the
stream with a bounded iterator that stops at the `end` frame.
"""

import json
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from scribe import db, jobs, paths
from scribe.app import create_app
from scribe.web import jobs_ui
from seed import seed_job, seed_media


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


HX = {"HX-Request": "true"}

TRACE = (
    "Traceback (most recent call last):\n"
    '  File "scribe/stages/transcribe.py", line 42, in run\n'
    "    model = load()\n"
    "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2 GiB\n"
)


@pytest.fixture
def board(conn):
    """One running job (in transcribe), one queued, one failed, with events."""
    alpha = seed_media(conn, title="Alpha", duration=600.0)
    beta = seed_media(conn, title="Beta", duration=120.0)
    gamma = seed_media(conn, title="Gamma", duration=60.0)

    running = seed_job(conn, alpha, status="running", stage="transcribe", progress=0.4)
    jobs.emit(conn, running, "stage", name="probe")
    jobs.emit(conn, running, "probe", duration=600.0, format_name="wav")
    jobs.emit(conn, running, "stage", name="prepare")
    jobs.emit(conn, running, "stage", name="transcribe")

    queued = seed_job(conn, beta, status="queued")

    failed = seed_job(
        conn,
        gamma,
        status="failed",
        stage="transcribe",
        error_code="CUDA_OOM",
        error_detail="CUDA out of memory. Tried to allocate 2 GiB",
    )
    jobs.emit(conn, failed, "stage", name="probe")
    jobs.emit(conn, failed, "stage", name="prepare")
    jobs.emit(conn, failed, "stage", name="transcribe")
    jobs.emit(conn, failed, "error", trace=TRACE)

    return {
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "running": running,
        "queued": queued,
        "failed": failed,
    }


def _section(body: str, name: str) -> str:
    """The HTML of one dashboard section, by its id."""
    start = body.index(f'id="{name}"')
    end = body.find("<section", start + 1)
    return body[start:] if end == -1 else body[start:end]


# --- jobs.active_count -------------------------------------------------------------


def test_active_count_counts_running_and_queued_jobs(conn):
    media_id = seed_media(conn)
    assert jobs.active_count(conn) == 0

    running = seed_job(conn, media_id, status="running")
    queued = seed_job(conn, media_id, status="queued")
    seed_job(conn, media_id, status="done")
    seed_job(conn, media_id, status="failed", error_code="RUNTIME")
    assert jobs.active_count(conn) == 2

    jobs.finish(conn, running, "done")
    assert jobs.active_count(conn) == 1
    jobs.request_cancel(conn, queued)  # a queued job dies instantly
    assert jobs.active_count(conn) == 0


def test_the_jobs_router_does_not_reach_into_the_app_module():
    """scribe.app imports scribe.web; a router importing scribe.app back is a
    cycle that only works because mount() defers the router imports. What
    the board needs (retryable statuses, the ETA) is scribe.jobs' to give."""
    source = open(jobs_ui.__file__, encoding="utf-8").read()
    assert "from scribe.app" not in source
    assert "import scribe.app" not in source


# --- the dashboard ------------------------------------------------------------------


def test_jobs_page_lists_running_queued_and_history_in_their_sections(client, board):
    resp = client.get("/jobs")

    assert resp.status_code == 200
    body = resp.text
    assert "<html" in body
    assert 'id="jobs-live"' in body
    running = _section(body, "running")
    queued = _section(body, "queued")
    history = _section(body, "history")
    assert f'href="/jobs/{board["running"]}"' in running and ">Alpha<" in running
    assert ">Beta<" not in running and ">Gamma<" not in running
    assert f'href="/jobs/{board["queued"]}"' in queued and ">Beta<" in queued
    assert ">Alpha<" not in queued and ">Gamma<" not in queued
    assert f'href="/jobs/{board["failed"]}"' in history and ">Gamma<" in history
    assert ">Alpha<" not in history and ">Beta<" not in history
    assert "s-failed" in history and "CUDA_OOM" in history


def test_running_job_stepper_marks_transcribe_active_and_earlier_stages_done(client, board):
    running = _section(client.get("/jobs").text, "running")

    assert 'class="step done" data-stage="probe"' in running
    assert 'class="step done" data-stage="prepare"' in running
    assert 'class="step active" data-stage="transcribe"' in running
    assert 'class="step todo" data-stage="diarize"' in running
    assert 'class="step todo" data-stage="attribute"' in running
    assert 'class="step todo" data-stage="finalize"' in running
    assert 'max="100" value="40"' in running
    assert "transcribe · 40%" in running


def test_running_job_shows_elapsed_and_the_eta_from_stage_history(client, conn, board):
    # transcribe ran at half realtime on this machine: 100 s of media in 50 s.
    jobs.record_stage_perf(conn, "transcribe", "large-v3-turbo", 100.0, 50.0)

    running = _section(client.get("/jobs").text, "running")

    # Started a minute ago (seed_job), so elapsed is 1:00 give or take a tick;
    # the number carries data-since so the page can keep counting.
    assert re.search(r'class="elapsed live" data-since="[0-9.]+">1:0[0-2]<', running)
    # Alpha is 600 s, so the stage takes 300 s; 40 % done leaves 180 s.
    assert "3:00" in running
    assert "left in transcribe" in running


def test_running_job_without_history_says_the_eta_is_unknown(client, board):
    running = _section(client.get("/jobs").text, "running")

    assert "3:00" not in running
    assert "no estimate yet" in running


def test_fragment_polls_every_2s_with_active_jobs_and_every_15s_once_idle(client, conn, board):
    resp = client.get("/jobs/fragment")

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    assert 'hx-get="/jobs/fragment"' in body
    assert 'hx-trigger="every 2s' in body

    jobs.finish(conn, board["running"], "done")
    jobs.request_cancel(conn, board["queued"])

    body = client.get("/jobs/fragment").text
    assert 'hx-trigger="every 15s' in body
    assert 'hx-trigger="every 2s' not in body


def test_fragment_refreshes_on_the_events_the_page_fires(client, board):
    body = client.get("/jobs/fragment").text

    # app.js fires `refresh` after a confirmed form posts; the transcribe
    # dialog's response carries HX-Trigger: jobs-changed.
    assert re.search(r'hx-trigger="every 2s[^"]*\brefresh\b', body)
    assert re.search(r'hx-trigger="every 2s[^"]*jobs-changed from:body', body)
    assert 'hx-swap="outerHTML"' in body


def test_an_hx_request_to_jobs_returns_the_fragment_alone(client, board):
    body = client.get("/jobs", headers=HX).text

    assert "<html" not in body
    assert 'id="jobs-live"' in body
    assert ">Alpha<" in body


def test_queued_jobs_list_in_claim_order_with_positions_and_a_params_summary(
    client, conn, board
):
    delta = seed_media(conn, title="Delta")
    urgent = jobs.enqueue(
        conn,
        "transcribe",
        media_id=delta,
        params={
            "model": "large-v3",
            "task": "translate",
            "language": "nl",
            "diarize": True,
            "num_speakers": 2,
        },
        priority=5,
    )

    queued = _section(client.get("/jobs").text, "queued")

    # Higher priority claims first, whatever the order they were queued in.
    assert queued.index(f'href="/jobs/{urgent}"') < queued.index(f'href="/jobs/{board["queued"]}"')
    rows = re.findall(r'<td class="num">(\d+)</td>', queued)
    assert rows == ["1", "2"]
    assert "Maximaal" in queued
    assert "nl" in queued
    assert "translate" in queued
    assert "2 speakers" in queued


def test_cancel_and_retry_forms_post_to_the_job_api_and_refresh_the_board(
    client, conn, board
):
    done = seed_job(conn, board["alpha"], status="done")
    body = client.get("/jobs").text
    running = _section(body, "running")
    queued = _section(body, "queued")
    history = _section(body, "history")

    for section, job_id in ((running, board["running"]), (queued, board["queued"])):
        form = re.search(
            rf'<form[^>]*action="/api/jobs/{job_id}/cancel"[^>]*>', section
        )
        assert form, f"no cancel form for job {job_id}"
        assert "data-confirm=" in form.group(0)
        assert 'data-refresh="#jobs-live"' in form.group(0)

    retry = re.search(rf'<form[^>]*action="/api/jobs/{board["failed"]}/retry"[^>]*>', history)
    assert retry and 'data-refresh="#jobs-live"' in retry.group(0)
    assert f'action="/api/jobs/{done}/retry"' not in history  # done is not retryable


def test_a_cancel_already_requested_shows_as_cancelling(client, conn, board):
    jobs.request_cancel(conn, board["running"])

    running = _section(client.get("/jobs").text, "running")

    assert "cancelling" in running
    assert re.search(r"<button[^>]*disabled[^>]*>cancelling", running)


def test_history_keeps_the_last_fifty_terminal_jobs(client, conn, board, monkeypatch):
    monkeypatch.setattr(jobs_ui, "HISTORY_LIMIT", 3)
    ids = [seed_job(conn, board["alpha"], status="done") for _ in range(4)]

    history = _section(client.get("/jobs").text, "history")

    assert f'href="/jobs/{ids[-1]}"' in history
    assert f'href="/jobs/{ids[-3]}"' in history
    assert f'href="/jobs/{ids[0]}"' not in history
    assert f'href="/jobs/{board["failed"]}"' not in history


def test_a_doctor_job_shows_its_own_stage_and_no_transcribe_summary(client, conn):
    """A doctor job has one stage, gpu-checks, and neither media nor
    options. It is not a transcribe job wearing a six-step stepper and a
    "Turbo · auto-detect language · speakers" line - the steps come from
    the registry for the job's type, and the summary only from transcribe
    params."""
    job_id = jobs.enqueue(conn, "doctor")
    jobs.claim_next(conn)
    jobs.set_stage(conn, job_id, "gpu-checks", 0.5)

    running = _section(client.get("/jobs").text, "running")
    assert f'href="/jobs/{job_id}"' in running and ">doctor job<" in running
    assert 'class="step active" data-stage="gpu-checks"' in running
    assert 'data-stage="probe"' not in running and 'data-stage="transcribe"' not in running
    assert "⚡ Turbo" not in running and "auto-detect language" not in running

    detail = client.get(f"/jobs/{job_id}").text
    assert 'class="step active" data-stage="gpu-checks"' in detail
    assert 'data-stage="transcribe"' not in detail
    assert "⚡ Turbo" not in detail and "auto-detect language" not in detail

    # A transcribe job keeps its six steps and its summary.
    media_id = seed_media(conn, title="Delta")
    seed_job(conn, media_id, status="queued")
    queued = _section(client.get("/jobs").text, "queued")
    assert "⚡ Turbo" in queued and "auto-detect language" in queued


def test_titles_are_escaped_on_the_board(client, conn):
    media_id = seed_media(conn, title="<b>Zaphod</b>")
    seed_job(conn, media_id, status="running")

    body = client.get("/jobs").text

    assert "&lt;b&gt;Zaphod&lt;/b&gt;" in body
    assert "<b>Zaphod</b>" not in body


# --- the detail page ------------------------------------------------------------------


def test_job_detail_shows_the_error_card_with_code_detail_and_the_trace(client, board):
    resp = client.get(f"/jobs/{board['failed']}")

    assert resp.status_code == 200
    body = resp.text
    assert "<html" in body
    assert 'id="job-panel"' in body
    assert ">Gamma<" in body and f'href="/media/{board["gamma"]}"' in body
    card = body[body.index('class="error-card"'):]
    assert "CUDA_OOM" in card
    assert "CUDA out of memory. Tried to allocate 2 GiB" in card
    assert "<details" in card and "stderr" in card
    assert "Traceback (most recent call last):" in card
    assert "torch.cuda.OutOfMemoryError" in card
    # The stepper stops where it failed.
    assert 'class="step done" data-stage="prepare"' in body
    assert 'class="step failed" data-stage="transcribe"' in body
    assert 'class="step todo" data-stage="diarize"' in body
    assert f'action="/api/jobs/{board["failed"]}/retry"' in body
    # Retry refreshes both regions: the verdict lands in the status panel and
    # the closing events in the details, and a page showing one without the
    # other is a page that lies.
    assert 'data-refresh="#job-panel, #job-details"' in body


def test_job_detail_of_a_running_job_has_no_error_card_and_a_cancel_form(client, board):
    body = client.get(f"/jobs/{board['running']}").text

    assert 'class="error-card"' not in body
    assert f'action="/api/jobs/{board["running"]}/cancel"' in body
    assert 'class="step active" data-stage="transcribe"' in body


def test_job_detail_links_the_retry_chain_both_ways(client, conn, board):
    again = jobs.enqueue(conn, "transcribe", media_id=board["gamma"], retry_of=board["failed"])

    new = client.get(f"/jobs/{again}").text
    old = client.get(f"/jobs/{board['failed']}").text

    assert f'href="/jobs/{board["failed"]}"' in new  # retry of ...
    assert f'href="/jobs/{again}"' in old  # ... retried as
    assert 'class="step todo" data-stage="probe"' in new  # queued: nothing done yet


def test_job_detail_renders_the_last_200_events_and_wires_the_stream(client, conn, board):
    job_id = board["queued"]
    for i in range(1, 206):
        jobs.emit(conn, job_id, "tick", n=i)

    body = client.get(f"/jobs/{job_id}").text

    assert 'data-seq="205"' in body
    assert 'data-seq="6"' in body
    assert 'data-seq="5"' not in body
    assert "n=205" in body
    pre = re.search(r'<pre id="log"[^>]*>', body)
    assert pre, "no live log element"
    assert f'data-stream-url="/api/jobs/{job_id}/stream"' in pre.group(0)
    assert 'data-last-seq="205"' in pre.group(0)
    assert 'data-terminal="false"' in pre.group(0)


def test_job_detail_log_is_prefilled_with_the_events_the_page_already_has(client, conn, board):
    """The stream only carries what happens after the page loads, so a job
    opened after it finished - or the part of a running one that ran before -
    showed an empty log. The <pre> now starts with every event rendered."""
    done = board["failed"]
    jobs.emit(conn, done, "tick", n=42)

    body = client.get(f"/jobs/{done}").text

    pre = re.search(r'<pre id="log"[^>]*>(.*?)</pre>', body, re.S)
    assert pre, "no log element"
    log = pre.group(1)
    assert "stage prepare" in log
    assert "tick n=42" in log
    assert "-- end of log (failed) --" in log
    assert 'data-terminal="true"' in pre.group(0)
    assert ">Log</label>" in body and "Live log" not in body

    running = client.get(f"/jobs/{board['running']}").text
    pre = re.search(r'<pre id="log"[^>]*>(.*?)</pre>', running, re.S)
    assert "-- end of log" not in pre.group(1)
    assert "Live log</label>" in running


def test_live_text_events_are_log_lines_and_stay_out_of_the_events_table(client, conn, board):
    job_id = board["running"]
    jobs.emit(conn, job_id, "log", at=83.0, until=90.5, text="Don't panic. Bring a towel.")
    jobs.emit(conn, job_id, "tick", n=1)

    body = client.get(f"/jobs/{job_id}").text

    pre = re.search(r'<pre id="log"[^>]*>(.*?)</pre>', body, re.S).group(1)
    assert "[1:23] Don&#39;t panic. Bring a towel." in pre or "[1:23] Don't panic. Bring a towel." in pre
    assert "log [1:23]" not in pre  # the kind word is left off a text line
    table = body[body.index('<table class="events">'):]
    assert "Bring a towel" not in table
    assert "tick" in table


def test_job_detail_hx_request_returns_the_panel_alone(client, board):
    body = client.get(f"/jobs/{board['failed']}", headers=HX).text

    assert "<html" not in body
    assert 'id="job-panel"' in body
    assert 'class="error-card"' in body
    assert 'id="log"' not in body  # the live log stays where it is


def test_unknown_job_is_a_404_on_every_route(client, board):
    assert client.get("/jobs/9999").status_code == 404
    assert client.get("/api/jobs/9999/stream").status_code == 404


# --- the stream ------------------------------------------------------------------------


def _read_frames(client, url, headers=None, max_lines=2000):
    """SSE frames from ``url`` as dicts, read line by line until the `end`
    frame, never more than ``max_lines`` lines. Comment lines become
    ``{"comment": ...}`` frames of their own."""
    frames: list[dict] = []
    current: dict = {}
    seen = 0
    with client.stream("GET", url, headers=headers or {}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in resp.headers["cache-control"]
        for line in resp.iter_lines():
            seen += 1
            assert seen <= max_lines, "the stream did not end"
            if line == "":
                if current:
                    frames.append(current)
                    if current.get("event") == "end":
                        break
                    current = {}
                continue
            if line.startswith(":"):
                frames.append({"comment": line[1:].strip()})
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "data" and "data" in current:
                current["data"] += "\n" + value
            else:
                current[field] = value
    return frames


def _events(frames):
    return [f for f in frames if "event" in f]


def test_stream_replays_the_events_with_ids_and_ends_for_a_done_job(client, conn, board):
    job_id = seed_job(conn, board["alpha"], status="done", stage="finalize")
    jobs.emit(conn, job_id, "stage", name="probe")
    jobs.emit(conn, job_id, "probe", duration=600.0)
    jobs.emit(conn, job_id, "stage", name="finalize")
    jobs.emit(conn, job_id, "finalize", n_words=40)

    frames = _read_frames(client, f"/api/jobs/{job_id}/stream")

    assert frames[0] == {"retry": "2000"}
    events = _events(frames)
    assert [e.get("id") for e in events[:4]] == ["1", "2", "3", "4"]
    assert [e["event"] for e in events[:4]] == ["stage", "log", "stage", "log"]
    assert json.loads(events[0]["data"]) == {
        "seq": 1, "kind": "stage", "payload": {"name": "probe"}, "ts": json.loads(events[0]["data"])["ts"],
    }
    assert json.loads(events[1]["data"])["kind"] == "probe"
    assert json.loads(events[3]["data"])["payload"] == {"n_words": 40}
    # The job row's state rides along without an id; the end frame closes it.
    progress = [e for e in events if e["event"] == "progress"]
    assert progress and "id" not in progress[0]
    assert json.loads(progress[0]["data"])["status"] == "done"
    assert events[-1]["event"] == "end"
    assert "id" not in events[-1]
    end = json.loads(events[-1]["data"])
    assert end["status"] == "done"
    assert end["last_seq"] == 4
    assert end["job_id"] == job_id


def test_stream_resumes_after_last_event_id(client, conn, board):
    job_id = seed_job(conn, board["alpha"], status="done", stage="finalize")
    for i in range(1, 5):
        jobs.emit(conn, job_id, "tick", n=i)

    by_header = _read_frames(client, f"/api/jobs/{job_id}/stream", headers={"Last-Event-ID": "2"})
    by_query = _read_frames(client, f"/api/jobs/{job_id}/stream?last_event_id=2")

    for frames in (by_header, by_query):
        ids = [e["id"] for e in _events(frames) if "id" in e]
        assert ids == ["3", "4"]
        assert json.loads(_events(frames)[-1]["data"])["last_seq"] == 4

    # Nonsense means "from the beginning": replaying is the safe way to be wrong.
    frames = _read_frames(client, f"/api/jobs/{job_id}/stream", headers={"Last-Event-ID": "two"})
    assert [e["id"] for e in _events(frames) if "id" in e] == ["1", "2", "3", "4"]


def test_stream_names_error_events_and_the_end_carries_the_error_code(client, board):
    frames = _read_frames(client, f"/api/jobs/{board['failed']}/stream")

    events = _events(frames)
    error = [e for e in events if e["event"] == "error"]
    assert len(error) == 1
    assert json.loads(error[0]["data"])["payload"]["trace"] == TRACE
    end = json.loads(events[-1]["data"])
    assert end["status"] == "failed"
    assert end["error_code"] == "CUDA_OOM"


def test_stream_tails_a_running_job_until_it_finishes(client, conn, db_path, board, monkeypatch):
    """The stream follows a live job: events appended after it opened arrive,
    stage progress written to the row becomes a `progress` frame, silence
    produces heartbeats, and the runner's verdict ends the stream."""
    monkeypatch.setattr(jobs_ui, "SSE_POLL_SECONDS", 0.02)
    monkeypatch.setattr(jobs_ui, "SSE_HEARTBEAT_SECONDS", 0.05)
    job_id = board["running"]

    def runner():
        c = db.connect(db_path)
        try:
            time.sleep(0.2)
            jobs.emit(c, job_id, "transcribe", model="large-v3-turbo")
            jobs.set_stage(c, job_id, "transcribe", 0.5)
            time.sleep(0.2)
            jobs.set_stage(c, job_id, "finalize", 0.0)
            jobs.emit(c, job_id, "stage", name="finalize")
            time.sleep(0.2)
            jobs.finish(c, job_id, "done")
        finally:
            c.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        frames = _read_frames(client, f"/api/jobs/{job_id}/stream")
    finally:
        thread.join(timeout=5)
    assert not thread.is_alive()

    events = _events(frames)
    ids = [e["id"] for e in events if "id" in e]
    assert ids == ["1", "2", "3", "4", "5", "6"]  # four seeded, two from the thread
    assert [e["event"] for e in events if "id" in e][-2:] == ["log", "stage"]
    progress = [json.loads(e["data"]) for e in events if e["event"] == "progress"]
    assert progress[0]["status"] == "running"
    assert (progress[0]["stage"], progress[0]["stage_progress"]) == ("transcribe", 0.4)
    assert any(p["stage"] == "transcribe" and p["stage_progress"] == 0.5 for p in progress)
    assert any(p["stage"] == "finalize" for p in progress)
    assert progress[-1]["status"] == "done"
    assert any(f.get("comment") == "heartbeat" for f in frames)
    end = json.loads(events[-1]["data"])
    assert (end["status"], end["last_seq"]) == ("done", 6)


def test_stream_frames_are_well_formed_sse(client, conn, board):
    """Every data line is one JSON document per frame, and a payload with a
    newline in it does not break the framing."""
    job_id = seed_job(conn, board["alpha"], status="done")
    jobs.emit(conn, job_id, "error", trace="line one\nline two")

    with client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        raw = b"".join(resp.iter_bytes()).decode("utf-8")

    frames = [f for f in raw.split("\n\n") if f]
    assert frames[0] == "retry: 2000"
    error = [f for f in frames if "event: error" in f]
    assert len(error) == 1
    lines = error[0].split("\n")
    assert lines[0] == "id: 1"
    assert lines[1] == "event: error"
    assert lines[2].startswith("data: ")
    assert len(lines) == 3  # json.dumps escapes the newline; nothing leaks a bare line
    assert json.loads(lines[2][6:])["payload"]["trace"] == "line one\nline two"


# --- the job page's tabs ------------------------------------------------------------


def test_the_job_page_is_three_tabs_over_one_screen(client, board):
    """It stacked status, parameters, events and the live log, so watching a
    running job meant scrolling between the stepper and the log and never
    seeing both."""
    body = client.get(f"/jobs/{board['running']}").text

    for name in ("log", "events", "params"):
        assert f'data-jobpanel="{name}"' in body, name
    # A running job opens on the live log: that is the part that changes.
    assert re.search(r'id="jt-log"[^>]*\schecked', body)
    assert not re.search(r'id="jt-events"[^>]*\schecked', body)


def test_a_finished_job_opens_on_its_events_because_its_log_is_over(client, board):
    body = client.get(f"/jobs/{board['failed']}").text

    assert re.search(r'id="jt-events"[^>]*\schecked', body)
    assert not re.search(r'id="jt-log"[^>]*\schecked', body)


def test_the_chosen_tab_survives_a_refresh_of_either_region(client, board):
    """The radios live outside both htmx regions. One inside would be replaced
    by a fresh unchecked copy every time the stream ended - putting the page
    back on the default tab at the moment someone was reading it."""
    job_id = board["running"]
    page = client.get(f"/jobs/{job_id}").text

    panel = client.get(f"/jobs/{job_id}", headers={"HX-Request": "true"}).text
    details = client.get(f"/jobs/{job_id}/details", headers={"HX-Request": "true"}).text

    assert 'name="job_tab"' in page
    assert 'name="job_tab"' not in panel
    assert 'name="job_tab"' not in details
    # And the two regions between them hold every tab body.
    assert 'data-jobpanel="events"' in details and 'data-jobpanel="params"' in details
    assert 'id="job-details"' in details


def test_the_details_region_answers_on_its_own(client, board):
    resp = client.get(f"/jobs/{board['failed']}/details")

    assert resp.status_code == 200
    assert resp.text.lstrip().startswith("<section id=\"job-details\"")
    assert "Parameters" in resp.text and "Events" in resp.text


def test_a_silent_recording_is_a_readable_verdict_on_the_board_and_the_page(client, conn):
    """No code table translates error codes: the code and error_detail are what
    a person reads. So the detail probe writes has to carry the number and the
    remedy, and both surfaces have to show it."""
    media_id = seed_media(conn, title="Dead mic")
    detail = ("the recording contains no sound: peak -91 dBFS over the first 8 s "
              "(a working microphone gives room noise above -60 dBFS). "
              "Check the input device and record again.")
    job_id = seed_job(conn, media_id, status="failed", stage="probe",
                      error_code="SILENT_AUDIO", error_detail=detail)

    page = client.get(f"/jobs/{job_id}").text
    board = client.get("/jobs").text

    assert "SILENT_AUDIO" in page and "Check the input device" in page and "-91 dBFS" in page
    assert "SILENT_AUDIO" in board and "Check the input device" in board
    assert 'class="step failed" data-stage="probe"' in page


def test_the_live_region_is_the_status_sentence_not_the_streaming_log(client, board):
    """CR-004. aria-live on the <pre> read every trace line aloud for the
    whole run and drowned the flash; the sentence is what carries status."""
    body = client.get(f"/jobs/{board['running']}").text

    pre = re.search(r'<pre id="log"[^>]*>', body).group(0)
    status = re.search(r'<span id="log-status"[^>]*>', body).group(0)
    assert "aria-live" not in pre
    assert 'aria-live="polite"' in status


def test_each_tab_radio_names_the_panel_it_shows(client, board):
    """CR-027."""
    body = client.get(f"/jobs/{board['running']}").text

    for name in ("log", "events", "params"):
        assert f'id="jt-{name}" class="visually-hidden" aria-controls="jobpanel-{name}"' in body
        assert f'id="jobpanel-{name}"' in body


# --- an ingest_url job is named after its episode (TASK-021) ----------------------


def test_an_ingest_url_job_is_named_by_its_episode_from_queue_to_history(client, conn):
    """Twenty jobs from one feed were twenty rows called "ingest_url job". The
    listing's title names the row, the link itself when there was no listing,
    and a failed child with no media row behind it keeps its name in history."""
    job_id = jobs.enqueue(conn, "ingest_url", params={
        "url": "https://cdn.example/default.mp3", "from_playlist": True,
        "entry": {"title": "Love in the time of Palantir", "source_id": None},
        "source": {"url": "https://feeds.npr.org/510289/podcast.xml", "title": "Planet Money"},
    })
    bare = jobs.enqueue(conn, "ingest_url", params={"url": "https://youtu.be/dQw4w9WgXcQ"})

    queued = _section(client.get("/jobs").text, "queued")

    assert f'href="/jobs/{job_id}"' in queued and ">Love in the time of Palantir<" in queued
    assert f'href="/jobs/{bare}"' in queued and ">https://youtu.be/dQw4w9WgXcQ<" in queued
    assert "ingest_url job" not in queued

    jobs.claim_next(conn)
    jobs.finish(conn, job_id, "failed", error_code="UNAVAILABLE", error_detail="HTTP Error 403")

    history = _section(client.get("/jobs").text, "history")
    assert ">Love in the time of Palantir<" in history and "UNAVAILABLE" in history
    assert ">Love in the time of Palantir<" in client.get(f"/jobs/{job_id}").text


def test_an_episode_title_that_is_markup_is_escaped_on_the_board(client, conn):
    """The title now reaches the board through params_json, a new path; this
    pins that it goes through the same autoescape as a media title."""
    jobs.enqueue(conn, "ingest_url", params={"url": "https://cdn.example/x.mp3", "entry": {"title": "<b>Zaphod</b>"}})

    body = client.get("/jobs").text

    assert "&lt;b&gt;Zaphod&lt;/b&gt;" in body and "<b>Zaphod</b>" not in body


# --- moving a job from the board (TASK-047) -----------------------------------


def test_a_queued_row_shows_its_priority_and_the_controls_to_change_it(client, conn, board):
    """AC1 and AC5: visible, and operable without scripting - the forms post
    to the same JSON job API that Cancel and Retry use."""
    queued = _section(client.get("/jobs").text, "queued")

    assert "Normal" in queued
    assert re.search(r'action="/api/jobs/\d+/priority"', queued)
    assert re.search(r'action="/api/jobs/\d+/move"', queued)
    assert 'data-refresh="#jobs-live"' in queued


def test_raising_a_job_from_the_board_answers_with_its_new_place(client, conn, board):
    job_id = jobs.enqueue(conn, "transcribe", params={})

    response = client.post(f"/api/jobs/{job_id}/priority", data={"priority": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["priority"] == 10
    assert body["queue_position"] == 1  # nothing else is queued above it


def test_the_board_refuses_to_move_a_running_job(client, conn, board):
    # The board's own running job. Claiming a second one beside it is refused
    # now (TASK-070), which is the rule this test used to sidestep.
    job_id = board["running"]

    response = client.post(f"/api/jobs/{job_id}/priority", data={"priority": 10})

    assert response.status_code == 409
    assert "queued" in response.json()["detail"]


def test_an_unknown_move_is_a_400_not_a_guess(client, conn, board):
    job_id = jobs.enqueue(conn, "transcribe", params={})

    assert client.post(f"/api/jobs/{job_id}/move", data={"where": "sideways"}).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/move", data={"where": "front"}).status_code == 200


def test_moving_an_unknown_job_is_a_404(client, conn, board):
    assert client.post("/api/jobs/9999/priority", data={"priority": 0}).status_code == 404
