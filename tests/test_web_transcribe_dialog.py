"""Phase 3 Task 4: the transcribe dialog.

The dialog is how recordings get into the library from a browser: dropped or
picked files go through `media.ingest_stream`, a path on this machine through
`media.ingest_path`, and each one leaves a queued transcribe job behind whose
params come from one validated `TranscribeOptions`. The same model validates
the option fields on `POST /api/media`, so the JSON door and the dialog cannot
disagree about what "tier=max" means.

No GPU, no models, no pipeline: the uploads are a few hundred bytes that no
stage ever looks at, and the jobs stay queued because the supervisor is off.
"""

import ast
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe import app as scribe_app
from scribe import db, fsbrowse, paths
from scribe.app import create_app
from scribe import options
from scribe.options import TranscribeOptions
from scribe.web import transcribe_dialog
from scribe.stages import transcribe
from test_fsbrowse import link_dir


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
    """The browse panel and the path route may only see tmp_path."""
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))
    return tmp_path


@pytest.fixture
def client(db_path, data_dir, roots):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def src_dir(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    return d


HX = {"HX-Request": "true"}


def _wav(seed: bytes) -> bytes:
    """Bytes that look enough like a wav to carry the extension; nobody decodes them."""
    return b"RIFF" + seed * 64


def _jobs(conn):
    return conn.execute("SELECT * FROM job ORDER BY id").fetchall()


def _media(conn):
    return conn.execute("SELECT * FROM media ORDER BY id").fetchall()


def _setting(conn, key):
    row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


def _set(conn, key, value):
    with db.LOCK:
        conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)", (key, value))
        conn.commit()


# --- TranscribeOptions ---------------------------------------------------------


@pytest.mark.parametrize(
    "given, model, task",
    [
        ({}, transcribe.DEFAULT_MODEL, "transcribe"),
        ({"tier": "turbo"}, transcribe.DEFAULT_MODEL, "transcribe"),
        ({"tier": "max"}, transcribe.TRANSLATE_MODEL, "transcribe"),
        # Translate on turbo stays turbo here: the substitution to large-v3 is
        # resolve_model's, at run time, so the run row records that it happened.
        ({"translate": True}, transcribe.DEFAULT_MODEL, "translate"),
        ({"tier": "max", "translate": True}, transcribe.TRANSLATE_MODEL, "translate"),
    ],
)
def test_options_map_tier_and_translate_to_model_and_task(given, model, task):
    params = TranscribeOptions(**given).to_params()

    assert params["model"] == model
    assert params["task"] == task


def language_select(body: str) -> str:
    """Just the language <select>: the form has other selects with their own
    preselection (the folder picker), so counting `selected` over the whole
    body proves nothing about the language."""
    start = body.index('<select name="language">')
    return body[start : body.index("</select>", start)]


def test_default_options_are_auto_detect_turbo_with_speakers():
    options = TranscribeOptions()

    assert options.language is None
    assert options.tier == "turbo"
    assert options.diarize is True
    assert options.translate is False
    assert options.to_params() == {
        "model": transcribe.DEFAULT_MODEL,
        "task": "transcribe",
        "language": None,
        "diarize": True,
    }


def test_speaker_hints_appear_in_the_params_only_when_given():
    params = TranscribeOptions(num_speakers=2).to_params()
    assert params["num_speakers"] == 2
    assert "min_speakers" not in params and "max_speakers" not in params

    params = TranscribeOptions(min_speakers=2, max_speakers=4).to_params()
    assert (params["min_speakers"], params["max_speakers"]) == (2, 4)
    assert "num_speakers" not in params


def test_options_take_form_strings():
    options = TranscribeOptions(
        language="", tier="max", diarize="0", num_speakers="", min_speakers="2",
        max_speakers="3", translate="on",
    )

    assert options.language is None  # the empty choice is auto-detect
    assert options.tier == "max"
    assert options.diarize is False
    assert options.num_speakers is None
    assert (options.min_speakers, options.max_speakers) == (2, 3)
    assert options.translate is True


@pytest.mark.parametrize(
    "bad",
    [
        {"tier": "cheetah"},
        {"language": "xx"},
        {"language": "Dutch"},
        {"num_speakers": 0},
        {"min_speakers": -1},
        {"min_speakers": 3, "max_speakers": 2},
        {"diarize": "maybe"},
    ],
)
def test_options_refuse_what_the_pipeline_would_choke_on(bad):
    with pytest.raises(ValueError):
        TranscribeOptions(**bad)


