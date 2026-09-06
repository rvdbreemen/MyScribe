# MyScribe — Phase 3: Web UI Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The four screens from the spec, on top of the working pipeline: a library to browse and organize, a transcribe dialog to feed it, a jobs dashboard that shows what the pipeline is doing right now, and a transcript view with a synced player and speaker editing. After this phase the app is usable end to end from a browser.

**Architecture:** Server-rendered Jinja2 pages with htmx (vendored, no CDN, no build step) for partial updates. Live job status by polling a fragment whose own interval is rendered into it; SSE only for the job-detail log tail. All transcript grouping (paragraphs, sentence timestamps) is computed at render time from `word` rows through pure functions in `scribe/render.py` — nothing grouped is ever stored.

**Tech Stack:** FastAPI + Jinja2 (`jinja2` already pinned), htmx 2.0.x (copy `webui/static/htmx.min.js` from WHYcast — MIT, same author's repo), vanilla CSS/JS, `python-multipart` for uploads.

**Spec:** `docs/superpowers/specs/2026-09-01-myscribe-design.md` (section 4; sections 2–3 for the data it renders)

**Depends on:** Phase 2 complete (`b9f0cc3` or later): `POST /api/media`, the job API, and a finished run producing `run/segment/word/speaker_label` rows.

## Global Constraints

- Phase 1 and Phase 2 Global Constraints apply verbatim.
- No CDN, no npm, no bundler. Every asset is a file under `scribe/static/` served by the app. Pages must work with the machine offline.
- Bind 127.0.0.1 only; nothing in this phase adds auth (single user, per spec).
- Every route that mutates does so via `POST` with a form body; every page route is `GET`. htmx requests carry `HX-Request: true`; a route that serves both a full page and a fragment picks by that header.
- Grouping is derived, never stored: `scribe/render.py` functions take `word` rows (+ labels) and return view models. No table for paragraphs, cues, or sentences may be created.
- Poll intervals are rendered into the fragment: 2 s when any job is running or queued, 15 s otherwise. SSE only on the job-detail page.
- Media is served with `Range` support (HTTP 206). Never read a media file into memory to serve it.
- Templates escape by default (Jinja2 autoescape on); transcript text is user content and must never be marked safe.
- Tests use FastAPI's `TestClient` against a temp DB seeded by `tests/seed.py` helpers — no GPU, no models, no real pipeline run in UI tests.

## File Structure (new in Phase 3)

```
scribe/
  render.py               # pure view-model functions over word/segment/label rows
  fsbrowse.py             # server-side path listing for the "pick a file on this machine" flow
  web/                    # routers, one per screen
    __init__.py           # mount all routers, Jinja2 env, static files
    library.py            # /, /search, /folders/*, /media/{id}/(rename|move|trash|restore|download)
    transcribe_dialog.py  # /transcribe (GET form fragment), POST upload + path submit
    jobs_ui.py            # /jobs, /jobs/fragment, /jobs/{id}, /api/jobs/{id}/stream (SSE)
    transcript.py         # /media/{id}, /media/{id}/audio, speaker rename + reassign
    settings.py           # /settings
  templates/
    base.html  library.html  _media_rows.html  _sidebar.html  transcribe_dialog.html
    jobs.html  _jobs_fragment.html  job_detail.html  transcript.html  settings.html
    _macros.html          # timestamp, stage stepper, speaker badge
  static/
    htmx.min.js  app.css  app.js
tests/
  seed.py                 # seed_media(), seed_run(words, segments, labels), seed_job()
  test_render.py  test_web_library.py  test_web_transcribe_dialog.py
  test_web_jobs.py  test_web_transcript.py  test_web_settings.py  test_fsbrowse.py
```

---

### Task 1: Web scaffold — templates, static, `.env`, base page

**Files:**
- Create: `scribe/web/__init__.py`, `scribe/templates/base.html`, `scribe/static/app.css`, `scribe/static/app.js`, `scribe/static/htmx.min.js` (copied from `D:/Users/Robert/Documents/GitHub/RvdB/WHYcast-transcribe/webui/static/htmx.min.js`), `tests/seed.py`, `tests/test_web_scaffold.py`
- Modify: `scribe/app.py` (mount `web.mount(app)`), `scribe/__main__.py` (load `.env`, open browser), `requirements.txt` (`python-multipart`)

**Interfaces:**
- Produces: `web.mount(app: FastAPI) -> None` (StaticFiles at `/static`, Jinja2 env with autoescape, routers included); `web.templates` (the `Jinja2Templates` instance, `render(request, name, **ctx)` helper that adds `version`, `nav`, `now`); `scribe.env.load_dotenv(path=REPO/.env) -> dict` — a 15-line parser (KEY=VALUE, `#` comments, optional quotes) that sets `os.environ` only for keys not already set; called from `__main__` and `create_app`.
- `tests/seed.py`: `seed_media(conn, *, title="Clip", duration=30.0, folder_id=None) -> int`; `seed_run(conn, media_id, *, words: list[dict] | None = None, segments=None, labels: dict[str, str] | None = None, current=True) -> int` (defaults: 40 words across two speakers `SPEAKER_00/SPEAKER_01`, 4 segments); `seed_job(conn, media_id, status="running", stage="transcribe", progress=0.4) -> int`.
- Base page: top nav (Library · Jobs · Settings), a `<main>` block, a flash region, htmx loaded, `app.js` with one delegated `submit` listener that turns `data-confirm` forms into a `confirm()` and posts via fetch (WHYcast's pattern).

- [ ] **Step 1: failing tests** — `GET /` returns 200 HTML containing the nav and `htmx.min.js`; `GET /static/htmx.min.js` 200 with `text/javascript`; `load_dotenv` sets a missing key, leaves an existing one alone, ignores comments and quotes; seed helpers produce rows that satisfy the schema (one test round-trips `seed_run` and counts words/segments/labels).
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): scaffold with vendored htmx, base template, .env loading and seed helpers`

---

### Task 2: `render.py` — the pure view models

**Files:**
- Create: `scribe/render.py`, `tests/test_render.py`

**Interfaces:**
- Produces:
  - `paragraphs(words: Sequence[dict], labels: Mapping[str, str] | None = None, *, gap: float = 2.0, max_chars: int = 700) -> list[Paragraph]` — `Paragraph(speaker: str | None, display_name: str | None, start, end, sentences: list[Sentence])`; break on speaker change, on a silence gap ≥ `gap` s, or when the paragraph passes `max_chars`. `Sentence(start, end, text, words: list[dict])` breaks at sentence-final punctuation (`.?!…`) followed by whitespace, or at a paragraph break.
  - `confidence_band(probability: float | None) -> str` — `"high"` ≥ 0.85, `"mid"` ≥ 0.6, else `"low"`; `None` → `"unknown"`.
  - `format_ts(seconds: float, style: str = "clock") -> str` — `clock` → `m:ss` under an hour, `h:mm:ss` above; `srt` → `hh:mm:ss,mmm`.
  - `speaker_display(labels, cluster) -> str` — display name or `"Speaker N"` derived from `SPEAKER_NN`.
  - `join_text(words) -> str` — faster-whisper words carry their leading space; join without inserting extra ones, strip once.
- [ ] **Step 1: failing tests** — paragraph splits on speaker change; splits on a 2.5 s gap within one speaker; does not split on 1.9 s; splits after `max_chars`; sentence split on `.` followed by space, not on `3.14`; leading-space join yields no double spaces; `format_ts(65)=="1:05"`, `format_ts(3661)=="1:01:01"`, `format_ts(1.5,"srt")=="00:00:01,500"`; `speaker_display({}, "SPEAKER_03") == "Speaker 4"`; confidence bands at the boundaries (0.85 → high, 0.849 → mid).
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** (pure functions, no DB, no imports from scribe.web)
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(render): derived paragraphs, sentences and timestamps over canonical words`

---

### Task 3: Library page — sidebar, table, search, folders, file actions

**Files:**
- Create: `scribe/web/library.py`, `scribe/templates/library.html`, `_sidebar.html`, `_media_rows.html`, `_macros.html`, `tests/test_web_library.py`
- Modify: `scribe/db.py` — add migration v2: `INSERT INTO setting` defaults? No. v2 adds nothing structural; skip unless a column is missing. (Trash uses `media.trashed_at`, views are predicates in code for v1.)

**Interfaces:**
- Routes:
  - `GET /` and `GET /?view=recent|uncategorized|trash&folder=<id>&sort=<key>&q=<text>` — full page; with `HX-Request` returns `_media_rows.html` only. Sort keys whitelisted: `created_at`, `title`, `duration`.
  - `GET /search?q=` — FTS5 over `segment_fts` joined to the current run and media: rows of (media title, snippet via `snippet(segment_fts, 0, '<mark>', '</mark>', '…', 12)`, start time, `/media/{id}#t=<start>` link), ordered by `bm25`. Empty `q` → redirect `/`.
  - `POST /folders` (name, parent_id) · `POST /folders/{id}/rename` · `POST /folders/{id}/delete` (refuses when non-empty unless `force=1`, which moves contents to the parent)
  - `POST /media/{id}/rename` (title only — the on-disk path never changes) · `POST /media/{id}/move` (folder_id or empty for Uncategorized) · `POST /media/{id}/trash` · `POST /media/{id}/restore` · `POST /media/{id}/purge` (deletes the media row; the store file is removed only when no other row shares the sha256 — dedupe means it cannot)
  - `POST /media/bulk` (ids[], action ∈ move|trash|restore|retranscribe) — retranscribe enqueues a job per id with the media's last params.
  - `GET /media/{id}/download` — `FileResponse` of the original, `Content-Disposition: attachment` with the original filename, Range supported by Starlette.
- Sidebar: Recent (last 20 by created_at), Uncategorized (folder_id IS NULL AND trashed_at IS NULL), Trash, then the folder tree rendered from one `WITH RECURSIVE` query with depth for indentation; counts per folder from a single `GROUP BY folder_id`.
- Table columns: checkbox · title (link to `/media/{id}`) · uploaded (`created_at` formatted) · duration (`format_ts`) · mode (🐬/🐋 from the current run's model; empty when none) · status (latest job status badge; `done` shows ✓) · ⋯ menu (Open · Export (disabled until Phase 4) · Rename · Move · Download audio · Trash).
- [ ] **Step 1: failing tests** — seeded 3 media in 2 folders + 1 trashed: `/` lists 3 and not the trashed one; `?view=trash` lists exactly the trashed one; `?folder=` filters; `HX-Request` returns rows only (no `<html`); `/search?q=<word from seed>` returns a row whose link ends with `#t=<segment start>`; rename changes title and not `store_path`; move to a folder and back to Uncategorized; trash then restore round-trips `trashed_at`; bulk move of two ids; folder delete refuses when non-empty and moves contents with `force=1`; download responds 200 with `attachment; filename="..."` and 206 to a `Range: bytes=0-9` request with `Content-Length: 10`.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): library with folders, views, search, file actions and bulk operations`

---

### Task 4: Transcribe dialog — upload, path pick, options

**Files:**
- Create: `scribe/web/transcribe_dialog.py`, `scribe/fsbrowse.py`, `scribe/templates/transcribe_dialog.html`, `tests/test_web_transcribe_dialog.py`, `tests/test_fsbrowse.py`
- Modify: `scribe/app.py` — `POST /api/media` accepts the same option fields (already takes params; add validation via one `TranscribeOptions` pydantic model reused by both routes)

**Interfaces:**
- `TranscribeOptions(language: str | None = None (None = auto-detect), tier: Literal["turbo","max"] = "turbo", diarize: bool = True, num_speakers: int | None = None, min_speakers: int | None = None, max_speakers: int | None = None, translate: bool = False)` with `.to_params() -> dict` mapping `tier` → `model` (`large-v3-turbo` / `large-v3`) and `translate` → `task`.
- `GET /transcribe` — the dialog as an htmx fragment (loaded into a `<dialog>` on the library page): drop zone + file input (`multiple`, `webkitdirectory` toggle), "or pick a file on this machine" path field with a browse panel, language `<select>` from `transcribe.LANGUAGE_CHOICES` (the faster-whisper `_LANGUAGE_CODES` list rendered with names; Dutch preselected via `setting default_language`), tier radio Turbo/Maximaal, speakers checkbox + count fields, translate checkbox. Options persisted to `setting` on submit as the next default.
- `POST /transcribe/upload` — multipart, one or many files, each through `media.ingest_stream` then `jobs.enqueue("transcribe", media_id, params)`; responds with the new library rows fragment + an `HX-Trigger: jobs-changed` header.
- `POST /transcribe/path` — `{path}` → `media.ingest_path` (hardlink) + enqueue; same response shape. Refuses paths outside `fsbrowse.ALLOWED_ROOTS` (default: the user's home drive letters, configurable in `setting fsbrowse_roots`).
- `fsbrowse.listdir(path) -> dict(path, parent, dirs: [name], files: [(name, size, is_media)])` — `os.scandir`, media by extension set from `probe.MEDIA_EXTENSIONS`; `fsbrowse.is_allowed(path) -> bool`.
- `GET /fs?path=` — htmx fragment for the browse panel.
- [ ] **Step 1: failing tests** — `TranscribeOptions` mapping table (turbo/max/translate); upload of two small wav fixtures creates 2 media rows and 2 queued jobs with the expected params; uploading the same bytes twice dedupes to one media row but still enqueues a job (re-run is legitimate); path submit hardlinks and enqueues; path outside roots → 403; `fsbrowse.listdir(tmp_path)` marks `.wav` as media and `.txt` not; traversal `..` rejected.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): transcribe dialog with multi-file upload, path picker and options`

---

### Task 5: Jobs dashboard and job detail with SSE

**Files:**
- Create: `scribe/web/jobs_ui.py`, `scribe/templates/jobs.html`, `_jobs_fragment.html`, `job_detail.html`, `tests/test_web_jobs.py`
- Modify: `scribe/jobs.py` — add `active_count(conn) -> int` (running + queued)

**Interfaces:**
- `GET /jobs` — page with three sections: Running (stage stepper `probe → prepare → transcribe → diarize → attribute → finalize` with the active one highlighted and `stage_progress` as a bar, elapsed, ETA from `_eta`), Queued (position, params summary, Cancel), History (last 50 terminal jobs, status badge, duration, error_code, Retry). The sections live in `_jobs_fragment.html`, which contains `hx-get="/jobs/fragment" hx-trigger="every 2s"` when `active_count > 0` and `every 15s` otherwise — the interval is part of the swapped HTML.
- `GET /jobs/fragment` — the fragment alone.
- `GET /jobs/{id}` — detail: header (media title, params, retry_of link), stepper, the last 200 events rendered, and a `<pre id="log">` filled live by `EventSource("/api/jobs/{id}/stream")`; on `failed`, an error card with `error_code`, `error_detail` and a "show stderr" disclosure holding the `error` event's trace.
- `GET /api/jobs/{id}/stream` — SSE via `StreamingResponse(media_type="text/event-stream")`: replays `job_event` rows after `Last-Event-ID` (or all), then tails every 0.25 s while the job is non-terminal, `: heartbeat` every 15 s, `retry: 2000`, event names `progress|stage|log|error|end`; ends with `end` once the job is terminal.
- Cancel/Retry buttons post to the existing `/api/jobs/{id}/cancel|retry` and trigger a fragment refresh (`HX-Trigger`).
- [ ] **Step 1: failing tests** — seeded running + queued + failed jobs: `/jobs` shows all three in their sections and the running one's stepper marks `transcribe` active; fragment interval is `every 2s` with active jobs and `every 15s` after finishing them; `/jobs/{id}` shows the error card with the code for the failed one; stream returns the seeded events with ids and a terminal `event: end` for a done job (read the body with a bounded iterator); `Last-Event-ID: 2` skips events 1–2.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): jobs dashboard with stage stepper, adaptive polling and SSE job detail`

---

### Task 6: Transcript view with synced player

**Files:**
- Create: `scribe/web/transcript.py`, `scribe/templates/transcript.html`, `tests/test_web_transcript.py`
- Modify: `scribe/static/app.js` (player sync, click-to-seek, highlight, in-page search, timestamp toggle), `scribe/static/app.css`

**Interfaces:**
- `GET /media/{id}` — loads the current run's words + labels, renders `render.paragraphs(...)`: a speaker heading per paragraph (display name, click → rename form, Task 7), sentences with a leading `<a class="ts" data-start="12.34">0:12</a>`, each word in `<span class="w band-high|mid|low" data-i="idx" data-s="…" data-e="…">`. Timestamp toggle is a `<body class="hide-ts">` class persisted in `localStorage`. Right rail: Export (disabled until Phase 4), Download audio, Rename, Move, Trash, run info (model, language, xRT, created). No current run → a "not transcribed yet / job status" panel linking to the job.
- `GET /media/{id}/audio` — `FileResponse` of the original when its container is browser-playable (`mp3, wav, m4a, aac, ogg, opus, flac, webm, mp4`); otherwise a one-time transcode to `MEDIA_DIR/proxy/<sha256>.m4a` (`ffmpeg -vn -c:a aac -b:a 96k -movflags +faststart`) cached and served; the proxy's `ffprobe` duration must be within 50 ms of the original's or the route logs and serves the original anyway. Range supported.
- Player (`app.js`): sticky bottom bar with native `<audio controls>` plus speed buttons (0.75/1/1.25/1.5/2, `preservesPitch=true`), `#t=<sec>` deep-link seeks on load, delegated click on `.ts` and `.w` seeks, `timeupdate` → binary search over a `Float64Array` of word starts → `.current` class moves, auto-scroll suspended for 8 s after a manual scroll, resume position per media in `localStorage`. In-transcript search box filters/highlights via the CSS Custom Highlight API when available, falling back to `<mark>` wrapping.
- [ ] **Step 1: failing tests** — seeded run: page contains a heading per speaker in order, the display name for a labelled cluster and `Speaker 2` for the unlabelled one; each sentence has a `data-start`; word spans carry band classes matching `render.confidence_band`; `#t=` handling is JS (not tested server-side) but the page includes the audio element with `src="/media/{id}/audio"`; audio route returns 206 on a Range request; a fake `.mkv` original triggers the proxy path (monkeypatch the transcode to write a tiny m4a) and the proxy is reused on the second request; media without a run renders the "not transcribed" panel with the job link.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): transcript view with derived paragraphs, confidence tint and a synced player`

---

### Task 7: Speaker rename and segment reassignment

**Files:**
- Modify: `scribe/web/transcript.py`, `scribe/templates/transcript.html`, `scribe/static/app.js`, `tests/test_web_transcript.py`

**Interfaces:**
- `POST /media/{id}/speakers/{cluster}/rename` (display_name, color optional) → upsert `speaker_label` for the current run; returns the re-rendered transcript body fragment (all headings update at once).
- `POST /media/{id}/words/reassign` (from_idx, to_idx, speaker) → `UPDATE word SET speaker=?, edited_by_user=1 WHERE run_id=? AND idx BETWEEN ? AND ?`; `speaker` may be an existing cluster or a new label `USER_<n>` created on the fly (with a `speaker_label` row named by the user). Returns the fragment.
- UI: heading click → inline `<form hx-post=…>`; reassignment: click a word to set the range start, shift-click to set the end (range highlighted), then a small toolbar "Assign to ▾" listing known speakers + "New speaker…".
- [ ] **Step 1: failing tests** — rename upserts and the fragment shows the new name on every paragraph of that cluster; renaming again updates rather than duplicates (UNIQUE); reassign a range flips `speaker` and sets `edited_by_user=1` only on that range; reassign to a new label creates the `speaker_label` row; ranges outside the run's word count → 400.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): rename speakers and reassign word ranges from the transcript view`

