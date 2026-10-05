"""SQLite connection, migrations, and the schema (v1 tables, v2 indexes) for scribe.

No business logic lives here. jobs.py and friends build on these
primitives: one shared connection in the web process guarded by LOCK,
a fresh connection per runner child, and cross-process safety from the
SQL itself (BEGIN IMMEDIATE claims, CHECK-constrained status).
"""

import sqlite3
import threading
from pathlib import Path

from scribe import paths

# The single in-process lock guarding the shared connection.
# Imported everywhere else, never re-created.
LOCK = threading.RLock()

SCHEMA_VERSION = 18

_SCHEMA_V1 = """
CREATE TABLE folder(id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER REFERENCES folder(id) ON DELETE CASCADE);
CREATE TABLE media(
  id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, store_path TEXT NOT NULL,
  orig_name TEXT NOT NULL, title TEXT NOT NULL, folder_id INTEGER REFERENCES folder(id) ON DELETE SET NULL,
  duration REAL, size_bytes INTEGER NOT NULL, created_at REAL NOT NULL, trashed_at REAL);
CREATE TABLE run(
  id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  engine TEXT NOT NULL DEFAULT 'faster-whisper', model TEXT NOT NULL, compute_type TEXT NOT NULL,
  language TEXT, task TEXT NOT NULL DEFAULT 'transcribe', params_json TEXT NOT NULL DEFAULT '{}',
  xrt REAL, is_current INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE segment(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL, start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL,
  avg_logprob REAL, no_speech_prob REAL, compression_ratio REAL, temperature REAL);
CREATE TABLE word(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL, start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL,
  probability REAL, speaker TEXT, edited_by_user INTEGER NOT NULL DEFAULT 0);
CREATE TABLE speaker_label(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  cluster_label TEXT NOT NULL, display_name TEXT NOT NULL, color TEXT,
  UNIQUE(run_id, cluster_label));
CREATE TABLE speaker_embedding(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  cluster_label TEXT NOT NULL, embedding BLOB NOT NULL, UNIQUE(run_id, cluster_label));
CREATE TABLE job(
  id INTEGER PRIMARY KEY, type TEXT NOT NULL, media_id INTEGER REFERENCES media(id) ON DELETE CASCADE,
  run_id INTEGER REFERENCES run(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed','cancelled','interrupted')),
  stage TEXT, stage_progress REAL NOT NULL DEFAULT 0, cpu_prework_done INTEGER NOT NULL DEFAULT 0,
  params_json TEXT NOT NULL DEFAULT '{}', error_code TEXT, error_detail TEXT,
  pid INTEGER, retry_of INTEGER REFERENCES job(id) ON DELETE SET NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL, priority INTEGER NOT NULL DEFAULT 0);
CREATE TABLE job_event(
  id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(job_id, seq));
CREATE TABLE stage_perf(
  id INTEGER PRIMARY KEY, stage TEXT NOT NULL, model TEXT, media_duration REAL NOT NULL,
  wall_seconds REAL NOT NULL, created_at REAL NOT NULL);
CREATE TABLE llm_output(
  id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  content TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE vocab(id INTEGER PRIMARY KEY, term TEXT NOT NULL UNIQUE, weight REAL NOT NULL DEFAULT 1.0, variants_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE export_preset(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, options_json TEXT NOT NULL);
CREATE TABLE setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE watch_folder(id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1);
CREATE VIRTUAL TABLE segment_fts USING fts5(text, content='segment', content_rowid='id', tokenize='unicode61');
CREATE TRIGGER segment_ai AFTER INSERT ON segment BEGIN
  INSERT INTO segment_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER segment_ad AFTER DELETE ON segment BEGIN
  INSERT INTO segment_fts(segment_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TRIGGER segment_au AFTER UPDATE ON segment BEGIN
  INSERT INTO segment_fts(segment_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO segment_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE INDEX idx_job_claim ON job(status, priority DESC, id) WHERE status='queued';
CREATE INDEX idx_word_run ON word(run_id, idx);
CREATE INDEX idx_segment_run ON segment(run_id, idx);
CREATE INDEX idx_media_folder ON media(folder_id) WHERE trashed_at IS NULL;
"""

