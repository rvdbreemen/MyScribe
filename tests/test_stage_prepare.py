"""ffmpeg normalisation with honest progress (Phase 2 Task 3).

Three things are worth testing here and they need three different kinds of
test:

* **The parser** turns ffmpeg's `-progress` key/value stream into a fraction.
  Tested against text captured verbatim from a real run, because the awkward
  cases - `N/A` before the first frame, a stall, a container that overruns its
  advertised duration - are far easier to hand ffmpeg than to provoke from it.
* **The conversion** has to produce mono 16 kHz PCM, has to report the
  position ffmpeg actually reached, and has to report it while ffmpeg is still
  running. The first two against the real clip and a 30-minute loop of it; the
  third against a stand-in ffmpeg that refuses to finish until it has been
  heard, because a pipe read live and a pipe read after the child exits give
  identical numbers and no assertion about values can separate them.
* **The stage** has to land the file in the job's work directory, hand its
  path to the next stage through `ctx.state`, and clear up after itself when
  the conversion fails.

Wall-clock caveat, deliberately not asserted anywhere: ffmpeg emits a progress
block every ~0.5 s of *wall* time, so how many blocks a conversion produces
depends on how fast the machine is. The tests assert the shape of the progress
(non-decreasing, bounded, complete at the end), never a block count. Which is
why the conversions here are told the file is twice as long as it is: the
closing block then has to arrive as 0.5 whoever runs the suite, and a
conversion that reported nothing at all stops looking like a finished one.
"""

import json
import subprocess
import sys
from pathlib import Path

import pathlib

import pytest

from scribe import db, jobs, media, paths, runner
from scribe.stages import prepare, probe

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
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    (data / "media").mkdir(parents=True)
    return data


@pytest.fixture(scope="session")
def long_clip(tmp_path_factory):
    """Half an hour of audio, built by looping the fixture with a stream copy.

    Long enough that the conversion takes real wall time, so ffmpeg writes at
    least one `progress=continue` block rather than only its closing one - on
    this machine. How many, or whether any, depends on how fast the machine is,
    which is why the test that uses this never counts them. Stream-copied
    rather than re-encoded, so making it costs a third of a second.
    """
    path = tmp_path_factory.mktemp("long") / "half-hour.wav"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-stream_loop", "59", "-i", str(CLIP), "-c", "copy", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


# A stand-in for the ffmpeg binary, run as a real child process by the fixture
# below. It reports a position and then refuses to finish until somebody has
# acted on it - which is the whole point: see the test that uses it.
STUB_FFMPEG = '''\
import os
import sys
import time

handshake = os.environ["SCRIBE_TEST_HANDSHAKE"]
print("out_time_us=15000000")
print("progress=continue", flush=True)

deadline = time.monotonic() + 3.0
while not os.path.exists(handshake) and time.monotonic() < deadline:
    time.sleep(0.01)
if not os.path.exists(handshake):
    sys.exit("nobody heard this progress while ffmpeg was still running")

open(sys.argv[-1], "wb").close()
print("out_time_us=60000000")
print("progress=end", flush=True)
'''


