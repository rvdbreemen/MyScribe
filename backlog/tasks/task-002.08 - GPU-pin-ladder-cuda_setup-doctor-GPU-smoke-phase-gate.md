---
id: TASK-002.08
title: GPU pin ladder + cuda_setup + doctor GPU smoke (phase gate)
status: Done
assignee: []
created_date: '2026-09-01 20:10'
updated_date: '2026-09-02 02:51'
labels: []
dependencies: []
parent_task_id: TASK-002
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan Task 8. Try pin rungs: (1) torch 2.8+cu128 with faster-whisper 1.2.1, ctranslate2>=4.8.2, pyannote 4.0.x; (2) cu126/torch 2.7; (3) WHYcast recipe torch 2.3.1+cu118, faster-whisper 1.1.1, pyannote 3.3.2. Freeze first fully working rung to requirements-gpu.txt and record why. cuda_setup.ensure_cuda_libs adds torch/lib dll dir before ctranslate2 import. Doctor gpu_smoke transcribes tests/fixtures/clip30.wav, asserts >10 words, writes stage_perf. Known traps in plan comments: weights_only, platform_machine marker, cuDNN9 via torch/lib.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ensure_cuda_libs idempotent, safe without torch
- [x] #2 Doctor GPU smoke green on the 3080 with xRT logged
- [x] #3 requirements-gpu.txt frozen with chosen rung documented
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Pin ladder rung 1 held on the first attempt: torch 2.8.0+cu128, ctranslate2 4.8.2, faster-whisper 1.2.1, pyannote.audio 4.0.7, frozen to requirements-gpu.txt with the CUDA index in its header. cuda_setup registers torch/lib via add_dll_directory and PATH; 4 unit tests cover idempotency and the no-torch case. Doctor GPU smoke green on the RTX 3080: large-v3-turbo, 75 words from the 30s clip. Measured throughput on 5 minutes of Dutch audio with word timestamps and VAD: 14.5x realtime. Smoke timings deliberately filed under stage 'smoke' so the warmup-dominated short-clip number cannot poison eta_seconds(); regression test pins that. Commit 85e693e.
<!-- SECTION:FINAL_SUMMARY:END -->
