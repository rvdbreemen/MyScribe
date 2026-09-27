# Development

- [Running from source](#running-from-source)
- [How MyScribe is built](#how-myscribe-is-built)
- [Security model](#security-model)
- [Tests](#tests)
- [Dependencies](#dependencies)
- [Platform status](#platform-status)
- [Architecture decisions](#architecture-decisions)
- [Releases](#releases)

## Running from source

Install with `python install.py` as described in
[Installing MyScribe](installation.md#from-a-clone), then:

```sh
.venv\Scripts\python -m scribe          # Windows
.venv/bin/python -m scribe              # Linux, macOS
```

For a second instance that leaves the one you use alone:

```sh
python -m scribe --port 4299 --no-supervisor --no-browser
```

Run `python -m scribe.doctor` before blaming the code: it says which device
each stage will use (`accel`, `gpu-runtime`) and what is missing.

## How MyScribe is built

| Part | What it is |
| --- | --- |
| Web app | One FastAPI process on `127.0.0.1:4242`, server-rendered pages with htmx. |
| Jobs | A supervisor thread in the web process starts one runner child per job, so a crash or a GPU out-of-memory ends that job and not the app (ADR-001). |
| Storage | One SQLite database in WAL mode; it is also the only coordination between web, supervisor and runner (ADR-013). |
| Transcripts | Words are canonical; every grouping - segments, speakers, subtitles - is derived when rendered (ADR-003). |
| Transcription | faster-whisper on CTranslate2 (CUDA float16, or CPU int8); on Apple Silicon `mlx-whisper` on the Apple GPU. |
| Speakers | pyannote on CUDA, Metal (MPS) or the CPU, followed by an AI pass that names the speakers. |
| Setup | One engine, `scribe.setup`, behind a versioned JSON contract; the launcher window, the terminal and the web app only render it (ADR-015). |
| Launcher | A small per-platform program that installs the locked environment with uv on first start (ADR-011). |

## Security model

The app binds `127.0.0.1` and has no login. That is not the same as "only you
can reach it": a browser is a confused deputy. Any web page you visit can
submit a form to `http://127.0.0.1:4242/...`, and media ids are small
integers. `scribe/guard.py` closes both doors using headers every browser
already sends - the `Host` header must name this machine, which a DNS-rebound
page cannot fake - so it needs no login and no token. Read that module before
changing how routes are reached.

Do not put MyScribe behind a reverse proxy and call it multi-user. It was
never designed for that, and nothing in it checks who is asking.

The one exception is *Receive from phone* (ADR-022): a second, upload-only
listener on the LAN address, reachable only while it is open, behind a
one-time secret in the URL, closing itself after one file or 15 minutes. It
serves no route of the app. See [Receive from phone](receive-from-phone.md).

## Tests

```sh
.venv\Scripts\python -m pytest -q      # Windows
.venv/bin/python -m pytest -q          # Linux, macOS
```

- GPU tests are excluded by default; `-m gpu` runs them.
- Tests that need Windows (the loader's error mode, case-insensitive paths,
  the hidden-file attribute) skip elsewhere and say why. The recorder's
  browser half runs in `node` when it is installed.
- On Windows the whole-suite run sometimes stalls (CPython's socketpair
  emulation behind TestClient, see `pytest.ini`). Run the files separately
  when it does.
- Export golden files are regenerated with
  `SCRIBE_UPDATE_GOLDENS=1 python -m pytest tests/test_exports_text.py`. That
  run fails for every golden that moved and prints the diff; read it, then run
  again without the variable. A passing run always means the goldens matched,
  never that they were rewritten.

CI runs the suite on Windows, macOS and Linux for every pull request and every
push to `main`.

## Dependencies

`pyproject.toml` holds the pins and `uv.lock` the exact, hashed set per
platform (ADR-012): Windows gets torch built for CUDA 12.8, Linux PyPI's torch
with its CUDA wheels, an Apple Silicon Mac PyPI's CPU + MPS torch plus
`mlx-whisper`. On Windows the app registers torch's DLL directory before
CTranslate2 loads; ADR-012 explains why.

To change a pin, edit `pyproject.toml` and run `uv lock` with the pinned uv
(`.tools/bin/uv` after an install) - never with an older uv on `PATH`, which
rewrites `uv.lock` wholesale. Commit both files, and run the doctor on real
hardware after any change to the ML stack.

The sizes MyScribe shows before a download come from `scribe/footprint.json`
and `scribe/models.json`, which record what each number measured and when.

## Platform status

| Platform | Status |
| --- | --- |
| Windows 11 with an NVIDIA GPU | Verified. |
| Linux (Ubuntu 24.04, NVIDIA GPU, also under WSL2) | Verified. |
| macOS on Apple Silicon | Verified once, on an M2 (2026-09-11): the doctor green, a two-speaker file transcribed on MLX and diarized on MPS. What has changed for macOS since then is checked by [docs/macos-acceptance.md](macos-acceptance.md). |
| macOS on Intel | Not tried. |

Wherever something fails, the doctor's output is the bug report.

## Architecture decisions

Decisions that shape the code are recorded in [`docs/adr/`](adr/); the
[ADR index](adr/ADR-INDEX.md) lists every record with its status. Superseded
records stay for history and name their successor. Read the relevant records
before an architectural change.

## Releases

[docs/RELEASING.md](RELEASING.md) describes how a release is cut - the
version, the changelog, CI, the tag, the release notes - and what has gone
wrong doing it before.
