---
id: "ADR-011"
title: "Ship each platform as a small launcher that installs the locked environment with uv on first run"
status: "Accepted"
date: "2026-09-19"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "packaging"
  - "release"
  - "installer"
  - "ci"
aliases:
  - "launcher"
  - "uv sync"
  - "release workflow"
  - "Inno Setup"
  - "AppImage"
  - "dmg"
components:
  - "packaging.launcher"
  - ".github/workflows/release.yml"
symbols: []
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-011 Ship each platform as a small launcher that installs the locked environment with uv on first run

## Status

Accepted, 2026-09-19.

## Status History

```yaml
status_history:
  - date: 2026-09-11
    status: Proposed
    changed_by: Claude Opus 5 (agent)
    reason: "Initial proposal: per-OS executables built by release automation (user request 2026-09-11)"
    changed_via: adr-kit
  - date: 2026-09-19
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert on 2026-09-19, on the record of two published releases. The launcher decided here is built and started by CI on all three platforms and shipped in v0.5.0 and v0.5.1: run 35447454324 built each artifact on its own runner and proved it starts, and the v0.5.1 release carries the Windows installer, the macOS dmg and the Linux AppImage. The open question about the macOS ffmpeg build was answered by the build script that produced those artifacts."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

Installing MyScribe today means cloning, making a venv, and running pip
against a different requirement file per OS - with the CUDA (Compute
Unified Device Architecture, NVIDIA's GPU toolkit) index URL on Windows, or
pip silently installs a CPU (central processor) torch (ADR-006). The user asked on
2026-09-11 for deployable executables on Windows, Linux and macOS, built by
release automation that can be trusted to produce them on every release.

The facts that shape the answer, measured or sourced that day:

* The Windows environment is about 3.2 GB to download; the torch
  2.10.0+cu128 wheel alone is 2,867 MB. GitHub Releases refuse any single
  asset over 2 GiB, so a bundle with torch inside cannot be one download.
* The app starts its runner children as `sys.executable -m scribe.runner`
  (`scribe/supervisor.py:214`); `cuda_setup` finds `torch/lib` through a
  `site-packages` entry on `sys.path` (`scribe/cuda_setup.py:30-37`); the
  data directory, `.env` and the doctor's smoke clip are resolved relative
  to the source tree. A frozen interpreter (PyInstaller, Nuitka) breaks all
  four; a real venv breaks none of them.
* One universal `uv.lock` resolves the current pins for all three platforms
  (ADR-012). On an Apple M2 an environment synced from it with a
  uv-managed Python was package-for-package identical to the verified venv
  (129 packages) and passed the doctor with `transcription on mlx,
  diarization on mps`.
* Precedent: ComfyUI Desktop and the InvokeAI launcher ship a small app that
  installs torch with uv on first start; aTrain, Buzz and noScribe freeze
  torch into multi-gigabyte installers and split or self-host them.
* GitHub-hosted runners have no GPU. CI (continuous integration, the
  workflows that build on every change) can build and smoke-test the CPU
  path; the CUDA and Metal paths stay verified on real hardware (ADR-006's
  "any pin bump must re-run the doctor").

## Decision Drivers

* One click to install on each OS, no Python or pip knowledge required.
* Every release's artifacts come from CI, from a tag, reproducibly.
* No asset over GitHub's 2 GiB limit.
* The runner-child and CUDA-DLL (dynamic-link library, the Windows shared
  object torch ships its CUDA in) mechanisms keep working unchanged.
* Updates should not re-download 3 GB when only the app changed.

## Considered Options

* A per-OS launcher plus uv: a small native installer carrying the app
  source, `uv.lock`, a pinned `uv` binary and ffmpeg; the first launch
  creates the environment with `uv sync --frozen`.
* A frozen offline bundle per OS (PyInstaller onedir with torch inside).
* Status quo: clone and pip, documented in the README.

## Decision Outcome

Chosen option: **a per-OS launcher plus uv**, chosen by the user on
2026-09-11, because it is the only option that keeps every asset small,
keeps the runner and DLL (dynamic-link library) mechanisms untouched, and makes an update an
incremental sync rather than a full re-download.

The launcher is a small stdlib-only Python program frozen per OS. It holds
no ML code; it prepares a per-user home (`%LOCALAPPDATA%\MyScribe`,
`~/Library/Application Support/MyScribe`, `${XDG_DATA_HOME:-~/.local/share}/MyScribe`)
with the uv-managed Python, the environment and the data directory, runs
`uv sync --frozen` when the shipped lock differs from the one last synced,
then starts `python -m scribe` from that environment and opens the browser.
Per OS it ships as an Inno Setup installer (Windows, per-user, no admin), a
`.app` in a `.dmg` (macOS arm64) and an AppImage (Linux x86_64).

A release is a `v*` tag. The release workflow checks that the tag matches
`scribe.__version__`, runs the suite on all three OSes, builds the three
artifacts, installs each on its own runner and smoke-tests it (sync, doctor
without GPU, the app answering `/health`), then publishes them with
`SHA256SUMS` and build-provenance attestations. Artifacts are unsigned for
now (user decision 2026-09-11); the signing steps exist and run only when
their secrets are configured.

### Confirmation

* The release workflow's smoke job passes on `windows-latest`, `macos-14`
  and `ubuntu-latest` for the artifact it just built.
* A published release has the three artifacts, `SHA256SUMS`, and an
  attestation per artifact that `gh attestation verify` accepts.
* On real hardware, the installed app passes the doctor with the GPU
  checks: the RTX (NVIDIA's consumer GPU line) 3080 on Windows, an Apple
  Silicon Mac.

## Decision Contract

### Must

* Build every release artifact in CI from a `v*` tag; no hand-built
  artifact is published.
* Install the environment from the committed `uv.lock` with
  `uv sync --frozen`, using a `uv` binary whose version is pinned and whose
  checksum is verified in the build.
* Keep each release asset under 2 GiB.
* Smoke-test each artifact on its own OS before publishing it.
* Keep the launcher stdlib-only: it must work before the environment exists.

### Must Not

* Bundle torch, CTranslate2 or pyannote into the launcher.
* Put the environment or the data directory inside the install directory
  (Program Files, the `.app`, the AppImage are read-only or replaced on
  update).
* Publish an artifact whose tag and `scribe.__version__` disagree.

### Exceptions

* Code signing is deferred by the user (2026-09-11); the unsigned
  first-open steps are documented in the README until certificates exist.

### Verification

* `.github/workflows/release.yml` (build, smoke, publish jobs).
* `tests/test_launcher.py` for the launcher's state and sync decisions.
* `gh attestation verify <artifact> -R rvdbreemen/MyScribe`.

## Consequences

### Positive

* Installers of tens of megabytes on every OS; no split downloads.
* Updates re-sync only the wheels that changed; torch is not fetched again
  when only the app changed.
* The same `uv.lock` serves developers, CI and users.

### Negative

* The first launch needs internet and time: about 3.2 GB on Windows, with
  the PyTorch index and PyPI reachable. The launcher shows progress and
  says so up front.
* Unsigned artifacts meet Gatekeeper and SmartScreen warnings until
  certificates are bought (Apple Developer ID; for Windows an OV
  (organization-validated) or EV (extended-validation) code-signing
  certificate - Azure Artifact Signing is open to individuals only in the
  US and Canada).
* The repository is private: release downloads need a GitHub account with
  access, and Actions minutes are billed (macOS minutes count ten times),
  so the full matrix runs on tags and pull requests, not on every push.
* CI cannot exercise CUDA or Metal; that verification stays manual.

## Pros and Cons of the Options

### Per-OS launcher plus uv

* Good, because the app runs in a real venv: no code paths change to
  survive freezing.
* Good, because assets stay small and updates are incremental.
* Bad, because the first launch depends on the network and the indexes.

### Frozen offline bundle

* Good, because it works offline once downloaded.
* Bad, because the Windows bundle is about 2.9 GB, over the per-asset
  limit, so it must be split or hosted elsewhere, and every update is a
  full download.
* Bad, because freezing needs an argv dispatch for the runner, a frozen
  branch in `cuda_setup`, hidden imports for pyannote and mlx, and moved
  data paths - four ways to break the pipeline that a venv never has.

### Status quo

* Good, because it costs nothing.
* Bad, because it is exactly what the user asked to replace.

## Open Questions

- [x] Which LGPL (GNU Lesser General Public License) ffmpeg/ffprobe build to ship for macOS arm64 (Windows and Linux: BtbN's LGPL builds), or rely on Homebrew's there. — **Answered 2026-09-19 by User: Robert van den Breemen:** MyScribe builds its own ffmpeg for macOS arm64 rather than relying on Homebrew's, because a launcher that assumes Homebrew is a launcher that fails on a machine without it. packaging/build_ffmpeg_macos.sh builds 8.1.2 from the FFmpeg release source tarball, checked against its sha256 and its PGP (Pretty Good Privacy) signature on 2026-09-11; the configure line omits --enable-gpl, so the result is LGPL, and --disable-autodetect keeps it hermetic - nothing from Homebrew links in. Built on the M2 in 1 m 52 s, it links only libSystem and three system frameworks and does every operation the app asks of it. Windows and Linux ship BtbN's LGPL builds of the same version, pinned by sha256 in packaging/tools.json.

## Related Decisions

* ADR-012 (the lockfile this launcher installs from; successor to ADR-006).
* ADR-001 (the runner child the launcher's environment must keep working).

## References

* https://docs.astral.sh/uv/guides/integration/pytorch/
* https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
* https://github.com/Comfy-Org/desktop, https://github.com/invoke-ai/launcher
* https://github.com/chidiwilliams/buzz, https://github.com/JuergenFleiss/aTrain
* `scribe/supervisor.py`, `scribe/cuda_setup.py`, `scribe/paths.py`.

## Enforcement

```json
{
  "forbid_pattern": [
    {"pattern": "^\\s*(import|from)\\s+(scribe|torch|faster_whisper|ctranslate2|pyannote|fastapi|uvicorn)\\b", "path_glob": "packaging/launcher/**", "message": "The launcher runs before the environment exists: stdlib only (ADR-011)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
