"""Phase 6 Task 2: the URL door of the transcribe dialog.

A pasted link is the third way into the library, beside an upload and a path
on this machine, and it is the only one where the web process does not have
the bytes. So the route does almost nothing: it checks the shape of the URL,
maps the options, and writes one `ingest_url` job row (ADR-001 - the download
happens in a runner child, with its own progress and its own failure).

Three things are worth a test of their own here:

* **The options travel nested.** `url_stage.transcribe_params` reads
  `params["options"]`, and a route that merged them at the top level would
  enqueue a job that runs, fans out, downloads - and then transcribes with
  none of the options the user chose. Nothing would say so.
* **A URL is untrusted before it is anything else.** `file:///etc/passwd` and
  `javascript:` are refused with a 400 rather than handed to yt-dlp, which
  reads `file://` through its generic extractor.
* **The preview must not become the thing that hangs the dialog.** It is the
  one place in `scribe/web` that reaches the network (the carve-out the plan
  grants and `urls.py` records), so it runs in a worker thread that is
  *abandoned* on timeout: the page answers, whatever the site is doing.

No test reaches the network. The probe goes through `test_ingest_urls`'s
`FakeYdl` at the `urls.build_ydl` seam, so the real `urls.probe` code runs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe import db, fsbrowse, paths
from scribe.app import create_app
from scribe.ingest import urls
from scribe.stages import transcribe, url_stage
from scribe.web import ingest_ui
from test_ingest_urls import FakeYdl, build_returning, playlist_info, single_info


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
def roots(tmp_path, monkeypatch):
    """A cookies file may only be picked from under the allowed roots."""
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))
    return tmp_path


@pytest.fixture
def client(db_path, data_dir, roots):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


HX = {"HX-Request": "true"}

URL = "https://example.test/watch?v=abc123"


def _jobs(conn):
    return conn.execute("SELECT * FROM job ORDER BY id").fetchall()


def _media(conn):
    return conn.execute("SELECT * FROM media ORDER BY id").fetchall()


def _params(job) -> dict:
    return json.loads(job["params_json"])


def _setting(conn, key):
    row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


def _probing(monkeypatch, fake: FakeYdl) -> FakeYdl:
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    return fake


def _refuse_to_probe(monkeypatch) -> None:
    """Any call to yt-dlp from here on is a test failure."""

    def boom(opts):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("the preview probed a URL it should have refused")

    monkeypatch.setattr(urls, "build_ydl", boom)


def _preview(client, url: str, **kwargs):
    """The preview as the dialog asks for it: a POST, so the guard checks it."""
    return client.post(
        "/transcribe/url/preview", data={"url": url}, headers=HX, **kwargs
    )


# --- the door in the dialog ---------------------------------------------------------


def test_the_dialog_offers_a_url_beside_the_upload_and_the_path(client):
    body = client.get("/transcribe", headers=HX).text

    assert 'name="url"' in body
    assert 'hx-post="/transcribe/url"' in body
    assert 'formaction="/transcribe/url"' in body  # works with scripting off
    # The preview: a fragment the field fetches while it is typed into. A
    # POST, not a GET - see the SSRF test below for why the method is the
    # security property here and not a matter of taste.
    assert 'hx-post="/transcribe/url/preview"' in body
    assert 'id="url-preview"' in body
    # A login-only link needs a cookies file; a browser picker is not on offer.
    assert 'name="cookies_file"' in body


def test_the_url_button_leaves_the_chosen_files_out_of_its_post(client):
    """Same trap the Add file button has: htmx posts the whole enclosing form,
    so files already chosen in the drop zone would be uploaded with the URL
    and silently discarded."""
    import re

    body = client.get("/transcribe", headers=HX).text
    button = re.search(r'<button[^>]*hx-post="/transcribe/url"[^>]*>', body)

    assert button, "no Fetch button"
    assert 'hx-params="not files"' in button.group(0)


# --- posting a URL --------------------------------------------------------------------


def test_posting_a_url_enqueues_an_ingest_url_job_with_the_options_nested(client, conn):
    with db.LOCK:
        folder = conn.execute("INSERT INTO folder(name) VALUES ('Talks') RETURNING id").fetchone()["id"]
        conn.commit()

    resp = client.post(
        "/transcribe/url",
        data={
            "url": URL,
            "language": "nl",
            "tier": "max",
            "diarize": "1",
            "num_speakers": "2",
            "translate": "0",
            "folder_id": str(folder),
        },
        headers=HX,
    )

    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "jobs-changed"
    assert "<html" not in resp.text and "<table" in resp.text  # the library rows

    (job,) = _jobs(conn)
    assert job["type"] == url_stage.JOB_TYPE
    assert job["status"] == "queued"
    assert job["media_id"] is None  # there is no recording yet; that is the point
    assert _params(job) == {
        "url": URL,
        "folder_id": folder,
        # Nested under "options", which is what url_stage.transcribe_params
        # reads. Merged at the top level the job would still run and still
        # transcribe - with none of these.
        "options": {
            "model": transcribe.TRANSLATE_MODEL,
            "task": "transcribe",
            "language": "nl",
            "diarize": True,
            "num_speakers": 2,
        },
    }
    # Nothing was downloaded in the web process (ADR-001).
    assert _media(conn) == []


def test_a_url_post_says_what_it_is_doing_and_does_not_claim_a_transcript(client):
    body = client.post("/transcribe/url", data={"url": URL}, headers=HX).text

    assert 'id="flash"' in body and 'hx-swap-oob="true"' in body
    assert "Fetching" in body


@pytest.mark.parametrize(
    "bad",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://example.test/talk.mp3",
        "example.test/watch",  # yt-dlp would turn a bare string into a search
        "",
        "   ",
    ],
)
def test_a_url_that_is_not_http_is_refused_before_yt_dlp_sees_it(client, conn, bad, monkeypatch):
    _refuse_to_probe(monkeypatch)

    resp = client.post("/transcribe/url", data={"url": bad}, headers=HX)

    assert resp.status_code == 400
    assert _jobs(conn) == [] and _media(conn) == []


def test_bad_options_on_a_url_post_queue_nothing(client, conn):
    resp = client.post("/transcribe/url", data={"url": URL, "tier": "cheetah"}, headers=HX)

    assert resp.status_code == 400
    assert "tier" in resp.json()["detail"]
    assert _jobs(conn) == []


def test_a_url_post_remembers_the_options_as_the_next_defaults(client, conn):
    client.post(
        "/transcribe/url",
        data={"url": URL, "language": "en", "tier": "max", "diarize": "0"},
        headers=HX,
    )

    assert _setting(conn, "default_language") == "en"
    assert _setting(conn, "default_tier") == "max"
    assert _setting(conn, "default_diarize") == "0"


def test_a_plain_url_post_redirects_to_the_library(client, conn):
    resp = client.post("/transcribe/url", data={"url": URL}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert len(_jobs(conn)) == 1


def test_a_cookies_file_travels_with_the_job(client, conn, roots):
    cookies = roots / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

    client.post(
        "/transcribe/url", data={"url": URL, "cookies_file": str(cookies)}, headers=HX
    )

    (job,) = _jobs(conn)
    assert _params(job)["cookies_file"] == str(cookies)


def test_a_cookies_file_outside_the_roots_or_missing_is_refused(client, conn, roots, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "cookies.txt"
    outside.write_text("nope", encoding="utf-8")

    assert (
        client.post(
            "/transcribe/url", data={"url": URL, "cookies_file": str(outside)}, headers=HX
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/transcribe/url",
            data={"url": URL, "cookies_file": str(roots / "nope.txt")},
            headers=HX,
        ).status_code
        == 404
    )
    assert _jobs(conn) == []


# --- the preview ------------------------------------------------------------------------


def test_the_preview_says_what_a_single_link_is(client, monkeypatch):
    _probing(monkeypatch, FakeYdl(single_info()))

    resp = _preview(client, URL)

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    assert 'id="url-preview"' in body
    assert "Vogon Poetry Slam" in body
    assert "Prostetnic Jeltz" in body
    assert "0:42" in body  # 42 seconds, as every player shows it


def test_the_preview_of_a_playlist_counts_the_entries(client, monkeypatch):
    """Until TASK-021 this panel said "A playlist of 3 videos. Fetching it
    queues all 3": the count was the confirmation, because nothing could be
    fetched one at a time. Now the list under it is the picker, the row
    button hides while it shows, and "all 3" would describe a press that is
    no longer on offer."""
    _probing(monkeypatch, FakeYdl(playlist_info(3)))

    body = _preview(client, "https://example.test/playlist?list=PL42").text

    assert "The Hitchhiker Lectures" in body
    assert "3 episodes" in body
    assert "all 3" not in body
    assert body.count('name="entry"') == 3


def test_the_preview_escapes_a_title_that_is_markup(client, monkeypatch):
    _probing(monkeypatch, FakeYdl(single_info(title="<script>alert('pwn')</script>")))

    body = _preview(client, URL).text

    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_a_probe_failure_renders_the_reason_rather_than_a_stack_trace(client, monkeypatch):
    _probing(monkeypatch, FakeYdl(raises=Exception("ERROR: Private video. Sign in if you've been granted access")))

    resp = _preview(client, URL)

    assert resp.status_code == 200  # a fragment, not an error page: htmx swaps it in
    body = resp.text
    assert "Private video" in body
    assert "Traceback" not in body and "Exception" not in body


def test_the_preview_refuses_a_url_that_is_not_a_web_address_without_probing(client, monkeypatch):
    _refuse_to_probe(monkeypatch)

    body = _preview(client, "file:///etc/passwd").text

    assert "not a web address" in body


def test_the_preview_of_an_empty_field_asks_for_a_link_without_probing(client, monkeypatch):
    _refuse_to_probe(monkeypatch)

    resp = _preview(client, "   ")

    assert resp.status_code == 200
    assert 'id="url-preview"' in resp.text


# --- the preview is a door onto the network, and it is guarded like one ----------------


def test_a_page_on_another_site_cannot_make_this_app_fetch_a_url(client, monkeypatch):
    """The preview is the one route in `scribe/web` that opens a socket.

    As a GET it was outside `guard.SameOriginPosts` ("GETs are not guarded ...
    reading changes nothing"), which is true of every other GET in this app
    and false of this one: any page the user happens to be visiting could
    point it at a host of the attacker's choosing and have this process knock
    on it - a blind request and a LAN port scan, from inside the network. A
    POST is checked, so the browser's own `Sec-Fetch-Site` closes it.

    The status code is half the assertion. `_refuse_to_probe` is the other
    half: refused *before* the socket, not after.
    """
    _refuse_to_probe(monkeypatch)
    elsewhere = {**HX, "Sec-Fetch-Site": "cross-site"}
    target = "http://127.0.0.1:1/probe-me"

    # The method the hole was: a GET is not guarded, and this one fetches.
    # It has to be gone, not merely joined by a POST - htmx would go on using
    # whichever one it was pointed at.
    gone = client.get("/transcribe/url/preview", params={"url": target}, headers=elsewhere)
    refused = client.post("/transcribe/url/preview", data={"url": target}, headers=elsewhere)

    assert gone.status_code == 405
    assert refused.status_code == 403
    assert "cross-site" in refused.json()["detail"]


def _hold_slots(count: int) -> list:
    held = [ingest_ui._preview_slots.acquire(timeout=5) for _ in range(count)]
    assert all(held), "a previous test left a preview slot taken"
    return held


def _release_slots(held: list) -> None:
    for _ in held:
        ingest_ui._preview_slots.release()


def test_the_preview_refuses_rather_than_starting_an_unbounded_number_of_probes(
    client, monkeypatch
):
    """Every probe is a thread and an open socket, and a probe that timed out
    is abandoned (`abandon_on_cancel=True`) - anyio hands its limiter token
    back at the cancel while the thread is still on the socket, so nothing in
    the framework counts them. This does."""
    _refuse_to_probe(monkeypatch)
    held = _hold_slots(ingest_ui.PREVIEW_WORKERS)
    try:
        body = _preview(client, URL).text
    finally:
        _release_slots(held)

    assert "at once" in body  # the busy sentence, not a stack trace


class BlockingYdl:
    """A site that answers eventually. The preview must not wait for it."""

    def __init__(self, release: threading.Event):
        self.release = release
        self.started = threading.Event()

    def configure(self, opts):  # pragma: no cover - build_ydl is replaced wholesale
        return self

    def add_progress_hook(self, hook):  # pragma: no cover - a probe has none
        pass

    def extract_info(self, url, download=False):
        self.started.set()
        self.release.wait(10)
        return single_info()


def test_a_preview_that_hangs_gives_up_without_holding_the_page(client, monkeypatch):
    """The timeout has to actually return, not merely say the right thing.

    A worker thread that is waited on would still answer - ten seconds late,
    with exactly this message - so the elapsed time is the assertion that
    means anything here.
    """
    release = threading.Event()
    slow = BlockingYdl(release)
    monkeypatch.setattr(urls, "build_ydl", lambda opts: slow)
    monkeypatch.setattr(ingest_ui, "PREVIEW_TIMEOUT_SECONDS", 0.2)

    started = time.perf_counter()
    try:
        resp = _preview(client, "https://example.test/slow")
        elapsed = time.perf_counter() - started
    finally:
        release.set()  # let the abandoned thread finish while the loop is alive

    assert slow.started.wait(1), "the preview never got as far as probing"
    assert elapsed < 3.0, f"the preview waited {elapsed:.1f}s for a probe it gave up on"
    assert resp.status_code == 200
    assert "took too long" in resp.text.lower()


def test_a_probe_the_request_gave_up_on_still_counts_until_its_thread_is_done(
    client, monkeypatch
):
    """The slot goes back when the *thread* stops, not when the page answers.

    This is the whole reason the bound is a semaphore the probe releases
    itself rather than anyio's limiter: at the timeout the request is
    cancelled and anyio releases its token immediately (`_backends
    /_asyncio.py`, `async with limiter` around `await future`), while the
    worker thread is still sitting on the socket. Counting requests would
    therefore count none of the threads this is meant to bound.
    """
    release = threading.Event()
    slow = BlockingYdl(release)
    monkeypatch.setattr(urls, "build_ydl", lambda opts: slow)
    monkeypatch.setattr(ingest_ui, "PREVIEW_TIMEOUT_SECONDS", 0.2)
    held = _hold_slots(ingest_ui.PREVIEW_WORKERS - 1)
    try:
        assert "took too long" in _preview(client, "https://example.test/slow").text
        assert slow.started.wait(2), "the preview never got as far as probing"

        # The request is over; the thread is not, and the last slot is its.
        assert "at once" in _preview(client, "https://example.test/another").text

        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if "at once" not in _preview(client, "https://example.test/third").text:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the finished probe never gave its slot back")
    finally:
        release.set()
        _release_slots(held)


# --- the dialog's own behaviour, which lives in JavaScript ----------------------------

"""
Everything below drives `scribe/static/app.js` (and, from
`test_ingest_recording`, `recorder.js`) inside Node against a DOM small enough
to read.

There is no browser here and no npm, and the three bugs this section pins are
all in the script rather than in the markup: a preview that closed the dialog
it had just filled, a recorder the dialog forgot about, and an Enter key that
pressed whichever button happened to come first. Asserting on the source text
would pass against an inverted `if`, so the listeners are called for real and
what they did is what is asserted.

The stub is not a browser. Nothing propagates: a listener is looked up by the
node it was registered on and called directly, and `capture` is recorded but
not honoured. Layout, styling and the CSS cascade do not exist. Two rules keep
it from lying:

* **Anything it does not model throws.** A selector it cannot parse raises
  rather than matching nothing, because a fake DOM that answers "no" to a
  question it did not understand is exactly how a stub lets a broken script
  through.
* **It models the defaults that matter.** A `<button>` with no `type` submits,
  which is the rule app.js applies, so the stub reproduces that default rather
  than making every test spell it out.

Measured 2026-09-04 on this machine: the four Node tests here run in about
0.6 s together, one `node` process each. TASK-021 added seven more (the
episode list's filter, All shown, the cap, the patient retry, a dropped link)
and three lines to the stub: `checked` on inputs, `dispatchEvent`, `Event`.
"""

STATIC = Path(__file__).resolve().parent.parent / "scribe" / "static"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="the DOM harness runs the app's own scripts, which needs node on PATH",
)

DOM_STUB = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

/* ---- selectors ---------------------------------------------------------- */

/* The selector language these two scripts actually use: a comma-separated
   list of simple selectors, each an optional tag name followed by any number
   of #id, .class, [attr] and [attr="value"] parts. A descendant combinator or
   a pseudo-class throws: see this module's note on why a stub must not
   silently answer "no". */
const SIMPLE = /^([a-z]+)?((?:[#.][A-Za-z0-9_-]+|\[[a-z-]+(?:="[^"]*")?\])*)$/;
const PART = /[#.][A-Za-z0-9_-]+|\[[a-z-]+(?:="[^"]*")?\]/g;

function matchesOne(node, selector) {
  const found = SIMPLE.exec(selector.trim());
  if (!found) { throw new Error('the DOM stub does not model this selector: ' + selector); }
  if (found[1] && node.tagName !== found[1].toUpperCase()) { return false; }
  const parts = found[2] ? found[2].match(PART) || [] : [];
  return parts.every(function (part) {
    if (part[0] === '#') { return node.attrs.id === part.slice(1); }
    if (part[0] === '.') {
      return String(node.attrs.class || '').split(/\s+/).indexOf(part.slice(1)) !== -1;
    }
    const inside = part.slice(1, -1);
    const eq = inside.indexOf('=');
    if (eq === -1) { return node.hasAttribute(inside); }
    return node.getAttribute(inside.slice(0, eq)) === inside.slice(eq + 2, -1);
  });
}

function matches(node, selector) {
  return String(selector).split(',').some(function (one) { return matchesOne(node, one); });
}

/* ---- elements ------------------------------------------------------------ */

function el(tag, attrs) {
  const node = {
    tagName: String(tag).toUpperCase(),
    attrs: Object.assign({}, attrs || {}),
    children: [],
    parentNode: null,
    hidden: false,
    open: false,
    disabled: false,
    textContent: '',
    value: '',
    clicked: 0,
    closed: 0
  };
  node.hasAttribute = function (name) {
    return Object.prototype.hasOwnProperty.call(node.attrs, name);
  };
  node.getAttribute = function (name) {
    return node.hasAttribute(name) ? node.attrs[name] : null;
  };
  node.setAttribute = function (name, value) { node.attrs[name] = String(value); };
  node.removeAttribute = function (name) { delete node.attrs[name]; };
  /* classList, backed by the class attribute, so a script that marks a node
     (app.js's "Saved" button) can be driven here too. */
  node.classList = {
    _list: function () { return (node.attrs['class'] || '').split(/\s+/).filter(Boolean); },
    contains: function (name) { return node.classList._list().indexOf(name) >= 0; },
    add: function (name) {
      const list = node.classList._list();
      if (list.indexOf(name) < 0) { list.push(name); }
      node.attrs['class'] = list.join(' ');
    },
    remove: function (name) {
      node.attrs['class'] = node.classList._list().filter(function (c) { return c !== name; }).join(' ');
    }
  };
  Object.defineProperty(node, 'isConnected', { get: function () {
    let cur = node;
    while (cur.parentNode) { cur = cur.parentNode; }
    return cur === document || cur === body || cur.tagName === 'BODY' || cur.tagName === 'HTML';
  } });
  node.matches = function (selector) { return matches(node, selector); };
  node.closest = function (selector) {
    let at = node;
    while (at) {
      if (matches(at, selector)) { return at; }
      at = at.parentNode;
    }
    return null;
  };
  node.querySelectorAll = function (selector) {
    return descendants(node).filter(function (kid) { return matches(kid, selector); });
  };
  node.querySelector = function (selector) { return node.querySelectorAll(selector)[0] || null; };
  node.addEventListener = function (type, fn, options) { listen(node, type, fn, options); };
  node.append = function (child) { child.parentNode = node; node.children.push(child); return child; };
  node.remove = function () {
    if (!node.parentNode) { return node; }
    const kids = node.parentNode.children;
    kids.splice(kids.indexOf(node), 1);
    node.parentNode = null;
    return node;
  };
  node.click = function () { node.clicked += 1; };
  node.close = function () { node.closed += 1; node.open = false; };
  node.showModal = function () { node.open = true; };
  node.focus = function () { node.focused = (node.focused || 0) + 1; };
  /* dispatchEvent calls the listeners registered on this node, and nothing
     else - the same "never propagated" rule as fire(). app.js dispatches an
     `input` on the link field after a drop, so the field's own trigger runs. */
  node.dispatchEvent = function (ev) { return fire(node, ev.type, ev); };
  /* A <button> with no type attribute submits, and an <input> with none is
     text. Those two defaults are the whole point of the rule app.js applies,
     so the stub has them rather than every fixture spelling them out. A
     checkbox starts unticked, which is the default that matters to a count. */
  if (node.tagName === 'BUTTON') { node.type = node.attrs.type || 'submit'; }
  if (node.tagName === 'INPUT') { node.type = node.attrs.type || 'text'; node.checked = false; }
  return node;
}

function descendants(node) {
  const out = [];
  node.children.forEach(function (kid) {
    out.push(kid);
    descendants(kid).forEach(function (deep) { out.push(deep); });
  });
  return out;
}

/* ---- listeners: recorded, never propagated ------------------------------- */

const listeners = [];

function listen(target, type, fn, options) {
  listeners.push({
    target: target,
    type: type,
    fn: fn,
    capture: options === true || Boolean(options && options.capture)
  });
}

/* Calls every listener registered on `target` for `type`, in order, and says
   how many there were: a test that expected wiring and got none should fail
   on the count rather than on a silent nothing. */
function fire(target, type, ev) {
  const hit = listeners.filter(function (l) { return l.target === target && l.type === type; });
  hit.forEach(function (l) { l.fn(ev); });
  return hit.length;
}

function registered(target, type) {
  return listeners.filter(function (l) { return l.target === target && l.type === type; });
}

function event(props) {
  const ev = Object.assign({ defaultPrevented: 0, stopped: 0 }, props || {});
  ev.preventDefault = function () { ev.defaultPrevented += 1; };
  ev.stopPropagation = function () { ev.stopped += 1; };
  return ev;
}

/* `new Event('input', {bubbles: true})` as app.js writes it: the same plain
   object event() builds, with its type on it, so dispatchEvent can route it. */
function Event(type, props) {
  const ev = event(props);
  ev.type = type;
  return ev;
}
globalThis.Event = Event;

/* ---- the page ------------------------------------------------------------ */

const root = el('html');
const body = root.append(el('body'));

const document = {
  readyState: 'complete',
  body: body,
  documentElement: root,
  addEventListener: function (type, fn, options) { listen(document, type, fn, options); },
  getElementById: function (id) { return root.querySelector('#' + id); },
  querySelector: function (selector) { return root.querySelector(selector); },
  querySelectorAll: function (selector) { return root.querySelectorAll(selector); },
  createElement: function () { throw new Error('the DOM stub does not build elements'); },
  createTextNode: function () { throw new Error('the DOM stub does not build text nodes'); }
};

const window = {
  /* The two clocks (app.js's elapsed counter, recorder.js's) would keep node
     alive for ever, and neither is what any test here is about. */
  setInterval: function () { return 0; },
  clearInterval: function () {},
  setTimeout: function (fn, ms) { return setTimeout(fn, ms); },
  clearTimeout: function (id) { clearTimeout(id); },
  requestAnimationFrame: function () { return 0; },
  addEventListener: function (type, fn, options) { listen(window, type, fn, options); },
  removeEventListener: function () {},
  confirm: function () { return true; },
  location: { assign: function (url) { window.assigned = url; } }
};

const posted = [];
function fakeFetch(url, options) {
  posted.push({ url: url, options: options || {} });
  return Promise.resolve({
    ok: true,
    status: 200,
    redirected: false,
    url: url,
    json: function () { return Promise.resolve({ session: 'sessiontokenwith16' }); },
    text: function () { return Promise.resolve('{}'); }
  });
}

/* A recorder that records nothing: what the tests need from MediaRecorder is
   its state machine and the moment `onstop` fires, not audio. */
function FakeRecorder(stream, options) {
  this.stream = stream;
  this.options = options;
  this.state = 'inactive';
  this.started = 0;
}
FakeRecorder.prototype.start = function (ms) { this.state = 'recording'; this.started = ms; };
FakeRecorder.prototype.pause = function () { this.state = 'paused'; };
FakeRecorder.prototype.resume = function () { this.state = 'recording'; };
FakeRecorder.prototype.stop = function () {
  this.state = 'inactive';
  if (this.onstop) { this.onstop(); }
};
FakeRecorder.isTypeSupported = function () { return true; };
window.MediaRecorder = FakeRecorder;

const tracks = [];
const navigator = {
  mediaDevices: {
    getUserMedia: function () {
      const track = { stopped: 0, stop: function () { track.stopped += 1; } };
      tracks.push(track);
      return Promise.resolve({ getTracks: function () { return tracks.slice(); } });
    }
  }
};

/* The elapsed clock is read off Date.now(), so a test that wants to see a
   repainted clock moves the clock rather than waiting for it. */
const realNow = Date.now;
let clockOffset = 0;
Date.now = function () { return realNow() + clockOffset; };
function advance(ms) { clockOffset += ms; }

globalThis.document = document;
globalThis.window = window;
globalThis.fetch = fakeFetch;
/* node has a navigator of its own, and it is a getter with no setter. */
Object.defineProperty(globalThis, 'navigator', { value: navigator, configurable: true });

function load(file) {
  vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });
}

function settle() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}

function done(payload) { console.log('__RESULT__' + JSON.stringify(payload)); }
function fail(err) {
  console.log('__RESULT__' + JSON.stringify({ __error: String((err && err.stack) || err) }));
  process.exitCode = 1;
}
process.on('unhandledRejection', fail);
"""


def run_dom(tmp_path, body: str) -> dict:
    """Run `body` against the stub DOM in node and hand back what it reported.

    The body is JavaScript, run inside an async function so it can await the
    promises the scripts chain, and ends by calling `done({...})` with whatever
    the test wants to assert on.
    """
    prelude = (
        DOM_STUB
        + "const APP = " + json.dumps(str(STATIC / "app.js")) + ";\n"
        + "const RECORDER = " + json.dumps(str(STATIC / "recorder.js")) + ";\n"
    )
    script = tmp_path / "dom_harness.js"
    script.write_text(
        prelude + "\n(async function () {\n  try {\n" + body + "\n  } catch (err) { fail(err); }\n})();\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        timeout=60,
        text=True,
        encoding="utf-8",
    )
    lines = [l for l in (proc.stdout or "").splitlines() if l.startswith("__RESULT__")]
    assert proc.returncode == 0 and lines, (
        f"the harness did not report:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    result = json.loads(lines[-1][len("__RESULT__"):])
    assert "__error" not in result, result["__error"]
    return result


DIALOG_FIXTURE = r"""
    /* The transcribe dialog as library.html and transcribe_dialog.html build
       it: an open <dialog> with no data-stay-open, one form, and the three
       controls that post from inside it. */
    const dialog = body.append(el('dialog', { id: 'transcribe-dialog' }));
    dialog.open = true;
    const flash = dialog.append(el('p', { 'data-dialog-flash': '' }));
    const form = dialog.append(el('form', { 'data-enter-scope': '' }));
    const pathRow = form.append(el('div', { class: 'pick-path', 'data-submit-row': '' }));
    const pathField = pathRow.append(el('input', { type: 'text', name: 'path' }));
    const addFile = pathRow.append(el('button', { type: 'submit' }));
    const urlRow = form.append(el('div', { class: 'pick-url', 'data-submit-row': '' }));
    const urlField = urlRow.append(el('input', { type: 'url', name: 'url' }));
    const fetchIt = urlRow.append(el('button', { type: 'submit' }));
"""


@needs_node
def test_the_url_preview_does_not_close_the_dialog_it_was_typed_into(tmp_path):
    """CR-001. The preview is a POST that reads, and it always answers 200.

    app.js closed the dialog on any successful POST from inside it, so every
    preview took the panel away in the same tick it arrived - and the playlist
    warning ("this link is 40 videos") could not be read at all. The dialog is
    not marked data-stay-open, because an upload still has to close it.
    """
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE
        + r"""
    load(APP);
    const seen = fire(document.body, 'htmx:afterRequest', event({
      detail: { elt: urlField, successful: true, requestConfig: { verb: 'POST' } }
    }));
    done({ seen: seen, closed: dialog.closed, open: dialog.open });
""",
    )

    assert result["seen"] == 1, "nothing in app.js listened for htmx:afterRequest"
    assert result["closed"] == 0, "the preview closed the dialog it had just filled"
    assert result["open"] is True


@needs_node
def test_the_upload_and_both_row_buttons_still_close_the_dialog(tmp_path):
    """The other half of CR-001: scoping the auto-close must not remove it.

    A post from the form itself (Upload and transcribe) or from either row's
    submit button is the work being done, and the dialog goes away.
    """
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE
        + r"""
    load(APP);
    const out = {};
    [['form', form], ['addFile', addFile], ['fetchIt', fetchIt]].forEach(function (pair) {
      dialog.open = true;
      dialog.closed = 0;
      fire(document.body, 'htmx:afterRequest', event({
        detail: { elt: pair[1], successful: true, requestConfig: { verb: 'POST' } }
      }));
      out[pair[0]] = dialog.closed;
    });
    done(out);
""",
    )

    assert result == {"form": 1, "addFile": 1, "fetchIt": 1}


@needs_node
def test_enter_in_the_url_field_fetches_the_link_and_does_not_add_a_file(tmp_path):
    """CR-006. Enter submits the form, and the browser presses the *first*
    submit button in it - "Add file" - whichever field the cursor is in. The
    URL row's Enter therefore posted /transcribe/path and complained about a
    path nobody had typed.

    Reordering the buttons would only move the bug to the other field, so the
    key is handled per row: Enter presses the button in its own row.
    """
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE
        + r"""
    load(APP);
    const fromUrl = event({ key: 'Enter', target: urlField });
    const seenUrl = fire(document, 'keydown', fromUrl);
    const url = { fetchIt: fetchIt.clicked, addFile: addFile.clicked, prevented: fromUrl.defaultPrevented };

    addFile.clicked = 0;
    fetchIt.clicked = 0;
    const fromPath = event({ key: 'Enter', target: pathField });
    fire(document, 'keydown', fromPath);
    const path = { fetchIt: fetchIt.clicked, addFile: addFile.clicked, prevented: fromPath.defaultPrevented };

    done({ seenUrl: seenUrl, url: url, path: path });
""",
    )

    assert result["seenUrl"] >= 1, "nothing in app.js listened for Enter"
    assert result["url"] == {"fetchIt": 1, "addFile": 0, "prevented": 1}
    assert result["path"] == {"fetchIt": 0, "addFile": 1, "prevented": 1}


