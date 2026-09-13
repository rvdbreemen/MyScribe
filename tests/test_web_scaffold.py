"""Phase 3 Task 1: the web scaffold.

Four things and nothing more: the base page comes back with its navigation
and the vendored htmx; the static files are served with the right types;
`.env` loading respects what the environment already says; and the seed
helpers every later UI test builds on write rows the schema actually accepts.
No GPU, no models, no pipeline - a seeded temp database and the TestClient.
"""

import os

import re
import pytest

from scribe import web
from fastapi.testclient import TestClient

import scribe
from scribe import db, env
from scribe.app import create_app
from seed import seed_job, seed_media, seed_run


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


@pytest.fixture
def environ(monkeypatch):
    """A throwaway process environment, so a test cannot leak keys.

    `monkeypatch.delenv(raising=False)` on a key that does not exist records
    nothing, so a key the code under test then sets would survive the test.
    Swapping the whole mapping out is the only leak-proof sandbox.
    """
    sandbox: dict[str, str] = {}
    monkeypatch.setattr(os, "environ", sandbox)
    return sandbox


# --- the base page ------------------------------------------------------------


def test_index_is_an_html_page_with_the_nav_and_htmx(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "/static/htmx.min.js" in body
    assert "/static/app.js" in body
    assert "/static/app.css" in body
    assert "<main" in body
    for label, href in (("Library", "/"), ("Jobs", "/jobs"), ("Settings", "/settings")):
        assert f'href="{href}"' in body
        assert label in body
    assert scribe.__version__ in body


# --- static files -------------------------------------------------------------


def test_htmx_is_served_from_static_as_javascript(client):
    resp = client.get("/static/htmx.min.js")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/javascript")
    assert b"htmx" in resp.content


def test_the_apps_own_css_and_js_are_served_with_their_types(client):
    assert client.get("/static/app.css").headers["content-type"].startswith("text/css")
    assert client.get("/static/app.js").headers["content-type"].startswith("text/javascript")


# --- the theme switch ---------------------------------------------------------

# The palette is one `light-dark()` list, so the failure these guard against is
# not "the dark value is wrong" - a test cannot know that - but "a colour was
# added with only one value", which used to be invisible until someone with a
# dark desktop opened the page.

THEME_RE = re.compile(r"^\s*(--[a-z0-9-]+)\s*:\s*(.+?);\s*$", re.MULTILINE)

# Scales, not colours: these are the tokens that deliberately do not change
# when the lights go out.
NOT_COLOURS = {
    "--r-xs", "--r-sm", "--r-md", "--r-lg", "--r-pill",
    "--mono", "--sans",
    # Composites: offsets plus a colour token that is itself light-dark().
    "--shadow-sm", "--shadow-md", "--shadow-lg",
}


def _tokens(css: str) -> dict[str, str]:
    head = css.split("* { box-sizing: border-box; }")[0]
    return {name: value for name, value in THEME_RE.findall(head)}


def test_every_colour_token_carries_both_a_light_and_a_dark_value(client):
    tokens = _tokens(client.get("/static/app.css").text)

    assert "--bg" in tokens and "--accent" in tokens, "the token block moved"
    one_sided = [
        name
        for name, value in tokens.items()
        if name not in NOT_COLOURS and not value.startswith("light-dark(")
    ]
    assert one_sided == [], f"colour tokens with no dark value: {one_sided}"


def test_the_shadows_are_offsets_over_a_token_that_has_both_values(client):
    tokens = _tokens(client.get("/static/app.css").text)

    assert tokens["--shadow-color"].startswith("light-dark(")
    for name in ("--shadow-sm", "--shadow-md", "--shadow-lg"):
        assert "var(--shadow-color" in tokens[name]


def test_the_switch_sets_color_scheme_so_the_browsers_own_widgets_follow(client):
    """Scrollbars, <audio>, <progress> and a <select>'s drop-down are painted
    by the browser, not by this stylesheet. Without these three rules they
    would follow the operating system while everything else followed the
    switch - a dark audio player on a page the reader asked to be light."""
    css = client.get("/static/app.css").text

    assert "color-scheme: light dark;" in css
    assert ':root[data-theme="light"] { color-scheme: light; }' in css
    assert ':root[data-theme="dark"] { color-scheme: dark; }' in css


def test_theme_js_is_served_and_loaded_before_the_first_paint(client):
    """Not deferred, unlike every other script: the theme has to be on <html>
    before anything is painted, or the page flashes the other one."""
    body = client.get("/").text

    head = body.split("</head>")[0]
    assert re.search(r'<script src="/static/theme\.js[^"]*">', head)
    assert not re.search(r'<script src="/static/theme\.js[^"]*"\s+defer', head)
    assert client.get("/static/theme.js").headers["content-type"].startswith("text/javascript")


def test_the_switch_offers_system_light_and_dark_with_system_pressed(client):
    """Three states, not two. Following the operating system is what the page
    did before this control existed, so it stays reachable - and it is what an
    untouched page still does, which is why it is the one pressed in the HTML
    the server sends."""
    body = client.get("/").text

    for choice in ("system", "light", "dark"):
        assert f'data-theme-choice="{choice}"' in body
    assert 'data-theme-choice="system" aria-pressed="true"' in body


# --- .env ---------------------------------------------------------------------


def test_load_dotenv_sets_missing_keys_and_leaves_existing_ones_alone(tmp_path, environ):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# a comment\n"
        "\n"
        "SCRIBE_T1_A=1\n"
        'SCRIBE_T1_B="two words"\n'
        "SCRIBE_T1_C='single'\n"
        "  SCRIBE_T1_D = spaced  \n"
        "this line is not a pair\n",
        encoding="utf-8",
    )
    environ["SCRIBE_T1_B"] = "already"

    values = env.load_dotenv(dotenv)

    assert values == {
        "SCRIBE_T1_A": "1",
        "SCRIBE_T1_B": "two words",
        "SCRIBE_T1_C": "single",
        "SCRIBE_T1_D": "spaced",
    }
    assert environ["SCRIBE_T1_A"] == "1"
    assert environ["SCRIBE_T1_B"] == "already"  # the environment wins over the file
    assert environ["SCRIBE_T1_C"] == "single"
    assert environ["SCRIBE_T1_D"] == "spaced"
    assert not any(key.startswith("#") for key in environ)


