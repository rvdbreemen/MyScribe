"""Phase 3 Task 6: the transcript view and the audio it plays.

A seeded run seen through /media/{id}: a heading per speaker with the labels
applied, a clickable timestamp per sentence, a span per word carrying its
index, its times and its confidence band - every data-* attribute the player
in app.js reads and none it has to compute. Then the audio route: the
original when a browser can play it, a one-time AAC proxy when it cannot,
Range requests either way. No GPU, no models, no pipeline, and no ffmpeg: the
transcode and the duration probe are monkeypatched, because a test that
shells out to ffmpeg is a test of ffmpeg.

The player itself (seek, highlight, resume, search) is JavaScript and is not
tested here; what is tested is that the markup it needs is there.

Task 7 adds the two edits the transcript view allows: renaming a speaker
(one `speaker_label` row, every heading of that cluster follows) and
reassigning a range of words to another speaker, existing or new (one UPDATE
on `word.speaker`, `edited_by_user` set on that range and nowhere else). Text
is never rewritten by either; the tests check that as much as the flips.

`edited_by_user` is a fact about *speakers* - a person chose this word's
cluster - and the tests below say so in as many words, because reading it as
"a person edited this word" is what broke the glossary layer (CR-004, db.py's
v9 comment). The flag that means a person retyped a word is
`word.text_edited_by_user`, and nothing here writes it.
"""

import json
import logging
import pathlib
import re
import subprocess
import threading

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from mp3_headers import mp3
from test_web_url_dialog import needs_node, run_dom
from scribe import db, jobs, media, paths, playback, render
from scribe.app import create_app
from scribe.web import transcript
from seed import default_words, seed_job, seed_media, seed_run


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


HX = {"HX-Request": "true"}


def _media(conn, media_id):
    return conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()


def _store(conn, data_dir, media_id, payload: bytes):
    """Write ``payload`` where the media row says its bytes live."""
    stored = data_dir / _media(conn, media_id)["store_path"]
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(payload)
    return stored


def _make_mkv(conn, media_id):
    """Turn a seeded media row into one whose original is a Matroska file."""
    row = _media(conn, media_id)
    store_path = row["store_path"].rsplit(".", 1)[0] + ".mkv"
    with db.LOCK:
        conn.execute(
            "UPDATE media SET store_path=?, orig_name='clip.mkv' WHERE id=?",
            (store_path, media_id),
        )
        conn.commit()


@pytest.fixture
def transcribed(conn):
    """One media with a current run: the default forty words, two speakers,
    the first of them named."""
    media_id = seed_media(conn, title="Guide", duration=20.0)
    run_id = seed_run(conn, media_id, labels={"SPEAKER_00": "Arthur"})
    seed_job(conn, media_id, status="done")
    return {"media": media_id, "run": run_id}


# --- the page ------------------------------------------------------------------------


def test_transcript_shows_a_heading_per_speaker_in_order_with_labels_applied(client, transcribed):
    resp = client.get(f"/media/{transcribed['media']}")

    assert resp.status_code == 200
    body = resp.text
    assert "<html" in body
    assert "<title>Guide" in body
    headings = re.findall(r'<h3 class="speaker"[^>]*data-cluster="([^"]+)"[^>]*>([^<]*)<', body)
    assert headings == [("SPEAKER_00", "Arthur"), ("SPEAKER_01", "Speaker 2")]


def test_each_sentence_carries_a_clickable_timestamp_with_its_start(client, conn, transcribed):
    body = client.get(f"/media/{transcribed['media']}").text

    stamps = re.findall(r'<a class="ts"[^>]*href="#t=([^"]+)"[^>]*data-start="([^"]+)"[^>]*>([^<]*)</a>', body)
    # Four sentences of ten words each, half a second per word.
    words = default_words()
    starts = [str(words[i]["start"]) for i in (0, 10, 20, 30)]
    assert [s[0] for s in stamps] == starts
    assert [s[1] for s in stamps] == starts
    assert [s[2] for s in stamps] == ["0:00", "0:05", "0:10", "0:15"]


def test_word_spans_carry_index_times_and_confidence_bands(client, conn, transcribed):
    body = client.get(f"/media/{transcribed['media']}").text

    rows = conn.execute(
        "SELECT idx, start, end, text, probability FROM word WHERE run_id=? ORDER BY idx",
        (transcribed["run"],),
    ).fetchall()
    assert len(rows) == 40
    spans = re.findall(
        r'<span class="w band-(\w+)" data-i="(\d+)" data-s="([^"]+)" data-e="([^"]+)">([^<]*)</span>',
        body,
    )
    assert len(spans) == 40
    for row, (band, idx, start, end, text) in zip(rows, spans):
        assert band == render.confidence_band(row["probability"])
        assert int(idx) == row["idx"]
        assert start == str(row["start"]) and end == str(row["end"])
        # The leading space stays on the word; the text is escaped as text.
        assert text == str(escape(row["text"]))
    assert {s[0] for s in spans} == {"high", "mid", "low"}


def test_a_silence_gap_within_one_speaker_starts_a_new_paragraph(client, conn):
    media_id = seed_media(conn)
    words = [
        {"start": 0.0, "end": 0.4, "text": " One.", "speaker": "SPEAKER_00"},
        {"start": 0.5, "end": 0.9, "text": " Two.", "speaker": "SPEAKER_00"},
        {"start": 3.5, "end": 3.9, "text": " Three.", "speaker": "SPEAKER_00"},
    ]
    seed_run(conn, media_id, words=words)

    body = client.get(f"/media/{media_id}").text

    assert body.count('class="para"') == 2
    assert body.count('<h3 class="speaker"') == 2


def test_a_run_without_speakers_has_no_headings(client, conn):
    media_id = seed_media(conn)
    seed_run(conn, media_id, words=[{"start": 0.0, "end": 0.4, "text": " Hello."}])

    body = client.get(f"/media/{media_id}").text

    assert 'class="para"' in body
    assert '<h3 class="speaker"' not in body
    assert "> Hello.</span>" in body


def test_page_has_the_player_and_the_controls_app_js_reads(client, transcribed):
    media_id = transcribed["media"]
    body = client.get(f"/media/{media_id}").text

    audio = re.search(r"<audio[^>]*>", body)
    assert audio, "no audio element"
    assert f'src="/media/{media_id}/audio"' in audio.group(0)
    assert 'id="player"' in audio.group(0)
    assert f'data-resume-key="scribe:resume:{media_id}"' in audio.group(0)
    for speed in ("0.75", "1", "1.25", "1.5", "2"):
        assert f'data-speed="{speed}"' in body
    toggle = re.search(r"<button[^>]*data-toggle-ts[^>]*>", body)
    assert toggle and 'data-storage-key="scribe:hide-ts"' in toggle.group(0)
    assert "data-transcript-search" in body
    assert 'id="transcript"' in body


def test_rail_offers_the_file_actions_and_the_run_info(client, conn, transcribed):
    media_id = transcribed["media"]
    body = client.get(f"/media/{media_id}").text

    for action in ("rename", "move", "trash"):
        form = re.search(rf'<form[^>]*action="/media/{media_id}/{action}"[^>]*>', body)
        assert form, f"no {action} form"
        assert 'data-refresh="#transcript-panel"' in form.group(0)
    trash = re.search(rf'<form[^>]*action="/media/{media_id}/trash"[^>]*>', body).group(0)
    assert "data-confirm=" in trash
    assert f'href="/media/{media_id}/download"' in body
    # Phase 4: the export entry is a link into the export dialog. It reads
    # "Export…" now, with the four one-click formats listed above it, so the
    # ellipsis is what tells the dialog apart from a plain download.
    entry = re.search(rf'<a[^>]*href="/media/{media_id}/export"[^>]*>(.*?)</a>', body, re.S)
    assert entry and "Export…" in entry.group(1)
    assert not re.search(r"<button[^>]*disabled[^>]*>Export</button>", body)
    # Run info: model with its tier icon, language, xRT, when it was made.
    assert "large-v3-turbo" in body and "⚡" in body
    assert ">en<" in body
    assert "14.5" in body
    assert 'href="/jobs/' in body  # the latest job


def test_media_without_a_run_renders_the_not_transcribed_panel_with_the_job_link(client, conn):
    media_id = seed_media(conn, title="Fresh")
    job_id = seed_job(conn, media_id, status="running", stage="transcribe")

    body = client.get(f"/media/{media_id}").text

    assert "not transcribed" in body.lower()
    assert f'href="/jobs/{job_id}"' in body
    assert "s-running" in body
    assert 'class="w ' not in body
    assert f'src="/media/{media_id}/audio"' in body  # you can still listen to it


def test_media_without_a_run_or_a_job_says_so(client, conn):
    media_id = seed_media(conn, title="Untouched")

    body = client.get(f"/media/{media_id}").text

    assert "not transcribed" in body.lower()
    assert 'href="/jobs/' not in body


def test_hx_request_returns_the_panel_alone(client, transcribed):
    body = client.get(f"/media/{transcribed['media']}", headers=HX).text

    assert "<html" not in body
    assert 'id="transcript-panel"' in body
    assert 'id="transcript"' in body
    assert "<audio" not in body  # the player stays where it is


def test_transcript_text_and_speaker_names_are_escaped(client, conn):
    media_id = seed_media(conn, title="<b>Zaphod</b>")
    seed_run(
        conn,
        media_id,
        words=[{"start": 0.0, "end": 0.4, "text": " <script>alert(42)</script>", "speaker": "SPEAKER_00"}],
        labels={"SPEAKER_00": "<i>Trillian</i>"},
    )

    body = client.get(f"/media/{media_id}").text

    assert "<script>alert(42)</script>" not in body
    assert "&lt;script&gt;alert(42)&lt;/script&gt;" in body
    assert "<i>Trillian</i>" not in body and "&lt;i&gt;Trillian&lt;/i&gt;" in body
    assert "<b>Zaphod</b>" not in body and "&lt;b&gt;Zaphod&lt;/b&gt;" in body


def test_a_trashed_media_shows_a_banner_with_restore(client, conn, transcribed):
    media_id = transcribed["media"]
    client.post(f"/media/{media_id}/trash", headers=HX)

    body = client.get(f"/media/{media_id}").text

    assert "in the trash" in body
    assert f'action="/media/{media_id}/restore"' in body