@needs_node
def test_a_field_that_names_a_button_presses_that_one_from_outside_its_row(tmp_path):
    """The cookies field belongs to the URL flow and sits outside its row.

    This test used to assert the opposite - that such a field is "left to the
    browser" - and it passed because app.js does nothing, which is true. What
    it could not see is the half that matters: leaving it to the browser means
    the browser presses the *first* submit in the form, "Add file", so Enter in
    the cookies field produced the same complaint about an untyped path that
    CR-006 removed from the URL field. The harness has no default-submit, so
    `addFile: 0` there measured the stub rather than the browser. A field now
    says which button it means.
    """
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE
        + r"""
    const loose = form.append(el('input', {
      type: 'text', name: 'cookies_file', 'data-enter': '[data-url-submit]',
    }));
    fetchIt.setAttribute('data-url-submit', '');
    load(APP);
    const ev = event({ key: 'Enter', target: loose });
    fire(document, 'keydown', ev);
    done({ prevented: ev.defaultPrevented, addFile: addFile.clicked, fetchIt: fetchIt.clicked });
""",
    )

    assert result == {"prevented": 1, "addFile": 0, "fetchIt": 1}


@needs_node
def test_a_field_that_names_no_button_swallows_the_key(tmp_path):
    """The recording's name field and the three speaker-hint inputs. There is
    no button any of them could mean, and falling through would post the upload
    form - from a number input, while a microphone may be running. They carry
    no attribute at all: the scope is what makes them safe."""
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE
        + r"""
    const named = form.append(el('input', { type: 'number', name: 'num_speakers' }));
    load(APP);
    const ev = event({ key: 'Enter', target: named });
    fire(document, 'keydown', ev);
    done({ prevented: ev.defaultPrevented, addFile: addFile.clicked, fetchIt: fetchIt.clicked });
""",
    )

    assert result == {"prevented": 1, "addFile": 0, "fetchIt": 0}


