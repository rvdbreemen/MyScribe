---
id: TASK-093
title: The doctor segfaults about two runs in three when CUDA finds no device
status: To Do
assignee: []
created_date: '2026-09-22 11:10'
updated_date: '2026-09-22 11:11'
labels:
  - gpu
  - bug
  - ops
dependencies: []
priority: high
ordinal: 166000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`python -m scribe.doctor` dies with a Windows access violation about two runs in three when torch's CUDA stack initialises and finds no device. Measured by the orchestrator on 2026-09-22, fenced, on Robert's machine with the card hidden:

    SCRIBE_DATA_DIR=C:\ms-f\doc CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python -m scribe.doctor
    run 1  exit=139   (0 bytes of output)
    run 2  exit=1     (the full table, gpu-smoke OK: large-v3-turbo on cpu/int8,
                       75 words from 30s in 62.3s)
    run 3  exit=139   (0 bytes of output)

So the work itself succeeds: the run that survived transcribed the clip on the CPU and reported it. The crash is intermittent, and a run that crashes prints nothing at all - the doctor collects every result and renders once at the end, so a person sees an empty terminal and an exit code.

With `faulthandler` the frame is the model's constructor, not the transcription:

    Windows fatal exception: access violation
      faster_whisper/transcribe.py:689 in __init__
      scribe/stages/transcribe.py:583 in load_model

Pre-existing, not introduced by TASK-089.12: that task's diff touches `scribe/doctor.py` only, and within it only the check logic, the hints, the rendering, `--json` and the progress lines - `gpu_smoke`'s body, `load_model` and `cuda_setup` are untouched, and `scribe/stages/transcribe.py` is not in the diff. TASK-089.12's own build measured exit 139 at HEAD as well.

What was ruled out, each by a run of its own:

* Not CPU mode as such. With the card visible, `load_model('large-v3-turbo', device='cpu')` builds fine (`cpu int8`, exit 0).
* Not the model's size alone. With the card hidden, `tiny` builds and transcribes fine (2 segments, exit 0).
* Not `torch.cuda.is_available()` alone. With the card visible, calling it first and then building on the CPU is fine.
* Not the hidden card alone. With the card hidden and `device='cpu'` passed so the accelerator probe never runs, the large model builds fine.

A reduced reproduction of the combination did not crash in two attempts, which is what makes it intermittent rather than a clean recipe. The full command is the only reliable trigger anyone has.

Why it matters beyond a developer's terminal: a machine with no NVIDIA card at all has never been tried by anybody (TASK-089.12 criterion 9). If the same crash happens there, `python -m scribe.doctor` cannot exit 0 on a supported CPU machine, and TASK-089.12 criterion 1's whole-command half stays unreachable. If it does not, this is only about the hidden-card case. Nobody knows which, and this task is where that gets settled.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The crash is characterised before it is fixed: how often it happens over at least twenty runs of the full command, whether it needs the 1.6 GB model, and where it lands. The reduced reproduction that the orchestrator could not make crash is either made to crash or reported as not reducible, with the runs shown.
- [ ] #2 A crashing run is no longer a blank terminal. Whatever the cause turns out to be, a person who runs the doctor and loses the process sees which check it died in - TASK-089.12 added per-check progress on a TTY, so this may already be covered; if it is, say so with the output of a crashing run rather than by reading the code.
- [ ] #3 The question TASK-089.12 criterion 9 could not answer is settled: on a machine with no NVIDIA card at all, does `python -m scribe.doctor` complete? Either a real card-less machine or a GitHub runner from a probe branch. If it crashes there too, CPU transcription is broken on a supported platform and that is the headline; if it does not, this is only about a card hidden on a machine that has one, and the task says so.
- [ ] #4 Root cause named from evidence, not guessed: the faulthandler frame is faster_whisper/transcribe.py:689 in WhisperModel.__init__ through scribe/stages/transcribe.py:583. Whether it is CTranslate2, cuDNN loaded by cuda_setup.ensure_cuda_libs(), or the CUDA runtime left in a failed state by a probe is established with at least two independent observations before anything is changed.
- [ ] #5 If the fix is to keep the CUDA libraries out of the way when no device can be reached, ADR-012 is read first: cuda_setup.ensure_cuda_libs() exists because CTranslate2 delay-loads cuDNN and a late fix arrives after the DLL search has failed. A change there must not break the case that record exists for, and the doctor's own gpu-smoke on Robert's card must still pass - shown, not assumed.
<!-- AC:END -->
