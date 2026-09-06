# MyScribe — Phase 4: Exports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every transcript leaves the app in any of eight formats, shaped by options the user can preview before downloading, with presets, write-to-folder, bulk export over a selection, and a CLI — all computed from canonical words in seconds, never touching a model.

**Architecture:** `scribe/exports/` is a package of pure functions: `(words, labels, segments, media, run, options) -> bytes`. A subtitle segmentation engine turns words into cues under constraints and reports where it could not satisfy them. Routes in `scribe/web/exports_ui.py` are thin: parse options, call the writer, stream the bytes or write the file. The Advanced Export dialog posts to a preview route that renders the first ten cues live.

**Tech Stack:** stdlib (`csv`, `json`, `zipfile`, `io`), `python-docx` for DOCX, Jinja2 for the HTML bundle. No new JS dependencies.

**Spec:** `docs/superpowers/specs/2026-09-01-myscribe-design.md` (section 5; section 2 for the data; ADR-003)

**Depends on:** Phase 3 complete (transcript view, `render.py`, `tests/seed.py`).

## Global Constraints

- Phase 1–3 Global Constraints apply verbatim. ADR-003 governs: exporters read `word`, `segment`, `speaker_label` rows and never write to the database.
- Every writer is a pure function with signature
  `write(doc: TranscriptDoc, options: ExportOptions) -> bytes` where `TranscriptDoc` is the one loader's output (Task 1). No writer touches `sqlite3`, the filesystem, or the request.
- Text encodings: default UTF-8 without BOM; `utf-8-sig` for CSV by default (Excel); CP1252 and BOM selectable per format. Line endings selectable (`\n` default, `\r\n` on request); SRT and VTT always use `\n` internally and convert last.
- Timestamps: `render.format_ts` is the single formatter; SRT uses `hh:mm:ss,mmm`, VTT `hh:mm:ss.mmm`, TXT/MD/DOCX the `clock` style unless `timestamp_format` says otherwise.
- Filenames are derived from a template with a Win32 scrub (`<>:"/\|?*` and control chars → `_`, trailing dots/spaces stripped, reserved names like `CON` suffixed).
- Golden files live in `tests/golden/exports/` and are compared byte-for-byte; a change to a writer requires updating its golden file in the same commit with a reason in the message.
- Nothing in this phase runs a model; no test may need the GPU.

## File Structure (new in Phase 4)

```
scribe/exports/
  __init__.py       # FORMATS registry: name -> (writer, mime, extension)
  doc.py            # TranscriptDoc loader: one query set -> immutable doc
  options.py        # ExportOptions (pydantic), PRESETS, filename_for()
  cues.py           # subtitle segmentation engine + compliance report
  srt.py vtt.py txt.py csvw.py jsonw.py markdown.py docx.py html_bundle.py
  cli.py            # python -m scribe.export
scribe/web/exports_ui.py
scribe/templates/export_dialog.html  _export_preview.html
tests/test_exports_*.py  tests/golden/exports/*
```

---

### Task 1: `doc.py` + `options.py` — the loader and the option model

**Files:**
- Create: `scribe/exports/__init__.py`, `scribe/exports/doc.py`, `scribe/exports/options.py`, `tests/test_exports_doc.py`, `tests/test_exports_options.py`

