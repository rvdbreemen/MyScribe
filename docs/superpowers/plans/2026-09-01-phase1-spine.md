# MyScribe — Phase 1: Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The job system that everything else hangs on: pinned venv, SQLite schema, queue with atomic claims, supervisor thread, per-job runner child, startup reconciliation, and a doctor — proven by tests against scripted fake runners, ending with a verified GPU smoke test.

**Architecture:** One FastAPI process owns the DB and a supervisor thread; the supervisor claims jobs from SQLite (BEGIN IMMEDIATE + UPDATE…RETURNING) and spawns `python -m scribe.runner <job_id>` children. Words-canonical schema created up front. No pipeline stages yet beyond fakes — the real stages are Phase 2.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, sqlite3 (stdlib, WAL, FTS5), pytest. GPU deps (torch cu12x, faster-whisper 1.2.1, ctranslate2 ≥4.8.2, pyannote.audio 4.x) enter only in Task 8.

**Spec:** `docs/superpowers/specs/2026-09-01-myscribe-design.md`

## Global Constraints

- Windows 11 native, Python 3.12, venv at `.venv/` in repo root. No Docker, no WSL2, no Node/build toolchain.
- SQLite ≥ 3.35 (stdlib sqlite3 in CPython 3.12 ships 3.45 — assert at startup).
- One DB file `data/myscribe.db` (named `scribe.db` until 2026-09-06; an old file is adopted on start), WAL mode. Media bytes never in the DB. `data/` is gitignored.
- App binds 127.0.0.1:4242 only.
- Package name `scribe`; entry `python -m scribe`; runner entry `python -m scribe.runner <job_id>`; doctor `python -m scribe.doctor`.
- Job statuses exactly: `queued, running, done, failed, cancelled, interrupted` (CHECK constraint). Terminal: done/failed/cancelled. First verdict wins.
- Stage names for transcribe jobs exactly: `probe, prepare, enhance, transcribe, diarize, attribute, finalize`.
- Every file write of an artifact: temp file + `os.replace` (atomic).
- All timestamps in DB: UTC unix epoch floats (`time.time()`). Hashes: sha256 hex.
- Commit after every task with a passing test suite; message style `feat(scope): …` / `test(scope): …`; end with the session attribution trailer.
- TDD: write the failing test first in every task. Run `python -m pytest -q` from repo root; keep it green.

## File Structure (end state of Phase 1)

```
scribe/
  __init__.py            # __version__
  __main__.py            # arg parse → uvicorn app + supervisor start
  paths.py               # repo-relative data dirs, created on import of app
  db.py                  # connect(), migrations, RLock, schema; no business logic
  jobs.py                # enqueue/claim/finish/cancel/events/stage_perf/reconcile
  supervisor.py          # thread: claim loop, spawn runner, watch, kill
  runner.py              # child entry: stage loop over a registry, events out
  stages_fake.py         # scripted fake stages for tests (sleep/fail/hang/progress)
  doctor.py              # env + DB + (Task 8) GPU smoke
  app.py                 # FastAPI: /health, /api/jobs, /api/jobs/{id}, cancel/retry
tests/
  conftest.py            # tmp DB fixture, fake-runner helpers
  test_db.py  test_jobs.py  test_supervisor.py  test_runner.py  test_app.py
requirements.txt         # core (no GPU); requirements-gpu.txt after Task 8
```

---

### Task 1: Package skeleton, paths, core requirements

**Files:**
- Create: `scribe/__init__.py`, `scribe/paths.py`, `requirements.txt`, `tests/conftest.py`, `tests/test_paths.py`, `pytest.ini`

**Interfaces:**
- Produces: `paths.DATA_DIR`, `paths.DB_PATH`, `paths.MEDIA_DIR`, `paths.LOGS_DIR` (all `pathlib.Path`), `paths.ensure_dirs()`.

- [ ] **Step 1: venv + deps**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install fastapi uvicorn jinja2 pytest httpx
.venv\Scripts\python -m pip freeze > requirements.txt
```

- [ ] **Step 2: failing test**

```python
# tests/test_paths.py
from scribe import paths

def test_dirs_are_under_repo_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIBE_DATA_DIR", str(tmp_path / "d"))
    import importlib; importlib.reload(paths)
    paths.ensure_dirs()
    assert paths.DB_PATH.parent == paths.DATA_DIR
    assert paths.MEDIA_DIR.is_dir() and paths.LOGS_DIR.is_dir()
```

- [ ] **Step 3: implement**

```python
# scribe/paths.py
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SCRIBE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "myscribe.db"
MEDIA_DIR = DATA_DIR / "media"
LOGS_DIR = DATA_DIR / "logs"