def test_unknown_media_is_a_404(client):
    assert client.get("/media/9999").status_code == 404
    assert client.get("/media/9999/audio").status_code == 404


# --- the audio route ---------------------------------------------------------------------


def test_audio_serves_a_playable_original_with_range_support(client, conn, data_dir, transcribed):
    media_id = transcribed["media"]
    payload = bytes(range(256)) * 4
    _store(conn, data_dir, media_id, payload)

    full = client.get(f"/media/{media_id}/audio")
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("audio/wav")
    assert "content-disposition" not in full.headers  # inline, for the player
    assert full.content == payload

    part = client.get(f"/media/{media_id}/audio", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206
    assert part.headers["content-length"] == "10"
    assert part.headers["content-range"] == f"bytes 0-9/{len(payload)}"
    assert part.content == payload[:10]


def test_audio_of_a_missing_stored_file_is_a_404(client, transcribed):
    assert client.get(f"/media/{transcribed['media']}/audio").status_code == 404


def test_audio_transcodes_an_unplayable_container_once_and_reuses_the_proxy(
    client, conn, data_dir, transcribed, monkeypatch
):
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    original = _store(conn, data_dir, media_id, b"matroska bytes")
    calls = []

    def fake_transcode(src, dst):
        calls.append((src, dst))
        dst.write_bytes(b"aac in an mp4 box")

    monkeypatch.setattr(playback, "transcode", fake_transcode)
    monkeypatch.setattr(playback, "probe_duration", lambda path: 20.0)

    first = client.get(f"/media/{media_id}/audio")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("audio/mp4")
    assert first.content == b"aac in an mp4 box"
    assert len(calls) == 1
    assert calls[0][0] == original
    proxy = data_dir / "media" / "proxy" / f"{_media(conn, media_id)['sha256']}.m4a"
    assert proxy.is_file()
    assert not list(proxy.parent.glob("*.part"))

    second = client.get(f"/media/{media_id}/audio")
    assert second.status_code == 200
    assert second.content == b"aac in an mp4 box"
    assert len(calls) == 1  # the proxy is reused

    # And the original is still what the download route hands out.
    assert client.get(f"/media/{media_id}/download").content == b"matroska bytes"


def test_audio_proxy_supports_range_requests(client, conn, data_dir, transcribed, monkeypatch):
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    _store(conn, data_dir, media_id, b"matroska bytes")
    payload = bytes(range(256))
    monkeypatch.setattr(playback, "transcode", lambda src, dst: dst.write_bytes(payload))
    monkeypatch.setattr(playback, "probe_duration", lambda path: 20.0)

    part = client.get(f"/media/{media_id}/audio", headers={"Range": "bytes=10-19"})

    assert part.status_code == 206
    assert part.content == payload[10:20]


def test_audio_serves_the_original_when_the_proxy_duration_drifts(
    client, conn, data_dir, transcribed, monkeypatch, caplog
):
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    original = _store(conn, data_dir, media_id, b"matroska bytes")
    monkeypatch.setattr(playback, "transcode", lambda src, dst: dst.write_bytes(b"drifted"))
    # The proxy comes out 200 ms longer than the file the timestamps came from.
    monkeypatch.setattr(
        playback, "probe_duration", lambda path: 20.0 if path == original else 20.2
    )

    with caplog.at_level(logging.WARNING):
        resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.content == b"matroska bytes"
    assert "serving the original" in caplog.text
    proxy_dir = data_dir / "media" / "proxy"
    kept = [p.name for p in proxy_dir.rglob("*") if p.is_file()] if proxy_dir.exists() else []
    assert not any(name.endswith(media.PROXY_SUFFIX) for name in kept), kept  # nothing bad is kept
    assert any(name.endswith(".refused") for name in kept), kept  # the refusal is (TASK-075)


def test_a_refused_proxy_is_not_made_again_on_the_next_play(
    client, conn, data_dir, transcribed, monkeypatch
):
    """TASK-075: guard.py lets every GET through because reading changes
    nothing, and this one ran ffmpeg. Once per recording is the bound the
    fallback is allowed; a refused proxy used to cost the whole transcode on
    every page load, every seek that re-requests the source, and every
    cross-site page that embedded the address."""
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    original = _store(conn, data_dir, media_id, b"matroska bytes")
    calls = []

    def drifting(src, dst):
        calls.append(src)
        dst.write_bytes(b"drifted")

    monkeypatch.setattr(playback, "transcode", drifting)
    monkeypatch.setattr(playback, "probe_duration", lambda path: 20.0 if path == original else 20.2)

    first = client.get(f"/media/{media_id}/audio")
    second = client.get(f"/media/{media_id}/audio")

    assert first.content == second.content == b"matroska bytes"
    assert len(calls) == 1


def test_audio_serves_the_original_when_the_transcode_fails(
    client, conn, data_dir, transcribed, monkeypatch, caplog
):
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    _store(conn, data_dir, media_id, b"matroska bytes")

    def broken(src, dst):
        raise playback.ProxyError("ffmpeg could not convert clip.mkv: no such codec")

    monkeypatch.setattr(playback, "transcode", broken)

    with caplog.at_level(logging.WARNING):
        resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.content == b"matroska bytes"
    assert "no such codec" in caplog.text


def test_audio_serves_the_original_when_the_transcode_hits_a_disk_error(
    client, conn, data_dir, transcribed, monkeypatch, caplog
):
    """TASK-066: the route caught ProxyError only.

    The fallback exists because a player must play something. A disk that is
    full, a proxy directory that cannot be written, or Windows refusing to
    replace a file another process holds open are the same situation as a
    codec ffmpeg does not have - the original is still there and still
    playable. Catching only ProxyError turned those into a 500 and a player
    that plays nothing at all.
    """
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    _store(conn, data_dir, media_id, b"matroska bytes")

    def denied(src, dst):
        raise PermissionError("[WinError 5] Access is denied: proxy/ab.m4a.part")

    monkeypatch.setattr(playback, "transcode", denied)

    with caplog.at_level(logging.WARNING):
        resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.content == b"matroska bytes"
    assert "Access is denied" in caplog.text


def test_ensure_proxy_transcodes_once_when_two_requests_race(tmp_path, monkeypatch):
    """A browser's <audio preload="metadata"> asks for the audio twice, and
    the second request lands while the first is still transcoding - before
    the proxy exists. The second waits for the first and serves its result;
    a multi-hour original is not transcoded twice side by side."""
    original = tmp_path / "clip.mkv"
    original.write_bytes(b"matroska bytes")
    proxy = tmp_path / "proxy" / "abc.m4a"
    calls: list = []
    first_started = threading.Event()
    release = threading.Event()

    def slow_transcode(src, dst):
        calls.append(dst)
        first_started.set()
        assert release.wait(5), "the test never released the transcode"
        dst.write_bytes(b"aac")

    monkeypatch.setattr(playback, "transcode", slow_transcode)
    monkeypatch.setattr(playback, "probe_duration", lambda path: 20.0)

    errors: list = []

    def request():
        try:
            playback.ensure_proxy(original, proxy)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    first = threading.Thread(target=request)
    first.start()
    assert first_started.wait(5)
    second = threading.Thread(target=request)
    second.start()
    release.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(calls) == 1
    assert proxy.read_bytes() == b"aac"
    assert not list(proxy.parent.glob("*.part"))


def _make_mp3(conn, media_id):
    """Turn a seeded media row into one whose original is an MP3."""
    row = _media(conn, media_id)
    store_path = row["store_path"].rsplit(".", 1)[0] + ".mp3"
    with db.LOCK:
        conn.execute(
            "UPDATE media SET store_path=?, orig_name='clip.mp3' WHERE id=?",
            (store_path, media_id),
        )
        conn.commit()


def _put_proxy(conn, data_dir, media_id, payload=b"aac in an mp4 box"):
    """A proxy already on disk, where the pipeline or the backfill left it."""
    proxy = data_dir / "media" / "proxy" / f"{_media(conn, media_id)['sha256']}.m4a"
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(payload)
    return proxy


def _no_subprocess(monkeypatch):
    """A request that shells out from here on fails the test: the transcode
    of a VBR MP3 takes 80 s for 39 minutes, and no request waits on that."""

    def refuse(*args, **kwargs):
        raise AssertionError(f"the request ran a subprocess: {args[:1]}")

    monkeypatch.setattr(subprocess, "run", refuse)


def test_audio_plays_a_vbr_mp3_from_its_exact_seeking_proxy(client, conn, data_dir, transcribed):
    """TASK-057: a VBR MP3 seeks through a coarse table of contents, and
    Firefox played 3.7 s away from currentTime after a seek; the AAC proxy
    seeks exactly (-14..+13 ms measured)."""
    media_id = transcribed["media"]
    _make_mp3(conn, media_id)
    original = mp3(b"Xing")
    _store(conn, data_dir, media_id, original)
    _put_proxy(conn, data_dir, media_id)

    resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/mp4")
    assert resp.content == b"aac in an mp4 box"
    part = client.get(f"/media/{media_id}/audio", headers={"Range": "bytes=0-2"})
    assert part.status_code == 206
    assert part.content == b"aac"
    assert client.get(f"/media/{media_id}/download").content == original


def test_audio_serves_a_cbr_mp3_as_itself(client, conn, data_dir, transcribed, monkeypatch):
    media_id = transcribed["media"]
    _make_mp3(conn, media_id)
    payload = mp3(b"Info")
    _store(conn, data_dir, media_id, payload)
    _no_subprocess(monkeypatch)

    resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/mpeg")
    assert resp.content == payload


def test_audio_of_a_vbr_mp3_without_its_proxy_plays_the_original_at_once(
    client, conn, data_dir, transcribed, monkeypatch
):
    media_id = transcribed["media"]
    _make_mp3(conn, media_id)
    payload = mp3(b"Xing")
    _store(conn, data_dir, media_id, payload)
    _no_subprocess(monkeypatch)

    resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/mpeg")
    assert resp.content == payload
    assert not (data_dir / "media" / "proxy").exists()


def test_the_page_says_under_the_player_when_a_jump_can_run_the_highlight_late(
    client, conn, data_dir, transcribed
):
    media_id = transcribed["media"]
    _make_mp3(conn, media_id)
    _store(conn, data_dir, media_id, mp3(b"Xing"))

    body = client.get(f"/media/{media_id}").text

    note = re.search(r"<p[^>]*data-audio-inexact[^>]*>(.*?)</p>", body, re.S)
    assert note, "no note under the player"
    assert body.index('id="player"') < note.start()
    assert "python -m scribe.proxies" in note.group(1)


@pytest.mark.parametrize("tag, proxy", [(b"Info", False), (b"Xing", True)], ids=["cbr", "vbr with its proxy"])
def test_the_page_says_nothing_when_the_audio_seeks_exactly(client, conn, data_dir, transcribed, tag, proxy):
    media_id = transcribed["media"]
    _make_mp3(conn, media_id)
    _store(conn, data_dir, media_id, mp3(tag))
    if proxy:
        _put_proxy(conn, data_dir, media_id)

    assert "data-audio-inexact" not in client.get(f"/media/{media_id}").text


def test_within_tolerance_is_fifty_milliseconds():
    assert playback.durations_agree(20.0, 20.04)
    assert playback.durations_agree(20.0, 19.96)
    assert not playback.durations_agree(20.0, 20.06)
    assert not playback.durations_agree(None, 20.0)
    assert not playback.durations_agree(20.0, None)


# --- speaker rename and word reassignment ------------------------------------------------


def _headings(body: str) -> list[tuple[str, str]]:
    return re.findall(r'<h3 class="speaker"[^>]*data-cluster="([^"]+)"[^>]*>([^<]*)<', body)


def _labels(conn, run_id) -> list[tuple[str, str, str | None]]:
    rows = conn.execute(
        "SELECT cluster_label, display_name, color FROM speaker_label WHERE run_id=?"
        " ORDER BY cluster_label",
        (run_id,),
    ).fetchall()
    return [(r["cluster_label"], r["display_name"], r["color"]) for r in rows]


def _word_rows(conn, run_id) -> list[tuple[int, str | None, int, str]]:
    rows = conn.execute(
        "SELECT idx, speaker, edited_by_user, text FROM word WHERE run_id=? ORDER BY idx",
        (run_id,),
    ).fetchall()
    return [(r["idx"], r["speaker"], r["edited_by_user"], r["text"]) for r in rows]


def _rename(client, media_id, cluster, **fields):
    return client.post(
        f"/media/{media_id}/speakers/{cluster}/rename", data=fields, headers=HX
    )


def _reassign(client, media_id, **fields):
    return client.post(f"/media/{media_id}/words/reassign", data=fields, headers=HX)


@pytest.fixture
def alternating(conn):
    """Three paragraphs - SPEAKER_00, SPEAKER_01, SPEAKER_00 again - so a
    rename has more than one heading to change."""
    media_id = seed_media(conn, title="Dialogue")
    words = []
    for speaker in ("SPEAKER_00", "SPEAKER_01", "SPEAKER_00"):
        for token in ("So", "long", "and", "thanks."):
            i = len(words)
            words.append(
                {"start": i * 0.5, "end": i * 0.5 + 0.4, "text": " " + token, "speaker": speaker}
            )
    run_id = seed_run(conn, media_id, words=words)
    return {"media": media_id, "run": run_id}


def test_rename_upserts_the_label_and_every_heading_of_that_cluster_follows(
    client, conn, alternating
):
    media_id, run_id = alternating["media"], alternating["run"]
    assert _headings(client.get(f"/media/{media_id}").text) == [
        ("SPEAKER_00", "Speaker 1"),
        ("SPEAKER_01", "Speaker 2"),
        ("SPEAKER_00", "Speaker 1"),
    ]

    resp = _rename(client, media_id, "SPEAKER_00", display_name="  Arthur ")

    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert 'id="transcript-panel"' in resp.text
    assert _headings(resp.text) == [
        ("SPEAKER_00", "Arthur"),
        ("SPEAKER_01", "Speaker 2"),
        ("SPEAKER_00", "Arthur"),
    ]
    assert _labels(conn, run_id) == [("SPEAKER_00", "Arthur", None)]


def test_renaming_again_updates_the_one_label_row(client, conn, transcribed):
    media_id, run_id = transcribed["media"], transcribed["run"]
    before = _word_rows(conn, run_id)

    assert _rename(client, media_id, "SPEAKER_00", display_name="Ford").status_code == 200
    again = _rename(client, media_id, "SPEAKER_00", display_name="Ford Prefect")

    assert again.status_code == 200
    assert _labels(conn, run_id) == [("SPEAKER_00", "Ford Prefect", None)]
    assert _headings(again.text) == [("SPEAKER_00", "Ford Prefect"), ("SPEAKER_01", "Speaker 2")]
    assert _word_rows(conn, run_id) == before  # a rename touches no word (ADR-003)


def test_rename_takes_an_optional_colour_and_keeps_it_across_a_rename_without_one(
    client, conn, transcribed
):
    media_id, run_id = transcribed["media"], transcribed["run"]

    resp = _rename(client, media_id, "SPEAKER_01", display_name="Marvin", color="#FF8800")

    assert resp.status_code == 200
    assert _labels(conn, run_id) == [("SPEAKER_00", "Arthur", None), ("SPEAKER_01", "Marvin", "#ff8800")]
    heading = re.search(r'<h3 class="speaker"[^>]*data-cluster="SPEAKER_01"[^>]*>', resp.text).group(0)
    assert "--speaker-color: #ff8800" in heading
    unstyled = re.search(r'<h3 class="speaker"[^>]*data-cluster="SPEAKER_00"[^>]*>', resp.text).group(0)
    assert "--speaker-color" not in unstyled

    assert _rename(client, media_id, "SPEAKER_01", display_name="Marvin the Paranoid Android").status_code == 200
    assert _labels(conn, run_id)[1] == ("SPEAKER_01", "Marvin the Paranoid Android", "#ff8800")

    assert _rename(client, media_id, "SPEAKER_01", display_name="Marvin", color="orange").status_code == 400
    assert _labels(conn, run_id)[1] == ("SPEAKER_01", "Marvin the Paranoid Android", "#ff8800")


def test_rename_refuses_an_unknown_cluster_an_empty_name_and_a_media_without_a_run(
    client, conn, transcribed
):
    media_id, run_id = transcribed["media"], transcribed["run"]

    assert _rename(client, media_id, "SPEAKER_07", display_name="Nobody").status_code == 404
    assert _rename(client, media_id, "SPEAKER_00", display_name="   ").status_code == 400
    fresh = seed_media(conn, title="Fresh")
    assert _rename(client, fresh, "SPEAKER_00", display_name="Arthur").status_code == 409
    assert _rename(client, 9999, "SPEAKER_00", display_name="Arthur").status_code == 404

    assert _labels(conn, run_id) == [("SPEAKER_00", "Arthur", None)]


def test_reassign_flips_the_speaker_and_records_who_chose_it_for_that_range_only(
    client, conn, transcribed
):
    """`edited_by_user` marks the reassigned range and nothing else. It says a
    person chose the *speaker* of these words - the text assertion in the loop
    is the other half of that sentence, and it is why the glossary must not
    read this flag as "hand-edited text" (CR-004)."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    before = _word_rows(conn, run_id)

    resp = _reassign(client, media_id, from_idx="10", to_idx="19", speaker="SPEAKER_01")

    assert resp.status_code == 200
    assert "<html" not in resp.text
    for (idx, speaker, edited, text), (_, old_speaker, _, old_text) in zip(
        _word_rows(conn, run_id), before
    ):
        assert text == old_text  # never rewritten (ADR-003)
        if 10 <= idx <= 19:
            assert (speaker, edited) == ("SPEAKER_01", 1)
        else:
            assert (speaker, edited) == (old_speaker, 0)
    # The paragraphs regrouped themselves: ten words of Arthur, then thirty.
    assert _headings(resp.text) == [("SPEAKER_00", "Arthur"), ("SPEAKER_01", "Speaker 2")]
    paras = re.findall(r'<section class="para"[^>]*>(.*?)</section>', resp.text, re.S)
    assert [p.count('class="w ') for p in paras] == [10, 30]


def test_reassign_to_a_new_speaker_creates_a_user_label_named_by_the_user(
    client, conn, transcribed
):
    media_id, run_id = transcribed["media"], transcribed["run"]

    resp = _reassign(client, media_id, from_idx="30", to_idx="39", speaker="new", display_name="Zaphod")

    assert resp.status_code == 200
    moved = {(idx, speaker, edited) for idx, speaker, edited, _ in _word_rows(conn, run_id) if idx >= 30}
    assert moved == {(i, "USER_1", 1) for i in range(30, 40)}
    assert _labels(conn, run_id) == [("SPEAKER_00", "Arthur", None), ("USER_1", "Zaphod", None)]
    assert _headings(resp.text) == [
        ("SPEAKER_00", "Arthur"),
        ("SPEAKER_01", "Speaker 2"),
        ("USER_1", "Zaphod"),
    ]
    # The new speaker is now one of the known ones the toolbar offers.
    assert '<option value="USER_1">Zaphod</option>' in resp.text

    again = _reassign(client, media_id, from_idx="0", to_idx="4", speaker="new", display_name="Trillian")

    assert again.status_code == 200
    assert ("USER_2", "Trillian", None) in _labels(conn, run_id)
    assert _headings(again.text)[0] == ("USER_2", "Trillian")


def test_reassign_refuses_a_range_outside_the_run_or_back_to_front(client, conn, transcribed):
    media_id, run_id = transcribed["media"], transcribed["run"]

    def attempt(from_idx: str, to_idx: str) -> int:
        return _reassign(client, media_id, from_idx=from_idx, to_idx=to_idx, speaker="SPEAKER_01").status_code

    assert attempt("0", "40") == 400  # forty words: 0..39
    assert attempt("-1", "3") == 400
    assert attempt("12", "3") == 400
    assert attempt("", "3") == 400
    assert attempt("three", "5") == 400
    assert all(edited == 0 for _, _, edited, _ in _word_rows(conn, run_id))
    assert attempt("0", "39") == 200  # the whole run is a range too


def test_reassign_needs_a_known_speaker_or_a_name_for_a_new_one(client, conn, transcribed):
    media_id, run_id = transcribed["media"], transcribed["run"]

    assert _reassign(client, media_id, from_idx="0", to_idx="3", speaker="SPEAKER_07").status_code == 400
    assert _reassign(client, media_id, from_idx="0", to_idx="3", speaker="").status_code == 400
    assert _reassign(client, media_id, from_idx="0", to_idx="3", speaker="new", display_name=" ").status_code == 400
    fresh = seed_media(conn, title="Fresh")
    assert _reassign(client, fresh, from_idx="0", to_idx="0", speaker="new", display_name="X").status_code == 409

    assert _labels(conn, run_id) == [("SPEAKER_00", "Arthur", None)]
    assert all(edited == 0 for _, _, edited, _ in _word_rows(conn, run_id))


def test_speaker_edits_answer_a_plain_post_with_a_redirect_to_the_page(client, conn, transcribed):
    media_id = transcribed["media"]

    renamed = client.post(
        f"/media/{media_id}/speakers/SPEAKER_00/rename",
        data={"display_name": "Ford"},
        follow_redirects=False,
    )
    assert renamed.status_code == 303
    assert renamed.headers["location"] == f"/media/{media_id}"

    moved = client.post(
        f"/media/{media_id}/words/reassign",
        data={"from_idx": "0", "to_idx": "1", "speaker": "SPEAKER_01"},
        follow_redirects=False,
    )
    assert moved.status_code == 303
    assert moved.headers["location"] == f"/media/{media_id}"

    headings = _headings(client.get(f"/media/{media_id}").text)
    assert headings[:2] == [("SPEAKER_01", "Speaker 2"), ("SPEAKER_00", "Ford")]


def test_page_carries_the_speaker_controls_app_js_wires(client, transcribed):
    media_id = transcribed["media"]
    body = client.get(f"/media/{media_id}").text

    # Each heading knows where its rename posts.
    for cluster in ("SPEAKER_00", "SPEAKER_01"):
        heading = re.search(rf'<h3 class="speaker"[^>]*data-cluster="{cluster}"[^>]*>', body).group(0)
        assert f'data-rename-url="/media/{media_id}/speakers/{cluster}/rename"' in heading

    # The inline rename form is a template app.js clones, aimed at the panel.
    template = re.search(r"<template data-rename-template>(.*?)</template>", body, re.S)
    assert template, "no rename template"
    assert 'name="display_name"' in template.group(1)
    assert 'hx-target="#transcript-panel"' in template.group(1)

    # The assign toolbar: hidden until a range is chosen, posting the range
    # and a speaker chosen from the known ones or "new" with a name.
    toolbar = re.search(r"<form[^>]*data-assign-toolbar[^>]*>(.*?)</form>", body, re.S)
    assert toolbar, "no assign toolbar"
    assert " hidden" in toolbar.group(0).split(">", 1)[0]
    assert f'hx-post="/media/{media_id}/words/reassign"' in toolbar.group(0)
    assert 'name="from_idx"' in toolbar.group(1) and 'name="to_idx"' in toolbar.group(1)
    options = re.findall(r'<option value="([^"]+)">([^<]*)</option>', toolbar.group(1))
    assert options == [("SPEAKER_00", "Arthur"), ("SPEAKER_01", "Speaker 2"), ("new", "New speaker…")]
    assert 'name="display_name"' in toolbar.group(1)

    # The rail lists the speakers with a rename form each - the path without scripting.
    rail = re.search(r'<aside class="rail"[^>]*>(.*?)</aside>', body, re.S).group(1)
    forms = re.findall(r'<form[^>]*action="(/media/\d+/speakers/[^"]+/rename)"[^>]*>', rail)
    assert forms == [
        f"/media/{media_id}/speakers/SPEAKER_00/rename",
        f"/media/{media_id}/speakers/SPEAKER_01/rename",
    ]
    assert 'value="Arthur"' in rail and 'value="Speaker 2"' in rail


def test_the_rail_is_titled_groups_rather_than_one_list(client, transcribed):
    """The rail carried one "Actions" heading over four unrelated subjects, so
    an AI provider select and a rename box read as more of the same column.
    Each subject is a card with its own heading now, and the headings are what
    this asserts - the styling can move without failing it.

    AI left the rail in TASK-053.04: the menu is above the transcript, where
    the answer it produces now appears. What stays here is the file and the
    reading of it."""
    body = client.get(f"/media/{transcribed['media']}").text

    rail = re.search(r'<aside class="rail"[^>]*>(.*?)</aside>', body, re.S).group(1)
    headings = re.findall(r"<h2[^>]*>([^<]+)</h2>", rail)
    assert headings == ["Take away", "File", "Speakers", "This transcript"], headings
    assert "AI" not in headings
    assert len(re.findall(r'class="[^"]*rail-group', rail)) == len(headings)


def test_the_speed_is_rounded_rather_than_seventeen_digits_of_a_wall_clock(
    client, conn, transcribed
):
    """SQLite hands back the REAL the finalize stage measured; rendering it raw
    put "1.7054469741450804x realtime" in the rail, which is seventeen digits
    of precision about a number timed with a wall clock."""
    with db.LOCK:
        conn.execute("UPDATE run SET xrt=? WHERE id=?", (1.7054469741450804, transcribed["run"]))
        conn.commit()

    body = client.get(f"/media/{transcribed['media']}").text

    speed = re.search(r"([\d.]+)× realtime", body)
    assert speed, "no speed in the rail"
    assert speed.group(1) == "1.7"


def test_the_rail_downloads_four_formats_in_one_click(client, transcribed):
    """Each row is a form posting nothing but the format: parse_export_options
    fills the rest from the defaults, so a one-click download needs no route
    of its own and cannot drift from what the dialog would produce."""
    body = client.get(f"/media/{transcribed['media']}").text

    rail = re.search(r'<aside class="rail"[^>]*>(.*?)</aside>', body, re.S).group(1)
    posts = re.findall(
        r'<form method="post" action="/media/\d+/export">\s*'
        r'<input type="hidden" name="formats" value="([a-z]+)">',
        rail,
    )
    assert posts == [fmt for fmt, _ in transcript.QUICK_EXPORTS]
    assert 'name="destination" value="download"' in rail
    # Every one of them is a format this app actually writes.
    from scribe import exports

    assert set(posts) <= set(exports.FORMATS)


def test_one_click_export_returns_the_file_itself(client, transcribed):
    resp = client.post(
        f"/media/{transcribed['media']}/export",
        data={"formats": "txt", "destination": "download"},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.text.strip()


# --- typing over a word ----------------------------------------------------------


def _correct(client, media_id, idx, **fields):
    return client.post(f"/media/{media_id}/words/{idx}/correct", data=fields, headers=HX)


def test_typing_over_a_word_stores_a_correction_and_offers_the_others(client, conn):
    media_id = seed_media(conn, title="Guide")
    run_id = seed_run(conn, media_id)  # "the" occurs in every sentence of the default transcript
    words = _word_rows(conn, run_id)
    idx = next(i for i, _s, _e, t in words if t.strip().casefold() == "the")

    resp = _correct(client, media_id, idx, text="teh")

    assert resp.status_code == 200
    body = resp.text
    assert f'data-i="{idx}"' in body and "Edited: was &quot;the&quot;" in body
    assert 'class="word-offer"' in body
    assert "other words in this transcript say “the”" in body
    assert f'action="/media/{media_id}/words/{idx}/correct"' in body
    rows = conn.execute("SELECT word_idx, corrected, rule FROM word_correction WHERE run_id=?", (run_id,)).fetchall()
    assert [(r["word_idx"], r["corrected"], r["rule"]) for r in rows] == [(idx, " teh", "manual")]
    # Whisper's word is untouched (ADR-003).
    assert conn.execute("SELECT text FROM word WHERE run_id=? AND idx=?", (run_id, idx)).fetchone()["text"] == " the"


def test_replacing_every_occurrence_writes_one_row_per_word_and_exports_read_them(client, conn):
    from scribe.exports import doc as docs

    media_id = seed_media(conn, title="Guide")
    run_id = seed_run(conn, media_id)
    words = _word_rows(conn, run_id)
    the = [i for i, _s, _e, t in words if t.strip().casefold() == "the"]

    resp = _correct(client, media_id, the[0], text="THE", scope="all")

    assert resp.status_code == 200
    assert f"{len(the)} words now say “THE”." in resp.text
    rows = conn.execute("SELECT word_idx FROM word_correction WHERE run_id=? AND rule='manual' ORDER BY word_idx", (run_id,)).fetchall()
    assert [r["word_idx"] for r in rows] == the
    loaded = docs.load(conn, media_id)
    assert all(w["text"].strip(".,").strip() == "THE" for w in loaded.words if w["idx"] in the)
    assert "the" not in " ".join(w["text"] for w in loaded.words).split(" the ")[0:0]


def test_a_word_the_run_does_not_have_is_404_and_a_bad_scope_400(client, conn):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)

    assert _correct(client, media_id, 9999, text="x").status_code == 404
    assert _correct(client, media_id, 0, text="x", scope="everywhere").status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM word_correction").fetchone()[0] == 0


def test_the_panel_carries_the_inline_editor_template_and_the_correct_url(client, conn):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    body = client.get(f"/media/{media_id}").text
    assert "<template data-correct-template>" in body
    assert f'data-correct-url="/media/{media_id}/words/"' in body


# --- who owns a name (TASK-024) ----------------------------------------------------


def _label_row(conn, run_id, cluster):
    return dict(
        conn.execute(
            "SELECT display_name, source, llm_output_id, confidence FROM speaker_label"
            " WHERE run_id=? AND cluster_label=?",
            (run_id, cluster),
        ).fetchone()
    )


def test_renaming_a_speaker_the_model_named_takes_ownership_of_the_name(
    client, conn, transcribed
):
    """The rule that makes an unattended pass safe is 'never over a human', and
    that rule reads speaker_label.source. A rename that left the column alone
    would hand the name a person just typed back to the next pass to overwrite,
    because the row would still say 'llm' - whoever wrote it first.

    The analysis link goes with it: 'which analysis chose this' has no honest
    answer once somebody has typed over it.
    """
    media_id, run_id = transcribed["media"], transcribed["run"]
    with db.LOCK:
        conn.execute(
            "UPDATE speaker_label SET source='llm', confidence=97.0 WHERE run_id=?"
            " AND cluster_label='SPEAKER_00'",
            (run_id,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO speaker_label(run_id, cluster_label, display_name,"
            " source, confidence) VALUES (?, 'SPEAKER_00', 'Arthur', 'llm', 97.0)",
            (run_id,),
        )
        conn.commit()

    assert _rename(client, media_id, "SPEAKER_00", display_name="Ford").status_code == 200

    row = _label_row(conn, run_id, "SPEAKER_00")
    assert row["display_name"] == "Ford"
    assert row["source"] == "human"
    assert row["llm_output_id"] is None and row["confidence"] is None


def test_a_name_typed_from_scratch_belongs_to_the_person_who_typed_it(
    client, conn, transcribed
):
    media_id, run_id = transcribed["media"], transcribed["run"]

    _rename(client, media_id, "SPEAKER_00", display_name="Ford")

    assert _label_row(conn, run_id, "SPEAKER_00")["source"] == "human"


def test_the_panel_can_say_where_a_speakers_name_came_from(client, conn, transcribed):
    """Accountability has to reach the page, not stop at the table."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    with db.LOCK:
        conn.execute(
            "INSERT INTO speaker_label(run_id, cluster_label, display_name, source, confidence)"
            " VALUES (?, 'SPEAKER_00', 'Arthur', 'llm', 96.5)"
            " ON CONFLICT(run_id, cluster_label) DO UPDATE SET"
            "   display_name='Arthur', source='llm', confidence=96.5",
            (run_id,),
        )
        conn.commit()

    speakers = transcript.run_speakers(conn, run_id)
    arthur = next(s for s in speakers if s["cluster"] == "SPEAKER_00")

    assert arthur["source"] == "llm"
    assert arthur["confidence"] == 96.5


# --- the two readings (TASK-026) ---------------------------------------------------


def _publish_reading(conn, run_id, cleaned, words_in=100, words_out=90, words_hash=None):
    with db.LOCK:
        conn.execute(
            "INSERT INTO clean_reading(run_id, text, words_in, words_out, created_at, words_hash)"
            " VALUES (?, ?, ?, ?, 0.0, ?)",
            (run_id, cleaned, words_in, words_out, words_hash),
        )
        conn.commit()


def test_the_page_says_when_the_words_were_edited_after_the_reading(client, conn, transcribed):
    """TASK-071: a reading made from words that have since been corrected is
    still shown - it is a paid answer - but the page says so, and says how to
    get one of the words as they are now. A reading from before fingerprints
    (no hash) is not flagged: not knowing is not evidence."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    _publish_reading(conn, run_id, "The cleaned words.", words_hash="made-from-other-words")

    body = client.get(f"/media/{media_id}").text

    assert "The cleaned words." in body
    assert "data-reading-stale" in body
    assert "edited after this reading was made" in body

    with db.LOCK:
        conn.execute("UPDATE clean_reading SET words_hash=NULL WHERE run_id=?", (run_id,))
        conn.commit()
    assert "data-reading-stale" not in client.get(f"/media/{media_id}").text


def test_a_published_cleaning_puts_both_readings_on_the_page(client, conn, transcribed):
    """Both are already there, so the switch is a local matter and the
    transcript is one click away whichever is showing."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    _publish_reading(conn, run_id, "The cleaned words.")

    body = client.get(f"/media/{media_id}").text

    assert 'id="transcript"' in body
    assert 'id="clean-reading"' in body
    assert "The cleaned words." in body
    assert "data-reading-toggle" in body


def test_the_page_says_which_reading_it_is_showing(client, conn, transcribed):
    """A reader who cannot tell has been handed an edit without being told."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    _publish_reading(conn, run_id, "The cleaned words.")

    body = client.get(f"/media/{media_id}").text

    assert "Showing the transcript as it was heard." in body
    assert "100 words became" in body and "90" in body


def test_the_cleaned_block_starts_hidden_so_the_words_are_what_loads(
    client, conn, transcribed
):
    """The transcript is the default reading. The derived one is offered."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    _publish_reading(conn, run_id, "The cleaned words.")

    body = client.get(f"/media/{media_id}").text
    block = body.split('id="clean-reading"')[1][:120]

    assert "hidden" in block


def test_no_switch_at_all_when_nothing_was_published(client, transcribed):
    """Nobody asked for a cleaning: no switch, no cleaned block, and no line
    about one either."""
    body = client.get(f"/media/{transcribed['media']}").text

    assert "data-reading-toggle" not in body
    assert 'id="clean-reading"' not in body
    assert "data-cleanup-status" not in body


# --- a cleaning that is not shown says why (TASK-055) ---------------------------------
#
# Found on 2026-09-14: the live library had cleanup answers and no reading, and
# the page said nothing. The answers predated the gate; had it run, it would
# have refused media 7's (3418% of the words). Either way the reader was left
# to guess why the "cleaned reading" never appeared.


def _cleanup_answer(conn, media_id, run_id, params, kind="cleanup", model="qwen3.5:4b"):
    with db.LOCK:
        conn.execute(
            "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version,"
            " content, created_at, run_id, params_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (media_id, kind, "ollama", model, 1, "la la la", 1757518680.0, run_id,
             json.dumps(params)),
        )
        conn.commit()