**Interfaces:**
- `doc.load(conn, media_id: int, run_id: int | None = None) -> TranscriptDoc` — current run by default. `TranscriptDoc(media: dict, run: dict, words: tuple[dict, ...], segments: tuple[dict, ...], labels: dict[str, str], duration: float, title: str)`. Words carry `start, end, text, probability, speaker, idx`. Frozen dataclass; raises `doc.NoTranscript` when the media has no current run.
- `options.ExportOptions` (pydantic): `formats: list[str]` (subset of `FORMATS`), `speakers: bool = True`, `timestamps: Literal["none","paragraph","turn","cue","sentence","interval","word"] = "paragraph"`, `timestamp_format: Literal["clock","smpte","seconds"] = "clock"`, `interval_seconds: int = 60`, `txt_layout: Literal["cue","paragraph","monologue","turn"] = "paragraph"`, `cpl: int = 42`, `max_lines: int = 2`, `max_cps: float = 20.0`, `min_duration: float = 1.0`, `max_duration: float = 7.0`, `cue_gap: float = 0.08`, `encoding: Literal["utf-8","utf-8-sig","cp1252"] = "utf-8"`, `crlf: bool = False`, `filename_template: str = "{title}"`, `include_confidence: bool = False`.
- `options.PRESETS: dict[str, ExportOptions]` — `netflix` (cpl 42, max_lines 2, max_cps 20, min 0.83, max 7), `bbc` (cpl 37, max_cps 16), `youtube` (cpl 32, max_lines 2, max_duration 5), `podcast` (txt paragraph layout, timestamps turn, speakers on), `obsidian` (markdown, timestamps sentence, clock format, YAML front matter on).
- `options.filename_for(doc, options, ext) -> str` — template fields `{title} {date} {model} {lang}`, then the Win32 scrub.
- `exports.FORMATS = {"srt": …, "vtt": …, "txt": …, "csv": …, "json": …, "md": …, "docx": …, "html": …}` filled in by later tasks; Task 1 registers the names with `NotImplemented` writers so the registry shape is fixed now.
- [ ] **Step 1: failing tests** — `load` returns words in `idx` order with labels mapped; raises `NoTranscript` without a run; `ExportOptions` rejects an unknown format and `cpl < 10`; each preset validates; `filename_for` scrubs `a:b/c?.txt` → `a_b_c_.txt`, suffixes `CON`, strips trailing dots.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(exports): transcript document loader, export options and presets`

---

### Task 2: `cues.py` — the subtitle segmentation engine

**Files:**
- Create: `scribe/exports/cues.py`, `tests/test_exports_cues.py`

**Interfaces:**
- `cues.build(doc, options) -> CueSet` — `CueSet(cues: list[Cue], report: Report)`; `Cue(index, start, end, lines: list[str], speaker: str | None)`; `Report(violations: list[Violation])`, `Violation(cue_index, rule, value, limit)`.
- Algorithm (documented in the module): walk words in order; a speaker change is a hard cue break; accumulate words into a cue until adding the next would exceed `max_duration` or `cpl * max_lines` characters; prefer breaking at sentence-final punctuation, then at a comma, then at the largest inter-word silence within the last 40% of the candidate; balance lines with a DP that minimises the squared difference in line lengths under `cpl`; extend a cue's end to `min_duration` when the next word allows it; enforce `cue_gap` between consecutive cues; compute cps and record a `Violation` where cps > `max_cps` or a line > `cpl` could not be avoided (a single word longer than `cpl`).
- Determinism: same input + options → identical cues; no randomness, no dict-order dependence.
- [ ] **Step 1: failing tests** — a speaker change always starts a new cue; a 20-word sentence at cpl 42 / 2 lines splits into ≥2 cues, none over 84 chars, each line ≤ 42; sentence punctuation is preferred over a comma; balanced lines (given "aaaa bbbb cccc dddd" with cpl 9 → lines of 9/9 not 14/4); `min_duration` extends a short cue when the gap allows and records nothing; a single 60-char word at cpl 42 produces exactly one `Violation(rule="cpl")`; cps violation reported when 100 chars land in 1 s; cue gap enforced; determinism (build twice, compare).
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(exports): subtitle segmentation engine with a compliance report`

---

### Task 3: Text writers — SRT, VTT, TXT, CSV, JSON, Markdown

**Files:**
- Create: `scribe/exports/srt.py`, `vtt.py`, `txt.py`, `csvw.py`, `jsonw.py`, `markdown.py`, `tests/test_exports_text.py`, golden files under `tests/golden/exports/`
- Modify: `scribe/exports/__init__.py` (register writers)

