"""TASK-096: the door a phone sends a recording through.

`scribe.web.phone_door` is a second listener, separate from the app: it
answers one secret path with an upload form, hands an accepted file to a
callback, and closes by itself. Everything here runs it for real - a
uvicorn server on 127.0.0.1, a real HTTP client - with the three things that
would reach outside the test behind seams: the LAN-address picker (a lambda
answering 127.0.0.1), mDNS (a fake publisher) and the clock (a number the
test moves). Nothing binds 0.0.0.0 and nothing sends a multicast packet.

The laptop's half - the routes behind the guard, the panel, the ingest into
the library - is tests/test_web_phone.py.
"""

from __future__ import annotations

import json
import logging
import secrets
import socket
import threading

import httpx
import pytest

from scribe import applog, paths
from scribe.web import phone_door


class FakeClock:
    """A monotonic clock that only moves when the test says so."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


class FakePublisher:
    """Records what mDNS would have been asked; can be told to fail."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.published: list[tuple[str, str, int]] = []
        self.withdrawn = 0

    def publish(self, host: str, address: str, port: int) -> None:
        if self.fail is not None:
            raise self.fail
        self.published.append((host, address, port))

    def withdraw(self) -> None:
        self.withdrawn += 1


class Received:
    """The accept callback: what the door handed on, bytes and name."""

    def __init__(self) -> None:
        self.files: list[tuple[str, bytes]] = []

    def __call__(self, stream, name: str) -> None:
        self.files.append((name, stream.read()))


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """The app log in tmp_path, so a test can read what the door wrote."""
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    return tmp_path / "logs"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
def received():
    return Received()


def make_door(received, clock, publisher, **kw) -> phone_door.Door:
    kw.setdefault("address_picker", lambda: "127.0.0.1")
    kw.setdefault("port", 0)
    kw.setdefault("poll", 0.01)
    kw.setdefault("seconds", phone_door.OPEN_SECONDS)
    return phone_door.Door(received, publisher=publisher, clock=clock, **kw)


@pytest.fixture
def door(received, clock, publisher, logs):
    d = make_door(received, clock, publisher)
    yield d
    d.close()


def http() -> httpx.Client:
    # Windows takes about two seconds to refuse a connection to a closed
    # localhost port (it retries the SYN), so the timeout sits well above it.
    return httpx.Client(timeout=10.0)


def refused(address: str, port: int) -> bool:
    """True when nothing accepts a TCP connection on address:port."""
    try:
        with socket.create_connection((address, port), timeout=10.0):
            return False
    except OSError:
        return True


def read_log(logs) -> list[dict]:
    path = logs / applog.FILE_NAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def upload(url: str, name: str, data: bytes, content_type: str = "audio/mp4") -> httpx.Response:
    with http() as client:
        return client.post(url, files={phone_door.FILE_FIELD: (name, data, content_type)})


# --- nothing listens until the door is opened ------------------------------------


def test_a_door_that_was_built_listens_nowhere_and_publishes_nothing(received, clock, publisher, logs):
    started = []

    def spy_listener(sock, app):
        started.append(sock)
        return phone_door.UvicornListener(sock, app)

    d = make_door(received, clock, publisher, listener_factory=spy_listener)
    try:
        assert d.opening is None
        assert d.is_open() is False
        assert started == []
        assert publisher.published == []
    finally:
        d.close()


# --- the phone page ----------------------------------------------------------------


