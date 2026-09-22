---
id: TASK-092
title: >-
  MyScribe never falls back to the CPU behind a broken driver without being told
  to
status: To Do
assignee: []
created_date: '2026-09-22 10:32'
updated_date: '2026-09-22 10:33'
labels:
  - gpu
  - ux
  - bug
dependencies:
  - TASK-089.12
priority: high
ordinal: 165000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A machine with an NVIDIA card whose driver has stopped working transcribes on the CPU today, about thirty times slower, and says so nowhere. `accel.transcription_backend()` (scribe/accel.py:73-81) answers `cuda` when `cuda_available()` is true and `cpu` otherwise, and `cuda_available()` is `torch.cuda.is_available()` (:53-58) - which is equally false for a laptop that never had a card and for a 3080 behind a driver that broke this morning. `diarization_device()` (:84-90) does the same. `transcribe.load_model` (scribe/stages/transcribe.py:569-570) takes whatever it is given and builds a CPU model without a word.

Robert decided on 2026-09-22, asked with the three options and their consequences: MyScribe never switches to the CPU by itself when this machine has NVIDIA hardware. The job is refused with a sentence that names the driver, and a switch in Settings - off by default - is the one way to say yes once. That is the second option's middle ground: somebody on a train with a broken driver can still work, but only after saying so.

The distinction this rests on already exists: `doctor.nvidia_hardware_present()` (TASK-089.12) answers whether there is an NVIDIA card at all, driver or no driver, from `nvidia-smi` being installed and the PCI vendor id the firmware enumerated, never from `CUDA_VISIBLE_DEVICES` - which simulates a missing card and a broken driver identically - and every doubt answers True. A machine that genuinely has no NVIDIA hardware keeps running on the CPU with no question asked: that is a supported mode (README.md:46, :52-53) and this task must not make it louder.

Where the function should live is open: it sits in `scribe/doctor.py` and the runtime would import the doctor for it. Moving it to `scribe/accel.py` is the obvious answer and is this task's call, with the doctor then reading it from there.

Not decided here and deliberately left: whether the same rule should apply to `mps` on Apple Silicon. Nobody has a Mac (brief: G9).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red first: on a machine where nvidia hardware is present and torch.cuda.is_available() is False, a transcribe job is refused rather than run on the CPU. The refusal names the driver and nvidia-smi, says the switch exists and where, and never starts a model. Today the job runs to completion on the CPU with nothing said; that output is shown.
- [ ] #2 A machine with no NVIDIA hardware is unchanged: the job runs on the CPU, nothing is refused and nothing extra is said. A test covers both directions off the same seam, so a change that refuses everything cannot pass.
- [ ] #3 Settings carries one switch, "Transcribe on the CPU when the GPU is unavailable", off by default and written as its own row. With it on, the job runs on the CPU and every run records which backend it used, so a transcript made on the CPU can be told apart afterwards. With it off or absent, the job is refused. A test per state, and a test that a missing row means off (no row, no fallback - the shape ADR-016 uses for the provider).
- [ ] #4 The same rule covers the speaker pass: diarization_device() does not answer cpu behind present-but-unreachable hardware unless the switch is on. One test, and the notes say whether a job that transcribed on the GPU may still diarize on the CPU or whether the two move together.
- [ ] #5 nvidia_hardware_present() moves to scribe/accel.py and the doctor reads it from there, so the runtime does not import scribe.doctor. TASK-089.12's tests move with it rather than being duplicated, and the diff is shown. If the move turns out to cost more than it saves, the notes say why it was left where it is.
- [ ] #6 CUDA_VISIBLE_DEVICES stays what it is: somebody who sets it is choosing, so a job is not refused for it. A test pins that setting it to -1 on a machine with hardware runs on the CPU without the switch, and the notes say why this is not a hole - it cannot be set by accident, and TASK-089.12 made the doctor say out loud when it is set.
- [ ] #7 The refusal is not a crash: the job ends in the failed state with its sentence in error_detail, the board shows it, and nothing is half-written. Exit codes follow the runner's existing meanings and a test pins which one this is.
- [ ] #8 Needs a real machine: nobody has run this behind an actually broken driver. The code and the seam are built and tested with the hardware probe and torch.cuda.is_available() replaced; the notes say plainly that no real broken-driver run has happened, and name what would settle it - Robert disabling the display adapter in Device Manager on his own machine, which is reversible.
<!-- AC:END -->