# v2: the library page finds each media row's latest job and current run
# with correlated subqueries on media_id; without these the two lookups are a
# scan of job and run per row shown.
_SCHEMA_V2 = """
CREATE INDEX idx_job_media ON job(media_id, id);
CREATE INDEX idx_run_media ON run(media_id, is_current);
"""

# v3: the private-mode pin. A media is private when its own flag is set or any
# folder above it has one (scribe/llm/privacy.py), and a private media's text is
# refused to every non-local provider. Two columns rather than a settings row
# because the pin has to be a property of the file, not of the app: moving a
# recording into a pinned folder must pin it, and reading the flag has to be one
# query on the row already in hand. DEFAULT 0 - an existing library is what it
# always was, neither silently pinned nor silently exposed.
_SCHEMA_V3 = """
ALTER TABLE media ADD COLUMN private INTEGER NOT NULL DEFAULT 0;
ALTER TABLE folder ADD COLUMN private INTEGER NOT NULL DEFAULT 0;
"""

# v4: what an llm_output row has to be able to say for itself. The v1 table
# recorded what a model wrote and under which key; the `llm` job type (Phase 5
# Task 4) also needs to know what it read, what it cost and which part of the
# recording it covers.
#
# * `run_id` - the transcript the answer was made from. A re-transcription with
#   a better model is different words, so an output made before it is about
#   something that no longer exists. It is also half the resume key: chunk 3 of
#   a re-transcribed recording is not chunk 3 of the old one, because the
#   chunker's seams move with the segments. ON DELETE SET NULL rather than
#   CASCADE - losing a run must not silently delete the answers about it.
# * the two token counts - a stored answer has a price, and the row is the only
#   place it survives. NULL, never 0, when the provider reported none.
# * `params_json` - per-row detail that has no business being a column: the
#   chunk index and the segments a chunk covered, the model that actually
#   served the request when it differs from the one asked for, the custom
#   prompt. Never a key: keys are the five columns above it.
#
# The index is the read the AI panel does on every transcript page: the newest
# row of one kind for one media. Without it that is a scan of every output row
# in the library, per panel, per page load.
_SCHEMA_V4 = """
ALTER TABLE llm_output ADD COLUMN run_id INTEGER REFERENCES run(id) ON DELETE SET NULL;
ALTER TABLE llm_output ADD COLUMN prompt_tokens INTEGER;
ALTER TABLE llm_output ADD COLUMN completion_tokens INTEGER;
ALTER TABLE llm_output ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}';
CREATE INDEX idx_llm_output_media_kind ON llm_output(media_id, kind, id);
"""

# v5: the chat transcript. One row per turn of the conversation about a
# recording (Phase 5 Task 5), rather than one row per exchange, because a turn
# is what is rendered, what is replayed into the next prompt, and what a user
# deletes; a pair packed into one row would have to be unpacked by every reader.
#
# * `role` is a closed vocabulary - every reader switches on it, and a value
#   none of them renders is a row nobody sees. The same reasoning as job.status,
#   and the same CHECK.
# * `citations_json` holds the `[m:ss]` seconds `chat_tool.parse_citations`
#   found and could seek to, so the panel renders links from a list instead of
#   re-parsing prose, and a timestamp past the end of the recording is dropped
#   once, here, rather than by every reader.
# * ON DELETE CASCADE, unlike llm_output's run_id: a conversation is *about* the
#   recording, and questions with nothing left to ask about are not a record of
#   anything.
#
# No run_id: an answer is about the recording as the user is reading it, and a
# re-transcription does not make yesterday's question unasked. The consequence,
# stated rather than hidden: after a re-transcription the citations in an old
# answer may seek a few seconds off. `llm_output` is the table that keys on the
# run, because those answers are regenerated; a conversation is not.
_SCHEMA_V5 = """
CREATE TABLE chat_message(
  id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
  citations_json TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);
CREATE INDEX idx_chat_message_media ON chat_message(media_id, id);
"""