def test_the_secret_path_answers_a_plain_upload_form(door):
    opening = door.open()
    with http() as client:
        page = client.get(opening.ip_url)
    assert page.status_code == 200
    html = page.text
    assert 'enctype="multipart/form-data"' in html
    assert 'method="post"' in html
    assert 'type="file"' in html
    assert f'name="{phone_door.FILE_FIELD}"' in html
    # No accept filter: Safari on an iPhone greyed out Robert's audio file
    # under accept="audio/*,video/*" (2026-09-26), so the picker offers every
    # file and the door's own extension check refuses with a sentence.
    assert "accept=" not in html
    assert 'type="submit"' in html
    # Nothing from the internet: it has to work in Safari with nothing but
    # the laptop to talk to. One small inline script draws the progress bar
    # (Robert, 2026-09-26); no script is loaded from anywhere, and without it
    # the form above still posts as it is.
    assert "<progress" in html
    assert html.lower().count("<script") == 1
    assert "<script src" not in html.lower() and " src=" not in html.lower()
    assert "http://" not in html and "https://" not in html
    # The script posts back to the page's own path and counts upload bytes.
    assert "xhr.upload" in html
    assert "location.pathname" in html
    assert "<link" not in html.lower()


def test_the_urls_name_the_address_the_port_and_the_secret(door):
    opening = door.open()
    assert opening.address == "127.0.0.1"
    assert opening.port > 0
    assert opening.ip_url == f"http://127.0.0.1:{opening.port}/{opening.secret}/"
    assert opening.mdns_url == f"http://{phone_door.MDNS_HOST}:{opening.port}/{opening.secret}/"
    assert phone_door.MDNS_HOST == "myscribe.local"


# --- the secret ------------------------------------------------------------------


def test_the_secret_comes_from_the_secrets_module_and_is_long(door, monkeypatch):
    asked = []
    real = secrets.token_urlsafe

    def spy(nbytes=None):
        asked.append(nbytes)
        return real(nbytes)

    monkeypatch.setattr(phone_door.secrets, "token_urlsafe", spy)
    opening = door.open()
    assert asked and asked[0] >= 24
    assert len(opening.secret) >= 32


def test_every_opening_has_a_new_secret_and_the_old_one_stops_working(door):
    first = door.open()
    door.close()
    second = door.open()
    assert second.secret != first.secret
    old_url = f"http://127.0.0.1:{second.port}/{first.secret}/"
    with http() as client:
        assert client.get(old_url).status_code == 404
        assert client.get(second.ip_url).status_code == 200


def test_opening_an_open_door_keeps_the_secret_the_phone_already_has(door, publisher):
    first = door.open()
    again = door.open()
    assert again == first
    assert len(publisher.published) == 1


def test_the_path_is_compared_in_constant_time(door, monkeypatch):
    opening = door.open()
    compared = []
    real = phone_door.hmac.compare_digest

    def spy(a, b):
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(phone_door.hmac, "compare_digest", spy)
    with http() as client:
        client.get(opening.ip_url)
    assert compared, "the secret path was not compared with hmac.compare_digest"


def _wrong_paths(secret: str) -> dict[str, str]:
    """Every way a path can fail to be exactly the secret."""
    flipped = ("A" if secret[0] != "A" else "B") + secret[1:]
    return {
        "no secret": "/",
        "another secret of the same length": f"/{flipped}/",
        "the secret without its slash": f"/{secret}",
        "the secret with a page after it": f"/{secret}/health",
        "the secret in other case": f"/{secret.swapcase()}/",
        "the secret one character short": f"/{secret[:-1]}/",
        "the secret as a query": f"/?secret={secret}",
        "a main-app route": "/api/jobs",
    }


@pytest.mark.parametrize("case", sorted(_wrong_paths("x" * 43)))
def test_a_get_without_the_exact_secret_is_a_404(door, case):
    opening = door.open()
    path = _wrong_paths(opening.secret)[case]
    with http() as client:
        answer = client.get(f"http://127.0.0.1:{opening.port}{path}")
    assert answer.status_code == 404, case
    assert opening.secret not in answer.text


@pytest.mark.parametrize("case", sorted(_wrong_paths("x" * 43)))
def test_a_post_without_the_exact_secret_is_a_404_and_hands_nothing_on(door, received, case):
    opening = door.open()
    path = _wrong_paths(opening.secret)[case]
    answer = upload(f"http://127.0.0.1:{opening.port}{path}", "memo.m4a", b"sound")
    assert answer.status_code == 404, case
    assert received.files == []
    assert door.received == []


