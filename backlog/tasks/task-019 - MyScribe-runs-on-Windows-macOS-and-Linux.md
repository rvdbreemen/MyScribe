---
id: TASK-019
title: 'MyScribe runs on Windows, macOS and Linux'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-06 21:16'
updated_date: '2026-09-06 21:17'
labels: []
dependencies: []
ordinal: 60000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert asked (2026-09-06) that MyScribe run on all three. The code already guards its Windows-only parts (msvcrt lock, winreg key lookup, DLL directory registration, CREATE_NO_WINDOW, job objects) and picks cuda or cpu by itself, but nothing has ever been run or tested off Windows, and the install notes assume the CUDA index and .venv/Scripts. Verify on Linux (WSL Ubuntu 24.04 with the RTX 3080 is available here), fix what breaks, make the CPU path first-class for macOS (no CUDA; CTranslate2 has no MPS, so faster-whisper runs int8 on CPU), and write per-platform install steps. macOS cannot be verified on this machine; say so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The test suite passes on Linux (WSL) with the CPU stack; every failure that was Windows-specific is fixed or the test is marked with the platform it needs
- [ ] #2 python -m scribe starts on Linux, transcribes a file end to end (CPU or CUDA), and the doctor reports the platform honestly
- [ ] #3 A README documents install and first run for Windows, macOS and Linux, including the CUDA index for Linux/Windows and the CPU/MPS situation on macOS
- [ ] #4 The GPU path on Linux is verified with the cu128 wheels if the WSL GPU is reachable; otherwise the CPU path is measured and the GPU step is marked unverified
- [ ] #5 macOS is stated as unverified in the README and the task notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Copy the tree into WSL Ubuntu 24.04, venv with requirements.txt (CPU), static ffmpeg in ~/.local/bin (no sudo here). 2. Run the suite on Linux; fix or platform-mark what fails. 3. Start the app on Linux, transcribe the 30 s fixture on CPU, run the doctor. 4. Try the cu128 wheels in WSL for the GPU path. 5. Write README.md with per-platform steps; mark macOS unverified.
<!-- SECTION:PLAN:END -->
