"""A proxy on this machine: never between MyScribe and its own loopback, and
never shown with the password somebody put in its URL (TASK-089.05).

Measured on 2026-09-21 with a running Ollama and an answering app
(`docs/superpowers/specs/2026-09-20-installer-evidence/probe_product_proxy.py`,
`.output-2026-09-21.txt`): with HTTP_PROXY and HTTPS_PROXY at a closed port and
no NO_PROXY, `OllamaProvider.available()` spent its whole 2 s budget and
answered "Ollama is not running" for a daemon that was running, and the
launcher's `running_instance()` answered False after 1.08 s for an app that was
answering. A running Ollama reading as absent is what an offer to install it
would be gated on (ADR-017), so this is not a cosmetic failure.

Three things here are deliberate, and each of them is a way the first attempts
got the opposite answer:

* **A real socket, not `httpx2.MockTransport`.** The house style in
  tests/test_llm_ollama.py is a mock transport, and a mock transport never
  opens a socket - so a proxy variable is invisible to it and these tests would
  have been green before the fix.
* **The assertion is that the request arrived**, not what the probe concluded.
  A stand-in daemon can answer `(False, ...)` for its own reasons, before and
  after the fix.
* **A fresh provider per case, and no default opener carried between them.**
  `OllamaProvider` caches its client (`scribe/llm/ollama.py:361-375`) and httpx
  reads the proxy variables when the client is *built*; `urllib.request` keeps
  its default opener in the module with the proxies it was built from. Either
  one measures the first environment twice.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import socket
import threading
import urllib.request
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribe import credentials, db, models, paths, setup
from scribe.llm import ollama

LAUNCHER_PATH = Path(__file__).resolve().parent.parent / "packaging" / "launcher" / "myscribe_launcher.py"
_spec = importlib.util.spec_from_file_location("myscribe_launcher", LAUNCHER_PATH)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)

DEAD_PROXY = "http://127.0.0.1:9"
"""Port 9 is discard, and nothing listens on it here - the same closed port the
probe of 2026-09-21 used, so a run of these tests and that output compare."""

PROXY_NAMES = (
    "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
)

SENTINEL = "SENTINEL-never-printed"
"""The marker every planted password carries, as tests/test_credentials.py
plants it. A value that leaked would bring it along."""

WITH_A_PASSWORD = f"http://user:{SENTINEL}@127.0.0.1:9"
"""A proxy URL with credentials in it, at the same closed port: what a test
plants is also unreachable, so nothing waits on a name server."""


@pytest.fixture(autouse=True)
def _no_proxy_from_this_machine(monkeypatch):
    """Nothing this machine has configured answers a test here.

    The eight spellings go, and so does `urllib`'s default opener - it is built
    on first use and keeps the proxies it was built from, so without this the
    second test in a process would measure the first one's environment.

    `credentials.system_proxies` is stubbed here and not in tests/conftest.py:
    conftest stubs the places a *credential* can hide (TASK-089.04), the
    system proxy is this task's seam, and this machine has none configured
    today - which would make a test that read it pass for a reason that is
    true only here.
    """
    for name in PROXY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "system_proxies", dict)
    urllib.request.install_opener(None)
    yield
    urllib.request.install_opener(None)


@pytest.fixture
def loopback():
    """A server on 127.0.0.1 that records the path of every request that arrives.

    It answers all three probes: `/health` the way the app does, and
    `/api/tags` and `/api/version` the way Ollama does. Anything else gets a
    404 - or a 403 when the path says "refused" - which is what lets it stand
    in for a proxy that answers for a host it will not fetch.
    """
    arrived: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the stdlib's spelling
            arrived.append(self.path)
            body = json.dumps({
                "ok": True, "version": "test",
                "models": [{
                    "name": ollama.OllamaProvider.default_model,
                    "capabilities": [ollama.CHAT_CAPABILITY],
                }],
            }).encode()
            answered = 200 if self.path in ("/health", "/api/tags", "/api/version") else 404
            self.send_response(403 if "refused" in self.path else answered)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(port=port, host=f"http://127.0.0.1:{port}", arrived=arrived)
    finally:
        server.shutdown()
        server.server_close()


# --- the loopback probes ----------------------------------------------------------


def test_the_ollama_probe_reaches_this_machine_with_a_dead_proxy_configured(loopback, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", DEAD_PROXY)

    provider = ollama.OllamaProvider(host=loopback.host)  # fresh: the client is built on first use
    try:
        answer = provider.available()
    finally:
        provider.close()

    assert loopback.arrived == ["/api/tags"], f"the probe never reached 127.0.0.1; it said {answer!r}"


def test_the_ollama_detector_reaches_this_machine_with_a_dead_proxy_configured(
    loopback, monkeypatch, tmp_path, ollama_state_unstubbed
):
    """No acceptance criterion asks for this, and it is the one that would
    catch the regression that matters (TASK-089.06 guarding TASK-089.05).

    `ollama_setup.state()` is what the install offer is gated on: absent means
    "no answer at 127.0.0.1", and a proxy in front of the loopback made a
    running daemon answer nothing at all. So a hand-rolled `httpx.get` or an
    `import requests` anywhere in that module would put an installer over
    somebody's working Ollama - and every unit test of the detector uses a mock
    transport, which never opens a socket and so cannot see a proxy variable.
    This one opens a real socket.

    The assertion is which requests *arrived*, not what the detector concluded:
    a stand-in daemon can be read as any state for its own reasons.
    """
    from scribe import ollama_setup

    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", DEAD_PROXY)
    empty = tmp_path / "no-ollama-here"
    empty.mkdir()

    found = ollama_setup.state(
        host=loopback.host, locations=(), environ={"PATH": str(empty)}
    )

    assert loopback.arrived == ["/api/tags", "/api/version"], (
        f"a request the detector makes never reached 127.0.0.1; it said {found.state!r}"
    )
    assert found.state == ollama_setup.READY


def test_the_launchers_health_probe_reaches_this_machine_with_a_dead_proxy_configured(loopback, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", DEAD_PROXY)

    answered = launcher.running_instance(loopback.port)

    assert loopback.arrived == ["/health"], "the launcher's probe never reached 127.0.0.1"
    assert answered is True


def test_the_proofs_health_probe_reaches_this_machine_with_a_dead_proxy_configured(
    loopback, monkeypatch
):
    """`setup._health` is what decides whether `--prove` may load a model.

    Its `trust_env=False` is the bypass TASK-089.05 measured for the launcher,
    and the consequence of losing it is worse here than a wrong status line:
    with a proxy variable set and no NO_PROXY the loopback GET goes to the
    proxy, `_health` answers "nothing there", the gate opens, and a model
    loads beside an app that is serving - the ADR-001 breach the gate exists
    to prevent.

    The assertion is which request arrived, the way every test in this file
    does it; the second one is that the answer was read, so a probe that
    reached the port and threw the body away cannot pass either.
    """
    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", DEAD_PROXY)

    answered = setup._health(loopback.port)

    assert loopback.arrived == ["/health"], "the proof's probe never reached 127.0.0.1"
    assert answered is not None and answered["version"] == "test"


def test_the_hub_download_still_goes_through_the_proxy(loopback, tmp_path, monkeypatch):
    """The bypass is loopback-only, and this is the test that says so.

    A user behind a corporate proxy needs that proxy for the Hugging Face hub,
    for OpenAI and for OpenRouter. The assertion that matters is the empty
    request list: `ModelError` alone would also be raised by a 404, so it
    proves nothing on its own.
    """
    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    monkeypatch.setattr(models, "HUB", loopback.host)
    model = models.Model(repo="demo/model", revision="a" * 40, files={"weights.bin": {}},
                         license="mit", credit="Demo model, MIT.", gated=False)

    with pytest.raises(models.ModelError) as raised:
        models.fetch_file(model, "weights.bin", tmp_path / "weights.bin", None, lambda _n: None)

    assert raised.value.reason == "offline"
    assert loopback.arrived == [], "the hub download bypassed the proxy; the bypass is for loopback only"


# --- what is configured, and where ------------------------------------------------


def test_every_place_a_proxy_can_be_configured_is_reported_with_its_source_and_host():
    rows = credentials.proxies(
        environ={
            "HTTP_PROXY": "http://proxy.corp:3128",
            "HTTPS_PROXY": "http://proxy.corp:3129",
            "ALL_PROXY": "socks5://socks.corp:1080",
            "NO_PROXY": "127.0.0.1,localhost",
        },
        system={"http": "http://windows.corp:8080"},
    )

    assert [(row.name, row.source, row.host) for row in rows] == [
        ("HTTP_PROXY", "HTTP_PROXY (environment)", "proxy.corp:3128"),
        ("HTTPS_PROXY", "HTTPS_PROXY (environment)", "proxy.corp:3129"),
        ("ALL_PROXY", "ALL_PROXY (environment)", "socks.corp:1080"),
        ("NO_PROXY", "NO_PROXY (environment)", "127.0.0.1,localhost"),
        ("http", "http (Windows system proxy)", "windows.corp:8080"),
    ]


@pytest.mark.parametrize("value,expected", [
    ("http://proxy.corp:3128", "proxy.corp:3128"),
    ("proxy.corp:3128", "proxy.corp:3128"),
    ("socks5://socks.corp:1080", "socks.corp:1080"),
    ("http://[::1]:3128", "[::1]:3128"),
    ("http://user:123/pw@proxy.corp", credentials.NOT_A_HOST),
    ("http://user:123?pw@proxy.corp", credentials.NOT_A_HOST),
    ("http://user:123#pw@proxy.corp", credentials.NOT_A_HOST),
])
def test_a_proxy_value_is_read_or_refused_but_never_half_read(value, expected):
    """Every spelling both copies have to agree on, in one table.

    The last three are why this table exists. `urlsplit` ends the authority at
    the first `/`, `?` or `#`, so a proxy URL whose *password* contains one
    parses with the user name as its host: `http://user:123/pw@proxy.corp` was
    reported as `user:123`, which is a name and the front of a password. A
    proxy URL has no path, so those are refused. The bare `proxy.corp:3128` is
    the spelling urllib's own parser honours, and an IPv6 address keeps its
    brackets or `::1:3128` is not a form anybody can paste back.
    """
    rows = credentials.proxies(environ={"HTTP_PROXY": value}, system={})

    assert [row.host for row in rows] == [expected]
    assert launcher.proxy_host({"HTTP_PROXY": value}) == expected


def test_no_proxy_keeps_the_prefix_of_a_cidr_block_and_refuses_what_is_not_a_pattern():
    """NO_PROXY is a list of host patterns, and a CIDR block is one of them.

    `urlsplit` ends the authority at the `/`, so `10.0.0.0/8` came back as
    `10.0.0.0` - a row claiming a configuration this machine does not have.
    The prefix length is reattached when it is digits; the host in front of it
    still goes through the parser that keeps a password out of a row, which is
    why `user:<password>` is refused here as well.
    """
    rows = credentials.proxies(
        environ={"NO_PROXY": "127.0.0.1,localhost,.corp.com,*.corp.com,10.0.0.0/8"}, system={})
    assert [row.host for row in rows] == ["127.0.0.1,localhost,.corp.com,*.corp.com,10.0.0.0/8"]

    refused = credentials.proxies(environ={"NO_PROXY": f"user:{SENTINEL},localhost/../x"}, system={})
    assert [row.host for row in refused] == [f"{credentials.NOT_A_HOST},{credentials.NOT_A_HOST}"]
    assert SENTINEL.lower() not in json.dumps([asdict(row) for row in refused]).lower()


def test_a_machine_with_no_proxy_has_nothing_to_report():
    assert credentials.proxies(environ={"PATH": "/usr/bin"}, system={}) == []
    assert credentials.proxy_note(environ={"PATH": "/usr/bin"}, system={}) == ""


def test_the_lower_case_spelling_is_found_and_the_same_value_is_not_reported_twice():
    """`http_proxy` is the spelling urllib and httpx honour on Linux, and on
    Windows `os.environ` answers both spellings from one variable."""
    rows = credentials.proxies(environ={"http_proxy": "http://proxy.corp:3128"}, system={})
    assert [(row.name, row.host) for row in rows] == [("http_proxy", "proxy.corp:3128")]

    both = credentials.proxies(
        environ={"HTTP_PROXY": "http://proxy.corp:3128", "http_proxy": "http://proxy.corp:3128"},
        system={},
    )
    assert [(row.name, row.host) for row in both] == [("HTTP_PROXY", "proxy.corp:3128")]


def test_a_password_in_a_proxy_url_reaches_no_output(loopback, tmp_path, monkeypatch):
    """The SENTINEL test of criterion 3, over every surface that now carries a
    proxy: the rows themselves, the sentence a failed download adds, and the
    launcher's own copy of that sentence."""
    planted = {name: WITH_A_PASSWORD for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")}
    for name, value in planted.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(credentials, "system_proxies", lambda: {"http": WITH_A_PASSWORD})
    monkeypatch.setattr(models, "HUB", loopback.host)
    model = models.Model(repo="demo/model", revision="a" * 40, files={"weights.bin": {}},
                         license="mit", credit="Demo model, MIT.", gated=False)
    with pytest.raises(models.ModelError) as raised:
        models.fetch_file(model, "weights.bin", tmp_path / "weights.bin", None, lambda _n: None)

    rows = credentials.proxies()
    surfaces = [
        repr(rows),
        str(rows),
        json.dumps([asdict(row) for row in rows]),
        credentials.proxy_note(),
        launcher.proxy_note(planted),
        str(raised.value),
    ]

    for surface in surfaces:
        # Case-insensitively: `urlsplit` lower-cases a hostname, so a value
        # that leaked through the host would arrive with its case changed.
        assert SENTINEL.lower() not in surface.lower(), f"a proxy password reached an output: {surface}"
        assert "user:" not in surface.lower()
    assert all("127.0.0.1:9" in surface for surface in surfaces), "nothing useful was said either"


@pytest.mark.parametrize("value", [
    f"http://user:{SENTINEL}@",
    f"user:{SENTINEL}@",
    f"{SENTINEL} with a space in it",
    "://",
    f"http://user:{SENTINEL}/x@proxy.corp",
    f"http://user:123/{SENTINEL}@proxy.corp",
])
def test_a_proxy_value_that_is_not_a_host_is_never_shown_as_itself(value):
    """The one place a password could leak is the fallback: a value that does
    not parse must not be printed raw, and `netloc` carries `user:password@`.

    Two defences stand here and not one: `urlsplit` leaves the userinfo
    outside `hostname`, and `_HOST_CHARACTERS` refuses what comes back with an
    `@` or a space in it. The last two cases are the ones that got past the
    first - a `/` in a password ends the authority early - and are refused by
    the third rule, that a proxy URL has no path.
    """
    rows = credentials.proxies(environ={"HTTP_PROXY": value}, system={})

    assert [row.host for row in rows] == [credentials.NOT_A_HOST]
    assert SENTINEL.lower() not in json.dumps([asdict(row) for row in rows]).lower()


# --- nothing is written, and nothing is asked -------------------------------------


def test_asking_about_a_proxy_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    conn = db.connect(tmp_path / "test.db")
    db.migrate(conn)
    try:
        before_environ = dict(os.environ)
        before_files = sorted(path.name for path in tmp_path.iterdir())
        rows_before = conn.execute("SELECT count(*) FROM setting").fetchone()[0]

        assert credentials.proxies(environ={"HTTP_PROXY": "http://proxy.corp:3128"}, system={})
        assert credentials.proxy_note(environ={"HTTP_PROXY": "http://proxy.corp:3128"}, system={})

        assert conn.execute("SELECT count(*) FROM setting").fetchone()[0] == rows_before
        assert dict(os.environ) == before_environ
        assert sorted(path.name for path in tmp_path.iterdir()) == before_files
        assert not (tmp_path / "data").exists()
    finally:
        conn.close()


def test_setup_asks_nothing_about_a_proxy(monkeypatch):
    """Criterion 4's other half, as far as it can be pinned today: the setup
    engine's contract has no proxy in it. The question list itself arrives with
    TASK-089.09, so this is what there is to assert now.
    """
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp:3128")

    assert not [key for key in setup.needed().keys() if "proxy" in key.lower()]


# --- the sentence a failed download says ------------------------------------------


def test_a_failed_download_names_a_configured_proxy(tmp_path, monkeypatch):
    """The hub at a closed port either way, so the only difference between the
    two halves is the sentence."""
    monkeypatch.setattr(models, "HUB", "http://127.0.0.1:9")
    model = models.Model(repo="demo/model", revision="a" * 40, files={"weights.bin": {}},
                         license="mit", credit="Demo model, MIT.", gated=False)

    def download() -> str:
        with pytest.raises(models.ModelError) as raised:
            models.fetch_file(model, "weights.bin", tmp_path / "weights.bin", None, lambda _n: None)
        return str(raised.value)

    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    said = download()
    assert said.startswith("demo/model: could not be downloaded (")
    assert said.endswith("; a proxy is configured at 127.0.0.1:9, and the download did not get through it")

    monkeypatch.delenv("HTTP_PROXY")
    urllib.request.install_opener(None)  # the default opener holds the proxies it was built with
    assert "proxy" not in download()


def test_an_answer_that_came_from_the_proxy_names_it_too(loopback, tmp_path, monkeypatch):
    """The other half of the same sentence: when a proxy answers for the hub,
    the HTTP status is the proxy's and this side cannot tell the two apart -
    so the proxy is named there as well.
    """
    monkeypatch.setenv("HTTP_PROXY", loopback.host)  # a proxy that answers, and refuses
    monkeypatch.setattr(models, "HUB", "http://hub.invalid")
    model = models.Model(repo="demo/model", revision="a" * 40, files={"weights.bin": {}},
                         license="mit", credit="Demo model, MIT.", gated=False)

    with pytest.raises(models.ModelError) as raised:
        models.fetch_file(model, "weights.bin", tmp_path / "weights.bin", None, lambda _n: None)

    assert loopback.arrived == ["http://hub.invalid/demo/model/resolve/" + "a" * 40 + "/weights.bin"]
    assert str(raised.value) == (
        f"demo/model: the hub answered HTTP 404; a proxy is configured at 127.0.0.1:{loopback.port}, "
        "and the download did not get through it"
    )


def test_a_gated_answer_that_may_have_come_from_the_proxy_names_it_too(loopback, tmp_path, monkeypatch):
    """403 is what the hub answers for conditions that were not accepted, and
    also what a proxy that blocks a host answers. This side cannot tell the two
    apart, so the proxy is named on the gated line too - otherwise somebody is
    sent to accept conditions on a page their proxy will not let them reach.
    """
    monkeypatch.setenv("HTTP_PROXY", loopback.host)  # a proxy that answers, and refuses
    monkeypatch.setattr(models, "HUB", "http://hub.invalid")
    model = models.Model(repo="demo/refused-model", revision="a" * 40, files={"weights.bin": {}},
                         license="mit", credit="Demo model, MIT.", gated=True)

    with pytest.raises(models.ModelError) as raised:
        models.fetch_file(model, "weights.bin", tmp_path / "weights.bin", None, lambda _n: None)

    assert raised.value.reason == "token"
    assert str(raised.value).endswith(
        f"; a proxy is configured at 127.0.0.1:{loopback.port}, and the download did not get through it"
    )


def test_the_launchers_own_proxy_reader_says_what_credentials_says():
    """The launcher may not import `scribe` (ADR-011), so the reader exists
    twice. This is what stops the two copies drifting apart."""
    environ = {"HTTP_PROXY": "http://proxy.corp:3128", "NO_PROXY": "127.0.0.1"}

    assert launcher.proxy_note(environ) == credentials.proxy_note(environ=environ, system={})
    assert launcher.proxy_note(environ) == (
        "; a proxy is configured at proxy.corp:3128, and the download did not get through it"
    )
    assert launcher.proxy_note({"PATH": "/usr/bin"}) == ""

    # A bypass list is not a proxy, and it is exactly what somebody sets to
    # work around the bug this task fixes - so it is the one environment where
    # the copies could disagree unnoticed: only `credentials` reads NO_PROXY.
    bypass = {"NO_PROXY": "127.0.0.1,localhost"}
    assert launcher.proxy_note(bypass) == credentials.proxy_note(environ=bypass, system={}) == ""

    for value, host in (("proxy.corp:3128", "proxy.corp:3128"),                   # no scheme
                        ("http://[::1]:3128", "[::1]:3128"),                      # IPv6
                        ("http://user:123/pw@proxy.corp", credentials.NOT_A_HOST)):  # a `/` in the password
        one = {"HTTP_PROXY": value}
        said = f"; a proxy is configured at {host}, and the download did not get through it"
        assert launcher.proxy_note(one) == credentials.proxy_note(environ=one, system={}) == said