# v6: the log of microphone sessions (Phase 6 Task 3). A recording arrives as
# a stream of five-second chunks written to `WORK_DIR/rec/<session>/`, and this
# row is what the app knows about a session that is not finished yet - which is
# the only kind of session a crash can leave behind.
#
# * `session` is the url-safe token the browser posts its chunks at, and it is
#   UNIQUE because it is the address of a directory. Two rows claiming one
#   directory would be two recordings overwriting each other's chunks.
# * `media_id` is NULL until the chunks have been remuxed and taken into the
#   store, so "started but never became a recording" is a state the row can
#   express - which is what the 24-hour sweep looks for. ON DELETE SET NULL:
#   purging a recording from the library does not unmake the fact that it was
#   recorded here.
# * `chunk_count` and `bytes` are written from what is actually on disk when
#   the session ends, never accumulated as chunks arrive. A chunk POST that
#   died halfway through would otherwise leave the row claiming bytes that
#   were never written.
#
# No `title` or options: those are chosen on the finish form and belong to the
# media row and the job it queues. This table is about the session, and a
# session is over the moment it has produced one or been given up on.
_SCHEMA_V6 = """
CREATE TABLE recording(
  id INTEGER PRIMARY KEY, session TEXT NOT NULL UNIQUE, started_at REAL NOT NULL,
  finished_at REAL, media_id INTEGER REFERENCES media(id) ON DELETE SET NULL,
  chunk_count INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0);
"""

# v7: what a watched folder transcribes with (Phase 6 Task 4). The v1 table
# said which folders to watch and whether each is on; a folder that ingests by
# itself also has to know what to ask for, because nobody is at the dialog when
# the file lands.
#
# The value is `TranscribeOptions.model_dump()` - the *form's* vocabulary
# (language, tier, diarize, speaker counts), not `to_params()`'s. Two reasons,
# and they pull the same way: the settings form renders straight back out of it
# (a tier is a radio button, `TIER_MODELS['max']` is not), and the mapping from
# tier to model name stays where ADR-004 put it, applied at enqueue time, so a
# folder added today still picks up a changed default tomorrow.
#
# DEFAULT '{}' rather than a spelled-out default: an empty object validates to
# exactly `TranscribeOptions()`, so a folder that predates this column watches
# with the app's defaults instead of with whatever they were when it was added.
_SCHEMA_V7 = """
ALTER TABLE watch_folder ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}';
"""

# v8: the glossary's correction layer (Phase 6 Task 5). ADR-003 says words are
# the canonical transcript and that a rename or a reassignment never rewrites
# their text; a glossary that corrected "Vermulen" to "Vermeulen" in place would
# break exactly that, and irreversibly - the original spelling would be gone and
# a term removed from the glossary tomorrow could not be un-applied.
#
# So a correction is a row *over* a word rather than a change *to* one. Both
# readers of words apply it with one LEFT JOIN on (run_id, word_idx), and
# `DELETE FROM word_correction WHERE run_id=?` restores the transcript Whisper
# produced, byte for byte.
#
# * keyed on `word_idx`, not on `word.id` - the same key the stages, the
#   reassignment route and the exporters all address a word by, and the one
#   that survives a run being re-read from anywhere.
# * `original` is that word's stored text at the time the pass ran. It is
#   provenance, not the restore path (the restore path is the DELETE): what it
#   buys is the "was: Vermulen" the transcript view shows, and the ability to
#   see that the pass ran against text that has since changed.
# * `rule` and `confidence` say *why*, because a correction a user disagrees
#   with is one they need to be able to argue with. Two values today, `fuzzy`
#   and `phonetic`; no CHECK, because unlike `job.status` nothing switches on
#   it - it is rendered as itself.
# * UNIQUE(run_id, word_idx) is both the "one correction per word" rule and the
#   index the join reads. ON DELETE CASCADE: a correction of words that no
#   longer exist is not a record of anything.
_SCHEMA_V8 = """
CREATE TABLE word_correction(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  word_idx INTEGER NOT NULL, original TEXT NOT NULL, corrected TEXT NOT NULL,
  rule TEXT NOT NULL, confidence REAL, created_at REAL NOT NULL,
  UNIQUE(run_id, word_idx));
"""