def _status_line(body):
    found = re.search(r"<p[^>]*data-cleanup-status[^>]*>(.*?)</p>", body, re.S)
    return None if found is None else " ".join(found.group(1).split())


def test_a_refused_cleaning_says_why_instead_of_saying_nothing(client, conn, transcribed):
    media_id, run_id = transcribed["media"], transcribed["run"]
    _cleanup_answer(conn, media_id, run_id, {
        "finish_reason": "stop",
        "gate": {"published": False, "reasons": ["part 1 kept 8% of its words"], "words_in": 48, "words_out": 4},
    })

    body = client.get(f"/media/{media_id}").text

    line = _status_line(body)
    assert line is not None, "the page says nothing about the refused cleaning"
    assert "not shown" in line
    assert "part 1 kept 8% of its words" in line
    assert "qwen3.5:4b" in line
    assert "data-reading-toggle" not in body


# --- TASK-088: a copied part's refusal reads on the page like any other ------------


def test_a_part_copied_without_its_grouping_says_so_on_the_page(client, conn, transcribed):
    """The page joins the gate's reasons as they are, so the part rule's
    reason needs no page change - shown here with the reason the gate itself
    writes, not a hand-typed one."""
    from scribe.llm import tasks

    run_on = "[0:00] SPEAKER_00: so the towel\n[0:04] SPEAKER_00: is important"
    verdict = tasks.check_cleaning(
        [run_on, "[1:00] SPEAKER_01: uh forty-two"], [run_on, "[1:00] SPEAKER_01: Forty-two."]
    )
    assert not verdict["ok"]
    media_id, run_id = transcribed["media"], transcribed["run"]
    _cleanup_answer(conn, media_id, run_id, {
        "finish_reason": "stop",
        "gate": {"published": False, "reasons": verdict["reasons"],
                 "words_in": verdict["words_in"], "words_out": verdict["words_out"]},
    })

    line = _status_line(client.get(f"/media/{media_id}").text)

    assert line is not None and "not shown" in line
    assert (
        "part 0 came back as it went in, though 1 of its 2 lines continue the speaker "
        "before them: the grouping cleanup asks for was skipped"
    ) in line


