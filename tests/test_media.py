"""Content-addressed media store and ingest (Phase 2 Task 1).

The store is the one place the original bytes live, so these tests care about
two things above all: the hash is the real sha256 of the file (it is the
identity, the dedupe key and the path), and ingest never damages or moves the
user's source file.
"""

import hashlib
import os

import pytest

from scribe import db, media


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A data dir laid out exactly the way paths.py builds the real one.

    Both the store and the test sources live under tmp_path, on one volume,
    so os.link genuinely succeeds and the hardlink path is really exercised.
    """
    data = tmp_path / "data"
    monkeypatch.setattr(media.paths, "DATA_DIR", data)
    monkeypatch.setattr(media.paths, "MEDIA_DIR", data / "media")
    (data / "media").mkdir(parents=True)
    return data


@pytest.fixture
def src_dir(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    return d


def make_file(directory, name, payload=b"never gonna give you up"):
    f = directory / name
    f.write_bytes(payload)
    return f


class BoundedReader:
    """A stream that refuses to hand over everything at once."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.largest_request = 0

    def read(self, n=-1):
        if n is None or n < 0:
            raise AssertionError("hash_stream asked for the whole file at once")
        self.largest_request = max(self.largest_request, n)
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk


# --- hashing ---------------------------------------------------------------


def test_hash_matches_hashlib(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * (2 * 1024 * 1024) + b"tail")

    assert media.hash_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_hash_stream_reads_in_bounded_chunks():
    payload = b"y" * (3 * 1024 * 1024 + 17)
    reader = BoundedReader(payload)

    digest = media.hash_stream(reader)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert 0 < reader.largest_request <= media.CHUNK_SIZE


def test_hash_stream_can_copy_while_it_hashes(tmp_path):
    payload = b"two birds, one pass" * 1000
    out_path = tmp_path / "copy.bin"

    with open(out_path, "wb") as out:
        digest = media.hash_stream(BoundedReader(payload), sink=out)

    assert out_path.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()


# --- store layout ----------------------------------------------------------


def test_store_path_shards_on_the_first_two_hex_characters(data_dir):
    sha = "a1" + "0" * 62

    path = media.store_path_for(sha, ".wav")

    assert path == data_dir / "media" / "a1" / f"{sha}.wav"


def test_store_path_is_recorded_relative_to_the_data_dir(conn, data_dir, src_dir):
    src = make_file(src_dir, "talk.wav")

    row = media.ingest_path(conn, src)

    sha = row["sha256"]
    assert row["store_path"] == f"media/{sha[:2]}/{sha}.wav"
    assert (data_dir / row["store_path"]).is_file()


# --- ingest_path -----------------------------------------------------------


def test_ingest_dedupes_identical_content(conn, data_dir, src_dir):
    first = media.ingest_path(conn, make_file(src_dir, "one.wav"), title="First")
    second_src = make_file(src_dir, "two.wav")
    store = data_dir / first["store_path"]
    links_before = os.stat(store).st_nlink

    second = media.ingest_path(conn, second_src, title="Second")

    assert first["deduped"] is False
    assert second["deduped"] is True
    assert second["id"] == first["id"]
    assert second["title"] == "First"  # the existing row is returned untouched
    assert conn.execute("SELECT count(*) FROM media").fetchone()[0] == 1
    assert second_src.is_file()  # the duplicate source is left alone
    assert os.stat(store).st_nlink == links_before  # and never linked in


def test_ingest_hardlinks_and_leaves_source_in_place(conn, data_dir, src_dir):
    src = make_file(src_dir, "keep-me.wav")

    row = media.ingest_path(conn, src)

    store = data_dir / row["store_path"]
    assert src.is_file()
    assert store.is_file()
    assert os.path.samefile(src, store)
    assert os.stat(store).st_nlink == 2


def test_ingest_falls_back_to_copy_when_link_fails(conn, data_dir, src_dir, monkeypatch):
    def no_links(*_args, **_kwargs):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(media.os, "link", no_links)
    src = make_file(src_dir, "across-volumes.wav")

    row = media.ingest_path(conn, src)

    store = data_dir / row["store_path"]
    assert store.read_bytes() == src.read_bytes()
    assert not os.path.samefile(src, store)  # a copy, not a link
    assert list(store.parent.glob("*.part")) == []  # no half-written leftovers


def test_ingest_copies_when_linking_is_declined(conn, data_dir, src_dir):
    src = make_file(src_dir, "copy-please.wav")

    row = media.ingest_path(conn, src, link=False)

    store = data_dir / row["store_path"]
    assert store.read_bytes() == src.read_bytes()
    assert not os.path.samefile(src, store)


def test_ingest_records_name_size_title_and_folder(conn, data_dir, src_dir):
    payload = b"z" * 4242
    src = make_file(src_dir, "Board meeting 2026.m4a", payload)
    folder_id = conn.execute(
        "INSERT INTO folder(name) VALUES ('Meetings') RETURNING id"
    ).fetchone()["id"]

    row = media.ingest_path(conn, src, folder_id=folder_id)

    assert row["orig_name"] == "Board meeting 2026.m4a"
    assert row["title"] == "Board meeting 2026"  # defaults to the stem
    assert row["size_bytes"] == len(payload)
    assert row["folder_id"] == folder_id
    assert row["duration"] is None  # the probe stage fills this in
    assert row["created_at"] > 0


