---
id: TASK-019
title: 'MyScribe runs on Windows, macOS and Linux'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 21:16'
updated_date: '2026-09-06 21:44'
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
- [x] #1 The test suite passes on Linux (WSL) with the CPU stack; every failure that was Windows-specific is fixed or the test is marked with the platform it needs
- [x] #2 python -m scribe starts on Linux, transcribes a file end to end (CPU or CUDA), and the doctor reports the platform honestly
- [x] #3 A README documents install and first run for Windows, macOS and Linux, including the CUDA index for Linux/Windows and the CPU/MPS situation on macOS
- [x] #4 The GPU path on Linux is verified with the cu128 wheels if the WSL GPU is reachable; otherwise the CPU path is measured and the GPU step is marked unverified
- [x] #5 macOS is stated as unverified in the README and the task notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Copy the tree into WSL Ubuntu 24.04, venv with requirements.txt (CPU), static ffmpeg in ~/.local/bin (no sudo here). 2. Run the suite on Linux; fix or platform-mark what fails. 3. Start the app on Linux, transcribe the 30 s fixture on CPU, run the doctor. 4. Try the cu128 wheels in WSL for the GPU path. 5. Write README.md with per-platform steps; mark macOS unverified.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Linux (WSL2 Ubuntu 24.04, RTX 3080, no sudo): venv needed pip bootstrapped (python3-venv absent); static ffmpeg 7.0.2 in ~/.local/bin. requirements-ml.txt (GPU pins minus +cu128) installs torch 2.8.0+cu128 from PyPI with CUDA reachable. Doctor: all OK, gpu-smoke 75 words/30 s in 6.4 s. App on 4299: /transcribe/path with tests/fixtures/clip30.wav -> job 1 done, all stages, device cuda, pyannote community-1 (HF token from the copied .env), xrt 0.42. Suite: first run 49 failed, all in test_ingest_watching (tmp_path outside the home-directory browse root; junction test called cmd). Fixed in tests; rerun: 1565 passed + 79 passed/2 skipped. Windows watching tests 81 passed. macOS: not run; README says so. Commit pushed to github.com/rvdbreemen/MyScribe (private).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added requirements-ml.txt and README.md with per-platform install; fixed two Windows-assuming tests. Verified end to end on Linux with the GPU; macOS unverified and marked as such.
<!-- SECTION:FINAL_SUMMARY:END -->
