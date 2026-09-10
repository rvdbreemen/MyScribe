---
id: TASK-022
title: >-
  The settings page imports torch into the web process (ADR-001):
  test_the_web_process_never_imports_a_model_runtime fails on main
status: To Do
assignee: []
created_date: '2026-09-08 18:49'
updated_date: '2026-09-10 15:40'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
DIAGNOSIS CONFIRMED 2026-09-10 by reading the chain rather than by the import hook alone.

scribe/doctor.py:374 returns Check(name='accel', ok=True, detail=accel.describe()), and accel.describe() reaches accel.cuda_available(), which is literally:

    def cuda_available() -> bool:
        try:
            import torch
        except ImportError:
            return False
        return bool(torch.cuda.is_available())

The settings page renders a doctor context, so opening /settings imports torch into the web process - 687 modules - which is what ADR-001 forbids and what test_the_web_process_never_imports_a_model_runtime catches.

The shape of the fix follows from ADR-001 rather than from the symptom: the runner measures, the web process reports. The accel line the settings page shows should come from a value the doctor JOB wrote (it already runs in a runner child, where importing torch is the whole point), not from asking the question live in the request. Making describe() lazy or guarded would leave the web process one call away from the same violation; moving the measurement to where a model may live removes the possibility.

Not started: this is TASK-022's own work and it is queued behind the feature tasks.
<!-- SECTION:NOTES:END -->
