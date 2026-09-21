"""Phase 3 Task 3: the library page.

A seeded library - three live files across two folders and one in the trash -
seen through the sidebar views, the folder filter, the htmx fragment, the
full-text search, and every file and folder action the page offers. No GPU,
no models, no pipeline: rows come from tests/seed.py and the requests go
through FastAPI's TestClient.

The store directory is redirected to tmp_path, so the one test that needs a
real file on disk (download, with a Range request) writes it where the media
row says it is, and nothing here touches the repository's data/ directory.
"""

import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from scribe import db, jobs, paths
from scribe.app import create_app
from scribe.web import library as web_library
from seed import seed_job, seed_media, seed_run


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


def _folder(conn, name, parent_id=None):
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO folder(name, parent_id) VALUES (?, ?)", (name, parent_id)
        )
        conn.commit()
        return cur.lastrowid


def _media(conn, media_id):
    return conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()


@pytest.fixture
def library(conn):
    """Two folders (one nested), three live files, one trashed file."""
    docs = _folder(conn, "Docs")
    meetings = _folder(conn, "Meetings", parent_id=docs)
    alpha = seed_media(conn, title="Alpha", folder_id=docs, duration=65.0)
    beta = seed_media(conn, title="Beta", folder_id=meetings, duration=3661.0)
    gamma = seed_media(conn, title="Gamma", duration=12.0)
    trashed = seed_media(conn, title="Trashed", duration=5.0)
    with db.LOCK:
        conn.execute(
            "UPDATE media SET trashed_at=? WHERE id=?", (time.time(), trashed)
        )
        conn.commit()
    return {
        "docs": docs,
        "meetings": meetings,
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "trashed": trashed,
    }


HX = {"HX-Request": "true"}


# --- views ---------------------------------------------------------------------


def test_library_lists_live_media_and_hides_the_trashed_one(client, library):
    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.text
    assert "<html" in body
    for title in ("Alpha", "Beta", "Gamma"):
        assert title in body
    assert "Trashed" not in body
    # Every live title links to its transcript page.
    assert f'href="/media/{library["alpha"]}"' in body


def test_trash_view_lists_exactly_the_trashed_media(client, library):
    body = client.get("/?view=trash").text

    assert "Trashed" in body
    for title in ("Alpha", "Beta", "Gamma"):
        assert f">{title}<" not in body


def test_uncategorized_view_lists_only_media_without_a_folder(client, library):
    body = client.get("/?view=uncategorized").text

    assert ">Gamma<" in body
    assert ">Alpha<" not in body
    assert ">Beta<" not in body
    assert ">Trashed<" not in body


def test_folder_filter_lists_only_that_folders_media(client, library):
    body = client.get(f"/?folder={library['docs']}").text

    assert ">Alpha<" in body
    assert ">Beta<" not in body  # a subfolder's file is not the folder's file
    assert ">Gamma<" not in body


def test_sort_keys_are_whitelisted(client, library):
    assert client.get("/?sort=title").status_code == 200
    assert client.get("/?sort=duration").status_code == 200
    assert client.get("/?sort=created_at").status_code == 200
    assert client.get("/?sort=sha256").status_code == 400


def test_title_sort_orders_the_rows(client, library):
    body = client.get("/?sort=title").text

    assert body.index(">Alpha<") < body.index(">Beta<") < body.index(">Gamma<")


def test_duration_sort_orders_the_rows_longest_first(client, library):
    body = client.get("/?sort=duration").text

    # Beta 3661 s, Alpha 65 s, Gamma 12 s.
    assert body.index(">Beta<") < body.index(">Alpha<") < body.index(">Gamma<")


def test_hx_request_returns_the_rows_fragment_without_the_page(client, library):
    resp = client.get("/", headers=HX)

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    assert "<table" in body
    assert ">Alpha<" in body


def _folder_count(body, folder_id, name):
    """The count the sidebar shows next to the link of folder ``folder_id``."""
    found = re.search(
        rf'href="/\?folder={folder_id}">{name}</a>\s*<span class="count">(\d+)</span>', body
    )
    assert found, f"no count next to folder {name!r}"
    return int(found.group(1))


def test_sidebar_shows_the_folder_tree_with_counts(client, conn, library):
    body = client.get("/").text

    assert "Docs" in body and "Meetings" in body
    assert f'href="/?folder={library["docs"]}"' in body
    assert f'href="/?folder={library["meetings"]}"' in body
    assert 'href="/?view=recent"' in body
    assert 'href="/?view=uncategorized"' in body
    assert 'href="/?view=trash"' in body
    # The nested folder is rendered one level deeper than its parent.
    assert 'style="--depth: 0"' in body
    assert 'style="--depth: 1"' in body
    # Counts: live files only, and a folder counts its own files, not its
    # subfolder's (Docs holds Alpha; Beta is Meetings').
    assert 'All files <span class="count">3</span>' in body
    assert 'Uncategorized <span class="count">1</span>' in body
    assert 'Trash <span class="count">1</span>' in body
    assert _folder_count(body, library["docs"], "Docs") == 1
    assert _folder_count(body, library["meetings"], "Meetings") == 1

    # Trashing a file moves it out of its folder's count and into the trash's.
    client.post(f"/media/{library['beta']}/trash")
    body = client.get("/").text
    assert 'All files <span class="count">2</span>' in body
    assert 'Trash <span class="count">2</span>' in body
    assert _folder_count(body, library["docs"], "Docs") == 1
    assert _folder_count(body, library["meetings"], "Meetings") == 0


def test_trash_view_offers_restore_only_and_no_destination_folder(client, library):
    trash = client.get("/?view=trash").text
    assert '<option value="restore">' in trash
    assert '<option value="move">' not in trash
    assert 'aria-label="Destination folder"' not in trash

    everything = client.get("/").text
    assert '<option value="move">' in everything
    assert 'aria-label="Destination folder"' in everything