# --- what the door takes ----------------------------------------------------------


def test_an_audio_file_is_handed_on_and_the_phone_is_told(door, received):
    opening = door.open()
    answer = upload(opening.ip_url, "Voice memo 12.m4a", b"\x00audio bytes\xff")
    assert answer.status_code == 200
    assert "Received Voice memo 12.m4a; MyScribe is transcribing it on the laptop." in answer.text
    assert received.files == [("Voice memo 12.m4a", b"\x00audio bytes\xff")]
    assert door.received == ["Voice memo 12.m4a"]


def test_an_accepted_file_closes_the_door_after_the_answer(door, publisher, logs):
    # Robert, 2026-09-26: one recording per opening. The phone gets its
    # answer first; then the port is released and the name withdrawn.
    opening = door.open()
    answer = upload(opening.ip_url, "Voice memo 12.m4a", b"audio")
    assert answer.status_code == 200
    assert "Received Voice memo 12.m4a" in answer.text
    assert door.wait_closed(10)
    assert door.is_open() is False
    assert door.closed_reason == "received"
    assert refused("127.0.0.1", opening.port)
    assert publisher.withdrawn == 1
    assert door.received == ["Voice memo 12.m4a"]
    closes = [e for e in read_log(logs) if e["event"] == "phone.close"]
    assert [(e["reason"], e["received"]) for e in closes] == [("received", 1)]


def test_close_returns_only_when_a_close_already_under_way_has_finished(received, clock, publisher, logs):
    # Found by this file's own run: the door closing itself after a file was
    # still stopping its listener when the fixture's close() came back at
    # once, and its phone.close landed in the next test's log.
    started, release = threading.Event(), threading.Event()

    class SlowListener(phone_door.UvicornListener):
        def stop(self, timeout: float = 15.0) -> None:
            started.set()
            release.wait(10)
            super().stop(timeout)

    d = make_door(received, clock, publisher, listener_factory=SlowListener)
    opening = d.open()
    first = threading.Thread(target=d.close, args=("received",))
    first.start()
    assert started.wait(10)
    second_done = threading.Event()
    threading.Thread(target=lambda: (d.close(), second_done.set())).start()
    assert second_done.wait(0.5) is False  # still waiting for the first
    release.set()
    assert second_done.wait(10)
    first.join(10)
    assert refused("127.0.0.1", opening.port)
    assert [e["reason"] for e in read_log(logs) if e["event"] == "phone.close"] == ["received"]


def test_what_the_accept_callback_returns_is_kept_for_the_laptop(received, clock, publisher, logs):
    # The laptop's panel names the recording's job; the door does not know
    # what a job is, it keeps whatever the callback answered.
    def accept(stream, name):
        stream.read()
        return 42

    d = make_door(accept, clock, publisher)
    try:
        opening = d.open()
        assert upload(opening.ip_url, "a.m4a", b"x").status_code == 200
        assert d.wait_closed(10)
        assert d.results == [42]
    finally:
        d.close()


def test_a_refused_file_leaves_the_door_open_for_another_try(door, received):
    opening = door.open()
    assert upload(opening.ip_url, "notes.txt", b"hello", "text/plain").status_code == 415
    assert door.wait_closed(0.5) is False
    assert door.is_open() is True
    answer = upload(opening.ip_url, "memo.m4a", b"audio")
    assert answer.status_code == 200
    assert door.wait_closed(10)


def test_a_video_file_is_taken_too(door, received):
    opening = door.open()
    answer = upload(opening.ip_url, "IMG_0042.MOV", b"video", "video/quicktime")
    assert answer.status_code == 200
    assert [name for name, _ in received.files] == ["IMG_0042.MOV"]


def test_a_file_that_is_not_audio_or_video_is_refused_with_a_sentence(door, received):
    opening = door.open()
    answer = upload(opening.ip_url, "notes.txt", b"hello", "text/plain")
    assert answer.status_code == 415
    assert "notes.txt is not an audio or video file" in answer.text
    assert received.files == []