@pytest.fixture
def ffmpeg_that_waits_to_be_heard(tmp_path, monkeypatch):
    """Run the stub above in ffmpeg's place; yields the file that unblocks it.

    Only the program is swapped - the child process, the pipe, the reading and
    the parsing are all the real ones. The stub takes its output path from the
    end of the command line and looks at nothing else, so this fixture cannot
    turn into an assertion about how the command line is spelled; that is what
    the conversion tests against the real ffmpeg are for.
    """
    stub = tmp_path / "stub_ffmpeg.py"
    stub.write_text(STUB_FFMPEG, encoding="utf-8")
    handshake = tmp_path / "heard"
    monkeypatch.setenv("SCRIBE_TEST_HANDSHAKE", str(handshake))

    real_popen = subprocess.Popen

    def run_the_stub(argv, **kwargs):
        return real_popen([sys.executable, str(stub), *argv[1:]], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", run_the_stub)
    return handshake


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


def ingest_clip(conn, duration=30.0, src=CLIP):
    """A media row for the clip, with probe's duration already on it."""
    row = media.ingest_path(conn, src)
    with db.LOCK:
        conn.execute("UPDATE media SET duration=? WHERE id=?", (duration, row["id"]))
        conn.commit()
    return row


def block(out_time_us):
    """One ffmpeg -progress block, in the exact shape ffmpeg 8 writes it.

    Copied from a real run: note that `out_time_ms` also carries microseconds
    (an ffmpeg quirk older than most of its users), which is why the parser
    keys on `out_time_us` alone.
    """
    return [
        "bitrate= 256.0kbits/s",
        "total_size=960078",
        f"out_time_us={out_time_us}",
        f"out_time_ms={out_time_us}",
        "out_time=00:00:30.000000",
        "dup_frames=0",
        "drop_frames=0",
        "speed=1.58e+03x",
        "progress=continue",
    ]


# --- progress_fractions (parsing, no subprocess) ------------------------------


def test_progress_fractions_maps_out_time_onto_the_duration():
    lines = block(15_000_000) + block(30_000_000) + block(60_000_000)

    assert list(prepare.progress_fractions(lines, 60.0)) == [0.25, 0.5, 1.0]


def test_progress_fractions_clamps_an_overrunning_file_to_one():
    # A container whose advertised duration is short of what it decodes; the
    # progress bar must finish, not run past the end.
    assert list(prepare.progress_fractions(block(31_000_000), 30.0)) == [1.0]


def test_progress_fractions_never_goes_backwards():
    lines = block(20_000_000) + block(10_000_000) + block(30_000_000)

    assert list(prepare.progress_fractions(lines, 30.0)) == [pytest.approx(2 / 3), 1.0]


def test_progress_fractions_repeats_a_stalled_position():
    # ffmpeg writes a block every half second whether or not it got anywhere,
    # so a stall arrives as a repeat rather than a decrease - and a repeated
    # position is still a true one, so it is reported again rather than
    # swallowed. (The runner throttles what reaches the database anyway.)
    lines = block(15_000_000) + block(15_000_000) + block(30_000_000)

    assert list(prepare.progress_fractions(lines, 30.0)) == [0.5, 0.5, 1.0]


def test_progress_fractions_will_not_take_a_position_from_out_time_ms():
    # The parser's contract is out_time_us and nothing else. On the build
    # measured here the two keys carry the same microsecond value, so today
    # they are interchangeable by accident; only one of them is named after
    # the unit it actually holds. Reading the other one back is the mistake
    # this test exists to stop.
    lines = ["out_time_ms=15000000", "out_time=00:00:15.000000"]

    assert list(prepare.progress_fractions(lines, 30.0)) == []


def test_progress_fractions_ignores_values_ffmpeg_does_not_know_yet():
    # "N/A" before the first frame is decoded, and the int64 sentinel some
    # builds write instead - neither is a position in the file.
    lines = ["out_time_us=N/A"] + ["out_time_us=-9223372036854775808"] + block(30_000_000)

    assert list(prepare.progress_fractions(lines, 30.0)) == [1.0]


def test_progress_fractions_reads_every_line_even_with_nothing_to_report():
    # ffmpeg blocks forever if its stdout pipe fills, so the parser must drain
    # the stream whatever it makes of the contents.
    lines = iter(block(10_000_000))

    assert list(prepare.progress_fractions(lines, 0.0)) == []
    assert next(lines, None) is None


def test_progress_fractions_stays_silent_when_the_duration_is_unknown():
    # An unmeasurable stage reports 0 and then 1 (plan: never fake progress),
    # so the parser invents nothing in between.
    assert list(prepare.progress_fractions(block(10_000_000), 0.0)) == []


# --- to_wav (the real subprocess) --------------------------------------------


def test_to_wav_produces_mono_16k_signed_pcm(tmp_path):
    dst = tmp_path / "audio.wav"

    result = prepare.to_wav(CLIP, dst, 30.0, lambda p: None)

    assert result == dst
    info = probe.probe_media(dst)
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert len(audio) == 1
    assert audio[0]["sample_rate"] == 16000
    assert audio[0]["channels"] == 1
    assert audio[0]["codec_name"] == "pcm_s16le"
    assert info["duration"] == pytest.approx(30.0, abs=0.2)


def test_to_wav_maps_the_position_ffmpeg_reports_onto_the_duration(tmp_path):
    """The clip, converted against a duration twice its real length.

    Doubling the duration is what makes ffmpeg's own numbers visible: the clip
    converts in a fifth of a second, far too fast for anything but the closing
    `progress=end` block, and against its true duration that block reports the
    end of the file - 1.0, which is also the value `to_wav` appends when it is
    finished. Told the file is 60 s long, the same block has to come through as
    0.5. Assert only the closing 1.0 and an implementation that parsed nothing
    at all would look identical.
    """
    seen: list[float] = []

    prepare.to_wav(CLIP, tmp_path / "audio.wav", 60.0, seen.append)

    # Approximately, not exactly: this is ffmpeg's own reported position, and
    # builds disagree about it in the third decimal - the clip came through as
    # 29.952 s on ubuntu's ffmpeg 6.1.1 (0.4992) and as 30.0 here. What the
    # test is for survives either way: the block was parsed and scaled against
    # the claimed duration, rather than being the 1.0 that `to_wav` appends.
    assert seen[-2] == pytest.approx(0.5, abs=0.01)  # 30 s of media, 60 s claimed
    assert seen[-1] == 1.0
    assert seen == sorted(seen)
    assert all(0.0 <= p <= 1.0 for p in seen)


def test_to_wav_reports_the_same_way_on_a_conversion_of_real_length(
    tmp_path, long_clip
):
    """Half an hour of audio, again against a doubled duration.

    Why the fixture is worth its third of a second: this is the one conversion
    here long enough that ffmpeg is likely to write a `progress=continue` block
    rather than only its closing one, which is the only way the parser ever
    meets a block a real encoder produced instead of one this file wrote.
    Likely, not guaranteed - on a fast enough machine the conversion beats
    ffmpeg's half-second reporting interval and only the closing block arrives.
    So nothing here counts blocks or requires one. What is asserted holds
    either way: the fractions stay ordered and in range, and the last position
    ffmpeg reports is still the true end of the file.
    """
    seen: list[float] = []

    prepare.to_wav(long_clip, tmp_path / "audio.wav", 3600.0, seen.append)

    assert seen[-2] == pytest.approx(0.5, abs=0.01)  # 1800 s of 3600 s claimed
    assert seen[-1] == 1.0
    assert seen == sorted(seen)
    assert all(0.0 <= p <= 1.0 for p in seen)


def test_to_wav_reports_progress_while_ffmpeg_is_still_running(
    tmp_path, ffmpeg_that_waits_to_be_heard
):
    """The one property no assertion about the values can reach.

    A pipe read as it fills and a pipe slurped after the process exits produce
    exactly the same list of fractions, so none of the tests above can tell
    them apart - and after the fact is precisely when a four-hour conversion's
    progress is worthless. So the stand-in ffmpeg holds the conversion open
    until the callback has run: an implementation that waited for the child
    before reading its output would deadlock its own ffmpeg and come back with
    a failure instead of a file.
    """
    seen: list[float] = []

    def hear(fraction: float) -> None:
        seen.append(fraction)
        ffmpeg_that_waits_to_be_heard.touch()  # tell ffmpeg it may finish

    prepare.to_wav(CLIP, tmp_path / "audio.wav", 60.0, hear)

    # 15 s heard while it ran, then the end of the file, then to_wav's own
    # closing report.
    assert seen == [0.25, 1.0, 1.0]


def test_to_wav_overwrites_a_leftover_file_from_an_earlier_attempt(tmp_path):
    # -nostdin plus an existing output is how ffmpeg hangs its own overwrite
    # prompt; a retried job must not trip over the file its last try left.
    dst = tmp_path / "audio.wav"
    dst.write_bytes(b"stale")

    prepare.to_wav(CLIP, dst, 30.0, lambda p: None)

    assert probe.probe_media(dst)["duration"] == pytest.approx(30.0, abs=0.2)


def test_to_wav_refuses_a_file_ffmpeg_cannot_decode_and_quotes_it(tmp_path):
    junk = tmp_path / "shopping-list.txt"
    junk.write_text("milk\ntowels\n", encoding="utf-8")

    with pytest.raises(prepare.ConversionError) as exc:
        prepare.to_wav(junk, tmp_path / "audio.wav", 30.0, lambda p: None)

    assert "Invalid data" in str(exc.value)  # ffmpeg's own words, not ours


def test_to_wav_leaves_no_half_written_output_behind_when_it_fails(tmp_path):
    junk = tmp_path / "shopping-list.txt"
    junk.write_text("milk\ntowels\n", encoding="utf-8")
    dst = tmp_path / "audio.wav"

    with pytest.raises(prepare.ConversionError):
        prepare.to_wav(junk, dst, 30.0, lambda p: None)

    assert not dst.exists()


def test_to_wav_keeps_no_log_file_next_to_a_conversion_that_worked(tmp_path):
    dst = tmp_path / "audio.wav"

    prepare.to_wav(CLIP, dst, 30.0, lambda p: None)

    assert [p.name for p in tmp_path.iterdir()] == ["audio.wav"]


# --- the stage ---------------------------------------------------------------


def test_stage_writes_the_wav_into_this_job_work_directory(conn, data_dir):
    row = ingest_clip(conn)
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"])

    prepare.run(make_ctx(conn, job_id, data_dir / row["store_path"]))

    wav = paths.job_work_dir(job_id) / "audio.wav"
    assert wav.is_file()
    assert probe.probe_media(wav)["streams"][0]["sample_rate"] == 16000


def test_stage_hands_the_wav_path_to_the_next_stage_on_ctx_state(conn, data_dir):
    row = ingest_clip(conn)
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"])
    ctx = make_ctx(conn, job_id, data_dir / row["store_path"])

    prepare.run(ctx)

    assert ctx.state["wav"] == paths.job_work_dir(job_id) / "audio.wav"
    assert ctx.state["wav"].is_file()


def test_stage_reports_progress_that_ends_complete(conn, data_dir):
    row = ingest_clip(conn)
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"])
    progress: list[float] = []

    prepare.run(make_ctx(conn, job_id, data_dir / row["store_path"], progress))

    assert progress == sorted(progress)
    assert progress[-1] == 1.0


def test_stage_still_converts_a_media_row_with_no_known_duration(conn, data_dir):
    # Some containers tell ffprobe nothing about their length. The conversion
    # must still happen; only the progress in between is unavailable.
    row = ingest_clip(conn, duration=None)
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"])
    progress: list[float] = []

    prepare.run(make_ctx(conn, job_id, data_dir / row["store_path"], progress))

    assert (paths.job_work_dir(job_id) / "audio.wav").is_file()
    assert progress == [1.0]


def test_stage_leaves_no_empty_work_directory_behind_when_it_fails(
    conn, data_dir, tmp_path
):
    # A job that dies here never reaches finalize, and finalize is what deletes
    # work directories. Nothing else would ever collect this one.
    junk = tmp_path / "shopping-list.txt"
    junk.write_text("milk\ntowels\n", encoding="utf-8")
    row = ingest_clip(conn, src=junk)
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"])

    with pytest.raises(prepare.ConversionError):
        prepare.run(make_ctx(conn, job_id, data_dir / row["store_path"]))

    assert not paths.job_work_dir(job_id).exists()


def test_stage_without_a_media_path_refuses_to_guess(conn, data_dir):
    job_id = jobs.enqueue(conn, "transcribe")

    with pytest.raises(RuntimeError):
        prepare.run(make_ctx(conn, job_id, None))


# --- ctx.state ---------------------------------------------------------------


def test_each_runner_context_gets_its_own_empty_state(conn):
    job_id = jobs.enqueue(conn, "transcribe")
    first = make_ctx(conn, job_id, None)
    second = make_ctx(conn, job_id, None)

    assert first.state == {}
    first.state["wav"] = "somewhere"

    assert second.state == {}  # not one dict shared by every context


def test_state_carries_from_one_stage_to_the_next(conn, monkeypatch):
    seen: dict = {}
    monkeypatch.setitem(
        runner.STAGES,
        "transcribe",
        [
            ("first", lambda ctx: ctx.state.update(wav="audio.wav")),
            ("second", lambda ctx: seen.update(ctx.state)),
        ],
    )
    job_id = jobs.enqueue(conn, "transcribe")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert seen == {"wav": "audio.wav"}


# --- runner wiring -----------------------------------------------------------


def test_prepare_follows_probe_in_the_transcribe_registry():
    names = [name for name, _ in runner.STAGES["transcribe"]]
    assert names[:2] == ["probe", "prepare"]
    assert runner.STAGES["transcribe"][1][1] is prepare.run


def test_a_conversion_failure_is_reported_as_an_ffmpeg_decode_failure():
    assert runner._error_code(prepare.ConversionError("ffmpeg said no")) == "FFMPEG_DECODE"


def test_the_registered_stages_land_a_wav_through_the_runner(
    conn, data_dir, monkeypatch
):
    """probe and prepare, driven by the real runner, on the real clip.

    Sliced to the two stages that exist so it stays this cheap as the pipeline
    grows; the whole-pipeline run is Task 7's e2e test.
    """
    row = media.ingest_path(conn, CLIP)

    # The wav is inspected from inside the job: scratch never outlives a job
    # any more (the runner removes it on every way out), so asserting on the
    # file afterwards would only ever prove a leak.
    seen = {}

    def inspect(ctx):
        wav = pathlib.Path(ctx.state["wav"])
        seen["is_file"] = wav.is_file()
        seen["sample_rate"] = probe.probe_media(wav)["streams"][0]["sample_rate"]

    monkeypatch.setitem(
        runner.STAGES,
        "transcribe",
        runner.STAGES["transcribe"][:2] + [("inspect", inspect)],
    )
    job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"])
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert conn.execute(
        "SELECT status FROM job WHERE id=?", (job_id,)
    ).fetchone()["status"] == "done"
    assert seen == {"is_file": True, "sample_rate": 16000}
    assert not paths.job_work_dir(job_id).exists()
    perf = [r["stage"] for r in conn.execute("SELECT stage FROM stage_perf ORDER BY id")]
    assert perf == ["probe", "prepare", "inspect"]
