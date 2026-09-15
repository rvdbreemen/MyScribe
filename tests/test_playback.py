"""TASK-057: the audio a browser plays has to seek exactly.

Measured on 2026-09-15 with screen capture and loopback of the laptop's
speakers: a VBR MP3 plays from the start in sync, and after a seek plays
audio from somewhere else than `currentTime` says - Firefox 3.7 s late,
Chrome 0.36 s. The word times were right all along. A browser seeks a VBR
MP3 through the hundred-point table in its Xing header; a CBR MP3 (an
"Info" header) and AAC in an MP4 box it seeks exactly.

So the rule, `playback.seeks_exactly`, reads the first frame, and what does
not seek exactly is played from the AAC proxy the app already made for
containers a browser cannot open at all. The rule is tested here against
headers written by hand (tests/mp3_headers.py) and against what a real
encoder writes; then the choice of source, the pipeline stage that makes the
proxy for new media, and the command that makes it for the library that is
already there. ffmpeg is monkeypatched everywhere except the encoder test,
which is about what ffmpeg writes.
"""

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from mp3_headers import frame, mp3
from scribe import db, media, paths, playback, runner
from scribe.stages import proxy as proxy_stage

FIXTURES = Path(__file__).parent / "fixtures"
CLIP = FIXTURES / "clip30.wav"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    (data / "media").mkdir(parents=True)
    return data


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Tmp DB that the backfill command also finds through paths.DB_PATH."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


def _file(tmp_path, data: bytes, name: str = "clip.mp3") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _refuse(*args, **kwargs):
    raise AssertionError("nothing here should have been transcoded")


# --- the rule ----------------------------------------------------------------------------


def test_a_cbr_mp3_seeks_exactly(tmp_path):
    assert playback.seeks_exactly(_file(tmp_path, mp3(b"Info")))


@pytest.mark.parametrize(
    "data", [mp3(b"Xing"), mp3(vbri=True), mp3(None)], ids=["xing", "vbri", "no tag"]
)
def test_an_mp3_without_a_constant_bitrate_tag_does_not(tmp_path, data):
    """A table of contents is what makes the seek approximate; no tag at all
    is a file nobody vouched for, and a proxy costs disk, not correctness."""
    assert not playback.seeks_exactly(_file(tmp_path, data))


@pytest.mark.parametrize(
    "shape", [{"mono": True}, {"mpeg2": True}, {"mpeg2": True, "mono": True}],
    ids=["mpeg1 mono", "mpeg2 stereo", "mpeg2 mono"],
)
def test_the_tag_is_read_where_each_frame_shape_puts_it(tmp_path, shape):
    assert playback.seeks_exactly(_file(tmp_path, mp3(b"Info", **shape)))
    assert not playback.seeks_exactly(_file(tmp_path, mp3(b"Xing", **shape)))


def test_a_tag_at_another_shapes_offset_is_not_read_as_one(tmp_path):
    """"Info" 32 bytes into a mono frame is audio that happens to spell it,
    not a header: mono side information is 17 bytes long."""
    data = bytearray(frame(mono=True))
    data[36:40] = b"Info"

    assert not playback.seeks_exactly(_file(tmp_path, bytes(data) + frame(mono=True) * 3))


def test_the_first_frame_is_found_behind_an_id3_tag_the_size_of_a_cover_picture(tmp_path):
    """Podcasts carry their artwork in the ID3 tag, often hundreds of
    kilobytes of it, so the frame is looked for after the tag, not in the
    first few kilobytes of the file."""
    assert playback.seeks_exactly(_file(tmp_path, mp3(b"Info", id3_size=600_000)))
    assert not playback.seeks_exactly(_file(tmp_path, mp3(b"Xing", id3_size=600_000)))


def test_padding_before_the_first_frame_is_skipped(tmp_path):
    assert playback.seeks_exactly(_file(tmp_path, bytes(300) + mp3(b"Info")))


@pytest.mark.parametrize("data", [b"", b"not an mp3 at all " * 500], ids=["empty", "text"])
def test_a_file_with_no_frame_is_not_trusted(tmp_path, data):
    assert not playback.seeks_exactly(_file(tmp_path, data))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")
@pytest.mark.parametrize(
    "rate, exact", [(["-b:a", "128k"], True), (["-q:a", "4"], False)], ids=["cbr", "vbr"]
)
def test_what_a_real_encoder_writes_is_read_the_same_way(tmp_path, rate, exact):
    out = tmp_path / "clip.mp3"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-t", "3", "-i", str(CLIP),
         "-c:a", "libmp3lame", *rate, str(out)],
        check=True,
    )

    assert playback.seeks_exactly(out) is exact


@pytest.mark.parametrize("name", ["a.wav", "a.m4a", "a.mp4", "a.ogg", "a.opus", "a.flac", "a.webm"])
def test_the_other_containers_a_browser_plays_seek_exactly(tmp_path, name):
    assert playback.seeks_exactly(_file(tmp_path, b"bytes", name))


@pytest.mark.parametrize("name", ["a.aac", "a.mkv", "a.avi"])
def test_raw_aac_and_the_containers_a_browser_cannot_open_do_not(tmp_path, name):
    """Raw ADTS AAC has no index to seek by, just like a VBR MP3."""
    assert not playback.seeks_exactly(_file(tmp_path, b"bytes", name))


# --- which file the player gets ----------------------------------------------------------