# --- and the shape of the markup those handlers need ----------------------------------


def test_each_row_carries_its_own_submit_button_for_enter_to_press(client):
    """The hook CR-006's fix hangs on. Without data-submit-row on the row, the
    key handler has nothing to scope to and Enter goes back to pressing
    whichever button is first in the form."""
    import re

    body = client.get("/transcribe", headers=HX).text
    rows = re.findall(r'<div class="pick-(?:path|url)"[^>]*>(.*?)</div>', body, re.S)

    assert len(rows) == 2, "the path and url rows are what Enter is scoped to"
    for row in rows:
        assert "data-submit-row" not in row  # it belongs on the row, not inside it
        assert len(re.findall(r'<button[^>]*type="submit"', row)) == 1
    assert body.count("data-submit-row") == 2


def test_the_preview_is_not_a_submit_control_and_the_dialog_still_closes_itself(client):
    """The two shapes the fix must not take.

    The preview must stay a plain field: made a submit button it would close
    the dialog again, this time by the rule rather than in spite of it. And the
    dialog must not be marked data-stay-open - an upload has to close it.
    """
    import re

    body = client.get("/transcribe", headers=HX).text
    preview = re.search(r"<[a-z]+[^>]*hx-post=\"/transcribe/url/preview\"[^>]*>", body)

    assert preview, "no preview control"
    assert preview.group(0).startswith("<input")
    assert 'type="url"' in preview.group(0)
    assert "data-stay-open" not in body


