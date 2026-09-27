"""TASK-096: the laptop's half of Receive from phone.

The door itself (secret, limit, timeout, mDNS) is tests/test_phone_door.py.
This file is about how the app holds it: the routes that open and close it
sit behind the existing guard, nothing opens it but a person, a restart finds
it closed, no route of the app can be reached through it, and a file that
comes through it lands in the library exactly like a laptop upload.

The door is the real one - a uvicorn on 127.0.0.1 and a real HTTP client -
with the LAN picker, mDNS and the clock swapped for the test's own, the way
`install_door` below does it. The supervisor is off, so jobs stay queued.
"""

from __future__ import annotations

import json
import re
import socket
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from scribe import applog, db, paths
from scribe.app import create_app
from scribe.web import phone_door, phone_ui, transcribe_dialog
from test_phone_door import FakeClock, FakePublisher, refused


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
def clock():
    return FakeClock()


@pytest.fixture
def publisher():
    return FakePublisher()


def install_door(app, clock, publisher, **kw) -> phone_door.Door:
    """The app's door, with the test's seams: 127.0.0.1, port 0, fake mDNS."""
    kw.setdefault("address_picker", lambda: "127.0.0.1")
    kw.setdefault("port", 0)
    kw.setdefault("poll", 0.01)
    kw.setdefault("seconds", phone_door.OPEN_SECONDS)
    door = phone_door.Door(phone_ui.accept_for(app), publisher=publisher, clock=clock, **kw)
    app.state.phone_door = door
    return door


@pytest.fixture
def app(db_path, data_dir):
    return create_app(db_path=db_path, start_supervisor=False)


@pytest.fixture
def client(app, clock, publisher):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        install_door(app, clock, publisher)
        yield client
        app.state.phone_door.close()


def door_of(client) -> phone_door.Door:
    return client.app.state.phone_door


def http() -> httpx.Client:
    return httpx.Client(timeout=10.0)


def send(url: str, name: str, data: bytes) -> httpx.Response:
    with http() as c:
        return c.post(url, files={phone_door.FILE_FIELD: (name, data, "audio/mp4")})


HX = {"HX-Request": "true"}


# --- the control on the laptop ------------------------------------------------------


def test_the_library_offers_receive_from_phone(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Receive from phone" in page.text
    assert 'hx-get="/phone"' in page.text
    assert 'id="phone-dialog"' in page.text


def test_the_panel_of_a_closed_door_offers_to_open_it_and_opens_nothing(client, publisher):
    panel = client.get("/phone", headers=HX)
    assert panel.status_code == 200
    assert 'hx-post="/phone/open"' in panel.text
    assert "15 minutes" in panel.text
    assert door_of(client).is_open() is False
    assert publisher.published == []


def test_without_scripting_the_panel_is_a_page_with_a_plain_form(client):
    page = client.get("/phone")
    assert page.status_code == 200
    assert "<html" in page.text.lower()
    assert 'action="/phone/open"' in page.text


def test_opening_shows_the_qr_code_both_urls_a_countdown_and_close(client):
    panel = client.post("/phone/open", headers=HX)
    assert panel.status_code == 200
    opening = door_of(client).opening
    assert opening is not None
    html = panel.text
    assert "<svg" in html
    assert opening.mdns_url in html
    assert opening.ip_url in html
    assert "15:00" in html
    assert 'hx-get="/phone/countdown"' in html
    assert 'hx-post="/phone/close"' in html


def test_the_qr_code_carries_the_ip_address_url(client):
    """Scanned, not typed: the address that needs no mDNS is the one that
    always works (the name is shown for typing)."""
    import segno

    client.post("/phone/open", headers=HX)
    opening = door_of(client).opening
    expected = segno.make(opening.ip_url, error="m").svg_inline(scale=4)
    panel = client.get("/phone", headers=HX)
    assert expected in panel.text


def test_the_countdown_follows_the_clock(client, clock):
    client.post("/phone/open", headers=HX)
    clock.advance(75)
    tick = client.get("/phone/countdown", headers=HX)
    assert tick.status_code == 200
    assert "13:45" in tick.text


def test_the_countdown_of_a_closed_door_stops_polling_and_shows_the_closed_panel(client):
    client.post("/phone/open", headers=HX)
    door_of(client).close()
    tick = client.get("/phone/countdown", headers=HX)
    assert tick.status_code == 286  # htmx: stop polling
    assert tick.headers["HX-Retarget"] == "#phone-panel"
    assert 'hx-post="/phone/open"' in tick.text


def test_close_closes_the_door_and_the_port(client, publisher):
    client.post("/phone/open", headers=HX)
    opening = door_of(client).opening
    panel = client.post("/phone/close", headers=HX)
    assert panel.status_code == 200
    assert door_of(client).is_open() is False
    assert refused("127.0.0.1", opening.port)
    assert publisher.withdrawn == 1
    assert 'hx-post="/phone/open"' in panel.text


def test_a_failed_mdns_publish_is_said_and_the_ip_url_still_shown(app, clock):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        install_door(app, clock, FakePublisher(fail=OSError("no multicast")))
        try:
            panel = client.post("/phone/open", headers=HX)
            opening = door_of(client).opening
            assert "myscribe.local could not be published" in panel.text
            assert opening.ip_url in panel.text
            with http() as c:
                assert c.get(opening.ip_url).status_code == 200
        finally:
            door_of(client).close()


def test_no_network_is_a_sentence_on_the_panel(app, clock, publisher):
    with TestClient(app, base_url="http://127.0.0.1") as client:
        install_door(app, clock, publisher, address_picker=lambda: None)
        panel = client.post("/phone/open", headers=HX)
        assert panel.status_code == 200
        assert "no network address a phone could reach" in panel.text
        assert door_of(client).is_open() is False


@pytest.mark.skipif(sys.platform != "win32", reason="the firewall sentence is Windows's")
def test_the_panel_says_what_windows_firewall_will_ask(client):
    panel = client.post("/phone/open", headers=HX)
    assert "Windows" in panel.text and "firewall" in panel.text.lower()


# --- only the laptop's own browser can open or close it --------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
    ids=["foreign origin", "cross-site fetch"],
)
@pytest.mark.parametrize("route", ["/phone/open", "/phone/close"])
def test_a_page_from_elsewhere_cannot_open_or_close_the_door(client, publisher, headers, route):
    if route == "/phone/close":
        client.post("/phone/open", headers=HX)
    before = door_of(client).is_open()
    answer = client.post(route, headers=headers)
    assert answer.status_code == 403
    assert door_of(client).is_open() is before
    if route == "/phone/open":
        assert publisher.published == []


