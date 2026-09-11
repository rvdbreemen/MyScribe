---
id: TASK-029.01
title: pyproject.toml and one universal uv.lock replace the three requirement files
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
labels:
  - packaging
  - dependencies
dependencies: []
parent_task_id: TASK-029
ordinal: 71000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-009: three hand-maintained requirement files (gpu, ml, macos) had drifted into hand edits and nothing checked that they agreed. One pyproject with the same pins and a per-platform torch source, locked once for win-amd64, macos-arm64 and linux-x86_64, is what the launcher, CI and developers install from.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 uv lock --check passes and the lock covers win-amd64 (torch 2.10.0+cu128 from the cu128 index), macos-arm64 (PyPI torch plus the mlx stack) and linux-x86_64
- [ ] #2 An environment made by uv sync --frozen on the Mac has exactly the verified package versions and passes the doctor with the model load
- [ ] #3 The suite passes in an environment made by uv sync
- [ ] #4 requirements*.txt are gone; README and CLAUDE.md give uv sync as the install and .venv paths still work
- [ ] #5 The doctor passes with the GPU checks on the RTX 3080 in an environment made by uv sync --frozen
<!-- AC:END -->