def test_the_dialog_form_is_a_scope_where_enter_never_falls_through(client):
    """Seven fields share one form and the browser would press the first submit
    in it - "Add file" - from any of them.

    Tagging fields one at a time is the wrong primitive, and this test caught me
    proving it: my first pass covered the cookies field and the recording's name
    and missed the three speaker-hint inputs, and the test I wrote to check my
    own work searched for `type="text"` and missed them the same way. So the
    form itself is the scope: inside it Enter never reaches the browser, and a
    field opts in to a button rather than opting out of the default. A field
    added tomorrow is safe without anyone remembering this.
    """
    body = client.get("/transcribe", headers=HX).text

    assert "data-enter-scope" in body, "the form is no longer a scope"
    assert 'data-url-submit' in body, "the URL button lost the hook the cookies field names"
    assert 'name="cookies_file" data-enter="[data-url-submit]"' in body

    # Every field the key can reach, not only the ones spelled type="text".
    fields = [
        tag
        for tag in re.findall(r"<input[^>]*>", body)
        if not re.search(r'type="(checkbox|radio|file|hidden|submit|button)"', tag)
    ]
    assert len(fields) >= 6, f"only {len(fields)} fields found; the selector went stale"
    for field in fields:
        assert 'name="' in field, f"a field with no name: {field[:90]}"