def test_load_dotenv_without_a_file_is_a_quiet_no_op(tmp_path, environ):
    assert env.load_dotenv(tmp_path / "missing.env") == {}
    # pytest itself writes PYTEST_CURRENT_TEST into the environment mid-test,
    # so "nothing was set" means nothing of ours, not an empty mapping.
    assert not any(key.startswith("SCRIBE_") for key in environ)


def test_load_dotenv_defaults_to_the_repository_env_file():
    assert env.DEFAULT_PATH.name == ".env"
    assert (env.DEFAULT_PATH.parent / "scribe" / "app.py").exists()


def test_load_dotenv_reads_the_file_scribe_env_file_names(tmp_path, environ):
    """An installed copy runs from a read-only source tree, so its launcher
    keeps `.env` in the user's MyScribe folder and names it here (ADR-011)."""
    dotenv = tmp_path / "home" / ".env"
    dotenv.parent.mkdir()
    dotenv.write_text("SCRIBE_T2_FROM_HOME=yes\n", encoding="utf-8")
    environ["SCRIBE_ENV_FILE"] = str(dotenv)

    values = env.load_dotenv()

    assert values == {"SCRIBE_T2_FROM_HOME": "yes"}
    assert environ["SCRIBE_T2_FROM_HOME"] == "yes"


def test_create_app_does_not_read_the_repository_env_file(db_path, monkeypatch):
    """`.env` is `python -m scribe`'s to load, before scribe.paths reads the
    environment. An app built anywhere else - every test in this suite -
    must not pull a developer's HF_TOKEN or SCRIBE_DATA_DIR into the process."""
    calls: list = []
    monkeypatch.setattr(env, "load_dotenv", lambda *args, **kwargs: calls.append(args) or {})

    create_app(db_path=db_path, start_supervisor=False)

    assert calls == []


