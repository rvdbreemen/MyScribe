---
id: "ADR-012"
title: "Pin the whole stack in one uv lockfile with a per-platform torch source, and register torch's DLL directory before CTranslate2 loads"
status: "Accepted"
date: "2026-09-14"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes:
  - "ADR-006"
superseded_by: null
related:
  - "ADR-015"
topics:
  - "dependencies"
  - "gpu"
  - "windows"
  - "packaging"
aliases:
  - "uv.lock"
  - "pyproject.toml"
  - "cuda_setup"
  - "pytorch-cu128"
  - "cudnn_ops64_9.dll"
components:
  - "pyproject.toml"
  - "uv.lock"
  - "scribe.cuda_setup"
symbols:
  - "ensure_cuda_libs"
  - "gpu_smoke"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-012 Pin the whole stack in one uv lockfile with a per-platform torch source, and register torch's DLL directory before CTranslate2 loads

## Status

Accepted, 2026-09-14.
`bin/adr supersede ADR-006 --by ADR-012` (human-gated).

## Status History

```yaml
status_history:
  - date: 2026-09-11
    status: Proposed
    changed_by: Claude Opus 5 (agent)
    reason: "Initial proposal: successor to ADR-006, required by ADR-011's single lockfile"
    changed_via: adr-kit
  - date: 2026-09-14
    status: Proposed
    changed_by: "User: Robert van den Breemen"
    reason: the pin set moved from requirements-gpu.txt into the uv lockfile; ADR-012 keeps the DLL registration
    changed_via: adr-kit lifecycle
  - date: 2026-09-14
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: Robert asked for it on 2026-09-14, after the RTX 3080 run its open question demanded
    changed_via: adr-kit lifecycle
  - date: 2026-09-20
    status: Accepted
    changed_by: Claude (agent, session 2026-09-20)
    reason: Related to ADR-015
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

ADR-006 froze the CUDA (Compute Unified Device Architecture) pin set in `requirements-gpu.txt`, installed with the
cu128 index URL, and derived `requirements-ml.txt` (Linux, macOS) and
`requirements-macos.txt` from it by hand. Three files, each "regenerate
rather than edit", had drifted into being hand-edited twice by 2026-09-10
(commit d772289), and nothing checks that they agree.

ADR-011 installs one environment per OS from a lockfile. The pin set itself
does not change - torch 2.10.0 (+cu128 on Windows), CTranslate2 4.8.2,
faster-whisper 1.2.1, pyannote.audio 4.0.7 - and neither does the DLL
failure ADR-006 exists for: CTranslate2 delay-loads cuDNN and cuBLAS on the
first `transcribe()`, and only a PATH (the operating system's executable search path) prepend of `torch/lib` makes that
resolve on Windows.

Measured 2026-09-11 with uv 0.12.13: a `pyproject.toml` declaring the
current pins, with torch and torchaudio sourced from the cu128 index only
on Windows, locked for win-amd64, macOS-arm64 and linux-x86_64 in 1.1 s
(153 packages, a hash on every wheel). The lock gives Windows
`torch 2.10.0+cu128` from download.pytorch.org, macOS and Linux PyPI's
`torch 2.10.0` (Linux with its CUDA 12 `nvidia-*` wheels), and mlx only on
macOS. Synced on an Apple M2 with a uv-managed Python 3.12.14, the result
matched the verified venv package for package (129) and the doctor passed
with the model load.

## Decision Drivers

* One source of truth for the pins, checked by a tool instead of by care.
* Hashes on every artifact, so an install can be trusted to be the one
  that was tested.
* Keep ADR-006's DLL (dynamic-link library) mechanism and its guards exactly as they are.

## Considered Options

* `pyproject.toml` + a universal `uv.lock`, torch sourced per platform with
  markers.
* Keep the three requirement files and have the launcher pick one per OS.
* Add hashes to the requirement files (`uv pip compile --generate-hashes`)
  and keep three files.

## Decision Outcome

Chosen option: **`pyproject.toml` + universal `uv.lock`**, because it
replaces three hand-maintained files with one generated, hashed lock that
developers (`uv sync`), CI and ADR-011's launcher all install from.

The pins move into `pyproject.toml` unchanged. `[tool.uv.sources]` sends
`torch` and `torchaudio` to an explicit `pytorch-cu128` index for
`sys_platform == 'win32'` only; `[tool.uv] environments` limits the lock to
the three supported platforms; the mlx stack carries a
`sys_platform == 'darwin'` marker. The requirement files are removed.

### Confirmation

* `uv lock --check` passes in CI (the lock matches `pyproject.toml`).
* The doctor exits 0 with `gpu-runtime` and `gpu-smoke` green in an
  environment made by `uv sync --frozen`: on the RTX 3080 (Windows,
  `torch 2.10.0+cu128`, `torch/lib` still carrying `cublas64_12.dll` and
  `cudnn64_9.dll`) and on Apple Silicon (done 2026-09-11, M2).
* `tests/test_cuda_setup.py` and
  `tests/test_doctor.py::test_smoke_timings_never_land_under_the_transcribe_stage`.

## Decision Contract

### Must

* Install from the committed `uv.lock` (`uv sync`, `uv sync --frozen` in CI
  and the launcher); change pins in `pyproject.toml` and re-lock, never by
  hand in the lock.
* Source `torch` and `torchaudio` from the cu128 index on Windows through
  `[tool.uv.sources]` with an explicit index, so no other package can be
  resolved from it.
* Call `cuda_setup.ensure_cuda_libs()` before importing `faster_whisper` or
  `ctranslate2` in any process that will use the GPU.
* `doctor.check_gpu_runtime` fails when `torch.version.cuda is None` on a
  machine that should have CUDA.
* Re-run the doctor on real hardware after any pin bump.

### Must Not

* Import `faster_whisper` at module level in any module the web process
  imports.
* File the doctor smoke timing under stage `transcribe`.
* Resolve torch for Windows from PyPI (a CPU (central processing unit) build).

### Exceptions

* None.

### Verification

* `uv lock --check`; `python -m scribe.doctor` exit 0 on both GPU
  platforms; `tests/test_cuda_setup.py`; `tests/test_doctor.py`.

## Consequences

### Positive

* One file to bump; Dependabot reads `uv.lock` directly.
* Every wheel is hash-checked on install.
* A developer install is one command on every OS: `uv sync`.

### Negative

* Developers need uv (`winget install astral-sh.uv`, `brew install uv`, or
  its install script); plain `pip install -r` is gone.
* torchcodec 0.16 still comes along broken against FFmpeg 8.1 (ADR-005,
  unchanged).
* The Windows half of the confirmation needs the RTX 3080; until it runs,
  this ADR stays Proposed.

## Pros and Cons of the Options

### pyproject + universal uv.lock

* Good, because one lock covers all three platforms, with hashes.
* Bad, because it adds uv as a development prerequisite.

### Three requirement files, launcher picks one

* Good, because nothing changes for developers.
* Bad, because the drift between the files stays unchecked and there are
  no hashes.

### Hashed requirement files

* Good, because pip keeps working.
* Bad, because three files still have to be kept in step.

## Open Questions

- [x] Run the doctor with the GPU checks on the RTX 3080 in an environment made by `uv sync --frozen`. — **Answered 2026-09-14 by User: Robert van den Breemen:** Done 2026-09-14 on the RTX 3080. uv 0.5.9, uv sync --frozen from this lockfile built the environment in 7m19s (117 packages, into a scratch path so the machine's own .venv was untouched). python -m scribe.doctor from it: exit 0, all required checks passed - torch 2.10.0+cu128 with CUDA 12.8 on an RTX 3080 Laptop GPU (16.0 GB (gigabyte)), accel says transcription and diarization both on cuda, and gpu-smoke decoded 75 words from 30 s on cuda/float16 in 16.4 s (23.6 s load). So the DLL registration holds under a uv-made environment as it does under the pip one: CTranslate2 found cuDNN through torch/lib without requirements-gpu.txt existing.

## Related Decisions

* Supersedes ADR-006 on acceptance (same DLL rules; the pins move from
  `requirements-gpu.txt` to `uv.lock`).
* ADR-011 (the launcher that installs from this lock), ADR-005, ADR-001.

## References

* https://docs.astral.sh/uv/guides/integration/pytorch/
* https://docs.astral.sh/uv/concepts/resolution/
* `pyproject.toml`, `uv.lock`, `scribe/cuda_setup.py`, `scribe/doctor.py`.

## Enforcement

```json
{
  "forbid_import": [],
  "forbid_pattern": [
    {"pattern": "record_stage_perf\\(conn, \"transcribe\"", "path_glob": "scribe/doctor.py", "message": "Smoke timings are warmup-dominated; file them under 'smoke', never 'transcribe' (ADR-012)."},
    {"pattern": "^from faster_whisper import|^import faster_whisper", "path_glob": "scribe/**", "message": "Import faster_whisper inside the function that needs it, after ensure_cuda_libs() (ADR-012)."}
  ],
  "require_pattern": []
}
```
