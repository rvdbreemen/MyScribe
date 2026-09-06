# MyScribe

Local transcription with speakers, corrections, exports and an AI panel. One
web process on `127.0.0.1:4242`, a runner child per job, SQLite for
everything; nothing leaves the machine unless you pick a cloud provider.

Runs on Windows, Linux and macOS. Windows and Linux are verified on the
author's machine (Windows 11 native, Ubuntu 24.04 under WSL2, both with an
RTX 3080). **macOS is not verified**: the code has no platform-specific
branches left that would stop it, the CPU path is the same one Linux uses,
but nobody has run it there yet. If you do, the doctor's output is the bug
report.

## What you need

| | Windows | Linux | macOS |
|---|---|---|---|
| Python | 3.12 | 3.12 (`python3-venv` for a venv with pip) | 3.12 (`brew install python@3.12`) |
| ffmpeg + ffprobe | on `PATH` (`winget install Gyan.FFmpeg`) | `apt install ffmpeg`, or a static build in `~/.local/bin` | `brew install ffmpeg` |
| GPU | NVIDIA + CUDA wheels from the cu128 index | NVIDIA: PyPI's torch brings CUDA along; otherwise CPU | CPU (Apple GPU: see below) |
| Optional | `yt-dlp` for links, `node` for the recorder tests | same | same |

Transcription is faster-whisper on CTranslate2. On a CUDA card it runs
`large-v3-turbo` in float16 (about 14x realtime on the 3080); on a CPU it runs
int8, several times slower than realtime on a laptop. CTranslate2 has no Metal
backend, so an Apple GPU does not help transcription; PyTorch's MPS backend
exists but the diarization pipeline is run on the CPU everywhere but CUDA, on
purpose, until somebody measures it.

## Install

Clone, make a venv, install two requirement files. The first is the web app,
the second the ML stack; they are separate because the second is 3 GB.

### Linux and macOS

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-ml.txt
.venv/bin/python -m scribe.doctor          # add --no-gpu to skip the model load
.venv/bin/python -m scribe                 # opens http://127.0.0.1:4242
```

`requirements-ml.txt` is `requirements-gpu.txt` without the `+cu128` build
tags. On Linux, PyPI's torch depends on the `nvidia-*` wheels, so a machine
with an NVIDIA driver gets the GPU without an extra index; the doctor says
which device it found. On macOS the same file installs the CPU + MPS build.

If `python3 -m venv` gives you a venv without `pip` (Debian and Ubuntu do
that until `python3-venv` is installed), either install that package or
bootstrap pip: `curl -sL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python`.

### Windows

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -r requirements-gpu.txt --index-url https://download.pytorch.org/whl/cu128 --extra-index-url https://pypi.org/simple
.venv\Scripts\python -m scribe.doctor
.venv\Scripts\python -m scribe
```

The CUDA index matters: without it pip installs a CPU-only torch and says
nothing. ADR-006 explains the pin set and why the app registers torch's DLL
directory before CTranslate2 loads.

### Then

- Copy `.env.example` to `.env` for the optional keys: a Hugging Face token
  unlocks the better diarization model, an OpenAI or OpenRouter key the cloud
  side of the AI panel. Ollama on the machine needs no key.
- Data lives in `./data` (database, media store, models, logs). Point
  `SCRIBE_DATA_DIR` elsewhere to move it.
- `python -m scribe --port 4299 --no-supervisor --no-browser` runs a second
  instance for poking at without disturbing the one you use.

## Check that it works

```sh
python -m scribe.doctor
```

Says, per line, what it found: Python, SQLite, ffmpeg, the data directory,
disk, the database, the GPU runtime (or "CPU build" when torch has no CUDA)
and, unless `--no-gpu`, a real model load. Run it before blaming the code.

Then put a file through the Transcribe dialog. The job page shows the text
as it is decoded.

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

`docs/adr/` holds the architecture decisions: one web process and a runner
child per job (ADR-001), SQLite as the only coordination (ADR-002), words as
the canonical transcript (ADR-003), the default model (ADR-004), the
preloaded waveform for diarization (ADR-005), the CUDA pins (ADR-006), and
the application log as observation only (ADR-007).