def test_language_choices_match_the_codes_faster_whisper_accepts():
    """The list is copied rather than imported (the web process never loads
    faster_whisper, ADR-001), so this pins it to the installed library."""
    spec = importlib.util.find_spec("faster_whisper")
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("faster_whisper is not installed")
    source = Path(spec.submodule_search_locations[0], "tokenizer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    codes = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_LANGUAGE_CODES" for t in node.targets
        ):
            codes = set(ast.literal_eval(node.value))
    assert codes, "faster_whisper.tokenizer no longer defines _LANGUAGE_CODES"

    ours = [code for code, _ in transcribe.LANGUAGE_CHOICES]
    assert set(ours) == codes
    assert len(ours) == len(set(ours))
    assert all(name for _, name in transcribe.LANGUAGE_CHOICES)
    assert transcribe.LANGUAGE_CODES == frozenset(codes)


# --- the dialog ------------------------------------------------------------------


def test_the_library_page_hosts_the_dialog(client):
    body = client.get("/").text

    assert 'hx-get="/transcribe"' in body
    assert '<dialog id="transcribe-dialog"' in body


def test_dialog_fragment_offers_files_a_path_and_every_option(client):
    resp = client.get("/transcribe", headers=HX)

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    # Files: many at once, or a whole folder.
    assert 'hx-post="/transcribe/upload"' in body
    assert 'hx-encoding="multipart/form-data"' in body
    assert 'name="files"' in body and "multiple" in body
    assert "data-toggle-directory" in body
    # Or a file already on this machine, with a browse panel.
    assert 'hx-post="/transcribe/path"' in body
    assert 'name="path"' in body
    assert 'hx-get="/fs"' in body
    # Language: auto-detect selected out of the box, every language offered,
    # none of them preselected. Dutch used to be the default; two English
    # recordings then went through with language=nl on the run (Whisper
    # transcribed them fine - the code is a bias, not a constraint - but the
    # run row lied about the language). Detection is right for Dutch too.
    assert '<option value="" selected>Auto-detect</option>' in body
    assert '<option value="nl">Dutch</option>' in body
    assert language_select(body).count(" selected>") == 1  # only auto-detect
    assert '<option value="en">English</option>' in body
    assert body.count("<option") > 100
    # Tier, speakers, translate.
    assert 'name="tier" value="turbo" checked' in body
    assert 'name="tier" value="max"' in body and 'value="max" checked' not in body
    assert 'name="diarize" value="1" checked' in body
    for hint in ("num_speakers", "min_speakers", "max_speakers"):
        assert f'name="{hint}"' in body
    assert 'name="translate" value="1"' in body
    assert 'name="translate" value="1" checked' not in body


def test_dialog_preselects_the_stored_defaults(client, conn):
    _set(conn, "default_language", "en")
    _set(conn, "default_tier", "max")
    _set(conn, "default_diarize", "0")

    body = client.get("/transcribe", headers=HX).text

    assert '<option value="en" selected>English</option>' in body
    assert '<option value="nl" selected>' not in body
    assert 'name="tier" value="max" checked' in body
    assert 'name="tier" value="turbo" checked' not in body
    assert 'name="diarize" value="1" checked' not in body


def test_dialog_preselects_the_folder_the_library_is_showing(client, conn):
    with db.LOCK:
        folder = conn.execute("INSERT INTO folder(name) VALUES ('Talks') RETURNING id").fetchone()["id"]
        conn.commit()

    body = client.get(
        "/transcribe", headers={**HX, "HX-Current-URL": f"http://testserver/?folder={folder}"}
    ).text

    assert 'name="folder_id"' in body
    assert f'<option value="{folder}" selected>Talks</option>' in body


# --- upload ------------------------------------------------------------------------


