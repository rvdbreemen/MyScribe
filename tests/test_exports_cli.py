"""Phase 4 Task 6: `python -m scribe.export`, the command line over the writers.

`scribe.exports.cli.main` is the third door to the exporters, after the
dialog and the bulk action: a media id (or `--all`, or `--folder ID`), the
formats, a preset, the option fields as flags, and a directory to write into.
These tests run `main` in-process against a temp data directory (the paths
monkeypatched the way the web tests do) seeded with tests/seed.py, and check
what lands on disk, what is printed, and the exit code. The bytes written are
compared to what the writers produce for the same document and options, so
the CLI is proven to be the same export as the dialog's write-to-folder
(ADR-003: the writers are pure, and every door only calls them). One test
spawns `python -m scribe.export --help` to prove the module entry point is
wired. No GPU, no models, no pipeline, no ffmpeg.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scribe import db, exports, paths, playback
from scribe.exports import cli, cues, doc as export_doc, html_bundle, srt, txt, vtt
from scribe.exports.options import PRESETS, ExportOptions
from seed import seed_media, seed_run

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    data.mkdir()
    return data


@pytest.fixture
def conn(data_dir):
    """The database where the CLI looks for it: paths.DB_PATH."""
    c = db.connect(paths.DB_PATH)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


@pytest.fixture
def transcribed(conn):
    """One media with the seed transcript: two speakers, the first named."""
    media_id = seed_media(conn, title="Guide", duration=20.0)
    seed_run(conn, media_id, labels={"SPEAKER_00": "Arthur"})
    return media_id


@pytest.fixture
def three(conn):
    """Two transcribed media and one without a transcript."""
    alpha = seed_media(conn, title="Alpha")
    seed_run(conn, alpha, labels={"SPEAKER_00": "Arthur"})
    beta = seed_media(conn, title="Beta")
    seed_run(conn, beta)
    fresh = seed_media(conn, title="Fresh")
    return {"alpha": alpha, "beta": beta, "fresh": fresh}


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def _store(conn, media_id: int, payload: bytes) -> Path:
    row = conn.execute("SELECT store_path FROM media WHERE id=?", (media_id,)).fetchone()
    stored = paths.DATA_DIR / row["store_path"]
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(payload)
    return stored


# --- one media -----------------------------------------------------------------------


def test_export_of_one_media_writes_the_file_and_prints_its_path(conn, transcribed, out_dir, capsys):
    doc = export_doc.load(conn, transcribed)

    code = cli.main([str(transcribed), "--format", "srt", "--out", str(out_dir)])

    assert code == 0
    assert _names(out_dir) == ["Guide.srt"]
    assert (out_dir / "Guide.srt").read_bytes() == srt.write(doc, ExportOptions(formats=["srt"]))
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [str(out_dir / "Guide.srt")]
    assert captured.err == ""
    assert not list(out_dir.glob("*.part"))  # written whole, renamed into place


def test_several_formats_are_written_in_the_order_given(conn, transcribed, out_dir, capsys):
    doc = export_doc.load(conn, transcribed)
    options = ExportOptions(formats=["txt", "vtt", "srt"], cpl=30, speakers=False)

    code = cli.main(
        [str(transcribed), "--format", "txt", "--format", "vtt", "--format", "srt",
         "--cpl", "30", "--no-speakers", "--out", str(out_dir)]
    )

    assert code == 0
    assert capsys.readouterr().out.splitlines() == [
        str(out_dir / "Guide.txt"), str(out_dir / "Guide.vtt"), str(out_dir / "Guide.srt")
    ]
    assert (out_dir / "Guide.txt").read_bytes() == txt.write(doc, options)
    assert (out_dir / "Guide.vtt").read_bytes() == vtt.write(doc, options)
    assert (out_dir / "Guide.srt").read_bytes() == srt.write(doc, options)
    assert b"Arthur" not in (out_dir / "Guide.srt").read_bytes()


def test_the_format_defaults_to_srt_and_out_to_the_current_directory(conn, transcribed, out_dir, monkeypatch, capsys):
    monkeypatch.chdir(out_dir)

    assert cli.main([str(transcribed)]) == 0

    assert _names(out_dir) == ["Guide.srt"]
    assert capsys.readouterr().out.splitlines() == [str(out_dir / "Guide.srt")]


def test_out_is_created_when_it_does_not_exist(conn, transcribed, tmp_path):
    target = tmp_path / "new" / "deep"

    assert cli.main([str(transcribed), "--out", str(target)]) == 0

    assert _names(target) == ["Guide.srt"]


def test_every_option_field_is_a_flag(conn, transcribed, out_dir):
    doc = export_doc.load(conn, transcribed)
    options = ExportOptions(
        formats=["md", "csv"], timestamps="sentence", timestamp_format="seconds",
        front_matter=True, include_confidence=True, crlf=True, encoding="utf-8",
        filename_template="{title}-{lang}",
    )

    code = cli.main(
        [str(transcribed), "--format", "md", "--format", "csv", "--timestamps", "sentence",
         "--timestamp-format", "seconds", "--front-matter", "--include-confidence", "--crlf",
         "--encoding", "utf-8", "--filename-template", "{title}-{lang}", "--out", str(out_dir)]
    )

    assert code == 0
    assert _names(out_dir) == ["Guide-en.csv", "Guide-en.md"]
    md = (out_dir / "Guide-en.md").read_bytes()
    assert md == exports.FORMATS["md"].writer(doc, options)
    assert md.startswith(b"---\r\n")
    assert b"mean_confidence" in (out_dir / "Guide-en.csv").read_bytes()

    parser = cli.build_parser()
    flags = {action.dest for action in parser._actions}
    assert set(ExportOptions.model_fields) <= flags


# --- presets -------------------------------------------------------------------------


def _cue_lines(path: Path) -> list[str]:
    """The text lines of an SRT file: no cue numbers, no timings, no blanks."""
    text = path.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.isdigit() and "-->" not in line]


def test_the_youtube_preset_yields_cues_of_at_most_32_characters(conn, transcribed, tmp_path):
    doc = export_doc.load(conn, transcribed)
    # The default constraints let a line run past 32; the preset must bite.
    assert any(len(line) > 32 for cue in cues.build(doc, ExportOptions()).cues for line in cue.lines)

    # At the defaults the names are on. The engine fits the cue's lines to
    # cpl; the `NAME: ` prefix is the SRT writer's own and is not budgeted
    # (scribe/exports/srt.py says so), so the check is on the cue text behind
    # it, and the first line of the file may run past 32 by the name's width.
    named = tmp_path / "named"
    code = cli.main([str(transcribed), "--preset", "youtube", "--out", str(named)])

    assert code == 0
    assert _names(named) == ["Guide.srt"]
    lines = _cue_lines(named / "Guide.srt")
    assert any(line.startswith("Arthur: ") for line in lines)
    cue_text = [line.split(": ", 1)[1] if line.startswith(("Arthur: ", "Speaker 2: ")) else line for line in lines]
    assert cue_text and all(len(line) <= 32 for line in cue_text)
    assert (named / "Guide.srt").read_bytes() == srt.write(doc, PRESETS["youtube"])

    # Without names every line of the file is within 32.
    plain = tmp_path / "plain"
    code = cli.main([str(transcribed), "--preset", "youtube", "--no-speakers", "--out", str(plain)])

    assert code == 0
    lines = _cue_lines(plain / "Guide.srt")
    assert lines and all(len(line) <= 32 for line in lines)
    expected = PRESETS["youtube"].model_copy(update={"speakers": False})
    assert (plain / "Guide.srt").read_bytes() == srt.write(doc, expected)


def test_a_flag_overrides_the_presets_value(conn, transcribed, out_dir):
    doc = export_doc.load(conn, transcribed)

    code = cli.main([str(transcribed), "--preset", "youtube", "--cpl", "20", "--format", "vtt", "--out", str(out_dir)])

    assert code == 0
    assert _names(out_dir) == ["Guide.vtt"]
    expected = ExportOptions.model_validate({**PRESETS["youtube"].model_dump(), "cpl": 20, "formats": ["vtt"]})
    assert (out_dir / "Guide.vtt").read_bytes() == vtt.write(doc, expected)


def test_a_saved_preset_is_found_by_name(conn, transcribed, out_dir):
    doc = export_doc.load(conn, transcribed)
    saved = ExportOptions(formats=["vtt"], cpl=30, speakers=False)
    with db.LOCK:
        conn.execute(
            "INSERT INTO export_preset(name, options_json) VALUES (?, ?)",
            ("House style", saved.model_dump_json()),
        )
        conn.commit()

    code = cli.main([str(transcribed), "--preset", "house style", "--out", str(out_dir)])

    assert code == 0
    assert _names(out_dir) == ["Guide.vtt"]
    assert (out_dir / "Guide.vtt").read_bytes() == vtt.write(doc, saved)


def test_an_unknown_preset_is_a_usage_error_naming_the_known_ones(conn, transcribed, out_dir, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(transcribed), "--preset", "vogon", "--out", str(out_dir)])

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "vogon" in err and "netflix" in err
    assert _names(out_dir) == []


# --- --all and --folder ---------------------------------------------------------------


def test_all_exports_every_media_with_a_transcript_and_skips_the_rest_with_a_message(conn, three, out_dir, capsys):
    trashed = seed_media(conn, title="Binned")
    seed_run(conn, trashed)
    with db.LOCK:
        conn.execute("UPDATE media SET trashed_at=? WHERE id=?", (time.time(), trashed))
        conn.commit()

    code = cli.main(["--all", "--format", "srt", "--format", "txt", "--out", str(out_dir)])

    assert code == 0
    assert _names(out_dir) == ["Alpha.srt", "Alpha.txt", "Beta.srt", "Beta.txt"]
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        str(out_dir / "Alpha.srt"), str(out_dir / "Alpha.txt"),
        str(out_dir / "Beta.srt"), str(out_dir / "Beta.txt"),
    ]
    assert "Fresh" in captured.err and "no transcript" in captured.err
    assert "Binned" not in captured.err  # the trash is left out without a word
    alpha = export_doc.load(conn, three["alpha"])
    assert (out_dir / "Alpha.srt").read_bytes() == srt.write(alpha, ExportOptions(formats=["srt", "txt"]))
    assert b"Arthur" in (out_dir / "Alpha.srt").read_bytes()
    assert b"Arthur" not in (out_dir / "Beta.srt").read_bytes()


def test_all_with_nothing_to_export_exits_1_with_the_reason(conn, out_dir, capsys):
    seed_media(conn, title="Fresh")

    assert cli.main(["--all", "--out", str(out_dir)]) == 1

    err = capsys.readouterr().err
    assert "Fresh" in err and "no transcript" in err
    assert _names(out_dir) == []


def test_folder_exports_the_media_in_that_folder(conn, three, out_dir, capsys):
    with db.LOCK:
        cur = conn.execute("INSERT INTO folder(name) VALUES ('Talks')")
        folder_id = cur.lastrowid
        conn.execute("UPDATE media SET folder_id=? WHERE id=?", (folder_id, three["beta"]))
        conn.commit()

    code = cli.main(["--folder", str(folder_id), "--format", "md", "--out", str(out_dir)])

    assert code == 0
    assert _names(out_dir) == ["Beta.md"]
    assert capsys.readouterr().out.splitlines() == [str(out_dir / "Beta.md")]

    assert cli.main(["--folder", "9999", "--out", str(out_dir)]) == 1
    assert "9999" in capsys.readouterr().err


def test_all_gives_clashing_titles_distinct_filenames(conn, out_dir):
    first = seed_media(conn, title="Same")
    seed_run(conn, first)
    second = seed_media(conn, title="Same")
    seed_run(conn, second, words=[{"start": 0.0, "end": 0.4, "text": " Different."}])

    assert cli.main(["--all", "--out", str(out_dir)]) == 0

    assert _names(out_dir) == ["Same (2).srt", "Same.srt"]
    assert b"Different." in (out_dir / "Same (2).srt").read_bytes()


# --- refusals ----------------------------------------------------------------------------


def test_a_bad_format_exits_2_with_usage(conn, transcribed, out_dir, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(transcribed), "--format", "pdf", "--out", str(out_dir)])

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage:")
    assert "pdf" in err
    assert _names(out_dir) == []


def test_a_bad_option_value_exits_2_with_usage(conn, transcribed, out_dir, capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(transcribed), "--cpl", "5", "--out", str(out_dir)])

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage:") and "cpl" in err

    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(transcribed), "--timestamps", "hourly", "--out", str(out_dir)])
    assert excinfo.value.code == 2
    assert _names(out_dir) == []


def test_exactly_one_target_is_required(conn, transcribed, out_dir, capsys):
    for argv in ([], ["--all", str(transcribed)], ["--all", "--folder", "1"]):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv + ["--out", str(out_dir)])
        assert excinfo.value.code == 2, argv
        assert "usage:" in capsys.readouterr().err
    assert _names(out_dir) == []


def test_a_media_without_a_transcript_exits_1_with_the_reason_and_writes_nothing(conn, three, out_dir, capsys):
    code = cli.main([str(three["alpha"]), str(three["fresh"]), "--out", str(out_dir)])

    assert code == 1
    err = capsys.readouterr().err
    assert "Fresh" in err and "no transcript" in err
    assert _names(out_dir) == []  # the other id is not written either: all or nothing

    assert cli.main(["9999", "--out", str(out_dir)]) == 1
    assert "9999" in capsys.readouterr().err


def test_a_missing_database_exits_1_and_creates_nothing(data_dir, out_dir, capsys):
    assert not paths.DB_PATH.exists()

    assert cli.main(["1", "--out", str(out_dir)]) == 1

    assert str(paths.DB_PATH) in capsys.readouterr().err
    assert not paths.DB_PATH.exists()
    assert _names(out_dir) == []


def test_a_character_the_encoding_cannot_hold_exits_1_naming_it(conn, out_dir, capsys):
    media_id = seed_media(conn, title="Greek")
    seed_run(conn, media_id, words=[{"start": 0.0, "end": 0.4, "text": " Ωμέγα."}])

    assert cli.main([str(media_id), "--format", "txt", "--encoding", "cp1252", "--out", str(out_dir)]) == 1

    assert "cp1252" in capsys.readouterr().err
    assert _names(out_dir) == []


@pytest.fixture
def ascii_and_greek(conn):
    """One media the cp1252 code page can hold and one it cannot."""
    plain = seed_media(conn, title="Plain")
    seed_run(conn, plain, words=[{"start": 0.0, "end": 0.4, "text": " Fine."}])
    greek = seed_media(conn, title="Greek")
    seed_run(conn, greek, words=[{"start": 0.0, "end": 0.4, "text": " Ωμέγα."}])
    return {"plain": plain, "greek": greek}


def test_named_ids_are_all_or_nothing_when_a_later_one_cannot_be_encoded(conn, ascii_and_greek, out_dir, capsys):
    ids = [str(ascii_and_greek["plain"]), str(ascii_and_greek["greek"])]

    code = cli.main(ids + ["--format", "txt", "--encoding", "cp1252", "--out", str(out_dir)])

    assert code == 1
    err = capsys.readouterr().err
    assert "Greek" in err and "cp1252" in err
    assert _names(out_dir) == []  # the first id is not written either: all or nothing


def test_all_skips_a_media_the_encoding_cannot_hold_and_goes_on(conn, ascii_and_greek, out_dir, capsys):
    code = cli.main(["--all", "--format", "txt", "--encoding", "cp1252", "--out", str(out_dir)])

    assert code == 0
    assert _names(out_dir) == ["Plain.txt"]
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [str(out_dir / "Plain.txt")]
    assert captured.err.startswith("skipped:")
    assert "Greek" in captured.err and "cp1252" in captured.err


def test_all_with_nothing_the_encoding_can_hold_exits_1(conn, out_dir, capsys):
    greek = seed_media(conn, title="Greek")
    seed_run(conn, greek, words=[{"start": 0.0, "end": 0.4, "text": " Ωμέγα."}])

    code = cli.main(["--all", "--format", "txt", "--encoding", "cp1252", "--out", str(out_dir)])

    assert code == 1
    err = capsys.readouterr().err
    assert err.startswith("skipped:") and "Greek" in err
    assert "nothing" in err.splitlines()[-1]
    assert _names(out_dir) == []


# --- the same export as the dialog -----------------------------------------------------------


def test_the_html_bundle_carries_the_recording_like_the_dialog_does(conn, transcribed, out_dir):
    audio = b"RIFF" + bytes(range(256)) * 8
    _store(conn, transcribed, audio)
    doc = export_doc.load(conn, transcribed)

    assert cli.main([str(transcribed), "--format", "html", "--out", str(out_dir)]) == 0

    assert _names(out_dir) == ["Guide.html"]
    expected = html_bundle.write(doc, ExportOptions(formats=["html"]), audio=audio, audio_mime="audio/wav")
    assert (out_dir / "Guide.html").read_bytes() == expected
    assert b"data:audio/wav;base64," in expected


def test_a_subtitle_export_of_an_unplayable_container_never_transcodes_it(conn, transcribed, out_dir, monkeypatch):
    """--all --format srt over a video library must not run ffmpeg once: only
    the HTML bundle takes the recording."""
    row = conn.execute("SELECT store_path FROM media WHERE id=?", (transcribed,)).fetchone()
    with db.LOCK:
        conn.execute(
            "UPDATE media SET store_path=?, orig_name='Guide.mkv' WHERE id=?",
            (row["store_path"][:-4] + ".mkv", transcribed),
        )
        conn.commit()
    _store(conn, transcribed, b"\x1aE\xdf\xa3" + bytes(64))
    calls = []

    def fake(original, proxy):
        calls.append((original, proxy))
        proxy.parent.mkdir(parents=True, exist_ok=True)
        proxy.write_bytes(b"proxy")

    monkeypatch.setattr(playback, "ensure_proxy", fake)

    assert cli.main(["--all", "--format", "srt", "--out", str(out_dir)]) == 0
    assert calls == []

    assert cli.main([str(transcribed), "--format", "html", "--out", str(out_dir)]) == 0
    assert len(calls) == 1
    assert b"data:audio/mp4;base64," in (out_dir / "Guide.html").read_bytes()


def test_the_cli_writes_nothing_to_the_database(conn, transcribed, out_dir, monkeypatch):
    """ADR-003: the exporters read word, segment and speaker_label and never write."""
    real_connect = db.connect
    spies = []

    class Spy:
        def __init__(self, inner):
            self.inner = inner
            self.changes_at_close = None

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def close(self):
            self.changes_at_close = self.inner.total_changes
            self.inner.close()

    def connect(path=None):
        spy = Spy(real_connect(path))
        spies.append(spy)
        return spy

    monkeypatch.setattr(db, "connect", connect)

    assert cli.main([str(transcribed), "--format", "srt", "--format", "json", "--out", str(out_dir)]) == 0

    assert len(spies) == 1
    assert spies[0].changes_at_close == 0
    assert _names(out_dir) == ["Guide.json", "Guide.srt"]


def test_the_module_entry_point_runs_the_cli(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "scribe.export", "--help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "SCRIBE_DATA_DIR": str(tmp_path / "data")},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("usage: python -m scribe.export")
    for flag in ("--all", "--folder", "--format", "--preset", "--out", "--cpl", "--no-speakers"):
        assert flag in proc.stdout, flag
    assert not (tmp_path / "data").exists()  # --help touches nothing