**Interfaces:**
- `srt.write(doc, options) -> bytes` — cues from `cues.build`; `N\nhh:mm:ss,mmm --> hh:mm:ss,mmm\n<lines>\n\n`; speaker as a leading `NAME: ` on the first line when `options.speakers`.
- `vtt.write` — `WEBVTT\n\n` header; `<v Name>` voice tags when speakers on; cue identifiers as numbers.
- `txt.write` — four layouts: `cue` (one cue per line with optional timestamp), `paragraph` (render.paragraphs, blank line between, optional `[m:ss]` prefix), `monologue` (all text, no speakers, wrapped at 80), `turn` (`NAME (m:ss): text` per speaker turn).
- `csvw.write` — columns `index,start,end,duration,speaker,text,chars,cps,mean_confidence` (confidence columns only with `include_confidence`); `utf-8-sig` default; RFC 4180 quoting via `csv.writer`.
- `jsonw.write` — canonical: `{"schema_version": 1, "media": {...}, "run": {...}, "speakers": {...}, "words": [...], "segments": [...]}` with `ensure_ascii=False`, sorted keys, 2-space indent.
- `markdown.write` — optional YAML front matter (title, date, duration, model, language, speakers), `## Speaker` headings per paragraph, sentence timestamps as `[m:ss](#t=12.3)` links when `timestamps != "none"`.
- [ ] **Step 1: failing tests** — one golden file per writer from the seed transcript (`tests/seed.py` deterministic words) with default options; plus targeted asserts: SRT timestamp uses a comma, VTT a dot; VTT contains `<v ` when speakers on and not when off; TXT `monologue` has no speaker names; CSV starts with a BOM and parses back with `csv.reader` to the cue count; JSON round-trips and `schema_version == 1`; Markdown front matter present only when requested; `crlf=True` yields `\r\n` and no bare `\n`; `cp1252` encoding of a text with `é` encodes.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS (goldens committed)
- [ ] **Step 5: commit** `feat(exports): SRT, VTT, TXT, CSV, JSON and Markdown writers with golden files`

---

### Task 4: DOCX and the self-contained HTML bundle

**Files:**
- Create: `scribe/exports/docx.py`, `scribe/exports/html_bundle.py`, `scribe/templates/export_bundle.html`, `tests/test_exports_rich.py`
- Modify: `requirements.txt` (`python-docx`), `scribe/exports/__init__.py`

