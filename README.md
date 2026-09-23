# MyScribe

Local transcription with speakers, corrections, exports and an AI panel. One
web process on `127.0.0.1:4242`, a runner child per job, SQLite for
everything; nothing leaves the machine unless you pick a cloud provider.

Runs on Windows, Linux and macOS. Windows and Linux are verified on the
author's machine (Windows 11 native, Ubuntu 24.04 under WSL2, both with an
RTX 3080); macOS on an Apple M2 (macOS 26.3): the doctor green and a
two-speaker file transcribed on MLX and diarized on MPS through the app. An
Intel Mac has not been tried. Wherever something fails, the doctor's output
is the bug report.

## What it does

- **Transcribes** a file, a pasted link, a podcast feed, a YouTube channel or
  playlist, a watched folder, or a recording made in the browser. Whisper on
  the GPU, in windows that each hear 30 s past their cut so a cut is never an
  ending; a second opinion where the decode failed by its own measure.
- **Says who is speaking.** Diarization on the GPU, then an AI pass that
  names the clusters from what they say and never writes over a name a
  person typed. Names follow their voice across a re-transcription.
- **Corrects itself.** A glossary of your names and terms is applied as a
  stage, and a word you retype becomes a rule for next time.
- **Keeps a library**: folders, labels by hand or by an AI pass, full-text
  search, bulk actions, a row that updates itself while a job runs, and a
  private pin that keeps a recording's text on this machine.
- **Plays back exactly.** A VBR MP3 seeks by estimate in every browser, so
  the app makes an AAC copy at ingest and the highlight lands where the clock
  says. `python -m scribe.proxies` makes them for a library that predates it.
- **Exports** to TXT, Markdown, SRT, VTT, DOCX, CSV, JSON and a standalone
  HTML bundle, with presets, and from the command line.
- **Answers questions** about a recording in an AI panel - summary, action
  points, a cleaned reading, a chat - through Ollama on the machine or a
  cloud provider you pick a model for from its own list.

What changed, and when, is in `CHANGELOG.md`.

## What you need