# v9: a word's text being hand-edited is not the same fact as its speaker being
# hand-assigned, and until now one column carried both names.
#
# `edited_by_user` (v1) has exactly one writer, the reassignment route in
# `scribe.web.transcript`: `UPDATE word SET speaker=?, edited_by_user=1 ...`.
# That route changes which cluster a word belongs to and, by ADR-003, never a
# character of its text. But the column's only *reader* was the glossary's
# window builder, which skipped a flagged word on the grounds that "a word a
# person fixed by hand outranks any glossary" - a rule about spelling, applied
# to a flag about speakers.
#
# The consequence was silent and permanent. `glossary.store` replaces a run's
# layer (DELETE, then re-INSERT), so re-running corrections after somebody had
# fixed mislabelled diarization deleted the corrections over that range and
# regenerated none: the names fell back to what Whisper produced, in the view
# and in every export, and the range stayed uncorrectable for good. Nothing was
# lost - `word.text` was never touched - but the layer was wrong.
#
# So the two facts get two columns:
#
# * `edited_by_user` keeps its meaning and its data: a person chose this word's
#   speaker. Nothing reads it today. It is left alone rather than cleared,
#   because the reassignments it records did happen, and none of them was ever
#   a claim about spelling.
# * `text_edited_by_user` is the one the glossary reads: a person retyped this
#   word. No route in this app edits word text, so nothing writes it yet, and
#   DEFAULT 0 makes every existing database say exactly that - which is the
#   truth about all of them, and is what silently repairs a library whose
#   speakers were reassigned before this version.
#
# Whoever adds that text-editing route: this is the column to set, and the
# glossary will then leave those words alone, which is what the guard was for
# all along.
_SCHEMA_V9 = """
ALTER TABLE word ADD COLUMN text_edited_by_user INTEGER NOT NULL DEFAULT 0;
"""

# v10 (TASK-021, the feed import): where a recording came from, when it came
# over the network. `source_url` is the fetched URL without its fragment;
# `source_id` is yt-dlp's extractor-scoped id (`Youtube:<id>`, `Generic:<guid>`
# for a feed item). Both nullable: an upload, a path, a recording and a watch
# folder know no source, and every row from before v10 has none - the truth
# about all of them, and the dedupe path fills a NULL in when the same bytes
# arrive again through a feed. Read only by the transcribe dialog's listing,
# to say "in library" and "queued"; never rendered as a link. No index:
# measured 2026-09-08, the lookup costs 4-11 ms at 1k-10k rows against a
# probe that takes seconds.
_SCHEMA_V10 = """
ALTER TABLE media ADD COLUMN source_url TEXT;
ALTER TABLE media ADD COLUMN source_id TEXT;
"""

