"""ffprobe as the gate (Phase 2 Task 2).

probe is the stage that decides whether a file is media at all, so these
tests care most about the two ways it says no - a file ffprobe cannot open,
and a file it opens happily but that carries no audio - and about the one
fact the rest of the pipeline reads back out of it: `media.duration`.

The parsing is tested separately from the subprocess (`summarize`) because
containers that omit a format-level duration, or write "N/A" where a number
belongs, are real and awkward to synthesise on disk.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from scribe import db, jobs, media, paths, runner
from scribe.stages import probe

FIXTURES = Path(__file__).parent / "fixtures"
CLIP = FIXTURES / "clip30.wav"


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Tmp DB that runner.main() also finds via the default paths.DB_PATH."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    (data / "media").mkdir(parents=True)
    return data


@pytest.fixture(scope="session")
def video_only(tmp_path_factory):
    """A real container with a video stream and no audio at all.

    The text-file case exercises ffprobe refusing to open a file; this one
    exercises ffprobe succeeding on something we still cannot transcribe.
    """
    path = tmp_path_factory.mktemp("silent") / "no-audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=1",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def make_ctx(conn, job_id, media_path, progress=None):
    """A RunnerContext as the runner would build it, minus the throttle."""
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    return runner.RunnerContext(
        conn=conn,
        job=job,
        params=json.loads(job["params_json"] or "{}"),
        report=(lambda p: None) if progress is None else progress.append,
        cancelled=lambda: False,
        media_path=media_path,
    )


def enqueue_for(conn, media_row):
    return jobs.enqueue(conn, "transcribe", media_id=media_row["id"])


# --- probe_media -------------------------------------------------------------


def test_probe_reads_duration_and_the_audio_stream():
    info = probe.probe_media(CLIP)

    assert info["duration"] == pytest.approx(30.0, abs=0.2)
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert len(audio) == 1
    assert audio[0]["codec_name"] == "pcm_s16le"
    assert audio[0]["channels"] == 1
    assert audio[0]["sample_rate"] == 16000  # a number, not ffprobe's string


def test_probe_reports_the_container_format_chapters_and_tags():
    info = probe.probe_media(CLIP)

    assert "wav" in info["format_name"]
    assert info["chapters"] == []
    assert isinstance(info["tags"], dict)


def test_probe_rejects_a_file_that_is_not_media(tmp_path):
    not_media = tmp_path / "shopping-list.txt"
    not_media.write_text("milk\ntowels\n", encoding="utf-8")

    with pytest.raises(probe.NotMediaError) as exc:
        probe.probe_media(not_media)

    assert "Invalid data" in str(exc.value)  # ffprobe's own words, not ours


def test_probe_rejects_a_container_with_no_audio_stream(video_only):
    with pytest.raises(probe.NotMediaError) as exc:
        probe.probe_media(video_only)

    assert "audio" in str(exc.value).lower()


def test_probe_says_missing_rather_than_not_media_for_an_absent_file(tmp_path):
    # A file that is gone is a different problem from a file that is junk,
    # and the user needs to be told which one it is.
    with pytest.raises(FileNotFoundError):
        probe.probe_media(tmp_path / "never-existed.wav")


# --- summarize (parsing, no subprocess) --------------------------------------


def _payload(streams, fmt=None, chapters=None):
    return {"streams": streams, "format": fmt or {}, "chapters": chapters or []}


def test_summarize_falls_back_to_the_longest_stream_duration():
    info = probe.summarize(
        _payload(
            [
                {"index": 0, "codec_type": "audio", "duration": "12.5"},
                {"index": 1, "codec_type": "video", "duration": "13.25"},
            ],
            fmt={"format_name": "matroska"},  # no format-level duration
        )
    )

    assert info["duration"] == pytest.approx(13.25)


def test_summarize_treats_unparseable_numbers_as_unknown():
    info = probe.summarize(
        _payload(
            [{"index": 0, "codec_type": "audio", "sample_rate": "N/A", "channels": "N/A"}],
            fmt={"duration": "N/A"},
        )
    )

    assert info["duration"] is None
    assert info["streams"][0]["sample_rate"] is None
    assert info["streams"][0]["channels"] is None


def test_summarize_refuses_a_payload_without_an_audio_stream():
    with pytest.raises(probe.NotMediaError):
        probe.summarize(_payload([{"index": 0, "codec_type": "video"}]))


# --- the stage ---------------------------------------------------------------


def test_stage_writes_duration_onto_the_media_row(conn, data_dir):
    row = media.ingest_path(conn, CLIP)
    assert row["duration"] is None
    job_id = enqueue_for(conn, row)

    probe.run(make_ctx(conn, job_id, data_dir / row["store_path"]))

    duration = conn.execute(
        "SELECT duration FROM media WHERE id=?", (row["id"],)
    ).fetchone()["duration"]
    assert duration == pytest.approx(30.0, abs=0.2)


def test_stage_emits_a_probe_event_carrying_the_summary(conn, data_dir):
    row = media.ingest_path(conn, CLIP)
    job_id = enqueue_for(conn, row)

    probe.run(make_ctx(conn, job_id, data_dir / row["store_path"]))

    probes = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "probe"]
    assert len(probes) == 1
    payload = probes[0]["payload"]
    assert payload["duration"] == pytest.approx(30.0, abs=0.2)
    assert "wav" in payload["format_name"]
    assert payload["streams"][0]["codec_type"] == "audio"


def test_stage_reports_itself_complete(conn, data_dir):
    row = media.ingest_path(conn, CLIP)
    job_id = enqueue_for(conn, row)
    progress: list[float] = []

    probe.run(make_ctx(conn, job_id, data_dir / row["store_path"], progress))

    assert progress[-1] == 1.0
    assert progress == sorted(progress)


def test_stage_without_a_media_path_refuses_to_guess(conn):
    job_id = jobs.enqueue(conn, "transcribe")

    with pytest.raises(RuntimeError):
        probe.run(make_ctx(conn, job_id, None))


# --- runner wiring -----------------------------------------------------------


def test_transcribe_job_type_is_registered_and_starts_with_probe():
    stages = runner.STAGES["transcribe"]
    assert stages[0][0] == "probe"
    assert stages[0][1] is probe.run
    assert all(callable(fn) for _, fn in stages)


def test_not_media_is_reported_as_an_ffmpeg_decode_failure():
    assert runner._error_code(probe.NotMediaError("no audio")) == "FFMPEG_DECODE"


def test_runner_hands_the_stage_the_stored_media_path(conn, data_dir, monkeypatch):
    row = media.ingest_path(conn, CLIP)
    seen: dict = {}
    monkeypatch.setitem(
        runner.STAGES, "transcribe", [("probe", lambda ctx: seen.update(p=ctx.media_path))]
    )
    job_id = enqueue_for(conn, row)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert seen["p"] == data_dir / row["store_path"]
    assert seen["p"].is_file()


def test_runner_leaves_media_path_none_for_a_job_without_media(conn, monkeypatch):
    seen: dict = {}
    monkeypatch.setitem(
        runner.STAGES, "transcribe", [("probe", lambda ctx: seen.update(p=ctx.media_path))]
    )
    job_id = jobs.enqueue(conn, "transcribe")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert seen["p"] is None


def test_the_registered_probe_stage_lands_duration_through_the_runner(
    conn, data_dir, monkeypatch
):
    """The real registry entry, driven by the real runner, on a real clip.

    Sliced to the probe entry so it stays this cheap as later stages join the
    pipeline; the whole-pipeline run is Task 7's e2e test. Note that job
    stage_progress is deliberately not asserted: the runner's 0.4 s report
    throttle swallows an instant stage's closing 1.0, and the next stage's
    opening 0.0 corrects it.
    """
    row = media.ingest_path(conn, CLIP)
    monkeypatch.setitem(runner.STAGES, "transcribe", runner.STAGES["transcribe"][:1])
    job_id = enqueue_for(conn, row)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "done"
    duration = conn.execute(
        "SELECT duration FROM media WHERE id=?", (row["id"],)
    ).fetchone()["duration"]
    assert duration == pytest.approx(30.0, abs=0.2)
    assert [e["kind"] for e in jobs.events_after(conn, job_id, 0)].count("probe") == 1


def test_a_non_media_upload_fails_the_whole_job_with_ffmpeg_decode(
    conn, data_dir, tmp_path
):
    junk = tmp_path / "notes.txt"
    junk.write_text("this was never a recording", encoding="utf-8")
    row = media.ingest_path(conn, junk)
    job_id = enqueue_for(conn, row)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert job["error_code"] == "FFMPEG_DECODE"
    assert job["error_detail"]


# --- loudness, and the silence that fails a job -----------------------------------------


def _lavfi_wav(tmp_path_factory, name: str, source: str, seconds: float = 2.0) -> Path:
    """A real WAV from one of ffmpeg's generators: `anullsrc` is digital
    silence, `sine` a tone, `sine` through `volume` a quiet one."""
    path = tmp_path_factory.mktemp("loud") / name
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"{source}:duration={seconds:g}",
            "-ar", "16000", "-ac", "1", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(scope="session")
def silent_wav(tmp_path_factory):
    return _lavfi_wav(tmp_path_factory, "silent.wav", "anullsrc=r=16000:cl=mono")


@pytest.fixture(scope="session")
def tone_wav(tmp_path_factory):
    return _lavfi_wav(tmp_path_factory, "tone.wav", "sine=frequency=440:sample_rate=16000")


@pytest.fixture(scope="session")
def quiet_wav(tmp_path_factory):
    """A real signal at about -48 dBFS: quiet, and not silence. ffmpeg's `sine`
    generates at about -18 dBFS (measured, not assumed - the first version of
    this test believed a full-scale sine and was wrong), and -30 dB under that
    is well clear of both the tone and the -60 dBFS line."""
    path = tmp_path_factory.mktemp("loud") / "quiet.wav"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=2",
            "-af", "volume=-30dB", "-ar", "16000", "-ac", "1", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_parse_volumedetect_reads_both_levels_and_tolerates_their_absence():
    report = (
        "[Parsed_volumedetect_0 @ 0x1] n_samples: 400008\n"
        "[Parsed_volumedetect_0 @ 0x1] mean_volume: -91.0 dB\n"
        "[Parsed_volumedetect_0 @ 0x1] max_volume: -91.0 dB\n"
        "[Parsed_volumedetect_0 @ 0x1] histogram_91db: 400008\n"
    )
    assert probe.parse_volumedetect(report) == (-91.0, -91.0)
    assert probe.parse_volumedetect("mean_volume: -18.5 dB\nmax_volume: 0.0 dB") == (-18.5, 0.0)
    # ffmpeg decoded nothing it could measure: no lines, and no numbers - never
    # a zero, because a reading that did not happen is not a reading of silence.
    assert probe.parse_volumedetect("[out#0/null] video:0KiB audio:0KiB") == (None, None)


def test_measure_loudness_hears_silence_a_tone_and_a_quiet_signal(silent_wav, tone_wav, quiet_wav):
    silent = probe.measure_loudness(silent_wav)
    tone = probe.measure_loudness(tone_wav)
    quiet = probe.measure_loudness(quiet_wav)

    # Relations, not this ffmpeg build's numbers: the sine's amplitude is the
    # generator's choice (measured -18 dBFS here), and `quiet` is defined as
    # 30 dB under whatever that is.
    assert silent["max_db"] < probe.SILENT_PEAK_DB < quiet["max_db"] < tone["max_db"]
    assert abs((tone["max_db"] - quiet["max_db"]) - 30) < 3
    assert quiet["max_db"] - probe.SILENT_PEAK_DB > 6, "quiet sits too close to the threshold"
    assert silent["scanned_seconds"] == probe.SILENCE_SCAN_SECONDS


def test_the_stage_refuses_a_silent_file_with_its_own_error_and_says_why(
    conn, data_dir, silent_wav
):
    """The recording of 2026-09-05: 8.3 s at -91 dBFS, transcribed to nothing,
    reported `done`. Now it stops here, with the number and the remedy."""
    row = media.ingest_path(conn, silent_wav)
    job_id = enqueue_for(conn, row)

    with pytest.raises(probe.SilentAudioError) as caught:
        probe.run(make_ctx(conn, job_id, data_dir / row["store_path"]))

    message = str(caught.value)
    assert "no sound" in message
    assert re.search(r"peak -\d+ dBFS", message)
    assert "input device" in message
    # The measurement is on the record either way.
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM job_event WHERE job_id=?", (job_id,))]
    assert "loudness" in kinds


def test_the_stage_passes_a_tone_and_a_quiet_but_real_recording(conn, data_dir, tone_wav, quiet_wav):
    for path in (tone_wav, quiet_wav):
        row = media.ingest_path(conn, path)
        job_id = enqueue_for(conn, row)
        progress: list[float] = []

        probe.run(make_ctx(conn, job_id, data_dir / row["store_path"], progress))

        assert progress == [1.0]
        loud = conn.execute(
            "SELECT payload_json FROM job_event WHERE job_id=? AND kind='loudness'", (job_id,)
        ).fetchall()
        assert len(loud) == 1 and json.loads(loud[0]["payload_json"])["max_db"] > probe.SILENT_PEAK_DB


def test_silent_audio_has_its_own_error_code():
    assert runner._error_code(probe.SilentAudioError("x")) == "SILENT_AUDIO"
    # And it is not the file's fault, so not the file's code.
    assert runner._error_code(probe.NotMediaError("x")) == "FFMPEG_DECODE"


def test_a_silent_upload_fails_the_whole_job_with_silent_audio(conn, data_dir, silent_wav):
    row = media.ingest_path(conn, silent_wav)
    job_id = enqueue_for(conn, row)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert job["error_code"] == "SILENT_AUDIO"
    assert "Check the input device" in job["error_detail"]


def test_a_loudness_pass_that_times_out_is_not_measured_rather_than_a_failure(
    conn, data_dir, tone_wav, monkeypatch
):
    """CR-024. A huge video container on a slow volume is minutes of I/O even
    with -vn; the old code let TimeoutExpired escape as a generic RUNTIME."""
    monkeypatch.setattr(probe, "LOUDNESS_TIMEOUT", 0.001)

    loud = probe.measure_loudness(tone_wav)

    assert loud["max_db"] is None and loud["mean_db"] is None
    assert loud.get("timed_out") is True

    row = media.ingest_path(conn, tone_wav)
    job_id = enqueue_for(conn, row)
    probe.run(make_ctx(conn, job_id, data_dir / row["store_path"]))  # no raise
