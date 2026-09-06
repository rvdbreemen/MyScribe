# MyScribe — Phase 2: Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Phase 1 spine into a working transcriber: a file goes in, and a finished transcript with words, speakers, segments and a searchable index comes out — with honest per-stage progress the whole way.

**Architecture:** Six real stages registered on the Phase 1 runner's `STAGES["transcribe"]` registry. Each stage is a pure-ish function over `RunnerContext`, reporting progress through `ctx.report()`. Media enters through a content-addressed store keyed by sha256. Nothing in the web layer changes.

**Tech Stack:** faster-whisper (CTranslate2), pyannote.audio, ffmpeg/ffprobe subprocesses, stdlib sqlite3. Exact pins live in `requirements-gpu.txt` (frozen in Phase 1 Task 8).

**Spec:** `docs/superpowers/specs/2026-09-01-myscribe-design.md` (sections 2–3)

**Depends on:** Phase 1 complete — `scribe/{db,jobs,runner,supervisor,app,doctor,cuda_setup}.py` and a green `python -m scribe.doctor`.

## Global Constraints

- Everything from the Phase 1 plan's Global Constraints still applies verbatim.
- Stage names exactly: `probe, prepare, enhance, transcribe, diarize, attribute, finalize`. Phase 2 implements all but `enhance` (that ships with its A/B harness in a later phase); the name stays reserved.
- `ctx.report(p)` with `p` in 0..1 within the current stage. Never fake progress: if a stage cannot measure itself, it reports 0 and then 1.
- Default model `large-v3-turbo`; `task="translate"` forces `large-v3` and records the substitution in the run row.
- Words are canonical. No stage writes a paragraph, a cue, or any grouped text to the database.
- Every GPU stage must free its VRAM before the next one loads a model: `del model; gc.collect(); torch.cuda.empty_cache()` in that order.
- Never load an entire media file into RAM. ffmpeg streams; pyannote gets a path.
  - **Sanctioned exception, diarize only (Task 6).** On this machine pyannote gets
    samples, not a path: torchcodec 0.16.0 links against libav* symbols FFmpeg 8.1
    no longer exports, so `import torchcodec` dies with `[WinError 127]` and
    torchaudio 2.8 dispatches through it. pyannote cannot open a file here at all,
    and its own error names the supported alternative — a preloaded
    `{"waveform": tensor, "sample_rate": int}` dict. The cost is real and is the
    thing this constraint exists to prevent: 230 MB of RAM per hour of audio for
    the length of the stage, released the moment diarization returns rather than
    at the end of the job. The full reasoning lives in `scribe/stages/diarize.py`'s
    module docstring. Transcription — the stage that actually meets a four-hour
    file — still streams. Revisit when torchcodec and FFmpeg agree again; no ADR
    records this yet, and one should.
- All new tests must run on CPU with the `tiny` model. GPU-only tests carry `@pytest.mark.gpu` and are excluded by default.

## File Structure (new in Phase 2)

```
scribe/
  media.py         # sha256 store, ingest from upload/path/bytes, ffprobe metadata
  stages/
    __init__.py    # TRANSCRIBE_STAGES ordered list, registered into runner.STAGES
    probe.py       # ffprobe gate + duration/streams into media row
    prepare.py     # ffmpeg -> mono 16k wav, progress from -progress pipe
    transcribe.py  # faster-whisper generator, run row, words + segments
    diarize.py     # pyannote with hook progress, embeddings
    attribute.py   # word x turn max-overlap join (ported from WHYcast)
    finalize.py    # one transaction: is_current flip, FTS check, xRT
tests/
  test_media.py test_stage_probe.py test_stage_prepare.py
  test_stage_transcribe.py test_attribution.py test_pipeline_e2e.py
```

---

### Task 1: media.py — content-addressed store and ingest

**Files:**
- Create: `scribe/media.py`, `tests/test_media.py`

**Interfaces:**
- Produces:
  - `hash_file(path) -> str` — sha256 hex, streamed in 1 MiB chunks (never loads the file).
  - `store_path_for(sha256, suffix) -> Path` — `MEDIA_DIR/<h[:2]>/<h><suffix>`.
  - `ingest_path(conn, src: Path, *, title: str | None = None, folder_id: int | None = None, link: bool = True) -> dict` — hashes, dedupes on `media.sha256` (returns the existing row untouched), otherwise hardlinks (`os.link`) into the store and falls back to `shutil.copy2` across volumes, then inserts the media row. Returns the media row as a dict with an extra `"deduped": bool`.
  - `ingest_stream(conn, stream, filename, **kw) -> dict` — writes to a temp file in `MEDIA_DIR/.incoming` while hashing, then hands off to the same insert path (used by upload and mic recording later).

- [ ] **Step 1: failing tests**

