import sqlite3

import pytest

from scribe import db

EXPECTED_TABLES = {
    "folder",
    "media",
    "run",
    "segment",
    "word",
    "speaker_label",
    "speaker_embedding",
    "job",
    "job_event",
    "stage_perf",
    "llm_output",
    "chat_message",
    "vocab",
    "export_preset",
    "setting",
    "watch_folder",
    "word_correction",
    "segment_fts",
    "label",
    "media_label",
}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def _seed_media_and_run(conn) -> int:
    """Insert a media row and a run row; return the run id."""
    cur = conn.execute(
        "INSERT INTO media(sha256, store_path, orig_name, title, size_bytes, created_at)"
        " VALUES ('deadbeef', 'media/de/deadbeef.wav', 'a.wav', 'a', 1, 0.0)"
    )
    media_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO run(media_id, model, compute_type, created_at)"
        " VALUES (?, 'large-v3-turbo', 'float16', 0.0)",
        (media_id,),
    )
    return cur.lastrowid


def test_migrate_creates_schema_and_is_idempotent(conn):
    db.migrate(conn)
    db.migrate(conn)  # second run must be a no-op
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert EXPECTED_TABLES <= names


def _indexes(conn) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }


def test_schema_v2_indexes_the_per_media_lookups(conn):
    """The library page correlates the latest job and the current run per
    media row; without an index on job.media_id and run.media_id each of
    those is a scan per row - the N+1 shape without the N+1 queries."""
    db.migrate(conn)

    assert db.SCHEMA_VERSION >= 2
    assert {"idx_job_media", "idx_run_media"} <= _indexes(conn)
    plan = " ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM job WHERE media_id=? ORDER BY id DESC LIMIT 1", (1,)
        )
    )
    assert "idx_job_media" in plan
    plan = " ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT model FROM run WHERE media_id=? AND is_current=1", (1,)
        )
    )
    assert "idx_run_media" in plan


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_schema_v3_adds_the_private_pin_to_media_and_folder(conn):
    """Phase 5's private mode is a column, not a convention: `llm.privacy`
    refuses to send a pinned media's text to a non-local provider, and it reads
    these two flags to decide."""
    db.migrate(conn)

    assert db.SCHEMA_VERSION >= 3
    assert "private" in _columns(conn, "media")
    assert "private" in _columns(conn, "folder")


def test_migrate_walks_a_v2_database_up_to_the_private_pin(tmp_path):
    """A library from a Phase 1-4 build is at user_version 2. It gains the pin
    on the next start, and every file it already holds reads as public - the
    state it was actually in, rather than a promise nobody made."""
    path = tmp_path / "v2.db"
    old = db.connect(path)
    old.executescript(db._MIGRATIONS[0])
    old.executescript(db._MIGRATIONS[1])
    old.execute("PRAGMA user_version = 2")
    old.commit()
    _seed_media_and_run(old)
    old.execute("INSERT INTO folder(name) VALUES ('Meetings')")
    old.commit()
    assert "private" not in _columns(old, "media")

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert old.execute("SELECT private FROM media").fetchone()["private"] == 0
    assert old.execute("SELECT private FROM folder").fetchone()["private"] == 0
    old.close()


def test_schema_v4_widens_llm_output_for_the_llm_job(conn):
    """Phase 5 Task 4 stores what a model said *and* what it cost, keyed by the
    transcript it read: `run_id` because a re-transcription is different words,
    the two token counts because a stored answer has a price, and `params_json`
    because a chunk row has to say which part of the recording it covers."""
    db.migrate(conn)

    assert db.SCHEMA_VERSION >= 4
    assert {"run_id", "prompt_tokens", "completion_tokens", "params_json"} <= _columns(
        conn, "llm_output"
    )
    assert "idx_llm_output_media_kind" in _indexes(conn)
    plan = " ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM llm_output WHERE media_id=? AND kind=?", (1, "summary")
        )
    )
    assert "idx_llm_output_media_kind" in plan


