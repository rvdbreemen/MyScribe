"""Microphone recording: the chunk store on disk (Phase 6 Task 3).

The browser half of this feature - getUserMedia, MediaRecorder, the level
meter - cannot be unit-tested from pytest, so the tests here take the plan's
alternative: real bytes. `tests/fixtures/chunk.webm` is one second of Opus in
a WebM container, produced once by

    ffmpeg -f lavfi -i "sine=frequency=440:duration=1" -c:a libopus -b:a 32k \
           -f webm pipe:1 > chunk.webm

and written *to a pipe* on purpose. That is what makes it a faithful stand-in
for a MediaRecorder chunk: a Matroska muxer writing to a seekable file goes
back and stamps the Segment duration into the header, and one writing to a
pipe cannot. `ffprobe` reports `duration=N/A` for the fixture, which is
exactly the complaint every MediaRecorder recording arrives with.

That is also why `finalize` exists in the shape it does. Two assertions carry
the whole feature:

* the fixture has no duration (asserted, not assumed - if a future ffmpeg
  starts writing one, the test that follows stops proving anything and this
  one says so first);
* the file `finalize` produces does have one.

Between those two lines sits the container fix: a stream copy into Matroska,
no re-encode, no decode, no model.

One thing did turn out to be testable without a browser. The last section of
this module drives `scribe/static/recorder.js` itself, in node, against the
stub DOM `test_web_url_dialog` builds - because the two bugs it pins (a
recorder that keeps running after its dialog closes, and a swapped-in block
that resets to idle while the microphone is still live) live entirely in that
file and would pass any assertion made about the markup.

The three appends in `test_three_appends_land_as_three_ordered_files` are
three copies of a *complete* webm file, which is not quite what a browser
sends - MediaRecorder's first chunk carries the EBML header and the rest are
cluster continuations. ffmpeg reads the concatenation of complete files
anyway (with a warning about the unknown-length elements), and the duration
that comes out is the first segment's, not the sum. So the assertion is
`> 0`, which is what the plan asks for and all these bytes can honestly show.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from scribe import db, media, paths
from scribe.ingest import recording
# The stub DOM the browser half is driven against lives beside the dialog it
# belongs to; the last section of this module borrows it.
from test_web_url_dialog import needs_node, run_dom

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHUNK = FIXTURES / "chunk.webm"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    paths.ensure_dirs()
    return data


@pytest.fixture
def conn(tmp_path, data_dir):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


def chunk_bytes() -> bytes:
    return CHUNK.read_bytes()


def ffprobe_duration(path: Path) -> float | None:
    """The duration ffprobe reports for a file, or None when it reports none.

    `scribe.stages.probe.probe_media` would do, but it summarises and would
    hide which of the two answers came back. This asks the one question.
    """
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    payload = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    raw = (payload.get("format") or {}).get("duration")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def rows(conn) -> list:
    return conn.execute("SELECT * FROM recording ORDER BY id").fetchall()


@pytest.fixture
def listings(monkeypatch):
    """Counts how often one directory is enumerated; yields a lookup function.

    Both doors are watched because a listing can arrive through either:
    `Path.glob` reaches the filesystem through `os.scandir` (measured on this
    machine's CPython 3.12.9 - one `scandir` call per directory globbed) and
    plain `os.listdir` is the other way anyone would write it. Watching the
    `os` functions rather than a helper inside `recording` means the count is
    of real work done, not of a seam this test invented, so a future rewrite
    that re-lists the directory some third way is still caught.

    Counted per resolved path, because the process lists plenty of other
    directories - sqlite's, the media store's, pytest's own - and a counter
    that summed those would be measuring noise instead of the claim.
    """
    counts: dict[Path, int] = {}
    real_scandir, real_listdir = os.scandir, os.listdir

    def note(path) -> None:
        try:
            resolved = Path(os.fspath(path)).resolve()
        except (OSError, TypeError, ValueError):
            return  # a file descriptor, or bytes: not a directory we asked about
        counts[resolved] = counts.get(resolved, 0) + 1

    def scandir(path=".", *args, **kwargs):
        note(path)
        return real_scandir(path, *args, **kwargs)

    def listdir(path=".", *args, **kwargs):
        note(path)
        return real_listdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(os, "listdir", listdir)
    return lambda directory: counts.get(Path(directory).resolve(), 0)


# --- the fixture itself ------------------------------------------------------------


def test_the_captured_chunk_carries_no_duration_the_way_a_browser_sends_it():
    """The premise every other test here rests on.

    If this ever starts reporting a number, the fixture was regenerated to a
    seekable file and `test_finalize_...` below is no longer evidence that the
    remux did anything.
    """
    assert CHUNK.is_file(), "capture the fixture: see this module's docstring"
    assert ffprobe_duration(CHUNK) is None


# --- starting and appending ---------------------------------------------------------


def test_start_returns_a_url_safe_session_and_makes_its_directory(conn):
    session = recording.start(conn)

    assert len(session) >= 16
    assert recording.session_dir(session).is_dir()
    assert recording.session_dir(session).parent == paths.WORK_DIR / recording.REC_DIRNAME

    (row,) = rows(conn)
    assert row["session"] == session
    assert row["started_at"] > 0
    assert row["finished_at"] is None and row["media_id"] is None


def test_two_sessions_do_not_share_a_directory(conn):
    first, second = recording.start(conn), recording.start(conn)

    assert first != second
    assert recording.session_dir(first) != recording.session_dir(second)


def test_three_appends_land_as_three_ordered_files(conn):
    session = recording.start(conn)

    indexes = [recording.append(session, f"chunk {n}".encode()) for n in range(3)]

    assert indexes == [0, 1, 2]
    names = sorted(p.name for p in recording.session_dir(session).iterdir())
    assert names == ["000000.webm", "000001.webm", "000002.webm"]
    # Sorted by name is sorted by arrival: that is what the zero padding buys,
    # and it is what the concatenation in finalize relies on.
    assert [p.read_bytes() for p in recording.chunk_paths(session)] == [
        b"chunk 0", b"chunk 1", b"chunk 2"
    ]


def test_appending_to_a_session_that_was_never_started_is_refused(conn):
    with pytest.raises(recording.UnknownSession):
        recording.append("neverstartedatall", b"bytes")


@pytest.mark.parametrize(
    "hostile",
    [
        "../../evil",
        "..\\..\\evil",
        "a/b",
        "a\\b",
        "..",
        ".",
        "",
        "  ",
        "not a token",
        "x" * 200,
        "café",  # only the url-safe alphabet secrets.token_urlsafe produces
    ],
)
def test_a_session_that_is_not_a_token_never_becomes_a_path(conn, hostile):
    """A session id arrives from the URL path, percent-decoded by Starlette.

    So it is a string a stranger's page can choose, and it is used to build a
    path under WORK_DIR. The shape check lives in this module rather than only
    in the route for the same reason `urls.ensure_http_url` does: a route is
    not the only caller.
    """
    for call in (
        lambda: recording.session_dir(hostile),
        lambda: recording.append(hostile, b"bytes"),
        lambda: recording.finalize(conn, hostile, title="t", folder_id=None),
        lambda: recording.cancel(conn, hostile),
    ):
        with pytest.raises(recording.UnknownSession):
            call()

    # Nothing was created, and nothing outside the recording root was touched.
    assert list((paths.WORK_DIR / recording.REC_DIRNAME).glob("*")) == []
    assert not (paths.WORK_DIR / "evil").exists()
    assert not (paths.DATA_DIR / "evil").exists()


# --- what one chunk costs, and what two at once cost ----------------------------------
#
# These two go together and are easiest to read as one thought. `append` has to
# answer "which index is this?", and the two ways of getting that wrong pull in
# opposite directions: work it out from the directory every time and the cost of
# a chunk grows with the length of the recording; remember it and skip the
# exclusive create, and two chunks that arrive at once claim the same name. The
# tests below pin both ends - the remembering must be O(1), and the file on disk
# must still be the thing that decides.


def test_a_chunk_costs_the_same_whether_it_is_the_second_or_the_twentieth(conn, listings):
    """The cost of a chunk must not depend on how many came before it.

    `append` used to take its index from `len(list(directory.glob("*.webm")))`,
    which is one full directory enumeration per five-second chunk: O(n) per
    POST and O(n^2) over a session, growing exactly as the recording that can
    least afford it gets longer. Measured 2026-09-04 on this machine (warm OS
    cache, so this is the optimistic number): 0.7 ms to list 60 chunks, 4.5 ms
    at one hour, 22 ms at four hours, 62 ms at eight - all of it spent in
    Starlette's threadpool re-reading an answer the process already had.

    So this asserts the shape of the cost and not just the answer: twenty
    appends may look at the directory once, to find out where they are, and
    after that not at all. The indexes are checked too, because a cheap
    `append` that puts the chunks in the wrong order is not a fix.
    """
    session = recording.start(conn)
    directory = recording.session_dir(session)

    first = recording.append(session, b"chunk 0")
    after_first = listings(directory)
    rest = [recording.append(session, f"chunk {n}".encode()) for n in range(1, 20)]
    after_twenty = listings(directory)

    assert [first, *rest] == list(range(20))
    assert after_first <= 1, (
        f"the first chunk enumerated the session directory {after_first} times;"
        " once is all it takes to find out where a restarted process is"
    )
    assert after_twenty == after_first, (
        f"chunks 2..20 enumerated the session directory {after_twenty - after_first}"
        " more times; the cost of a chunk still grows with the length of the"
        " recording, which is the wrong way round for a recorder"
    )


def test_a_session_that_outlives_the_process_carries_on_from_the_disk(conn, listings):
    """A restart mid-recording must not start counting from zero again.

    Whatever `append` remembers is per-process, and this process can be
    replaced while a browser is still recording - the app restarts, the tab
    does not. The chunks on disk are the only durable record of how far the
    session got, so a cold `append` has to read them, once, and then carry on
    from there. Starting at zero would overwrite the recording so far; the
    exclusive create would stop the overwrite but only by walking the whole
    directory one `FileExistsError` at a time.
    """
    session = recording.start(conn)
    for n in range(3):
        recording.append(session, f"chunk {n}".encode())
    directory = recording.session_dir(session)

    recording._forget(session)  # exactly what a fresh process remembers: nothing
    before = listings(directory)
    index = recording.append(session, b"after the restart")

    assert index == 3, "a restarted process aimed at a name that was already taken"
    assert listings(directory) - before == 1, "one look at the disk, not none and not four"
    assert (directory / "000003.webm").read_bytes() == b"after the restart"
    # And the three that were already there are untouched.
    assert [p.read_bytes() for p in recording.chunk_paths(session)] == [
        b"chunk 0", b"chunk 1", b"chunk 2", b"after the restart"
    ]


def test_two_chunks_that_arrive_at_once_get_two_names_and_two_files(conn, monkeypatch):
    """Two overlapping chunk POSTs must not both become chunk seven.

    Not hypothetical: `POST /record/{session}/chunk` runs `append` through
    `run_in_threadpool`, so a browser that retries a slow chunk or pipelines
    the next one really does put two threads in here at once. Without the
    exclusive create both would write the same NNNNNN.webm, one would silently
    replace the other, both requests would answer 200, and the recording would
    finalize and transcribe with a five-second hole in it and nothing anywhere
    reporting a loss.

    The interleaving that does the damage is the one where both threads decide
    where they are writing *before* either writes, so that is the one forced
    here: the barrier sits inside the index lookup, which both threads always
    reach, and neither is allowed to leave it until both are holding an index.
    After that they race into the create for real.
    """
    session = recording.start(conn)
    both_decided = threading.Barrier(2, timeout=30)
    real_next_index = recording._next_index

    def rendezvous(session_, directory):
        index = real_next_index(session_, directory)
        both_decided.wait()  # neither claims a name until both have picked one
        return index

    monkeypatch.setattr(recording, "_next_index", rendezvous)

    indexes: dict[str, int] = {}

    def worker(name: str) -> None:
        indexes[name] = recording.append(session, f"from {name}".encode())

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "an append thread never came back"

    assert sorted(indexes.values()) == [0, 1], (
        f"two simultaneous chunks were given the indexes {sorted(indexes.values())};"
        " one of them overwrote the other"
    )
    written = {p.name: p.read_bytes() for p in recording.chunk_paths(session)}
    assert sorted(written) == ["000000.webm", "000001.webm"]
    assert sorted(written.values()) == [b"from a", b"from b"], "a chunk was lost"


def test_a_stale_index_hint_never_overwrites_a_chunk_that_is_already_there(
    conn, monkeypatch
):
    """Remembering the index is allowed to be wrong. Losing a chunk is not.

    Whatever `append` carries between calls is a hint - a second thread may
    have claimed the next name already, a directory may have been written to
    by something this process did not see. So the hint is aimed at a name that
    is definitely taken, here, on purpose. The chunk that is already on disk
    has to survive it, and the new one has to go somewhere else.

    This is the guarantee the cost fix above must not have traded away, which
    is why it is pinned separately from the threaded test: this one needs no
    scheduling luck at all.
    """
    session = recording.start(conn)
    recording.append(session, b"the chunk that was already here")

    monkeypatch.setattr(recording, "_next_index", lambda session_, directory: 0)
    index = recording.append(session, b"the chunk that came after it")

    assert index == 1, "a stale hint was believed over the file on disk"
    directory = recording.session_dir(session)
    assert (directory / "000000.webm").read_bytes() == b"the chunk that was already here"
    assert (directory / "000001.webm").read_bytes() == b"the chunk that came after it"


# --- finalizing ----------------------------------------------------------------------


def test_finalize_gives_the_recording_a_duration_the_chunks_never_had(conn):
    """The container fix, and the reason this stage exists at all."""
    session = recording.start(conn)
    recording.append(session, chunk_bytes())

    row = recording.finalize(conn, session, title="Marvin's diary", folder_id=None)

    stored = paths.DATA_DIR / row["store_path"]
    assert stored.is_file()
    duration = ffprobe_duration(stored)
    assert duration is not None and duration > 0, "the remux did not stamp a duration"


def test_finalize_ingests_three_chunks_as_one_recording(conn):
    session = recording.start(conn)
    for _ in range(3):
        recording.append(session, chunk_bytes())

    row = recording.finalize(conn, session, title="Three chunks", folder_id=None)

    assert row["title"] == "Three chunks"
    assert row["deduped"] is False
    stored = paths.DATA_DIR / row["store_path"]
    assert ffprobe_duration(stored) > 0
    (in_db,) = conn.execute("SELECT * FROM media").fetchall()
    assert in_db["id"] == row["id"] and in_db["sha256"] == media.hash_file(stored)


def test_finalize_stamps_the_row_and_clears_the_session_directory(conn):
    session = recording.start(conn)
    recording.append(session, chunk_bytes())
    recording.append(session, chunk_bytes())
    expected_bytes = 2 * len(chunk_bytes())

    row = recording.finalize(conn, session, title="", folder_id=None)

    (stamped,) = rows(conn)
    assert stamped["media_id"] == row["id"]
    assert stamped["finished_at"] is not None
    assert stamped["chunk_count"] == 2
    assert stamped["bytes"] == expected_bytes
    # The chunks are gone; the bytes live in the store, under one hardlink.
    assert not recording.session_dir(session).exists()
    assert (paths.DATA_DIR / row["store_path"]).is_file()


def test_finalize_puts_the_recording_in_the_chosen_folder(conn):
    with db.LOCK:
        folder = conn.execute(
            "INSERT INTO folder(name) VALUES ('Voice notes') RETURNING id"
        ).fetchone()["id"]
        conn.commit()
    session = recording.start(conn)
    recording.append(session, chunk_bytes())

    row = recording.finalize(conn, session, title="A note", folder_id=folder)

    assert row["folder_id"] == folder


def test_a_recording_with_no_title_is_named_after_when_it_was_made(conn):
    session = recording.start(conn)
    recording.append(session, chunk_bytes())

    row = recording.finalize(conn, session, title="   ", folder_id=None)

    assert row["title"].startswith(recording.DEFAULT_TITLE_PREFIX)
    assert row["title"] != recording.DEFAULT_TITLE_PREFIX  # a date follows it


def test_finalizing_a_session_with_no_chunks_refuses_and_leaves_no_media(conn):
    """The user pressed stop before the first chunk landed. There is nothing
    to keep, and an empty media row would be worse than a refusal."""
    session = recording.start(conn)

    with pytest.raises(recording.EmptyRecording):
        recording.finalize(conn, session, title="nothing", folder_id=None)

    assert conn.execute("SELECT COUNT(*) c FROM media").fetchone()["c"] == 0


def test_finalizing_an_unknown_session_is_refused(conn):
    with pytest.raises(recording.UnknownSession):
        recording.finalize(conn, "neverstartedatall", title="t", folder_id=None)


def test_chunks_that_are_not_media_fail_the_remux_with_ffmpegs_own_words(conn):
    session = recording.start(conn)
    recording.append(session, b"this is not a webm file, not even slightly")

    with pytest.raises(recording.RemuxFailed) as exc:
        recording.finalize(conn, session, title="junk", folder_id=None)

    assert str(exc.value).strip(), "a failure with no reason is not a failure report"
    assert conn.execute("SELECT COUNT(*) c FROM media").fetchone()["c"] == 0
    # The chunks stay: a failed remux is not a reason to throw away the only
    # copy of what somebody just recorded.
    assert recording.session_dir(session).is_dir()


# --- cancelling and sweeping ----------------------------------------------------------


def test_cancel_removes_the_directory_and_says_the_session_is_over(conn):
    session = recording.start(conn)
    recording.append(session, chunk_bytes())

    recording.cancel(conn, session)

    assert not recording.session_dir(session).exists()
    (row,) = rows(conn)
    assert row["finished_at"] is not None and row["media_id"] is None


def test_cancelling_an_unknown_session_is_refused(conn):
    with pytest.raises(recording.UnknownSession):
        recording.cancel(conn, "neverstartedatall")


def _age(path: Path, seconds: float) -> None:
    """Make a directory and everything in it look ``seconds`` old."""
    when = time.time() - seconds
    for child in path.iterdir():
        os.utime(child, (when, when))
    os.utime(path, (when, when))


def test_sweep_removes_an_abandoned_session_and_keeps_a_fresh_one(conn):
    old = recording.start(conn)
    recording.append(old, chunk_bytes())
    fresh = recording.start(conn)
    recording.append(fresh, chunk_bytes())
    _age(recording.session_dir(old), 2 * recording.SWEEP_AFTER_SECONDS)

    removed = recording.sweep(conn)

    assert removed == 1
    assert not recording.session_dir(old).exists()
    assert recording.session_dir(fresh).is_dir()


def test_sweep_records_what_it_threw_away(conn):
    session = recording.start(conn)
    recording.append(session, chunk_bytes())
    _age(recording.session_dir(session), 2 * recording.SWEEP_AFTER_SECONDS)

    recording.sweep(conn)

    (row,) = rows(conn)
    assert row["finished_at"] is not None
    assert row["media_id"] is None  # it never became a recording
    assert row["chunk_count"] == 1 and row["bytes"] == len(chunk_bytes())


def test_sweep_leaves_a_directory_that_is_still_being_written_to(conn):
    """An old session that gained a chunk a minute ago is a long recording,
    not an abandoned one. The age of the newest chunk is what decides."""
    session = recording.start(conn)
    recording.append(session, chunk_bytes())
    _age(recording.session_dir(session), 2 * recording.SWEEP_AFTER_SECONDS)
    recording.append(session, chunk_bytes())  # ... and then it carried on

    assert recording.sweep(conn) == 0
    assert recording.session_dir(session).is_dir()


def test_sweep_on_a_machine_that_has_never_recorded_does_nothing(conn):
    assert recording.sweep(conn) == 0


# --- the browser half, driven in node -------------------------------------------------

RECORDER_FIXTURE = r"""
    /* The transcribe dialog with the microphone door open in it, as
       library.html, transcribe_dialog.html and _recorder.html build it. */
    function recorderBlock() {
      const root = el('div', { class: 'recorder', 'data-recorder': '' });
      root.hidden = true;
      root.append(el('button', { type: 'button', class: 'record', 'data-record-start': '' }));
      root.append(el('button', { type: 'button', 'data-record-pause': '' })).hidden = true;
      root.append(el('button', { type: 'button', 'data-record-finish': '' })).hidden = true;
      root.append(el('button', { type: 'button', 'data-record-cancel': '' })).hidden = true;
      root.append(el('meter', { 'data-record-level': '' }));
      root.append(el('span', { 'data-record-elapsed': '' })).textContent = '0:00';
      root.append(el('p', { 'data-record-status': '' })).textContent = 'Nothing is uploaded to anyone.';
      return root;
    }

    function snap(root) {
      return {
        hidden: root.hidden,
        start: root.querySelector('[data-record-start]').hidden,
        pause: root.querySelector('[data-record-pause]').hidden,
        finish: root.querySelector('[data-record-finish]').hidden,
        cancel: root.querySelector('[data-record-cancel]').hidden,
        elapsed: root.querySelector('[data-record-elapsed]').textContent,
        status: root.querySelector('[data-record-status]').textContent,
        busy: root.getAttribute('data-busy')
      };
    }

    function press(node) { return fire(document, 'click', event({ target: node })); }

    const dialog = body.append(el('dialog', { id: 'transcribe-dialog' }));
    dialog.open = true;
    const flash = dialog.append(el('p', { 'data-dialog-flash': '' }));
    const closeButton = dialog.append(el('button', { type: 'button', 'data-close-dialog': '' }));
    const form = dialog.append(el('form'));
    let block = form.append(recorderBlock());
"""


@needs_node
def test_a_swapped_in_recorder_repaints_the_live_controls_rather_than_resetting(tmp_path):
    """CR-002, first half. `live` outlives the markup it was started from.

    Reopening the dialog runs hx-get /transcribe with hx-swap="innerHTML", so a
    fresh idle copy of the block arrives while the MediaRecorder is still
    running and still posting a chunk every five seconds. `reveal()` used to
    set that copy to idle unconditionally: Record shown beside a clock that
    keeps counting, and `begin()` returning at once because `live` is set, so
    the button did nothing and said nothing. No reachable control called
    `finish()` or `discard()` any more - the only way out was a reload, which
    abandons the session directory under WORK_DIR.
    """
    result = run_dom(
        tmp_path,
        RECORDER_FIXTURE
        + r"""
    load(RECORDER);
    press(block.querySelector('[data-record-start]'));
    await settle();
    await settle();
    const live = snap(block);

    /* The dialog was closed and opened again: htmx replaced its contents. */
    block.remove();
    const fresh = form.append(recorderBlock());
    advance(65000);
    fire(document.body, 'htmx:afterSwap', event({}));

    done({ live: live, after: snap(fresh) });
""",
    )

    assert result["live"]["start"] is True and result["live"]["finish"] is False
    after = result["after"]
    assert after["hidden"] is False
    assert after["start"] is True, "Record came back while a recording was still running"
    assert after["finish"] is False and after["cancel"] is False, "no way to stop or discard"
    assert after["pause"] is False
    assert after["elapsed"] == "1:05", "the clock was not repainted from the live session"
    assert after["status"].startswith("Recording.")


@needs_node
def test_a_second_start_before_the_first_has_answered_opens_nothing(tmp_path):
    """The recording that played over itself (2026-09-06).

    `live` was the only guard against a second `begin`, and `live` is set only
    after getUserMedia and POST /record/start have both answered. A second
    `begin` inside that window - the autostart re-run by a swap, a second
    click - opened a second session and a second MediaRecorder on the same
    microphone, and both recorders then posted every chunk to the session
    `live` named at the time. Thirteen chunk POSTs for a 32-second recording;
    62 seconds of audio in a file stamped 32.
    """
    result = run_dom(
        tmp_path,
        RECORDER_FIXTURE
        + r"""
    const made = [];
    const Real = window.MediaRecorder;
    function Counting(stream, options) { Real.call(this, stream, options); made.push(this); }
    Counting.prototype = Real.prototype;
    Counting.isTypeSupported = Real.isTypeSupported;
    window.MediaRecorder = Counting;

    load(RECORDER);
    press(block.querySelector('[data-record-start]'));
    press(block.querySelector('[data-record-start]'));  /* before anything answered */
    await settle();
    await settle();
    await settle();
    const starts = posted.filter(function (p) { return p.url === '/record/start'; }).length;

    /* Every recorder that exists hands over a chunk; only the live one may post. */
    made.forEach(function (r) {
      if (r.ondataavailable) { r.ondataavailable({ data: { size: 3, type: 'audio/webm' } }); }
    });
    await settle();
    await settle();
    const chunks = posted.filter(function (p) { return /\/chunk$/.test(p.url); }).length;

    done({ starts: starts, recorders: made.length, chunks: chunks, mics: tracks.length });
""",
    )

    assert result["starts"] == 1, "a second session was opened"
    assert result["recorders"] == 1, "a second MediaRecorder was started"
    assert result["mics"] == 1, "the microphone was opened twice"
    assert result["chunks"] == 1


@needs_node
def test_a_chunk_from_a_recorder_that_is_not_the_live_one_is_dropped(tmp_path):
    """Belt to the guard's braces: even if a stray recorder existed, its chunks
    could not land in the live session."""
    result = run_dom(
        tmp_path,
        RECORDER_FIXTURE
        + r"""
    const made = [];
    const Real = window.MediaRecorder;
    function Counting(stream, options) { Real.call(this, stream, options); made.push(this); }
    Counting.prototype = Real.prototype;
    Counting.isTypeSupported = Real.isTypeSupported;
    window.MediaRecorder = Counting;

    load(RECORDER);
    press(block.querySelector('[data-record-start]'));
    await settle();
    await settle();
    const stray = new Real({}, {});
    stray.ondataavailable = made[0].ondataavailable;  /* the same handler, another recorder */
    stray.ondataavailable({ data: { size: 3, type: 'audio/webm' } });
    made[0].ondataavailable({ data: { size: 3, type: 'audio/webm' } });
    await settle();
    await settle();
    done({ chunks: posted.filter(function (p) { return /\/chunk$/.test(p.url); }).length });
""",
    )
    assert result["chunks"] == 1


@needs_node
def test_the_dialog_refuses_to_close_while_the_microphone_is_live(tmp_path):
    """CR-002, second half. Nothing tied the recorder to the dialog at all.

    The close button, Cancel, Escape and the auto-close after a successful post
    each left a MediaRecorder running with no control on screen. All four ask
    the dialog first now, and the recorder is what says no - and what says why,
    in the dialog's own flash line.
    """
    result = run_dom(
        tmp_path,
        RECORDER_FIXTURE
        + r"""
    load(APP);
    load(RECORDER);
    press(block.querySelector('[data-record-start]'));
    await settle();
    await settle();

    press(closeButton);
    const button = { closed: dialog.closed, flash: flash.textContent };

    const escape = event({ target: dialog });
    const seenCancel = fire(document, 'cancel', escape);
    const esc = { seen: seenCancel, prevented: escape.defaultPrevented, closed: dialog.closed };

    /* An upload that succeeds while the microphone is live is still not a
       reason to take the microphone's controls away. */
    fire(document.body, 'htmx:afterRequest', event({
      detail: { elt: form, successful: true, requestConfig: { verb: 'POST' } }
    }));

    done({
      button: button,
      esc: esc,
      afterUpload: dialog.closed,
      busy: block.getAttribute('data-busy'),
      capture: registered(document, 'cancel').map(function (l) { return l.capture; })
    });
""",
    )

    assert result["button"]["closed"] == 0, "the close button abandoned a running recorder"
    assert "recording" in result["button"]["flash"].lower(), result["button"]["flash"]
    assert result["esc"] == {"seen": 1, "prevented": 1, "closed": 0}
    # A <dialog>'s cancel event does not bubble, so the listener has to be a
    # capturing one on an ancestor to see it at all.
    assert result["capture"] == [True]
    assert result["afterUpload"] == 0
    assert result["busy"], "the recorder did not say it was busy"


@needs_node
def test_discarding_the_recording_lets_the_dialog_close_again(tmp_path):
    """The other side of the refusal, and the one a fix invites: a dialog that
    can never be closed is worse than one that closes too eagerly.

    Discard ends the session, and the moment it does the dialog is ordinary
    again. This is the case that catches a busy marker set where `live` is
    repainted rather than where it is set and cleared.
    """
    result = run_dom(
        tmp_path,
        RECORDER_FIXTURE
        + r"""
    load(APP);
    load(RECORDER);
    press(block.querySelector('[data-record-start]'));
    await settle();
    await settle();

    press(block.querySelector('[data-record-cancel]'));   /* window.confirm says yes */
    await settle();
    await settle();
    await settle();
    const after = snap(block);

    press(closeButton);
    done({
      after: after,
      closed: dialog.closed,
      cancelled: posted.filter(function (p) { return /\/cancel$/.test(p.url); }).length
    });
""",
    )

    assert result["cancelled"] == 1, "the session was not cancelled on the server"
    assert result["after"]["busy"] is None, "the dialog stayed locked after the discard"
    assert result["after"]["start"] is False, "Record never came back"
    assert result["closed"] == 1, "the dialog would not close once the recorder had stopped"
