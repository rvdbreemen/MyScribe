# MyScribe — Phase 6: Ingest Extras Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The three ways media arrives that are not "pick a file" — paste a URL, record from the microphone, drop a file in a watched folder — plus the glossary that makes names come out right, both as a decoding bias and as a reversible correction pass afterwards.

**Architecture:** Every new ingest path ends at the same door: `media.ingest_*` then `jobs.enqueue("transcribe", …)`. Downloading is a job type of its own (`ingest_url`) so a slow network never blocks the web process and a failed download is a job row with a reason. Recording streams chunks to disk as they arrive, so a two-hour session survives a closed tab. Watch folders run in one supervisor-owned thread with quiescence gating. The glossary post-pass is a derived, reversible layer over words — never a rewrite (ADR-003).

**Tech Stack:** `yt-dlp`, `watchdog`, `rapidfuzz`, `jellyfish` (all new pins), MediaRecorder in the browser, ffmpeg for remuxing.

**Spec:** `docs/superpowers/specs/2026-09-01-myscribe-design.md` (sections 3–4)

**Depends on:** Phases 1–4. Phase 5 is independent of this one; either order works.

## Global Constraints

- Phase 1–5 Global Constraints apply verbatim. ADR-001: downloads and conversions run in runner children, never in the web process. ADR-003: the glossary post-pass stores corrections as a separate layer keyed to word ids; word text is never overwritten.
- Anything reached over the network is untrusted input: a downloaded filename, a video title, a playlist entry title. Sanitise before it touches a path (`filename_for`'s Win32 scrub from Phase 4) and escape before it touches a template.
- `yt-dlp` is pinned but expected to age badly; the doctor gains a check that reports its version and age, and the URL dialog says plainly when a download fails for a reason that smells like an outdated extractor.
- Recording chunks land in `WORK_DIR/rec/<session>/` and are cleaned up by the same `paths.remove_job_work_dir` discipline once ingested; an abandoned session older than 24 h is swept at startup.
- Watch folders never move or modify the user's files: ingest hardlinks, exactly like the path picker.
- No test may reach the network. `yt-dlp` is exercised through an injected fake; one `@pytest.mark.gpu`-style `@pytest.mark.network` test may hit a real URL and skips by default.

## File Structure (new in Phase 6)

```
scribe/
  ingest/
    __init__.py
    urls.py          # yt-dlp wrapper: probe, download, playlist expansion
    recording.py     # chunk store, append, finalize/remux
    watching.py      # watchdog observer, quiescence, startup reconcile
  stages/url_stage.py    # the ingest_url job type
  glossary.py          # CRUD + hotword composition (moves from transcribe.py) + post-pass
  web/ingest_ui.py     # URL dialog, recorder UI, watch-folder settings, glossary settings
  templates/_url_panel.html  _recorder.html  _glossary.html
  static/recorder.js
tests/test_ingest_*.py  tests/test_glossary.py
```

---

### Task 1: `urls.py` + the `ingest_url` job type

**Files:**
- Create: `scribe/ingest/__init__.py`, `scribe/ingest/urls.py`, `scribe/stages/url_stage.py`, `tests/test_ingest_urls.py`
- Modify: `scribe/runner.py` (register `STAGES["ingest_url"]`), `requirements.txt` (`yt-dlp`), `scribe/doctor.py` (yt-dlp version check, optional)

**Interfaces:**
- `urls.probe(url, *, ydl=None) -> UrlInfo` — `UrlInfo(kind: Literal["single","playlist"], title, duration, uploader, entries: list[dict], webpage_url)`. Uses `extract_info(download=False)` with `--flat-playlist` semantics for playlists.
- `urls.download(url, dest_dir, *, on_progress, ydl=None) -> DownloadedMedia` — `DownloadedMedia(path, title, duration, uploader, info: dict)`. Format selection `bestaudio/best`, **no re-encode** (`--no-post-overwrites`, no `--extract-audio`), `--write-info-json` kept alongside so the metadata can feed hotwords. Progress from yt-dlp's `progress_hooks` mapped onto 0..1.
- `urls.hotword_terms(info: dict) -> list[str]` — proper nouns from title, uploader, chapter titles; feeds the glossary composition in Task 5.
- `url_stage.STAGES = [("fetch", …), ("register", …)]` — `fetch` downloads into the job's work dir with progress; `register` ingests via `media.ingest_path` (a move into the store, since the download is ours) and enqueues a `transcribe` job carrying the original options. Playlist: `register` fans out one `ingest_url` job per entry instead, so each video is its own job with its own progress and failure.
- Error mapping: yt-dlp's `DownloadError` → `error_code="DOWNLOAD_FAILED"` with the last line of its message; an unsupported URL → `UNSUPPORTED_URL`; a private/removed video → `UNAVAILABLE`.
- Windows note to carry in the module docstring: `--cookies-from-browser` does not work for Chrome/Edge since App-Bound Encryption; Firefox or an exported `cookies.txt` only. The dialog offers a cookies-file field for that reason.
- [ ] **Step 1: failing tests** (fake `ydl` object recording calls) — a single-video URL yields `kind="single"` and downloads to the work dir; a playlist yields entries and the stage enqueues one job per entry, not one download; progress hook fractions are non-decreasing and end at 1.0; a `DownloadError` becomes `DOWNLOAD_FAILED` with the reason preserved; the info-json is written next to the media and `hotword_terms` pulls the title's proper nouns; a title containing `..\..\evil` never reaches a path (scrubbed).
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(ingest): URL import as a job, with playlist fan-out and honest failures`

---

### Task 2: URL dialog in the web UI

**Files:**
- Create: `scribe/web/ingest_ui.py`, `scribe/templates/_url_panel.html`, `tests/test_web_url_dialog.py`
- Modify: `scribe/templates/transcribe_dialog.html` (a URL tab beside upload and path)

**Interfaces:**
- `POST /transcribe/url` — `{url, cookies_file?, + TranscribeOptions fields}`; validates the URL shape (`http`/`https` only, no `file:`), enqueues `ingest_url`, returns the library-rows fragment plus `HX-Trigger: jobs-changed`.
- `GET /transcribe/url/preview?url=` — an htmx fragment that runs `urls.probe` **in a threadpool with a 10 s timeout** and shows title, uploader, duration, and for a playlist the entry count with a "transcribe all N" confirmation. A probe failure renders the reason, not a stack trace.
- The jobs board already shows the new job type; its stage names (`fetch`, `register`) appear in the stepper without further work.
- [ ] **Step 1: failing tests** — posting a valid URL enqueues an `ingest_url` job with the options mapped; `file:///etc/passwd` and `javascript:` are refused with 400; the preview fragment shows a probed title and, for a playlist, the entry count; a probe timeout renders a readable message; the panel escapes a title containing markup.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): URL import dialog with a preview that says what will be fetched`

---

### Task 3: Microphone recording

**Files:**
- Create: `scribe/ingest/recording.py`, `scribe/templates/_recorder.html`, `scribe/static/recorder.js`, `tests/test_ingest_recording.py`, `tests/test_web_recorder.py`
- Modify: `scribe/web/ingest_ui.py` (routes), `scribe/db.py` (migration: `recording(id, session, started_at, finished_at, media_id, chunk_count, bytes)`)

**Interfaces:**
- `recording.start(conn) -> str` (session token, url-safe, 16 bytes); `recording.append(session, data: bytes) -> int` (chunk index; writes `WORK_DIR/rec/<session>/<idx:06d>.webm`); `recording.finalize(conn, session, *, title, folder_id) -> dict` — concatenates the chunks, remuxes with `ffmpeg -f webm -i - -c copy -f matroska` (the container fix: MediaRecorder output has no duration header, and a stream copy into Matroska gives one without re-encoding), ingests the result and returns the media row; `recording.sweep(conn, older_than=86400)` removes abandoned sessions.
- Routes: `POST /record/start` → `{session}`; `POST /record/{session}/chunk` (raw body, `Content-Type: audio/webm`) → `{index}`; `POST /record/{session}/finish` (title, folder, options) → media row + enqueued job; `POST /record/{session}/cancel` → removes the directory.
- `recorder.js`: `getUserMedia({audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false}})` — the call-tuned DSP is wrong for transcription; a level meter from an `AnalyserNode`; `MediaRecorder(stream, {mimeType: 'audio/webm;codecs=opus'})` started with `start(5000)` so a chunk lands every five seconds; pause/resume; a beforeunload warning while recording; the elapsed timer.
- Server-side test strategy: the browser half cannot be unit-tested here, so the routes are tested with real webm bytes captured once into `tests/fixtures/chunk.webm` (a 1-second Opus fragment produced by ffmpeg), and `finalize` is asserted to produce a file ffprobe reports with a duration.
- [ ] **Step 1: failing tests** — start returns a session and creates the directory; three appends land as three ordered files; finish produces a media row whose file ffprobe reports with a duration > 0 and enqueues a transcribe job; finishing an unknown session → 404; cancel removes the directory; sweep removes a session older than the cutoff and keeps a fresh one; a session token containing a path separator is refused (no traversal into `WORK_DIR`).
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(ingest): browser microphone recording that survives a closed tab`

---

### Task 4: Watch folders

**Files:**
- Create: `scribe/ingest/watching.py`, `tests/test_ingest_watching.py`
- Modify: `scribe/app.py` (start/stop the watcher in the lifespan), `scribe/web/settings.py` + `settings.html` (watch-folder CRUD), `requirements.txt` (`watchdog`)

**Interfaces:**
- `watching.Watcher(db_path, poll_interval=2.0)` with `.start()`, `.stop()`, `.reconcile(conn)`. One `watchdog.observers.Observer` over every enabled `watch_folder` row.
- Quiescence: a created/modified file is not ingested until its size and mtime have been unchanged for `QUIESCE_SECONDS = 5` — a file still being copied has a growing size, and ingesting it half-written would hash the wrong bytes and store a broken file forever.
- `reconcile(conn)` runs at startup over every watched folder: anything whose sha256 is not already in `media` is ingested. This is what makes a file dropped while the app was closed still arrive, and it is also the fallback when a filesystem event is missed (network drives drop them).
- Extension filter from `probe.MEDIA_EXTENSIONS`; hidden files, partial-download suffixes (`.part`, `.crdownload`, `.tmp`) and files inside `MEDIA_DIR` itself are skipped.
- Settings UI: add/remove folders (validated against `fsbrowse.is_allowed`), an enable toggle, and per-folder default options stored as JSON.
- [ ] **Step 1: failing tests** (driving the handler directly, no sleeping on real events) — a file that stops changing is ingested once and enqueued; a file still growing is not ingested; the same file appearing twice is ingested once (dedupe by hash); `.part` and hidden files are ignored; `reconcile` picks up a file created while the watcher was down; a folder outside the allowed roots is refused by the settings route; stopping the watcher joins its thread.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(ingest): watch folders with quiescence gating and a startup reconcile`

---

### Task 5: Glossary — management, decode bias, reversible post-pass

**Files:**
- Create: `scribe/glossary.py`, `scribe/templates/_glossary.html`, `tests/test_glossary.py`
- Modify: `scribe/stages/transcribe.py` (import hotword composition from `glossary`, keeping the function's behaviour and tests), `scribe/stages/finalize.py` or a new `correct` stage (the post-pass), `scribe/db.py` (migration: `word_correction(id, run_id, word_idx, original, corrected, rule, confidence, created_at)`), `scribe/web/settings.py`, `requirements.txt` (`rapidfuzz`, `jellyfish`)

**Interfaces:**
- `glossary.terms(conn) -> list[Term]` — `Term(term, weight, variants)`; CRUD helpers `add/update/remove`; import/export as one term per line.
- `glossary.compose_hotwords(conn, media_row, extra_terms=(), limit_tokens=223)` — the existing composition, moved here and extended to take the URL-import metadata terms from Task 1.
- `glossary.corrections_for(words, terms) -> list[Correction]` — `Correction(word_idx, original, corrected, rule, confidence)`. Two rules, in order: exact-ish fuzzy match via `rapidfuzz.fuzz.WRatio` over 1–3 word windows above a threshold (default 85), and phonetic match for names via `jellyfish.metaphone` when the fuzzy score is between 70 and 85. Never corrects a word the user has edited (`edited_by_user = 1`).
- The `correct` stage writes `word_correction` rows; **the `word.text` column is not touched**. `render.py` and the exporters apply corrections when reading (one join), so the layer is reversible: deleting the rows restores the original transcript exactly.
- Settings UI: the term list with weights, add/remove, a "re-run corrections on the whole library" button that enqueues one `correct` job per media with a current run.
- [ ] **Step 1: failing tests** — a term with a close misspelling is corrected and the row records both forms; a phonetic-only match (e.g. "Marijke" for "Marieke") is caught by the metaphone rule and marked with that rule; a word below both thresholds is left alone; a user-edited word is never corrected; applying corrections at render time changes the displayed text while `word.text` in the database is unchanged; deleting the correction rows restores the original render exactly; hotword composition still passes its Phase 2 tests after the move.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(glossary): decode bias plus a reversible correction layer over canonical words`

---

## Self-review (done at authoring time)

- Spec §3–4 coverage of the deferred-to-Phase-6 items: URL import with playlist fan-out (T1, T2), microphone recording with chunk-append durability (T3), watch folders with quiescence and reconcile (T4), custom vocabulary on both the decode side and as a post-pass (T5). Cloud-sync import is covered by watch folders, since OneDrive/Dropbox folders are already local — noted in the spec and not a separate task.
- ADR-003 upheld: corrections are a separate keyed layer applied at render time; `word.text` is immutable. ADR-001 upheld: downloads and conversions happen in runner children.
- Type consistency: every path ends at `media.ingest_path`/`ingest_stream` and `jobs.enqueue("transcribe", …)`; `TranscribeOptions` (Phase 3) carries the options through all three new doors.
- Deliberate omission: no cookies-from-browser automation. It is broken for Chrome/Edge on Windows, and a file field the user controls is both honest and safer.