def test_row_shows_mode_icon_and_latest_job_status(client, conn, library):
    seed_run(conn, library["alpha"])
    seed_job(conn, library["alpha"], status="done")
    seed_job(conn, library["beta"], status="failed", error_code="CUDA_OOM")

    body = client.get("/").text

    assert "⚡" in body  # large-v3-turbo is the default seed model
    assert "s-done" in body
    assert "s-failed" in body
    assert "1:05" in body  # Alpha's 65 s duration
    assert "1:01:01" in body  # Beta's 3661 s duration


# --- search --------------------------------------------------------------------


def test_search_links_each_hit_to_the_segment_start(client, conn, library):
    seed_run(conn, library["alpha"])
    vogon = conn.execute(
        "SELECT start FROM segment WHERE text LIKE 'Vogon%'"
    ).fetchone()["start"]

    resp = client.get("/search?q=Vogon")

    assert resp.status_code == 200
    body = resp.text
    assert "Alpha" in body
    assert f'href="/media/{library["alpha"]}#t={vogon}"' in body
    assert "<mark>Vogon</mark>" in body


def test_search_skips_trashed_media_and_non_current_runs(client, conn, library):
    seed_run(conn, library["trashed"])
    seed_run(conn, library["beta"], current=False)

    body = client.get("/search?q=Vogon").text

    assert "No matches" in body


def test_search_survives_quotes_and_operators_in_the_query(client, conn, library):
    seed_run(conn, library["alpha"])

    # A quote inside a term is part of the term, and the term is searched
    # (and the snippet text is escaped: the apostrophe comes out as &#39;).
    body = client.get("/search", params={"q": "don't"}).text
    assert "<mark>Don&#39;t</mark>" in body

    # Parentheses and stray quotes next to a real term are text, not syntax:
    # no error, and the real term still finds its segment.
    body = client.get("/search", params={"q": 'Vogon ("'}).text
    assert "<mark>Vogon</mark>" in body

    # Operators are text too: as syntax, OR would match two segments here;
    # as a word, nothing contains it.
    body = client.get("/search", params={"q": "Vogon OR Marvin"}).text
    assert "No matches" in body
    assert "<mark>" not in body


def test_search_does_not_query_the_library_rows(client, conn, library, monkeypatch):
    """The search page renders the sidebar, not the table; the rows query
    for the whole library is wasted work there and must not run."""
    seed_run(conn, library["alpha"])

    def no_rows(*args, **kwargs):
        raise AssertionError("media_rows() ran on the search page")

    monkeypatch.setattr(web_library, "media_rows", no_rows)

    resp = client.get("/search?q=Vogon")

    assert resp.status_code == 200
    assert "<mark>Vogon</mark>" in resp.text
    assert 'id="sidebar"' in resp.text


def test_search_caption_says_when_the_hits_are_capped_at_the_limit(
    client, conn, library, monkeypatch
):
    monkeypatch.setattr(web_library, "SEARCH_LIMIT", 2)
    for key in ("alpha", "beta", "gamma"):
        seed_run(conn, library[key])

    body = client.get("/search?q=Vogon").text

    assert body.count("<mark>Vogon</mark>") == 2
    assert "(showing the first 2)" in body


def test_search_says_it_matches_what_was_heard_when_corrections_exist(
    client, conn, library
):
    """Search reads `segment_fts`, which no correction touches - so it is a
    third reader of the transcript that `glossary.py` ("the two readers of
    words") does not know about. Measured: a page showing 'Welkom bij
    WHYcast.' answers 0 hits for "WHYcast" and 1 for "why cast", with the old
    spelling in the snippet.

    Teaching the index the corrected text is a migration, a backfill and a
    trigger, so what ships here is the honest sentence instead - shown only
    where it can bite, which is a library that has a correction layer at all.
    """
    run_id = seed_run(conn, library["alpha"])

    assert "as it was heard" not in client.get("/search?q=Vogon").text

    with db.LOCK:
        conn.execute(
            "INSERT INTO word_correction(run_id, word_idx, original, corrected,"
            " rule, confidence, created_at) VALUES (?, 0, 'Vogonn', 'Vogon', 'fuzzy', 0.9, 0)",
            (run_id,),
        )
        conn.commit()

    assert "as it was heard" in client.get("/search?q=Vogon").text


def test_a_plain_rail_post_goes_back_to_the_recording_page(client, library):
    """TASK-078: the transcript rail's rename, move, trash and restore post to
    the library's routes, whose plain answer is a 303 to the library view -
    from /media/12 that was the library root. The Referer says where the
    person was, as it already does for the library's own view state."""
    media_id = library["alpha"]
    came_from = {"Referer": f"http://testserver/media/{media_id}"}

    renamed = client.post(
        f"/media/{media_id}/rename", data={"title": "Alpha again"}, headers=came_from, follow_redirects=False
    )
    trashed = client.post(f"/media/{media_id}/trash", headers=came_from, follow_redirects=False)

    assert (renamed.status_code, renamed.headers["location"]) == (303, f"/media/{media_id}")
    assert (trashed.status_code, trashed.headers["location"]) == (303, f"/media/{media_id}")


def test_a_referer_that_is_not_a_recording_page_keeps_the_library_redirect(client, library):
    resp = client.post(
        f"/media/{library['alpha']}/rename",
        data={"title": "Alpha"},
        headers={"Referer": "http://testserver/media/not-a-number"},
        follow_redirects=False,
    )

    assert (resp.status_code, resp.headers["location"]) == (303, "/")


def test_search_with_an_empty_query_redirects_to_the_library(client, library):
    resp = client.get("/search?q=", follow_redirects=False)

    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"].endswith("/")


# --- file actions --------------------------------------------------------------


