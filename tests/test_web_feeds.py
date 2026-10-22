"""TASK-025: the Feeds page, and the three things it owes a reader.

When it last looked and what it found; when it stopped working; and a way out.
Each of those missing is a way a subscription turns into a surprise.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scribe import db, paths
from scribe.app import create_app
from scribe.ingest import feeds, urls
from scribe.stages import url_stage

HX = {"HX-Request": "true"}
FEED_URL = "https://example.test/podcast.xml"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", tmp_path / "models")
    paths.ensure_dirs()
    return tmp_path


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "feeds.db"


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def client(db_path, data_dir, conn):
    app = create_app(db_path=db_path, start_supervisor=False, start_watcher=False)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _subscribe(conn, url=FEED_URL, title="The Feed", **kw):
    """An established feed: one whose back-catalogue question is answered.

    `answered=True` is here rather than at each call site because it is not
    what any test in this file is about - they are about the Feeds page, and a
    feed that is still asking is a different page (it shows the question, and
    the watcher leaves it alone). TASK-044 made asking the default for a new
    subscription, so without this flag every test here would silently change
    subject. The tests that *are* about the question say so, and live in
    tests/test_feed_first_episode.py and tests/test_feed_backfill.py.
    """
    return feeds.subscribe(conn, url, title=title, **kw, answered=True)


def _row(conn, feed_id):
    return dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())


# --- what the page says ------------------------------------------------------------


def test_the_page_lists_a_feed_with_its_url_and_what_the_last_check_found(client, conn):
    feed_id = _subscribe(conn)
    with db.LOCK:
        conn.execute(
            "UPDATE feed SET checked_at=?, last_result='2 new' WHERE id=?",
            (time.time(), feed_id),
        )
        conn.commit()

    body = client.get("/feeds").text

    assert "The Feed" in body
    assert FEED_URL in body
    assert "2 new" in body


def test_a_feed_that_has_never_been_checked_says_so_rather_than_showing_a_date(
    client, conn
):
    _subscribe(conn)

    body = client.get("/feeds").text

    assert "not yet" in body


def test_a_stranger_s_feed_url_is_never_a_link(client, conn):
    """The same rule the episode list follows: this text came over the network
    from somebody else's server."""
    _subscribe(conn, url="https://evil.test/x.xml")

    body = client.get("/feeds").text

    assert 'href="https://evil.test/x.xml"' not in body
    assert "https://evil.test/x.xml" in body


def test_a_feed_that_stopped_answering_says_so_on_the_page(client, conn):
    """Silence is how a subscription stops being one without anybody noticing."""
    feed_id = _subscribe(conn)
    with db.LOCK:
        conn.execute(
            "UPDATE feed SET failures=?, last_result='UrlError: no such host' WHERE id=?",
            (feeds.MAX_FAILURES_REPORTED, feed_id),
        )
        conn.commit()

    body = client.get("/feeds").text

    assert "not answering" in body
    assert "no such host" in body


def test_an_empty_page_says_how_to_start_following_something(client):
    body = client.get("/feeds").text

    assert "Nothing is being followed yet" in body


def test_feeds_is_in_the_nav_beside_jobs(client):
    body = client.get("/").text

    assert 'href="/feeds"' in body


def test_the_board_polls_itself_but_not_at_the_jobs_board_s_pace(client):
    """A feed changes on the hour. Asking every two seconds is only noise."""
    body = client.get("/feeds").text

    assert 'hx-get="/feeds/fragment"' in body
    assert "every 60s" in body


# --- the way out -------------------------------------------------------------------


def test_pausing_a_feed_stops_it_being_due(client, conn):
    feed_id = _subscribe(conn)

    resp = client.post(f"/feeds/{feed_id}/pause", data={"paused": "1"}, headers=HX)

    assert resp.status_code == 200
    assert _row(conn, feed_id)["paused"] == 1
    assert feeds.due_feeds(conn, now=1e12) == []


