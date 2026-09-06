---
id: TASK-020
title: 'macOS: Apple Silicon acceleration - MLX for transcription, MPS for diarization'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-06 22:36'
updated_date: '2026-09-06 22:45'
labels: []
dependencies: []
ordinal: 61000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert asked (2026-09-07) for an efficient macOS path, to be run on Jim's MacBook. CTranslate2 has no Metal backend, so faster-whisper stays CPU there; mlx-whisper (Apple MLX) runs large-v3-turbo on the Apple GPU with word timestamps. pyannote runs on MPS (OpenTranscribe measured an M2 Max at 2-3.5x slower than a 3080, far faster than CPU) with PYTORCH_ENABLE_MPS_FALLBACK=1 for the ops MPS lacks. Build: an mlx backend behind transcribe.load_model's interface (same generator/segment shape, so collect_segments and the rest are untouched), mps in diarize.resolve_device, doctor reporting the chosen backend, requirements-macos.txt, README. Unverified here: no Mac on this machine; the doctor on Jim's MacBook is the acceptance run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 On macOS arm64 with mlx-whisper installed, load_model returns the MLX backend and the transcribe stage records device 'mlx'
- [x] #2 The MLX backend yields segments and words in faster-whisper's shape; a fake mlx_whisper module proves it in the suite
- [x] #3 resolve_device returns 'mps' when torch reports it, and the runner sets PYTORCH_ENABLE_MPS_FALLBACK=1
- [x] #4 The doctor says which transcription backend and which diarization device it will use, on every platform
- [x] #5 requirements-macos.txt and the README describe the install; the suite passes on Windows and Linux with the change
- [ ] #6 Acceptance on a real Mac: doctor output and one transcribed file with timings, pasted into the task notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. scribe/accel.py: cuda/mlx/mps/cpu probes, transcription_backend(), diarization_device(), describe(). 2. stages/mlx_backend.py: MlxWhisperModel with faster-whisper's transcribe() shape; repo derived by rule. 3. load_model returns it on Apple Silicon; resolve_device asks accel; MPS fallback env in diarize. 4. Doctor: accel line, Apple-aware gpu-runtime, smoke via load_model. 5. requirements-macos.txt, README. 6. Acceptance on Jim's MacBook.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built and tested blind (no Mac here). Windows: test_stage_transcribe_mlx 10, transcribe+doctor 87 passed. Linux (WSL): mlx+transcribe+diarize+doctor 156 passed; doctor shows 'accel transcription on cuda, diarization on cuda'. Open: AC 6, the run on Jim's MacBook. Instructions for that run are in the task's final comment.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-06 22:45
---
Acceptance on the MacBook (Apple Silicon): git clone https://github.com/rvdbreemen/MyScribe && cd MyScribe && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-ml.txt -r requirements-macos.txt && brew install ffmpeg && .venv/bin/python -m scribe.doctor. Expected: 'accel transcription on mlx, diarization on mps' and a gpu-smoke line 'large-v3-turbo on mlx/float16: N words from 30s in X s'. Then .venv/bin/python -m scribe, transcribe a file with speakers on, and paste the job's events (transcribe: device, diarize: pipeline) plus the doctor output here. If anything is red, the doctor line is the bug report.
---
<!-- COMMENTS:END -->