def test_a_post_without_a_file_is_refused_with_a_sentence(door, received):
    opening = door.open()
    with http() as client:
        answer = client.post(opening.ip_url, data={"other": "x"}, files={"unrelated": ("", b"", "application/octet-stream")})
    assert answer.status_code == 400
    assert "Choose a recording" in answer.text
    assert received.files == []


def test_a_file_over_the_limit_is_refused_by_its_length(received, clock, publisher, logs):
    d = make_door(received, clock, publisher, max_bytes=1000)
    try:
        opening = d.open()
        answer = upload(opening.ip_url, "long.m4a", b"x" * 5000)
        assert answer.status_code == 413
        assert "larger than" in answer.text
        assert received.files == []
    finally:
        d.close()


def test_a_declared_length_over_the_limit_is_refused_before_the_body_is_read(received, clock, publisher, logs):
    """The header alone is enough: a phone about to send 5 GB hears no before
    it sends a byte. Only headers go out here; a door that waited for the
    body would never answer, and the read below would time out."""
    d = make_door(received, clock, publisher, max_bytes=1000)
    try:
        opening = d.open()
        request = (
            f"POST {opening.path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{opening.port}\r\n"
            "Content-Type: multipart/form-data; boundary=x\r\n"
            "Content-Length: 5000\r\n"
            "\r\n"
        ).encode("ascii")
        with socket.create_connection(("127.0.0.1", opening.port), timeout=5) as sock:
            sock.sendall(request)
            head = sock.recv(4096).decode("latin-1")
        assert head.startswith("HTTP/1.1 413")
        assert received.files == []
    finally:
        d.close()


def test_a_file_over_the_limit_is_refused_when_no_length_was_sent(received, clock, publisher, logs):
    """A chunked body has no Content-Length to trust; the bytes are counted."""
    d = make_door(received, clock, publisher, max_bytes=1000)
    try:
        opening = d.open()
        boundary = "b0undary"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{phone_door.FILE_FIELD}\";"
            f" filename=\"long.m4a\"\r\nContent-Type: audio/mp4\r\n\r\n"
        ).encode() + b"x" * 5000 + f"\r\n--{boundary}--\r\n".encode()

        def chunks():
            for i in range(0, len(body), 512):
                yield body[i:i + 512]

        with http() as client:
            answer = client.post(
                opening.ip_url,
                content=chunks(),
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )
        assert answer.status_code == 413
        assert received.files == []
    finally:
        d.close()


def test_the_limit_is_four_gibibytes():
    assert phone_door.MAX_UPLOAD_BYTES == 4 * 1024**3


def test_the_received_name_is_escaped(door):
    opening = door.open()
    # No slash in it: a slash makes the rest a path, and only the last part
    # is the name.
    answer = upload(opening.ip_url, "<img src=x onerror=alert(1)>.m4a", b"a")
    assert answer.status_code == 200
    assert "<img src=x" not in answer.text
    assert "&lt;img src=x onerror=alert(1)&gt;.m4a" in answer.text


# --- closing ----------------------------------------------------------------------


def test_the_door_stays_open_until_the_last_second_of_fifteen_minutes(door, clock):
    opening = door.open()
    assert opening.closes_at - clock() == 15 * 60
    clock.advance(15 * 60 - 1)
    assert door.wait_closed(timeout=0.2) is False
    with http() as client:
        assert client.get(opening.ip_url).status_code == 200


def test_the_door_closes_by_itself_after_fifteen_minutes(door, clock, publisher):
    opening = door.open()
    clock.advance(15 * 60)
    assert door.wait_closed(timeout=10.0), "the watchdog did not close the door"
    assert door.is_open() is False
    assert refused("127.0.0.1", opening.port)
    assert publisher.withdrawn == 1


