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

import logging
import pathlib
import re
import threading

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from scribe import db, jobs, paths, render
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

    monkeypatch.setattr(transcript, "transcode", fake_transcode)
    monkeypatch.setattr(transcript, "probe_duration", lambda path: 20.0)

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
    monkeypatch.setattr(transcript, "transcode", lambda src, dst: dst.write_bytes(payload))
    monkeypatch.setattr(transcript, "probe_duration", lambda path: 20.0)

    part = client.get(f"/media/{media_id}/audio", headers={"Range": "bytes=10-19"})

    assert part.status_code == 206
    assert part.content == payload[10:20]


def test_audio_serves_the_original_when_the_proxy_duration_drifts(
    client, conn, data_dir, transcribed, monkeypatch, caplog
):
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    original = _store(conn, data_dir, media_id, b"matroska bytes")
    monkeypatch.setattr(transcript, "transcode", lambda src, dst: dst.write_bytes(b"drifted"))
    # The proxy comes out 200 ms longer than the file the timestamps came from.
    monkeypatch.setattr(
        transcript, "probe_duration", lambda path: 20.0 if path == original else 20.2
    )

    with caplog.at_level(logging.WARNING):
        resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.content == b"matroska bytes"
    assert "serving the original" in caplog.text
    proxy_dir = data_dir / "media" / "proxy"
    assert not proxy_dir.exists() or not list(proxy_dir.iterdir())  # nothing bad is kept


def test_audio_serves_the_original_when_the_transcode_fails(
    client, conn, data_dir, transcribed, monkeypatch, caplog
):
    media_id = transcribed["media"]
    _make_mkv(conn, media_id)
    _store(conn, data_dir, media_id, b"matroska bytes")

    def broken(src, dst):
        raise transcript.ProxyError("ffmpeg could not convert clip.mkv: no such codec")

    monkeypatch.setattr(transcript, "transcode", broken)

    with caplog.at_level(logging.WARNING):
        resp = client.get(f"/media/{media_id}/audio")

    assert resp.status_code == 200
    assert resp.content == b"matroska bytes"
    assert "no such codec" in caplog.text


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

    monkeypatch.setattr(transcript, "transcode", slow_transcode)
    monkeypatch.setattr(transcript, "probe_duration", lambda path: 20.0)

    errors: list = []

    def request():
        try:
            transcript.ensure_proxy(original, proxy)
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


def test_within_tolerance_is_fifty_milliseconds():
    assert transcript.durations_agree(20.0, 20.04)
    assert transcript.durations_agree(20.0, 19.96)
    assert not transcript.durations_agree(20.0, 20.06)
    assert not transcript.durations_agree(None, 20.0)
    assert not transcript.durations_agree(20.0, None)


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


def test_the_rail_is_four_titled_groups_rather_than_one_list(client, transcribed):
    """The rail carried one "Actions" heading over four unrelated subjects, so
    an AI provider select and a rename box read as more of the same column.
    Each subject is a card with its own heading now, and the headings are what
    this asserts - the styling can move without failing it."""
    body = client.get(f"/media/{transcribed['media']}").text

    rail = re.search(r'<aside class="rail"[^>]*>(.*?)</aside>', body, re.S).group(1)
    headings = re.findall(r"<h2[^>]*>([^<]+)</h2>", rail)
    assert headings == ["Take away", "AI", "File", "Speakers", "This transcript"], headings
    # The AI section carries the class alongside its own, so match the class
    # rather than the whole attribute.
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


def _publish_reading(conn, run_id, cleaned, words_in=100, words_out=90):
    with db.LOCK:
        conn.execute(
            "INSERT INTO clean_reading(run_id, text, words_in, words_out, created_at)"
            " VALUES (?, ?, ?, ?, 0.0)",
            (run_id, cleaned, words_in, words_out),
        )
        conn.commit()


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
    """A cleaning that the gate refused leaves no trace on the page: the
    recording reads exactly as it did before."""
    body = client.get(f"/media/{transcribed['media']}").text

    assert "data-reading-toggle" not in body
    assert 'id="clean-reading"' not in body


# --- step 1 of the rebuild: four faults that were bugs (TASK-053.01) ------------------


def test_the_answer_section_is_the_live_region_not_the_working_hint(client, transcribed):
    """The hint is REMOVED when the answer lands, so a live region attached to
    it is gone by the time there is something to announce - and the arrival of
    the answer is the only announcement worth making. On the section it
    survives every poll, because each poll swaps this element's outerHTML and
    the replacement carries the attribute too.
    """
    body = client.get(f"/media/{transcribed['media']}").text

    section = re.search(r'<section id="ai-summary"[^>]*>', body)
    assert section, "no summary answer section"
    assert 'aria-live="polite"' in section.group(0)
    assert 'aria-busy="false"' in section.group(0)   # nothing running

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
    for rel in ("scribe/templates/_ai_panel.html", "scribe/templates/transcript.html"):
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
