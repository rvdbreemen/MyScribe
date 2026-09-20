---
id: TASK-089.12
title: >-
  The doctor judges a CPU machine fairly, gives hints that exist, and can be
  read by a program
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - ops
  - bug
dependencies:
  - TASK-089.04
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 149000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
gpu-runtime is a required FAIL on any Windows or Linux machine without NVIDIA, although CPU transcription is a supported mode (README.md:46, :52-53). Both platforms get a CUDA-enabled torch from the lock, so the check lands in the 'cannot reach a device' branch (scribe/doctor.py:341-347) and tells the user to 'Check the NVIDIA driver with nvidia-smi' - for a driver and a tool that machine does not have - while the app would in fact transcribe. A reader simulated it on 2026-09-20 with CUDA_VISIBLE_DEVICES=-1: gpu-runtime FAIL, accel 'transcription on cpu', '1 required check(s) failed'. It was not run on real hardware without a GPU.

ADR-012 governs this check and is Accepted, so only one case may soften. Its Must: '`doctor.check_gpu_runtime` fails when `torch.version.cuda is None` on a machine that should have CUDA'; its Verification: `python -m scribe.doctor` exit 0 on both GPU platforms (docs/adr/ADR-012-...md:139-140, :156-157). check_gpu_runtime has two failing branches on Windows and Linux. A CPU-only torch build (scribe/doctor.py:335-340) means the lock was not honoured, and stays a required FAIL. A CUDA build that cannot reach a device (:341-347) is today the same FAIL for a laptop with no NVIDIA hardware and for Robert's card behind a broken driver. CUDA_VISIBLE_DEVICES=-1 simulates both identically, so the variable alone cannot be what turns the FAIL into information: a broken driver on the reference machine would then read 'transcription on cpu' with exit 0.

The hints point at things that are not there. The CPU-build hint says `pip install torch` (:339) in a venv that has no pip. The ffmpeg hint says `winget install Gyan.FFmpeg` on every OS (:173).

The summary misleads. A user who skipped the token reads 'All required checks passed (2 optional check(s) not wired yet)' (:800) and concludes the developer has unfinished work, not that speaker separation will not run on their machine.

And no program can read it. There is one flag, `--no-gpu` (:816-822), a rendered table and an exit code. The results are printed once, after every check has finished (:824-825), so a cold gpu-smoke that downloads weights is a blank terminal that reads as a hang.

Needs a real machine: The no-hardware case is tested with the hardware probe replaced. A real machine without NVIDIA hardware has been tried by nobody: a GitHub runner from a probe branch, or a laptop Robert names. Robert's machine for the two cases that must stay as they are.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red first: on a CUDA build with no reachable device and no NVIDIA hardware found, `python -m scribe.doctor` exits 0. gpu-runtime reads as information ('no NVIDIA GPU on this machine; transcription on cpu') and no nvidia-smi advice appears. Today it exits 1. Whether NVIDIA hardware is present is asked of a function the test replaces. Which signals it reads - nvidia-smi on PATH or in its known locations, the OS's list of display adapters - is decided at implementation, and none has been tried. Any doubt counts as hardware present.
- [ ] #2 No fix hint names pip; the CPU-build hint says `uv sync`.
- [ ] #3 The ffmpeg hint is per OS: winget, brew or apt.
- [ ] #4 The closing line says what each skipped check means, for example 'speaker separation not set up' or 'weights not downloaded'. The words 'not wired yet' are gone.
- [ ] #5 `--json` prints one object per check with name, ok, optional, detail and fix_hint. Exit codes are unchanged, and a test pins the shape.
- [ ] #6 On a TTY each check's name is printed as it starts, so a cold gpu-smoke is not a blank terminal.
- [ ] #7 NVIDIA hardware present and no device reachable stays a required FAIL with the driver hint, and when CUDA_VISIBLE_DEVICES is set the detail names it. One test. On Robert's machine `CUDA_VISIBLE_DEVICES=-1 python -m scribe.doctor` therefore still exits 1, and that output is shown: it is the broken-driver case behaving as ADR-012 wants.
- [ ] #8 `torch.version.cuda is None` on Windows or Linux stays a required FAIL (ADR-012's Must); only its hint changes, to `uv sync`. One test. `python -m scribe.doctor` on Robert's card still exits 0, which is ADR-012's Verification, and the output is shown.
- [ ] #9 Needs a machine without NVIDIA hardware. GitHub's runners have none ('no runner has a card', .github/workflows/ci.yml:82), but the CI Doctor step skips gpu-runtime with `--no-gpu`, so they have not answered it either. One run of check_gpu_runtime alone on a windows or ubuntu runner, from a probe branch - billed minutes - or on a laptop Robert names. If neither was done, the notes say so and this box stays unticked.
<!-- AC:END -->