def test_a_cleanup_answer_from_before_the_gate_says_it_was_never_checked(client, conn, transcribed):
    """Row 15 in the live library: made on 2026-09-10 before cleanings were
    checked at all, so it carries no verdict. That is not a refusal, and the
    page does not pretend it is one."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    _cleanup_answer(conn, media_id, run_id, {"finish_reason": "length"})

    line = _status_line(client.get(f"/media/{media_id}").text)

    assert line is not None
    assert "never checked" in line
    assert "Clean transcript" in line


def test_a_cleanup_answer_about_an_earlier_transcript_adds_no_line(client, conn, transcribed):
    """Its transcript is gone (run_id set null), so it says nothing about the
    words on this page."""
    _cleanup_answer(conn, transcribed["media"], None, {"gate": {"published": False, "reasons": ["x"]}})

    assert "data-cleanup-status" not in client.get(f"/media/{transcribed['media']}").text


def test_a_cleanup_part_alone_adds_no_line(client, conn, transcribed):
    """Media 12's shape: part 0 of a chunked cleaning that never finished. A
    part is not an answer, and nothing was ever there to show."""
    _cleanup_answer(conn, transcribed["media"], transcribed["run"], {}, kind="cleanup:chunk:0")

    assert "data-cleanup-status" not in client.get(f"/media/{transcribed['media']}").text


def test_a_published_reading_is_shown_even_beside_a_later_refusal(client, conn, transcribed):
    """A newer cleaning that was refused leaves the published one in place, so
    the page offers that reading and has nothing to apologise for."""
    media_id, run_id = transcribed["media"], transcribed["run"]
    _publish_reading(conn, run_id, "The cleaned words.")
    _cleanup_answer(conn, media_id, run_id, {"gate": {"published": False, "reasons": ["x"]}})

    body = client.get(f"/media/{media_id}").text

    assert "data-reading-toggle" in body
    assert "data-cleanup-status" not in body


# --- step 1 of the rebuild: four faults that were bugs (TASK-053.01) ------------------


def test_the_slot_around_an_answer_is_the_live_region_not_the_answer_or_the_hint(client, conn, transcribed):
    """The hint is REMOVED when the answer lands, so a live region attached to
    it is gone by the time there is something to announce - and the arrival of
    the answer is the only announcement worth making. Not on the section
    either (TASK-106.06): each poll swaps its outerHTML, and a screen reader
    takes the replacement for a new region rather than a change in the one
    it watched. It sits on the .ai-slot around it, which no poll replaces.

    An answer has to exist for there to be a section at all: since
    TASK-053.03 a kind nobody asked about renders nothing.
    """
    with db.LOCK:
        conn.execute(
            "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version,"
            " content, created_at, run_id) VALUES (?,?,?,?,?,?,?,?)",
            (transcribed["media"], "summary", "ollama", "qwen3.5:4b", 1,
             '{"text": "A summary."}', 0.0, transcribed["run"]),
        )
        conn.commit()

    body = client.get(f"/media/{transcribed['media']}").text

    section = re.search(r'<section id="ai-summary"[^>]*>', body)
    assert section, "no summary answer section"
    assert "aria-live" not in section.group(0)
    assert 'aria-busy="false"' in section.group(0)   # nothing running
    slot = re.search(r'<div class="ai-slot" data-slot="summary"[^>]*>\s*<section id="ai-summary"', body)
    assert slot and 'aria-live="polite"' in slot.group(0), "the stable slot is the live region"

    hint = re.search(r'<p class="hint working"[^>]*>', body)
    assert hint is None or "aria-live" not in hint.group(0)


def test_a_running_answer_says_it_is_busy(client, conn, transcribed):
    """aria-busy is what stops a screen reader announcing a half-written
    answer on every two-second poll."""
    media_id = transcribed["media"]
    jobs.enqueue(conn, "llm", media_id, {"media_id": media_id, "kind": "summary"})

    body = client.get(f"/media/{media_id}").text

    section = re.search(r'<section id="ai-summary"[^>]*>', body)
    assert section and 'aria-busy="true"' in section.group(0)


def test_the_comments_do_not_claim_six_questions(client):
    """KINDS has been nine for a while; three comments still said six, which
    is how a reader learns to distrust the comments in a file that otherwise
    earns being trusted."""
    from scribe.llm import tasks

    root = pathlib.Path(__file__).resolve().parents[1]
    assert len(tasks.KINDS) == 9
    for rel in ("scribe/templates/_ai_region.html", "scribe/templates/transcript.html"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "six" not in text.lower().replace("sixth", ""), rel


def test_the_speed_buttons_read_the_rate_rather_than_remembering_it(client):
    """The player's own overflow menu is browser chrome this app cannot
    remove, and a rate chosen there used to leave 1x reading aria-pressed=true
    while the audio ran at 2x. The only fix is to listen to the element.

    Asserted in the source because this repository has no harness that runs
    app.js - the behaviour itself is checked in a browser.
    """
    js = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.js").read_text(
        encoding="utf-8"
    )
    assert "addEventListener('ratechange'" in js
    assert "function showSpeed()" in js
    # setSpeed must not write the buttons itself, or the two paths can disagree
    body = js.split("function setSpeed(")[1].split("}")[0]
    assert "aria-pressed" not in body


@needs_node
def test_a_swap_outside_the_panel_leaves_the_word_selection_alone(tmp_path):
    """TASK-068: the AI cards poll every two seconds, outside the panel.

    app.js reset the transcript's state on every htmx:afterSwap that reached
    the body, asking only whether a #transcript-panel existed - not whether it
    was the thing replaced. So while an AI answer was pending, every two
    seconds `anchor = -1` threw away the word a reader had clicked, and the
    shift-click that should have closed a range started a new one instead.

    The anchor is a variable, so what is asserted is what it does: click word
    0, let a card swap happen, shift-click word 2. With the anchor intact that
    is three selected words; without it, one.

    (The same listener also calls clearSearch(). That half cannot be shown
    here: the find code walks text nodes, and this stub refuses to build them
    on purpose - see its createTextNode. The anchor half needs only classes.)
    """
    result = run_dom(
        tmp_path,
        r"""
    /* The page as _transcript_panel.html builds it: the panel with the words,
       and the AI region beside it, outside the panel. */
    const panel = body.append(el('div', { id: 'transcript-panel' }));
    /* The block keys off the player: no #player, no transcript behaviour. */
    panel.append(el('audio', { id: 'player' }));
    const transcript = panel.append(el('div', { id: 'transcript' }));
    const para = transcript.append(el('p', { class: 'para' }));
    const words = ['budget', 'is', 'the', 'budget'].map(function (text, i) {
      const w = para.append(el('span', {
        class: 'w', 'data-i': String(i), 'data-s': String(i), 'data-e': String(i + 1)
      }));
      w.textContent = text;
      return w;
    });
    const card = body.append(el('section', { id: 'ai-summary' }));

    load(APP);

    function selected() { return document.querySelectorAll('#transcript .w.sel').length; }

    fire(document, 'click', event({ target: words[0] }));      /* anchors word 0 */
    const anchored = document.querySelectorAll('#transcript .w.anchor').length;

    /* The AI card's own poll answered and htmx swapped it. The panel was not
       touched, so the reader's anchor must still be there. */
    fire(document.body, 'htmx:afterSwap', event({ detail: { target: card } }));
    fire(document, 'click', event({ target: words[2], shiftKey: true }));
    const afterCardSwap = selected();

    /* The panel itself being replaced is the case that must still reset:
       those words are new nodes and an index into the old ones means nothing. */
    fire(document, 'click', event({ target: words[0] }));
    fire(document.body, 'htmx:afterSwap', event({ detail: { target: panel } }));
    fire(document, 'click', event({ target: words[2], shiftKey: true }));
    const afterPanelSwap = selected();

    done({ anchored: anchored, afterCardSwap: afterCardSwap, afterPanelSwap: afterPanelSwap });