# v11 (TASK-023, content labels): what a recording is about, as a thing several
# recordings share rather than a column one recording owns. Hence a table and a
# link table: "show me everything about lockpicking" is then a join instead of a
# scan over text.
#
# Three details carry the weight.
#
# `name` is UNIQUE COLLATE NOCASE because the vocabulary is only useful as a
# filter if it does not fork. The pass hands the model the labels that exist and
# asks it to reuse them; a model that answers "Hacking" where the library holds
# "hacking" means the one that exists, and NOCASE is what makes the insert say
# so instead of quietly creating a second row that splits the count.
#
# The link's primary key is the pair, so re-running the pass over a recording
# cannot double what it already decided. `INSERT OR IGNORE` is then the whole
# idempotency story.
#
# `source` says who decided - 'llm' or 'human' - and exists so an automatic run
# can never overwrite a person's judgement. Without it the only way to protect a
# hand-typed label would be to not re-run at all.
#
# The cascade is deliberately asymmetric: purging a recording drops its links
# but leaves the labels, because the vocabulary goes on describing everything
# else. A label nobody uses any more is not wrong, only unused.
_SCHEMA_V11 = """
CREATE TABLE label(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  created_at REAL NOT NULL);

CREATE TABLE media_label(
  media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  label_id INTEGER NOT NULL REFERENCES label(id) ON DELETE CASCADE,
  source TEXT NOT NULL CHECK(source IN ('llm', 'human')),
  created_at REAL NOT NULL,
  PRIMARY KEY(media_id, label_id));

CREATE INDEX idx_media_label_label ON media_label(label_id);
"""

# v12 (TASK-024, speaker names applied automatically): who decided a speaker's
# name, and what they decided it from.
#
# Until now a speaker_label row was just a name, and that was honest because a
# person had typed every one of them. Once a pass writes them unattended the
# row has to answer two more questions, and both are asked in anger rather than
# in theory.
#
# "Why does this say Jeff Man?" - `llm_output_id` points at the analysis, which
# holds the model, the prompt version, the tokens, the timestamp and the quote
# with its [m:ss]. That is the whole accountability story, and it works because
# tasks.py never overwrites an output row: a re-run adds one and the earlier
# decision survives to be compared against.
#
# "May I overwrite this?" - `source`. An automatic pass must never quietly
# replace a name a person chose. Existing rows default to 'human' because that
# is what every one of them is: nothing else could have written them yet.
#
# ON DELETE SET NULL, not CASCADE. Losing the analysis must lose the receipt,
# never the name - a speaker who was correctly identified does not become
# anonymous because somebody purged an old llm_output row.
#
# `confidence` is what the model claimed, kept beside the name so the threshold
# that let it through is visible afterwards. It is NOT a probability: a model
# answering 95 is answering a question about its own certainty that nothing
# trained it to answer well. It is stored for the audit, not for arithmetic.
_SCHEMA_V12 = """
ALTER TABLE speaker_label ADD COLUMN source TEXT NOT NULL DEFAULT 'human'
  CHECK(source IN ('llm', 'human'));
ALTER TABLE speaker_label ADD COLUMN llm_output_id INTEGER
  REFERENCES llm_output(id) ON DELETE SET NULL;
ALTER TABLE speaker_label ADD COLUMN confidence REAL;
"""

# v13 (TASK-026, the cleaned reading): a second way to read a transcript,
# beside the words rather than instead of them.
#
# ADR-003 makes words canonical and every grouping derived at render time. A
# cleaned transcript is a grouping in that sense - the same recording, read
# differently - so it lives here and the words are never touched. That choice
# is what makes Robert's undo free: refusing a bad cleaning means not writing
# this row, and nothing has to be put back.
#
# Keyed by run, one reading each, because a cleaning is about the words a
# particular run produced. Re-transcribing gives a new run and the old
# reading stays with the old words, which is the truth about both.
# ON DELETE CASCADE: a reading of words that no longer exist is not history,
# it is a claim about a transcript nobody can check.
#
# `llm_output_id` is the receipt - which answer this text came from, with its
# model and prompt version - and SET NULL for the reason speaker_label uses:
# losing the receipt must not delete the reading. `words_in` and `words_out`
# are what the gate measured, kept so a reader can see how much shorter this
# is without counting, and so a refusal and an acceptance are recorded in the
# same units.
_SCHEMA_V13 = """
CREATE TABLE clean_reading(
  run_id INTEGER PRIMARY KEY REFERENCES run(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  llm_output_id INTEGER REFERENCES llm_output(id) ON DELETE SET NULL,
  words_in INTEGER NOT NULL,
  words_out INTEGER NOT NULL,
  created_at REAL NOT NULL);
"""