def test_upload_of_two_files_creates_two_media_rows_and_two_queued_jobs(client, conn, data_dir):
    resp = client.post(
        "/transcribe/upload",
        files=[
            ("files", ("one.wav", _wav(b"1"), "audio/wav")),
            ("files", ("two.wav", _wav(b"2"), "audio/wav")),
        ],
        data={
            "language": "nl", "tier": "max", "diarize": "1", "num_speakers": "2",
            "translate": "0",
        },
        headers=HX,
    )

    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "jobs-changed"
    body = resp.text
    assert "<html" not in body and "<table" in body
    assert ">one<" in body and ">two<" in body  # the rows fragment, titles from the stems

    rows = _media(conn)
    assert [r["orig_name"] for r in rows] == ["one.wav", "two.wav"]
    assert all((data_dir / r["store_path"]).is_file() for r in rows)

    jobs = _jobs(conn)
    assert [j["media_id"] for j in jobs] == [rows[0]["id"], rows[1]["id"]]
    assert all(j["status"] == "queued" and j["type"] == "transcribe" for j in jobs)
    expected = {
        "model": transcribe.TRANSLATE_MODEL,
        "task": "transcribe",
        "language": "nl",
        "diarize": True,
        "num_speakers": 2,
    }
    assert [json.loads(j["params_json"]) for j in jobs] == [expected, expected]


def test_uploading_the_same_bytes_twice_dedupes_the_media_and_still_queues_a_job(client, conn):
    same = _wav(b"same")
    client.post("/transcribe/upload", files={"files": ("a.wav", same, "audio/wav")}, headers=HX)
    resp = client.post(
        "/transcribe/upload", files={"files": ("b.wav", same, "audio/wav")}, headers=HX
    )

    assert resp.status_code == 200
    rows = _media(conn)
    assert len(rows) == 1
    assert rows[0]["orig_name"] == "a.wav"  # the existing row is kept as it was
    assert [j["media_id"] for j in _jobs(conn)] == [rows[0]["id"], rows[0]["id"]]


def test_upload_lands_in_the_chosen_folder(client, conn):
    with db.LOCK:
        folder = conn.execute("INSERT INTO folder(name) VALUES ('Talks') RETURNING id").fetchone()["id"]
        conn.commit()

    client.post(
        "/transcribe/upload",
        files={"files": ("a.wav", _wav(b"f"), "audio/wav")},
        data={"folder_id": str(folder)},
        headers=HX,
    )

    assert _media(conn)[0]["folder_id"] == folder


def test_upload_persists_the_options_as_the_next_defaults(client, conn):
    client.post(
        "/transcribe/upload",
        files={"files": ("a.wav", _wav(b"d"), "audio/wav")},
        data={"language": "en", "tier": "max", "diarize": "0"},
        headers=HX,
    )

    assert _setting(conn, "default_language") == "en"
    assert _setting(conn, "default_tier") == "max"
    assert _setting(conn, "default_diarize") == "0"

    body = client.get("/transcribe", headers=HX).text
    assert '<option value="en" selected>English</option>' in body
    assert 'name="tier" value="max" checked' in body


def test_upload_without_a_file_or_with_bad_options_is_a_400(client, conn):
    assert client.post("/transcribe/upload", data={"tier": "turbo"}, headers=HX).status_code == 400
    assert (
        client.post(
            "/transcribe/upload",
            files={"files": ("a.wav", _wav(b"x"), "audio/wav")},
            data={"tier": "cheetah"},
            headers=HX,
        ).status_code
        == 400
    )
    assert _media(conn) == [] and _jobs(conn) == []