# --- the feed import (TASK-021): the list, the marks, the patient retry, the ticks ---

"""
A feed or a channel pasted into the link field lists its episodes, and the
url route grows two cases in front of today's one: ticked entries become one
job each, and a list with nothing ticked is a 400. The feed-shaped fixture is
`feed_info` from `test_ingest_urls` - yt-dlp's own reading of a feed, smuggled
guid and all - because that is the input the feature is about; `playlist_info`
stays for the YouTube shape.
"""

import calendar
import html

from scribe import jobs, media
from scribe.web import library
from test_ingest_urls import FEED_URL, feed_info

PLAYLIST = "https://example.test/playlist?list=PL42"


def _entry(url: str, title: str = "Episode", source_id: str | None = None) -> str:
    return json.dumps({"url": url, "title": title, "source_id": source_id})


def _entries(n: int) -> list[str]:
    return [
        _entry(f"https://example.test/ep{i}.mp3", f"Episode {i}", f"Generic:g{i}")
        for i in range(n)
    ]


def _post_episodes(client, entries, feed_url=FEED_URL, feed_title="The Hitchhiker Lectures", **extra):
    data = {"url": feed_url, "feed_url": feed_url, "feed_title": feed_title, "entry": entries, **extra}
    return client.post("/transcribe/url", data=data, headers=HX)


def _rows(body: str) -> list[str]:
    return re.findall(r"<li>(.*?)</li>", body, re.S)


def _checkbox_values(body: str) -> list[dict]:
    values = re.findall(r'<input type="checkbox" name="entry" value="([^"]*)"', body)
    return [json.loads(html.unescape(value)) for value in values]


def _seed_media(conn, tmp_path, name: str, **provenance) -> dict:
    path = tmp_path / name
    path.write_bytes(f"bytes of {name}".encode())
    return media.ingest_path(conn, path, **provenance)


# --- the list ------------------------------------------------------------------------


def test_the_playlist_preview_lists_one_row_per_entry_with_a_value_that_round_trips(
    client, monkeypatch
):
    """The checkbox value is the entry as JSON, autoescaped into the attribute
    and unescaped by the browser on the way back; a title with quotes, an
    ampersand and an angle bracket has to survive that trip untouched."""
    _probing(monkeypatch, FakeYdl(feed_info()))

    body = _preview(client, FEED_URL).text

    values = _checkbox_values(body)
    assert len(values) == 4
    assert values[0]["title"] == 'Trump drinks Venezuela\'s milkshake & "more" <3 café'
    assert values[0]["source_id"] == "Generic:guid-0"
    assert values[0]["url"].startswith("https://cdn.test/ep/default.mp3?d=1&e=0#")
    assert "<script" not in body and "&lt;3 café" in body
    assert "4 episodes" in body
    assert f'name="feed_url" value="{FEED_URL}"' in body
    assert 'name="feed_title" value="The Hitchhiker Lectures"' in body


def test_the_preview_marks_what_is_in_the_library_in_the_trash_and_queued(
    client, conn, tmp_path, monkeypatch
):
    info = playlist_info(4)
    info["entries"][1]["ie_key"] = "Youtube"  # source_id Youtube:vid1
    _probing(monkeypatch, FakeYdl(info))
    _seed_media(conn, tmp_path, "a.mp3", source_url="https://example.test/watch?v=vid0")
    trashed = _seed_media(conn, tmp_path, "b.mp3", source_id="Youtube:vid1")
    conn.execute("UPDATE media SET trashed_at=? WHERE id=?", (time.time(), trashed["id"]))
    conn.commit()
    jobs.enqueue(conn, url_stage.JOB_TYPE, params={"url": "https://example.test/watch?v=vid2#frag"})

    rows = _rows(_preview(client, PLAYLIST).text)

    assert len(rows) == 4
    assert "in library" in rows[0] and "queued" not in rows[0]
    assert "in trash" in rows[1] and "in library" not in rows[1]
    assert "queued" in rows[2]
    assert "ep-state" not in rows[3]


def test_a_queued_job_wins_over_a_library_row_for_the_same_url(client, conn, tmp_path, monkeypatch):
    """The transient fact that stops a duplicate download wins; "in library"
    reappears on its own once the job finishes."""
    _probing(monkeypatch, FakeYdl(playlist_info(1)))
    _seed_media(conn, tmp_path, "a.mp3", source_url="https://example.test/watch?v=vid0")
    jobs.enqueue(conn, url_stage.JOB_TYPE, params={"url": "https://example.test/watch?v=vid0"})

    row = _rows(_preview(client, PLAYLIST).text)[0]

    assert "queued" in row and "in library" not in row