```python
def test_hash_matches_hashlib(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"x" * (2 * 1024 * 1024) + b"tail")
    import hashlib
    assert media.hash_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()

def test_ingest_dedupes_identical_content(conn, tmp_path, monkeypatch):
    # two files, same bytes, different names -> one media row, second says deduped
def test_ingest_hardlinks_and_leaves_source_in_place(conn, tmp_path, monkeypatch):
    # source still exists; store file has st_nlink == 2 on the same volume
def test_ingest_falls_back_to_copy_when_link_fails(conn, tmp_path, monkeypatch):
    # monkeypatch os.link to raise OSError -> file still lands in the store
```

- [ ] **Step 2: run** → FAIL (no module)
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(media): content-addressed store with hardlink ingest and dedupe`

---

### Task 2: probe stage — ffprobe as the gate

**Files:**
- Create: `scribe/stages/__init__.py`, `scribe/stages/probe.py`, `tests/test_stage_probe.py`
- Modify: `scribe/runner.py` (register `STAGES["transcribe"]`)

**Interfaces:**
- Produces: `probe.probe_media(path) -> dict` with keys `duration, format_name, streams (list of {index, codec_type, codec_name, channels, sample_rate}), chapters, tags`; raises `probe.NotMediaError` when ffprobe returns no audio stream. `probe.run(ctx)` — the stage: probes `ctx.media_path`, writes `media.duration`, emits a `probe` event with the summary, `ctx.report(1.0)`.
- Adds to the error taxonomy in `runner._ERROR_CODES`: `NotMediaError -> "FFMPEG_DECODE"`.

- [ ] **Step 1: failing tests** — `probe_media(clip30.wav)` returns duration ≈30.0 (±0.2), one audio stream, `codec_type == "audio"`; `probe_media(text file)` raises `NotMediaError`; stage run writes duration onto the media row.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** — `ffprobe -v error -print_format json -show_format -show_streams -show_chapters`, 60 s timeout, parse JSON.
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(stages): ffprobe gate that decides what counts as media`

---

### Task 3: prepare stage — ffmpeg normalize with real progress

**Files:**
- Create: `scribe/stages/prepare.py`, `tests/test_stage_prepare.py`

**Interfaces:**
- Produces: `prepare.to_wav(src: Path, dst: Path, duration: float, on_progress: Callable[[float], None]) -> Path` — runs `ffmpeg -nostdin -v error -progress pipe:1 -i <src> -vn -sn -dn -ac 1 -ar 16000 -c:a pcm_s16le <dst>`, parses `out_time_us=` lines from stdout, calls `on_progress(min(1.0, out_time_us/1e6/duration))`. `prepare.run(ctx)` writes to `LOGS_DIR/../work/<job_id>/audio.wav` and puts the path on `ctx.state["wav"]`.
- Adds: `RunnerContext.state: dict` (mutable scratch shared between stages of one job) — small change to `scribe/runner.py`, plus a test that state survives across stages.

- [ ] **Step 1: failing tests** — converting `clip30.wav` yields a 16 kHz mono PCM file (assert via ffprobe); `on_progress` is called at least twice with values that never decrease and end ≥0.9; a corrupt input raises with stderr captured.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(stages): ffmpeg normalize with progress parsed from -progress`

---

### Task 4: transcribe stage — the generator is the progress bar

**Files:**
- Create: `scribe/stages/transcribe.py`, `tests/test_stage_transcribe.py`

**Interfaces:**
- Produces:
  - `transcribe.resolve_model(requested: str, task: str) -> tuple[str, str | None]` — returns `(model_name, substitution_note)`; `("large-v3-turbo", "translate")` → `("large-v3", "turbo cannot translate; using large-v3")`.
  - `transcribe.compose_hotwords(conn, media_row, limit_tokens: int = 223) -> str` — glossary terms first, then proper nouns from media tags/filename, truncated on token boundaries.
  - `transcribe.transcribe_audio(wav, *, model_name, language, task, hotwords, on_progress, cancelled) -> tuple[dict, list[dict], list[dict]]` — returns `(info, segments, words)`; consumes the faster-whisper generator lazily so `on_progress(segment.end / info.duration)` fires as it goes, and checks `cancelled()` between segments.
  - `transcribe.run(ctx)` — creates the `run` row, persists segments + words, records `stage_perf`.

- [ ] **Step 1: failing tests** (CPU, `tiny` model) — `resolve_model` substitution table; `compose_hotwords` respects the token cap and puts glossary first; transcribing `clip30.wav` yields >10 words with monotonic timestamps and every word carrying a probability; `on_progress` values are non-decreasing and reach ≥0.8; setting `cancelled` mid-stream stops early and raises `Cancelled`.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** — `cuda_setup.ensure_cuda_libs()` before importing faster_whisper; `word_timestamps=True`, `vad_filter=True`, `condition_on_previous_text=True`, `compression_ratio_threshold=2.4`; persist `avg_logprob/no_speech_prob/compression_ratio/temperature` per segment.
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(stages): faster-whisper stage with live progress from the segment generator`

---

### Task 5: attribute — port WHYcast's word-level speaker join

**Files:**
- Create: `scribe/stages/attribute.py`, `tests/test_attribution.py`
- Reference (read, do not import): `D:/Users/Robert/Documents/GitHub/RvdB/WHYcast-transcribe/whycast/pipeline/attribution.py`