@pytest.mark.parametrize("host", ["myscribe.local", "192.168.1.23", "myscribe.local:4243"])
def test_the_main_app_still_refuses_every_other_host(client, host):
    assert client.get("/", headers={"Host": host}).status_code == 400
    assert client.post("/phone/open", headers={"Host": host}).status_code == 400
    assert door_of(client).is_open() is False


def test_the_main_app_still_binds_loopback_only():
    from scribe import __main__ as main
    from scribe import guard

    assert main.HOST == "127.0.0.1"
    assert guard.ALLOWED_HOSTS == ("127.0.0.1", "localhost")


# --- nothing of the app is reachable through the door ------------------------------------


def _routes(routes, prefix: str = ""):
    """(path, route) for every route, into included routers as well.

    FastAPI 0.141 keeps an included router as one `_IncludedRouter` entry in
    ``app.routes`` rather than copying its routes in, so a flat walk sees
    only the JSON spine and none of the pages.
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            inner = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            yield from _routes(original.routes, prefix + inner)
            continue
        path = getattr(route, "path", None)
        if path is not None:
            yield prefix + path, route


def _app_paths(app) -> list[str]:
    """Every route of the main app as a concrete path, parameters as 1."""
    found = []
    for path, route in _routes(app.routes):
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        if isinstance(route, Mount):
            concrete = concrete.rstrip("/") + "/app.css"
        found.append(concrete or "/")
    return sorted(set(found))


def test_no_route_of_the_main_app_answers_through_the_door(client):
    client.post("/phone/open", headers=HX)
    opening = door_of(client).opening
    paths_ = _app_paths(client.app)
    for known in ("/", "/api/media", "/phone/open", "/transcribe/upload", "/media/1/download", "/static/app.css"):
        assert known in paths_, known
    assert len(paths_) > 50, "the walk found too few routes to prove anything"
    base = f"http://127.0.0.1:{opening.port}"
    with http() as c:
        for path in paths_:
            for prefix in ("", f"/{opening.secret}"):
                if prefix + path == opening.path:
                    continue  # the library's "/" behind the secret is the door's own page
                for method in ("GET", "POST"):
                    answer = c.request(method, base + prefix + path)
                    assert answer.status_code == 404, (method, prefix and "<secret>", path)
    assert door_of(client).is_open() is True


# --- nothing listens until a person opens it; a restart finds it closed --------------------


def test_starting_the_app_opens_no_door(db_path, data_dir, monkeypatch):
    started = []
    published = []

    class SpyListener(phone_door.UvicornListener):
        def start(self, timeout=10.0):  # pragma: no cover - must not run
            started.append(True)
            super().start(timeout)

    class SpyPublisher(phone_door.ZeroconfPublisher):
        def publish(self, *a):  # pragma: no cover - must not run
            published.append(a)

    monkeypatch.setattr(phone_door, "UvicornListener", SpyListener)
    monkeypatch.setattr(phone_door, "ZeroconfPublisher", SpyPublisher)
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.get("/")
        client.get("/phone", headers=HX)
        door = client.app.state.phone_door
        assert isinstance(door, phone_door.Door)
        assert door.is_open() is False
    assert started == []
    assert published == []
    # And on the wire: nothing answers on the door's port at this laptop's
    # own LAN address (read, not bound: lan_address() only asks the routing
    # table).
    address = phone_door.lan_address()
    if address is not None:
        assert refused(address, phone_door.DEFAULT_PORT)


def test_the_default_door_binds_the_lan_address_on_4243_and_publishes_with_zeroconf(app):
    door = app.state.phone_door
    assert door._port == phone_door.DEFAULT_PORT == 4243
    assert door._pick is phone_door.lan_address
    assert isinstance(door._publisher, phone_door.ZeroconfPublisher)


def test_a_restart_finds_the_door_closed_and_the_old_port_released(db_path, data_dir, clock, publisher):
    first = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(first, base_url="http://127.0.0.1") as client:
        install_door(first, clock, publisher)
        client.post("/phone/open", headers=HX)
        opening = door_of(client).opening
        assert opening is not None
        # Nobody presses Close: the app stops with the door open.
    assert refused("127.0.0.1", opening.port)
    assert publisher.withdrawn == 1

    second = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(second, base_url="http://127.0.0.1") as client:
        assert client.app.state.phone_door.is_open() is False
        conn = db.connect(db_path)
        try:
            rows = conn.execute("SELECT key FROM setting WHERE key LIKE '%phone%'").fetchall()
        finally:
            conn.close()
        assert rows == []


# --- what comes through lands in the library like a laptop upload -------------------------------


def _setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def test_a_file_through_the_door_becomes_a_recording_with_a_job_on_the_stored_defaults(client, monkeypatch):
    conn = client.app.state.conn
    with db.LOCK:
        _setting(conn, transcribe_dialog.SETTING_LANGUAGE, "nl")
        _setting(conn, transcribe_dialog.SETTING_TIER, "max")
        _setting(conn, transcribe_dialog.SETTING_DIARIZE, "0")
    expected = transcribe_dialog.read_defaults(conn).to_params()

    calls = []
    real = transcribe_dialog.file_upload

    def spy(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(transcribe_dialog, "file_upload", spy)

    client.post("/phone/open", headers=HX)
    opening = door_of(client).opening
    answer = send(opening.ip_url, "Meeting.m4a", b"phone audio bytes")
    assert answer.status_code == 200
    assert "Received Meeting.m4a; MyScribe is transcribing it on the laptop." in answer.text

    assert len(calls) == 1 and calls[0]["via"] == "phone"
    with db.LOCK:
        media = conn.execute("SELECT * FROM media").fetchall()
        jobs = conn.execute("SELECT * FROM job").fetchall()
        settings = dict(conn.execute("SELECT key, value FROM setting").fetchall())
    assert len(media) == 1
    assert media[0]["orig_name"] == "Meeting.m4a"
    assert media[0]["folder_id"] is None
    assert (paths.DATA_DIR / media[0]["store_path"]).read_bytes() == b"phone audio bytes"
    assert len(jobs) == 1
    assert jobs[0]["type"] == "transcribe" and jobs[0]["status"] == "queued"
    assert jobs[0]["media_id"] == media[0]["id"]
    assert json.loads(jobs[0]["params_json"]) == expected
    # The phone uses the defaults; it does not choose new ones.
    assert settings[transcribe_dialog.SETTING_TIER] == "max"
    assert settings[transcribe_dialog.SETTING_LANGUAGE] == "nl"

    # The door closed behind the file (one recording per opening); the panel
    # still says what came in.
    assert door_of(client).wait_closed(10)
    panel = client.get("/phone", headers=HX)
    assert "Meeting.m4a" in panel.text


def _received_and_closed(client, name="Meeting.m4a"):
    client.post("/phone/open", headers=HX)
    opening = door_of(client).opening
    assert send(opening.ip_url, name, b"phone audio bytes").status_code == 200
    assert door_of(client).wait_closed(10)
    return opening


def test_a_door_closed_on_a_received_file_says_so_and_reloads_the_library_after_3_seconds(client):
    _received_and_closed(client)
    tick = client.get("/phone/countdown", headers=HX)
    assert tick.status_code == phone_ui.STOP_POLLING
    assert tick.headers["HX-Retarget"] == "#phone-panel"
    text = tick.text
    assert "Received Meeting.m4a" in text
    # No supervisor runs in this test, so the job is still waiting its turn.
    assert "Transcription is queued" in text
    # Three seconds on screen, then one request that reloads the library.
    assert 'hx-get="/phone/done"' in text
    assert 'hx-trigger="load delay:3s"' in text
    done = client.get("/phone/done", headers=HX)
    assert done.status_code == 200
    assert done.headers.get("HX-Refresh") == "true"


def test_a_running_transcription_is_named_as_started(client):
    _received_and_closed(client)
    conn = client.app.state.conn
    with db.LOCK:
        conn.execute("UPDATE job SET status = 'running'")
        conn.commit()
    tick = client.get("/phone/countdown", headers=HX)
    assert "Transcription has started" in tick.text


def test_a_door_that_timed_out_does_not_reload_the_library(client, clock):
    client.post("/phone/open", headers=HX)
    clock.advance(15 * 60)
    tick = client.get("/phone/countdown", headers=HX)
    assert tick.status_code == phone_ui.STOP_POLLING
    assert "/phone/done" not in tick.text


def test_opening_the_panel_later_does_not_reload_the_library(client):
    # The reload belongs to the moment the file arrived, not to every later
    # look at the panel: that would reload the library in a loop.
    _received_and_closed(client)
    panel = client.get("/phone", headers=HX)
    assert "Meeting.m4a" in panel.text
    assert "/phone/done" not in panel.text


def test_the_library_after_the_reload_shows_the_recording(client):
    _received_and_closed(client)
    page = client.get("/")
    assert "Meeting.m4a" in page.text


def test_a_refused_file_leaves_no_row_and_nothing_in_the_store(client):
    client.post("/phone/open", headers=HX)
    opening = door_of(client).opening
    answer = send(opening.ip_url, "notes.txt", b"hello")
    assert answer.status_code == 415
    conn = client.app.state.conn
    with db.LOCK:
        assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
    incoming = paths.MEDIA_DIR / ".incoming"
    assert not incoming.exists() or list(incoming.iterdir()) == []


def test_the_laptop_upload_still_goes_through_the_same_helper(client, monkeypatch):
    calls = []
    real = transcribe_dialog.file_upload

    def spy(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)

    monkeypatch.setattr(transcribe_dialog, "file_upload", spy)
    answer = client.post(
        "/transcribe/upload",
        files={transcribe_dialog.FILES_FIELD: ("desk.m4a", b"desk bytes", "audio/mp4")},
        data={"tier": "turbo"},
    )
    assert answer.status_code in (200, 303)
    assert len(calls) == 1 and calls[0].get("via", "upload") == "upload"


def test_the_secret_is_not_in_the_app_log(client):
    client.post("/phone/open", headers=HX)
    opening = door_of(client).opening
    send(opening.ip_url, "memo.m4a", b"x")
    assert door_of(client).wait_closed(10)  # the file closes the door behind it
    client.get("/phone/countdown", headers=HX)
    client.post("/phone/close", headers=HX)
    text = applog.path().read_text(encoding="utf-8")
    assert "phone.open" in text and "phone.close" in text and "ingest.phone" in text
    assert opening.secret not in text


def test_a_countdown_that_says_closed_means_the_port_is_closed(app, clock, publisher):
    """Found in the real run on 2026-09-26: the countdown answered 286 at
    60.3 s while the listener still accepted, because it read the clock and
    the watchdog had not ticked yet. The watchdog here never ticks (poll one
    hour), so only the countdown itself can close the door."""
    with TestClient(app, base_url="http://127.0.0.1") as client:
        install_door(app, clock, publisher, poll=3600)
        client.post("/phone/open", headers=HX)
        opening = door_of(client).opening
        clock.advance(15 * 60)
        tick = client.get("/phone/countdown", headers=HX)
        assert tick.status_code == 286
        assert door_of(client).is_open() is False
        assert refused("127.0.0.1", opening.port)
        assert publisher.withdrawn == 1
