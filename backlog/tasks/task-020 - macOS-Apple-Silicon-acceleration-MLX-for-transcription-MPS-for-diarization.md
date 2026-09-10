---
id: TASK-020
title: 'macOS: Apple Silicon acceleration - MLX for transcription, MPS for diarization'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-06 22:36'
updated_date: '2026-09-10 17:03'
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

CHECKED 2026-09-10, because this branch touched the code AC4 covers.

TASK-022 moved check_accelerators out of the checks the WEB process runs (it imports torch, which ADR-001 forbids there) into a WEB_SAFE_CHECKS split, reached through doctor.web_checks(). accel.describe() and check_accelerators themselves were not changed, and the CLI still runs the full CPU set - so the doctor's accel line is intact. Verified here: 'python -m scribe.doctor --no-gpu' prints '[OK  ] accel       transcription on cuda, diarization on cuda', and tests/test_stage_transcribe_mlx.py + tests/test_doctor.py pass 23. AC1-AC5 still hold after a day of changes.

ONE THING THE ACCEPTANCE RUN SHOULD NOW SAY. The comment's instructions clone the repo's default branch, which is right for this task - the mlx/mps work is on main (82f3033) and main is the simpler thing to measure. But feat/feed-episode-import is 27 commits ahead of main today, and one of them changes where the accel check runs. So the evidence pasted here should name the revision it came from ('git rev-parse --short HEAD' beside the doctor output). Without that, a doctor line from main and one from the branch are two different code paths wearing the same words.

AC6 IS NOT DOABLE FROM HERE, and this is a statement about the criterion rather than about effort. It asks for doctor output and a transcribed file with timings from a real Apple Silicon machine. There is no Mac on this machine and no way to produce that evidence; the README says as much about the whole macOS path, and calls the first doctor run on one the acceptance test. Checking it would record a measurement nobody took.

The task therefore stays In Progress deliberately. That is the honest terminal state for work whose only open criterion is a measurement on hardware that is not here - not Done, and not a checked box resting on an argument.

scripts/mac-acceptance.sh added 2026-09-10: AC6's run as one command instead of a paragraph in a comment.

It prints the revision and branch it ran against (a doctor line from main and one from a feature branch can be two code paths wearing the same words), says plainly when it is not on macOS, reads accel.transcription_backend() and diarization_device() before the doctor so a wrong answer is visible without reading the doctor's prose, runs the doctor with the model load, and - given an audio file - ingests it and queues a diarized transcribe job, naming the jobs page whose events go into this task.

It installs nothing and changes no setting. A missing venv or a red doctor line stops it with the install steps rather than a guess, because that line IS the bug report.

Exercised here on Windows: bash -n clean, and the run correctly reported 'this is not macOS, so the numbers below are not the acceptance' and stopped at the missing Unix venv. Its happy path is untested for the same reason AC6 is open - there is no Mac here.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-06 22:45
---
Acceptance on the MacBook (Apple Silicon): git clone https://github.com/rvdbreemen/MyScribe && cd MyScribe && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-ml.txt -r requirements-macos.txt && brew install ffmpeg && .venv/bin/python -m scribe.doctor. Expected: 'accel transcription on mlx, diarization on mps' and a gpu-smoke line 'large-v3-turbo on mlx/float16: N words from 30s in X s'. Then .venv/bin/python -m scribe, transcribe a file with speakers on, and paste the job's events (transcribe: device, diarize: pipeline) plus the doctor output here. If anything is red, the doctor line is the bug report.
---
<!-- COMMENTS:END -->