**Interfaces:**
- Produces: `attribute.turns_from_diarization(annotation) -> list[Turn]`; `attribute.speaker_for_interval(turns, start, end) -> str | None` (maximum temporal overlap, ties to the earlier turn); `attribute.attribute_words(words, turns) -> list[dict]` (adds `speaker`); `attribute.fill_unattributed(words) -> list[dict]` (carry forward, then backward at the head).
- This task is pure functions with synthetic fixtures — **no GPU, no models**. It is the piece most worth testing hard, because a wrong join silently mislabels every speaker.

- [ ] **Step 1: failing tests** — a word fully inside one turn gets that speaker; a word straddling two turns gets the one with more overlap; a word in a gap inherits the previous speaker; leading gap words inherit the first known speaker; empty turn list leaves speakers None without raising; overlapping turns (both speakers talking) pick the larger overlap deterministically.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement** (port the algorithm, keep the tests)
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(stages): word-level speaker attribution by maximum overlap`

---

### Task 6: diarize stage — pyannote with sub-stage progress and embeddings

**Files:**
- Create: `scribe/stages/diarize.py`, `tests/test_stage_diarize.py`

**Interfaces:**
- Produces:
  - `diarize.load_pipeline(model_dir_or_id, device) -> Pipeline` — resolves weights from a local dir first (`MODELS_DIR/pyannote`), falling back to the HF id with a token from settings; raises `diarize.WeightsUnavailable` with a plain-language hint when neither works.
  - `diarize.ProgressHook(on_progress)` — implements pyannote's `(step_name, step_artifact, file=None, total=None, completed=None)` signature and maps known steps onto a 0..1 fraction.
  - `diarize.diarize(wav, *, num_speakers=None, min_speakers=None, max_speakers=None, on_progress, device) -> tuple[list[Turn], dict[str, bytes]]` — returns turns and mean embeddings per cluster (`return_embeddings=True`).
  - `diarize.run(ctx)` — persists `speaker_embedding` rows, puts turns on `ctx.state["turns"]`, then frees VRAM (`del pipeline; gc.collect(); torch.cuda.empty_cache()`).

- [ ] **Step 1: failing tests** — `ProgressHook` maps a known step sequence to non-decreasing fractions ending at 1.0 and tolerates unknown step names; `load_pipeline` raises `WeightsUnavailable` with a hint when both sources are missing (monkeypatched); `@pytest.mark.gpu` test: diarizing `clip30.wav` returns ≥1 turn and one embedding per cluster.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS (GPU test run explicitly with `-m gpu`)
- [ ] **Step 5: commit** `feat(stages): pyannote diarization with sub-stage progress and speaker embeddings`

---

### Task 7: finalize stage + end-to-end pipeline

**Files:**
- Create: `scribe/stages/finalize.py`, `tests/test_pipeline_e2e.py`
- Modify: `scribe/stages/__init__.py` (full `TRANSCRIBE_STAGES`), `scribe/runner.py` (registry + error codes), `scribe/app.py` (POST `/api/media` ingest + enqueue)

**Interfaces:**
- Produces: `finalize.run(ctx)` — in one transaction: writes attributed speakers onto the word rows, flips `run.is_current` (previous current run cleared), records measured `xrt`, verifies the FTS row count matches the segment count, and deletes the job's work directory. `app` gains `POST /api/media` (multipart or `{"path": ...}` JSON) returning the media row plus the enqueued job id.
- Adds `_ERROR_CODES`: `torch.cuda.OutOfMemoryError -> "CUDA_OOM"`, `FileNotFoundError -> "FILE_MISSING"`, `PermissionError -> "FILE_LOCKED"`, `OSError(28) -> "DISK_FULL"`.

- [ ] **Step 1: failing test** — end-to-end with the `tiny` model on CPU and diarization disabled: ingest `clip30.wav` through the API, run the job with `runner.main`, then assert: job `done`; one `run` row with `is_current=1` and `xrt > 0`; >10 word rows with monotonic starts; segment count == FTS row count; a MATCH query on a word from the clip returns that segment; the work directory is gone.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS; then run the same flow once on GPU with the real default model and record the xRT in the task notes.
- [ ] **Step 5: commit** `feat(pipeline): finalize stage and end-to-end transcribe job`

---

## Self-review (done at authoring time)

- Spec §3 stage table: probe (T2), prepare (T3), transcribe (T4), diarize (T6), attribute (T5), finalize (T7); `enhance` deliberately reserved and named as deferred. Spec §2 word-canonical storage: enforced by T4 (words+segments only) and asserted in T7's e2e test.
- Ordering note: attribution (T5) is deliberately built before diarization (T6) because it is pure, testable without a GPU, and is where a silent bug would be most expensive.
- Interfaces consistent with Phase 1: stages receive `RunnerContext`; `ctx.state` is the one addition, introduced in T3 with its own test.
- No placeholders; each task ends with an independently testable deliverable.