---

### Task 8: Settings page and `python -m scribe` polish

**Files:**
- Create: `scribe/web/settings.py`, `scribe/templates/settings.html`, `tests/test_web_settings.py`
- Modify: `scribe/__main__.py`

**Interfaces:**
- `GET /settings` — doctor results (CPU checks live; GPU checks shown from the last stored run in `setting doctor_last`), models present in the HF cache (scan `~/.cache/huggingface/hub/models--*faster-whisper*` and `models--pyannote*`), disk usage of `MEDIA_DIR`, defaults (language, tier, diarize) as a form → `setting` rows, `fsbrowse_roots`.
- `POST /settings` — save defaults. `POST /settings/doctor` — run CPU checks now (GPU checks are a job: enqueue `type="doctor"` with a one-stage registry that calls `doctor.gpu_smoke` and stores the result in `setting`).
- `python -m scribe` — after uvicorn binds, `webbrowser.open("http://127.0.0.1:4242/")` unless `--no-browser`.
- [ ] **Step 1: failing tests** — settings page lists the seeded default language; saving defaults persists to `setting`; doctor POST returns the CPU table; the `doctor` job type is registered and its fake-run stores a `setting doctor_last` row.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): settings page with defaults, model inventory and an on-demand doctor`

---

## Self-review (done at authoring time)

- Spec §4 coverage: Library (T3), Transcribe dialog incl. path picker and options (T4; mic recording and URL import are Phase 6 by the spec's own split), Jobs with stepper/ETA/cancel/retry/SSE/error card (T5), Transcript with player, click-to-seek, highlight, search, confidence, toggle (T6), rename + reassignment (T7), Settings (T8). Exports rail is present but disabled until Phase 4.
- Spec §2 "words canonical": enforced by `render.py` being the only grouping code and by the Global Constraint forbidding grouping tables.
- Type consistency: `render.paragraphs` consumed by T6/T7; `TranscribeOptions` shared by T4 and `POST /api/media`; `jobs.active_count` introduced in T5 and used only there.
- No placeholders; each task ends with a testable page.
