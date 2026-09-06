"""The Log page: the application log on screen, and live while it is looked at."""

from __future__ import annotations

import re

import threading

import pytest
from fastapi.testclient import TestClient

from scribe import applog, db, paths
from scribe.app import create_app


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setattr(paths, "LOGS_DIR", logs)
    monkeypatch.setattr(applog, "_default_proc", None)
    monkeypatch.setattr(applog, "_names", threading.local())
    return logs


@pytest.fixture
def client(tmp_path, logs_dir):
    path = tmp_path / "test.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    app = create_app(db_path=path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def offset_in(body: str) -> int:
    return int(re.search(r'id="log-offset" name="after" value="(\d+)"', body).group(1))


def test_log_is_in_the_top_navigation(client):
    body = client.get("/").text
    assert 'href="/logs"' in body and ">Log<" in body


def test_the_page_shows_the_last_lines_and_the_files_path(client):
    """Starting the app is itself the first line: lifespan logs app.start."""
    applog.log("record.start", session="s1")
    applog.log("record.chunk", level="debug", session="s1", index=0, bytes=4200)

    body = client.get("/logs").text

    assert "app.start" in body
    assert "record.start" in body and "record.chunk" in body
    assert "session</span>=s1" in body
    assert str(applog.path()) in body


def test_tail_returns_only_what_is_new_and_carries_the_next_offset(client):
    page = client.get("/logs").text
    offset = offset_in(page)

    quiet = client.get(f"/logs/tail?after={offset}").text
    assert "<tr" not in quiet
    assert offset_in(quiet) == offset

    applog.log("job.enqueued", job=7, type="transcribe")
    fresh = client.get(f"/logs/tail?after={offset}").text

    assert fresh.count("<tr") == 1 and "job.enqueued" in fresh
    assert offset_in(fresh) > offset
    # The polling row asks with the offset the page carries, and appends.
    assert 'hx-swap="beforeend"' in page and 'hx-include="#log-offset"' in page


def test_a_tail_that_lost_its_offset_starts_from_the_end_not_from_the_top(client):
    """`after=0` on the page means "the last 300"; on the tail it would repeat
    them under whatever is already shown, so it means "from now"."""
    applog.log("old")
    fresh = client.get("/logs/tail?after=0").text

    assert "<tr" not in fresh
    applog.log("new")
    assert "new" in client.get(f"/logs/tail?after={offset_in(fresh)}").text


def test_level_and_text_filters_apply_on_the_server(client):
    applog.log("record.chunk", level="debug", session="s1")
    applog.log("runner.exited", level="error", job=3, code=1)
    applog.log("job.enqueued", job=4)

    warn_up = client.get("/logs?level=warn").text
    assert "runner.exited" in warn_up
    assert "record.chunk" not in warn_up and "job.enqueued" not in warn_up

    by_text = client.get("/logs?q=job%3D4").text
    assert "job.enqueued" in by_text and "runner.exited" not in by_text

    # And the poll keeps the same filter.
    offset = offset_in(client.get("/logs?level=error").text)
    applog.log("record.chunk", level="debug")
    applog.log("runner.exited", level="error", job=5, code=1)
    tail = client.get(f"/logs/tail?after={offset}&level=error").text
    assert "runner.exited" in tail and "record.chunk" not in tail


def test_a_line_that_is_not_json_is_shown_as_such_rather_than_breaking_the_page(client):
    applog.log("fine")
    with open(applog.path(), "ab") as fh:
        fh.write(b"not json at all\n")

    body = client.get("/logs").text

    assert "fine" in body
    assert "unparseable" in body and "not json at all" in body


def test_values_are_text_on_the_page(client):
    applog.log("ingest.upload", filename="<script>alert(42)</script>.mp3")

    body = client.get("/logs").text

    assert "<script>alert(42)</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_tail_offset_that_outlived_a_rotation_resyncs_instead_of_repeating(client, monkeypatch):
    """CR-008. After a rotation the page's offset exceeds the new file's size,
    and the old code answered with the last 300 lines - which the page then
    appended under the copies it already showed."""
    stale = offset_in(client.get("/logs").text)
    monkeypatch.setattr(applog, "MAX_BYTES", 300)
    for i in range(20):
        applog.log("fill", i=i, pad="x" * 60)
    assert applog.size() < stale + 2000  # rotated: the file is small again

    body = client.get(f"/logs/tail?after={stale + 5000}").text

    assert "<tr" not in body
    assert offset_in(body) == applog.size()


def test_an_unknown_level_in_a_line_filters_as_info_rather_than_500(client):
    """CR-018. A spliced line can still parse as JSON with a garbage level."""
    with open(applog.path(), "ab") as fh:
        fh.write(b'{"ts": 1.0, "level": "shout", "event": "odd"}\n')

    assert client.get("/logs?level=warn").status_code == 200
    assert "odd" not in client.get("/logs?level=warn").text
    assert "odd" in client.get("/logs?level=info").text


def test_the_page_can_be_paused_and_says_how_much_it_keeps(client):
    """CR-017 and CR-012. Auto-updating content has a stop control, and the
    table is trimmed by app.js to data-log-keep rows."""
    body = client.get("/logs").text

    assert '<button type="button" class="logpause" data-log-pause aria-pressed="false" aria-controls="log-body">' in body
    assert 'id="log-body" data-log-keep="1200"' in body
    js = client.get("/static/app.js").text
    assert "data-log-keep" in js and "[data-log-pause]" in js
