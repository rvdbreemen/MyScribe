"""Watch folders: quiescence, dedupe by hash, and the startup reconcile.

Nothing here sleeps on a real filesystem event. Watchdog's observer runs on a
thread and delivers events whenever the OS feels like it - on Windows through
`ReadDirectoryChangesW`, coalesced and reordered - so a test that dropped a
file and waited would be a stopwatch, not an assertion. The handler is driven
directly instead: `notice()` is what the observer would have called, `pump()`
is the tick that decides, and the clock is a parameter.

The quiescence rule is the point of the whole module and it is worth saying
why in a test file. A file that is still being copied has a growing size. Hash
it halfway and you get a sha256 of half a recording, stored under a name that
claims to be the whole thing - and because the store is content-addressed,
that wrong hash is *permanent*: the finished file will later hash differently
and be ingested a second time, while the truncated one keeps its row forever.
So `test_a_file_that_is_still_being_copied_is_not_ingested_until_it_settles`
asserts both halves: nothing lands while it grows, and what finally lands is
the finished bytes.

The media bytes here are not media. `media.ingest_path` hashes and hardlinks;
it never opens a decoder (ffprobe's turn comes inside the job), so a `.mp3`
holding the word "hello" exercises every line these tests are about and costs
no ffmpeg.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from watchdog.events import DirCreatedEvent, FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from scribe import db, fsbrowse, media, paths
from scribe.app import create_app
from scribe.ingest import recording, watching
from scribe.options import TranscribeOptions
from scribe.stages import transcribe

HX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def tmp_inside_the_roots(tmp_path, monkeypatch):
    """pytest's tmp_path has to lie under a browse root, or the watcher refuses
    every file these tests drop - by design (`watching` takes nothing from
    outside the roots). On Windows the default root is the whole profile
    drive and the temp directory is on it; on Linux and macOS the default is
    the home directory and the temp directory is /tmp or /private/var, which
    is outside. So the roots are widened to include tmp_path here, the way
    test_web_exports and test_pipeline_e2e already set them. Tests that want
    a folder *outside* the roots narrow them explicitly (`narrow_roots`)."""
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (*fsbrowse.ALLOWED_ROOTS, tmp_path))


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
def db_path(tmp_path, data_dir):
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
def inbox(tmp_path):
    """A folder outside DATA_DIR, on the same volume so hardlinks work."""
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def watched(conn, inbox):
    """`inbox`, registered and enabled, with the default options."""
    watching.add_folder(conn, inbox, TranscribeOptions())
    return inbox


@pytest.fixture
def watcher(db_path):
    return watching.Watcher(db_path)


@pytest.fixture
def client(db_path, data_dir):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


SETTLED_AGO = 60.0
"""How long ago a dropped file is stamped as having finished arriving.