# v14 (TASK-025, feeds as subscriptions): a feed you started with stays
# watched. ADR-008 revised, 2026-09-09.
#
# One row per source URL. `url` is UNIQUE because a second row for the same
# feed would poll it twice and race itself into queueing every new episode
# twice - the one mistake this table must make impossible.
#
# `checked_at` is the whole scheduling mechanism. Due-ness is compared against
# it rather than counted down in a sleeping thread, because this is a desktop
# app that is off more than it is on: a laptop closed for a week must come back
# and find everything overdue, and a restart must lose nothing. "Check daily"
# and "check at startup" are then the same code rather than two mechanisms
# that can disagree.
#
# `paused` is the per-feed switch, and `failures` with `last_result` are what
# the Feeds page shows: a feed whose probe keeps failing has to say so rather
# than going quiet, which is how a subscription becomes a surprise.
#
# `feed_seen` is how subscribing imports no back catalogue. The episodes on a
# feed the day you subscribe are recorded as seen and never queued; only what
# appears afterwards is new. Without it, following The Daily would queue its
# 2970 episodes at once, which is the opposite of what subscribing means.
#
# It holds source ids rather than URLs because that is what identifies an
# episode across a CDN move (v10), and it is a second line of defence rather
# than the only one: `known_sources` still asks the library and the live jobs,
# so an episode already downloaded is not queued twice even if this table
# somehow lost it.
#
# No cascade to media. A recording keeps its own provenance in media.source_url
# and media.source_id (v10), so unsubscribing from a feed does not disown the
# episodes it brought in - they are still yours, and still say where they came
# from.
_SCHEMA_V14 = """
CREATE TABLE feed(
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL DEFAULT '',
  interval_seconds INTEGER NOT NULL DEFAULT 86400,
  checked_at REAL,
  last_result TEXT NOT NULL DEFAULT '',
  failures INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,
  folder_id INTEGER REFERENCES folder(id) ON DELETE SET NULL,
  created_at REAL NOT NULL);

CREATE INDEX idx_feed_due ON feed(checked_at) WHERE paused = 0;

CREATE TABLE feed_seen(
  feed_id INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL,
  PRIMARY KEY(feed_id, source_id));
"""

_SCHEMA_V15 = """
-- v15 (TASK-044): a feed that is new fetches one episode and then asks.
--
-- NULL means the question is open: the feed was subscribed, its newest
-- episode was queued, and nobody has said yet whether to fetch the rest. The
-- watcher skips such a feed, because polling it would queue the back
-- catalogue the question is about.
--
-- Every feed that exists today was subscribed under the old rule, which
-- recorded the whole listing as seen - their question genuinely is answered,
-- so they are stamped with their own created_at rather than left asking.
-- Without that backfill, upgrading would stop every feed in the library.
ALTER TABLE feed ADD COLUMN backfill_answered_at REAL;
UPDATE feed SET backfill_answered_at = created_at;

-- How many episodes the feed listed when it was subscribed, so the question
-- can say what each choice would cost ("the last 3" of 47) without probing
-- the feed again every time the page renders. Zero for the feeds above: they
-- are answered, so nothing asks.
ALTER TABLE feed ADD COLUMN backfill_total INTEGER NOT NULL DEFAULT 0;
"""


