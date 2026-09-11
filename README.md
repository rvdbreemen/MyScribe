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

Clone, then one command on every OS. `pyproject.toml` holds the pins and
`uv.lock` the exact, hashed set for each platform (ADR-009): Windows gets
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
ADR-009 explains why the app registers torch's DLL directory before
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