def test_rename_changes_the_title_and_not_the_store_path(client, conn, library):
    before = _media(conn, library["alpha"])

    resp = client.post(
        f"/media/{library['alpha']}/rename", data={"title": "Alpha renamed"}, headers=HX
    )

    assert resp.status_code == 200
    after = _media(conn, library["alpha"])
    assert after["title"] == "Alpha renamed"
    assert after["store_path"] == before["store_path"]
    assert after["orig_name"] == before["orig_name"]
    assert "Alpha renamed" in resp.text


def test_a_mutation_answers_with_a_table_whose_cells_are_not_out_of_band(
    client, conn, library
):
    """TASK-058: the two meta cells belong in the table this response replaces.

    `hx-swap-oob` on a cell inside a full-table swap makes htmx lift it out and
    place it by id instead, so the table that lands is two cells short per row:
    the upload date slides into the Category column and the duration into
    Labels. The ids go with them, which is what the status cell's poll aims at.
    The flash is out of band on purpose and stays that way.
    """
    resp = client.post(
        f"/media/{library['alpha']}/rename", data={"title": "Alpha renamed"}, headers=HX
    )

    assert resp.status_code == 200
    cells = re.findall(r'<td class="(?:folder|labels)"[^>]*>', resp.text)
    assert cells, "the answer should still carry the two meta cells"
    assert not [cell for cell in cells if "hx-swap-oob" in cell]
    assert f'id="folder-{library["alpha"]}"' in resp.text
    assert f'id="labels-{library["alpha"]}"' in resp.text


def test_the_status_poll_still_carries_the_meta_cells_out_of_band(client, library):
    """The other half of TASK-058: the poll is where the flag belongs."""
    resp = client.get(f"/media/{library['alpha']}/status")

    assert resp.status_code == 200
    cells = re.findall(r'<td class="(?:folder|labels)"[^>]*>', resp.text)
    assert len(cells) == 2
    assert all("hx-swap-oob" in cell for cell in cells)


def test_rename_refuses_an_empty_title(client, conn, library):
    resp = client.post(f"/media/{library['alpha']}/rename", data={"title": "  "})

    assert resp.status_code == 400
    assert _media(conn, library["alpha"])["title"] == "Alpha"


def test_move_to_a_folder_and_back_to_uncategorized(client, conn, library):
    gamma = library["gamma"]

    client.post(f"/media/{gamma}/move", data={"folder_id": library["meetings"]}, headers=HX)
    assert _media(conn, gamma)["folder_id"] == library["meetings"]

    client.post(f"/media/{gamma}/move", data={"folder_id": ""}, headers=HX)
    assert _media(conn, gamma)["folder_id"] is None


def test_move_to_an_unknown_folder_is_a_404(client, conn, library):
    resp = client.post(f"/media/{library['gamma']}/move", data={"folder_id": 9999})

    assert resp.status_code == 404
    assert _media(conn, library["gamma"])["folder_id"] is None


def test_trash_then_restore_round_trips_trashed_at(client, conn, library):
    alpha = library["alpha"]
    assert _media(conn, alpha)["trashed_at"] is None

    client.post(f"/media/{alpha}/trash", headers=HX)
    trashed_at = _media(conn, alpha)["trashed_at"]
    assert trashed_at is not None
    assert abs(trashed_at - time.time()) < 5
    assert ">Alpha<" not in client.get("/").text
    assert ">Alpha<" in client.get("/?view=trash").text

    client.post(f"/media/{alpha}/restore", headers=HX)
    assert _media(conn, alpha)["trashed_at"] is None
    assert ">Alpha<" in client.get("/").text


def test_purge_removes_the_row_and_the_stored_file_of_a_trashed_media(
    client, conn, library, data_dir
):
    row = _media(conn, library["trashed"])
    stored = data_dir / row["store_path"]
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"bytes")
    # The browser-playable proxy the transcript view may have transcoded is
    # keyed by the same hash and goes with the row, or it would sit in
    # media/proxy forever with nothing pointing at it.
    proxy = data_dir / "media" / "proxy" / f"{row['sha256']}.m4a"
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"aac")

    resp = client.post(f"/media/{library['trashed']}/purge", headers=HX)

    assert resp.status_code == 200
    assert _media(conn, library["trashed"]) is None
    assert not stored.exists()
    assert not proxy.exists()


def test_purge_refuses_while_a_job_for_the_media_is_queued_or_running(client, conn, library):
    """`job.media_id` cascades from the media row. Deleting it under a runner
    child would leave the child burning GPU on rows that no longer exist and
    with nowhere to write its verdict; the file it holds open could not be
    unlinked either. So a live job blocks the purge until it is terminal."""
    trashed = library["trashed"]
    running = seed_job(conn, trashed, status="running")

    resp = client.post(f"/media/{trashed}/purge", headers=HX)

    assert resp.status_code == 409
    assert "running" in resp.json()["detail"] and str(running) in resp.json()["detail"]
    assert _media(conn, trashed) is not None
    assert conn.execute("SELECT status FROM job WHERE id=?", (running,)).fetchone()["status"] == "running"

    jobs.finish(conn, running, "cancelled")
    queued = seed_job(conn, trashed, status="queued")
    assert client.post(f"/media/{trashed}/purge", headers=HX).status_code == 409
    assert _media(conn, trashed) is not None

    jobs.request_cancel(conn, queued)  # a queued job dies instantly
    assert client.post(f"/media/{trashed}/purge", headers=HX).status_code == 200
    assert _media(conn, trashed) is None
    assert conn.execute("SELECT COUNT(*) FROM job WHERE media_id=?", (trashed,)).fetchone()[0] == 0


def test_purge_refuses_a_media_that_is_not_in_the_trash(client, conn, library):
    resp = client.post(f"/media/{library['alpha']}/purge")

    assert resp.status_code == 409
    assert _media(conn, library["alpha"]) is not None


def test_unknown_media_is_a_404(client, library):
    assert client.post("/media/9999/trash").status_code == 404
    assert client.get("/media/9999/download").status_code == 404