def test_a_plain_upload_redirects_to_the_library(client, conn):
    resp = client.post(
        "/transcribe/upload",
        files={"files": ("a.wav", _wav(b"p"), "audio/wav")},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert len(_jobs(conn)) == 1


def test_an_htmx_upload_says_how_many_files_it_queued(client):
    resp = client.post(
        "/transcribe/upload",
        files=[
            ("files", ("one.wav", _wav(b"1"), "audio/wav")),
            ("files", ("two.wav", _wav(b"2"), "audio/wav")),
        ],
        headers=HX,
    )

    body = resp.text
    assert 'id="flash"' in body and 'hx-swap-oob="true"' in body
    assert "2 files" in body


def test_the_path_button_leaves_the_chosen_files_out_of_its_post(client):
    """The Add file button posts the enclosing form to /transcribe/path, and
    htmx includes the whole form - files already chosen in the drop zone too,
    an upload the path route silently discards. The button leaves them out."""
    body = client.get("/transcribe", headers=HX).text

    button = re.search(r'<button[^>]*hx-post="/transcribe/path"[^>]*>', body)
    assert button, "no Add file button"
    assert 'hx-params="not files"' in button.group(0)


# --- a path on this machine ------------------------------------------------------


def test_path_submit_hardlinks_the_file_and_queues_a_job(client, conn, data_dir, src_dir):
    src = src_dir / "talk.wav"
    src.write_bytes(_wav(b"t"))

    resp = client.post("/transcribe/path", data={"path": str(src), "tier": "turbo"}, headers=HX)

    assert resp.status_code == 200
    assert resp.headers["HX-Trigger"] == "jobs-changed"
    assert ">talk<" in resp.text

    (row,) = _media(conn)
    assert row["orig_name"] == "talk.wav"
    store = data_dir / row["store_path"]
    assert src.is_file()  # the source is left where it was
    assert os.path.samefile(src, store)
    assert os.stat(store).st_nlink == 2

    (job,) = _jobs(conn)
    assert job["media_id"] == row["id"]
    assert job["status"] == "queued"
    assert json.loads(job["params_json"])["model"] == transcribe.DEFAULT_MODEL


def test_path_outside_the_allowed_roots_is_a_403(client, conn, tmp_path, monkeypatch):
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (inside,))
    outside = tmp_path / "outside"
    outside.mkdir()
    src = outside / "talk.wav"
    src.write_bytes(_wav(b"o"))

    resp = client.post("/transcribe/path", data={"path": str(src)}, headers=HX)

    assert resp.status_code == 403
    assert _media(conn) == [] and _jobs(conn) == []

    # Traversal that would land inside is refused all the same.
    sneaky = inside / ".." / "outside" / "talk.wav"
    assert client.post("/transcribe/path", data={"path": str(sneaky)}, headers=HX).status_code == 403


def test_the_json_door_honours_the_same_roots_as_the_dialog(client, conn, tmp_path, monkeypatch):
    """`POST /api/media {"path": ...}` is the other way a path gets in. A path
    the browse panel would refuse is refused here as well, or the roots would
    guard one door of two."""
    inside = tmp_path / "inside"
    inside.mkdir()
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (inside,))
    outside = tmp_path / "outside"
    outside.mkdir()
    src = outside / "talk.wav"
    src.write_bytes(_wav(b"o"))

    resp = client.post("/api/media", json={"path": str(src)})

    assert resp.status_code == 403
    assert "Settings" in resp.json()["detail"]
    assert _media(conn) == [] and _jobs(conn) == []

    sneaky = inside / ".." / "outside" / "talk.wav"
    assert client.post("/api/media", json={"path": str(sneaky)}).status_code == 403
    assert _media(conn) == []

    # A path under the roots goes through as before.
    good = inside / "talk.wav"
    good.write_bytes(_wav(b"i"))
    assert client.post("/api/media", json={"path": str(good)}).status_code == 201
    assert len(_media(conn)) == 1 and len(_jobs(conn)) == 1


def test_roots_can_be_widened_through_the_setting(client, conn, tmp_path, monkeypatch):
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path / "elsewhere",))
    src = tmp_path / "src.wav"
    src.write_bytes(_wav(b"s"))
    assert client.post("/transcribe/path", data={"path": str(src)}, headers=HX).status_code == 403

    _set(conn, "fsbrowse_roots", str(tmp_path))

    assert client.post("/transcribe/path", data={"path": str(src)}, headers=HX).status_code == 200


def test_path_that_is_missing_empty_or_a_directory_says_so(client, conn, src_dir):
    assert client.post("/transcribe/path", data={"path": str(src_dir / "nope.wav")}, headers=HX).status_code == 404
    assert client.post("/transcribe/path", data={"path": str(src_dir)}, headers=HX).status_code == 400
    assert client.post("/transcribe/path", data={"path": ""}, headers=HX).status_code == 400
    assert _media(conn) == []


# --- the browse panel --------------------------------------------------------------


def test_fs_panel_lists_directories_and_picks_media_files(client, src_dir):
    (src_dir / "talk.wav").write_bytes(_wav(b"w"))
    (src_dir / "notes.txt").write_text("no", encoding="utf-8")
    (src_dir / "Archive").mkdir()

    resp = client.get("/fs", params={"path": str(src_dir)}, headers=HX)

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    assert "Archive" in body
    assert f'hx-get="/fs?path={_q(src_dir / "Archive")}"' in body
    assert f'hx-get="/fs?path={_q(src_dir.parent)}"' in body  # up one level
    assert f'data-pick-path="{src_dir / "talk.wav"}"' in body
    assert "notes.txt" in body
    assert f'data-pick-path="{src_dir / "notes.txt"}"' not in body


