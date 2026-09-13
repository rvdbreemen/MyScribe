---
id: "ADR-006"
title: "Pin the CUDA stack and register torch's DLL directory before CTranslate2 loads"
status: "Superseded"
date: "2026-09-14"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: "ADR-012"
topics:
  - "dependencies"
  - "gpu"
  - "windows"
aliases:
  - "cuda_setup"
  - "requirements-gpu"
  - "cudnn_ops64_9.dll"
components:
  - "scribe.cuda_setup"
  - "scribe.doctor"
symbols:
  - "ensure_cuda_libs"
  - "gpu_smoke"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-006 Pin the CUDA stack and register torch's DLL directory before CTranslate2 loads

## Status

Superseded by ADR-012, 2026-09-14.

## Status History

```yaml
status_history:
  - date: 2026-09-02
    status: Proposed
    changed_by: Claude Fable 5.1 (agent)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-06
    status: Accepted
    changed_by: Robert van den Breemen
    reason: "Accepted by the user in session 2026-09-06 (explicit: 'Accept ADR-006')"
    changed_via: adr-kit lifecycle
  - date: 2026-09-14
    status: Superseded
    changed_by: "User: Robert van den Breemen"
    reason: the pin set moved from requirements-gpu.txt into the uv lockfile; ADR-012 keeps the DLL registration
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Native Windows CUDA (Compute Unified Device Architecture) for faster-whisper fails in a specific, nasty way:
`WhisperModel(device="cuda")` constructs, the model downloads, and only the
first `transcribe()` dies with "Library cudnn_ops64_9.dll is not found",
because CTranslate2 delay-loads cuDNN and cuBLAS on the first compute call.
Two projects in the research corpus and WHYcast itself hit this. A second
trap: a dependency marker of `platform_machine == 'x86_64'` does not match
Windows (`AMD64`), so pip silently installs CPU (central processing unit) torch.

## Decision Drivers

* Reproducible install: one command, one verified pin set.
* The DLL (dynamic-link library) failure must be caught at install time, not on the first real job.
* faster-whisper's Python wrapper is dormant (last release 2025-10) while
  CTranslate2 is active (4.8.2, 2026-08-31): pin the wrapper, take the engine.

## Considered Options

* torch 2.8.0+cu128 + ctranslate2 4.8.2 + faster-whisper 1.2.1 +
  pyannote.audio 4.0.7, with `cuda_setup.ensure_cuda_libs()` registering
  `torch/lib` before any CTranslate2 import.
* WHYcast's recipe: torch 2.3.1+cu118 + `nvidia-cublas-cu12` wheel +
  faster-whisper 1.1.1 + pyannote 3.3.2.
* Docker/WSL2.

## Decision Outcome

Chosen option: **the cu128 pin set with torch/lib registration**, frozen in
`requirements-gpu.txt` on 2026-09-02 after the pin ladder's first rung held on
the first attempt. `ensure_cuda_libs()` registers `torch/lib` (and any
`nvidia/*/bin|lib` wheel dirs) with both `os.add_dll_directory` **and** a PATH (the process's library search path)
prepend: delay-loaded imports resolve through the classic Win32 search order,
which ignores `add_dll_directory` — measured on this machine on 2026-08-24.
The doctor's `gpu-smoke` check transcribes a clip on the card, which is the
only test that exercises the delay-load path.

### Confirmation

`python -m scribe.doctor` exits 0 with `gpu-runtime` (torch 2.8.0+cu128, CUDA
12.8, RTX (NVIDIA's GeForce RTX line) 3080 Laptop) and `gpu-smoke` (75 words from the 30 s clip) green on
the target machine. `tests/test_cuda_setup.py` covers idempotency and the
no-torch case.

## Decision Contract

### Must

* Install the GPU (graphics processing unit) stack from `requirements-gpu.txt` with the CUDA index URL (uniform resource locator)
  in its header.
* Call `cuda_setup.ensure_cuda_libs()` before importing `faster_whisper` or
  `ctranslate2` in any process that will use the GPU.
* `doctor.check_gpu_runtime` fails when `torch.version.cuda is None`.

### Must Not

* Import `faster_whisper` at module level in any module the web process
  imports.
* File the doctor smoke timing under stage `transcribe` (it is warmup-dominated
  and would poison the ETA (estimated time of arrival) median).

### Exceptions

* None.

### Verification

* `python -m scribe.doctor` exit 0; `tests/test_cuda_setup.py`;
  `tests/test_doctor.py::test_smoke_timings_never_land_under_the_transcribe_stage`.

## Consequences

### Positive

* A fresh machine reaches a green GPU smoke with two commands.
* The engine (CTranslate2) stays current while the dormant wrapper is pinned.

### Negative

* torch 2.8 pulls torchcodec 0.16, which is broken against FFmpeg 8.1 — the
  reason ADR-005 exists.
* Any pin bump must re-run the doctor on real hardware; version numbers alone
  prove nothing here.

## Pros and Cons of the Options

### cu128 pin set + torch/lib registration

* Good, because torch's wheels ship the cuDNN 9 DLLs CTranslate2 wants — no
  separate NVIDIA (the GPU vendor) wheels to hunt.
* Bad, because torchcodec comes along broken (handled by ADR-005).

### WHYcast cu118 recipe

* Good, because proven on this exact machine for a year.
* Bad, because it pins an older CTranslate2 and an older pyannote; it is the
  fallback rung, not the choice.

### Docker/WSL2

* Bad, because the user required native Windows without Docker; and it
  changes nothing about the DLL story inside the container.

## Open Questions

* None.

## Related Decisions

* ADR-001 (which process performs the registration), ADR-005.

## References

* `scribe/cuda_setup.py`, `requirements-gpu.txt`, `scribe/doctor.py`.
* WHYcast `whycast/cuda_setup.py` (measurement of 2026-08-24).

## Enforcement

```json
{
  "forbid_import": [],
  "forbid_pattern": [
    {"pattern": "record_stage_perf\\(conn, \"transcribe\"", "path_glob": "scribe/doctor.py", "message": "Smoke timings are warmup-dominated; file them under 'smoke', never 'transcribe' (ADR-006)."},
    {"pattern": "^from faster_whisper import|^import faster_whisper", "path_glob": "scribe/**", "message": "Import faster_whisper inside the function that needs it, after ensure_cuda_libs() (ADR-006)."}
  ],
  "require_pattern": []
}
```
