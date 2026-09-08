# MyScribe — Design

Status: approved by Robert on 2026-09-01 (sections 1–9 approved in-chat, in three parts).
Scope: v1 of a local-first transcription application. This document is the validated
design; the implementation plan is derived from it separately.

## 0. What this is, and what it is not

A single-user, localhost-only web application that does what a hosted
transcription service does — upload or record audio/video, transcribe,
recognize speakers, browse, search and export transcripts — entirely on the
user's own machine (Windows 11, RTX 3080 Laptop 16 GB VRAM, Python 3.12,
ffmpeg on PATH), with one addition those services deliberately hide: a job
dashboard with real per-stage pipeline progress.

Grounding (all verified in this repo's research phase, 2026-09-01):

- WHYcast-transcribe is the code donor: its pipeline internals (CUDA setup,
  word-level speaker attribution, event sink, atomic writes) and its webui job
  machinery (SQLite-WAL claim pattern, first-verdict-wins, SSE log tail) are
  ported, not imported. WHYcast itself stays untouched.
- 198 features of the hosted services were inventoried from their public
  pages; the parity matrix and the v1 cut line below follow from it.
- 22+ open-source alternatives were profiled and adversarially reviewed. Verdict:
  build. Nothing combines a durable per-stage job queue, a real library,
  native Windows CUDA, and a no-build-toolchain UI. OpenTranscribe (AGPL,
  Docker, ~10–14 GB idle) is the benchmark to compare against, not the base.
- Measured on this machine: large-v3 loads in 17–19 s (warm == cold), ~5 GB VRAM
  fp16; 30 s clip transcribes in 12.4 s end-to-end on CUDA in the WHYcast venv;
  1327 s episode → 458 s transcription + 33 s diarization (one run, large-v3).

### Locked user decisions

| Decision | Value |
|---|---|
| Relation to WHYcast | New standalone app in this repo; port code, never refactor WHYcast |
| Users / network | Single user, 127.0.0.1 only, no auth |
| Database | SQLite (WAL), one file |
| Default ASR model | **large-v3-turbo**; large-v3 selectable, and auto-substituted for translate |
| Transcript UI scope | Read, search, export, speaker rename + segment reassignment. No free-text editing in v1 |
| v1 extras | Folders, URL import (yt-dlp), microphone recording, translate-to-English |
| LLM providers | Pluggable: Ollama / OpenAI / OpenRouter; commercial default, private-mode pin to Ollama |
| Build vs adopt | Build; OpenTranscribe serves as external benchmark |
| Process model | One FastAPI process + supervisor thread + one runner child per job |

## 1. Process architecture

```
one venv, one start command (python -m scribe)
┌────────────────────────────────────────────────────────────────┐
│ FastAPI process (uvicorn, 127.0.0.1:4242)                      │
│  ├─ routes + Jinja2 templates + vendored htmx (no CDN)         │
│  ├─ SQLite (WAL) opened once, shared behind one RLock          │
│  └─ supervisor thread                                          │
│      claim: BEGIN IMMEDIATE; UPDATE job … RETURNING id         │
│      spawn: python -m scribe.runner <job_id>  ──────────────►  │ runner child
│      overlap: CPU prework (hash, ffprobe, ffmpeg) of next job  │  loads models,
│  SSE: tails job_event rows for the job-detail log view         │  runs stages,
└────────────────────────────────────────────────────────────────┘  emits events,
                                                                    exits → VRAM freed
```

- **Why a runner child per job**: CUDA OOM or a driver hiccup kills only the
  child; faster-whisper does not reliably release VRAM in-process
  (SYSTRAN/faster-whisper#71), so process exit is the only guaranteed free.
  Model load is 17–19 s measured (turbo will be less); acceptable per job.
- **GPU serialization**: exactly one GPU runner at a time. While it runs, the
  supervisor may execute the CPU-only stages (probe, prepare) of the *next*
  queued job in a separate CPU-marked child; completed stages are recorded per
  stage on the job row, and the GPU runner resumes at the first unfinished
  stage. One code path, two claim modes (cpu-prework / gpu).
- **Restart semantics**: restarting the app kills a running job; startup
  reconciliation marks orphaned `running` jobs `interrupted` with one-click
  retry. Accepted trade-off (chosen over WHYcast's separate worker process).
- **Windows CUDA story**: `os.add_dll_directory(<site-packages>/torch/lib)`
  before importing ctranslate2 (torch, pulled in by pyannote, ships the cuDNN 9
  and cuBLAS DLLs that CTranslate2 delay-loads), plus WHYcast's
  `ensure_cuda_libs()` PATH-prepend as belt-and-braces. Verified failure mode
  it prevents: model constructs fine, first transcribe() dies on missing DLL.
- **Doctor**: `python -m scribe.doctor` checks DLL resolution, model presence,
  runs a 30 s GPU transcription, writes the timing to `stage_perf`. Run at
  install and on demand.

## 2. Data model (SQLite ≥ 3.35, WAL, FTS5)

Principles: **words are canonical** (every user-facing segmentation is a pure
function of words, evaluated at render/export time — the documented
"Resegment" staleness trap of the segment-storing designs cannot exist here);
**runs are append-only** (re-runs
never overwrite); **media bytes never live in the DB**.

| Table | Purpose / key columns |
|---|---|
| `media` | One physical recording. `sha256` content hash (dedupe + store path `media/<h[:2]>/<hash>.<ext>`), original filename, display title, `folder_id`, duration, size, `trashed_at` (3-level delete), created_at. Originals are kept permanently; free-disk floor guards ingest. |
| `folder` | Adjacency list (`parent_id`); nesting via `WITH RECURSIVE`. |
| `run` | One pipeline execution: model, compute_type, language, task, decode params (JSON), measured xRT, `is_current` flag. Append-only. |
| `segment` | Whisper's native segments for a run (start, end, text). Immutable source data; carries the FTS index. Not user-facing segmentation. |
| `word` | Canonical: run_id, idx, start, end, text, probability, speaker cluster label, `edited_by_user` flag (reassignments). |
| `speaker_label` | (run_id, cluster_label) → display name + colour. Applied at render/export; transcript text is never rewritten by a rename. |
| `job` | type (`transcribe`, `diarize_only`, `llm`, `export_bulk`, `ingest_url`…), media_id/run_id, status ∈ {queued, running, done, failed, cancelled, interrupted}, current stage, stage_progress 0–1, params JSON, error_code, error_detail, pid, retry_of, timestamps. UI polls this row. |
| `job_event` | Append-only (job_id, seq, ts, kind, payload JSON). SSE tail; survives for post-mortems. |
| `stage_perf` | (stage, model, media_duration, wall_seconds) per completed stage → rolling-median ETA calibrated to this GPU. |
| `speaker_embedding` | Mean pyannote embedding per (run, cluster) from `return_embeddings=True`. v1 use: re-anchor display names after re-diarization; later: cross-file identity. |
| `llm_output` | (media_id, kind, provider, model, prompt_version) → content, created_at. Re-running with a better model adds a row. |
| `vocab` | User glossary: term, weight, optional phonetic variants. Feeds hotwords and the post-pass. |
| `export_preset`, `setting`, `watch_folder`, `view` | Config rows. `view` holds the predicate behind Recent/Uncategorized/Trash and future saved searches. |

- **FTS5**: external-content table over `segment` (content=segment) with the
  three sync triggers and unicode61; queries use `bm25()` + `snippet()`; every
  hit deep-links to its timestamp. A trigram companion index covers substring
  and Dutch-morphology lookups.
- **Concurrency**: one connection in the web process (`check_same_thread=False`)
  behind a single RLock defined in `db.py` and imported everywhere else
  (WHYcast's proven pattern); the runner child gets its own connection;
  cross-process safety comes from SQL (`BEGIN IMMEDIATE` claims, CHECK-constrained
  status, first-verdict-wins on finish).
- **Hash**: sha256, hex — one algorithm everywhere (store path, dedupe, share).

## 3. Pipeline

Stages of a `transcribe` job, each with honest measured progress:

```
probe → prepare → [enhance] → transcribe → diarize → attribute → finalize
```

| Stage | What | Progress source |
|---|---|---|
| probe | `ffprobe -show_format -show_streams -show_chapters` is the arbiter of "is this media"; duplicate-hash and disk-floor checks | instant |
| prepare | one ffmpeg call → mono 16 kHz WAV; audio track picker for multi-track containers | ffmpeg `-progress pipe:1` |
| enhance (opt-in) | DeepFilterNet (CPU) — ships only together with the A/B diff (§7): both versions transcribed, word-level diff shown, original selected by default | ffmpeg/DFN chunk count |
| transcribe | faster-whisper generator consumed live: `segment.end / info.duration`; `word_timestamps=True`, VAD on, hotwords composed from metadata + glossary (hotwords, not initial_prompt — re-injected every window), repetition guards on and surfaced | segment generator |
| diarize | pyannote community-1 (weights re-hosted with SHA-256 pinning — no HF token dance), `num/min/max_speakers` hint, `return_embeddings=True`, custom `hook=` for sub-stage progress | pyannote hook |
| attribute | WHYcast's word-level max-overlap join (ported with its tests): speaker per word, gap fill, span grouping | word count |
| finalize | persist words/segments/embeddings + FTS in one transaction (atomic-write pattern for any file artifacts) | instant |

- **Model tiers (UI)**: **Turbo 🐬** = large-v3-turbo (default) and
  **Maximaal 🐋** = large-v3. Cached `small` is a hidden CPU fallback and
  preview engine, never a quality tier. Turbo cannot run `task=translate`:
  choosing translate-to-English auto-substitutes large-v3 and says so in the UI.
- **Language**: full runtime-enumerated list + auto-detect (5 VAD-selected
  windows, modal vote, top-3 with probabilities, warning below 0.5).
- **Confidence is persisted**, not discarded: word probability, avg_logprob,
  no_speech_prob, compression_ratio, temperature per segment.
- **Derived-only segmentation**: paragraph grouping (speaker change / >2 s gap /
  ~700 chars) and subtitle cues (CPL, max lines, reading speed, sentence-aware
  balancing) are render-time functions; any cache is keyed by (run, rules
  version) and invalidated on edit.
- **LLM stages run as separate `llm` jobs**, never inside the transcribe job; a
  semaphore keeps local LLM inference off the GPU while a runner holds it.

## 4. Web UI (Jinja2 + vendored htmx, no build toolchain)

Four screens. Live updates: htmx polling (2 s when active, slow when idle —
interval rendered into the swapped fragment, WHYcast's trick); SSE only for the
job-detail log tail and LLM token streams.

1. **Library** — sidebar (Recent / Uncategorized / Trash / folder tree / New
   Folder); table: name, uploaded, duration, mode icon, status, ⋯-menu (open,
   export, rename, move, trash, download audio); bulk select + bulk actions
   (move, trash, re-run, export); library-wide FTS search box, hits jump to
   the moment. **Transcribe dialog**: multi-file + folder drag-drop
   (`webkitGetAsEntry`), server-side path picker (hardlink into store, copy
   across volumes), URL field (yt-dlp: bestaudio, playlists fan out, info.json
   feeds hotwords), language + auto-detect, tier choice, checkboxes: recognize
   speakers (+count hint), translate to English, restore audio (A/B).
   **Record dialog**: getUserMedia with voice-call DSP off, level meter,
   pause/resume, MediaRecorder 5 s chunks appended server-side (crash-safe),
   remux to fix WebM duration.
2. **Transcript** — speaker headers (click = rename, applied everywhere);
   per-sentence clickable timestamps + show/hide toggle; sticky player
   (Range/206 serving + AAC faststart proxy transcoded from the exact audio
   the timestamps came from, asserted within 50 ms; speed 0.5–3×,
   resume-with-2 s-rewind); click-to-seek; playback highlight (binary search
   over a Float64Array, suspends 8 s after manual scroll); in-transcript
   search; confidence tinting with jump-to-next-suspect; select a word range →
   assign to another speaker (one UPDATE, `edited_by_user` set);
   `content-visibility: auto` keeps 10-hour files scrollable. Right rail:
   exports, AI actions, download audio, rename/move/trash.
3. **Jobs** — queue + running + history; per job a stage stepper with real
   percentages, queue position, ETA from `stage_perf` medians, cancel, retry
   (new job row, `retry_of` set); failure rows show the classified error and
   link to detail; job detail streams the event log over SSE and always offers
   the last 8 KB of stderr.
4. **Settings** — model download manager with preflight (disk, VRAM table);
   LLM providers + keys + defaults + private-mode rules; glossary; export
   presets; watch folders (quiescence-gated, reconcile scan at startup).

## 5. Exports

All exporters are pure functions of stored words + speaker labels — re-export
of the whole library after a rename or preset change costs seconds and no GPU.

Formats (v1): **SRT** (speaker change = hard cue break) · **VTT** (`<v Name>`
tags) · **TXT** (cue / paragraph / monologue / speaker-turn layouts) · **CSV**
(documented columns incl. confidence, utf-8-sig) · **DOCX** (python-docx,
speaker bold, clickable timestamps) · **JSON** (canonical word-level,
`schema_version`) · **Markdown** (YAML front matter, deep links) ·
**self-contained HTML** with embedded player — doubles as the Share artifact.
PDF: deferred (font shipping for CJK makes it a real task).

Options: timestamp granularity (none/paragraph/turn/cue/sentence/interval/word)
and format (hh:mm:ss / SMPTE / seconds); speaker labels on/off; subtitle
constraints (CPL, max lines, reading speed, min duration, cue gap) with a
compliance report when unsatisfiable; filename template with Win32 scrub;
UTF-8/BOM/CP1252 + line-ending choice. **Advanced Export dialog** posts to
`/export/preview` and shows the first 10 rendered cues live. Presets (Netflix,
BBC, YouTube, podcast, Obsidian) stored as JSON rows. Write-to-folder (default:
next to source media) and bulk export over any selection — no caps. The export
route doubles as the API; a thin argparse CLI wraps it for PowerShell.

## 6. LLM layer

One `chat()` seam (`scribe/llm.py`):

- **OpenAI + OpenRouter** through the OpenAI SDK (same wire format; base_url +
  key). **Ollama** through its native `/api/chat` so `num_ctx` and `keep_alive`
  are controllable. Provider + model per task, with per-job override.
- **Defaults**: commercial providers (user decision). **Private-mode pin**: a
  per-file/per-folder flag forces Ollama and the UI always shows which text
  leaves the machine before it does.
- Robustness rules (from millet's measured failures): never carry a model name
  across providers on fallback; treat a 200 OK whose body is an error envelope
  as a failure; clamp per-model quirks (e.g. OpenRouter reasoning burn).
- **Features on the seam**: six preset outputs (summary, action items with
  evidence timestamps, chapters, meeting minutes, blog draft, custom prompt) as
  Jinja prompt templates + pydantic schemas, map-reduce over segment boundaries
  for long transcripts (each chunk a resumable job row); chat-with-transcript
  (full stuffing when it fits, FTS5 retrieval when it does not) with timestamp
  citations that drive the player. Outputs stored per (kind, provider, model,
  prompt_version).
- Local inference waits on the GPU semaphore; prompts and keys live in
  `setting`, keys never in git.

## 7. Errors, recovery, and the one known hard risk

- **First-verdict-wins** on job finish (runner, supervisor safety net, cancel
  route may all write; DB CHECK + first write wins; refused verdicts parked and
  replayed at startup) — ported from WHYcast.
- **Startup reconciliation**: orphaned `running` → `interrupted` + one-click
  retry; temp dirs swept; every artifact written atomically (temp + fsync +
  rename), so no half-written state exists.
- **Error taxonomy with an action attached**: `CUDA_OOM` (suggest Turbo /
  close Ollama), `CUDNN_MISSING` (doctor link), `FFMPEG_DECODE` (show ffprobe
  output), `FILE_LOCKED`, `DISK_FULL` (floor checked before start, not after
  a crash). Raw stderr (last 8 KB) always retrievable.
- **Cancel**: cooperative flag checked between segments and stages; after a
  10 s grace, `terminate()`/`kill()` — pyannote has no interruption point.
- **Known hard risk, stated up front**: pyannote cluster labels
  (`SPEAKER_00`…) are arbitrary per run, so re-diarization scrambles rename
  maps. v1 mitigation: re-anchor names by cosine distance over stored mean
  embeddings; if the match is ambiguous the UI warns before re-diarizing
  instead of silently losing names.
- **A/B rule for Restore Audio**: the denoiser ships only with the comparison
  (two transcripts, word-level diff, original default; auto-promote only if
  avg_logprob improves AND compression ratio stays < 2.4 AND no_speech_prob
  does not rise). Independent 2026 papers show enhancement can *raise* WER —
  a bare "enhance" button would quietly make transcripts worse.

## 8. Testing

- **Unit (no GPU)**: segmentation engine, subtitle splitter, attribution join
  (port WHYcast's existing tests), export writers against golden files, FTS
  triggers, hash/dedupe, ETA math.
- **Integration (CPU, CI-speed)**: full pipeline on a 30 s fixture with the
  `tiny` model; job state machine against a scripted fake runner that fails,
  hangs, or crashes on command; SSE resume via Last-Event-ID.
- **Hardware smoke**: `scribe.doctor` (§1) is both the install check and the
  recurring "does it still work" answer; its timings feed `stage_perf`.
- Workflow: TDD per feature (test first), verification before any completion
  claim, `graphify update .` after code changes, ADRs via adr-kit for the
  decisions in this spec (judge gate runs pre-commit).

## 9. Scope

**v1** = everything above: folders/rename/3-level delete, URL import, mic
recording, translate-to-English, watch folders, glossary (decode + post-pass),
confidence surfacing, speaker rename/reassign, per-stage re-run
(diarize-only ~3 min/h), library FTS, 8 export formats + presets + preview +
bulk, LLM presets + chat with citations, jobs dashboard with ETA/cancel/retry.

**Deferred**: free-text editing (drags find-replace, undo, versioning, merge —
its own phase), PDF, waveform scrubber, word-karaoke highlight, translation to
other languages, chapters/topic segmentation, semantic search, tab/system-audio
capture, second ASR backend (Parakeet TDT v3 — the registry seam exists from
day one), LAN share, RSS ingest.

**Dropped**: tier caps and quotas, High Volume Mode, teams/auth/licensing,
41-locale i18n (NL+EN), a Resegment button (impossible by construction — that
is the point), in-app delete-everything.

The line: a v1 feature is a projection of data already on disk; anything that
needs a second model, a second runtime, or a second venv waits.

**Amendment 2026-09-08 (TASK-021).** RSS ingest moves out of Deferred. It
turned out to need no second parser: yt-dlp's generic extractor already reads
a podcast feed into a playlist, so the link door lists a feed's or a
channel's episodes and imports the ticked ones, one `ingest_url` job each.
Design: `2026-09-08-feed-episode-import-design.md`; decision: ADR-008.
Subscribing to a feed (a table, a poller) stays deferred.

## 10. Package layout

```
scribe/                  # the app package
  __main__.py            # python -m scribe → uvicorn + supervisor
  app.py  db.py  jobs.py # web, storage, queue (WHYcast-webui lineage)
  runner.py              # per-job child process entry
  doctor.py              # install/health smoke test
  pipeline/              # probe, prepare, enhance, transcribe, diarize,
                         #   attribute, finalize  (WHYcast-pipeline lineage)
  llm.py  exports/       # provider seam; pure exporters
  templates/  static/    # Jinja2, vendored htmx, app.css, app.js
tests/                   # unit + integration + fixtures (30 s clip)
docs/adr/                # decisions distilled from this spec
```

License hygiene for donors: patterns may come from anywhere; verbatim code only
from MIT/Apache sources (WHYcast — own repo; NoobScribe MIT; WhisperLiveKit
Apache; TranscrIA Apache; whisper-asr-webservice MIT). AGPL/GPL projects
(OpenTranscribe, noScribe, aTrain) contribute ideas and pin-set facts only.