def ensure_dirs() -> None:
    for p in (DATA_DIR, MEDIA_DIR, LOGS_DIR):
        p.mkdir(parents=True, exist_ok=True)
```

`scribe/__init__.py`: `__version__ = "0.1.0"`. `pytest.ini`: `[pytest]\ntestpaths = tests`.

- [ ] **Step 4: run** `.venv\Scripts\python -m pytest -q` → PASS
- [ ] **Step 5: commit** `feat(scribe): package skeleton, data paths, core deps`

---

### Task 2: db.py — connection, migrations, schema v1, FTS

**Files:**
- Create: `scribe/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `paths.DB_PATH`.
- Produces: `db.connect(path=None) -> sqlite3.Connection` (row_factory=Row, WAL, foreign_keys ON, `check_same_thread=False`); module-level `db.LOCK` (threading.RLock — the only lock in the app, imported elsewhere, never re-created); `db.migrate(conn)` (PRAGMA user_version ladder); `db.SCHEMA_VERSION`.

Schema v1 DDL (exact tables; all `id INTEGER PRIMARY KEY`):

```sql
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
```

- [ ] **Step 1: failing tests** — `test_migrate_creates_schema_and_is_idempotent` (migrate twice, `PRAGMA user_version` == SCHEMA_VERSION, all table names present); `test_wal_and_fk_on`; `test_job_status_check_constraint_rejects_bogus` (INSERT status='weird' raises IntegrityError); `test_fts_triggers_sync` (insert/update/delete a segment row; `SELECT rowid FROM segment_fts WHERE segment_fts MATCH 'unique_word'` follows).
- [ ] **Step 2: run** → FAIL (module missing)
- [ ] **Step 3: implement** `connect()` sets `journal_mode=WAL, synchronous=NORMAL, foreign_keys=ON, busy_timeout=5000`; `migrate()` applies `_MIGRATIONS: list[str]` guarded by `user_version`; assert `sqlite3.sqlite_version_info >= (3,35,0)`.
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(db): schema v1 with words-canonical tables, FTS5 triggers, WAL`

---

### Task 3: jobs.py — enqueue, claim, finish (first-verdict-wins), events

**Files:**
- Create: `scribe/jobs.py`, `tests/test_jobs.py`

**Interfaces:**
- Consumes: `db.connect`, `db.LOCK`.
- Produces (exact signatures; all take `conn` first):
  - `enqueue(conn, type_: str, media_id: int | None = None, params: dict | None = None, priority: int = 0, retry_of: int | None = None) -> int`
  - `claim_next(conn, mode: str = "gpu") -> dict | None` — atomic: `BEGIN IMMEDIATE; UPDATE job SET status='running', started_at=? , pid=NULL WHERE id=(SELECT id FROM job WHERE status='queued' ORDER BY priority DESC, id LIMIT 1) RETURNING *`. `mode="cpu-prework"` variant selects `status='queued' AND cpu_prework_done=0` and does NOT flip status (returns row for prework; sets `cpu_prework_done=1` when told via `mark_prework_done(conn, job_id)`).
  - `finish(conn, job_id: int, status: str, error_code: str | None = None, error_detail: str | None = None) -> bool` — first verdict wins: `UPDATE job SET status=?, finished_at=?, error_code=?, error_detail=? WHERE id=? AND status IN ('running','queued')`; returns rowcount==1.
  - `request_cancel(conn, job_id) -> None` (sets flag; if still queued → finish cancelled immediately)
  - `emit(conn, job_id: int, kind: str, **payload) -> int` (next seq = `COALESCE(MAX(seq),0)+1` under LOCK), `events_after(conn, job_id, after_seq: int) -> list[dict]`
  - `set_stage(conn, job_id, stage: str, progress: float) -> None`
  - `record_stage_perf(conn, stage, model, media_duration, wall_seconds)` and `eta_seconds(conn, stage, model, media_duration) -> float | None` (rolling median over last 5 rows of wall/duration ratio × duration)
  - `queue_position(conn, job_id) -> int | None`
- [ ] **Step 1: failing tests** — claim returns the queued job and flips status exactly once when two threads race (spawn 8 threads claiming from 4 queued jobs → exactly 4 successful claims, no duplicates); `finish` twice → second returns False and status keeps first verdict; cancel on queued → cancelled instantly; events get monotonically increasing seq per job under concurrent emit; `eta_seconds` returns None with no history and ~duration×ratio with seeded rows; `queue_position` counts earlier queued jobs of same-or-higher priority.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** (each write wrapped in `with db.LOCK:`; cross-process safety comes from the SQL itself)
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(jobs): atomic queue with first-verdict-wins and event log`

