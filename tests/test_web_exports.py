"""Phase 4 Task 5: the export routes, the dialog with its live preview, the
presets, write-to-folder and bulk export.

Everything here is `scribe.web.exports_ui` seen through the TestClient over a
seeded library (tests/seed.py): the dialog fragment and its fields, the
preview fragment the dialog re-fetches on every change, the export post in
both destinations (a download - one file or a ZIP - and a write into a folder
under the browse roots), the bulk action over a selection, and the preset
rows the settings page manages. The bytes a route hands out are compared to
what the writers produce for the same document and options, so the routes
are proven thin (ADR-003: nothing grouped is stored; the writers are pure
and the routes only call them). No GPU, no models, no pipeline, no ffmpeg.

The browse roots are pinned to tmp_path, so a write-to-folder can be refused
by pointing it at a directory pytest made elsewhere.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from mp3_headers import mp3
from scribe import db, fsbrowse, paths, playback
from scribe.app import create_app
from scribe.exports import cues, doc as export_doc, html_bundle, srt, txt
from scribe.exports.options import PRESETS, ExportOptions, filename_for
from scribe.web import exports_ui
from seed import default_words, seed_media, seed_run


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
    """Write-to-folder may only see tmp_path (and the store, always)."""
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))
    return tmp_path


@pytest.fixture
def client(db_path, data_dir, roots):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def transcribed(conn):
    """One media with the seed transcript: two speakers, the first named."""
    media_id = seed_media(conn, title="Guide", duration=20.0)
    run_id = seed_run(conn, media_id, labels={"SPEAKER_00": "Arthur"})
    return {"media": media_id, "run": run_id}


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


HX = {"HX-Request": "true"}
FORMATS = ["srt", "vtt", "txt", "csv", "json", "md", "docx", "html"]


def _media(conn, media_id):
    return conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()


def _store(conn, data_dir, media_id, payload: bytes):
    stored = data_dir / _media(conn, media_id)["store_path"]
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(payload)
    return stored


def _presets(conn):
    return conn.execute("SELECT id, name, options_json FROM export_preset ORDER BY id").fetchall()


def _zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def _select_options(fragment: str, select_id: str) -> list[tuple[str, str]]:
    """``(value, text)`` of every option in the select with ``select_id``."""
    found = re.search(rf'<select[^>]*id="{select_id}"[^>]*>(.*?)</select>', fragment, re.S)
    assert found, f"no select {select_id!r}"
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', found.group(1))


# --- the dialog ---------------------------------------------------------------------


def test_dialog_lists_all_eight_formats_and_the_five_built_in_presets(client, conn, transcribed):
    media_id = transcribed["media"]

    resp = client.get(f"/media/{media_id}/export", headers=HX)

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    boxes = re.findall(r'<input type="checkbox" name="formats" value="([a-z]+)"', body)
    assert boxes == FORMATS
    assert 'value="srt" checked' in body  # the default options select SRT
    values = [value for value, _ in _select_options(body, "export-preset")]
    assert values[0] == ""  # "custom", chosen once a field is edited by hand
    assert values[1:] == list(PRESETS)
    # A preset carries its options for app.js to fill the fields with.
    netflix = re.search(r'<option value="netflix"[^>]*data-options="([^"]*)"', body)
    assert netflix and json.loads(html.unescape(netflix.group(1)))["cpl"] == 42
    # The option groups the plan names.
    for legend in ("Formats", "Timestamps", "Speakers", "Subtitles", "File", "Destination"):
        assert f"<legend>{legend}<" in body, legend
    # Every group but Formats says which formats read it, so the stylesheet
    # can fold the ones the ticked formats do not use; the one tick that
    # shows them all again is not an option field.
    found = re.findall(r'<fieldset class="options" data-for="([^"]+)">\s*<legend>(\w+)<', body)
    assert {legend: formats for formats, legend in found} == {
        "Timestamps": "txt md docx html",
        "Speakers": "srt vtt txt md docx html json csv",
        "Subtitles": "srt vtt",
        "Text": "txt md csv json",
        "File": "srt vtt txt md docx html json csv",
        "Destination": "srt vtt txt md docx html json csv",
    }
    assert 'data-show-all-options' in body and 'name="show_all' not in body
    # The preview pane loads itself and follows every change of the form.
    pane = re.search(r"<div[^>]*id=\"export-preview\"[^>]*>", body)
    assert pane, "no preview pane"
    assert f'hx-post="/media/{media_id}/export/preview"' in pane.group(0)
    assert "load" in pane.group(0) and "change" in pane.group(0)
    # Both destinations, the folder defaulting to where the recording lives.
    assert f'action="/media/{media_id}/export"' in body
    assert 'name="destination" value="download"' in body
    assert 'name="destination" value="folder"' in body
    stored = paths.DATA_DIR / _media(conn, media_id)["store_path"]
    assert f'name="path" value="{html.escape(str(stored.parent))}"' in body


def test_dialog_is_a_page_of_its_own_without_htmx(client, transcribed):
    resp = client.get(f"/media/{transcribed['media']}/export")

    assert resp.status_code == 200
    assert "<html" in resp.text
    assert 'name="formats"' in resp.text


def test_dialog_refuses_a_media_without_a_transcript_and_an_unknown_one(client, conn):
    fresh = seed_media(conn, title="Fresh")

    assert client.get(f"/media/{fresh}/export", headers=HX).status_code == 409
    assert client.get("/media/9999/export", headers=HX).status_code == 404


def test_bulk_dialog_lists_the_selection_and_posts_to_the_bulk_route(client, conn, transcribed):
    other = seed_media(conn, title="Other")
    seed_run(conn, other)
    fresh = seed_media(conn, title="Fresh")  # no transcript: shown as skipped

    resp = client.get("/export", params={"ids": [transcribed["media"], other, fresh]}, headers=HX)

    assert resp.status_code == 200
    body = resp.text
    assert 'action="/media/bulk"' in body
    assert 'name="action" value="export"' in body
    for media_id in (transcribed["media"], other, fresh):
        assert f'name="ids" value="{media_id}"' in body
    assert "Guide" in body and "Other" in body
    assert "Fresh" in body and "no transcript" in body.lower()
    assert f'hx-post="/media/{transcribed["media"]}/export/preview"' in body  # the first one previews

    assert client.get("/export", headers=HX).status_code == 400


# --- the preview --------------------------------------------------------------------


def _preview(client, media_id, **fields):
    return client.post(f"/media/{media_id}/export/preview", data=fields, headers=HX)


def test_preview_returns_ten_cues_and_the_violation_summary_at_cpl_20(client, conn, transcribed):
    media_id = transcribed["media"]
    options = ExportOptions(formats=["srt"], cpl=20, max_lines=1)
    built = cues.build(export_doc.load(conn, media_id), options)
    assert len(built.cues) == 15 and built.report.violations  # the seed at these constraints

    resp = _preview(client, media_id, formats="srt", cpl="20", max_lines="1")

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    rows = re.findall(r'<li class="cue[^"]*"', body)
    assert len(rows) == 10
    assert "10 of 15 cues" in body
    # The first cue as the SRT would show it.
    assert "00:00:00,000 --> 00:00:00,920" in body
    assert "Arthur:" in body
    assert "Don&#39;t panic," in body
    # The summary counts every violation of the whole file, not just the ten
    # shown (the 20 s media cannot hold its last cue to min_duration either),
    # and for SRT it is the file's report: a first line that its speaker name
    # takes past 20 counts as well. Only two cues carry a name here - the one
    # that opens Arthur's run and the one that opens the next speaker's - and
    # only one of those two goes over, so the file adds a single cpl violation
    # to the engine's ten. Before the writer stopped repeating the name on
    # every cue of a run, this said "cpl ×11".
    assert exports_ui.summary(built.report) == "10 violations: min_duration ×8, cps ×2"
    assert "11 violations: min_duration ×8, cps ×2, cpl ×1" in body
    # A cue that breaks a rule is marked with the rule, in the list.
    first = re.search(r'<li class="cue bad"[^>]*>.*?</li>', body, re.S)
    assert first and "min_duration" in first.group(0)


def test_preview_of_srt_counts_the_speaker_prefix_against_cpl(client, transcribed):
    """At the YouTube preset the cue text fits 32 but `Arthur: ` takes the
    written first line to 40; the SRT preview says so, the VTT preview (a
    voice tag, not text on the line) does not.

    Two, not four: the name is written where a speaker run opens, so only
    those first lines pay for it."""
    media_id = transcribed["media"]
    youtube = dict(cpl="32", max_lines="2", max_duration="5")

    body = _preview(client, media_id, formats="srt", **youtube).text
    assert "cpl ×2" in body
    first = re.search(r'<li class="cue bad"[^>]*value="1"[^>]*>.*?</li>', body, re.S)
    assert first and "cpl" in first.group(0)

    vtt = _preview(client, media_id, formats="vtt", **youtube).text
    assert "cpl" not in vtt

    plain = _preview(client, media_id, formats="srt", speakers="0", **youtube).text
    assert "cpl" not in plain


def test_preview_of_a_clean_subtitle_reports_no_violations(client, transcribed):
    body = _preview(client, transcribed["media"], formats="srt", speakers="0").text

    assert "No violations" in body
    assert "4 cues" in body
    assert 'class="cue bad"' not in body

    # With names on, the one first line the name takes past 42 is reported
    # ("Speaker 2: Marvin says the improbability drive" is 46), and only that.
    named = _preview(client, transcribed["media"], formats="srt").text
    assert "1 violation: cpl ×1" in named
    assert named.count('class="cue bad"') == 1


def test_preview_uses_vtt_timing_for_vtt_and_the_first_selected_format_decides(client, transcribed):
    media_id = transcribed["media"]

    vtt = _preview(client, media_id, formats=["vtt", "txt"]).text
    assert "00:00:00.000 --> 00:00:04.900" in vtt

    # Prose first: the first ten lines of the text writer's output instead.
    text = _preview(client, media_id, formats=["txt", "srt"]).text
    assert "-->" not in text
    assert "[0:00] Arthur: Don&#39;t panic" in text


def test_preview_of_a_text_format_shows_its_first_ten_lines(client, conn, transcribed):
    media_id = transcribed["media"]
    doc = export_doc.load(conn, media_id)
    expected = txt.write(doc, ExportOptions(formats=["txt"], timestamps="word")).decode("utf-8")
    assert len(expected.splitlines()) < 10  # nothing to cut: every line is shown

    body = _preview(client, media_id, formats="txt", timestamps="word").text
    for line in expected.splitlines():
        assert html.escape(line, quote=False).replace("'", "&#39;") in body
    assert "cues" not in body.lower()

    many = _preview(client, media_id, formats="md", front_matter="1", timestamps="sentence").text
    assert "10 of " in many and "lines" in many
    assert "title: &#34;Guide&#34;" in many


def test_preview_of_a_rich_format_falls_back_to_the_text_layout(client, transcribed):
    body = _preview(client, transcribed["media"], formats="docx").text

    assert "shown as text" in body.lower()
    assert "Arthur:" in body
    # ... and says which of the fields the stand-in honours the format does not.
    assert "TXT layout does not apply" in body
    json_body = _preview(client, transcribed["media"], formats="json").text
    assert "timestamps do not apply" in json_body
    html_body = _preview(client, transcribed["media"], formats="html").text
    assert "timestamp format do not apply" in html_body
    assert "apply" not in _preview(client, transcribed["media"], formats="txt").text


def test_the_dialog_says_csv_carries_a_bom_whatever_utf8_is_chosen(client, transcribed):
    """The Encoding select offers utf-8 and utf-8-sig alike, but the CSV
    writer always puts a BOM in front for Excel; the dialog has to say so
    next to the select rather than let utf-8 look like a BOM-less choice."""
    body = client.get(f"/media/{transcribed['media']}/export", headers=HX).text

    field = re.search(r'<label class="field">Encoding.*?</label>', body, re.S)
    assert field, "no Encoding field"
    assert "CSV always gets a BOM" in field.group(0)


def test_preview_with_bad_options_is_a_400_and_a_missing_run_a_409(client, conn, transcribed):
    resp = _preview(client, transcribed["media"], formats="srt", cpl="5")
    assert resp.status_code == 400
    assert "cpl" in resp.json()["detail"]

    assert _preview(client, transcribed["media"], formats="pdf").status_code == 400

    fresh = seed_media(conn, title="Fresh")
    assert _preview(client, fresh, formats="srt").status_code == 409


# --- download ---------------------------------------------------------------------


def _export(client, media_id, **fields):
    return client.post(f"/media/{media_id}/export", data=fields)


def test_export_of_srt_downloads_the_writers_bytes_under_the_right_filename(client, conn, transcribed):
    media_id = transcribed["media"]
    doc = export_doc.load(conn, media_id)
    options = ExportOptions(formats=["srt"], cpl=30, speakers="0")

    resp = _export(client, media_id, destination="download", formats="srt", cpl="30", speakers="0")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-subrip")
    assert resp.headers["content-disposition"] == 'attachment; filename="Guide.srt"'
    assert resp.content == srt.write(doc, options)
    assert filename_for(doc, options, "srt") == "Guide.srt"
    assert "Arthur" not in resp.text


def test_export_defaults_to_a_download_of_srt(client, transcribed):
    resp = _export(client, transcribed["media"])

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="Guide.srt"'
    assert b"-->" in resp.content


def test_export_scrubs_the_title_in_the_filename(client, conn):
    media_id = seed_media(conn, title='Take 2: "final"/v3?')
    seed_run(conn, media_id)

    resp = _export(client, media_id, formats="vtt")

    assert resp.headers["content-disposition"] == 'attachment; filename="Take 2_ _final__v3_.vtt"'


def test_export_of_three_formats_downloads_a_zip_with_three_members(client, conn, transcribed):
    media_id = transcribed["media"]
    doc = export_doc.load(conn, media_id)

    resp = _export(client, media_id, destination="download", formats=["srt", "txt", "json"])

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    assert resp.headers["content-disposition"] == 'attachment; filename="Guide.zip"'
    archive = _zip(resp.content)
    assert archive.namelist() == ["Guide.srt", "Guide.txt", "Guide.json"]
    options = ExportOptions(formats=["srt", "txt", "json"])
    assert archive.read("Guide.srt") == srt.write(doc, options)
    assert archive.read("Guide.txt") == txt.write(doc, options)


def test_html_export_embeds_a_small_recording_and_ships_a_sidecar_for_a_large_one(
    client, conn, data_dir, transcribed, monkeypatch
):
    media_id = transcribed["media"]
    audio = b"RIFF" + bytes(range(256)) * 8
    _store(conn, data_dir, media_id, audio)
    doc = export_doc.load(conn, media_id)

    small = _export(client, media_id, formats="html")
    assert small.headers["content-disposition"] == 'attachment; filename="Guide.html"'
    assert small.content == html_bundle.write(doc, ExportOptions(formats=["html"]), audio=audio, audio_mime="audio/wav")
    assert b"data:audio/wav;base64," in small.content

    monkeypatch.setattr(exports_ui, "EMBED_CAP_BYTES", len(audio) - 1)
    large = _export(client, media_id, formats="html")
    assert large.headers["content-type"].startswith("application/zip")
    archive = _zip(large.content)
    assert archive.namelist() == ["Guide.html", "Guide.wav"]
    assert archive.read("Guide.wav") == audio
    assert b'src="Guide.wav"' in archive.read("Guide.html")
    assert archive.read("Guide.html") == html_bundle.write(doc, ExportOptions(formats=["html"]), audio_mime="audio/wav")


def test_html_export_without_a_stored_file_still_writes_the_bundle(client, conn, transcribed):
    resp = _export(client, transcribed["media"], formats="html")

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="Guide.html"'
    assert b"data:" not in resp.content
    assert b'src="Guide.m4a"' in resp.content  # the name a sidecar would have


def test_export_refuses_bad_options_a_bad_destination_and_a_missing_run(client, conn, transcribed):
    media_id = transcribed["media"]

    assert _export(client, media_id, formats="pdf").status_code == 400
    assert _export(client, media_id, formats="srt", max_lines="0").status_code == 400
    assert _export(client, media_id, formats="srt", destination="email").status_code == 400
    fresh = seed_media(conn, title="Fresh")
    assert _export(client, fresh, formats="srt").status_code == 409
    assert _export(client, 9999, formats="srt").status_code == 404


def test_unticking_every_format_is_refused_not_silently_exported_as_srt(client, transcribed):
    """A form with no format box ticked posts nothing under formats. With
    the other fields there, that is "no format", not "the default", and the
    model's own message says so; a bare post (no fields at all) still means
    the defaults, as the API is used."""
    media_id = transcribed["media"]

    resp = _export(client, media_id, destination="download", speakers="0", cpl="30")
    assert resp.status_code == 400
    assert "at least one format" in resp.json()["detail"]

    preview = _preview(client, media_id, cpl="30")
    assert preview.status_code == 400
    assert "at least one format" in preview.json()["detail"]

    assert _export(client, media_id).status_code == 200


# --- off the event loop ---------------------------------------------------------------------


def test_the_files_are_built_and_written_off_the_event_loop(client, transcribed, out_dir, monkeypatch):
    """The writers, the audio's base64, the ZIP and the disk writes run in a
    worker thread (run_in_threadpool), as every other heavy route does, so
    an export does not freeze the app for every other request."""
    seen = []

    def on_loop() -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    def spy(name, real):
        def wrapper(*args, **kwargs):
            seen.append((name, on_loop()))
            return real(*args, **kwargs)
        return wrapper

    for name in ("members_for", "collect", "preview_context", "write_into"):
        monkeypatch.setattr(exports_ui, name, spy(name, getattr(exports_ui, name)))
    media_id = transcribed["media"]

    assert _preview(client, media_id, formats="srt").status_code == 200
    assert _export(client, media_id, formats="srt").status_code == 200
    folder = client.post(
        f"/media/{media_id}/export",
        data={"destination": "folder", "path": str(out_dir), "formats": "srt"},
        headers=HX,
    )
    assert folder.status_code == 200
    bulk = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [media_id], "destination": "download", "formats": "srt"},
    )
    assert bulk.status_code == 200

    assert {name for name, _ in seen} == {"members_for", "collect", "preview_context", "write_into"}
    assert [name for name, on_the_loop in seen if on_the_loop] == []


# --- a character the encoding cannot hold ----------------------------------------------------


@pytest.fixture
def greek(conn):
    """One transcript cp1252 can hold and one it cannot."""
    plain = seed_media(conn, title="Plain")
    seed_run(conn, plain, words=[{"start": 0.0, "end": 0.4, "text": " Fine."}])
    greek = seed_media(conn, title="Greek")
    seed_run(conn, greek, words=[{"start": 0.0, "end": 0.4, "text": " Ωμέγα."}])
    return {"plain": plain, "greek": greek}


def test_an_export_the_encoding_cannot_hold_is_a_400_naming_the_character_and_the_fix(client, greek, out_dir):
    resp = _export(client, greek["greek"], formats="txt", encoding="cp1252")

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Greek" in detail and "cp1252" in detail and "utf-8" in detail
    assert "Ω" in detail and "U+03A9" in detail

    folder = client.post(
        f"/media/{greek['greek']}/export",
        data={"destination": "folder", "path": str(out_dir), "formats": "txt", "encoding": "cp1252"},
        headers=HX,
    )
    assert folder.status_code == 400
    assert list(out_dir.iterdir()) == []

    bulk = client.post(
        "/media/bulk",
        data={
            "action": "export", "ids": [greek["plain"], greek["greek"]], "destination": "folder",
            "path": str(out_dir), "formats": "txt", "encoding": "cp1252",
        },
        headers=HX,
    )
    assert bulk.status_code == 400
    assert "Greek" in bulk.json()["detail"]
    assert list(out_dir.iterdir()) == []  # Plain is not written either: the export is refused whole

    assert _export(client, greek["greek"], formats="txt", encoding="utf-8").status_code == 200
    assert _export(client, greek["plain"], formats="txt", encoding="cp1252").status_code == 200


def test_the_preview_warns_when_the_encoding_cannot_hold_the_text(client, greek):
    lines = _preview(client, greek["greek"], formats="txt", encoding="cp1252")
    assert lines.status_code == 200
    assert "cp1252" in lines.text and "utf-8" in lines.text and "U+03A9" in lines.text

    cues_ = _preview(client, greek["greek"], formats="srt", encoding="cp1252")
    assert cues_.status_code == 200
    assert "cp1252" in cues_.text and "U+03A9" in cues_.text

    # Nothing to warn about: utf-8 holds anything, the text is Latin, or the
    # format's bytes never follow the encoding (JSON is always UTF-8).
    assert "cp1252" not in _preview(client, greek["greek"], formats="txt", encoding="utf-8").text
    assert "cp1252" not in _preview(client, greek["plain"], formats="txt", encoding="cp1252").text
    assert "cp1252" not in _preview(client, greek["greek"], formats="json", encoding="cp1252").text


# --- the recording, and only for the bundle ------------------------------------------------


@pytest.fixture
def mkv(conn, data_dir, transcribed):
    """The seed media stored as an .mkv - a container a browser cannot play,
    so the HTML bundle needs the AAC proxy and no other format may ask for it."""
    media_id = transcribed["media"]
    store_path = _media(conn, media_id)["store_path"][:-4] + ".mkv"
    with db.LOCK:
        conn.execute(
            "UPDATE media SET store_path=?, orig_name='Guide.mkv' WHERE id=?", (store_path, media_id)
        )
        conn.commit()
    _store(conn, data_dir, media_id, b"\x1aE\xdf\xa3" + bytes(64))
    return media_id


@pytest.fixture
def proxy_calls(monkeypatch):
    """`playback.ensure_proxy` replaced by a counter that drops a stub proxy
    in place (no ffmpeg here); the list of calls made."""
    calls = []

    def fake(original, proxy):
        calls.append((original, proxy))
        proxy.parent.mkdir(parents=True, exist_ok=True)
        proxy.write_bytes(b"proxy")

    monkeypatch.setattr(playback, "ensure_proxy", fake)
    return calls


def test_a_subtitle_export_of_an_unplayable_container_never_transcodes_it(client, mkv, proxy_calls, out_dir):
    resp = _export(client, mkv, formats=["srt", "vtt", "txt"])

    assert resp.status_code == 200
    assert proxy_calls == []

    bulk = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [mkv], "destination": "folder", "path": str(out_dir), "formats": "srt"},
        headers=HX,
    )
    assert bulk.status_code == 200
    assert proxy_calls == []


@pytest.fixture
def as_mp3(conn, data_dir, transcribed):
    """The seed media stored as an MP3 with the given first-frame tag: b"Info"
    for a constant bitrate, b"Xing" for a variable one (tests/mp3_headers.py)."""

    def store(tag: bytes) -> int:
        media_id = transcribed["media"]
        store_path = _media(conn, media_id)["store_path"][:-4] + ".mp3"
        with db.LOCK:
            conn.execute(
                "UPDATE media SET store_path=?, orig_name='Guide.mp3' WHERE id=?", (store_path, media_id)
            )
            conn.commit()
        _store(conn, data_dir, media_id, mp3(tag))
        return media_id

    return store


def test_an_html_export_of_a_vbr_mp3_carries_its_proxy_like_the_player(client, conn, data_dir, as_mp3):
    """TASK-057: the bundle's player seeks the same way the app's does, so it
    gets the same file - the proxy, not the VBR original."""
    media_id = as_mp3(b"Xing")
    proxy = data_dir / "media" / "proxy" / f"{_media(conn, media_id)['sha256']}.m4a"
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"aac in an mp4 box")

    resp = _export(client, media_id, formats=["srt", "html"])

    assert resp.status_code == 200
    page = _zip(resp.content).read("Guide.html")
    assert b"data:audio/mp4;base64,YWFjIGluIGFuIG1wNCBib3g=" in page


def test_an_html_export_of_a_vbr_mp3_without_a_proxy_makes_one(client, as_mp3, proxy_calls):
    resp = _export(client, as_mp3(b"Xing"), formats=["srt", "html"])

    assert resp.status_code == 200
    assert len(proxy_calls) == 1
    assert b"data:audio/mp4;base64," in _zip(resp.content).read("Guide.html")


def test_an_html_export_of_a_cbr_mp3_carries_the_original(client, as_mp3, proxy_calls):
    resp = _export(client, as_mp3(b"Info"), formats=["srt", "html"])

    assert resp.status_code == 200
    assert proxy_calls == []
    assert b"data:audio/mpeg;base64," in _zip(resp.content).read("Guide.html")


def test_an_html_export_of_an_unplayable_container_makes_the_proxy_once(client, mkv, proxy_calls):
    resp = _export(client, mkv, formats=["srt", "html"])

    assert resp.status_code == 200
    assert len(proxy_calls) == 1
    assert _zip(resp.content).namelist() == ["Guide.srt", "Guide.html"]
    assert b"data:audio/mp4;base64," in _zip(resp.content).read("Guide.html")


# --- write to folder ---------------------------------------------------------------------


def test_write_to_folder_creates_the_files_and_answers_with_their_paths(
    client, conn, transcribed, out_dir
):
    media_id = transcribed["media"]
    doc = export_doc.load(conn, media_id)

    resp = client.post(
        f"/media/{media_id}/export",
        data={"destination": "folder", "path": str(out_dir), "formats": ["srt", "md"], "cpl": "30"},
        headers=HX,
    )

    assert resp.status_code == 200
    body = resp.text
    assert "<html" not in body
    written = sorted(p.name for p in out_dir.iterdir())
    assert written == ["Guide.md", "Guide.srt"]
    assert (out_dir / "Guide.srt").read_bytes() == srt.write(doc, ExportOptions(formats=["srt", "md"], cpl=30))
    for name in written:
        assert html.escape(str(out_dir / name)) in body
    assert "2 files" in body
    assert "(replaced)" not in body
    assert not list(out_dir.glob("*.part"))  # written whole, renamed into place

    # Exporting again replaces the files rather than refusing or numbering
    # them, and says which.
    again = client.post(
        f"/media/{media_id}/export",
        data={"destination": "folder", "path": str(out_dir), "formats": "srt"},
        headers=HX,
    )
    assert again.status_code == 200
    assert sorted(p.name for p in out_dir.iterdir()) == ["Guide.md", "Guide.srt"]
    assert re.search(r"Guide\.srt\s*<span[^>]*>\(replaced\)</span>", again.text)


def test_a_file_that_cannot_be_put_in_place_is_a_409_naming_it_and_what_was_written(
    client, transcribed, out_dir
):
    """A target that will not be replaced (a folder of that name here; a
    file open in Excel in life) is a 409 naming the path and the files
    written before it, not a 500 - and no .part file is left behind."""
    media_id = transcribed["media"]
    (out_dir / "Guide.srt").mkdir()

    resp = client.post(
        f"/media/{media_id}/export",
        data={"destination": "folder", "path": str(out_dir), "formats": ["md", "srt"]},
        headers=HX,
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "Guide.srt" in detail and "Guide.md" in detail
    assert (out_dir / "Guide.md").is_file()
    assert not list(out_dir.glob("*.part"))


def test_write_to_folder_puts_the_sidecar_next_to_a_large_html_bundle(
    client, conn, data_dir, transcribed, out_dir, monkeypatch
):
    media_id = transcribed["media"]
    audio = b"RIFF" + bytes(range(256)) * 8
    _store(conn, data_dir, media_id, audio)
    monkeypatch.setattr(exports_ui, "EMBED_CAP_BYTES", 16)

    resp = client.post(
        f"/media/{media_id}/export",
        data={"destination": "folder", "path": str(out_dir), "formats": "html"},
        headers=HX,
    )

    assert resp.status_code == 200
    assert sorted(p.name for p in out_dir.iterdir()) == ["Guide.html", "Guide.wav"]
    assert (out_dir / "Guide.wav").read_bytes() == audio
    assert b'src="Guide.wav"' in (out_dir / "Guide.html").read_bytes()


def test_write_to_folder_refuses_a_path_outside_the_roots(client, transcribed, tmp_path_factory, roots):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    assert not fsbrowse.is_allowed(elsewhere, (roots,))

    resp = client.post(
        f"/media/{transcribed['media']}/export",
        data={"destination": "folder", "path": str(elsewhere), "formats": "srt"},
        headers=HX,
    )

    assert resp.status_code == 403
    assert list(elsewhere.iterdir()) == []

    dotted = client.post(
        f"/media/{transcribed['media']}/export",
        data={"destination": "folder", "path": str(roots / ".." / roots.name), "formats": "srt"},
        headers=HX,
    )
    assert dotted.status_code == 403


def test_write_to_folder_needs_an_existing_directory(client, transcribed, roots):
    missing = roots / "nope"

    resp = client.post(
        f"/media/{transcribed['media']}/export",
        data={"destination": "folder", "path": str(missing), "formats": "srt"},
        headers=HX,
    )
    assert resp.status_code == 400
    assert not missing.exists()

    blank = client.post(
        f"/media/{transcribed['media']}/export",
        data={"destination": "folder", "path": "  ", "formats": "srt"},
        headers=HX,
    )
    assert blank.status_code == 400


def test_the_store_itself_is_always_a_writable_root(client, conn, data_dir, transcribed, monkeypatch):
    """The dialog's default is the folder the recording lives in; it has to
    work whether or not the browse roots happen to cover the data directory."""
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (data_dir / "nowhere",))
    media_id = transcribed["media"]
    stored = _store(conn, data_dir, media_id, b"RIFF")

    resp = client.post(
        f"/media/{media_id}/export",
        data={"destination": "folder", "path": str(stored.parent), "formats": "srt"},
        headers=HX,
    )

    assert resp.status_code == 200
    assert (stored.parent / "Guide.srt").is_file()


def test_a_plain_write_to_folder_answers_with_a_page(client, transcribed, out_dir):
    resp = client.post(
        f"/media/{transcribed['media']}/export",
        data={"destination": "folder", "path": str(out_dir), "formats": "srt"},
    )

    assert resp.status_code == 200
    assert "<html" in resp.text
    assert "Guide.srt" in resp.text


# --- bulk -----------------------------------------------------------------------------


@pytest.fixture
def three(conn):
    """Two transcribed media and one without a transcript."""
    alpha = seed_media(conn, title="Alpha")
    seed_run(conn, alpha, labels={"SPEAKER_00": "Arthur"})
    beta = seed_media(conn, title="Beta")
    seed_run(conn, beta)
    fresh = seed_media(conn, title="Fresh")
    return {"alpha": alpha, "beta": beta, "fresh": fresh}


def test_bulk_export_over_two_ids_writes_six_files_for_three_formats(client, conn, three, out_dir):
    resp = client.post(
        "/media/bulk",
        data={
            "action": "export",
            "ids": [three["alpha"], three["beta"]],
            "destination": "folder",
            "path": str(out_dir),
            "formats": ["srt", "vtt", "txt"],
        },
        headers=HX,
    )

    assert resp.status_code == 200
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == ["Alpha.srt", "Alpha.txt", "Alpha.vtt", "Beta.srt", "Beta.txt", "Beta.vtt"]
    assert "6 files" in resp.text
    alpha = export_doc.load(conn, three["alpha"])
    assert (out_dir / "Alpha.srt").read_bytes() == srt.write(alpha, ExportOptions(formats=["srt", "vtt", "txt"]))
    assert b"Arthur" in (out_dir / "Alpha.srt").read_bytes()
    assert b"Arthur" not in (out_dir / "Beta.srt").read_bytes()


def test_bulk_export_skips_a_media_without_a_transcript_and_says_so(client, three, out_dir):
    resp = client.post(
        "/media/bulk",
        data={
            "action": "export",
            "ids": [three["alpha"], three["fresh"]],
            "destination": "folder",
            "path": str(out_dir),
            "formats": "srt",
        },
        headers=HX,
    )

    assert resp.status_code == 200
    assert sorted(p.name for p in out_dir.iterdir()) == ["Alpha.srt"]
    assert "Fresh" in resp.text and "no transcript" in resp.text.lower()

    nothing = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [three["fresh"]], "destination": "folder", "path": str(out_dir), "formats": "srt"},
        headers=HX,
    )
    assert nothing.status_code == 409


def test_bulk_download_is_one_zip_of_every_file(client, conn, three):
    resp = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [three["alpha"], three["beta"]], "destination": "download", "formats": ["srt", "md"]},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")
    assert resp.headers["content-disposition"] == 'attachment; filename="export.zip"'
    assert _zip(resp.content).namelist() == ["Alpha.srt", "Alpha.md", "Beta.srt", "Beta.md"]


def test_bulk_download_refuses_above_the_zip_cap_and_points_at_write_to_folder(client, three, monkeypatch):
    monkeypatch.setattr(exports_ui, "ZIP_CAP_BYTES", 100)

    resp = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [three["alpha"], three["beta"]], "destination": "download", "formats": "srt"},
    )

    assert resp.status_code == 413
    assert "folder" in resp.json()["detail"].lower()


def test_a_bulk_download_stops_building_at_the_cap(client, three, monkeypatch):
    """The cap bounds what is held while the files are built, not only the
    ZIP: the first media past it ends the build, so a library of HTML
    bundles is not materialised in memory before the 413."""
    monkeypatch.setattr(exports_ui, "ZIP_CAP_BYTES", 100)
    built = []
    real = exports_ui.members_for

    def counting(doc, options, audio):
        built.append(doc.title)
        return real(doc, options, audio)

    monkeypatch.setattr(exports_ui, "members_for", counting)

    resp = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [three["alpha"], three["beta"]], "destination": "download", "formats": "srt"},
    )

    assert resp.status_code == 413
    assert "at least" in resp.json()["detail"]
    assert built == ["Alpha"]  # Beta was never built


def test_bulk_export_gives_clashing_titles_distinct_filenames(client, conn, out_dir):
    first = seed_media(conn, title="Same")
    seed_run(conn, first)
    second = seed_media(conn, title="Same")
    seed_run(conn, second, words=[{"start": 0.0, "end": 0.4, "text": " Different."}])

    resp = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [first, second], "destination": "download", "formats": "srt"},
    )

    assert resp.status_code == 200
    names = _zip(resp.content).namelist()
    assert names == ["Same.srt", "Same (2).srt"]
    assert b"Different." in _zip(resp.content).read("Same (2).srt")


def test_bulk_export_keeps_the_number_on_a_title_at_the_filename_limit(client, conn):
    """Two media sharing a 250-character title: the second's ` (2)` has to
    survive the cut to MAX_STEM, or no suffix ever makes the names differ
    and the export answers 409 after 99 tries."""
    title = "y" * 250
    first = seed_media(conn, title=title)
    seed_run(conn, first)
    second = seed_media(conn, title=title)
    seed_run(conn, second)

    resp = client.post(
        "/media/bulk",
        data={"action": "export", "ids": [first, second], "destination": "download", "formats": "srt"},
    )

    assert resp.status_code == 200
    names = _zip(resp.content).namelist()
    assert names == ["y" * 200 + ".srt", "y" * 196 + " (2).srt"]


def test_bulk_export_needs_ids_and_valid_options(client, three, out_dir):
    assert client.post("/media/bulk", data={"action": "export", "formats": "srt"}).status_code == 400
    assert (
        client.post(
            "/media/bulk", data={"action": "export", "ids": [three["alpha"]], "formats": "pdf"}
        ).status_code
        == 400
    )
    assert client.post("/media/bulk", data={"action": "export", "ids": [9999], "formats": "srt"}).status_code == 404


def test_the_library_offers_a_bulk_export_and_the_export_dialog(client, three):
    body = client.get("/").text

    button = re.search(r"<button[^>]*hx-get=\"/export\"[^>]*>", body)
    assert button, "no bulk export button"
    assert 'hx-include="#bulk-form"' in button.group(0)
    assert 'hx-target="#export-dialog"' in button.group(0)
    assert '<dialog id="export-dialog"' in body


# --- presets ------------------------------------------------------------------------------


def test_saving_a_preset_makes_it_appear_in_the_dialog_and_on_the_settings_page(client, conn, transcribed):
    resp = client.post(
        "/settings/presets",
        data={"name": "House style", "formats": ["srt", "vtt"], "cpl": "37", "max_cps": "16", "speakers": "0"},
        headers=HX,
    )

    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert 'id="settings-presets"' in resp.text
    assert "House style" in resp.text
    (row,) = _presets(conn)
    assert row["name"] == "House style"
    stored = ExportOptions.model_validate_json(row["options_json"])
    assert stored.formats == ["srt", "vtt"] and stored.cpl == 37 and stored.max_cps == 16.0
    assert stored.speakers is False

    dialog = client.get(f"/media/{transcribed['media']}/export", headers=HX).text
    values = [value for value, _ in _select_options(dialog, "export-preset")]
    assert values == [""] + list(PRESETS) + ["House style"]
    option = re.search(r'<option value="House style"[^>]*data-options="([^"]*)"', dialog)
    assert json.loads(html.unescape(option.group(1)))["cpl"] == 37

    page = client.get("/settings").text
    assert 'id="settings-presets"' in page
    assert "House style" in page
    assert f'action="/settings/presets/{row["id"]}/delete"' in page


def test_saving_a_preset_as_options_json(client, conn):
    resp = client.post(
        "/settings/presets",
        data={"name": "From JSON", "options": json.dumps({"formats": ["md"], "front_matter": True, "timestamps": "sentence"})},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings?section=presets#settings-presets"  # its own card (TASK-077)
    (row,) = _presets(conn)
    stored = ExportOptions.model_validate_json(row["options_json"])
    assert stored.formats == ["md"] and stored.front_matter is True and stored.timestamps == "sentence"

    assert client.post("/settings/presets", data={"name": "Bad", "options": "{not json"}).status_code == 400
    assert client.post("/settings/presets", data={"name": "Bad", "options": json.dumps({"cpl": 3})}).status_code == 400
    assert [r["name"] for r in _presets(conn)] == ["From JSON"]


def test_saving_a_preset_from_the_dialog_returns_the_select_with_it_chosen(client, conn, transcribed):
    resp = client.post(
        "/settings/presets",
        data={"name": "Mine", "formats": "srt", "cpl": "30"},
        headers={**HX, "HX-Target": "export-preset"},
    )

    assert resp.status_code == 200
    body = resp.text
    assert body.lstrip().startswith("<select")
    assert 'id="export-preset"' in body
    assert re.search(r'<option value="Mine"[^>]*selected', body)
    assert [r["name"] for r in _presets(conn)] == ["Mine"]


def test_a_preset_needs_a_name_that_is_new_and_not_a_built_in(client, conn):
    assert client.post("/settings/presets", data={"name": "  ", "formats": "srt"}).status_code == 400
    assert client.post("/settings/presets", data={"name": "netflix", "formats": "srt"}).status_code == 400
    assert client.post("/settings/presets", data={"name": "Twice", "formats": "srt"}, headers=HX).status_code == 200
    assert client.post("/settings/presets", data={"name": "twice", "formats": "vtt"}).status_code == 409
    assert [r["name"] for r in _presets(conn)] == ["Twice"]


def test_deleting_a_preset_removes_it_from_the_dialog(client, conn, transcribed):
    client.post("/settings/presets", data={"name": "Gone soon", "formats": "srt"}, headers=HX)
    (row,) = _presets(conn)

    resp = client.post(f"/settings/presets/{row['id']}/delete", headers=HX)

    assert resp.status_code == 200
    assert 'id="settings-presets"' in resp.text
    assert "Gone soon" not in resp.text
    assert _presets(conn) == []
    assert "Gone soon" not in client.get(f"/media/{transcribed['media']}/export", headers=HX).text
    assert client.post(f"/settings/presets/{row['id']}/delete").status_code == 404


def test_a_preset_row_edited_into_nonsense_is_skipped_not_fatal(client, conn, transcribed):
    with db.LOCK:
        conn.execute("INSERT INTO export_preset(name, options_json) VALUES ('Broken', 'not json')")
        conn.execute("INSERT INTO export_preset(name, options_json) VALUES ('Fine', ?)", (ExportOptions().model_dump_json(),))
        conn.commit()

    dialog = client.get(f"/media/{transcribed['media']}/export", headers=HX).text

    values = [value for value, _ in _select_options(dialog, "export-preset")]
    assert "Fine" in values and "Broken" not in values
    assert client.get("/settings").status_code == 200


# --- the entries that were disabled -----------------------------------------------------------


def test_the_rail_entry_is_a_link_no_longer_disabled(client, conn, transcribed):
    media_id = transcribed["media"]

    body = client.get(f"/media/{media_id}").text

    # The label gained an ellipsis and a second line when the four one-click
    # formats moved above it: this entry is now the way to every *other*
    # format and every option, which is what "Export…" says.
    link = re.search(
        rf'<a[^>]*href="/media/{media_id}/export"[^>]*>(.*?)</a>', body, re.S
    )
    assert link, "no export link in the rail"
    assert "Export…" in link.group(1)
    assert f'hx-get="/media/{media_id}/export"' in link.group(0)
    assert 'hx-target="#export-dialog"' in link.group(0)
    assert not re.search(r"<button[^>]*disabled[^>]*>Export</button>", body)
    assert '<dialog id="export-dialog"' in body

    # Nothing to export yet: the entry waits, saying why.
    fresh = seed_media(conn, title="Fresh")
    body = client.get(f"/media/{fresh}").text
    assert re.search(r"<button[^>]*disabled[^>]*>Export</button>", body)
    assert f'href="/media/{fresh}/export"' not in body


def test_the_library_menu_entry_is_a_link_for_a_transcribed_media(client, conn, three):
    body = client.get("/").text

    for key in ("alpha", "beta"):
        assert re.search(rf'<a[^>]*href="/media/{three[key]}/export"[^>]*hx-target="#export-dialog"[^>]*>Export</a>', body), key
    assert f'href="/media/{three["fresh"]}/export"' not in body
    assert body.count("<button type=\"button\" disabled") == 1  # Fresh's entry, and only that
