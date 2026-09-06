"""The two guards that keep the localhost app to its one user's own browser.

The app binds 127.0.0.1 and has no login, which is the spec's choice for a
single user. Two things a browser does anyway would otherwise turn that into
"any page you visit": a page on some other site can auto-submit a form to
127.0.0.1:4242 (trash the library, queue GPU jobs), and with DNS rebinding it
can read the answers too. The guards close both doors with headers every
browser already sends: the Host header must name this machine, and a request
that mutates must come from this app's own pages (Sec-Fetch-Site / Origin).
A script on the machine itself - curl, a test - sends neither and stays
welcome; it is the user.
"""

import pytest
from fastapi.testclient import TestClient

from scribe import db, guard, paths
from scribe.app import create_app


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
    # The browser's address bar says 127.0.0.1, so the Host header does too.
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _folders(conn):
    return conn.execute("SELECT name FROM folder ORDER BY id").fetchall()


# --- the Host header ---------------------------------------------------------------


def test_a_foreign_host_header_is_refused(client):
    """DNS rebinding: the page at evil.example points its name at 127.0.0.1
    and reads the library. The Host header still says evil.example."""
    resp = client.get("/health", headers={"Host": "evil.example"})

    assert resp.status_code == 400
    assert "ok" not in resp.text


def test_the_loopback_names_are_accepted_with_or_without_a_port(client):
    for host in ("127.0.0.1", "127.0.0.1:4242", "localhost", "localhost:4242"):
        resp = client.get("/health", headers={"Host": host})
        assert resp.status_code == 200, host
        assert resp.json()["ok"] is True


def test_the_allowed_hosts_are_loopback_only():
    assert set(guard.ALLOWED_HOSTS) == {"127.0.0.1", "localhost"}


# --- cross-site posts ------------------------------------------------------------------


def test_a_cross_site_post_is_refused_before_it_runs(client, conn):
    """A form on another site auto-submitted to us: the browser labels it."""
    for site in ("cross-site", "same-site"):
        resp = client.post(
            "/folders",
            data={"name": "Planted"},
            headers={"Sec-Fetch-Site": site, "Origin": "http://evil.example"},
        )
        assert resp.status_code == 403, site
        assert "refused" in resp.json()["detail"]
    assert _folders(conn) == []


def test_a_post_with_a_foreign_origin_is_refused(client, conn):
    """An older browser that sends Origin but no Sec-Fetch-Site; and the
    `null` origin a sandboxed frame or a file:// page sends."""
    for origin in ("http://evil.example", "http://127.0.0.1.evil.example", "null"):
        resp = client.post("/folders", data={"name": "Planted"}, headers={"Origin": origin})
        assert resp.status_code == 403, origin
    assert _folders(conn) == []


def test_the_apps_own_pages_may_post(client, conn):
    same_origin = {"Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1"}
    resp = client.post(
        "/folders", data={"name": "Ours"}, headers=same_origin, follow_redirects=False
    )
    assert resp.status_code == 303

    # A navigation the user started (address bar, bookmark) carries `none`.
    resp = client.post(
        "/folders", data={"name": "Typed"}, headers={"Sec-Fetch-Site": "none"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert [row["name"] for row in _folders(conn)] == ["Ours", "Typed"]


def test_the_origin_is_compared_to_the_host_the_request_came_in_on(client, conn):
    """localhost:4242 in the address bar means Origin http://localhost:4242 -
    the same place, however it is spelled, as long as both headers agree."""
    resp = client.post(
        "/folders",
        data={"name": "Local"},
        headers={"Host": "localhost:4242", "Origin": "http://localhost:4242"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # A page at localhost posting to 127.0.0.1 is a different origin.
    resp = client.post(
        "/folders",
        data={"name": "Crossed"},
        headers={"Host": "127.0.0.1:4242", "Origin": "http://localhost:4242"},
    )
    assert resp.status_code == 403
    assert [row["name"] for row in _folders(conn)] == ["Local"]


def test_a_scripted_post_without_browser_headers_is_allowed(client, conn):
    """curl, a test, a script on this machine: no Origin, no Sec-Fetch-Site."""
    resp = client.post("/folders", data={"name": "Scripted"}, follow_redirects=False)

    assert resp.status_code == 303
    assert [row["name"] for row in _folders(conn)] == ["Scripted"]


def test_cross_site_reads_are_not_the_guards_business(client):
    """A link from another site is how a shared URL arrives; a GET changes
    nothing and is served."""
    resp = client.get("/", headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 200


def test_the_json_api_is_guarded_too(client):
    """The guard sits in front of every route, the JSON spine included, and
    answers before the route can say the job does not exist."""
    resp = client.post("/api/jobs/1/cancel", headers={"Sec-Fetch-Site": "cross-site"})

    assert resp.status_code == 403


def test_cross_site_reason_names_what_was_wrong():
    assert guard.cross_site_reason({"sec-fetch-site": "cross-site"}, "http") is not None
    assert guard.cross_site_reason({"sec-fetch-site": "same-origin"}, "http") is None
    assert guard.cross_site_reason({}, "http") is None
    assert guard.cross_site_reason({"origin": "http://127.0.0.1", "host": "127.0.0.1"}, "http") is None
    assert guard.cross_site_reason({"origin": "HTTP://LOCALHOST", "host": "localhost"}, "http") is None
    assert "evil.example" in guard.cross_site_reason(
        {"origin": "http://evil.example", "host": "127.0.0.1"}, "http"
    )