def test_known_sources_marks_queued_and_running_jobs_but_no_terminal_one(conn):
    urls_ = [f"https://example.test/watch?v=s{i}" for i in range(7)]
    running = jobs.enqueue(conn, url_stage.JOB_TYPE, params={"url": urls_[1]})
    jobs.claim_next(conn)  # the oldest queued job: `running` is now running
    jobs.enqueue(conn, url_stage.JOB_TYPE, params={"url": urls_[0]})
    for i, status in zip((2, 3, 4, 5), ("done", "failed", "cancelled", "interrupted")):
        job_id = jobs.enqueue(conn, url_stage.JOB_TYPE, params={"url": urls_[i]})
        assert jobs.finish(conn, job_id, status)
    jobs.enqueue(conn, url_stage.JOB_TYPE, params={"url": FEED_URL})  # the feed itself, from Fetch
    assert conn.execute("SELECT status FROM job WHERE id=?", (running,)).fetchone()["status"] == "running"

    states = ingest_ui.known_sources(conn, [{"url": u, "source_id": None} for u in urls_])

    assert states == ["queued", "queued", None, None, None, None, None]


def test_known_sources_matches_on_the_id_when_the_url_differs(conn, tmp_path, data_dir):
    """A channel video added by hand as youtu.be/ID is the same recording
    the channel listing calls watch?v=ID; the extractor-scoped id says so."""
    _seed_media(conn, tmp_path, "a.mp3", source_url="https://youtu.be/vid0", source_id="Youtube:vid0")

    states = ingest_ui.known_sources(
        conn,
        [
            {"url": "https://www.youtube.com/watch?v=vid0", "source_id": "Youtube:vid0"},
            {"url": "https://www.youtube.com/watch?v=vid1", "source_id": "Youtube:vid1"},
        ],
    )

    assert states == ["library", None]


def test_the_header_says_the_first_n_when_the_listing_was_cut(client, monkeypatch):
    monkeypatch.setattr(ingest_ui, "MAX_LISTED", 3)
    info = playlist_info(3)
    info["playlist_count"] = 7
    fake = _probing(monkeypatch, FakeYdl(info))

    body = _preview(client, PLAYLIST).text

    assert "the first 3 of 7 episodes" in body
    assert fake.opts["playlistend"] == 3  # the limit reached the seam

    _probing(monkeypatch, FakeYdl(playlist_info(3)))  # a channel: no total
    assert "the first 3 episodes" in _preview(client, PLAYLIST).text


def test_the_header_names_the_cap(client, monkeypatch):
    _probing(monkeypatch, FakeYdl(playlist_info(2)))

    body = _preview(client, PLAYLIST).text

    assert f"at most {url_stage.MAX_FAN_OUT} per import" in body
    assert f'data-max="{url_stage.MAX_FAN_OUT}"' in body


def test_an_episode_row_shows_its_date_and_duration_and_copes_without_either(
    client, monkeypatch
):
    """Noon UTC, so the local date is the same in every zone from UTC-12 to
    UTC+11 - the reasoning the export tests record for NOON."""
    info = playlist_info(3)
    info["entries"][0]["timestamp"] = calendar.timegm((2026, 9, 4, 12, 0, 0))
    info["entries"][0]["duration"] = 1527.0
    info["entries"][1]["timestamp"] = calendar.timegm((2026, 9, 2, 12, 0, 0))
    info["entries"][1].pop("duration")
    info["entries"][2].pop("duration")
    _probing(monkeypatch, FakeYdl(info))

    rows = _rows(_preview(client, PLAYLIST).text)

    assert "2026-09-04" in rows[0] and "25:27" in rows[0]
    assert "2026-09-02" in rows[1] and "·" not in re.sub(r"\s+", " ", rows[1]).split("2026-09-02")[1]
    assert "Lecture 2" in rows[2]
    assert "None" not in rows[2] and "1970" not in rows[2]


def test_the_import_button_is_the_row_button_s_twin_and_the_tools_are_not_submits(
    client, monkeypatch
):
    _probing(monkeypatch, FakeYdl(playlist_info(3)))

    body = _preview(client, PLAYLIST).text

    tag = re.search(r"<button[^>]*data-episodes-submit[^>]*>", body).group(0)
    assert 'type="submit"' in tag and 'formaction="/transcribe/url"' in tag
    assert 'hx-post="/transcribe/url"' in tag and 'hx-params="not files"' in tag
    for name in ("data-episodes-all", "data-episodes-none"):
        other = re.search(rf"<button[^>]*{name}[^>]*>", body).group(0)
        assert 'type="button"' in other
    filter_tag = re.search(r"<input[^>]*data-episode-filter[^>]*>", body).group(0)
    assert " name=" not in filter_tag


def test_the_link_field_previews_on_input_never_on_change(client):
    """`change` fires on blur - the first click into the list - and the swap
    would replace the list, ticks and filter text and all."""
    body = client.get("/transcribe", headers=HX).text
    tag = re.search(r'<input[^>]*name="url"[^>]*>', body).group(0)
    trigger = re.search(r'hx-trigger="([^"]*)"', tag).group(1)
    specs = [spec.strip().split()[0] for spec in trigger.split(",")]

    assert "change" not in specs and "input" in specs
    assert 'hx-sync="closest [data-panel]:replace"' in tag


# --- the patient retry ---------------------------------------------------------------


def _patient(client, url: str):
    return client.post("/transcribe/url/preview", data={"url": url, "patient": "1"}, headers=HX)