`reconcile` asks how long a file has held still before it opens it, so a file
written this instant is - correctly - taken for a copy in progress. Every test
here that is not about that case wants a file that has finished, and a real
watched folder is full of them. `settled=False` is the opposite case, and it
is the one `test_reconcile_does_not_hash_a_file_that_is_still_being_copied` is
about."""


def drop(
    folder: Path,
    name: str,
    body: bytes = b"a recording of something",
    *,
    settled: bool = True,
) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if settled:
        old = time.time() - SETTLED_AGO
        os.utime(path, (old, old))
    return path


def media_rows(conn) -> list:
    return conn.execute("SELECT * FROM media ORDER BY id").fetchall()


def job_rows(conn) -> list:
    return conn.execute("SELECT * FROM job ORDER BY id").fetchall()


class FakeObserver:
    """A watchdog Observer with the threads taken out.

    Enough of the interface for `_sync_schedules` and the start/stop dance:
    schedule hands back a token, unschedule takes it away, and the three
    lifecycle calls are recorded so a test can say the loop tidied up.
    """

    def __init__(self) -> None:
        self.watches: dict[str, str] = {}
        self.started = False
        self.stopped = False
        self.joined = False
        self._issued = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout=None) -> None:
        self.joined = True

    def schedule(self, handler, path, recursive=False):
        self._issued += 1
        token = f"watch-{self._issued}"
        self.watches[token] = str(path)
        return token

    def unschedule(self, watch) -> None:
        self.watches.pop(watch, None)

    def paths(self) -> set[str]:
        return {os.path.normcase(p) for p in self.watches.values()}


class RefusingObserver(FakeObserver):
    """A FakeObserver that will not schedule one particular folder.

    What that is on a real machine: a folder on a network drive that dropped
    away between the row being read and the watch being opened - watchdog's
    Windows backend calls CreateFileW on the directory and raises - or one this
    account may list but not open for change notifications. Both are ordinary,
    and neither is a reason for the folders after it to get no events.
    """

    def __init__(self, refuse: str) -> None:
        super().__init__()
        self.refuse = os.path.normcase(str(refuse))
        self.attempts: list[str] = []

    def schedule(self, handler, path, recursive=False):
        self.attempts.append(str(path))
        if os.path.normcase(str(path)) == self.refuse:
            raise OSError(f"the directory {path} cannot be watched")
        return super().schedule(handler, path, recursive=recursive)


def narrow_roots(conn, *roots: Path) -> None:
    """Point `fsbrowse` at exactly these folders, as the settings form does."""
    with db.LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            (fsbrowse.SETTING_KEY, "\n".join(str(root) for root in roots)),
        )
        conn.commit()


# --- quiescence -----------------------------------------------------------------------


def test_a_file_that_has_stopped_changing_is_ingested_once_and_enqueued(conn, watcher, watched):
    path = drop(watched, "interview.mp3")
    watcher.notice(path)

    # The first tick only takes a snapshot. Asserted rather than assumed: a
    # watcher that ingested here would pass the second half of this test and
    # still hash files that are being written.
    assert watcher.pump(conn, now=1000.0) == []
    assert media_rows(conn) == [] and job_rows(conn) == []

    taken = watcher.pump(conn, now=1000.0 + watching.QUIESCE_SECONDS)

    assert len(taken) == 1
    (row,) = media_rows(conn)
    assert row["orig_name"] == "interview.mp3"
    assert row["sha256"] == media.hash_file(path)
    (job,) = job_rows(conn)
    assert job["type"] == "transcribe" and job["media_id"] == row["id"]
    assert job["status"] == "queued"
    assert taken[0]["job_id"] == job["id"]


def test_a_file_is_not_taken_in_before_the_quiet_interval_has_passed(conn, watcher, watched):
    """Unchanged is not enough; unchanged *for QUIESCE_SECONDS* is the rule.

    A copy over a slow network can pause for a second between writes, and two
    looks a microsecond apart would both see the same size.
    """
    watcher.notice(drop(watched, "settling.mp3"))
    watcher.pump(conn, now=1000.0)

    assert watcher.pump(conn, now=1000.0 + watching.QUIESCE_SECONDS - 0.5) == []
    assert media_rows(conn) == []

    assert len(watcher.pump(conn, now=1000.0 + watching.QUIESCE_SECONDS)) == 1


def test_a_file_that_is_still_being_copied_is_not_ingested_until_it_settles(conn, watcher, watched):
    """The rule this module exists for.

    Half a file has a sha256 of its own, and a content-addressed store would
    keep that row forever while the finished file arrived beside it as a
    second one. So the bytes in the store must be the bytes that stopped
    growing - which is what the last assertion checks.
    """
    path = drop(watched, "copying.mp3", b"first part")
    watcher.notice(path)
    watcher.pump(conn, now=1000.0)

    for step, more in enumerate((b" second part", b" third part"), start=1):
        with open(path, "ab") as out:
            out.write(more)
        os.utime(path, (1000.0 + step, 1000.0 + step))
        assert watcher.pump(conn, now=1000.0 + step * 10 * watching.QUIESCE_SECONDS) == []
        assert media_rows(conn) == []

    # It stopped growing; one quiet interval later it is taken in whole.
    taken = watcher.pump(conn, now=1000.0 + 25 * watching.QUIESCE_SECONDS)

    assert len(taken) == 1
    (row,) = media_rows(conn)
    assert row["sha256"] == media.hash_file(path)
    stored = paths.DATA_DIR / row["store_path"]
    assert stored.read_bytes() == b"first part second part third part"
    assert row["size_bytes"] == len(b"first part second part third part")


def test_a_file_noticed_again_after_it_was_taken_in_is_not_rehashed_or_requeued(
    conn, watcher, watched, monkeypatch
):
    """A cloud-sync re-stamp or an antivirus touch is a modify event.

    Two guarantees, one cheap and one absolute: an unchanged file is dropped
    without opening it (no hash), and even if it were hashed the store would
    dedupe it. The hash counter is what proves the first one.
    """
    path = drop(watched, "meeting.mp3")
    watcher.notice(path)
    watcher.pump(conn, now=1000.0)
    watcher.pump(conn, now=1005.0)

    hashed: list[Path] = []
    real = media.hash_file
    monkeypatch.setattr(media, "hash_file", lambda p: hashed.append(Path(p)) or real(p))

    watcher.notice(path)
    watcher.pump(conn, now=1100.0)
    # The second tick is the one that would open it: by then the file has been
    # unchanged for a quiet interval, which is exactly when a file that had
    # *not* been taken in gets hashed.
    assert watcher.pump(conn, now=1110.0) == []

    assert hashed == [], "an unchanged file that is already in the store was re-hashed"
    assert len(media_rows(conn)) == 1 and len(job_rows(conn)) == 1


def test_two_names_for_the_same_bytes_become_one_recording_and_one_job(conn, watcher, watched):
    """Dedupe is by sha256, not by path: a copy under a second name is the
    same recording, and transcribing it twice would cost a GPU hour for a
    file the library already has."""
    first = drop(watched, "keynote.mp3", b"identical bytes")
    second = drop(watched, "keynote (1).mp3", b"identical bytes")

    watcher.notice(first)
    watcher.notice(second)
    watcher.pump(conn, now=1000.0)
    taken = watcher.pump(conn, now=1010.0)

    assert len(taken) == 1
    assert len(media_rows(conn)) == 1
    assert len(job_rows(conn)) == 1


def test_a_recording_the_library_already_has_is_not_transcribed_again(conn, watcher, watched):
    """Deliberately unlike `POST /api/media`, which does queue a second job.

    There a person asked for it. Here nobody did: a folder that re-appears
    after a sync must not queue a transcription of something already sitting
    in the library with a transcript against it.
    """
    already = drop(watched, "old.mp3", b"already known")
    media.ingest_path(conn, already)
    assert job_rows(conn) == []

    watcher.notice(already)
    watcher.pump(conn, now=1000.0)
    taken = watcher.pump(conn, now=1010.0)

    assert taken == []
    assert len(media_rows(conn)) == 1
    assert job_rows(conn) == []


def test_a_file_that_disappears_before_it_settles_is_forgotten(conn, watcher, watched):
    path = drop(watched, "gone.mp3")
    watcher.notice(path)
    watcher.pump(conn, now=1000.0)
    path.unlink()

    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []


# --- what is not a candidate ------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "download.mp3.part",          # yt-dlp, and most downloaders
        "download.mp4.crdownload",    # Chrome
        "download.mp3.tmp",
        "download.mp3.partial",       # Edge
        "notes.txt",
        "archive.zip",
        "no-extension",
    ],
)
def test_a_file_that_is_not_a_finished_recording_is_ignored(conn, watcher, watched, name):
    path = drop(watched, name)

    assert watcher.notice(path) is False
    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []


def test_a_dotfile_is_ignored(conn, watcher, watched):
    assert watcher.notice(drop(watched, ".hidden.mp3")) is False


@pytest.mark.skipif(sys.platform != "win32", reason="the hidden attribute is Windows'")
def test_a_file_carrying_the_windows_hidden_attribute_is_ignored(conn, watcher, watched):
    """The rule that actually bites here: cloud-sync clients hide their
    scratch files with the attribute rather than a leading dot."""
    path = drop(watched, "sync-scratch.mp3")
    assert ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02), "could not hide the file"

    assert watching.is_candidate(path) is False
    assert watcher.notice(path) is False


def test_the_apps_own_data_directory_is_never_taken_back_in(conn, watcher, inbox):
    """A watch folder may legitimately contain DATA_DIR - the default browse
    root on Windows is the whole drive - and everything under it looks like
    media: the store itself, `work/*.wav` from a running job, and the webm
    chunks of a recording in progress. Ingesting those would loop."""
    watching.add_folder(conn, inbox, TranscribeOptions())
    inside = [
        drop(paths.MEDIA_DIR / "ab", "abcdef.mp3"),
        drop(paths.WORK_DIR, "7/audio.wav"),
        drop(paths.WORK_DIR / "rec" / "session", "000000.webm"),
    ]

    for path in inside:
        assert watching.is_candidate(path) is False
        assert watcher.notice(path) is False
    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []


def test_a_file_outside_every_watched_folder_is_not_ingested(conn, watcher, watched, tmp_path):
    elsewhere = drop(tmp_path / "elsewhere", "stranger.mp3")

    watcher.notice(elsewhere)
    watcher.pump(conn, now=1000.0)

    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []


def test_a_file_in_a_disabled_folder_is_not_ingested(conn, watcher, watched):
    path = drop(watched, "later.mp3")
    (row,) = watching.folders(conn)
    watching.set_enabled(conn, row["id"], False)

    watcher.notice(path)
    watcher.pump(conn, now=1000.0)

    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []


# --- the user's files stay the user's ------------------------------------------------------


def test_the_watched_file_is_left_exactly_where_it_was(conn, watcher, watched):
    path = drop(watched, "precious.mp3")
    before = path.stat()

    watcher.notice(path)
    watcher.pump(conn, now=1000.0)
    watcher.pump(conn, now=1010.0)

    (row,) = media_rows(conn)
    assert path.is_file()
    assert path.read_bytes() == b"a recording of something"
    assert path.stat().st_mtime == before.st_mtime
    # And the store holds the same inode rather than a second copy.
    assert (paths.DATA_DIR / row["store_path"]).samefile(path)


# --- per-folder options ---------------------------------------------------------------------


def test_the_folders_own_options_are_what_the_job_runs_with(conn, watcher, inbox):
    watching.add_folder(
        conn, inbox, TranscribeOptions(language="nl", tier="max", diarize=False, num_speakers=3)
    )
    path = drop(inbox, "dutch.mp3")

    watcher.notice(path)
    watcher.pump(conn, now=1000.0)
    watcher.pump(conn, now=1010.0)

    (job,) = job_rows(conn)
    params = json.loads(job["params_json"])
    assert params["language"] == "nl"
    assert params["model"] == transcribe.TIER_MODELS["max"]
    assert params["diarize"] is False
    assert params["num_speakers"] == 3


@pytest.fixture
def nested(conn, inbox):
    """Two watched folders, one inside the other, with options that differ.

    The parent is registered *first*, so it has the lower id. That order is the
    whole point of the fixture: `folders()` orders by id, so a pass that ignores
    `folder_for` and simply uses the row it is iterating lands on the parent's
    options. Registered the other way round the same broken code would pick the
    right options by accident and the test would prove nothing.
    """
    child = inbox / "dutch"
    child.mkdir()
    watching.add_folder(conn, inbox, TranscribeOptions(language="en", tier="turbo"))
    watching.add_folder(conn, child, TranscribeOptions(language="nl", tier="max"))
    return child


def test_a_file_in_a_nested_watch_folder_gets_the_deeper_folders_options(conn, watcher, nested):
    """Deepest wins: a folder watched inside another was configured on purpose.

    This is `pump`'s half of the rule and it already held; it is pinned here
    because the rule itself has never been asserted anywhere - `folder_for`
    sorts by path length and reversing that sort is a refactor nothing would
    have caught.
    """
    path = drop(nested, "gesprek.mp3")

    watcher.notice(path)
    watcher.pump(conn, now=1000.0)
    watcher.pump(conn, now=1010.0)

    (job,) = job_rows(conn)
    params = json.loads(job["params_json"])
    assert params["language"] == "nl"
    assert params["model"] == transcribe.TIER_MODELS["max"]


def test_the_startup_reconcile_uses_the_same_deepest_folder_rule_as_a_tick(conn, watcher, nested):
    """The same file, the same folders, and it used to depend on nothing but
    whether the app happened to be running when it landed.

    `reconcile` walks each enabled folder and transcribed whatever it found
    with *that* folder's options - and because the walk is recursive and the
    parent has the lower id, a file in the nested folder was taken in with the
    parent's options. Dropped while the app was up it got Dutch and the max
    model; dropped while it was closed, English and turbo. A GPU hour spent on
    the wrong options, reported as a success.
    """
    drop(nested, "gesprek.mp3")

    assert watcher.reconcile(conn) == 1

    (job,) = job_rows(conn)
    params = json.loads(job["params_json"])
    assert params["language"] == "nl"
    assert params["model"] == transcribe.TIER_MODELS["max"]


def test_options_that_no_longer_parse_fall_back_to_the_defaults(conn, watcher, inbox):
    """A row hand-edited, or written by a version that spelled a field
    differently. The folder keeps working with the app's defaults rather than
    silently watching nothing."""
    watching.add_folder(conn, inbox, TranscribeOptions())
    with db.LOCK:
        conn.execute("UPDATE watch_folder SET options_json='{\"tier\": \"warp-nine\"}'")
        conn.commit()
    path = drop(inbox, "whatever.mp3")

    watcher.notice(path)
    watcher.pump(conn, now=1000.0)
    watcher.pump(conn, now=1010.0)

    (job,) = job_rows(conn)
    assert json.loads(job["params_json"]) == TranscribeOptions().to_params()


# --- the handler ------------------------------------------------------------------------------


def test_the_handler_notices_created_modified_and_moved_files(conn, watcher, watched):
    path = drop(watched, "arrived.mp3")
    moved = drop(watched, "renamed.mp3", b"other bytes")
    handler = watcher.handler

    handler.on_created(FileCreatedEvent(str(path)))
    handler.on_modified(FileModifiedEvent(str(path)))
    handler.on_moved(FileMovedEvent(str(watched / "tmp.part"), str(moved)))
    handler.on_created(DirCreatedEvent(str(watched / "subfolder")))

    watcher.pump(conn, now=1000.0)
    taken = watcher.pump(conn, now=1010.0)

    assert len(taken) == 2
    assert {row["orig_name"] for row in media_rows(conn)} == {"arrived.mp3", "renamed.mp3"}


# --- reconcile -----------------------------------------------------------------------------------


def test_reconcile_picks_up_a_file_created_while_the_watcher_was_down(conn, watcher, watched):
    """The event never happened - the app was closed, or a network drive
    dropped it. The folder is the truth, so the folder is walked."""
    drop(watched, "dropped-yesterday.mp3")
    drop(watched, "subfolder/deeper.mp3", b"deeper bytes")

    assert watcher.reconcile(conn) == 2

    assert {row["orig_name"] for row in media_rows(conn)} == {
        "dropped-yesterday.mp3", "deeper.mp3"
    }
    assert len(job_rows(conn)) == 2


def test_reconcile_does_not_hash_a_file_that_is_still_being_copied(conn, watcher, watched):
    """The startup pass had none of the gating the tick has, and it needed it.

    Measured on the real database before this test existed: a file being
    copied while the app started was walked, hashed at 1100 bytes and
    hardlinked into the store under that hash - and because it is a hardlink,
    the store's own file went on growing to 2300 bytes under a row that says
    1100. `row sha256 c4e9d3f9… / store file sha256 2542bd98… / MATCHES:
    False`, permanently, plus a second row and a second GPU job when the
    finished copy hashed differently. Cloud-sync folders - the case watch
    folders are for - download at exactly app-start time.

    `pump` has three tests for this and `reconcile` had none, which is the
    whole reason it shipped.
    """
    path = drop(watched, "copying.mp3", b"first part", settled=False)

    assert watcher.reconcile(conn) == 0
    assert media_rows(conn) == [] and job_rows(conn) == []

    # The copy finishes and the folder settles. Now it is taken in - whole,
    # and with the store holding the bytes the row claims.
    with open(path, "ab") as out:
        out.write(b" second part")
    old = time.time() - SETTLED_AGO
    os.utime(path, (old, old))

    assert watcher.reconcile(conn) == 1
    (row,) = media_rows(conn)
    stored = paths.DATA_DIR / row["store_path"]
    assert row["sha256"] == media.hash_file(path) == media.hash_file(stored)
    assert row["size_bytes"] == stored.stat().st_size == len(b"first part second part")


def test_a_file_reconcile_found_mid_copy_is_taken_in_once_it_has_settled(
    conn, watcher, watched
):
    """Deferred, not dropped. Reconcile hands it to the same pending table the
    observer's events land in, so the tick that follows finishes the job - a
    file the startup pass declined must not need another app start."""
    path = drop(watched, "arriving.mp3", b"half", settled=False)

    assert watcher.reconcile(conn) == 0

    with open(path, "ab") as out:
        out.write(b" and half")
    assert watcher.pump(conn, now=1000.0) == []  # first look: only a snapshot
    taken = watcher.pump(conn, now=1000.0 + watching.QUIESCE_SECONDS)

    assert len(taken) == 1
    (row,) = media_rows(conn)
    assert row["sha256"] == media.hash_file(path)
    assert row["size_bytes"] == len(b"half and half")


def test_reconcile_leaves_content_the_library_already_has(conn, watcher, watched):
    known = drop(watched, "known.mp3", b"already stored")
    media.ingest_path(conn, known)
    drop(watched, "new.mp3", b"not yet stored")

    assert watcher.reconcile(conn) == 1

    assert len(media_rows(conn)) == 2
    (job,) = job_rows(conn)
    assert job["media_id"] == media_rows(conn)[1]["id"]


def test_reconcile_is_idempotent(conn, watcher, watched):
    drop(watched, "once.mp3")

    assert watcher.reconcile(conn) == 1
    assert watcher.reconcile(conn) == 0
    assert len(job_rows(conn)) == 1


def test_reconcile_skips_disabled_folders_and_the_files_it_would_skip_live(conn, watcher, inbox, tmp_path):
    off = tmp_path / "off"
    off.mkdir()
    watching.add_folder(conn, inbox, TranscribeOptions())
    watching.add_folder(conn, off, TranscribeOptions(), enabled=False)
    drop(off, "ignored.mp3", b"in the disabled folder")
    drop(inbox, ".hidden.mp3", b"hidden")
    drop(inbox, "half.mp3.part", b"still downloading")
    drop(inbox, "real.mp3", b"the only one")

    assert watcher.reconcile(conn) == 1

    (row,) = media_rows(conn)
    assert row["orig_name"] == "real.mp3"


def test_the_do_not_reopen_cache_does_not_grow_with_the_size_of_the_folder(
    conn, watcher, watched, monkeypatch
):
    """Measured before it was bounded: a reconcile over 500 files left 500
    entries, ~160 bytes each, and nothing ever removed them. It is a cache -
    forgetting an entry costs one hash the store then dedupes - so the bound
    is free, and the library is unaffected by it."""
    monkeypatch.setattr(watching, "TAKEN_MEMORY", 4)
    for n in range(10):
        drop(watched, f"take{n}.mp3", f"recording {n}".encode())

    assert watcher.reconcile(conn) == 10

    assert len(watcher._taken) == watching.TAKEN_MEMORY
    assert len(media_rows(conn)) == 10 and len(job_rows(conn)) == 10

    # An evicted file is hashed again and goes nowhere: no row, no second job.
    watcher.notice(watched / "take0.mp3")
    watcher.pump(conn, now=1000.0)
    assert watcher.pump(conn, now=1010.0) == []
    assert len(media_rows(conn)) == 10 and len(job_rows(conn)) == 10


# --- the cache that survives a restart -----------------------------------------------------
#
# The arithmetic these are about, from the store's own numbers (2026-09-04): the
# do-not-reopen cache started empty at every launch and `reconcile` hashes what
# it walks, so a 100 GB watched archive was 100 GB of reads per app start - about
# 8 minutes of a disk saturated at 200 MB/s, far worse over SMB, and competing
# with the running job for the same spindle. Nothing about it was visible: the
# recordings all arrive, they just arrive after the disk has been read twice.


def test_a_restart_does_not_read_again_what_the_last_one_already_took_in(
    conn, db_path, watcher, watched, monkeypatch
):
    """The point of persisting the cache: a second Watcher over the same folder
    opens no file it does not have to."""
    drop(watched, "one.mp3", b"first recording")
    drop(watched, "two.mp3", b"second recording")
    assert watcher.reconcile(conn) == 2

    hashed: list[Path] = []
    real = media.hash_file
    monkeypatch.setattr(media, "hash_file", lambda p: hashed.append(Path(p)) or real(p))

    restarted = watching.Watcher(db_path)

    assert restarted.reconcile(conn) == 0
    assert hashed == [], "a restart re-hashed files nothing had touched"


def test_after_a_restart_only_the_files_that_actually_changed_are_read_again(
    conn, db_path, watcher, watched, monkeypatch
):
    """Cheap is not the same as correct, and the size is what keeps them apart.

    A sync client that replaces a file with a longer version and restores the
    original mtime - which is exactly what "keep the modification date" does -
    moves the size and nothing else. A cache keyed on the path, or on the mtime
    alone, would skip that file for good and the library would keep the old
    recording under a row that claims to be the current one.
    """
    same = drop(watched, "unchanged.mp3", b"one recording")
    grown = drop(watched, "grown.mp3", b"another recording")
    assert watcher.reconcile(conn) == 2

    stamp = grown.stat().st_mtime
    with open(grown, "ab") as out:
        out.write(b" with rather more of it")
    os.utime(grown, (stamp, stamp))  # the mtime it already had: only the size moved

    hashed: list[Path] = []
    real = media.hash_file
    monkeypatch.setattr(media, "hash_file", lambda p: hashed.append(Path(p)) or real(p))
    restarted = watching.Watcher(db_path)

    assert restarted.reconcile(conn) == 1
    assert hashed == [grown], "the wrong set of files was opened after the restart"
    assert same.read_bytes() == b"one recording"


def test_a_recording_purged_from_the_library_is_taken_in_again_after_a_restart(
    conn, db_path, watcher, watched
):
    """The cache must not start deciding something the library used to decide.

    Before it was persisted it began empty at every launch, so a reconcile after
    a purge hashed the file, found no row and took it in again. Storing the
    sha256 beside the snapshot is what keeps that true: a snapshot the cache
    knows is trusted only while the library still holds those bytes. Preserved
    deliberately rather than improved - whether a purge should be permanent is a
    real question, and not this one.
    """
    drop(watched, "purged.mp3")
    assert watcher.reconcile(conn) == 1
    (row,) = media_rows(conn)
    with db.LOCK:
        conn.execute("DELETE FROM media WHERE id=?", (row["id"],))
        conn.commit()

    restarted = watching.Watcher(db_path)

    assert restarted.reconcile(conn) == 1


def test_a_taken_cache_that_cannot_be_read_leaves_the_watcher_working(conn, db_path, watched):
    """A file truncated by a power cut, or written by a version that spelled it
    differently. It is a cache: the cost of ignoring it is one pass of hashing,
    and the cost of raising on it would be a watcher that never starts."""
    watching.cache_path().parent.mkdir(parents=True, exist_ok=True)
    watching.cache_path().write_text("{ this was never json", encoding="utf-8")
    drop(watched, "still-works.mp3")

    watcher = watching.Watcher(db_path)

    assert watcher.reconcile(conn) == 1
    assert json.loads(watching.cache_path().read_text(encoding="utf-8")), (
        "the unreadable cache was not replaced by one that can be read"
    )


def test_reconcile_survives_a_folder_that_is_no_longer_there(conn, watcher, inbox, tmp_path):
    gone = tmp_path / "unplugged-drive"
    watching.add_folder(conn, gone, TranscribeOptions())
    watching.add_folder(conn, inbox, TranscribeOptions())
    drop(inbox, "still-here.mp3")

    assert watcher.reconcile(conn) == 1


def test_one_unreadable_file_does_not_stop_the_rest(conn, watcher, watched, monkeypatch):
    """A file another program holds open, a OneDrive placeholder that is not
    really on this disk. The watcher logs it and carries on; `Supervisor`
    treats a transient error the same way, and for the same reason."""
    bad = drop(watched, "locked.mp3", b"cannot read this")
    good = drop(watched, "fine.mp3", b"can read this")

    real = media.ingest_path

    def refuse(conn_, src, **kw):
        if Path(src).name == "locked.mp3":
            raise PermissionError("the file is open in another program")
        return real(conn_, src, **kw)

    monkeypatch.setattr(media, "ingest_path", refuse)

    assert watcher.reconcile(conn) == 1
    (row,) = media_rows(conn)
    assert row["orig_name"] == "fine.mp3"

    watcher.notice(bad)
    watcher.notice(good)
    watcher.pump(conn, now=1000.0)
    assert watcher.pump(conn, now=1010.0) == []  # `good` is already in the store


def test_a_file_whose_first_ingest_failed_is_opened_again_later(conn, watcher, watched, monkeypatch):
    """The failures this catches are all temporary ones.

    Antivirus holding a file for a few seconds after it lands, a OneDrive
    placeholder that is not on the disk yet, a drive that reconnects. Written
    down as handled *before* the attempt, an unchanged file was never opened
    again for the life of the process - and since the entry is only ever
    dropped by eviction, "later" meant "after a restart".
    """
    path = drop(watched, "held-open.mp3", b"readable in a moment")
    attempts: list[Path] = []
    real = media.ingest_path

    def flaky(conn_, src, **kw):
        attempts.append(Path(src))
        if len(attempts) == 1:
            raise PermissionError("the file is open in another program")
        return real(conn_, src, **kw)

    monkeypatch.setattr(media, "ingest_path", flaky)

    watcher.notice(path)
    watcher.pump(conn, now=1000.0)
    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []

    # The next event for it - a sync client re-stamping it, the next reconcile
    # - has to get as far as opening it again.
    watcher.notice(path)
    watcher.pump(conn, now=1100.0)
    taken = watcher.pump(conn, now=1110.0)

    assert len(attempts) == 2, "the watcher never looked at the file a second time"
    assert len(taken) == 1
    (row,) = media_rows(conn)
    assert row["orig_name"] == "held-open.mp3"


# --- the thread and its observer ---------------------------------------------------------------------


def test_start_is_idempotent_and_stop_joins_the_thread(db_path):
    watcher = watching.Watcher(db_path, poll_interval=0.02, observer_factory=FakeObserver)

    watcher.start()
    thread = watcher._thread
    watcher.start()
    assert watcher._thread is thread
    assert thread.is_alive()

    watcher.stop()

    assert not thread.is_alive()
    assert watcher._thread is None
    assert threading.current_thread() is threading.main_thread()


def test_stopping_during_a_startup_reconcile_stops_the_reconcile(
    db_path, conn, watched, monkeypatch
):
    """The existing shutdown test runs against an empty database, so its
    reconcile returns instantly and it cannot see this at all.

    A reconcile over a synced folder runs for minutes. `stop()` set the event,
    joined for its bounded timeout and dropped the reference regardless, so a
    shutdown in the middle returned with the thread still walking and still
    ingesting into a database the app was about to close underneath it.
    """
    for n in range(40):
        drop(watched, f"take{n}.mp3", f"recording {n}".encode())

    walking = threading.Event()
    real = media.ingest_path

    def slow(conn_, src, **kw):
        walking.set()
        time.sleep(0.02)  # 40 of these: comfortably longer than the stop below
        return real(conn_, src, **kw)

    monkeypatch.setattr(media, "ingest_path", slow)
    watcher = watching.Watcher(db_path, poll_interval=0.02, observer_factory=FakeObserver)
    watcher.start()
    thread = watcher._thread
    assert walking.wait(10), "the reconcile never started"

    watcher.stop()

    assert not thread.is_alive()
    assert watcher._thread is None
    ingested = len(media_rows(conn))
    assert ingested < 40, f"the reconcile walked all {ingested} files after being stopped"


def test_a_stop_that_could_not_join_says_so_and_keeps_the_thread(db_path, watched, capsys):
    """A join that timed out used to be indistinguishable from a clean one:
    the reference was dropped either way, so `start()` would happily begin a
    second loop over the same folders while the first was still ingesting."""
    watcher = watching.Watcher(db_path, poll_interval=0.02, observer_factory=FakeObserver)
    stuck = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    stuck.start()
    watcher._thread = stuck

    watcher.stop(timeout=0.05)

    assert watcher._thread is stuck, "a thread that never stopped was forgotten"
    assert "still running" in capsys.readouterr().err
    watcher.start()
    assert watcher._thread is stuck, "a second loop was started over the same folders"


def test_the_loop_starts_and_stops_its_observer(db_path, conn, inbox):
    watching.add_folder(conn, inbox, TranscribeOptions())
    observers: list[FakeObserver] = []

    def factory():
        observers.append(FakeObserver())
        return observers[-1]

    watcher = watching.Watcher(db_path, poll_interval=0.02, observer_factory=factory)
    watcher.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (observers and observers[0].paths()):
        time.sleep(0.01)
    watcher.stop()

    (observer,) = observers
    assert observer.started and observer.stopped and observer.joined
    assert observer.paths() == {os.path.normcase(str(inbox))}


def test_an_observer_that_cannot_be_built_still_reconciles_and_stops_cleanly(
    db_path, conn, watched, capsys
):
    """Found by running it, not by reading it.

    A watchdog import that blows up used to kill the watcher thread before the
    loop began: the app kept serving, `app.state.watcher` still held a Watcher,
    and nothing was ever ingested - indistinguishable from a folder nobody had
    used. Now it says so and does what it still can, which is the startup pass.
    """
    drop(watched, "was-already-there.mp3")

    def broken():
        raise ImportError("no filesystem observer on this machine")

    watcher = watching.Watcher(db_path, poll_interval=0.02, observer_factory=broken)
    watcher.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not media_rows(conn):
        time.sleep(0.02)
    thread = watcher._thread
    watcher.stop()

    assert not thread.is_alive()
    (row,) = media_rows(conn)
    assert row["orig_name"] == "was-already-there.mp3"
    assert len(job_rows(conn)) == 1
    assert "watchdog" in capsys.readouterr().err


def test_a_folder_added_while_the_app_runs_is_watched_at_the_next_tick(conn, watcher, inbox, tmp_path):
    """`poll_interval` is what makes this true: every tick reconciles the
    observer's schedules against the enabled rows, so Settings does not need
    a restart to take effect."""
    second = tmp_path / "second"
    second.mkdir()
    observer = FakeObserver()

    watching.add_folder(conn, inbox, TranscribeOptions())
    watcher._sync_schedules(observer, conn)
    assert observer.paths() == {os.path.normcase(str(inbox))}

    watching.add_folder(conn, second, TranscribeOptions())
    watcher._sync_schedules(observer, conn)
    assert observer.paths() == {os.path.normcase(str(inbox)), os.path.normcase(str(second))}

    # ... and a folder switched off, or removed, stops being watched.
    (row, _) = watching.folders(conn, enabled_only=False)
    watching.set_enabled(conn, row["id"], False)
    watcher._sync_schedules(observer, conn)
    assert observer.paths() == {os.path.normcase(str(second))}

    watcher._sync_schedules(observer, conn)  # nothing changed: no churn
    assert observer.paths() == {os.path.normcase(str(second))}


def test_a_folder_that_is_not_there_is_not_scheduled(conn, watcher, tmp_path):
    watching.add_folder(conn, tmp_path / "unplugged-drive", TranscribeOptions())
    observer = FakeObserver()

    watcher._sync_schedules(observer, conn)

    assert observer.paths() == set()


def test_one_folder_that_cannot_be_scheduled_does_not_take_the_others_with_it(
    conn, watcher, inbox, tmp_path
):
    """The refusing folder is registered first, and that is the test.

    The scheduling loop had one try/except around the whole of it - one folder
    that raised ended the tick, so every folder ordered after it got no
    filesystem events for the life of the process while the settings page went
    on showing it as On. Registered second, the same broken code would schedule
    the good folder before it ever reached the bad one and prove nothing.
    """
    second = tmp_path / "second"
    second.mkdir()
    watching.add_folder(conn, inbox, TranscribeOptions())
    watching.add_folder(conn, second, TranscribeOptions())
    observer = RefusingObserver(inbox)

    watcher._sync_schedules(observer, conn)

    assert observer.paths() == {os.path.normcase(str(second))}
    assert len(observer.attempts) == 2, "the second folder was never even tried"


def test_a_folder_that_cannot_be_scheduled_is_said_once_rather_than_every_tick(
    conn, watcher, inbox, capsys
):
    """Every tick is every two seconds: a folder on a drive that went away
    filled stderr with the same traceback all day, which is how a log stops
    being read at all. Said once, and again only when something changes."""
    watching.add_folder(conn, inbox, TranscribeOptions())
    observer = RefusingObserver(inbox)

    watcher._sync_schedules(observer, conn)
    first = capsys.readouterr().err
    for _ in range(3):
        watcher._sync_schedules(observer, conn)

    assert str(inbox) in first
    assert capsys.readouterr().err == "", "the same failure was printed on every tick"
    assert watcher.cannot_watch(inbox) is True

    # The drive comes back. Every tick retries, so it is watched again - and it
    # stops being flagged, or the settings page would carry a stale warning for
    # the life of the process.
    observer.refuse = ""
    watcher._sync_schedules(observer, conn)

    assert observer.paths() == {os.path.normcase(str(inbox))}
    assert watcher.cannot_watch(inbox) is False


# --- the browse roots go on deciding ---------------------------------------------------------
#
# The roots are the one control for "what this app may read". They were checked
# when a folder was added and never again, so narrowing them stopped the
# transcribe dialog and POST /api/media and left the watcher ingesting from a
# folder both of those had started refusing.


def test_a_folder_the_browse_roots_no_longer_cover_is_not_read_from(
    conn, watcher, watched, tmp_path
):
    """Both doors: the startup walk and the tick. Neither had ever asked."""
    drop(watched, "private.mp3")
    elsewhere = tmp_path / "allowed"
    elsewhere.mkdir()
    narrow_roots(conn, elsewhere)

    assert watcher.reconcile(conn) == 0

    watcher.notice(watched / "private.mp3")
    watcher.pump(conn, now=1000.0)

    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []


def test_a_folder_outside_the_roots_is_not_scheduled_for_events(conn, watcher, inbox, tmp_path):
    """And the observer stops being pointed at it, rather than delivering
    events into a pump that then declines every one of them."""
    other = tmp_path / "allowed"
    other.mkdir()
    watching.add_folder(conn, inbox, TranscribeOptions())
    narrow_roots(conn, other)
    observer = FakeObserver()

    watcher._sync_schedules(observer, conn)

    assert observer.paths() == set()


def test_a_folder_on_a_drive_that_is_not_there_is_missing_and_not_disallowed(conn, tmp_path):
    """Two different answers, and the settings page says different things about
    them. `is_allowed` resolves a path before it compares it, and a path that
    is not there resolves to itself - so "not plugged in" must not come back as
    "outside the folders this app may read"."""
    watching.add_folder(conn, tmp_path / "unplugged-drive", TranscribeOptions())

    (row,) = watching.folders(conn)

    assert row["allowed"] is True
    assert not Path(row["path"]).is_dir()


# --- the settings page ------------------------------------------------------------------------------


def test_the_settings_page_lists_the_watched_folders(client, conn, inbox):
    watching.add_folder(conn, inbox, TranscribeOptions(language="nl", tier="max"))

    body = client.get("/settings").text

    assert 'id="watch-folders"' in body
    assert str(inbox) in body
    assert 'action="/settings/watch"' in body


def render_watch_section(**folder) -> str:
    """`_settings_watch.html` with one folder row, rendered on its own.

    Through the app's own environment (`scribe.web.templates`), so autoescaping
    and the undefined policy are the page's rather than this test's. The keys
    the section marks a row with are optional - `watch_context` is what fills
    them in - which is exactly why both halves of each test below are here: the
    badge appears when the key is set and stays away when it is not.
    """
    from scribe.web import templates

    row = {
        "id": 1,
        "path": r"C:\Users\someone\Recordings",
        "enabled": True,
        "missing": False,
        "summary": "Auto-detect, Turbo, speakers recognised",
        **folder,
    }
    return templates.get_template("_settings_watch.html").render(
        watch_folders=[row],
        quiesce_seconds=int(watching.QUIESCE_SECONDS),
        watch_options=TranscribeOptions(),
        languages=transcribe.LANGUAGE_CHOICES,
        flash=None,
        oob=False,
    )


def test_the_watch_section_marks_a_folder_the_browse_roots_no_longer_cover():
    """`missing` had a badge and these two had none, so a folder this app had
    quietly stopped reading from looked exactly like one nothing was dropped
    into. Same treatment, next to the path, where the user is looking."""
    assert "out-of-roots" in render_watch_section(outside_roots=True)
    assert "out-of-roots" not in render_watch_section()


def test_the_watch_section_marks_a_folder_the_observer_refused():
    """Still walked at every start, but no live events - and "On" said
    otherwise."""
    assert "not-watching" in render_watch_section(unwatchable=True)
    assert "not-watching" not in render_watch_section()


def test_adding_a_watch_folder_stores_it_enabled_with_its_options(client, conn, inbox):
    resp = client.post(
        "/settings/watch",
        data={"path": str(inbox), "language": "nl", "tier": "max", "diarize": "0"},
        headers=HX,
    )

    assert resp.status_code == 200
    (row,) = watching.folders(conn)
    assert Path(row["path"]) == inbox
    assert row["enabled"] == 1
    assert row["options"].language == "nl"
    assert row["options"].tier == "max"
    assert row["options"].diarize is False


def test_a_plain_add_lands_on_the_watch_card(client, conn, inbox):
    """TASK-077: the 303 went to /settings#watch-folders, a fragment inside a
    card the radio had not opened, so the person who pressed 'Watch this
    folder' saw the Defaults form and no sign of their folder."""
    resp = client.post("/settings/watch", data={"path": str(inbox)}, follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings?section=watch#watch-folders"


def test_a_folder_outside_the_allowed_roots_is_refused(client, conn, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with db.LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            (fsbrowse.SETTING_KEY, str(allowed)),
        )
        conn.commit()

    resp = client.post("/settings/watch", data={"path": str(elsewhere)}, headers=HX)

    assert resp.status_code == 403
    assert "Settings" in resp.json()["detail"]
    assert watching.folders(conn, enabled_only=False) == []


def test_a_watch_folder_that_is_not_a_directory_is_refused(client, conn, inbox):
    a_file = drop(inbox, "not-a-folder.mp3")

    for path in (str(a_file), str(inbox / "does-not-exist"), "  ", "relative/path"):
        resp = client.post("/settings/watch", data={"path": path}, headers=HX)
        assert resp.status_code == 400, path

    assert watching.folders(conn, enabled_only=False) == []


def test_the_apps_own_data_directory_cannot_be_watched(client, conn, data_dir):
    resp = client.post("/settings/watch", data={"path": str(data_dir)}, headers=HX)

    assert resp.status_code == 400
    assert watching.folders(conn, enabled_only=False) == []


def test_the_same_folder_cannot_be_added_twice(client, conn, inbox):
    client.post("/settings/watch", data={"path": str(inbox)}, headers=HX)

    resp = client.post("/settings/watch", data={"path": str(inbox)}, headers=HX)

    assert resp.status_code == 409
    assert len(watching.folders(conn, enabled_only=False)) == 1


def test_a_watch_folder_can_be_switched_off_and_removed(client, conn, inbox):
    watching.add_folder(conn, inbox, TranscribeOptions())
    (row,) = watching.folders(conn)

    off = client.post(f"/settings/watch/{row['id']}", data={"enabled": "0"}, headers=HX)
    assert off.status_code == 200
    assert watching.folders(conn) == []
    assert len(watching.folders(conn, enabled_only=False)) == 1

    on = client.post(f"/settings/watch/{row['id']}", data={"enabled": "1"}, headers=HX)
    assert on.status_code == 200
    assert len(watching.folders(conn)) == 1

    gone = client.post(f"/settings/watch/{row['id']}/delete", headers=HX)
    assert gone.status_code == 200
    assert watching.folders(conn, enabled_only=False) == []


def test_touching_a_watch_folder_that_is_not_there_is_a_404(client):
    assert client.post("/settings/watch/42", data={"enabled": "0"}, headers=HX).status_code == 404
    assert client.post("/settings/watch/42/delete", headers=HX).status_code == 404


# --- the lifespan -----------------------------------------------------------------------------------


def test_the_app_starts_no_watcher_when_it_starts_no_supervisor(db_path, data_dir, monkeypatch):
    """`--no-supervisor` exists so a second instance can be run against the
    same data without disturbing the first. A second watcher over the same
    folders would be a second ingest, so it follows the same switch."""
    built: list = []
    monkeypatch.setattr(watching, "Watcher", lambda *a, **kw: built.append((a, kw)))

    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1"):
        assert app.state.watcher is None
    assert built == []


def test_the_app_starts_and_stops_the_watcher_when_asked_to(db_path, data_dir, monkeypatch):
    class FakeWatcher:
        def __init__(self, db_path):
            self.db_path = db_path
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    built: list[FakeWatcher] = []
    monkeypatch.setattr(
        watching, "Watcher", lambda db_path: built.append(FakeWatcher(db_path)) or built[-1]
    )

    app = create_app(db_path=db_path, start_supervisor=False, start_watcher=True)
    with TestClient(app, base_url="http://127.0.0.1"):
        (watcher,) = built
        assert app.state.watcher is watcher
        assert watcher.started and not watcher.stopped
    assert watcher.stopped


def test_startup_sweeps_the_recording_sessions_a_previous_life_abandoned(db_path, data_dir):
    """`recording.sweep` had no caller until this task's lifespan change; its
    docstring named this spot. Proven here rather than trusted."""
    conn = db.connect(db_path)
    session = recording.start(conn)
    conn.close()
    directory = recording.session_dir(session)
    (directory / "000000.webm").write_bytes(b"chunk")
    old = time.time() - 2 * recording.SWEEP_AFTER_SECONDS
    for child in list(directory.iterdir()) + [directory]:
        os.utime(child, (old, old))

    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1"):
        pass

    assert not directory.exists()


# --- the badges reach the real page, not only the template ----------------------------


def test_the_settings_page_marks_a_folder_the_browse_roots_no_longer_cover(
    client, conn, inbox, tmp_path
):
    """render_watch_section proves the template; this proves the wiring.

    The two tests above hand a synthetic dict straight to the template, so they
    stay green whether or not anything ever fills those keys - which is exactly
    what happened: `folders()` knew the folder was out of the roots and
    `watch_context` dropped the fact on the floor. This asks the page.
    """
    client.post("/settings/watch", data={"path": str(inbox)}, headers=HX)
    assert "out-of-roots" not in client.get("/settings").text

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with db.LOCK:  # narrow the roots so the watched folder falls outside them
        conn.execute(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            (fsbrowse.SETTING_KEY, str(elsewhere)),
        )
        conn.commit()

    assert "out-of-roots" in client.get("/settings").text


def test_the_settings_page_marks_a_folder_the_observer_refused(client, conn, inbox):
    """The stderr line says "It is marked on the Settings page". This is what
    makes that sentence true."""
    client.post("/settings/watch", data={"path": str(inbox)}, headers=HX)
    assert "not-watching" not in client.get("/settings").text

    class RefusesEverything:
        def cannot_watch(self, path):
            return True

    client.app.state.watcher = RefusesEverything()
    try:
        assert "not-watching" in client.get("/settings").text
    finally:
        client.app.state.watcher = None


def test_the_page_still_renders_when_no_watcher_is_running(client, conn, inbox):
    """`python -m scribe --no-supervisor` starts no watcher, and the settings
    page must not be the thing that notices."""
    client.post("/settings/watch", data={"path": str(inbox)}, headers=HX)
    client.app.state.watcher = None

    body = client.get("/settings").text
    assert "not-watching" not in body  # unknown is not the same as refused
    assert str(inbox.name) in body


def test_a_file_that_leaves_the_roots_through_a_junction_is_not_taken_in(
    conn, watcher, watched, tmp_path
):
    """The folder gate held; the file gate leaked, and only on one of the two doors.

    `fsbrowse.is_allowed` says in its own docstring that "a junction into
    another drive [is] not a way in". `pump` honoured that - `folder_for`
    returning None means this file is not inside anything we may read, and it
    does `continue`. `reconcile` wrote `folder_for(path, watched) or folder`,
    so the None was replaced by the folder the walk started from and the file
    was ingested anyway. os.walk does not follow symlinks but does descend
    directory junctions, which `mklink /J` creates without administrator
    rights, so this was reachable by anyone who could write inside a watched
    folder. Both doors are asked the same question now.
    """
    outside = tmp_path / "outside-the-roots"
    outside.mkdir()
    drop(outside, "confidential.mp3", b"not yours to read" * 64)  # settled, or
    junction = watched / "shortcut"  # the quiescence gate would defer it and
    # this test would pass without ever reaching the decision it is about
    # A junction is a Windows thing: os.walk does not follow a POSIX symlink,
    # so there is no equivalent door to test elsewhere, and `cmd` does not
    # exist there to ask - the skip has to come before the call, not after.
    if sys.platform != "win32":
        pytest.skip("directory junctions are Windows-only; os.walk does not follow symlinks")
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    if made.returncode != 0:  # junctions unavailable here
        pytest.skip(f"could not create a junction: {made.stderr.strip() or made.stdout.strip()}")

    assert any(
        p.name == "confidential.mp3" for p in watching._walk(watched)
    ), "the walk did not descend the junction, so this test proves nothing"

    assert watcher.reconcile(conn) == 0
    assert media_rows(conn) == []

    # And the tick agrees, as it already did - pinned so the two cannot drift.
    watcher.notice(junction / "confidential.mp3")
    watcher.pump(conn, now=1000.0)
    assert watcher.pump(conn, now=1010.0) == []
    assert media_rows(conn) == []


@pytest.mark.parametrize(
    "payload",
    [
        '{"files": []}',  # a list where a mapping is expected
        '{"files": 5}',
        '{"files": "x"}',
        '{"files": {"k": "not-a-triple"}}',
        '{"files": {"k": [1, 2]}}',  # too few members to unpack
        "[]",  # valid json, wrong shape at the top
    ],
)
def test_a_taken_cache_of_the_wrong_shape_still_leaves_the_watcher_working(
    conn, db_path, watched, payload
):
    """The test above covers a file that is not JSON. These are files that are.

    `load_cache`'s docstring already says raising here "would cost a watcher
    that does not start", and it was worse than that: `Watcher.__init__` runs
    inside the FastAPI lifespan before it yields, so an AttributeError escaping
    this - which `{"files": []}` produces, because a list has no `.items` -
    took the whole web process down with it. No routes at all, over a cache.
    """
    watching.cache_path().parent.mkdir(parents=True, exist_ok=True)
    watching.cache_path().write_text(payload, encoding="utf-8")
    drop(watched, "still-works.mp3")

    assert watching.load_cache() == {}

    watcher = watching.Watcher(db_path)
    assert watcher.reconcile(conn) == 1


def test_a_taken_cache_from_another_format_version_is_ignored_rather_than_read(
    conn, db_path, watched
):
    """CACHE_VERSION was written and never read back, so the field that exists
    to make a format change safe did nothing. The first change of shape would
    not have cost the "one full pass of hashing" the docstring promises."""
    watching.cache_path().parent.mkdir(parents=True, exist_ok=True)
    watching.cache_path().write_text(
        json.dumps({"version": watching.CACHE_VERSION + 1, "files": {"k": [1, 2.0, "sha"]}}),
        encoding="utf-8",
    )

    assert watching.load_cache() == {}

    # And the current version is still read, or the guard would be a way of
    # throwing the cache away on every start.
    watching.cache_path().write_text(
        json.dumps({"version": watching.CACHE_VERSION, "files": {"k": [1, 2.0, "sha"]}}),
        encoding="utf-8",
    )
    assert set(watching.load_cache()) == {"k"}


def test_no_observer_at_all_marks_every_folder_rather_than_none_of_them(
    client, conn, db_path, inbox
):
    """The per-folder badge covered the smaller failure and missed the larger.

    `_note_unschedulable` is the only writer of `_unschedulable`, and `_loop`
    calls `_sync_schedules` only when there IS an observer. So when
    `_open_observer` returns None - watchdog missing, or Observer() raising -
    no folder was ever recorded, `cannot_watch` said False for all of them, and
    the settings page showed every row as On with no badge. That is the state
    where NOTHING gets live events, said less loudly than the state where one
    folder does not. `_open_observer`'s own docstring ends "a degraded mode
    nobody can see is a lie"; its only statement was on stderr.
    """
    client.post("/settings/watch", data={"path": str(inbox)}, headers=HX)

    watcher = watching.Watcher(db_path, observer_factory=lambda: (_ for _ in ()).throw(OSError("no watchdog here")))
    watcher.start()
    try:
        deadline = time.time() + 5
        while watcher._observer_live is None and time.time() < deadline:
            time.sleep(0.02)
        assert watcher._observer_live is False, "the loop never reported the observer's absence"
        assert watcher.cannot_watch(inbox) is True

        client.app.state.watcher = watcher
        assert "not-watching" in client.get("/settings").text
    finally:
        client.app.state.watcher = None
        watcher.stop()


def test_a_watcher_that_has_not_started_claims_nothing_about_live_watches(db_path, inbox):
    """Unknown is not the same as refused - the same distinction
    `watch_context` already makes for a watcher that is None."""
    watcher = watching.Watcher(db_path)

    assert watcher._observer_live is None
    assert watcher.cannot_watch(inbox) is False


def test_the_bound_and_the_persistence_hold_together_not_only_apart(
    conn, db_path, watched, monkeypatch
):
    """Two tests proved these separately and neither noticed they cancelled out.

    `_walk` yields sorted paths and eviction is oldest-first, so a folder
    larger than the bound ended a pass holding the LAST n entries in sort
    order - and the next pass starts at the FIRST, misses every one, and evicts
    exactly what it is about to need. Measured with the bound at 10: ten files
    re-hashed nothing, eleven re-hashed everything. Not a gradient, a cliff, and
    the module docstring's promise that a restart does not re-read the archive
    is false on the far side of it.
    """
    monkeypatch.setattr(watching, "TAKEN_MEMORY", 10)
    for i in range(watching.TAKEN_MEMORY + 5):
        drop(watched, f"clip{i:04d}.mp3", b"x" * 64 + str(i).encode())

    first = watching.Watcher(db_path)
    assert first.reconcile(conn) == watching.TAKEN_MEMORY + 5
    first._save_taken()

    hashed: list[Path] = []
    real = media.hash_file
    monkeypatch.setattr(media, "hash_file", lambda p, *a, **k: (hashed.append(p), real(p, *a, **k))[1])

    watching.Watcher(db_path).reconcile(conn)

    # A slope, not a cliff: the five that did not fit are re-hashed, the ten
    # that did are not. Before the fix all fifteen were, because eviction on
    # insert threw away the entries the next pass would ask for first.
    assert len(hashed) == 5, (
        f"{len(hashed)} of {watching.TAKEN_MEMORY + 5} files were hashed again on the "
        "second pass; past its bound the cache should degrade by the overflow, "
        "not stop working altogether"
    )


def test_the_swapped_watch_section_carries_the_badges_too(client, conn, inbox, tmp_path):
    """The page is not the path a user takes; the fragment is.

    Every other badge test asks GET /settings or hands a synthetic dict to the
    template. But the section is swapped in place after every add, toggle and
    remove, and that answer comes from `_watch_answer`, which threads the
    watcher separately. Dropping it there was invisible to the whole suite -
    96 tests passed with the badge gone from the fragment - while the same
    mutation on `page_context` went red at once. This asks the fragment.
    """
    client.post("/settings/watch", data={"path": str(inbox)}, headers=HX)
    (row,) = watching.folders(conn, enabled_only=False)

    class RefusesEverything:
        def cannot_watch(self, path):
            return True

    client.app.state.watcher = RefusesEverything()
    try:
        # Toggling is the cheapest of the three routes that re-render it.
        fragment = client.post(f"/settings/watch/{row['id']}", data={}, headers=HX).text
        assert "not-watching" in fragment, "the swapped section lost the badge the page shows"

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        with db.LOCK:
            conn.execute(
                "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
                (fsbrowse.SETTING_KEY, str(elsewhere)),
            )
            conn.commit()
        fragment = client.post(f"/settings/watch/{row['id']}", data={}, headers=HX).text
        assert "out-of-roots" in fragment
    finally:
        client.app.state.watcher = None


def test_trimming_the_cache_is_linear_rather_than_quadratic(monkeypatch):
    """`taken.pop(next(iter(taken)))` in a loop rescans the dict's entry array
    past every tombstone the previous pop left, so trimming is O(n^2).

    It was unreachable while eviction ran on insert - the dict never grew past
    the bound. Moving the trim to the way out (which is what stopped the bound
    being a cliff) made it reachable: a pass over a folder larger than the bound
    now leaves exactly that oversized dict for `_trim_taken` to cut down.
    Measured 2026-09-04: 200,000 entries down to 100,000 took 11.0 s by popping
    and 0.047 s by slicing. Inside `Watcher.__init__` that is startup, and
    `load_cache` runs there before the lifespan yields.

    Timed rather than counted because the shape is the point, and asserted with
    a wide margin so a slow machine does not fail it - quadratic loses by more
    than two orders of magnitude at this size, not by a factor of three.
    """
    monkeypatch.setattr(watching, "TAKEN_MEMORY", 20_000)
    watcher = watching.Watcher.__new__(watching.Watcher)
    watcher._lock = threading.RLock()
    watcher._taken = {
        f"k{i}": watching._Taken(1, 1.0, "sha") for i in range(watching.TAKEN_MEMORY * 3)
    }

    started = time.perf_counter()
    watcher._trim_taken()
    elapsed = time.perf_counter() - started

    assert len(watcher._taken) == watching.TAKEN_MEMORY
    assert elapsed < 1.0, (
        f"trimming 60,000 entries to 20,000 took {elapsed:.2f}s; that is the "
        "quadratic pop-one-at-a-time loop, not a slice"
    )


def test_a_long_reconcile_writes_its_measurements_before_it_finishes(
    conn, db_path, watched, monkeypatch
):
    """A hard kill during the first pass over a large archive used to throw
    away everything the pass had measured, and the next start read it all
    again - which is the one case the cache exists for."""
    monkeypatch.setattr(watching, "SAVE_EVERY", 5)
    for i in range(12):
        drop(watched, f"clip{i:04d}.mp3", b"x" * 64 + str(i).encode())

    saves: list[int] = []
    watcher = watching.Watcher(db_path)
    real = watcher._save_taken
    monkeypatch.setattr(
        watcher, "_save_taken", lambda: (saves.append(len(watcher._taken)), real())[1]
    )

    watcher.reconcile(conn)

    assert len(saves) >= 2, f"only {len(saves)} saves during a 12-file pass; nothing checkpointed"
    assert saves[0] > 0, "the first checkpoint wrote an empty cache"