---

### Task 4: runner.py — child entry with stage registry + fake stages

**Files:**
- Create: `scribe/runner.py`, `scribe/stages_fake.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `jobs.emit/set_stage/finish`, `db.connect`.
- Produces: `runner.main(argv) -> int` (exit 0 done, 1 failed, 2 cancelled); `runner.STAGES: dict[str, list[tuple[str, callable]]]` mapping job type → ordered `(stage_name, fn)`; each stage fn signature `fn(ctx) -> None` with `ctx = RunnerContext(conn, job: dict, params: dict, report: Callable[[float], None], cancelled: Callable[[], bool])`. `report(p)` throttles `set_stage` writes to ≥0.4 s apart. Between stages the runner re-reads `cancel_requested`; a set flag → finish cancelled, exit 2. Uncaught exception → `emit('error', trace=…)`, finish failed with `error_code` from exception class map, exit 1. Registry for job type `"fake"` comes from `stages_fake.py`: `ok` (three stages, progress ticks), `boom` (stage 2 raises RuntimeError), `slow` (sleeps 10 s in 0.1 s slices, honouring `cancelled()`), driven by `params["scenario"]`.
- [ ] **Step 1: failing tests** — run `main` in-process against a tmp DB: scenario ok → status done, stages advanced in order, ≥3 events, stage_perf rows written per stage; boom → failed with error_code `RUNTIME` and traceback event; slow + cancel flag set mid-run → cancelled, exit 2.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(runner): stage-loop child with cooperative cancel and scripted fakes`

---

### Task 5: supervisor.py — claim loop, spawn, watch, kill, reconcile

**Files:**
- Create: `scribe/supervisor.py`, `tests/test_supervisor.py`

**Interfaces:**
- Consumes: `jobs.claim_next/finish/request_cancel`, `db`.
- Produces: `class Supervisor(db_path, poll_interval=1.0, runner_cmd=None)` with `.start()` (daemon thread), `.stop()`, and module fn `reconcile(conn) -> int` (orphaned `running` with dead/absent pid → `interrupted`; returns count). Loop: claim → `subprocess.Popen([sys.executable, "-m", "scribe.runner", str(id)], creationflags=CREATE_NO_WINDOW)` → store pid on job row → poll child; if `cancel_requested` and child alive past 10 s grace → `terminate()` then `kill()`; child exit without verdict → safety-net `finish(failed, error_code='RUNNER_DIED')`. `runner_cmd` override lets tests spawn a scripted fake runner script.
- [ ] **Step 1: failing tests** — with `runner_cmd` pointing at a tiny script (writes verdict then exits): job goes queued→running→done; script that exits(1) without verdict → failed/RUNNER_DIED; script that sleeps → request_cancel → process killed within grace+2 s and job cancelled; `reconcile` flips a fabricated running-row-with-dead-pid to interrupted.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** (psutil-free: `pid_alive` via `os.kill(pid, 0)` try/except on POSIX and `ctypes OpenProcess` on Windows — implement Windows branch, that is the platform)
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(supervisor): spawn-and-watch loop with kill grace and startup reconciliation`

---

### Task 6: app.py + __main__ — FastAPI shell and JSON job API

**Files:**
- Create: `scribe/app.py`, `scribe/__main__.py`, `tests/test_app.py`

**Interfaces:**
- Produces routes: `GET /health` → `{"ok": true, "version": …}`; `GET /api/jobs?status=` → job rows (+`queue_position`, `eta_seconds` for queued/running); `GET /api/jobs/{id}` → row + last 100 events; `POST /api/jobs/{id}/cancel`; `POST /api/jobs/{id}/retry` → new job with `retry_of`, copies params; `GET /api/jobs/{id}/events?after=` (JSON tail — SSE upgrade comes with the UI phase). Lifespan: `paths.ensure_dirs()`, `db.connect+migrate`, `reconcile()`, `Supervisor.start()`; app.state.conn shared. `__main__`: argparse `--port 4242 --no-supervisor`, uvicorn on 127.0.0.1 only.
- [ ] **Step 1: failing tests** — httpx TestClient (supervisor off): health ok; enqueue fake job via jobs.enqueue → listed; cancel on queued → cancelled; retry on a failed job → new queued row with retry_of set; events tail returns after-seq slice.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(app): FastAPI shell with job API, reconcile-on-boot, supervisor lifecycle`

---

### Task 7: doctor.py (CPU part) — environment gate

**Files:**
- Create: `scribe/doctor.py`, `tests/test_doctor.py`