_SCHEMA_V16 = """
-- v16 (TASK-047): the queue gets an order key of its own.
--
-- Until now `id` was the FIFO key: claim_next ordered by priority DESC, id.
-- That is right for a queue nobody touches and wrong the moment a person can
-- move a job, because id cannot change - it is the rowid, the target of
-- job_event.job_id and job.retry_of, the /jobs/{id} URL, and the runner
-- child's argv.
--
-- queue_seq is allocated at enqueue (greater than every queued row) and
-- rewritten when a person moves a job. Three properties, stated here because
-- a reader who does not know them will file the second one as a bug:
--   * enqueue puts a job behind everything queued - FIFO, as before;
--   * a priority change puts a job at the BACK of its new level, so raising
--     the oldest row in the table does not put it in front of jobs queued
--     before it at that level;
--   * front/back within a level is a separate, deliberate action. "Run this
--     next" is therefore two clicks: priority says what class of work this
--     is, position says this one job jumps its own queue.
--
-- The backfill is queue_seq = id, so an existing queue keeps exactly the
-- order it had; nothing is reordered by upgrading. The counter only has to
-- outrank the rows that are queued, so it may be reused once the queue
-- drains - which is what keeps its allocation an index read rather than a
-- scan over every job ever run.
--
-- Not unique, and it must never be made unique: a front-move deliberately
-- hands out a number a row at another priority level already holds. Ordering
-- is within a level; the two never compare.
ALTER TABLE job ADD COLUMN queue_seq INTEGER NOT NULL DEFAULT 0;
UPDATE job SET queue_seq = id;
DROP INDEX IF EXISTS idx_job_claim;
CREATE INDEX idx_job_claim ON job(status, priority DESC, queue_seq) WHERE status='queued';
"""

_SCHEMA_V17 = """
-- v17 (TASK-071): a reading says which words it read.
--
-- clean_reading is keyed by run, and the words under a run move - the
-- glossary pass writes corrections, a reader retypes a word - while the
-- reading stays what it was, and the page said nothing. It is not recomputed:
-- a reading is a paid answer with a receipt, and ADR-003's cache exception is
-- for derivations that cost nothing to make again. So it carries a
-- fingerprint of the corrected words it was made from, and the page compares
-- that with the words as they read now. NULL for the readings from before
-- this column: not knowing what they read is not evidence that the words
-- moved, so they are never called stale.
ALTER TABLE clean_reading ADD COLUMN words_hash TEXT;
"""

_SCHEMA_V18 = """
-- v18 (TASK-107.01): a watch folder takes audio unless video is switched on.
--
-- The outside macOS walk of 0.8.3 watched ~/Downloads and queued 882 jobs,
-- 558 of them .ts files a downloader left behind. A folder added from now on
-- takes audio only by default (`watching.add_folder`), and video is a choice
-- per folder. The column's default is 1 so that every folder watched before
-- this keeps doing exactly what it did: nothing changes under anybody.
ALTER TABLE watch_folder ADD COLUMN include_video INTEGER NOT NULL DEFAULT 1;
"""

# One entry per schema version; _MIGRATIONS[n - 1] migrates to user_version n.
_MIGRATIONS: list[str] = [
    _SCHEMA_V1, _SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4, _SCHEMA_V5, _SCHEMA_V6,
    _SCHEMA_V7, _SCHEMA_V8, _SCHEMA_V9, _SCHEMA_V10, _SCHEMA_V11, _SCHEMA_V12, _SCHEMA_V13, _SCHEMA_V14, _SCHEMA_V15, _SCHEMA_V16,
    _SCHEMA_V17, _SCHEMA_V18,
]


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the scribe database (paths.DB_PATH unless overridden)."""
    assert sqlite3.sqlite_version_info >= (3, 35, 0), (
        f"SQLite >= 3.35 required, found {sqlite3.sqlite_version}"
    )
    if path is None:
        paths.adopt_legacy_db()  # a scribe.db from before the rename, if any
        path = paths.DB_PATH
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Bring the database to SCHEMA_VERSION via the PRAGMA user_version ladder."""
    with LOCK:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for target in range(version + 1, SCHEMA_VERSION + 1):
            conn.executescript(_MIGRATIONS[target - 1])
            conn.execute(f"PRAGMA user_version = {target:d}")
            conn.commit()
