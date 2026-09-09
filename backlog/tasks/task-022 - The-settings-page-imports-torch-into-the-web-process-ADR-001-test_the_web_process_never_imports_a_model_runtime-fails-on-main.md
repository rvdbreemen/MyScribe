---
id: TASK-022
title: >-
  The settings page imports torch into the web process (ADR-001):
  test_the_web_process_never_imports_a_model_runtime fails on main
status: To Do
assignee: []
created_date: '2026-09-08 18:49'
labels: []
dependencies: []
ordinal: 63000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-08 while running the suite for TASK-021, and reproduced on main in a clean worktree: tests/test_web_settings.py::test_the_web_process_never_imports_a_model_runtime fails with 687 torch modules in sys.modules after GET /settings. The chain, recorded with an import hook: settings.settings_page -> page_context -> doctor_context -> doctor.checks -> check_accelerators -> accel.describe -> accel.transcription_backend -> accel.cuda_available -> import torch. Introduced with the Apple Silicon work (commit 4ed080f). ADR-001's contract says the web process never imports torch; the doctor's accel check needs to answer without importing it in-process (a subprocess probe, a cached answer written by the runner, or reading torch's metadata rather than the module).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GET /settings in a fresh interpreter leaves no torch, ctranslate2, faster_whisper or pyannote module in sys.modules (the existing test passes)
- [ ] #2 The doctor's accel line still says what was picked, on CUDA, CPU and Apple Silicon
<!-- AC:END -->