def test_a_plain_post_redirects_back_to_the_library_view_it_came_from(client, library):
    resp = client.post(
        f"/media/{library['alpha']}/trash",
        headers={"Referer": "http://testserver/?view=uncategorized&sort=title"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/?view=uncategorized&sort=title"


def test_a_post_that_cannot_use_html_gets_no_content_instead_of_a_redirect(client, conn, library):
    """app.js's data-refresh forms (the transcript rail) re-fetch their own
    panel after the post and want nothing back. They say so with an Accept
    that has no text/html in it, and get a 204 - not a 303 that fetch would
    follow into a full library page nobody reads."""
    resp = client.post(
        f"/media/{library['alpha']}/rename",
        data={"title": "Alpha quietly"},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert resp.status_code == 204
    assert resp.content == b""
    assert _media(conn, library["alpha"])["title"] == "Alpha quietly"

    # A browser's form post (Accept lists text/html, or */*) still redirects.
    for accept in ("text/html,application/xhtml+xml,*/*;q=0.8", "*/*"):
        resp = client.post(
            f"/media/{library['alpha']}/rename",
            data={"title": "Alpha loudly"},
            headers={"Accept": accept},
            follow_redirects=False,
        )
        assert resp.status_code == 303, accept


def test_an_htmx_post_returns_the_rows_with_the_sidebar_swapped_out_of_band(
    client, library
):
    resp = client.post(
        f"/media/{library['gamma']}/trash",
        headers={**HX, "HX-Current-URL": "http://testserver/?view=uncategorized"},
    )

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    assert "<table" in body
    assert ">Gamma<" not in body  # the uncategorized view no longer has it
    assert 'id="sidebar"' in body and 'hx-swap-oob="true"' in body


# --- bulk ------------------------------------------------------------------------


def test_bulk_move_of_two_ids(client, conn, library):
    resp = client.post(
        "/media/bulk",
        data={
            "ids": [library["alpha"], library["gamma"]],
            "action": "move",
            "folder_id": library["meetings"],
        },
        headers=HX,
    )

    assert resp.status_code == 200
    assert _media(conn, library["alpha"])["folder_id"] == library["meetings"]
    assert _media(conn, library["gamma"])["folder_id"] == library["meetings"]
    assert _media(conn, library["beta"])["folder_id"] == library["meetings"]  # untouched


def test_bulk_trash_and_restore(client, conn, library):
    ids = [library["alpha"], library["beta"]]

    client.post("/media/bulk", data={"ids": ids, "action": "trash"}, headers=HX)
    assert all(_media(conn, i)["trashed_at"] is not None for i in ids)
    assert _media(conn, library["gamma"])["trashed_at"] is None

    client.post("/media/bulk", data={"ids": ids, "action": "restore"}, headers=HX)
    assert all(_media(conn, i)["trashed_at"] is None for i in ids)


def test_bulk_retranscribe_enqueues_a_job_per_id_with_the_last_params(
    client, conn, library
):
    with db.LOCK:
        conn.execute(
            "INSERT INTO job(type, media_id, status, params_json, created_at)"
            " VALUES ('transcribe', ?, 'done', '{\"language\": \"nl\"}', ?)",
            (library["alpha"], time.time()),
        )
        conn.commit()

    client.post(
        "/media/bulk",
        data={"ids": [library["alpha"], library["gamma"]], "action": "retranscribe"},
        headers=HX,
    )

    queued = conn.execute(
        "SELECT media_id, params_json FROM job WHERE status='queued' ORDER BY id"
    ).fetchall()
    assert [(q["media_id"], q["params_json"]) for q in queued] == [
        (library["alpha"], '{"language": "nl"}'),
        (library["gamma"], "{}"),
    ]


def test_the_bulk_form_asks_before_it_acts(client, library):
    """One Apply can trash every checked file or queue a GPU job per file;
    the form asks first, as every per-row destructive form does."""
    for url in ("/", "/?view=trash"):
        body = client.get(url).text
        form = re.search(r'<form id="bulk-form"[^>]*>', body)
        assert form, f"no bulk form on {url}"
        assert "hx-confirm=" in form.group(0)


def test_bulk_without_a_selection_or_with_an_unknown_action_is_a_400(client, library):
    assert client.post("/media/bulk", data={"action": "trash"}).status_code == 400
    assert (
        client.post(
            "/media/bulk", data={"ids": [library["alpha"]], "action": "explode"}
        ).status_code
        == 400
    )


# --- folders ---------------------------------------------------------------------


def test_create_and_rename_a_folder(client, conn, library):
    client.post("/folders", data={"name": "Ideas", "parent_id": library["docs"]}, headers=HX)
    row = conn.execute("SELECT * FROM folder WHERE name='Ideas'").fetchone()
    assert row is not None
    assert row["parent_id"] == library["docs"]

    client.post(f"/folders/{row['id']}/rename", data={"name": "Better ideas"}, headers=HX)
    assert conn.execute("SELECT name FROM folder WHERE id=?", (row["id"],)).fetchone()[0] == "Better ideas"

    assert client.post("/folders", data={"name": " "}).status_code == 400
    assert client.post("/folders", data={"name": "Orphan", "parent_id": 9999}).status_code == 404


def test_folder_delete_refuses_when_non_empty_and_moves_contents_with_force(
    client, conn, library
):
    meetings = library["meetings"]

    refused = client.post(f"/folders/{meetings}/delete", headers=HX)
    assert refused.status_code == 409
    assert conn.execute("SELECT 1 FROM folder WHERE id=?", (meetings,)).fetchone()

    forced = client.post(f"/folders/{meetings}/delete", data={"force": "1"}, headers=HX)
    assert forced.status_code == 200
    assert conn.execute("SELECT 1 FROM folder WHERE id=?", (meetings,)).fetchone() is None
    assert _media(conn, library["beta"])["folder_id"] == library["docs"]  # moved to the parent


def test_deleting_the_folder_you_are_looking_at_sends_you_home(client, conn, library):
    docs = library["docs"]

    hx = client.post(
        f"/folders/{docs}/delete",
        data={"force": "1"},
        headers={**HX, "HX-Current-URL": f"http://testserver/?folder={docs}"},
    )
    assert hx.status_code == 200
    assert hx.headers["HX-Redirect"] == "/"
    assert conn.execute("SELECT 1 FROM folder WHERE id=?", (docs,)).fetchone() is None

    empty = _folder(conn, "Empty")
    plain = client.post(
        f"/folders/{empty}/delete",
        headers={"Referer": f"http://testserver/?folder={empty}&sort=title"},
        follow_redirects=False,
    )
    assert plain.status_code == 303
    assert plain.headers["location"] == "/?sort=title"


def test_deleting_an_empty_root_folder_needs_no_force(client, conn, library):
    empty = _folder(conn, "Empty")

    resp = client.post(f"/folders/{empty}/delete", headers=HX)

    assert resp.status_code == 200
    assert conn.execute("SELECT 1 FROM folder WHERE id=?", (empty,)).fetchone() is None


def test_forced_delete_of_a_root_folder_leaves_its_files_uncategorized(
    client, conn, library
):
    client.post(f"/folders/{library['docs']}/delete", data={"force": "1"}, headers=HX)

    assert _media(conn, library["alpha"])["folder_id"] is None
    # The child folder moves up with it and keeps its file.
    assert conn.execute(
        "SELECT parent_id FROM folder WHERE id=?", (library["meetings"],)
    ).fetchone()[0] is None
    assert _media(conn, library["beta"])["folder_id"] == library["meetings"]


# --- download ----------------------------------------------------------------------


def test_download_sends_the_original_as_an_attachment_with_range_support(
    client, conn, library, data_dir
):
    row = _media(conn, library["alpha"])
    stored = data_dir / row["store_path"]
    stored.parent.mkdir(parents=True)
    payload = bytes(range(256)) * 4
    stored.write_bytes(payload)

    full = client.get(f"/media/{library['alpha']}/download")
    assert full.status_code == 200
    assert full.headers["content-disposition"] == 'attachment; filename="Alpha.wav"'
    assert full.content == payload

    part = client.get(
        f"/media/{library['alpha']}/download", headers={"Range": "bytes=0-9"}
    )
    assert part.status_code == 206
    assert part.headers["content-length"] == "10"
    assert part.headers["content-range"] == f"bytes 0-9/{len(payload)}"
    assert part.content == payload[:10]


def test_retranscribe_ignores_the_params_of_a_job_that_is_not_a_transcribe(
    client, conn, library
):
    """A chat question is a job on the same recording, and it was the most
    recent one. Observed 2026-09-03: asking the AI panel a question and then
    pressing re-transcribe queued a transcribe job carrying the language
    model's params, so the transcribe stage asked HuggingFace for
    'openai/gpt-5.6-luna' and failed with a 404. "The media's last params"
    has to mean the last *transcribe* params; a recording grew other job
    types in phases 5 and 6 and this query never noticed."""
    with db.LOCK:
        conn.execute(
            "INSERT INTO job(type, media_id, status, params_json, created_at)"
            " VALUES ('transcribe', ?, 'done', '{\"model\": \"large-v3-turbo\"}', ?)",
            (library["alpha"], time.time()),
        )
        conn.execute(  # later, so it wins on `ORDER BY id DESC` without a type filter
            "INSERT INTO job(type, media_id, status, params_json, created_at)"
            " VALUES ('llm', ?, 'done',"
            " '{\"kind\": \"chat\", \"provider\": \"openrouter\","
            " \"model\": \"openai/gpt-5.6-luna\"}', ?)",
            (library["alpha"], time.time()),
        )
        conn.commit()

    client.post(
        "/media/bulk",
        data={"ids": [library["alpha"]], "action": "retranscribe"},
        headers=HX,
    )

    queued = conn.execute(
        "SELECT params_json FROM job WHERE status='queued' ORDER BY id"
    ).fetchall()
    assert [q["params_json"] for q in queued] == ['{"model": "large-v3-turbo"}']


def test_retranscribe_does_not_repeat_a_model_that_is_not_a_speech_model(
    client, conn, library
):
    """The poisoned row heals itself rather than re-seeding.

    Filtering by job type was not enough on this machine: the bad job was
    *already* a transcribe job carrying a chat model, so "the last transcribe
    request" faithfully copied it forward and every re-transcribe queued
    another doomed job. A model that is not a checkpoint is dropped, and the
    job falls back to the default tier.
    """
    with db.LOCK:
        conn.execute(
            "INSERT INTO job(type, media_id, status, params_json, created_at)"
            " VALUES ('transcribe', ?, 'failed',"
            " '{\"model\": \"openai/gpt-5.6-luna\", \"language\": \"nl\","
            " \"kind\": \"chat\", \"provider\": \"openrouter\"}', ?)",
            (library["alpha"], time.time()),
        )
        conn.commit()

    client.post(
        "/media/bulk",
        data={"ids": [library["alpha"]], "action": "retranscribe"},
        headers=HX,
    )

    queued = conn.execute(
        "SELECT params_json FROM job WHERE status='queued' ORDER BY id"
    ).fetchall()
    params = json.loads(queued[0]["params_json"])
    assert "model" not in params  # dropped, so the stage takes its default
    assert params == {"language": "nl"}  # and the chat-only keys went with it


# --- the row menu, and the status that keeps itself current ------------------------


def test_the_row_menu_is_an_overlay_rather_than_something_that_grows_the_row(
    client, library
):
    """It was a <details> that expanded in place, because .table-wrap scrolls
    and an absolutely positioned child would be clipped at its edge whatever
    its z-index. `popover` puts the panel in the top layer instead, above
    every clip, and popovertarget drives it with no script - so the menu works
    before app.js has loaded."""
    body = client.get("/").text

    assert "<details class=\"menu\"" not in body
    for key in ("alpha", "beta", "gamma"):
        media_id = library[key]
        assert f'popovertarget="menu-media-{media_id}"' in body
        assert f'id="menu-media-{media_id}" popover' in body
        # No anchor names: the popover anchors to its invoker implicitly
        # (review #3, CR-019), so the markup carries no inline styles.
        assert f"anchor-name: --menu-media-{media_id}" not in body


def test_the_folder_menu_is_the_same_overlay(client, library):
    body = client.get("/").text

    assert f'popovertarget="menu-folder-{library["docs"]}"' in body
    assert f'id="menu-folder-{library["docs"]}" popover' in body


def test_a_running_row_asks_for_its_own_status_and_a_finished_one_stops(client, conn, library):
    """The library used to be a still photograph: a run finished and the row
    went on saying `running`. The cell refreshes itself now - and only the
    cell, because swapping the whole table would close an open menu and throw
    away a half-typed rename."""
    seed_job(conn, library["alpha"], status="running")
    seed_job(conn, library["beta"], status="done")

    body = client.get("/").text

    running = re.search(rf'<td class="status" id="status-{library["alpha"]}"[^>]*>', body).group(0)
    finished = re.search(rf'<td class="status" id="status-{library["beta"]}"[^>]*>', body).group(0)
    assert f'hx-get="/media/{library["alpha"]}/status"' in running
    assert 'hx-trigger="every 2s"' in running
    # Terminal: the answer carries no polling, which is what stops it.
    assert "hx-get" not in finished
    assert ">running<" in body and "\u2713 done" in body


def test_the_status_route_answers_the_one_cell_and_stops_polling_when_done(
    client, conn, library
):
    job = seed_job(conn, library["gamma"], status="queued")

    queued = client.get(f"/media/{library['gamma']}/status")

    assert queued.status_code == 200
    assert queued.text.strip().startswith("<td")
    assert 'hx-trigger="every 2s"' in queued.text
    assert f'href="/jobs/{job}"' in queued.text

    with db.LOCK:
        conn.execute("UPDATE job SET status='done' WHERE id=?", (job,))
        conn.commit()

    done = client.get(f"/media/{library['gamma']}/status").text

    assert "hx-get" not in done
    assert "\u2713 done" in done


# --- review #3 ---------------------------------------------------------------------


def test_the_status_badge_has_an_id_so_a_swap_gives_focus_back(client, conn, library):
    """CR-014. htmx restores focus after a swap only to an element with an id;
    the cell re-swaps every two seconds while a job runs, and a keyboard user
    resting on the badge was dropped to <body> each time."""
    job = seed_job(conn, library["alpha"], status="running")

    body = client.get("/").text

    assert f'id="job-badge-{job}"' in body


def test_the_row_menus_are_named_and_carry_no_inline_styles(client, library):
    """CR-026 and CR-019. The panel has an accessible name; the anchor is the
    popover's implicit one, so no per-row style attributes."""
    body = client.get("/").text

    assert 'popover class="menu-body" aria-label="Actions for Alpha"' in body
    assert "anchor-name:" not in body and "position-anchor:" not in body


def test_the_sort_direction_is_conveyed_on_the_header(client, library):
    """CR-029. Each key sorts one way (library.SORTS); aria-sort says which."""
    assert 'aria-sort="descending"' in client.get("/?sort=created_at").text
    assert 'aria-sort="ascending"' in client.get("/?sort=title").text
    assert 'aria-sort="descending"' in client.get("/?sort=duration").text


def test_the_flash_region_is_filled_not_replaced():
    """CR-016. A live region that is swapped out is announced by nothing; every
    out-of-band flash in the templates targets the region's contents. Pinned
    on the templates because the flash is optional on every route."""
    import re
    from pathlib import Path

    from scribe import web

    offenders = []
    for template in Path(web.TEMPLATES_DIR).glob("*.html"):
        text = template.read_text(encoding="utf-8")
        for tag in re.findall(r'<div id="flash"[^>]*hx-swap-oob="[^"]*"[^>]*>', text):
            if 'hx-swap-oob="innerHTML:#flash"' not in tag:
                offenders.append(f"{template.name}: {tag}")
    assert offenders == []
    # And at least one template does flash out of band, or this tests nothing.
    assert any('hx-swap-oob="innerHTML:#flash"' in p.read_text(encoding="utf-8")
               for p in Path(web.TEMPLATES_DIR).glob("*.html"))


# --- labels as a facet (TASK-023) --------------------------------------------------


def _attach_label(conn, media_id, name, source="llm"):
    with db.LOCK:
        conn.execute("INSERT OR IGNORE INTO label(name, created_at) VALUES (?, 0.0)", (name,))
        conn.execute(
            "INSERT OR IGNORE INTO media_label(media_id, label_id, source, created_at)"
            " SELECT ?, id, ?, 0.0 FROM label WHERE name = ? COLLATE NOCASE",
            (media_id, source, name),
        )
        conn.commit()


def test_filtering_on_a_label_lists_only_the_recordings_that_carry_it(client, conn, library):
    _attach_label(conn, library["alpha"], "hacking")
    _attach_label(conn, library["gamma"], "hacking")
    _attach_label(conn, library["beta"], "cooking")

    body = client.get("/?label=hacking").text

    assert "Alpha" in body and "Gamma" in body
    assert "Beta" not in body


def test_a_label_filter_crosses_folders(client, conn, library):
    """The point of a label: Alpha is in Docs and Gamma is in no folder, and
    one click gathers both. A folder cannot do that."""
    _attach_label(conn, library["alpha"], "hacking")
    _attach_label(conn, library["gamma"], "hacking")

    body = client.get("/?label=hacking").text

    assert "Alpha" in body and "Gamma" in body
    # Beta carries no label: without this the test passes on a page that
    # ignored the filter and listed the whole library.
    assert "Beta" not in body


def test_a_label_matches_regardless_of_case(client, conn, library):
    _attach_label(conn, library["alpha"], "hacking")

    body = client.get("/?label=Hacking").text

    assert "Alpha" in body
    assert "Beta" not in body and "Gamma" not in body


def test_the_sidebar_lists_every_label_with_how_many_carry_it(client, conn, library):
    _attach_label(conn, library["alpha"], "hacking")
    _attach_label(conn, library["gamma"], "hacking")
    _attach_label(conn, library["beta"], "cooking")

    ctx = web_library.sidebar_context(conn, web_library.State())

    assert ctx["labels"] == [
        {"name": "hacking", "count": 2},
        {"name": "cooking", "count": 1},
    ]


def test_the_sidebar_renders_the_labels_as_links_that_filter(client, conn, library):
    """The context test above proves the query; this proves the markup, which
    is the half a person actually clicks."""
    _attach_label(conn, library["alpha"], "hacker history")

    body = client.get("/").text

    assert "<h2>Labels</h2>" in body
    assert 'href="/?label=hacker%20history"' in body
    assert "hacker history" in body


def test_the_labels_heading_is_absent_when_nothing_is_labelled(client, library):
    assert "<h2>Labels</h2>" not in client.get("/").text


def test_a_trashed_recording_does_not_count_toward_a_label(client, conn, library):
    """The sidebar counts what a click would show, and a click does not show
    the trash - the same rule the folder counts already follow."""
    _attach_label(conn, library["alpha"], "hacking")
    _attach_label(conn, library["trashed"], "hacking")

    ctx = web_library.sidebar_context(conn, web_library.State())

    assert ctx["labels"] == [{"name": "hacking", "count": 1}]
    assert "Trashed" not in client.get("/?label=hacking").text


def test_a_label_that_nothing_carries_is_not_offered(client, conn, library):
    """A label whose last recording was purged is not wrong, only unused, and
    a sidebar row that always answers "nothing here" is noise."""
    with db.LOCK:
        conn.execute("INSERT INTO label(name, created_at) VALUES ('orphan', 0.0)")
        conn.commit()

    ctx = web_library.sidebar_context(conn, web_library.State())

    assert ctx["labels"] == []


def test_a_label_filter_and_a_title_filter_narrow_together(client, conn, library):
    _attach_label(conn, library["alpha"], "hacking")
    _attach_label(conn, library["gamma"], "hacking")

    body = client.get("/?label=hacking&q=Alph").text

    assert "Alpha" in body
    # Gamma has the label but not the title; Beta has neither.
    assert "Gamma" not in body and "Beta" not in body


def test_an_unknown_label_shows_an_empty_table_rather_than_an_error(client, library):
    """A folder id that does not exist is a 404 because it came from the app's
    own markup. A label comes from a URL a person may type, and "no results"
    is the honest answer to a word nothing carries."""
    resp = client.get("/?label=nothing-uses-this")

    assert resp.status_code == 200
    assert "Alpha" not in resp.text


def test_the_heading_names_the_label_being_filtered_on(client, conn, library):
    _attach_label(conn, library["alpha"], "hacking")

    ctx = web_library.library_context(conn, web_library.State(label="hacking"))

    assert ctx["heading"] == "hacking"


def test_the_label_survives_a_sort_link(conn, library):
    """Every link the page builds carries the state it was built from; a sort
    that silently dropped the filter would be a different page."""
    state = web_library.State(label="hacking")

    assert "label=hacking" in state.url(sort="title")


# --- adding and removing a label by hand (TASK-023 AC5) -----------------------------


def _labels_of(conn, media_id):
    return {
        row["name"]: row["source"]
        for row in conn.execute(
            "SELECT l.name AS name, ml.source AS source FROM media_label ml"
            " JOIN label l ON l.id = ml.label_id WHERE ml.media_id = ?",
            (media_id,),
        )
    }


def test_adding_a_label_by_hand_records_that_a_person_chose_it(client, conn, library):
    resp = client.post(f"/media/{library['alpha']}/labels", data={"name": "lockpicking"}, headers=HX)

    assert resp.status_code == 200
    assert _labels_of(conn, library["alpha"]) == {"lockpicking": "human"}


def test_a_hand_added_label_joins_the_vocabulary_rather_than_forking_it(client, conn, library):
    _attach_label(conn, library["gamma"], "hacking")

    client.post(f"/media/{library['alpha']}/labels", data={"name": "Hacking"}, headers=HX)

    assert conn.execute("SELECT COUNT(*) FROM label").fetchone()[0] == 1
    assert set(_labels_of(conn, library["alpha"])) == {"hacking"}


def test_a_person_is_not_rationed_the_way_the_model_is(client, conn, library):
    """MAX_NEW_LABELS governs what an automatic pass may invent, because the
    pass cannot be asked whether it is sure. A person typing a label has
    already decided; rationing that would be the app second-guessing its
    user."""
    for i in range(10):
        resp = client.post(
            f"/media/{library['alpha']}/labels", data={"name": f"subject {i}"}, headers=HX
        )
        assert resp.status_code == 200

    assert len(_labels_of(conn, library["alpha"])) == 10


def test_adding_a_label_the_recording_already_carries_changes_nothing(client, conn, library):
    _attach_label(conn, library["alpha"], "hacking", source="llm")

    resp = client.post(f"/media/{library['alpha']}/labels", data={"name": "hacking"}, headers=HX)

    # The status matters: without it this passes on a route that does not
    # exist, because "nothing happened" is what it asserts.
    assert resp.status_code == 200
    # Still one link, and still the model's - re-adding is not a claim about
    # who decided it first.
    assert _labels_of(conn, library["alpha"]) == {"hacking": "llm"}


def test_removing_a_label_detaches_it_but_keeps_the_word(client, conn, library):
    """The vocabulary outlives one recording's opinion of it: another file may
    still carry the label, and a word that is briefly unused is not wrong."""
    _attach_label(conn, library["alpha"], "hacking")
    _attach_label(conn, library["gamma"], "hacking")

    client.post(f"/media/{library['alpha']}/labels/remove", data={"name": "hacking"}, headers=HX)

    assert _labels_of(conn, library["alpha"]) == {}
    assert _labels_of(conn, library["gamma"]) == {"hacking": "llm"}
    assert conn.execute("SELECT COUNT(*) FROM label").fetchone()[0] == 1


def test_removing_a_label_that_is_not_there_is_not_an_error(client, conn, library):
    resp = client.post(
        f"/media/{library['alpha']}/labels/remove", data={"name": "never-attached"}, headers=HX
    )

    assert resp.status_code == 200


def test_a_blank_label_is_refused(client, conn, library):
    resp = client.post(f"/media/{library['alpha']}/labels", data={"name": "   "}, headers=HX)

    assert resp.status_code == 400
    assert _labels_of(conn, library["alpha"]) == {}


def test_a_label_cannot_be_added_to_a_recording_that_does_not_exist(client):
    assert client.post("/media/9999/labels", data={"name": "x"}, headers=HX).status_code == 404


# --- the backfill: labelling what is already in the library ------------------------


def _set_provider(conn, name):
    with db.LOCK:
        conn.execute(
            "INSERT INTO setting(key, value) VALUES ('llm_provider', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (name,),
        )
        conn.commit()


def _llm_jobs(conn):
    return [
        json.loads(row["params_json"])
        for row in conn.execute("SELECT params_json FROM job WHERE type='llm' ORDER BY id")
    ]


def test_bulk_label_queues_one_pass_per_chosen_recording(client, conn, library):
    """How a library that predates the labels pass catches up."""
    seed_run(conn, library["alpha"])
    seed_run(conn, library["gamma"])
    _set_provider(conn, "ollama")

    resp = client.post(
        "/media/bulk",
        data={"action": "label", "ids": [library["alpha"], library["gamma"]]},
        headers=HX,
    )

    assert resp.status_code == 200
    queued = _llm_jobs(conn)
    assert [p["media_id"] for p in queued] == [library["alpha"], library["gamma"]]
    assert {p["kind"] for p in queued} == {"labels"}


def test_bulk_label_skips_a_recording_with_no_transcript(client, conn, library):
    """The pass reads words. A job that can only fail is not worth a row on
    the board."""
    seed_run(conn, library["alpha"])
    _set_provider(conn, "ollama")

    client.post(
        "/media/bulk",
        data={"action": "label", "ids": [library["alpha"], library["beta"]]},
        headers=HX,
    )

    assert [p["media_id"] for p in _llm_jobs(conn)] == [library["alpha"]]


def test_a_private_recording_is_skipped_by_a_bulk_pass_to_an_external_model(
    client, conn, library
):
    """A private recording is never offered to an external service in bulk.
    Sending one out is a decision taken for that recording, on its own page.

    Skipped rather than refused, deliberately: a refusal only teaches the habit
    of adjusting the selection until the button works, and a bulk button must
    never be the thing that puts private words on somebody else's server. The
    rest of the batch still runs."""
    seed_run(conn, library["alpha"])
    seed_run(conn, library["gamma"])
    _set_provider(conn, "openai")
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (library["gamma"],))
        conn.commit()

    resp = client.post(
        "/media/bulk",
        data={"action": "label", "ids": [library["alpha"], library["gamma"]]},
        headers=HX,
    )

    assert resp.status_code == 200
    assert [p["media_id"] for p in _llm_jobs(conn)] == [library["alpha"]]


def test_the_skipped_private_recordings_are_reported_rather_than_dropped_quietly(
    client, conn, library
):
    """A person who ticked forty rows should not have to count the jobs to
    discover that three are not coming."""
    seed_run(conn, library["alpha"])
    seed_run(conn, library["gamma"])
    _set_provider(conn, "openai")
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (library["gamma"],))
        conn.commit()

    resp = client.post(
        "/media/bulk",
        data={"action": "label", "ids": [library["alpha"], library["gamma"]]},
        headers=HX,
    )

    notice = json.loads(resp.headers["HX-Trigger"])["scribe-notice"]
    assert "1 private recording was skipped" in notice
    assert "openai" in notice


def test_nothing_is_reported_when_nothing_was_skipped(client, conn, library):
    seed_run(conn, library["alpha"])
    _set_provider(conn, "ollama")

    resp = client.post(
        "/media/bulk", data={"action": "label", "ids": [library["alpha"]]}, headers=HX
    )

    assert "HX-Trigger" not in resp.headers


def test_bulk_label_of_a_private_recording_is_fine_on_a_local_provider(client, conn, library):
    seed_run(conn, library["gamma"])
    _set_provider(conn, "ollama")
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (library["gamma"],))
        conn.commit()

    resp = client.post(
        "/media/bulk", data={"action": "label", "ids": [library["gamma"]]}, headers=HX
    )

    assert resp.status_code == 200
    assert [p["media_id"] for p in _llm_jobs(conn)] == [library["gamma"]]


def test_bulk_label_queues_nothing_and_says_why_when_nobody_has_chosen(client, conn, library):
    """ADR-016. The bulk pass asks `default_provider` and then reads the
    class: with no row that used to be OpenRouter, and it must now be a
    sentence and an empty queue rather than a provider nobody picked."""
    seed_run(conn, library["alpha"])
    seed_run(conn, library["gamma"])

    resp = client.post(
        "/media/bulk",
        data={"action": "label", "ids": [library["alpha"], library["gamma"]]},
        headers=HX,
    )

    assert resp.status_code == 200
    assert _llm_jobs(conn) == []
    notice = json.loads(resp.headers["HX-Trigger"])["scribe-notice"]
    assert "choose a provider" in notice.lower()
    assert "/settings#llm-providers" in notice
    # The library page posts `action` and `ids` and nothing else: there is no
    # provider select on this screen, so the notice must not send a person
    # looking for one. The panel and the chat form get that clause; this does not.
    assert "for this request" not in notice.lower()