def test_a_request_after_the_deadline_is_a_404_before_the_watchdog_looks(received, clock, publisher, logs):
    d = make_door(received, clock, publisher, poll=3600)
    try:
        opening = d.open()
        clock.advance(15 * 60)
        with http() as client:
            assert client.get(opening.ip_url).status_code == 404
        assert upload(opening.ip_url, "late.m4a", b"a").status_code == 404
        assert received.files == []
    finally:
        d.close()


def test_closing_on_request_stops_the_listener_and_withdraws_the_name(door, publisher):
    opening = door.open()
    door.close()
    assert door.is_open() is False
    assert refused("127.0.0.1", opening.port)
    assert publisher.withdrawn == 1


def test_closing_twice_is_harmless(door, publisher):
    door.open()
    door.close()
    door.close()
    assert publisher.withdrawn == 1


def test_remaining_counts_down_with_the_clock(door, clock):
    door.open()
    assert door.remaining() == 15 * 60
    clock.advance(61)
    assert door.remaining() == 15 * 60 - 61
    door.close()
    assert door.remaining() == 0


def test_a_taken_port_is_a_sentence_not_a_crash(received, clock, publisher, logs):
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    try:
        d = make_door(received, clock, publisher, port=holder.getsockname()[1])
        with pytest.raises(phone_door.DoorError, match="in use"):
            d.open()
        assert d.is_open() is False
        assert publisher.published == []
    finally:
        holder.close()


def test_no_network_address_is_a_sentence(received, clock, publisher, logs):
    d = make_door(received, clock, publisher, address_picker=lambda: None)
    with pytest.raises(phone_door.DoorError, match="network"):
        d.open()
    assert d.is_open() is False


# --- how long ----------------------------------------------------------------------


def test_fifteen_minutes_is_the_default():
    assert phone_door.OPEN_SECONDS == 15 * 60
    assert phone_door.open_seconds({}) == 15 * 60


@pytest.mark.parametrize(
    "value, expected",
    [("5", 5.0), ("900", 900.0), ("99999", 900.0), ("0", 900.0), ("-3", 900.0), ("soon", 900.0)],
)
def test_the_override_can_shorten_the_door_and_never_lengthen_it(value, expected):
    assert phone_door.open_seconds({phone_door.SHORTER_ENV: value}) == expected


# --- mDNS ----------------------------------------------------------------------------


def test_the_name_is_published_while_open_and_withdrawn_on_close(door, publisher):
    opening = door.open()
    assert publisher.published == [("myscribe.local", "127.0.0.1", opening.port)]
    assert opening.mdns_error is None
    door.close()
    assert publisher.withdrawn == 1


def test_a_failed_publish_leaves_the_ip_address_working_and_says_why(received, clock, logs):
    failing = FakePublisher(fail=OSError("no multicast here"))
    d = make_door(received, clock, failing)
    try:
        opening = d.open()
        assert opening.mdns_error and "no multicast here" in opening.mdns_error
        with http() as client:
            assert client.get(opening.ip_url).status_code == 200
    finally:
        d.close()


class FakeZeroconf:
    instances: list["FakeZeroconf"] = []
    fail_next: Exception | None = None

    def __init__(self, interfaces=None, **kw):
        self.interfaces = interfaces
        self.registered = []
        self.unregistered = []
        self.closed = False
        FakeZeroconf.instances.append(self)

    def register_service(self, info, **kw):
        if FakeZeroconf.fail_next:
            raise FakeZeroconf.fail_next
        self.registered.append(info)

    def unregister_service(self, info):
        self.unregistered.append(info)

    def close(self):
        self.closed = True


def test_the_zeroconf_publisher_announces_myscribe_local_on_the_lan_address():
    FakeZeroconf.instances = []
    FakeZeroconf.fail_next = None
    pub = phone_door.ZeroconfPublisher(zeroconf_factory=FakeZeroconf)
    pub.publish("myscribe.local", "192.168.1.23", 4243)
    zc = FakeZeroconf.instances[-1]
    assert zc.interfaces == ["192.168.1.23"]
    (info,) = zc.registered
    assert info.server == "myscribe.local."
    assert info.port == 4243
    assert info.addresses == [socket.inet_aton("192.168.1.23")]
    pub.withdraw()
    assert zc.unregistered == [info]
    assert zc.closed is True