def test_migrate_walks_a_v3_database_up_to_the_wider_llm_output(tmp_path):
    """A library that already has the private pin keeps every output row it
    holds; the new columns arrive empty, which is the truth about a row written
    before anybody counted tokens."""
    path = tmp_path / "v3.db"
    old = db.connect(path)
    for script in db._MIGRATIONS[:3]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 3")
    old.commit()
    _seed_media_and_run(old)
    old.execute(
        "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version, content,"
        " created_at) VALUES (1, 'summary', 'openrouter', 'm', '1', 'the old summary', 0.0)"
    )
    old.commit()
    assert "run_id" not in _columns(old, "llm_output")

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = old.execute("SELECT * FROM llm_output").fetchone()
    assert row["content"] == "the old summary"
    assert row["run_id"] is None
    assert row["prompt_tokens"] is None
    assert row["params_json"] == "{}"
    old.close()


def test_schema_v5_adds_the_chat_transcript(conn):
    """Phase 5 Task 5 keeps a conversation per recording: the question, the
    answer, and the timestamps the answer cited so the panel can render them as
    seek links without asking a model to find them again."""
    db.migrate(conn)

    assert db.SCHEMA_VERSION >= 5
    assert {"id", "media_id", "role", "content", "citations_json", "created_at"} <= _columns(
        conn, "chat_message"
    )
    assert "idx_chat_message_media" in _indexes(conn)
    plan = " ".join(
        row[3]
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT content FROM chat_message WHERE media_id=? ORDER BY id",
            (1,),
        )
    )
    assert "idx_chat_message_media" in plan


def test_a_chat_message_needs_a_role_the_readers_know(conn):
    """Every reader of this table switches on `role`; a third value would be
    rendered by none of them. A closed vocabulary says so at write time."""
    db.migrate(conn)
    _seed_media_and_run(conn)

    conn.execute(
        "INSERT INTO chat_message(media_id, role, content, created_at)"
        " VALUES (1, 'user', 'is there tea?', 0.0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO chat_message(media_id, role, content, created_at)"
            " VALUES (1, 'vogon', 'listen to this poem', 0.0)"
        )


def test_deleting_a_media_takes_its_chat_with_it(conn):
    """The conversation is about the recording; keeping it after the recording
    is gone would leave the questions on a library page with no answer to."""
    db.migrate(conn)
    _seed_media_and_run(conn)
    conn.execute(
        "INSERT INTO chat_message(media_id, role, content, created_at)"
        " VALUES (1, 'user', 'is there tea?', 0.0)"
    )

    conn.execute("DELETE FROM media WHERE id=1")

    assert conn.execute("SELECT COUNT(*) FROM chat_message").fetchone()[0] == 0


