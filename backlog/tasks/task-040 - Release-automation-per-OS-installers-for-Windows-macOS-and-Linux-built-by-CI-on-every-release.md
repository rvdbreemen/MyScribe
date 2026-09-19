---
id: TASK-040
title: >-
  Release automation: per-OS installers for Windows, macOS and Linux built by CI
  on every release
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-19 16:58'
labels:
  - packaging
  - release
  - ci
dependencies: []
references:
  - >-
    docs/adr/ADR-011-ship-each-platform-as-a-small-launcher-that-installs-the-locked-environment-with-uv-on-first-run.md
  - >-
    docs/adr/ADR-012-pin-the-whole-stack-in-one-uv-lockfile-with-a-per-platform-torch-source-and-register-torch-s-dll-directory-before-ctranslate2-loads.md
priority: high
ordinal: 70000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Installing MyScribe means cloning, a venv, and a different pip command per OS - with the CUDA index URL on Windows or pip silently installs a CPU torch. The user asked (2026-09-11) for deployable executables on Windows, Linux and macOS, produced by release automation that can be trusted to build them for every release. Decided with the user the same day: a small per-OS launcher that installs the locked environment with uv on first run (ADR-011), one universal uv.lock replacing the three requirement files (ADR-012, successor to ADR-006), unsigned artifacts for now with signing steps that activate when secrets exist, and releases kept in the private repo. Research and a spike (uv.lock resolved for all three platforms; synced on an M2 it matched the verified venv and passed the doctor) are recorded in the ADRs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Pushing a v* tag produces a GitHub Release with a Windows installer, a macOS arm64 dmg and a Linux x86_64 AppImage, plus SHA256SUMS and a build-provenance attestation per artifact, all built by CI
- [ ] #2 Each artifact is installed on a clean runner of its own OS in the release run, syncs its environment and serves /health, and the doctor without GPU passes there
- [ ] #3 A tag that disagrees with scribe.__version__ fails the release before anything is built or published
- [ ] #4 The installed app passes the doctor with the GPU checks on the RTX 3080 (Windows) and on an Apple Silicon Mac
- [x] #5 README explains install per OS including the unsigned first-open steps; docs/RELEASING.md explains cutting a release
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC5 geverifieerd 2026-09-19. README.md regels 64-94 beschrijven de install per platform: een tabel met de drie artefactnamen, en per OS de first-open-stappen voor ongetekende artefacten - macOS Control-click, Open, dan Open Anyway; Windows SmartScreen More info, Run anyway met de installatielocatie %LOCALAPPDATA%\MyScribe; Linux chmod +x. Daaronder de verificatie tegen SHA256SUMS. docs/RELEASING.md (commit d4bdfc0) beschrijft het snijden van een release: de drie plekken waar de versie moet kloppen, de volgorde commit-CI-merge-tag, wat elke job bewijst, de generale repetitie via workflow_dispatch, en de vier dingen die op weg naar v0.5.0 echt misgingen.
<!-- SECTION:NOTES:END -->
