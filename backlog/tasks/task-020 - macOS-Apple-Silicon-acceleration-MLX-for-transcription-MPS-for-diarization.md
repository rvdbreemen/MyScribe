---
id: TASK-020
title: 'macOS: Apple Silicon acceleration - MLX for transcription, MPS for diarization'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 22:36'
updated_date: '2026-09-11 20:55'
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
- [x] #6 Acceptance on a real Mac: doctor output and one transcribed file with timings, pasted into the task notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. scribe/accel.py: cuda/mlx/mps/cpu probes, transcription_backend(), diarization_device(), describe(). 2. stages/mlx_backend.py: MlxWhisperModel with faster-whisper's transcribe() shape; repo derived by rule. 3. load_model returns it on Apple Silicon; resolve_device asks accel; MPS fallback env in diarize. 4. Doctor: accel line, Apple-aware gpu-runtime, smoke via load_model. 5. requirements-macos.txt, README. 6. Acceptance on Jim's MacBook.

7. Regression found by the full suite on the M2 (2026-09-11): check_accelerators sits in CPU_CHECKS, which the settings page runs in the web process, and accel.cuda_available() imports torch - breaking ADR-001 (test_the_web_process_never_imports_a_model_runtime). Move it to the head of GPU_CHECKS, which already means 'imports torch, runs in the doctor job's runner child'; the CLI doctor still prints it.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built and tested blind (no Mac here). Windows: test_stage_transcribe_mlx 10, transcribe+doctor 87 passed. Linux (WSL): mlx+transcribe+diarize+doctor 156 passed; doctor shows 'accel transcription on cuda, diarization on cuda'. Open: AC 6, the run on Jim's MacBook. Instructions for that run are in the task's final comment.

Acceptance on a real Mac, 2026-09-11: Apple M2, macOS 26.3 (25D125), Python 3.12.14 (brew), torch 2.10.0, mlx 0.32.2, mlx-whisper 0.4.3; fresh venv from the three requirement files, pip check clean.
Doctor: exit 0. accel 'transcription on mlx, diarization on mps'; gpu-runtime 'torch 2.10.0 on Apple Silicon: MLX available for transcription, MPS available for diarization'; gpu-smoke 'large-v3-turbo on mlx/float16: 77 words from 30s in 44.0s (load 19.4s)' cold, 8.5s (load 1.5s) warm.
Real run through the app (python -m scribe --port 4299, POST /api/media, diarize on): a 44.3 s two-voice dialogue (macOS say, Samantha and Daniel, 6 alternating turns - synthetic speech, not a recorded meeting). Job 1 failed at diarize: no HF token, and pyannote/segmentation-3.0 - the fallback's 'public' checkpoint - now answers 401 too, so without a token there is no diarization at all. With HF_TOKEN in .env, job 2 done in 38.1 s wall: transcribe 11.7 s (device mlx, float16, 143 words, 6 segments), diarize 25.4 s (community-1, fallback false, resolve_device(None) = mps, 2 speakers, 6 turns), attribute 0 unattributed, xrt 1.16 overall. Speaker turns match the voices on 142/143 words; the miss is 'Thanks.' at 7.00-7.46 s, the first word after the first change of speaker. Both stages are load-dominated at this length.
Regression found by the full suite on the Mac and fixed: check_accelerators was in CPU_CHECKS, which the settings page runs in the web process, and accel.cuda_available imports torch - test_the_web_process_never_imports_a_model_runtime failed (722 torch modules in the web process). Moved to the head of GPU_CHECKS (the doctor job's runner child); the CLI doctor still prints it, --no-gpu drops it with gpu-runtime. Red then green; full suite 1654 passed, 18 skipped. The settings page's doctor job (job 3) stored accel in doctor_last and the page shows it. Why the Windows run in d772289 passed that test was not established.
requirements-macos.txt frozen at the versions above (9 packages); README and requirements-ml.txt no longer say macOS is unverified or diarizes on the CPU.
Not in scope, for a follow-up: the run row's engine column is hard-coded 'faster-whisper' (scribe/stages/transcribe.py:732), so an MLX run says faster-whisper with device mlx in params; .env.example still says the 3.1 fallback needs no token.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-06 22:45
---
Acceptance on the MacBook (Apple Silicon): git clone https://github.com/rvdbreemen/MyScribe && cd MyScribe && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-ml.txt -r requirements-macos.txt && brew install ffmpeg && .venv/bin/python -m scribe.doctor. Expected: 'accel transcription on mlx, diarization on mps' and a gpu-smoke line 'large-v3-turbo on mlx/float16: N words from 30s in X s'. Then .venv/bin/python -m scribe, transcribe a file with speakers on, and paste the job's events (transcribe: device, diarize: pipeline) plus the doctor output here. If anything is red, the doctor line is the bug report.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
macOS on Apple Silicon: faster-whisper's engine has no Metal backend, so transcription swaps in mlx-whisper behind load_model's interface and pyannote runs on MPS. Accepted on an M2 (2026-09-11): doctor exit 0 with 'transcription on mlx, diarization on mps'; a 44 s two-speaker file through the app transcribed on mlx in 11.7 s and diarized on mps in 25.4 s, 142/143 words on the right speaker. The Mac suite caught a regression from this task - the doctor's accel check imported torch in the web process (ADR-001) - fixed by making it a GPU check; full suite 1654 passed. requirements-macos.txt is now frozen from that machine.
<!-- SECTION:FINAL_SUMMARY:END -->