def test_a_paused_feed_can_be_resumed(client, conn):
    feed_id = _subscribe(conn)
    feeds.set_paused(conn, feed_id, True)

    client.post(f"/feeds/{feed_id}/pause", data={"paused": "0"}, headers=HX)

    assert _row(conn, feed_id)["paused"] == 0


def test_unsubscribing_keeps_the_episodes_the_feed_brought_in(client, conn):
    """They are the person's, not the subscription's, and they keep their own
    provenance."""
    feed_id = _subscribe(conn)
    with db.LOCK:
        conn.execute(
            "INSERT INTO media(sha256, store_path, orig_name, title, size_bytes,"
            " created_at, source_url, source_id)"
            " VALUES ('a', 'p', 'o', 'Episode 1', 1, 0.0, 'https://cdn/1.mp3', 'Generic:g1')"
        )
        conn.commit()

    resp = client.post(f"/feeds/{feed_id}/unsubscribe", headers=HX)

    assert resp.status_code == 200
    assert feeds.all_feeds(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1


def test_an_action_on_a_feed_that_is_gone_is_a_404_not_a_crash(client):
    assert client.post("/feeds/999/pause", headers=HX).status_code == 404
    assert client.post("/feeds/999/unsubscribe", headers=HX).status_code == 404


def test_check_now_polls_that_feed_and_queues_what_is_new(client, conn, monkeypatch):
    """The button exists to bypass the schedule."""
    feed_id = _subscribe(conn)
    info = urls.UrlInfo(
        kind="playlist",
        title="The Feed",
        duration=None,
        uploader="",
        webpage_url=FEED_URL,
        entries=[
            {"url": "https://cdn/1.mp3", "title": "One", "source_id": "Generic:g1"},
        ],
    )
    from scribe.web import ingest_ui

    monkeypatch.setattr(ingest_ui.urls, "probe", lambda url, **kw: info)

    resp = client.post(f"/feeds/{feed_id}/poll", headers=HX)

    assert resp.status_code == 200
    queued = [
        json.loads(row["params_json"])
        for row in conn.execute("SELECT params_json FROM job WHERE type=?", (url_stage.JOB_TYPE,))
    ]
    assert [p[url_stage.ENTRY_KEY]["source_id"] for p in queued] == ["Generic:g1"]
    assert _row(conn, feed_id)["checked_at"] is not None


def test_check_now_does_not_bypass_the_cap(client, conn, monkeypatch):
    """The reason for the ceiling - a night of unattended transcription - does
    not become a good idea because somebody is watching this time."""
    monkeypatch.setattr(feeds, "MAX_NEW_PER_POLL", 2)
    feed_id = _subscribe(conn)
    info = urls.UrlInfo(
        kind="playlist",
        title="The Feed",
        duration=None,
        uploader="",
        webpage_url=FEED_URL,
        entries=[
            {"url": f"https://cdn/{i}.mp3", "title": f"E{i}", "source_id": f"Generic:g{i}"}
            for i in range(6)
        ],
    )
    from scribe.web import ingest_ui

    monkeypatch.setattr(ingest_ui.urls, "probe", lambda url, **kw: info)

    client.post(f"/feeds/{feed_id}/poll", headers=HX)

    n = conn.execute(
        "SELECT COUNT(*) FROM job WHERE type=?", (url_stage.JOB_TYPE,)
    ).fetchone()[0]
    assert n == 2


def test_a_plain_post_redirects_the_way_every_other_action_page_does(client, conn):
    feed_id = _subscribe(conn)

    resp = client.post(f"/feeds/{feed_id}/pause", data={"paused": "1"}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/feeds"


# --- the question a new feed leaves on this page (TASK-044, TASK-045) --------------


def test_a_new_feed_asks_on_the_page_with_a_count_per_choice(client, conn):
    """The question has to survive a closed tab, so it is a row on the page
    rather than a flash. Each choice says what it would fetch."""
    entries = [
        {"url": f"https://cdn.test/ep{n}.mp3", "title": f"Episode {n}", "source_id": f"Generic:g{n}"}
        for n in range(12)
    ]
    feeds.subscribe(conn, FEED_URL, title="The Feed", entries=entries)

    body = client.get("/feeds").text

    assert "How much of the rest" in body
    assert "the last 3 (+2)" in body
    assert "everything (+11)" in body
    assert "nothing more" in body


def test_an_answered_feed_does_not_ask(client, conn):
    _subscribe(conn, entries=[{"url": "https://cdn.test/e.mp3", "source_id": "Generic:g0"}])

    assert "How much of the rest" not in client.get("/feeds").text


def test_answering_on_the_page_queues_what_was_chosen_and_closes_the_question(
    client, conn, monkeypatch
):
    entries = [
        {"url": f"https://cdn.test/ep{n}.mp3", "title": f"Episode {n}", "source_id": f"Generic:g{n}"}
        for n in range(12)
    ]
    feed_id = feeds.subscribe(conn, FEED_URL, title="The Feed", entries=entries)
    monkeypatch.setattr(
        urls,
        "probe",
        lambda url, **kw: urls.UrlInfo(
            kind="playlist", title="The Feed", duration=None, uploader="",
            webpage_url=FEED_URL, entries=list(entries),
        ),
    )

    response = client.post(f"/feeds/{feed_id}/backfill", data={"choice": "3"}, headers=HX)

    assert response.status_code == 200
    assert feeds.is_asking(conn, feed_id) is False
    queued = conn.execute(
        "SELECT COUNT(*) AS n FROM job WHERE type=?", (url_stage.JOB_TYPE,)
    ).fetchone()["n"]
    assert queued == 3  # the first episode, plus the two the answer asked for
    assert "How much of the rest" not in response.text


def test_a_choice_that_is_not_on_offer_is_a_400_and_leaves_the_question_open(
    client, conn, monkeypatch
):
    entries = [{"url": "https://cdn.test/e0.mp3", "source_id": "Generic:g0"}, {"url": "https://cdn.test/e1.mp3", "source_id": "Generic:g1"}]
    feed_id = feeds.subscribe(conn, FEED_URL, title="The Feed", entries=entries)

    response = client.post(f"/feeds/{feed_id}/backfill", data={"choice": "42"}, headers=HX)

    assert response.status_code == 400
    assert feeds.is_asking(conn, feed_id) is True


# --- TASK-091: the Feeds page shows a refusal that kept its point ---------------------


def test_the_feeds_page_shows_a_disk_refusal_that_kept_what_to_do(client, conn, monkeypatch):
    """The stored line is what the page prints under the feed. At the longest
    path Windows allows it still says how much is free, how much is needed
    and that nothing was downloaded; the path gave up its middle."""
    import shutil
    from pathlib import PureWindowsPath

    from scribe import doctor

    longest = PureWindowsPath("C:\\" + "\\".join(["a" * 50] * 4) + "\\" + "b" * 53)
    monkeypatch.setattr(doctor, "disk_probe_path", lambda: longest)
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: shutil._ntuple_diskusage(total=1000 * 2**30, used=0, free=2**30),
    )
    feed_id = _subscribe(conn)
    feed = dict(conn.execute("SELECT * FROM feed WHERE id=?", (feed_id,)).fetchone())
    feeds.poll(conn, feed, probe=lambda url, **kw: None, known_sources=lambda c, e: [], options={})

    body = client.get("/feeds").text

    assert "only 1.0 GB free at C:\\" + "a" * 20 + "\u2026" + "b" * 24 in body
    assert "keeps 10 GB clear" in body
    assert "Free up space and retry; nothing was downloaded." in body


# --- GitHub #41 follow-up: a feed the guard refuses is a sentence, not a 500 ---------


@pytest.fixture
def browser(db_path, data_dir, conn):
    """The app as a browser meets it: a server error is a 500 response rather
    than an exception re-raised into the test, so "500" is what a red run says."""
    app = create_app(db_path=db_path, start_supervisor=False, start_watcher=False)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def feed_that_redirects_home(monkeypatch):
    """A followed feed whose document now 302s to this machine.

    The origin listens on 127.0.0.1 but is reached by the public-looking name
    `feeds.test`, the way test_ingest_urls.py's `redirect_home` does it; the
    trap is what the redirect points at, and a hit there would be the guard
    failing. Real yt-dlp, real 302: nothing about the refusal is stubbed.
    """
    trap_hits: list[str] = []
    origin_hits: list[str] = []

    class Trap(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            trap_hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    trap = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trap)
    trap_url = f"http://127.0.0.1:{trap.server_address[1]}/latest/meta-data"

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            origin_hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", trap_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    for server in (trap, origin):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host == "feeds.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", int(port or 0)))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    real_resolves_private = urls.resolves_private
    monkeypatch.setattr(urls.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(
        urls, "resolves_private",
        lambda host, *rest: host != "feeds.test" and real_resolves_private(host, *rest),
    )
    try:
        yield SimpleNamespace(
            url=f"http://feeds.test:{origin.server_address[1]}/podcast.xml",
            origin_hits=origin_hits,
            trap_hits=trap_hits,
        )
    finally:
        for server in (trap, origin):
            server.shutdown()
            server.server_close()


@pytest.fixture
def feed_whose_host_is_gone(monkeypatch):
    """A followed feed whose name no longer resolves: the source's fault, and
    the plainest one. Answered by the resolver stub, so no network is used."""
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host == "gone.test":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(urls.socket, "getaddrinfo", getaddrinfo)
    return "https://gone.test/podcast.xml"


ASKING_ENTRIES = [
    {"url": f"https://cdn.test/ep{n}.mp3", "title": f"Episode {n}", "source_id": f"Generic:g{n}"}
    for n in range(12)
]


def _queued(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM job WHERE type=?", (url_stage.JOB_TYPE,)
    ).fetchone()["n"]


def test_backfill_from_a_feed_that_redirects_home_says_why_and_leaves_the_question_open(
    browser, conn, feed_that_redirects_home
):
    """GitHub #41, review of PR #59: `backfill` probes under the public guard,
    and the guard's refusal is a `UrlError` - a RuntimeError the route did not
    map, so the person pressing "everything" got a 500. It is the feed's
    answer, so it is the person's answer too: which feed, and why."""
    feed_id = feeds.subscribe(
        conn, feed_that_redirects_home.url, title="The Feed", entries=ASKING_ENTRIES
    )
    before = _queued(conn)

    response = browser.post(f"/feeds/{feed_id}/backfill", data={"choice": "all"}, headers=HX)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert feed_that_redirects_home.url in detail
    assert "latest/meta-data points at this machine or the local network" in detail
    assert feeds.is_asking(conn, feed_id) is True  # the same button works later
    assert _queued(conn) == before
    assert feed_that_redirects_home.origin_hits
    assert feed_that_redirects_home.trap_hits == []


def test_backfill_from_a_feed_whose_host_is_gone_says_so(browser, conn, feed_whose_host_is_gone):
    """The other way a followed feed fails on the button: its host is gone.
    Not the person's fault and not the app's, so not a 500 either."""
    feed_id = feeds.subscribe(conn, feed_whose_host_is_gone, title="The Feed", entries=ASKING_ENTRIES)
    before = _queued(conn)

    response = browser.post(f"/feeds/{feed_id}/backfill", data={"choice": "3"}, headers=HX)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert feed_whose_host_is_gone in detail
    assert "Name or service not known" in detail  # the why, not only the which
    assert feeds.is_asking(conn, feed_id) is True
    assert _queued(conn) == before


def test_check_now_on_a_feed_that_redirects_home_answers_with_the_guard_s_sentence(
    browser, conn, feed_that_redirects_home
):
    """The "check now" half of the same review point. `feeds.poll` already
    turns any probe failure into the feed's last result, so this pins that
    the button answers with the board and the reason on it, not a 500."""
    feed_id = _subscribe(conn, url=feed_that_redirects_home.url)

    response = browser.post(f"/feeds/{feed_id}/poll", headers=HX)

    assert response.status_code == 200
    assert "points at this machine or the local network" in response.text
    assert _queued(conn) == 0
    assert feed_that_redirects_home.trap_hits == []