def test_fs_panel_without_a_path_lists_the_roots(client, roots):
    body = client.get("/fs", headers=HX).text

    assert f'hx-get="/fs?path={_q(roots)}"' in body


def test_fs_panel_refuses_paths_outside_the_roots_or_with_traversal(client, tmp_path):
    assert client.get("/fs", params={"path": str(tmp_path.parent)}, headers=HX).status_code == 403
    assert client.get("/fs", params={"path": str(tmp_path / "..")}, headers=HX).status_code == 400
    assert client.get("/fs", params={"path": str(tmp_path / "nope")}, headers=HX).status_code == 404


def test_fs_panel_and_path_submit_refuse_a_junction_that_leads_out_of_the_roots(
    client, conn, tmp_path, tmp_path_factory
):
    """A junction under the root pointing at a directory outside it: the
    panel will not list it and the path route will not ingest through it."""
    outside = tmp_path_factory.mktemp("outside")  # a sibling of the root, not under it
    (outside / "talk.wav").write_bytes(_wav(b"j"))
    link = tmp_path / "elsewhere"
    link_dir(outside, link)
    assert (link / "talk.wav").is_file()

    assert client.get("/fs", params={"path": str(link)}, headers=HX).status_code == 403
    assert client.post("/transcribe/path", data={"path": str(link / "talk.wav")}, headers=HX).status_code == 403
    assert _media(conn) == [] and _jobs(conn) == []


def _q(path) -> str:
    from urllib.parse import quote

    return quote(str(path), safe="")


# --- POST /api/media takes the same options -----------------------------------------


def test_api_media_accepts_the_option_fields(client, conn, src_dir):
    src = src_dir / "talk.wav"
    src.write_bytes(_wav(b"a"))

    resp = client.post(
        "/api/media",
        json={"path": str(src), "tier": "max", "language": "nl", "diarize": False},
    )

    assert resp.status_code == 201
    (job,) = _jobs(conn)
    assert json.loads(job["params_json"]) == {
        "model": transcribe.TRANSLATE_MODEL,
        "task": "transcribe",
        "language": "nl",
        "diarize": False,
    }


def test_api_media_takes_the_options_as_form_fields_on_an_upload(client, conn):
    resp = client.post(
        "/api/media",
        files={"file": ("talk.wav", _wav(b"u"), "audio/wav")},
        data={"translate": "1", "min_speakers": "2", "max_speakers": "3"},
    )

    assert resp.status_code == 201
    (job,) = _jobs(conn)
    params = json.loads(job["params_json"])
    assert params["task"] == "translate"
    assert (params["min_speakers"], params["max_speakers"]) == (2, 3)


def test_the_json_spine_does_not_depend_on_the_html_routers():
    """Sharing one `TranscribeOptions` must not make the JSON API import an
    HTML router: the model lives in `scribe.options`, and scribe/app.py
    touches `scribe.web` only to mount it."""
    source = Path(scribe_app.__file__).read_text(encoding="utf-8")
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)

    assert not [name for name in modules if name.startswith("scribe.web.")]
    assert "scribe.options" in modules


def test_api_media_validates_the_options_and_refuses_them_next_to_raw_params(client, conn, src_dir):
    src = src_dir / "talk.wav"
    src.write_bytes(_wav(b"v"))

    bad = client.post("/api/media", json={"path": str(src), "tier": "cheetah"})
    assert bad.status_code == 400
    assert "tier" in bad.json()["detail"]

    both = client.post(
        "/api/media", json={"path": str(src), "tier": "max", "params": {"model": "small"}}
    )
    assert both.status_code == 400

    assert _jobs(conn) == []


def test_a_stored_empty_language_means_auto_detect(client, conn):
    # save_defaults stores auto-detect as "" - it must come back as auto, not
    # as an unknown code and not as the old Dutch default.
    _set(conn, "default_language", "")

    body = client.get("/transcribe", headers=HX).text

    assert '<option value="" selected>Auto-detect</option>' in body
    assert language_select(body).count(" selected>") == 1


