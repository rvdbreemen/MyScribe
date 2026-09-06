---
id: TASK-020
title: 'macOS: Apple Silicon acceleration - MLX for transcription, MPS for diarization'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-06 22:36'
updated_date: '2026-09-06 22:36'
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
- [ ] #1 On macOS arm64 with mlx-whisper installed, load_model returns the MLX backend and the transcribe stage records device 'mlx'
- [ ] #2 The MLX backend yields segments and words in faster-whisper's shape; a fake mlx_whisper module proves it in the suite
- [ ] #3 resolve_device returns 'mps' when torch reports it, and the runner sets PYTORCH_ENABLE_MPS_FALLBACK=1
- [ ] #4 The doctor says which transcription backend and which diarization device it will use, on every platform
- [ ] #5 requirements-macos.txt and the README describe the install; the suite passes on Windows and Linux with the change
- [ ] #6 Acceptance on a real Mac: doctor output and one transcribed file with timings, pasted into the task notes
<!-- AC:END -->