def test_migrate_walks_a_v4_database_up_to_the_chat_transcript(tmp_path):
    """A library from a build that had the six preset outputs but no chat gains
    the table and keeps its outputs."""
    path = tmp_path / "v4.db"
    old = db.connect(path)
    for script in db._MIGRATIONS[:4]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 4")
    old.commit()
    _seed_media_and_run(old)
    old.execute(
        "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version, content,"
        " created_at) VALUES (1, 'summary', 'openrouter', 'm', '1', 'the old summary', 0.0)"
    )
    old.commit()
    assert "chat_message" not in {
        row["name"] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert old.execute("SELECT COUNT(*) FROM chat_message").fetchone()[0] == 0
    assert old.execute("SELECT content FROM llm_output").fetchone()[0] == "the old summary"
    old.close()


def test_schema_v9_tells_a_text_edit_apart_from_a_speaker_reassignment(conn):
    """Two flags because they answer two questions, and only one of them is a
    statement about what a word *says*. `edited_by_user` is written by
    /media/{id}/words/reassign and means a person chose this word's speaker;
    `text_edited_by_user` means a person retyped its text, which is what the
    glossary's window builder must refuse to correct over."""
    db.migrate(conn)

    assert db.SCHEMA_VERSION >= 9
    assert {"edited_by_user", "text_edited_by_user"} <= _columns(conn, "word")


def test_migrate_walks_a_v8_database_up_to_the_text_edit_flag(tmp_path):
    """A library that has had a speaker range reassigned carries
    `edited_by_user = 1` on those words. The migration adds the new column and
    leaves that one alone: the reassignment happened, the row is the record of
    it, and nothing in it was ever a claim about the word's spelling. Every
    word reads as text nobody has retyped - which is true of every word in
    every database, because no route edits word text.
    """
    path = tmp_path / "v8.db"
    old = db.connect(path)
    for script in db._MIGRATIONS[:8]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 8")
    old.commit()
    run_id = _seed_media_and_run(old)
    old.execute(
        "INSERT INTO word(run_id, idx, start, end, text, speaker, edited_by_user)"
        " VALUES (?, 0, 0.0, 0.5, ' Vermulen', 'USER_1', 1)",
        (run_id,),
    )
    old.commit()
    assert "text_edited_by_user" not in _columns(old, "word")

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = old.execute("SELECT * FROM word").fetchone()
    assert (row["speaker"], row["edited_by_user"]) == ("USER_1", 1)
    assert row["text_edited_by_user"] == 0
    assert row["text"] == " Vermulen"
    old.close()


def test_migrate_walks_a_v1_database_up_the_ladder(tmp_path):
    """A database a Phase 1-3 build left at user_version 1 gets the new
    indexes on the next start and nothing else changes."""
    path = tmp_path / "old.db"
    old = db.connect(path)
    old.executescript(db._MIGRATIONS[0])
    old.execute("PRAGMA user_version = 1")
    old.commit()
    _seed_media_and_run(old)
    old.commit()
    assert "idx_job_media" not in _indexes(old)

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert {"idx_job_media", "idx_run_media"} <= _indexes(old)
    assert old.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1
    old.close()


def test_wal_and_fk_on(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_job_status_check_constraint_rejects_bogus(conn):
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job(type, status, created_at) VALUES ('fake', 'weird', 0.0)"
        )


def test_fts_triggers_sync(conn):
    db.migrate(conn)
    run_id = _seed_media_and_run(conn)

    cur = conn.execute(
        "INSERT INTO segment(run_id, idx, start, end, text)"
        " VALUES (?, 0, 0.0, 1.0, 'the xyzzyfirst word')",
        (run_id,),
    )
    seg_id = cur.lastrowid

    hits = conn.execute(
        "SELECT rowid FROM segment_fts WHERE segment_fts MATCH 'xyzzyfirst'"
    ).fetchall()
    assert [row["rowid"] for row in hits] == [seg_id]

    conn.execute(
        "UPDATE segment SET text = 'now plughsecond instead' WHERE id = ?", (seg_id,)
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM segment_fts WHERE segment_fts MATCH 'xyzzyfirst'"
        ).fetchone()[0]
        == 0
    )
    hits = conn.execute(
        "SELECT rowid FROM segment_fts WHERE segment_fts MATCH 'plughsecond'"
    ).fetchall()
    assert [row["rowid"] for row in hits] == [seg_id]

    conn.execute("DELETE FROM segment WHERE id = ?", (seg_id,))
    assert (
        conn.execute(
            "SELECT count(*) FROM segment_fts WHERE segment_fts MATCH 'plughsecond'"
        ).fetchone()[0]
        == 0
    )


def test_schema_v10_gives_media_two_nullable_provenance_columns(conn):
    """The feed import (TASK-021) writes where a recording came from, so the
    dialog can say "in library" the next time the feed is listed. Both are
    nullable because an upload, a path, a recording and a watch folder know
    no source."""
    db.migrate(conn)

    assert db.SCHEMA_VERSION >= 10
    assert {"source_url", "source_id"} <= _columns(conn, "media")