def test_param_keys_covers_everything_to_params_can_produce():
    """The whitelist and the method that fills it drift otherwise.

    `library._last_params` keeps only PARAM_KEYS when it copies a previous
    request forward, so a key that falls out of the set is silently dropped on
    re-transcribe. The set exists because params from another job type reached
    the transcribe stage once (2026-09-03); it is only safe while it is
    complete."""
    fullest = TranscribeOptions(
        language="nl",
        tier="max",
        diarize=True,
        num_speakers=2,
        min_speakers=1,
        max_speakers=3,
        translate=True,
    )
    assert set(fullest.to_params()) <= options.PARAM_KEYS
    assert set(TranscribeOptions().to_params()) <= options.PARAM_KEYS

    # The dialog is not the only producer, and asserting equality against it is
    # what let extra_hotwords fall out of the sieve: a URL import puts the
    # video's own proper nouns there and the transcribe stage reads them, so a
    # re-transcribe of an imported video silently decoded without them.
    from scribe.stages import url_stage

    imported = url_stage.transcribe_params(
        {url_stage.OPTIONS_KEY: fullest.to_params()}, ["Zaphod", "Trillian"]
    )
    assert set(imported) <= options.PARAM_KEYS
    assert transcribe.EXTRA_HOTWORDS_KEY in imported  # the key this test exists for

    # Every key the sieve admits is one some producer writes; nothing decorative.
    # `device` and `compute_type` joined with TASK-034, when the sieve became
    # the door: their producer is a raw params object on `POST /api/media`
    # (tests/test_pipeline_e2e.py's CPU_PARAMS runs tiny on the CPU that way),
    # and the stages that read them are transcribe.run and diarize.run.
    assert options.PARAM_KEYS == set(fullest.to_params()) | {
        transcribe.EXTRA_HOTWORDS_KEY, "device", "compute_type"
    }


def test_param_keys_covers_every_producer_of_a_transcribe_job():
    """The test above restates the constant against itself plus one hardcoded
    extra, so it knows nothing about who actually writes these params. That is
    how extra_hotwords fell out of the sieve in the first place: it was written
    by url_stage, not by the dialog, and the equality held all the same.

    Ask the producers instead. There are two besides the dialog - a URL import
    and a watch folder - and a key any of them writes that the sieve does not
    admit is dropped, silently, on the first re-transcribe."""
    from scribe.ingest import watching
    from scribe.stages import url_stage

    dialog = TranscribeOptions(language="nl", tier="max", diarize=True).to_params()
    assert set(dialog) <= options.PARAM_KEYS, "the dialog writes a key the sieve drops"

    imported = url_stage.transcribe_params({url_stage.OPTIONS_KEY: dialog}, ["Zaphod"])
    assert set(imported) <= options.PARAM_KEYS, "a URL import writes a key the sieve drops"

    # What a watch folder queues: watching.take_in enqueues folder["options"]
    # through the same to_params, so its shape is the dialog's - asserted here
    # rather than assumed, because it is a third caller and callers drift.
    watched = watching.options_of(json.dumps(TranscribeOptions().model_dump())).to_params()
    assert set(watched) <= options.PARAM_KEYS, "a watch folder writes a key the sieve drops"


# --- the dialog says what it will do before you open the options ----------------------


def test_the_options_summary_names_every_setting_that_is_not_the_default():
    """Collapsed options are only acceptable if closed means answered.

    The summary is the whole argument for hiding them: a glance has to say
    which language, which model and whether speakers are on, or the panel is
    not collapsed but concealed.
    """
    default = TranscribeOptions()
    assert transcribe_dialog.options_summary(default) == "Auto-detect · Turbo · speakers on"

    picked = TranscribeOptions(language="nl", tier="max", diarize=False, translate=True)
    line = transcribe_dialog.options_summary(picked)
    assert "Dutch" in line, line
    assert "Maximaal" in line, line
    assert "no speakers" in line, line
    assert "translate" in line.lower(), line


def test_the_summary_counts_speakers_when_a_count_was_given():
    exact = TranscribeOptions(diarize=True, num_speakers=3)
    assert "3 speakers" in transcribe_dialog.options_summary(exact)

    between = TranscribeOptions(diarize=True, min_speakers=2, max_speakers=4)
    assert "2-4 speakers" in transcribe_dialog.options_summary(between)