def test_a_zeroconf_that_cannot_register_is_closed_and_the_error_raised():
    FakeZeroconf.instances = []
    FakeZeroconf.fail_next = OSError("port 5353 taken")
    try:
        pub = phone_door.ZeroconfPublisher(zeroconf_factory=FakeZeroconf)
        with pytest.raises(OSError, match="5353"):
            pub.publish("myscribe.local", "192.168.1.23", 4243)
        assert FakeZeroconf.instances[-1].closed is True
        pub.withdraw()  # nothing registered, nothing to do, no error
    finally:
        FakeZeroconf.fail_next = None


# --- the LAN address -------------------------------------------------------------------


class FakeUdp:
    def __init__(self, answer=None, fail=None):
        self.answer = answer
        self.fail = fail
        self.connected = None
        self.sent = False
        self.closed = False
        self.family = None

    def __call__(self, family, kind):
        self.family = (family, kind)
        return self

    def connect(self, target):
        if self.fail:
            raise self.fail
        self.connected = target

    def getsockname(self):
        return (self.answer, 50000)

    def send(self, *a):  # pragma: no cover - must never be called
        self.sent = True

    sendto = send

    def close(self):
        self.closed = True


def test_the_lan_address_is_the_one_the_default_route_would_use():
    udp = FakeUdp(answer="192.168.1.23")
    assert phone_door.lan_address(socket_factory=udp) == "192.168.1.23"
    assert udp.family == (socket.AF_INET, socket.SOCK_DGRAM)
    assert udp.connected[0] == "192.0.2.1"
    assert udp.sent is False
    assert udp.closed is True


@pytest.mark.parametrize("answer", ["127.0.0.1", "169.254.10.20", "0.0.0.0"])
def test_an_address_a_phone_cannot_reach_is_no_address(answer):
    assert phone_door.lan_address(socket_factory=FakeUdp(answer=answer)) is None


def test_no_route_is_no_address():
    udp = FakeUdp(fail=OSError("network is unreachable"))
    assert phone_door.lan_address(socket_factory=udp) is None
    assert udp.closed is True


# --- the secret is never logged ------------------------------------------------------------


def test_the_secret_appears_in_no_log(received, clock, publisher, logs, caplog, capfd):
    # The main app's uvicorn gives uvicorn.access a handler; the door runs in
    # the same process, so the test gives it one too.
    access = logging.getLogger("uvicorn.access")
    handler = logging.StreamHandler()
    access.addHandler(handler)
    old_level = access.level
    access.setLevel(logging.DEBUG)
    caplog.set_level(logging.DEBUG)
    d = make_door(received, clock, publisher)
    try:
        opening = d.open()
        with http() as client:
            client.get(opening.ip_url)
            client.get(f"http://127.0.0.1:{opening.port}/wrong/")
        # The refusal first: an accepted file closes the door behind it.
        upload(opening.ip_url, "notes.txt", b"a")
        upload(opening.ip_url, "memo.m4a", b"a")
        d.wait_closed(10)
        d.close()
    finally:
        access.removeHandler(handler)
        access.setLevel(old_level)
        d.close()
    secret = opening.secret
    captured = capfd.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    # httpx and httpcore are the test's client - the phone's side - and log the
    # URL they fetch; everything else in the process is MyScribe's.
    ours = [r for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))]
    assert all(secret not in r.getMessage() for r in ours)
    assert all(secret not in str(r.args) for r in ours)
    log_text = "".join(p.read_text(encoding="utf-8") for p in logs.glob("*.log*"))
    assert "phone.open" in log_text, "the door logged nothing at all; this test proved nothing"
    assert secret not in log_text
