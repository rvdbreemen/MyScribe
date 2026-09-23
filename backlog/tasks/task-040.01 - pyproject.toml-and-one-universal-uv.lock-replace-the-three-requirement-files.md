---
id: TASK-040.01
title: pyproject.toml and one universal uv.lock replace the three requirement files
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-23 22:07'
labels:
  - packaging
  - dependencies
dependencies: []
parent_task_id: TASK-040
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-012: three hand-maintained requirement files (gpu, ml, macos) had drifted into hand edits and nothing checked that they agreed. One pyproject with the same pins and a per-platform torch source, locked once for win-amd64, macos-arm64 and linux-x86_64, is what the launcher, CI and developers install from.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 uv lock --check passes and the lock covers win-amd64 (torch 2.10.0+cu128 from the cu128 index), macos-arm64 (PyPI torch plus the mlx stack) and linux-x86_64
- [x] #2 An environment made by uv sync --frozen on the Mac has exactly the verified package versions and passes the doctor with the model load
- [x] #3 The suite passes in an environment made by uv sync
- [x] #4 requirements*.txt are gone; README and CLAUDE.md give uv sync as the install and .venv paths still work
- [ ] #5 The doctor passes with the GPU checks on the RTX 3080 in an environment made by uv sync --frozen
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. pyproject.toml with the direct dependencies pinned (web, exports, ML, security pins; mlx behind a darwin marker; httpx+pytest as the dev group), package=false, environments limited to win-amd64/macos-arm64/linux-x86_64, torch+torchaudio from an explicit cu128 index on win32. 2. Lock once with the old requirement files as constraint-dependencies so the lock reproduces the verified set, then drop the constraints and re-lock (uv keeps locked versions). 3. Compare a lock-built env on the Mac with the verified venv; doctor. 4. uv sync the repo .venv; suite. 5. Replace pip advice in the doctor, urls and watching hints (uv venvs have no pip); README + CLAUDE.md; remove requirement files.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11 on the M2 with uv 0.12.13: uv lock resolves 153 packages for the three platforms, uv lock --check OK. A fresh env from uv sync --frozen (uv-managed CPython 3.12.14) has every verified package at the same version; three are gone on macOS because nothing there needs them - the old files were frozen on Windows: colorama (colorlog/tqdm/pytest, win32 only), tzdata (pandas, win32 only), greenlet (sqlalchemy, linux+win32). Doctor on that env: accel mlx/mps, gpu-runtime OK, gpu-smoke 77 words from 30s. Repo .venv converted by uv sync (uninstalled exactly those 3). Suite from it: 1654 passed, 18 skipped. Behaviour change: the yt-dlp hints now say 'uv lock --upgrade-package yt-dlp && uv sync' (urls.UPDATE_COMMAND) and the doctor's fix hints say uv sync; tests/test_ingest_urls.py assertions moved with them. AC 5 (RTX 3080 doctor from uv sync --frozen) still open.

2026-09-24 (orchestrator). #5 is closed by Robert's fresh-clone run of python install.py (TASK-089.17 #1): that makes .venv with the pinned uv and uv sync --frozen; running .venv/Scripts/python -m scribe.doctor afterwards, without --no-gpu, on the RTX 3080 is the evidence this criterion asks for.
<!-- SECTION:NOTES:END -->