**Interfaces:**
- Produces: `doctor.checks() -> list[Check]` with `Check(name, ok, detail, fix_hint)`; `python -m scribe.doctor` prints a table, exit 0 iff all non-optional pass. CPU checks: python ≥3.12; sqlite ≥3.35 + FTS5 compiled in (`SELECT fts5(?1)` probe via creating a temp vtab); ffmpeg and ffprobe on PATH with versions; data dir writable + free disk ≥ configurable floor (default 10 GB); DB migrates cleanly to SCHEMA_VERSION. GPU checks registered but marked `optional=True` until Task 8 wires them.
- [ ] **Step 1: failing test** — `checks()` returns all-ok on the dev machine for CPU set; ffmpeg check parses a version string; disk-floor check fails when floor is set above free space (monkeypatch `shutil.disk_usage`).
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(doctor): environment checks as the install gate`

---

### Task 8: GPU dependency pin + doctor GPU smoke (phase gate)

This task is experimental by design: it establishes and RECORDS the working GPU
pin set for Python 3.12 native Windows. It ends with a real 30 s GPU
transcription or an explicit, documented fallback.

**Files:**
- Create: `requirements-gpu.txt`, `scribe/cuda_setup.py`, `tests/test_cuda_setup.py` (unit-only), fixture `tests/fixtures/clip30.wav` (copy from scratchpad probe clip or re-cut with ffmpeg from any WHYcast episode)
- Modify: `scribe/doctor.py` (wire GPU checks: torch cuda available; DLL resolution; model cache present; 30 s transcribe with timing → `stage_perf`)

**Interfaces:**
- Produces: `cuda_setup.ensure_cuda_libs() -> list[str]` — `os.add_dll_directory(<site-packages>/torch/lib)` + PATH prepend (port of WHYcast `whycast/cuda_setup.py:64-97`, adapted: torch/lib first, nvidia-* wheel dirs second), idempotent, returns dirs added; MUST run before any `import ctranslate2`/`faster_whisper`.

- [ ] **Step 1: attempt pin ladder, record outcome.** Install in `.venv`, newest first; stop at the first fully working rung and freeze it:
  1. `torch==2.8.*+cu128 torchaudio==2.8.*` (index `https://download.pytorch.org/whl/cu128`) + `faster-whisper==1.2.1 ctranslate2>=4.8.2 pyannote.audio==4.0.*`
  2. same but `cu126` / torch 2.7.*
  3. WHYcast's proven recipe: torch 2.3.1+cu118 + `nvidia-cublas-cu12` + faster-whisper 1.1.1 + pyannote.audio 3.3.2 (known-good on this exact machine)
  Known traps to check per rung (from research, cite in comments): torch ≥2.6 `weights_only` default breaks pyannote <4.0.3; `platform_machine=='x86_64'` markers silently route Windows (`AMD64`) to CPU torch — verify `torch.version.cuda` is not None; ctranslate2 needs cuDNN 9 DLLs, which torch/lib supplies.
- [ ] **Step 2: failing test (unit)** — `ensure_cuda_libs()` returns ≥1 existing dir and is idempotent (second call adds nothing); does not raise when torch absent (returns []).
- [ ] **Step 3: wire doctor GPU checks** — `gpu_smoke()`: load pinned default model (`large-v3-turbo` if present in cache else `large-v3`), transcribe `clip30.wav` with `word_timestamps=True`, assert >10 words, write wall time to `stage_perf(stage='transcribe', model=…)`. Mark check non-optional now.
- [ ] **Step 4: run doctor on the real machine** `.venv\Scripts\python -m scribe.doctor` → all green, note xRT in output. `python -m pytest -q` stays green (GPU tests live behind `-m gpu` marker, excluded by default).
- [ ] **Step 5: freeze + document** — `pip freeze > requirements-gpu.txt`; append the chosen rung + why to the plan file under this task; commit `feat(gpu): pinned CUDA stack with doctor smoke test` (include requirements-gpu.txt and, if rung 3 was needed, a note in the spec's §1).

---

## Self-review (done at authoring time)

- Spec coverage of Phase-1 slice: §1 (processes: Tasks 4-6), §2 (schema: Task 2; claim/verdict: Task 3), §7 (reconcile/cancel/verdict: Tasks 3-5; taxonomy starts with RUNTIME/RUNNER_DIED, extended in Phase 2), §8 (fake-runner state-machine tests: Tasks 4-5; doctor: 7-8). Pipeline stages, UI, exports, LLM: Phases 2+, deliberately absent here.
- Types consistent: `conn` first arg throughout; job dicts are sqlite3.Row→dict.
- No placeholders; every step has code or an exact command.