**Interfaces:**
- `docx.write(doc, options) -> bytes` — title heading, metadata line, one paragraph per `render.paragraphs` paragraph with the speaker as a bold run and, when timestamps on, a hyperlink run `m:ss` pointing at `scribe://media/<id>#t=<s>` (a hand-rolled `w:hyperlink` element, since python-docx has no public API for it).
- `html_bundle.write(doc, options, *, audio: bytes | None = None, audio_mime: str = "audio/mp4") -> bytes` — one HTML file: inline CSS, the transcript markup from the same Jinja macros the app uses (speaker headings, `data-start` timestamps, word spans), a `<audio>` element whose `src` is a `data:` URL when `audio` is given (the route caps this at 25 MB; above that it writes a sidecar file next to the HTML and references it relatively), and a ~60-line inline script for click-to-seek and highlight (no htmx, no external assets). This is the Share artifact.
- [ ] **Step 1: failing tests** — DOCX opens with `python-docx`, has the title as the first heading, one paragraph per rendered paragraph, a bold run per speaker, and a hyperlink element per timestamp when on; HTML bundle contains no `http://`/`https://` references, has a `data:` audio src when audio given, a relative `src` when not, and every `data-start` from the transcript; the bundle validates as parseable by `html.parser` with balanced `<script>`.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(exports): DOCX writer and the self-contained HTML share bundle`

---

### Task 5: Export routes, dialog with live preview, presets, write-to-folder, bulk

**Files:**
- Create: `scribe/web/exports_ui.py`, `scribe/templates/export_dialog.html`, `scribe/templates/_export_preview.html`, `tests/test_web_exports.py`
- Modify: `scribe/web/__init__.py` (router), `scribe/templates/transcript.html` and `_media_rows.html` (enable the Export entries), `scribe/web/settings.py` (preset management section)

**Interfaces:**
- `GET /media/{id}/export` — dialog fragment: format checkboxes, preset `<select>` (built-ins + saved), the option fields grouped (Timestamps / Speakers / Subtitles / File), a destination choice (Download · Write to folder [path, default: the media's source folder]), and a preview pane loaded by `hx-post="/media/{id}/export/preview" hx-trigger="change, load"`.
- `POST /media/{id}/export/preview` — renders the first 10 cues for the first selected subtitle format (or the first 10 lines of TXT/MD otherwise) plus the compliance summary (`N violations: cps ×2, cpl ×1`).
- `POST /media/{id}/export` — `destination=download`: one format → the file with `Content-Disposition`; several → a ZIP; `destination=folder`: writes each file with `filename_for` into `path` (must pass `fsbrowse.is_allowed`), returns a fragment listing the written paths.
- `POST /media/bulk` gains `action=export` with the same option fields: writes into the folder (download of N files becomes one ZIP streamed from an in-memory buffer; documented cap 500 MB → refuse with a message to use write-to-folder).
- Presets: `POST /settings/presets` (name + options JSON) and `POST /settings/presets/{id}/delete`; the dialog's `<select>` applies a preset client-side by filling the fields (`app.js`, small).
- Enable the previously disabled Export entries in the transcript rail and the library ⋯ menu.
- [ ] **Step 1: failing tests** — dialog renders with all 8 formats and 5 built-in presets; preview returns 10 cues and the violation summary for the seed transcript at cpl 20; export of `srt` downloads with the right filename and bytes equal to `srt.write`; export of three formats returns a ZIP with three members; write-to-folder creates the files and refuses a path outside the roots (403); bulk export over two ids writes six files for three formats; saving a preset makes it appear in the dialog; the rail entry is a link, no longer disabled.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(web): export dialog with live preview, presets, write-to-folder and bulk export`

---

### Task 6: CLI — `python -m scribe.export`

**Files:**
- Create: `scribe/exports/cli.py`, `scribe/export/__main__.py` (thin: `from scribe.exports.cli import main`), `tests/test_exports_cli.py`

**Interfaces:**
- `python -m scribe.export <media_id | --all | --folder ID> --format srt [--format vtt …] [--preset youtube] [--out DIR] [--cpl 42 …]` — same `ExportOptions` fields as flags; `--out` defaults to the current directory; prints one line per file written; exit 1 with the reason when a media has no transcript.
- [ ] **Step 1: failing tests** — export one media to a tmp dir produces the file; `--all` exports every media with a current run and skips the rest with a message; a bad format → exit 2 with usage; `--preset youtube` yields cues ≤ 32 chars per line.
- [ ] **Step 2: run** → FAIL
- [ ] **Step 3: implement**
- [ ] **Step 4: run** → PASS
- [ ] **Step 5: commit** `feat(exports): command-line export over the same writers`

---

## Self-review (done at authoring time)

- Spec §5 coverage: eight formats (T3, T4), segmentation engine with constraints and compliance report (T2), timestamp granularity/format and speaker toggle (T1 options, applied in T3/T4), Advanced Export dialog with live preview (T5), presets (T1, T5), write-to-folder and bulk (T5), API+CLI (T5, T6), filename template and encodings/line endings (T1, T3). PDF stays deferred per spec.
- ADR-003: all writers are pure over `TranscriptDoc`; the loader is the only DB reader; nothing writes.
- Type consistency: `TranscriptDoc` and `ExportOptions` from T1 are the only inputs to every writer; `cues.build` from T2 is used by SRT/VTT/CSV/TXT-cue; `render.paragraphs` (Phase 3) by TXT-paragraph/MD/DOCX/HTML.
- No placeholders; goldens are named per writer and committed with the writer.