def _original(data_dir, data: bytes, suffix: str = ".mp3") -> Path:
    path = data_dir / "media" / "ab" / f"abc{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _proxy(sha256: str = "abc") -> Path:
    proxy = media.proxy_path_for(sha256)
    proxy.parent.mkdir(parents=True, exist_ok=True)
    proxy.write_bytes(b"aac in an mp4 box")
    return proxy


def test_the_source_is_the_original_when_it_seeks_exactly(data_dir):
    original = _original(data_dir, mp3(b"Info"))

    assert playback.source(original, "abc") == playback.Source(original, "audio/mpeg", exact=True)


def test_the_source_is_the_proxy_once_it_exists(data_dir):
    original = _original(data_dir, mp3(b"Xing"))
    proxy = _proxy()

    assert playback.source(original, "abc") == playback.Source(proxy, "audio/mp4", exact=True)


def test_the_source_is_the_playable_original_while_there_is_no_proxy(data_dir):
    original = _original(data_dir, mp3(b"Xing"))

    assert playback.source(original, "abc") == playback.Source(original, "audio/mpeg", exact=False)


def test_an_unplayable_container_without_a_proxy_has_no_source_yet(data_dir):
    assert playback.source(_original(data_dir, b"\x1aE\xdf\xa3", ".mkv"), "abc") is None


# --- new media: the pipeline makes the proxy ---------------------------------------------


def _ingest(conn, tmp_path, data: bytes, name: str) -> dict:
    return media.ingest_path(conn, _file(tmp_path, data, name))


def _ctx(conn, row) -> runner.RunnerContext:
    return runner.RunnerContext(
        conn,
        {"id": 1, "media_id": row["id"]},
        {},
        lambda progress: None,
        lambda: False,
        paths.DATA_DIR / row["store_path"],
    )


def test_the_proxy_stage_follows_prepare_in_the_transcribe_registry():
    names = [name for name, _ in runner.STAGES["transcribe"]]

    assert names[:3] == ["probe", "prepare", "proxy"]
    assert runner.STAGES["transcribe"][2][1] is proxy_stage.run


def test_the_proxy_stage_makes_the_proxy_of_a_vbr_mp3(conn, data_dir, tmp_path, monkeypatch):
    row = _ingest(conn, tmp_path, mp3(b"Xing"), "Talk.mp3")
    monkeypatch.setattr(playback, "transcode", lambda src, dst: dst.write_bytes(b"aac"))
    monkeypatch.setattr(playback, "probe_duration", lambda path: 20.0)

    proxy_stage.run(_ctx(conn, row))

    assert media.proxy_path_for(row["sha256"]).read_bytes() == b"aac"


def test_the_proxy_stage_leaves_an_original_that_seeks_exactly_alone(conn, data_dir, tmp_path, monkeypatch):
    row = _ingest(conn, tmp_path, mp3(b"Info"), "Song.mp3")
    monkeypatch.setattr(playback, "transcode", _refuse)

    proxy_stage.run(_ctx(conn, row))

    assert not media.proxy_path_for(row["sha256"]).exists()


def test_a_proxy_that_fails_does_not_fail_the_transcription(conn, data_dir, tmp_path, monkeypatch, caplog):
    """The words do not depend on the proxy; the page says when the player
    has to fall back to the original, and the log says why."""
    row = _ingest(conn, tmp_path, mp3(b"Xing"), "Talk.mp3")

    def broken(src, dst):
        raise playback.ProxyError("ffmpeg could not convert Talk.mp3: no such codec")

    monkeypatch.setattr(playback, "transcode", broken)

    with caplog.at_level(logging.WARNING):
        proxy_stage.run(_ctx(conn, row))

    assert "no such codec" in caplog.text
    assert not media.proxy_path_for(row["sha256"]).exists()


# --- the library that is already there: one command --------------------------------------


def test_the_dry_run_lists_what_needs_a_proxy_and_makes_nothing(conn, data_dir, tmp_path, monkeypatch, capsys):
    _ingest(conn, tmp_path, mp3(b"Xing"), "Talk.mp3")
    _ingest(conn, tmp_path, mp3(b"Info"), "Song.mp3")
    monkeypatch.setattr(playback, "transcode", _refuse)

    assert playback.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Talk.mp3" in out
    assert "Song.mp3" not in out
    assert not (data_dir / "media" / "proxy").exists()


def test_the_backfill_makes_each_missing_proxy_once(conn, data_dir, tmp_path, monkeypatch, capsys):
    talk = _ingest(conn, tmp_path, mp3(b"Xing"), "Talk.mp3")
    _ingest(conn, tmp_path, mp3(b"Info"), "Song.mp3")
    calls = []

    def fake(src, dst):
        calls.append(src)
        dst.write_bytes(b"aac")

    monkeypatch.setattr(playback, "transcode", fake)
    monkeypatch.setattr(playback, "probe_duration", lambda path: 20.0)

    assert playback.main([]) == 0
    assert calls == [paths.DATA_DIR / talk["store_path"]]
    assert media.proxy_path_for(talk["sha256"]).is_file()

    capsys.readouterr()
    assert playback.main([]) == 0
    assert len(calls) == 1
    assert "Talk.mp3" not in capsys.readouterr().out


def test_a_backfill_with_a_failed_proxy_exits_1_and_says_why(conn, data_dir, tmp_path, monkeypatch, capsys):
    _ingest(conn, tmp_path, mp3(b"Xing"), "Talk.mp3")

    def broken(src, dst):
        raise playback.ProxyError("ffmpeg could not convert Talk.mp3: no such codec")

    monkeypatch.setattr(playback, "transcode", broken)

    assert playback.main([]) == 1
    assert "no such codec" in capsys.readouterr().out