""",
    )

    assert result["anchored"] == 1, "the click did not anchor, so the test proves nothing"
    assert result["afterCardSwap"] == 3, "a swap outside the panel threw the anchor away"
    assert result["afterPanelSwap"] == 1


def test_the_search_looks_at_whichever_reading_is_on_screen(client):
    """With the cleaned reading showing, search used to count matches in the
    hidden words, highlight them where nobody could see and scroll to them."""
    js = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.js").read_text(
        encoding="utf-8"
    )
    assert "function searchBlocks()" in js
    # runSearch asks searchBlocks() rather than querying the words directly.
    # Scoped to that function's body: the comment above it names the old
    # selector on purpose, and a file-wide assertion would fail on the
    # explanation rather than on the code.
    run_search = js.split("function runSearch(")[1].split("\n    }")[0]
    assert "searchBlocks()" in run_search
    assert "querySelectorAll" not in run_search
    assert "#clean-reading mark.find" in js   # clearing covers both readings


# --- step 2: the highlight is sampled finer than the words (TASK-053.02) -------------


def _app_js() -> str:
    return (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.js").read_text(
        encoding="utf-8"
    )


def test_playback_drives_the_highlight_from_animation_frames(client):
    """timeupdate fires about four times a second and wordAt returns the last
    word started by t, so words that begin between two events were never lit.

    Asserted in the source because nothing here runs app.js; the count of
    words actually lit is measured in a browser.
    """
    js = _app_js()
    assert "function followFrame()" in js
    assert "requestAnimationFrame(followFrame)" in js
    for event in ("'play'", "'playing'"):
        assert f"addEventListener({event}, startFollowing)" in js
    for event in ("'pause'", "'ended'"):
        assert f"addEventListener({event}, stopFollowing)" in js


def test_timeupdate_stays_wired_as_the_fallback(client):
    """A backgrounded tab gets no animation frames while the audio plays on,
    and the resume position is saved from here. Both paths call the same
    function and highlight returns early when the word has not changed, so
    they cannot fight."""
    js = _app_js()
    handler = js.split("audio.addEventListener('timeupdate', function () {")[1].split("});")[0]
    assert "highlight(audio.currentTime, true)" in handler
    assert "writeStore(resumeKey" in handler


def test_the_page_only_scrolls_when_the_word_would_leave_the_band(client):
    """At four samples a second, re-centring on every move was fine. At sixty
    the highlight moves once per word - ten times a second in fast speech -
    and a smooth scroll restarted ten times a second never arrives."""
    js = _app_js()
    assert "function offScreen(el)" in js
    assert "offScreen(words[i])" in js
    assert "FOLLOW_BAND_TOP" in js and "FOLLOW_BAND_BOTTOM" in js


def test_only_one_follow_loop_can_run(client):
    """Two loops would double every highlight call and never stop on pause."""
    js = _app_js()
    start = js.split("function startFollowing()")[1].split("}")[0]
    assert "if (!following)" in start


# --- step 3: delete what nobody asked for, and cap the rail (TASK-053.03) ------------


def test_every_kind_gets_a_card_asked_or_not(client, transcribed):
    """TASK-053.03 gated the unasked cards away as clutter; Robert put them
    back - "de 9 antwoordkaarten zijn nog altijd nuttige kaarten". An unasked
    card says what this recording could be asked and holds the place its
    answer will land in. The complaint this rebuild came from was the distance
    to the answer, never the menu."""
    from scribe.llm import tasks

    body = client.get(f"/media/{transcribed['media']}").text

    assert body.count('class="ai-output"') == len(tasks.KINDS)
    for kind in tasks.KINDS:
        assert f'id="ai-{kind}"' in body, kind
    assert "Not asked yet" in body


def test_an_answer_that_exists_replaces_its_empty_card(client, conn, transcribed):
    media_id = transcribed["media"]
    with db.LOCK:
        conn.execute(
            "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version,"
            " content, created_at, run_id) VALUES (?,?,?,?,?,?,?,?)",
            (media_id, "summary", "ollama", "qwen3.5:4b", 1,
             '{"text": "They talked about libraries."}', 0.0, transcribed["run"]),
        )
        conn.commit()

    summary = re.search(r'<section id="ai-summary".*?</section>',
                        client.get(f"/media/{media_id}").text, re.DOTALL).group(0)

    # What the card shows is _ai_output.html's business and varies by kind;
    # what this test is about is that an answered kind stops offering itself
    # and starts saying where its answer came from.
    assert "Not asked yet" not in summary
    assert "qwen3.5:4b" in summary and "ollama" in summary


def test_the_ask_box_still_belongs_to_the_last_kind(client, transcribed):
    """The trap the gate had to avoid. The rail builds its buttons from
    ai.panels[:-1] and its ask box from ai.panels[-1], which holds only
    because `custom` is last in KINDS. Filtering the list upstream would make
    one button disappear and the ask box relabel itself - and a test that
    counted answer sections would pass either way."""
    from scribe.llm import tasks

    body = client.get(f"/media/{transcribed['media']}").text

    last = tasks.KINDS[-1]
    assert last == "custom"
    assert f'hx-target="#ai-{last}"' in body
    # every other kind still has a button of its own
    for kind in tasks.KINDS[:-1]:
        assert f'hx-target="#ai-{kind}"' in body, kind


def test_the_rail_cannot_put_its_own_bottom_out_of_reach(client):
    """A `top`-only sticky element taller than the viewport can never scroll
    its own bottom into view, and the rail grows a field per speaker."""
    css = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.css").read_text(
        encoding="utf-8"
    )
    rail = css.split(".rail {")[1].split("}")[0]
    assert "position: sticky" in rail
    assert "max-height" in rail
    assert "overflow-y: auto" in rail


def test_the_typed_question_survives_a_panel_refresh(client, transcribed):
    """Eleven unrelated actions refresh this panel and panel_context rebuilds
    it from settings, so the question, the provider and whether the details
    were open were all lost without warning."""
    body = client.get(f"/media/{transcribed['media']}").text

    details = re.search(r'<details class="options"[^>]*>', body)
    assert details and 'hx-preserve="true"' in details.group(0)
    assert 'id="ai-options"' in details.group(0)
    for control in ('id="ai-prompt"', 'id="ai-provider"', 'id="ai-model"'):
        assert control in body, control


# --- steps 4 and 5: the menu, the tabs, the bounded panel (TASK-053.04/.05) ----------


def _region(body: str) -> str:
    return re.search(r'<section id="ai-region".*?</section>\s*\n\n', body, re.DOTALL).group(0)


def test_the_ai_region_comes_before_the_transcript_and_is_not_inside_it(client, transcribed):
    """Two properties, and the second is the one the whole rebuild rests on.

    Before, because an answer thirty thousand pixels below the button that
    asked for it is the fault this exists to remove. Outside, because
    #transcript-panel swaps its own outerHTML and would destroy an answer
    being written.

    Asserted as "a sibling, earlier" rather than "follows #transcript-panel",
    because the old test was the latter and it is exactly the assertion
    somebody fixes instead of thinking about.
    """
    body = client.get(f"/media/{transcribed['media']}").text

    region_at = body.index('id="ai-region"')
    panel_at = body.index('id="transcript-panel"')
    assert region_at < panel_at

    panel = body[panel_at:]
    assert "ai-region" not in panel.split("</aside>")[0]


def test_an_htmx_refresh_of_the_panel_carries_no_answer_markup(client, transcribed):
    """The other half of the same property, from the server's side: the
    fragment a rename gets back must contain nothing the answers own, or the
    swap would replace them with a copy that has lost its polling."""
    body = client.get(f"/media/{transcribed['media']}", headers=HX).text

    assert "ai-region" not in body
    assert 'id="ai-' not in body


def test_the_menu_offers_every_kind_and_each_asks_for_its_own(client, transcribed):
    from scribe.llm import tasks

    region = _region(client.get(f"/media/{transcribed['media']}").text)

    for kind in tasks.KINDS:
        assert f'data-asks="{kind}"' in region, kind
        assert f'hx-target="#ai-{kind}"' in region, kind


def test_every_card_is_rendered_and_all_but_one_are_hidden(client, transcribed):
    """Hidden is not absent. hx-trigger="every 2s" only fires for an element
    in the document, so a card left out to save markup would stop polling and
    its answer would never arrive."""
    from scribe.llm import tasks

    region = _region(client.get(f"/media/{transcribed['media']}").text)

    slots = re.findall(r'<div class="ai-slot" data-slot="([^"]+)"([^>]*)>', region)
    assert [kind for kind, _ in slots] == list(tasks.KINDS)
    shown = [kind for kind, attrs in slots if "hidden" not in attrs]
    assert len(shown) == 1


def test_a_tab_per_kind_and_exactly_one_is_selected(client, transcribed):
    from scribe.llm import tasks

    region = _region(client.get(f"/media/{transcribed['media']}").text)

    tabs = re.findall(r'<button type="button" role="tab"[^>]*data-tab="([^"]+)"[^>]*aria-selected="([^"]+)"', region)
    assert [kind for kind, _ in tabs] == list(tasks.KINDS)
    assert [k for k, sel in tabs if sel == "true"] == [tasks.KINDS[0]]


def test_the_region_opens_on_the_job_that_is_running(client, conn, transcribed):
    """The one somebody is waiting for, not the first in the list."""
    media_id = transcribed["media"]
    jobs.enqueue(conn, "llm", media_id, {"media_id": media_id, "kind": "chapters"})

    region = _region(client.get(f"/media/{media_id}").text)

    assert re.search(r'data-tab="chapters"[^>]*aria-selected="true"', region)
    assert re.search(r'data-slot="chapters"(?![^>]*hidden)', region)


def test_the_region_opens_on_the_newest_answer_when_nothing_runs(client, conn, transcribed):
    media_id = transcribed["media"]
    with db.LOCK:
        for kind, created in (("summary", 10.0), ("labels", 99.0)):
            conn.execute(
                "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version,"
                " content, created_at, run_id) VALUES (?,?,?,?,?,?,?,?)",
                (media_id, kind, "ollama", "qwen3.5:4b", 1, "{}", created, transcribed["run"]),
            )
        conn.commit()

    region = _region(client.get(f"/media/{media_id}").text)

    assert re.search(r'data-tab="labels"[^>]*aria-selected="true"', region)


def test_the_answer_panel_is_bounded_so_the_transcript_starts_in_one_place(client):
    """`blog` allows 6000 output tokens and `cleanup` is as long as the input.
    Without a ceiling here the transcript would start several screens down on
    every later visit - the fault this rebuild removes, moved rather than
    fixed."""
    css = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.css").read_text(
        encoding="utf-8"
    )
    panels = css.split(".ai-panels {")[1].split("}")[0]
    assert "max-height" in panels
    assert "overflow-y: auto" in panels


def test_asking_switches_to_its_own_tab_before_the_request_leaves(client):
    """scrollIntoView and htmx's show: are no-ops inside a hidden container,
    so asking while another tab is open would put the answer where nobody can
    see it - the same bug as the thirty thousand pixels, one layer up."""
    js = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.js").read_text(
        encoding="utf-8"
    )
    handler = js.split("function wireAiTabs()")[1]
    assert "data-asks" in handler
    assert "showTab(ask.getAttribute('data-asks'), true)" in handler
    # and the tab machinery uses properties, not CSS-state selectors
    show = js.split("function showTab(kind, open)")[1].split("\n  }")[0]
    assert "slot.hidden" in show
    assert ":checked" not in show and "[hidden]" not in show


def test_pinning_private_answers_with_the_region_not_the_whole_transcript(client, transcribed):
    """Pinning changes which providers are offered, not a single word. It used
    to re-render every paragraph to move one badge."""
    media_id = transcribed["media"]

    resp = client.post(
        f"/media/{media_id}/private",
        data={"private": "1"},
        headers={**HX, "HX-Target": "ai-region"},
    )

    assert resp.status_code == 200
    assert 'id="ai-region"' in resp.text
    assert 'id="transcript-panel"' not in resp.text
    assert "🔒 Private" in resp.text


def test_the_answer_panel_rests_closed_so_the_transcript_is_the_page(client, transcribed):
    """Open by default costs the transcript 300px of a 800px window on every
    visit, and Robert's rule is that the transcript is the page. The tabs stay
    either way: which kinds hold an answer is worth seeing without opening
    one."""
    region = _region(client.get(f"/media/{transcribed['media']}").text)

    assert 'data-open="false"' in region
    toggle = re.search(r'<button type="button" class="ai-toggle"[^>]*>', region)
    assert toggle and 'aria-expanded="false"' in toggle.group(0)
    assert 'class="ai-tabs"' in region   # the strip is still there


def test_a_job_you_are_waiting_for_opens_the_panel(client, conn, transcribed):
    media_id = transcribed["media"]
    jobs.enqueue(conn, "llm", media_id, {"media_id": media_id, "kind": "minutes"})

    region = _region(client.get(f"/media/{media_id}").text)

    assert 'data-open="true"' in region
    assert re.search(r'class="ai-toggle"[^>]*aria-expanded="true"', region)


def test_the_closed_panel_is_hidden_by_the_stylesheet_not_by_markup(client):
    """Hidden is not absent: every card keeps polling while the panel is shut,
    or an answer asked for and then collapsed would never arrive."""
    css = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.css").read_text(
        encoding="utf-8"
    )
    assert '.ai-region[data-open="false"] .ai-panels { display: none; }' in css


# --- step 6: the correction offer follows the word (TASK-053.06) ---------------------


def test_the_correction_offer_names_the_word_it_is_about(client, conn, transcribed):
    """Without the index there is nothing to move it to, and the offer stays
    at the top of the main column - up to 25,000px from the word on a
    64-minute recording, reported to a screen nobody is looking at."""
    media_id = transcribed["media"]
    # Word 2 is " the", which the seeded transcript says seven times, so
    # correcting it leaves six others saying the same.
    resp = client.post(
        f"/media/{media_id}/words/2/correct",
        data={"scope": "one", "text": "lockpicking"},
        headers=HX,
    )

    offer = re.search(r'<form class="word-offer"[^>]*>', resp.text)
    assert offer, "no offer after correcting a word others share"
    assert 'data-word-offer="2"' in offer.group(0)


def test_the_offer_is_rendered_where_it_works_without_scripting(client, conn, transcribed):
    """It is moved beside the word by app.js; the server still puts it in the
    main column, so with scripting off it is visible and reachable - just
    further from the word than it could be."""
    media_id = transcribed["media"]
    resp = client.post(
        f"/media/{media_id}/words/2/correct",
        data={"scope": "one", "text": "lockpicking"},
        headers=HX,
    )

    body = resp.text
    assert body.index('class="word-offer"') < body.index('id="transcript"')
    assert "Replace" in body


def test_the_offer_is_moved_by_index_rather_than_by_measuring(client):
    """content-visibility: auto on .para makes off-screen boxes unreliable, so
    anything that positioned this by geometry would work on screen and fail
    for the paragraph you actually corrected."""
    js = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.js").read_text(
        encoding="utf-8"
    )
    place = js.split("function placeWordOffer()")[1].split("\n    }")[0]
    assert "data-word-offer" in place
    assert "insertAdjacentElement('afterend'" in place
    assert "getBoundingClientRect" not in place
    # the swap drops focus to <body>; the next thing a reader wants is the button
    assert "focus(" in place


# --- step 7: the player gets a structure ribbon (TASK-053.07) ------------------------


def test_the_ribbon_is_a_band_per_paragraph_placed_by_time(client, conn, transcribed):
    body = client.get(f"/media/{transcribed['media']}").text

    ribbon = re.search(r'<div class="ribbon"[^>]*>(.*?)</div>', body, re.DOTALL)
    assert ribbon, "no ribbon"
    bands = re.findall(r'<button type="button" class="band[^"]*"\s+data-at="([^"]+)"\s+style="left: ([^%]+)%; width: ([^%]+)%"', ribbon.group(1))
    assert len(bands) == body.count('class="para"')
    # placed in order, inside the track, and none of zero width
    lefts = [float(left) for _, left, _ in bands]
    assert lefts == sorted(lefts)
    assert all(0.0 <= left <= 100.0 for left in lefts)
    assert all(float(width) > 0 for _, _, width in bands)


def test_a_recording_without_a_duration_gets_no_ribbon(client, conn):
    """A band whose width is a guess is worse than no ribbon: it looks
    authoritative."""
    media_id = seed_media(conn, title="No duration", duration=None)
    seed_run(conn, media_id)

    body = client.get(f"/media/{media_id}").text

    assert 'class="ribbon"' not in body


def test_a_band_says_where_it_goes_and_who_is_speaking(client, transcribed):
    """A ribbon of unlabelled blocks is decoration. Each band carries the
    second it seeks to and a name a screen reader can read."""
    body = client.get(f"/media/{transcribed['media']}").text

    ribbon = re.search(r'<div class="ribbon".*?</div>\s*\n\s*<audio', body, re.DOTALL).group(0)
    assert 'data-at="0"' in ribbon
    assert "Arthur" in ribbon
    assert "visually-hidden" in ribbon


def test_the_ribbon_moves_the_playhead_by_time_never_by_measuring(client):
    """content-visibility: auto makes off-screen paragraphs unreliable to
    measure, and the ones worth jumping to are always off screen."""
    js = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.js").read_text(
        encoding="utf-8"
    )
    move = js.split("function movePlayhead()")[1].split("\n    }")[0]
    assert "getBoundingClientRect" not in move
    assert "data-duration" in move
    assert "currentTime" in move


def test_the_page_promises_no_waveform(client):
    """Nothing stores peaks and decoding a 64-minute file in the browser costs
    minutes of CPU, so a waveform would be a promise this app cannot keep.
    Asserted because it is the obvious thing for a later hand to add."""
    js = (pathlib.Path(__file__).resolve().parents[1] / "scribe/static/app.js").read_text(
        encoding="utf-8"
    )
    for absent in ("AudioContext", "decodeAudioData", "getChannelData"):
        assert absent not in js, absent


def test_speaker_bands_are_clipped_to_the_recording(client, conn):
    """A run whose last word runs past the stored duration would otherwise
    place a band off the end of the track."""
    from scribe.web.transcript import speaker_bands
    from scribe import render

    words = [
        {"idx": 0, "start": 0.0, "end": 5.0, "text": " One.", "speaker": "S0", "probability": 0.9},
        {"idx": 1, "start": 90.0, "end": 200.0, "text": " Two.", "speaker": "S1", "probability": 0.9},
    ]
    bands = speaker_bands(render.paragraphs(words), duration=100.0)

    assert bands and all(band["left"] + band["width"] <= 100.001 for band in bands), bands


def test_no_template_comment_leaks_into_the_page(client, conn, transcribed):
    """Found by looking: editing inside a {# … #} block left an unbalanced
    closer, and half a comment rendered as visible text above the player -
    "would be a promise this app cannot keep. #}". Every template test passed.

    One assertion over the whole page, because the next one will be in a
    different file.
    """
    media_id = transcribed["media"]
    for path in (f"/media/{media_id}", "/", "/jobs", "/feeds", "/settings"):
        body = client.get(path).text
        assert "#}" not in body, path
        assert "{#" not in body, path


# --- a run whose speakers could not be worked out says so (TASK-089.08) ---------
#
# `diarization_note` has been written to `run.params_json` since the assembled
# fallback shipped and was rendered by nothing: no template and no route read
# it, so "says so on the run" was only ever half true. Putting it on the page
# is what makes a job that keeps its transcript and skips the speakers a
# success with a note rather than a silence.


def _note_on_the_run(conn, run_id, note):
    with db.LOCK:
        conn.execute(
            "UPDATE run SET params_json=? WHERE id=?",
            (json.dumps({"diarization_note": note}), run_id),
        )
        conn.commit()


def _diarization_note(body):
    found = re.search(r"<p[^>]*data-diarization-note[^>]*>(.*?)</p>", body, re.S)
    return None if found is None else " ".join(found.group(1).split())


def test_a_run_that_could_not_work_out_the_speakers_says_so_on_the_page(
    client, conn, transcribed
):
    _note_on_the_run(
        conn,
        transcribed["run"],
        "Speakers were not worked out: no Hugging Face token was found.",
    )

    body = client.get(f"/media/{transcribed['media']}").text

    line = _diarization_note(body)
    assert line is not None, "the page says nothing about the speakers it has none of"
    assert "no Hugging Face token was found" in line


def test_the_note_links_to_the_token_field_rather_than_naming_it(client, conn, transcribed):
    """A link that lands on the field, not on the paragraph above it: the
    three routes are in the note, and the one a user can take right now is a
    click away. Its text is the action, because the same paragraph carries
    two different notes and one of them names no place at all."""
    _note_on_the_run(conn, transcribed["run"], "Speakers were not worked out.")

    body = client.get(f"/media/{transcribed['media']}").text

    assert (
        '<a href="/settings?section=defaults#hf-token">Open the token field</a>' in body
    )


def test_a_run_that_has_its_speakers_says_nothing_about_missing_ones(client, transcribed):
    assert "data-diarization-note" not in client.get(f"/media/{transcribed['media']}").text


def test_the_substitution_note_reaches_the_page_too(client, conn, transcribed):
    """The rider on TASK-089.08, pinned rather than left implicit.

    `FALLBACK_NOTE` has been written to the run since the assembled 3.1
    fallback shipped and was rendered by nothing. This is a run that *has*
    speakers, so the line says what diarized rather than what is missing -
    and the route it offers is the same one, because accepting the
    conditions is what gets community-1 back.
    """
    from scribe.stages.diarize import FALLBACK_NOTE

    _note_on_the_run(conn, transcribed["run"], FALLBACK_NOTE)

    line = _diarization_note(client.get(f"/media/{transcribed['media']}").text)

    assert line is not None
    assert "speaker-diarization-3.1" in line
    assert "/settings?section=defaults#hf-token" in line


def test_a_note_that_is_not_json_leaves_the_transcript_alone(client, conn, transcribed):
    """A run row with unreadable params is a run with no note, not a 500.

    This is a line beside a transcript, and the transcript is the thing the
    page exists to show - so the parse failure costs the note and nothing
    else."""
    with db.LOCK:
        conn.execute(
            "UPDATE run SET params_json=? WHERE id=?", ("not json", transcribed["run"])
        )
        conn.commit()

    body = client.get(f"/media/{transcribed['media']}").text

    assert "data-diarization-note" not in body
    # The words are still there; only the note is gone. (The first word is
    # "Don't", which renders escaped - so the second.)
    assert default_words()[1]["text"].strip() in body


def test_the_panel_on_its_own_carries_the_note_as_well(client, conn, transcribed):
    """The panel is re-fetched alone after every rail action (`refresh`), so a
    note that only survived the full page would vanish at the first rename."""
    _note_on_the_run(conn, transcribed["run"], "Speakers were not worked out.")

    body = client.get(f"/media/{transcribed['media']}", headers=HX).text

    assert "<html" not in body
    assert "data-diarization-note" in body


# --- a speaker pass that could not be asked says so (TASK-089.10) --------------


def _speaker_pass_note_on_the_run(conn, run_id, note):
    with db.LOCK:
        conn.execute(
            "UPDATE run SET params_json=? WHERE id=?",
            (json.dumps({"speaker_pass_note": note}), run_id),
        )
        conn.commit()


def _speaker_pass_note(body):
    found = re.search(r"<p[^>]*data-speaker-pass-note[^>]*>(.*?)</p>", body, re.S)
    return None if found is None else " ".join(found.group(1).split())


def test_a_run_whose_speaker_pass_could_not_be_asked_says_so_on_the_page(
    client, conn, transcribed
):
    """TASK-089.08's lesson, applied before the defect can repeat: a note
    written to `run.params_json` and rendered by nothing says it only to the
    database. This note exists to be read, so it is on the page beside the
    diarization one."""
    _speaker_pass_note_on_the_run(
        conn,
        transcribed["run"],
        "The speakers were not named automatically: Ollama is not running at "
        "http://127.0.0.1:11434 (start it, then reload).",
    )

    line = _speaker_pass_note(client.get(f"/media/{transcribed['media']}").text)

    assert line is not None, "the page says nothing about the pass it never asked"
    assert "not running" in line
    assert "start it" in line


def test_a_run_whose_speakers_were_asked_for_says_nothing_about_a_pass(client, transcribed):
    assert "data-speaker-pass-note" not in client.get(f"/media/{transcribed['media']}").text


# --- TASK-106.03: the recording first --------------------------------------------


def test_the_page_opens_on_the_title_and_the_way_back_before_the_ai_tasks(client, conn, alternating):
    """Measured 2026-10-05: the page opened on the privacy strip and the AI
    tasks, with the title and the back link below them."""
    page = client.get(f"/media/{alternating['media']}").text

    title = page.index('id="transcript-title"')
    assert title < page.index('id="ai-region"') if 'id="ai-region"' in page else True
    assert title < page.index("Not private") if "Not private" in page else True
    assert page.count('id="transcript-title"') == 1, "the full page carries the title once"


def test_a_rename_of_the_recording_updates_the_title_at_the_top(client, conn, alternating):
    media_id = alternating["media"]
    client.post(f"/media/{media_id}/rename", data={"title": "Now with a new name"})

    panel = client.get(f"/media/{media_id}", headers={"HX-Request": "true"}).text

    assert 'id="transcript-title" class="transcript-title" hx-swap-oob="true"' in panel
    assert "Now with a new name" in panel