def test_a_slow_preview_offers_to_list_the_episodes_anyway(client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(urls, "build_ydl", lambda opts: BlockingYdl(release))
    monkeypatch.setattr(ingest_ui, "PREVIEW_TIMEOUT_SECONDS", 0.2)
    try:
        body = _preview(client, "https://example.test/slow").text
    finally:
        release.set()

    assert ingest_ui.HINT_SLOW in body
    tag = re.search(r'<button[^>]*hx-post="/transcribe/url/preview"[^>]*>', body).group(0)
    assert 'type="button"' in tag
    assert 'hx-params="url,patient"' in tag and "patient" in re.search(r"hx-vals='([^']*)'", tag).group(1)
    assert 'hx-target="#url-preview"' in tag and 'hx-swap="outerHTML"' in tag
    assert "hx-sync=" in tag and 'hx-disabled-elt="this"' in tag


def test_a_patient_preview_waits_past_the_short_budget(client, monkeypatch):
    """Same site, same delay: the short budget gave up on it and the patient
    one did not. That is what "a different budget" means."""
    release = threading.Event()
    monkeypatch.setattr(urls, "build_ydl", lambda opts: BlockingYdl(release))
    monkeypatch.setattr(ingest_ui, "PREVIEW_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ingest_ui, "PATIENT_TIMEOUT_SECONDS", 3.0)
    try:
        assert "took too long" in _preview(client, "https://example.test/slow").text
        threading.Timer(0.6, release.set).start()
        started = time.perf_counter()
        body = _patient(client, "https://example.test/slow").text
        elapsed = time.perf_counter() - started
    finally:
        release.set()

    assert "Vogon Poetry Slam" in body and "took too long" not in body
    assert 0.2 < elapsed < 3.0, f"the patient preview answered after {elapsed:.2f}s"


def test_a_patient_preview_attaches_to_the_listing_already_running(client, monkeypatch):
    """The 10 s probe's thread is still listing when the button is pressed;
    the patient request joins it rather than starting a second listing of
    the same URL on a second slot."""
    release = threading.Event()
    builds: list = []
    slow = BlockingYdl(release)
    monkeypatch.setattr(urls, "build_ydl", lambda opts: (builds.append(opts), slow)[1])
    monkeypatch.setattr(ingest_ui, "PREVIEW_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ingest_ui, "PATIENT_TIMEOUT_SECONDS", 3.0)
    try:
        _preview(client, "https://example.test/slow")
        threading.Timer(0.3, release.set).start()
        body = _patient(client, "https://example.test/slow").text
    finally:
        release.set()

    assert "Vogon Poetry Slam" in body
    assert len(builds) == 1


def test_a_patient_preview_that_still_times_out_offers_no_second_retry(client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(urls, "build_ydl", lambda opts: BlockingYdl(release))
    monkeypatch.setattr(ingest_ui, "PATIENT_TIMEOUT_SECONDS", 0.2)
    try:
        started = time.perf_counter()
        body = _patient(client, "https://example.test/slow").text
        elapsed = time.perf_counter() - started
    finally:
        release.set()

    assert ingest_ui.HINT_SLOW in body
    assert "patient" not in body
    assert elapsed < 3.0


def test_a_follower_holds_no_slot(client, monkeypatch):
    """Two requests, one listing: the second is a follower and takes no
    slot, so with every other slot held it still waits on the listing rather
    than answering busy - while a different URL, which would need a slot of
    its own, is refused."""
    release = threading.Event()
    slow = BlockingYdl(release)
    monkeypatch.setattr(urls, "build_ydl", lambda opts: slow)
    monkeypatch.setattr(ingest_ui, "PREVIEW_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ingest_ui, "PATIENT_TIMEOUT_SECONDS", 0.2)
    held = _hold_slots(ingest_ui.PREVIEW_WORKERS - 1)
    try:
        assert "took too long" in _preview(client, "https://example.test/slow").text
        assert slow.started.wait(2)
        follower = _patient(client, "https://example.test/slow").text
        other = _preview(client, "https://example.test/another").text
    finally:
        release.set()
        _release_slots(held)

    assert "took too long" in follower and "at once" not in follower
    assert "at once" in other


@pytest.mark.parametrize("patient", [None, "1"])
def test_the_preview_refuses_rather_than_starting_an_unbounded_number_of_probes_patient_or_not(
    client, monkeypatch, patient
):
    _refuse_to_probe(monkeypatch)
    data = {"url": URL}
    if patient:
        data["patient"] = patient
    held = _hold_slots(ingest_ui.PREVIEW_WORKERS)
    try:
        body = client.post("/transcribe/url/preview", data=data, headers=HX).text
    finally:
        _release_slots(held)

    assert "at once" in body


def test_a_page_on_another_site_cannot_list_a_feed_patiently_either(client, monkeypatch):
    _refuse_to_probe(monkeypatch)
    elsewhere = {**HX, "Sec-Fetch-Site": "cross-site"}

    refused = client.post(
        "/transcribe/url/preview",
        data={"url": "http://127.0.0.1:1/probe-me", "patient": "1"},
        headers=elsewhere,
    )

    assert refused.status_code == 403


# --- the url route with a list on screen ---------------------------------------------


def test_ticked_entries_become_one_job_each_in_posted_order(client, conn):
    resp = _post_episodes(client, _entries(3))

    assert resp.status_code == 200
    rows = _jobs(conn)
    assert [row["type"] for row in rows] == [url_stage.JOB_TYPE] * 3
    params = [_params(row) for row in rows]
    assert [p["url"] for p in params] == [f"https://example.test/ep{i}.mp3" for i in range(3)]
    for i, p in enumerate(params):
        assert p["from_playlist"] is True
        assert p["entry"] == {"title": f"Episode {i}", "source_id": f"Generic:g{i}"}
        assert p["source"] == {"url": FEED_URL, "title": "The Hitchhiker Lectures"}
        assert p["options"] == transcribe_defaults()
        assert p["folder_id"] is None
    assert all(p["url"] != FEED_URL for p in params)


def transcribe_defaults() -> dict:
    from scribe.options import TranscribeOptions

    return TranscribeOptions().to_params()


def test_exactly_the_cap_s_worth_are_all_queued_and_one_more_is_refused_with_both_numbers(
    client, conn, monkeypatch
):
    """The boundary is inclusive here as it is in the fan-out: the cap is a
    size that works, and one past it names both numbers."""
    monkeypatch.setattr(url_stage, "MAX_FAN_OUT", 3)

    refused = _post_episodes(client, _entries(4))
    assert refused.status_code == 400
    assert "4" in refused.json()["detail"] and "3" in refused.json()["detail"]
    assert _jobs(conn) == []

    accepted = _post_episodes(client, _entries(3))
    assert accepted.status_code == 200
    assert len(_jobs(conn)) == 3


def test_a_whole_listing_posted_at_once_is_refused_by_this_route_not_by_starlette(client, conn):
    """Starlette's form parser refuses more than 1000 fields with its own
    sentence; "All shown, Import" on a big feed must reach this route's
    answer instead."""
    resp = _post_episodes(client, _entries(ingest_ui.MAX_LISTED))

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "at most" in detail and "Too many fields" not in detail
    assert _jobs(conn) == []


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[1]",
        '"str"',
        "null",
        "{}",
        '{"url": "file:///etc/passwd"}',
        '{"url": "ytsearch:how to make bread"}',
        '{"url": "javascript:alert(1)"}',
        '{"url": 5}',
        '{"url": "https://x.test/a", "title": NaN}',
        pytest.param("x" * 5000, id="oversize"),
        pytest.param("[" * 50000 + "]" * 50000, id="nested-100k"),
    ],
)
def test_one_bad_entry_among_good_ones_queues_none_of_them(client, conn, bad, monkeypatch):
    """Between two good ones on purpose: good-then-bad catches a route that
    enqueues as it validates, bad-then-good one that skips the bad entry and
    queues the rest."""
    _refuse_to_probe(monkeypatch)
    good = _entry("https://example.test/ep1.mp3", "Lecture 1")

    resp = _post_episodes(client, [good, bad, good])

    assert resp.status_code == 400
    assert "episode 2" in resp.json()["detail"]
    assert _jobs(conn) == []


def test_an_entry_title_is_cut_at_the_library_s_name_limit(client, conn):
    _post_episodes(client, [_entry("https://example.test/ep.mp3", "x" * 400)])

    assert len(_params(_jobs(conn)[0])["entry"]["title"]) == library.MAX_NAME


def test_a_feed_url_that_is_not_http_is_refused_and_queues_nothing(client, conn):
    resp = _post_episodes(client, _entries(1), feed_url="file:///etc/passwd")

    assert resp.status_code == 400
    assert _jobs(conn) == []


def test_a_list_with_nothing_ticked_is_refused_and_queues_nothing(client, conn, monkeypatch):
    _refuse_to_probe(monkeypatch)

    resp = client.post(
        "/transcribe/url",
        data={"url": FEED_URL, "feed_url": FEED_URL, "feed_title": "The Hitchhiker Lectures"},
        headers=HX,
    )

    assert resp.status_code == 400
    assert "tick at least one" in resp.json()["detail"]
    assert _jobs(conn) == []


def test_a_post_with_neither_still_queues_one_job_on_the_link(client, conn):
    """Today's door, unchanged: a single video, a paste without scripting, a
    feed whose listing timed out - the child's fan-out stays the fallback."""
    resp = client.post("/transcribe/url", data={"url": URL}, headers=HX)

    assert resp.status_code == 200
    rows = _jobs(conn)
    assert len(rows) == 1 and _params(rows[0])["url"] == URL
    assert set(_params(rows[0])) == {"url", "folder_id", "options"}


def test_the_cookies_file_travels_to_every_episode_job(client, conn, roots):
    cookies = roots / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")

    _post_episodes(client, _entries(2), cookies_file=str(cookies))

    assert [_params(row)["cookies_file"] for row in _jobs(conn)] == [str(cookies)] * 2


def test_the_flash_names_the_feed_and_the_count(client):
    body = _post_episodes(client, _entries(3)).text

    assert "Fetching 3 episodes of The Hitchhiker Lectures" in body


def test_a_feed_title_is_cut_for_the_flash(client):
    body = _post_episodes(client, _entries(1), feed_title="y" * 400).text

    assert "y" * ingest_ui.MAX_FEED_TITLE in body
    assert "y" * (ingest_ui.MAX_FEED_TITLE + 1) not in body


# --- never an href -------------------------------------------------------------------


def test_a_javascript_source_url_on_a_job_is_text_on_the_details_page_never_an_href(client, conn):
    job_id = jobs.enqueue(
        conn, url_stage.JOB_TYPE,
        params={"url": "https://example.test/x", "source": {"url": "javascript:alert(1)", "title": "t"}},
    )

    body = client.get(f"/jobs/{job_id}/details", headers=HX).text

    assert 'href="javascript:' not in body
    assert "javascript:alert(1)" in html.unescape(body)


def test_no_template_binds_an_href_to_a_stranger_s_url():
    """A URL that came over the network is text on every page it reaches.
    Autoescaping quotes an attribute correctly and does nothing about a
    javascript: scheme, so the invariant is "never an href"."""
    from scribe import web

    names = r"(source_url|webpage_url|feed_url|params\.url|source\.url|entry\.url|e\.url)"
    offenders, mentions = [], 0
    for path in sorted(Path(web.TEMPLATES_DIR).glob("*.html")):
        text = path.read_text(encoding="utf-8")
        mentions += len(re.findall(names, text))
        for match in re.finditer(r"""href=["']\{\{[^}]*""" + names, text):
            offenders.append((path.name, match.group(0)))

    assert offenders == []
    assert mentions > 0, "the guard tests nothing if no template names one of these"


# --- the episode list's conveniences, which live in JavaScript (TASK-021) -------------

EPISODES_FIXTURE = r"""
    /* The URL panel as _url_panel.html renders a listing into it: the
       sources block with the link tab's radio, the panel with the toolbar,
       three rows whose data-search carries a date, and the Import submit.
       data-max is 2 so the cap is reachable with three rows. */
    const sources = form.append(el('div', { 'data-sources': '' }));
    const srcUrl = sources.append(el('input', { type: 'radio', id: 'src-url', name: 'source_tab' }));
    sources.append(urlRow.remove());
    const panel = sources.append(el('div', { id: 'url-preview', 'data-panel': 'url' }));
    const tools = panel.append(el('div', { 'data-episodes': '' }));
    const filter = tools.append(el('input', { type: 'search', 'data-episode-filter': '' }));
    const allShown = tools.append(el('button', { type: 'button', 'data-episodes-all': '' }));
    const none = tools.append(el('button', { type: 'button', 'data-episodes-none': '' }));
    const count = tools.append(el('span', { 'data-episodes-count': '', 'data-max': '2' }));
    const list = panel.append(el('ul', { class: 'episodes' }));
    function row(search) {
      const li = list.append(el('li'));
      const label = li.append(el('label', { class: 'episode', 'data-search': search }));
      const box = label.append(el('input', { type: 'checkbox', name: 'entry', value: '{}' }));
      return { label: label, box: box };
    }
    const r1 = row('love in the time of palantir 2025-03-14');
    const r2 = row('the indicator crossover 2026-08-28');
    const r3 = row('vogon poetry slam 2026-09-01');
    const importIt = panel.append(el('button', { type: 'submit', 'data-episodes-submit': '' }));
    function tick(r, on) {
      r.box.checked = on;
      fire(document, 'input', event({ target: r.box }));
    }
    function typeFilter(text) {
      filter.value = text;
      fire(document, 'input', event({ target: filter }));
    }
"""


@needs_node
def test_the_filter_matches_the_date_so_a_year_selects_that_years_episodes(tmp_path):
    """No digits in any title, so a match on "2025" can only come from the
    date the row carries beside it - "filter on 2025, All shown, Import" is
    the flow the list is sold on."""
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE + EPISODES_FIXTURE + r"""
    load(APP);
    typeFilter('2025');
    const hidden = [r1.label.hidden, r2.label.hidden, r3.label.hidden];
    fire(document, 'click', event({ target: allShown }));
    done({ hidden: hidden, ticked: [r1.box.checked, r2.box.checked, r3.box.checked], count: count.textContent });
""",
    )

    assert result["hidden"] == [False, True, True]
    assert result["ticked"] == [True, False, False]
    assert result["count"] == "1 selected"


@needs_node
def test_all_shown_ticks_only_what_the_filter_left_and_none_clears(tmp_path):
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE + EPISODES_FIXTURE + r"""
    load(APP);
    typeFilter('palantir');
    fire(document, 'click', event({ target: allShown }));
    const afterAll = [r1.box.checked, r2.box.checked, r3.box.checked];
    typeFilter('');
    const shownAgain = [r1.label.hidden, r2.label.hidden, r3.label.hidden];
    fire(document, 'click', event({ target: none }));
    done({ afterAll: afterAll, shownAgain: shownAgain,
           afterNone: [r1.box.checked, r2.box.checked, r3.box.checked], count: count.textContent });
""",
    )

    assert result["afterAll"] == [True, False, False]
    assert result["shownAgain"] == [False, False, False]
    assert result["afterNone"] == [False, False, False]
    assert result["count"] == "0 selected"


@needs_node
def test_ticking_past_the_cap_disables_import_and_says_so(tmp_path):
    """The server's 400 with the count is the backstop; with scripting on the
    cap is shown where the decision is made, before the press."""
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE + EPISODES_FIXTURE + r"""
    load(APP);
    tick(r1, true); tick(r2, true); tick(r3, true);
    const over = { disabled: importIt.disabled, count: count.textContent, marked: count.classList.contains('over') };
    tick(r3, false);
    done({ over: over, back: { disabled: importIt.disabled, count: count.textContent, marked: count.classList.contains('over') } });
""",
    )

    assert result["over"] == {"disabled": True, "count": "3 selected - at most 2 in one go", "marked": True}
    assert result["back"] == {"disabled": False, "count": "2 selected", "marked": False}


