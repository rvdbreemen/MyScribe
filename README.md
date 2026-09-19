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

The first start downloads the speech engine - about 3 GB on Windows and Linux,
less on a Mac - and shows its progress. Later starts skip it. The model
weights are a separate download the app makes for itself; `Settings` says what
is still missing and how big it is.

Verify a download against `SHA256SUMS`, published beside the artifacts.

### From a clone

Clone, then one command on every OS. `pyproject.toml` holds the pins and
`uv.lock` the exact, hashed set for each platform (ADR-012): Windows gets
torch built for CUDA 12.8 from PyTorch's index, Linux PyPI's torch with its
CUDA wheels, an Apple Silicon Mac PyPI's CPU + MPS torch plus `mlx-whisper`.
The first sync downloads about 3 GB on Windows and Linux.

```sh
uv sync                                    # makes .venv from uv.lock
.venv/bin/python -m scribe.doctor          # add --no-gpu to skip the model load
.venv/bin/python -m scribe                 # opens http://127.0.0.1:4242
```

On Windows the paths are `.venv\Scripts\python`. Then read the doctor's
`accel` and `gpu-runtime` lines: they say which device each stage will use.
ADR-012 explains why the app registers torch's DLL directory before
CTranslate2 loads on Windows.

To change a pin, edit `pyproject.toml` and run `uv lock`; commit both files.
Re-run the doctor on real hardware after any change to the ML stack.

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

## Check that it works

```sh
python -m scribe.doctor
```

Says, per line, what it found: Python, SQLite, ffmpeg, the data directory,
disk, the database, the GPU runtime (or "CPU build" when torch has no CUDA)
and, unless `--no-gpu`, a real model load. Run it before blaming the code.

Then put a file through the Transcribe dialog. The job page shows the text
as it is decoded.

The dialog's link tab takes a video, a podcast RSS feed, a YouTube channel or
a playlist - pasted or dropped. A feed or channel lists its episodes with a
filter; tick one or many and press Import, and each becomes its own download
job. Episodes already in the library or already queued are marked as such
the next time the feed is listed.

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

## Where things are decided

`docs/adr/` holds the architecture decisions. Accepted: one web process, a
supervisor thread and a runner child per job (ADR-001); words as the
canonical transcript, every grouping derived at render time (ADR-003); the
default model and what translate substitutes (ADR-004); the preloaded
waveform for diarization (ADR-005); the application log as observation only,
nothing reads it to decide (ADR-007); SQLite in WAL mode as the only
coordination between web, supervisor and runner, with the import rules that
enforce it (ADR-009, which supersedes ADR-002); and one uv lockfile with a
per-platform torch source (ADR-012, which supersedes ADR-006). Proposed: the
feed import as a polled subscription (ADR-008), reasoning as a per-kind hint
(ADR-010), and a per-OS launcher that installs the locked environment on
first run (ADR-011).