# --- seed helpers -------------------------------------------------------------


def test_seed_media_writes_a_row_the_schema_accepts(conn):
    first = seed_media(conn)
    second = seed_media(conn, title="Other", duration=12.5)

    rows = conn.execute("SELECT * FROM media ORDER BY id").fetchall()
    assert [r["id"] for r in rows] == [first, second]
    assert rows[0]["title"] == "Clip"
    assert rows[0]["duration"] == 30.0
    assert rows[0]["folder_id"] is None
    assert rows[0]["trashed_at"] is None
    assert len(rows[0]["sha256"]) == 64
    assert rows[0]["store_path"]
    assert rows[1]["title"] == "Other"
    assert rows[1]["duration"] == 12.5
    assert rows[0]["sha256"] != rows[1]["sha256"]  # UNIQUE(sha256) must hold


def test_seed_run_round_trips_words_segments_and_labels(conn):
    media_id = seed_media(conn)

    run_id = seed_run(conn, media_id, labels={"SPEAKER_00": "Arthur"})

    def count(table):
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)
        ).fetchone()[0]

    assert count("word") == 40
    assert count("segment") == 4
    assert count("speaker_label") == 1

    run = conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
    assert run["media_id"] == media_id
    assert run["is_current"] == 1
    assert run["model"]

    words = conn.execute(
        "SELECT * FROM word WHERE run_id=? ORDER BY idx", (run_id,)
    ).fetchall()
    assert [w["idx"] for w in words] == list(range(40))
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)
    assert all(w["end"] > w["start"] for w in words)
    assert all(w["probability"] is not None for w in words)
    assert all(w["text"].startswith(" ") for w in words)  # faster-whisper's leading space
    assert {w["speaker"] for w in words} == {"SPEAKER_00", "SPEAKER_01"}

    label = conn.execute(
        "SELECT * FROM speaker_label WHERE run_id=?", (run_id,)
    ).fetchone()
    assert (label["cluster_label"], label["display_name"]) == ("SPEAKER_00", "Arthur")

    # The segments went through the FTS triggers, so search can find them.
    segments = conn.execute(
        "SELECT * FROM segment WHERE run_id=? ORDER BY idx", (run_id,)
    ).fetchall()
    assert all(s["text"] for s in segments)
    hits = conn.execute(
        "SELECT rowid FROM segment_fts WHERE segment_fts MATCH 'towel'"
    ).fetchall()
    assert segments[0]["id"] in [h["rowid"] for h in hits]


def test_seed_run_moves_the_current_flag_to_the_latest_run(conn):
    media_id = seed_media(conn)
    first = seed_run(conn, media_id)
    second = seed_run(conn, media_id)
    third = seed_run(conn, media_id, current=False)

    def is_current(run_id):
        return conn.execute("SELECT is_current FROM run WHERE id=?", (run_id,)).fetchone()[0]

    assert (is_current(first), is_current(second), is_current(third)) == (0, 1, 0)


def test_seed_run_takes_custom_words_and_derives_a_segment_for_them(conn):
    media_id = seed_media(conn)

    run_id = seed_run(
        conn,
        media_id,
        words=[
            {"start": 0.0, "end": 0.4, "text": " Mostly", "speaker": "SPEAKER_00"},
            {"start": 0.5, "end": 0.9, "text": " harmless.", "speaker": "SPEAKER_00"},
        ],
    )

    words = conn.execute(
        "SELECT * FROM word WHERE run_id=? ORDER BY idx", (run_id,)
    ).fetchall()
    assert [w["text"] for w in words] == [" Mostly", " harmless."]
    assert [w["idx"] for w in words] == [0, 1]
    segments = conn.execute(
        "SELECT * FROM segment WHERE run_id=?", (run_id,)
    ).fetchall()
    assert len(segments) == 1
    assert segments[0]["text"] == "Mostly harmless."
    assert (segments[0]["start"], segments[0]["end"]) == (0.0, 0.9)