@needs_node
def test_listing_the_episodes_anyway_does_not_close_the_dialog_and_import_does(tmp_path):
    """CR-001's class again: the retry is a POST that always answers 200 and
    must leave the dialog open; Import is the work being done and closes it."""
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE + EPISODES_FIXTURE + r"""
    load(APP);
    const retry = panel.append(el('button', { type: 'button' }));
    fire(document.body, 'htmx:afterRequest', event({
      detail: { elt: retry, successful: true, requestConfig: { verb: 'POST' } }
    }));
    const afterRetry = dialog.closed;
    fire(document.body, 'htmx:afterRequest', event({
      detail: { elt: importIt, successful: true, requestConfig: { verb: 'POST' } }
    }));
    done({ afterRetry: afterRetry, afterImport: dialog.closed });
""",
    )

    assert result == {"afterRetry": 0, "afterImport": 1}


@needs_node
def test_a_dropped_link_opens_the_link_tab_fills_the_field_and_previews(tmp_path):
    """"Drop a URL", read literally: a link dragged from an address bar or a
    feed icon onto the dialog lands in the link field, and the field's own
    trigger (an `input` event) starts the preview. text/uri-list may carry
    several lines and comments (RFC 2483); the first real line is the link."""
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE + EPISODES_FIXTURE + r"""
    load(APP);
    let previews = 0;
    listen(urlField, 'input', function () { previews += 1; });
    const over = event({ target: sources });
    fire(document, 'dragover', over);
    const drop = event({ target: sources, dataTransfer: { files: [], getData: function (kind) {
      return kind === 'text/uri-list' ? '# from a feed icon\r\nhttps://feeds.npr.org/510289/podcast.xml\r\n' : '';
    } } });
    fire(document, 'drop', drop);
    done({ dragPrevented: over.defaultPrevented, prevented: drop.defaultPrevented, tab: srcUrl.checked,
           value: urlField.value, previews: previews, focused: urlField.focused || 0 });
""",
    )

    assert result["dragPrevented"] == 1 and result["prevented"] == 1
    assert result["tab"] is True
    assert result["value"] == "https://feeds.npr.org/510289/podcast.xml"
    assert result["previews"] == 1
    assert result["focused"] == 1


@needs_node
def test_a_dropped_text_that_is_not_a_link_is_ignored(tmp_path):
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE + EPISODES_FIXTURE + r"""
    load(APP);
    const drop = event({ target: sources, dataTransfer: { files: [], getData: function (kind) {
      return kind === 'text/plain' ? 'D:\\music\\x.mp3' : '';
    } } });
    fire(document, 'drop', drop);
    done({ tab: srcUrl.checked, value: urlField.value });
""",
    )

    assert result["tab"] is False
    assert result["value"] == ""


@needs_node
def test_enter_in_the_link_field_with_a_list_present_still_presses_the_row_button(tmp_path):
    """The row button hides under CSS while a list shows, but Enter still
    clicks it - and that is harmless now, because the route it posts to
    honours the ticks. The key must keep going to the row, not to the list."""
    result = run_dom(
        tmp_path,
        DIALOG_FIXTURE + EPISODES_FIXTURE + r"""
    load(APP);
    tick(r1, true);
    const ev = event({ key: 'Enter', target: urlField });
    fire(document, 'keydown', ev);
    done({ fetchIt: fetchIt.clicked, importIt: importIt.clicked, addFile: addFile.clicked, prevented: ev.defaultPrevented });
""",
    )

    assert result == {"fetchIt": 1, "importIt": 0, "addFile": 0, "prevented": 1}