def test_ingest_tolerates_a_store_file_left_by_an_earlier_crash(conn, data_dir, src_dir):
    src = make_file(src_dir, "again.wav")
    first = media.ingest_path(conn, src)
    conn.execute("DELETE FROM media")  # row lost, bytes still in the store
    conn.commit()

    row = media.ingest_path(conn, src)

    assert row["deduped"] is False
    assert row["store_path"] == first["store_path"]
    assert (data_dir / row["store_path"]).read_bytes() == src.read_bytes()


@pytest.mark.parametrize(
    "name, payload",
    [("no real extension here", b"a"), ("meeting.2026 final", b"b")],
)
def test_ingest_ignores_an_extension_that_is_not_a_plain_suffix(
    conn, data_dir, src_dir, name, payload
):
    src = make_file(src_dir, name, payload)

    row = media.ingest_path(conn, src)

    assert row["store_path"] == f"media/{row['sha256'][:2]}/{row['sha256']}"
    assert (data_dir / row["store_path"]).is_file()


# --- ingest_stream ---------------------------------------------------------


def test_ingest_stream_lands_in_the_store_and_leaves_no_temp(conn, data_dir):
    payload = b"streamed bytes" * 5000

    row = media.ingest_stream(conn, BoundedReader(payload), "C:\\rec\\Talk.WAV")

    store = data_dir / row["store_path"]
    assert store.read_bytes() == payload
    assert row["sha256"] == hashlib.sha256(payload).hexdigest()
    assert row["orig_name"] == "Talk.WAV"
    assert row["title"] == "Talk"
    assert row["size_bytes"] == len(payload)
    assert store.suffix == ".wav"
    assert list((data_dir / "media" / ".incoming").glob("*")) == []


def test_ingest_stream_dedupes_and_cleans_up_its_temp(conn, data_dir, src_dir):
    payload = b"already known"
    first = media.ingest_path(conn, make_file(src_dir, "known.wav", payload))

    second = media.ingest_stream(conn, BoundedReader(payload), "known-again.wav")

    assert second["deduped"] is True
    assert second["id"] == first["id"]
    assert conn.execute("SELECT count(*) FROM media").fetchone()[0] == 1
    assert list((data_dir / "media" / ".incoming").glob("*")) == []


def test_ingest_stream_cleans_up_when_the_stream_breaks(conn, data_dir):
    class UploadDied(Exception):
        pass

    class Broken:
        def read(self, n=-1):
            raise UploadDied("connection dropped mid-flight")

    with pytest.raises(UploadDied):
        media.ingest_stream(conn, Broken(), "half.wav")

    assert list((data_dir / "media" / ".incoming").glob("*")) == []
    assert conn.execute("SELECT count(*) FROM media").fetchone()[0] == 0


# --- the private-mode default (Phase 5 Task 6) ---------------------------------------


def set_default(conn, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
        (media.PRIVATE_DEFAULT_SETTING, value),
    )
    conn.commit()


def test_a_new_recording_is_not_private_when_nothing_says_otherwise(conn, data_dir, src_dir):
    row = media.ingest_path(conn, make_file(src_dir, "open.wav"))

    assert media.private_default(conn) is False
    assert row["private"] == 0


def test_a_new_recording_starts_pinned_when_the_default_says_so(conn, data_dir, src_dir):
    """The setting the AI settings page writes. Without this read it would be a
    checkbox that remembers itself and changes nothing."""
    set_default(conn, "1")

    row = media.ingest_path(conn, make_file(src_dir, "quiet.wav"))

    assert media.private_default(conn) is True
    assert row["private"] == 1


def test_an_uploaded_recording_starts_pinned_too(conn, data_dir):
    """Both doors into the store, because a default that only covers one is a
    default that leaks on the other."""
    set_default(conn, "1")

    row = media.ingest_stream(conn, BoundedReader(b"a quiet meeting"), "quiet.m4a")

    assert row["private"] == 1


def test_turning_the_default_on_does_not_pin_what_is_already_in_the_library(
    conn, data_dir, src_dir
):
    """Ingest is where the flag is chosen, and the dedupe short-circuit returns
    the existing row untouched. So the setting says what *new* recordings start
    as; it is not a library-wide switch, and the settings page says so."""
    first = media.ingest_path(conn, make_file(src_dir, "old.wav"))
    set_default(conn, "1")

    again = media.ingest_path(conn, make_file(src_dir, "old-again.wav"))

    assert again["id"] == first["id"] and again["deduped"] is True
    assert again["private"] == 0


@pytest.mark.parametrize("value,expected", [("1", True), ("on", True), ("true", True),
                                           ("0", False), ("", False), ("no", False)])
def test_the_default_reads_the_same_words_as_every_other_checkbox(conn, value, expected):
    set_default(conn, value)

    assert media.private_default(conn) is expected