def test_seed_job_writes_a_running_job_the_api_lists(client, conn):
    media_id = seed_media(conn)

    job_id = seed_job(conn, media_id)

    row = client.get(f"/api/jobs/{job_id}").json()
    assert row["status"] == "running"
    assert row["stage"] == "transcribe"
    assert row["stage_progress"] == 0.4
    assert row["media_id"] == media_id
    assert row["type"] == "transcribe"
    assert row["started_at"] is not None
    assert row["finished_at"] is None


def test_seed_job_knows_a_queued_job_has_not_started_and_a_failed_one_has_finished(client, conn):
    media_id = seed_media(conn)

    queued = seed_job(conn, media_id, status="queued")
    failed = seed_job(conn, media_id, status="failed", error_code="CUDA_OOM")

    q = client.get(f"/api/jobs/{queued}").json()
    assert q["status"] == "queued"
    assert q["stage"] is None
    assert q["started_at"] is None
    assert q["queue_position"] == 1

    f = client.get(f"/api/jobs/{failed}").json()
    assert f["status"] == "failed"
    assert f["error_code"] == "CUDA_OOM"
    assert f["started_at"] is not None
    assert f["finished_at"] is not None


def test_a_changed_stylesheet_reaches_the_browser_without_a_hard_refresh(client, tmp_path):
    """StaticFiles sends an ETag and a Last-Modified and no Cache-Control, so a
    browser is free to guess how long it may keep the file - and Chrome guessed
    long enough that a restyled page kept rendering the old stylesheet until
    somebody knew to press ctrl-shift-R. Observed 2026-09-05 while changing the
    palette: the CSS on the wire was new, the page was not.

    There is no build step to fingerprint the file, so the URL carries the
    file's own modification time. Editing app.css changes the URL, which is the
    one thing a cache cannot ignore.
    """
    first = re.search(r'href="(/static/app\.css[^"]*)"', client.get("/").text)
    assert first, "no stylesheet link on the page"
    assert "?" in first.group(1), f"the stylesheet URL carries no version: {first.group(1)}"

    css = web.STATIC_DIR / "app.css"
    was = css.stat().st_mtime
    try:
        os.utime(css, (was + 10, was + 10))
        second = re.search(r'href="(/static/app\.css[^"]*)"', client.get("/").text)
        assert second.group(1) != first.group(1), (
            "the stylesheet URL did not change after the file did, so a cached "
            "copy would still be served"
        )
    finally:
        os.utime(css, (was, was))


def test_the_asset_version_does_not_change_when_nothing_does(client):
    """A version that moves on every render defeats caching entirely, which is
    the opposite failure and just as bad on a page that reloads constantly."""
    once = re.search(r'href="(/static/app\.css[^"]*)"', client.get("/").text).group(1)
    twice = re.search(r'href="(/static/app\.css[^"]*)"', client.get("/").text).group(1)

    assert once == twice


def test_a_closed_row_menu_is_not_painted(client):
    """The bug this guards: `.menu-body { display: flex }` is an author rule and
    beats the browser's own `[popover]:not(:popover-open) { display: none }`, so
    every menu in the table stayed drawn at once and opening one changed
    nothing but the top layer. A rule whose subject is the panel itself may
    only give it a `display` while it is open."""
    css = re.sub(r"/\*.*?\*/", "", client.get("/static/app.css").text, flags=re.S)

    for selectors, body in re.findall(r"([^{}]*)\{([^}]*)\}", css):
        if not re.search(r"(^|;)\s*display\s*:", body):
            continue
        for selector in selectors.split(","):
            selector = selector.strip()
            # The subject is the panel when the compound ends in .menu-body;
            # `.menu-body form` is about a child and may lay itself out freely.
            if not re.search(r"\.menu-body$", selector):
                continue
            assert ":popover-open" in selector, (
                f"{selector!r} sets display unconditionally, "
                "which keeps a closed menu painted"
            )