| | Windows | Linux | macOS |
|---|---|---|---|
| [uv](https://docs.astral.sh/uv/) | `winget install astral-sh.uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | `brew install uv` |
| Python | 3.12; uv fetches it when the machine has none | same | same |
| ffmpeg + ffprobe | on `PATH` (`winget install Gyan.FFmpeg`) | `apt install ffmpeg`, or a static build in `~/.local/bin` | `brew install ffmpeg` |
| GPU | NVIDIA + CUDA wheels from the cu128 index | NVIDIA: PyPI's torch brings CUDA along; otherwise CPU | CPU (Apple GPU: see below) |
| Optional | `yt-dlp` for links, `node` for the recorder tests | same | same |

A release artifact needs none of this: it carries its own uv, Python and
ffmpeg. The table is for running from a clone.

Transcription is faster-whisper on CTranslate2. On a CUDA card it runs
`large-v3-turbo` in float16 (about 14x realtime on the 3080); on a CPU it runs
int8, several times slower than realtime on a laptop.

**Apple Silicon** gets the GPU another way. CTranslate2 has no Metal backend,
so on a Mac the app swaps in `mlx-whisper` (Apple's MLX framework) for
transcription when it is installed - the same Whisper weights, converted, on
the Apple GPU, with word timestamps. Diarization (pyannote, PyTorch) runs on
Metal through MPS; OpenTranscribe measured an M2 Max at 2-3.5x slower than an
RTX 3080 on the GPU-bound stages and faster on the CPU-bound ones. The
doctor's `accel` line says what was picked: `transcription on mlx,
diarization on mps` is the good outcome. Measured on an M2 (2026-09-11), a
44 s two-speaker file: transcription 11.7 s and diarization 25.4 s, both
including the model load, which dominates a file that short.

## Install

### From a release

Each release carries a ready-made artifact per platform, built on that
platform and started once by CI before it was published:

| | |
| --- | --- |
| macOS (Apple Silicon) | `MyScribe-<version>-macos-arm64.dmg` |
| Windows (x64) | `MyScribe-<version>-windows-x64.exe` |
| Linux (x86_64) | `MyScribe-<version>-linux-x64.AppImage` |

Nothing is signed yet, so each OS will say so the first time:

* **macOS** - the dmg is ad-hoc signed, so a download carries Apple's
  quarantine flag and Gatekeeper refuses it outright. Open it from Finder with
  **Control-click → Open**, then **Open Anyway**; the dmg's own "Open me first"
  note says the same. Copying it out of the dmg keeps the flag, which is why
  the bundled tools are installed as fresh files rather than copied.
* **Windows** - SmartScreen will warn about an unknown publisher: **More
  info → Run anyway**. It installs for your account only, into
  `%LOCALAPPDATA%\MyScribe`, and leaves your recordings there on uninstall.
* **Linux** - make the AppImage executable (`chmod +x`) and run it.

The first start asks where everything should go, shows the free space on each
drive and what this install will download, and then fetches the speech engine
with its progress. Later starts skip it. The model weights are a separate
download the app makes for itself; `Settings` says what is still missing and
how big it is. The sizes come from `scribe/footprint.json` and
`scribe/models.json`, which carry what each number measured and when, so this
page does not repeat a figure that would go stale.

Verify a download against `SHA256SUMS`, published beside the artifacts:

```sh
sha256sum -c SHA256SUMS --ignore-missing     # Linux
shasum -a 256 -c SHA256SUMS --ignore-missing # macOS
```

```powershell
# Windows: compare the one line for the file you downloaded
(Get-FileHash MyScribe-0.5.1-windows-x64.exe -Algorithm SHA256).Hash.ToLower()
Select-String -Path SHA256SUMS -Pattern windows-x64.exe$
```

There is no build-provenance attestation. GitHub does not offer one for a
user-owned private repository, so `SHA256SUMS` is the part you can check.

### From a clone

Clone, then one command on every OS:

```sh
python install.py                          # the pinned uv, .venv from uv.lock, the questions, the proof
python install.py --start                  # the same, then start the app at http://127.0.0.1:4242
```

`install.py` runs on the Python you already have (3.9 or newer, standard
library only). It fetches the uv that `packaging/tools.json` pins, checks its
sha256, and syncs `.venv` from `uv.lock` with it, frozen - never with the uv
on `PATH`, because an older uv rewrites `uv.lock` wholesale. `pyproject.toml`
holds the pins and `uv.lock` the exact, hashed set per platform (ADR-012):
Windows gets torch built for CUDA 12.8, Linux PyPI's torch with its CUDA
wheels, an Apple Silicon Mac PyPI's CPU + MPS torch plus `mlx-whisper`. The
first sync's size is in `scribe/footprint.json`, and the free space is
checked before it starts. On Windows and Linux a pinned ffmpeg lands in
`.tools/bin` when none is on `PATH`; on a Mac one line says `brew install
ffmpeg`.

It then hands the terminal to `python -m scribe.setup`, which asks only what
is still open, and ends with the doctor's proof report and the start command.
`--check` prints that report alone. `--data-dir DIR` puts the library outside
the clone: a library inside it is deleted by `git clean -fdx`. Re-running is
cheap - a `.venv` synced from the current `uv.lock` is left alone - and
`scripts/start.ps1` and `scripts/start.sh` say `run python install.py` after
a pull that moved the lock. While a MyScribe runs from the same checkout the
sync is refused; stop it first, or pass `--no-sync`.

The environment's python is `.venv\Scripts\python` on Windows and
`.venv/bin/python` elsewhere. `python -m scribe.doctor` (add `--no-gpu` to
skip the model load) says on its `accel` and `gpu-runtime` lines which device
each stage will use; ADR-012 explains why the app registers torch's DLL
directory before CTranslate2 loads on Windows.

To change a pin, edit `pyproject.toml` and run `uv lock` with the pinned uv
(`.tools/bin/uv` after an install); commit both files and re-run the doctor
on real hardware after any change to the ML stack.

### Then

- Copy `.env.example` to `.env` for the optional keys: a Hugging Face token
  unlocks the better diarization model, an OpenAI or OpenRouter key the cloud
  side of the AI panel. Ollama on the machine needs no key.
- Data lives in `./data` (database, media store, models, logs). Point
  `SCRIBE_DATA_DIR` elsewhere to move it.
- `python -m scribe --port 4299 --no-supervisor --no-browser` runs a second
  instance for poking at without disturbing the one you use.
- `scripts/start.ps1` and `scripts/start.sh` start the app with the venv's
  python, refuse a second instance on a port that already answers, and take
  `--detached` (`-Detached`) for a run that outlives the terminal - which is
  what a long transcribe queue wants.

## Configuration

`.env.example` is the reference and says what each key is for; copy it and
fill in what you need. Three rules it is worth knowing before you go looking
for them:

- **A key set under Settings in the app wins** over the environment. For
  OpenRouter the order is Settings, then `OPENROUTER_TOKEN`, then
  `OPENROUTER_API_KEY`.
- **A machine-wide environment variable is readable by every account on the
  machine.** `.env` is not, so prefer the file.
- **The private pin overrules every key.** A file or folder pinned private
  refuses every non-local provider outright, whatever is configured. Ollama
  still answers, because it never leaves the machine.

`SCRIBE_DATA_DIR` decides where everything lands. Inside it:

| | |
| --- | --- |
| `myscribe.db` | the library, transcripts, jobs and settings (SQLite, WAL) |
| `media/` | the originals and their AAC playback copies |
| `models/` | the Whisper and diarization weights |
| `work/` | scratch space for a running job |
| `logs/` | the application log - observation only, nothing reads it to decide (ADR-014) |

Back up `myscribe.db` and `media/`; the rest is rebuildable.

## Check that it works

```sh
python -m scribe.doctor
```

Says, per line, what it found. A healthy run on the author's Windows machine:

```
[OK  ] python       3.12.9 at ...\.venv\Scripts\python.exe
[OK  ] sqlite       3.45.3, FTS5 available
[OK  ] ffmpeg       ffmpeg version 8.1.2-full_build-www.gyan.dev
[OK  ] ffprobe      ffprobe version 8.1.2-full_build-www.gyan.dev
[OK  ] yt-dlp       yt-dlp 2026.8.19 (31 days old)
[OK  ] data-dir     ...\data writable
[OK  ] disk-space   30.2 GB free at ...\data
[OK  ] database     schema v17 at ...\data\myscribe.db
[OK  ] accel        transcription on cuda, diarization on cuda
[OK  ] diarization  pyannote/speaker-diarization-community-1 reachable with this token
[SKIP] models       1.6 GB still to download: whisper-large-v3-turbo (1.6 GB)
                    -> Run `python -m scribe.models --fetch`.
```

Every line that can fail tells you the command that fixes it. Run the doctor
before blaming the code.

Then put a file through the Transcribe dialog. The job page shows the text
as it is decoded.

The dialog's link tab takes a video, a podcast RSS feed, a YouTube channel or
a playlist - pasted or dropped. A feed or channel lists its episodes with a
filter; tick one or many and press Import, and each becomes its own download
job. Episodes already in the library or already queued are marked as such
the next time the feed is listed.

## From the command line

Everything below runs with the venv's python (`.venv\Scripts\python` on
Windows, `.venv/bin/python` elsewhere) and reads the same data directory as
the app.

| | |
| --- | --- |
| `python -m scribe` | the app. `--port`, `--no-supervisor`, `--no-browser` |
| `python -m scribe.doctor` | can this machine run it. `--no-gpu` skips the model load |
| `python -m scribe.setup` | the first-run answers. `--plan` prints what was found and what is still open as JSON, `--apply-stdin` reads one JSON document of answers, `--status` prints what it would ask about; `--provider`, `--tier`, `--diarize/--no-diarize`, `--fetch-models` answer without the browser. A token never goes on the command line: put it in `HF_TOKEN`, in the environment or `.env`, and `--hf-token` says so and refuses |
| `python -m scribe.models --fetch` | download the weights. `--only <repo>` for one, `--dest` for elsewhere |
| `python -m scribe.export` | export without the browser. Ids, or `--all`, or `--folder`; `--preset`, `--format`, `--out` and the subtitle knobs (`--cpl`, `--max-cps`, `--cue-gap`, …) |
| `python -m scribe.proxies` | make the exact-seeking AAC copy of every recording that needs one. `--dry-run` lists them and makes nothing |

`python -m scribe.export --help` prints the full set; there are more
subtitle and filename options than fit here.

## When something is wrong

| What you see | What it is | What to do |
| --- | --- | --- |
| SmartScreen: "unknown publisher" | The installer is unsigned; no certificate is configured | **More info → Run anyway** |
| macOS kills the app on open, or "damaged" | Gatekeeper's quarantine flag on an ad-hoc signed app | **Control-click → Open**, then **Open Anyway** |
| The doctor says `gpu-runtime: CPU build` | torch was installed without CUDA | `uv sync` again from the lock; on Windows the cu128 index is in `pyproject.toml`, and plain pip will silently give you a CPU torch |
| `accel` says `cpu` on a Mac | `mlx-whisper` is missing | It is in the lock for macOS-arm64; re-run `uv sync` |
| The doctor fails on disk space | It wants headroom for the weights and a job's scratch space | Free space, or point `SCRIBE_DATA_DIR` at a bigger volume |
| A finished transcript has no speakers and says why | No `HF_TOKEN`, or the conditions were never accepted for the one you have. There is no route that downloads weights without a token: `pyannote/segmentation-3.0`, which the speaker-diarization-3.1 fallback is built from, answered HTTP 401 unauthenticated on 2026-09-22 | Nothing was lost - the job keeps its transcript. Accept the conditions at the model page, then save the token under **Settings → Transcription**, or set `HF_TOKEN` in the environment or `.env`. A pipeline you already have needs no token at all: put it at `MODELS_DIR/pyannote` |
| The whole test suite stalls on Windows | CPython's socketpair emulation behind TestClient (see `pytest.ini`) | Kill it and run the two halves named in `CLAUDE.md` |
| A second launch does nothing visible | One instance already answers on the port | It opens the browser at the running one instead of starting a second server |
| Playback seeks to the wrong place | An old recording has no AAC copy yet | `python -m scribe.proxies` |

## One user, one machine

The app binds `127.0.0.1` and has no login, which is not the same as "only
you can reach it": a browser is a confused deputy. Any page you visit can
auto-submit a form at `http://127.0.0.1:4242/...` and your browser will send
it, and media ids are small integers. `scribe/guard.py` closes both doors on
headers every browser already sends - the `Host` header must name this
machine, which is what a DNS-rebound page cannot fake - so it costs no login
and no token. Read that module before changing how routes are reached.

Do not put the app behind a reverse proxy and call it multi-user. It was
never designed for that, and nothing in it checks who is asking.

## Tests

```sh
.venv/bin/python -m pytest -q          # Linux, macOS
.venv\Scripts\python -m pytest -q      # Windows
```

GPU tests are excluded by default (`-m gpu` runs them). Tests that need
Windows (the loader's error mode, case-insensitive paths, the hidden file
attribute) skip elsewhere and say why. The recorder's browser half runs in
`node` when it is installed and skips otherwise. On Windows the whole-suite
run stalls now and then (CPython's socketpair emulation behind TestClient,
see `pytest.ini`); run the two halves named in `CLAUDE.md` when it does.

Export golden files are regenerated with
`SCRIBE_UPDATE_GOLDENS=1 python -m pytest tests/test_exports_text.py`. That
run is red for every golden that moved and prints the diff; read it, then run
again without the variable. A green run always means the goldens matched,
never that they were rewritten.

CI runs the suite on Windows, macOS and Linux for every pull request and
every push to `main`. `docs/RELEASING.md` describes cutting a release and
what has actually gone wrong doing it.

## Where things are decided

`docs/adr/` holds the architecture decisions, and `docs/adr/ADR-INDEX.md`
lists them all.

**Accepted:** one web process, a supervisor thread and a runner child per job
(ADR-001); words as the canonical transcript, every grouping derived at
render time (ADR-003); the default model and what translate substitutes
(ADR-004); the preloaded waveform for diarization (ADR-005); one uv lockfile
with a per-platform torch source (ADR-012, superseding ADR-006); SQLite in
WAL mode as the only coordination between web, supervisor and runner, one
runner at a time in the claim (ADR-013, superseding ADR-009, which had
superseded ADR-002); the application log as observation only, nothing reads
it to decide (ADR-014, superseding ADR-007); a feed or channel as a
subscription polled on a due date, each new episode one `ingest_url` job
(ADR-008); reasoning as a per-kind hint, bounded by the cap only where the
endpoint enforces one (ADR-010); a per-OS launcher that installs the locked
environment with uv on first run (ADR-011); a missing provider row selecting
no provider, so nothing is sent until somebody has chosen (ADR-016);
first-run setup as one engine behind a JSON contract that every front-end
only renders (ADR-015); and third-party software installed only when it is
absent, shown and agreed to, leaving an Ollama that is there alone (ADR-017).

Nothing is Proposed. The installer's three records were accepted on
2026-09-21; their design is in
`docs/superpowers/specs/2026-09-20-installer-design.md`, what the short
keys in it mean in `2026-09-20-installer-decisions.md`, and the work
under TASK-089, which is under way.

ADR-002, ADR-006, ADR-007 and ADR-009 are history, and each names its
successor.