def test_the_dialog_renders_the_summary_and_keeps_the_options_collapsed(client):
    body = client.get("/transcribe", headers=HX).text

    assert "Auto-detect · Turbo · speakers on" in body
    assert "<details class=\"options\">" in body, "the options are not a collapsible section"
    assert "<details class=\"options\" open" not in body, "the options start open"


def test_the_source_tab_field_reaches_the_server_and_is_ignored(client, conn, tmp_path):
    """The tabs are radios, so the group has a name and the name is submitted.

    Every route reads the fields it wants by name, so a stray one is harmless -
    which is exactly the sort of claim that stops being true quietly. This is
    the test that notices.
    """
    src = tmp_path / "talk.wav"
    src.write_bytes(b"RIFF" + b"\0" * 64)

    resp = client.post(
        "/transcribe/path",
        data={"path": str(src), "source_tab": "record", "language": "nl"},
        headers=HX,
    )

    assert resp.status_code == 200
    (job,) = conn.execute("SELECT params_json FROM job ORDER BY id DESC LIMIT 1").fetchall()
    assert "source_tab" not in job["params_json"]


# --- the record dialog ------------------------------------------------------------


def test_recording_has_a_dialog_of_its_own(client):
    """It was the fourth tab of Transcribe. A person who clicked a microphone
    found themselves looking at "Upload a file" and "From a link" with no idea
    why: recording is a different act from handing over a file."""
    body = client.get("/record", headers=HX).text

    assert "<h2>Record</h2>" in body
    assert "data-recorder" in body
    # It starts as it opens; the browser's permission prompt is the consent gate.
    assert "data-record-autostart" in body
    # None of the file doors, and no submit button: finishing is the recorder's.
    for door in ("src-upload", "src-path", "src-url", 'name="files"', 'name="url"'):
        assert door not in body, door
    assert 'type="submit"' not in body


def test_the_transcribe_dialog_has_three_doors_and_no_microphone(client):
    body = client.get("/transcribe", headers=HX).text

    assert re.search(r'id="src-upload"[^>]*\schecked', body)
    for door in ("src-upload", "src-path", "src-url"):
        assert f'id="{door}"' in body, door
    assert "src-rec" not in body
    assert "data-recorder" not in body
    assert "data-record-autostart" not in body


def test_both_dialogs_share_the_same_option_fields(client):
    """A recording is transcribed with the same language, tier and speaker
    settings as an upload, and parse_options reads the same names from both.
    One partial is what keeps that true; this is what notices if it stops."""
    transcribe_body = client.get("/transcribe", headers=HX).text
    record_body = client.get("/record", headers=HX).text

    fields = ("language", "tier", "diarize", "num_speakers", "min_speakers", "max_speakers",
              "translate", "folder_id")
    for name in fields:
        assert f'name="{name}"' in transcribe_body, name
        assert f'name="{name}"' in record_body, name
    assert "Auto-detect · Turbo · speakers on" in record_body


def test_the_record_dialog_is_also_a_page(client):
    page = client.get("/record")

    assert page.status_code == 200
    assert "<html" in page.text
    assert "<h2>Record</h2>" in page.text


def test_a_hand_edited_source_is_not_an_error_it_is_the_usual_door(client):
    """A radio group with nothing checked would hide all three panels, since
    the :has() rules key off a checked radio - so an unknown value has to fall
    back rather than fail. `record` is unknown here too: it is a dialog now,
    not a door."""
    body = client.get("/transcribe?source=telepathy", headers=HX).text

    assert re.search(r'id="src-upload"[^>]*\schecked', body)
    assert opening_source_of("telepathy") == "upload"
    assert opening_source_of("record") == "upload"
    assert opening_source_of("URL") == "url"


def opening_source_of(value: str) -> str:
    class _Req:
        query_params = {}

    request = _Req()
    request.query_params = {"source": value}
    return transcribe_dialog.opening_source(request)  # type: ignore[arg-type]


def test_the_library_toolbar_offers_the_microphone_directly(client):
    body = client.get("/").text

    assert 'hx-get="/record"' in body
    assert 'href="/record"' in body
    assert 'hx-target="#record-dialog"' in body
    assert '<dialog id="record-dialog"' in body
    assert '<dialog id="transcribe-dialog"' in body