def test_migrate_walks_a_v9_database_up_to_the_provenance_columns(tmp_path):
    """A library from before the feed import is at user_version 9. It gains
    the two columns and every recording it already holds reads as "arrived
    without a source", which is the truth: nothing wrote one down."""
    path = tmp_path / "v9.db"
    old = db.connect(path)
    for script in db._MIGRATIONS[:9]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 9")
    old.commit()
    _seed_media_and_run(old)
    assert "source_url" not in _columns(old, "media")

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = old.execute("SELECT source_url, source_id FROM media").fetchone()
    assert (row["source_url"], row["source_id"]) == (None, None)
    old.close()


def test_schema_v11_gives_labels_their_own_table_and_a_link_to_media(conn):
    """TASK-023. A label is a thing a recording *has*, and several recordings
    share one - so a table and a link, not a comma-separated column. `source`
    records who decided: an LLM pass may not quietly overwrite a name a person
    typed, and that rule needs somewhere to read the answer from."""
    db.migrate(conn)

    assert db.SCHEMA_VERSION >= 11
    assert {"id", "name", "created_at"} <= _columns(conn, "label")
    assert {"media_id", "label_id", "source", "created_at"} <= _columns(conn, "media_label")


def test_two_labels_that_differ_only_in_case_are_one_label(conn):
    """The vocabulary is only useful as a filter if it does not fork. A model
    that answers "Hacking" where the library already holds "hacking" must land
    on the row that exists, so the uniqueness is NOCASE rather than exact."""
    db.migrate(conn)
    conn.execute("INSERT INTO label(name, created_at) VALUES ('hacking', 0.0)")

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO label(name, created_at) VALUES ('Hacking', 0.0)")


def test_a_recording_holds_one_row_per_label(conn):
    """Re-running the pass must not double what it already decided."""
    db.migrate(conn)
    media_id = _seed_media_and_run(conn) and conn.execute(
        "SELECT id FROM media"
    ).fetchone()["id"]
    conn.execute("INSERT INTO label(name, created_at) VALUES ('hacking', 0.0)")
    label_id = conn.execute("SELECT id FROM label").fetchone()["id"]
    conn.execute(
        "INSERT INTO media_label(media_id, label_id, source, created_at) VALUES (?,?,'llm',0.0)",
        (media_id, label_id),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO media_label(media_id, label_id, source, created_at)"
            " VALUES (?,?,'human',0.0)",
            (media_id, label_id),
        )


def test_deleting_a_recording_takes_its_label_links_but_not_the_labels(conn):
    """A purged recording must not leave a dangling link, and must not take
    the vocabulary down with it: the label goes on describing the others."""
    db.migrate(conn)
    _seed_media_and_run(conn)
    media_id = conn.execute("SELECT id FROM media").fetchone()["id"]
    conn.execute("INSERT INTO label(name, created_at) VALUES ('hacking', 0.0)")
    label_id = conn.execute("SELECT id FROM label").fetchone()["id"]
    conn.execute(
        "INSERT INTO media_label(media_id, label_id, source, created_at) VALUES (?,?,'llm',0.0)",
        (media_id, label_id),
    )

    conn.execute("DELETE FROM media WHERE id=?", (media_id,))

    assert conn.execute("SELECT COUNT(*) FROM media_label").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM label").fetchone()[0] == 1


def test_migrate_walks_a_v10_database_up_to_the_labels(tmp_path):
    """A library from the feed-import build is at user_version 10. It gains
    two empty tables and loses nothing: every recording it holds reads as
    unlabelled, which is the truth."""
    path = tmp_path / "v10.db"
    old = db.connect(path)
    for script in db._MIGRATIONS[:10]:
        old.executescript(script)
    old.execute("PRAGMA user_version = 10")
    old.commit()
    _seed_media_and_run(old)

    db.migrate(old)

    assert old.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert old.execute("SELECT COUNT(*) FROM label").fetchone()[0] == 0
    assert old.execute("SELECT COUNT(*) FROM media_label").fetchone()[0] == 0
    assert old.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    old.close()
