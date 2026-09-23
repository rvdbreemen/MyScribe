---
id: TASK-093
title: The doctor segfaults about two runs in three when CUDA finds no device
status: In Progress
assignee:
  - '@gpu-lane'
created_date: '2026-09-22 11:10'
updated_date: '2026-09-23 22:01'
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
- [x] #2 A crashing run is no longer a blank terminal. Whatever the cause turns out to be, a person who runs the doctor and loses the process sees which check it died in - TASK-089.12 added per-check progress on a TTY, so this may already be covered; if it is, say so with the output of a crashing run rather than by reading the code.
- [x] #3 The question TASK-089.12 criterion 9 could not answer is settled: on a machine with no NVIDIA card at all, does `python -m scribe.doctor` complete? Either a real card-less machine or a GitHub runner from a probe branch. If it crashes there too, CPU transcription is broken on a supported platform and that is the headline; if it does not, this is only about a card hidden on a machine that has one, and the task says so.
- [ ] #4 Root cause named from evidence, not guessed: the faulthandler frame is faster_whisper/transcribe.py:689 in WhisperModel.__init__ through scribe/stages/transcribe.py:583. Whether it is CTranslate2, cuDNN loaded by cuda_setup.ensure_cuda_libs(), or the CUDA runtime left in a failed state by a probe is established with at least two independent observations before anything is changed.
- [ ] #5 If the fix is to keep the CUDA libraries out of the way when no device can be reached, ADR-012 is read first: cuda_setup.ensure_cuda_libs() exists because CTranslate2 delay-loads cuDNN and a late fix arrives after the DLL search has failed. A change there must not break the case that record exists for, and the doctor's own gpu-smoke on Robert's card must still pass - shown, not assumed.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. #2 first, red first: a child process whose one check dies (faulthandler._sigsegv, and a real write to address 0, which is Windows' access violation) is run redirected and on a forced terminal. Today: 0 bytes when redirected, only the progress line on a terminal.
2. Make the doctor say where it died: faulthandler enabled in doctor.main, writing to stderr, unless something already enabled it (pytest, -X faulthandler) or there is no stderr with a file descriptor.
3. #1 and #4: a script, scripts/task093_crash_census.py, that Robert runs: twenty full doctor runs with CUDA_VISIBLE_DEVICES=-1, a fresh SCRIBE_DATA_DIR per run, an empty SCRIBE_ENV_FILE and PYTHONFAULTHANDLER=1, one run at a time, one JSON line per run (exit code, bytes, last line, fault frames), plus the reduced reproductions the task lists. Tested against stand-in children only; the doctor runs are not made here.
4. #3 and #5 wait: #3 for a card-less machine or a CI runner, #5 for a root cause from #1 and #4.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Build notes (gpu-lane, 2026-09-23) - not committed, criteria not ticked

EVIDENCE = scratchpad/build/gpu/093 (session d0ea7837). Worktree: MyScribe-wt-gpu. No doctor run was made here: no model was loaded and the twenty runs are Robert's.

### #2: a crashing run is no longer a blank terminal
- Found by running, not by reading. TASK-089.12's per-check progress prints "checking <label>" to stderr, flushed, but only when stderr is a terminal and not under --json. So a crash on a terminal already left the name of the check, and a redirected run - the orchestrator's three runs - left 0 bytes.
- Red, at HEAD (a copy of HEAD's scribe/ and tests/ with the new test file; EVIDENCE/red-test_doctor_crash-at-HEAD.txt): a child whose one check writes to address 0 exits 3221225477 (0xC0000005, Windows' access violation; Git Bash shows 139, the orchestrator's number) with 0 bytes on stderr. The faulthandler._sigsegv variant exits 3 with 0 bytes. On a forced terminal the only line is "checking check_that_dies".
- Change: scribe/doctor.py _report_a_crash(), called first in main(). It enables faulthandler on stderr, so a fatal signal or an access violation prints the Python frames, the check's function among them. It leaves a faulthandler that is already on alone (pytest, -X faulthandler) and skips a stderr without a file descriptor (pythonw, a captured stream). It costs nothing on a run that does not crash and changes no output of one that does not.
- Green: tests/test_doctor_crash.py, 4 passed (EVIDENCE/green-test_doctor_crash.txt): test_a_crash_in_a_redirected_run_still_names_the_check_it_died_in for sigsegv and access-violation, test_a_crash_on_a_terminal_shows_the_progress_line_and_the_frame, test_the_doctor_leaves_a_faulthandler_somebody_else_enabled_alone. The children run with PYTHONFAULTHANDLER removed and no -X faulthandler, so the frames come from the doctor.
- The output of the crashing runs, after the change: EVIDENCE/crash-run-output-access-violation.txt. Redirected: exit 139, 0 bytes of stdout, 380 bytes of stderr starting "Windows fatal exception: access violation" and naming check_that_dies, then doctor.py in checks and main. On a terminal: the same, after the line "checking check_that_dies".
- One trap found on the way: ctypes.string_at(0) is not a crash on Windows. ctypes wraps foreign calls and turns the access violation into an OSError, a Python traceback that names the check with or without the doctor's help. The test uses ctypes.c_int.from_address(0).value = 1, measured to exit 139 with "Windows fatal exception: access violation".
- Mutant (copy, scratchpad/build/gpu/mut-no-crash-report.txt): _report_a_crash made a no-op, 3 of 4 fail, exactly the three crash tests.
- A run that does not crash is unchanged, measured: python -m scribe.doctor --no-gpu, fenced, card visible, from a copy of HEAD and from the worktree. Both exit 0 with 0 bytes on stderr, and their stdout differs only in the three lines that print the fence path (scratchpad/build/gpu/093/nogpu-head/ and nogpu-wt/). That path imports torch, asks CUDA and imports the diarize stage, the places where faulthandler could have printed a block for a first-chance exception. The GPU pair was not run here: no model may be loaded in this lane.
- Still not shown: the real crash with this change. What the doctor prints when the model constructor itself dies is what Robert's census below records.

### #1 and #4: the census, for Robert to run
scripts/task093_crash_census.py. From the repository root, in Robert's own terminal, with nothing heavy running:

    .venv/Scripts/python scripts/task093_crash_census.py --out task093-census.jsonl

- Twenty full runs of python -m scribe.doctor, then each reduced variant ten times, one process at a time. Every run gets CUDA_VISIBLE_DEVICES=-1 (except the two "visible" variants), a fresh SCRIBE_DATA_DIR per run under a temp directory, an empty SCRIBE_ENV_FILE and PYTHONFAULTHANDLER=1. HF_HOME is left alone so the 1.6 GB model is not downloaded again per run.
- The variants: full; smoke-probe-turbo (check_gpu_runtime then gpu_smoke in one process); load-probe-turbo (load_model with no device, so the probe runs, then a transcription); load-probe-tiny (the same with tiny: does it need the big model?); load-cpu-turbo-hidden (device="cpu", the probe never runs); load-cpu-turbo-visible; probe-then-cpu-visible (is_available first, then cpu); diarize-import-then-probe-turbo (scribe.stages.diarize imported first, as the doctor's diarization check does before the GPU checks - the one import the full command makes that the reduced ones did not; my addition, not in the task's list).
- Time: about 20-25 minutes for the full runs (62 s for the smoke alone on 2026-09-22) and roughly as long again for the reduced ones. --only full or --only reduced, --runs and --reduced-runs shorten it. Ctrl+C keeps every line already written.
- What to send back: the task093-census.jsonl file and the summary printed at the end (per variant: crashed out of runs, exit codes, and the innermost frame of each crash).
- A run counts as a crash by its exit code alone: not 0, not 1 (the doctor's two verdicts) and not a timeout. The fault text is a column beside it and never decides, because faulthandler can print a block for an exception that native code then handles, and the full doctor with the card hidden normally exits 1.
- Tested against stand-in children only (tests/test_task093_census.py, 7 passed, EVIDENCE/green-test_task093_census.txt): a fault block with exit 1 is not a crash; an access violation is recorded with its frame and counted as a crash; exit 1 and exit 0 are not crashes; a hang is recorded as a timeout; the fence hides the card, moves the library and leaves the model cache; the plan is twenty full runs plus every variant; every variant script compiles. Mutants: fence without PYTHONFAULTHANDLER 2 failed; fence inheriting a hidden card for a visible variant 1 failed; crash counted by fault text instead of exit code 1 failed (scratchpad/build/gpu/mut-census-crash-by-fault-text.txt).
- Note for reading the results: with TASK-092 in the same tree, a hidden card (CUDA_VISIBLE_DEVICES set) is still allowed on the CPU, so the full and probe variants take the same path as on 2026-09-22.

### Waiting, and on whom
- #1: Robert's census run above.
- #3: a machine with no NVIDIA card at all, or a GitHub runner from a probe branch. Neither is reachable from here.
- #4: needs the census's frames and the reduced variants' crash rates; two independent observations before anything changes.
- #5: comes after #4. ADR-012 first, and gpu-smoke on Robert's card must still pass, shown.

### #3: evidence from CI, not settled
The orchestrator's GitHub CI run 35921408104 (branch task-089-installer-first-slice, 2026-09-23), logs at the orchestrator's scratchpad/ev/ci3-*.log, checked by me against the log text. install.py's proof on runners with no NVIDIA card finished with exit 0, in process, through the same load_model path the doctor's smoke uses:
- windows-latest, torch 2.10.0+cu128 (CUDA 12.8): gpu-runtime "no NVIDIA GPU on this machine; transcription on cpu"; "large-v3-turbo on cpu/int8: 78 words from 30s in 49.3s (load 16.8s)"; "(exit code 0)".
- ubuntu-latest, torch 2.10.0+cu128: "large-v3-turbo on cpu/int8: 76 words from 30s in 57.7s (load 16.1s)", exit 0.
- macos-latest: "large-v3-turbo on mlx/float16: 77 words from 30s in 34.4s", exit 0.
Two of those three runs took the CPU path; macOS ran on MLX and says nothing about this crash. The runs show that a card-less machine CAN complete. They do not rule out an intermittent crash there. They ran install.py's proof in process, not the python -m scribe.doctor command, and on Robert's machine with the card hidden the in-process reduced reproduction did not crash in two attempts either, while the full command crashed two runs in three. So a clean in-process run is what one would expect even where the full command crashes. Settling #3 wants repeated runs of the full doctor command on a card-less runner, for example the census script with --only full on a probe branch.

Verified 2026-09-24 by the orchestrator in MyScribe-wt-gpu, fenced, one file per process: test_cpu_fallback 19, test_accel 8, test_doctor 56 (8 moved to test_accel), test_task093_census 7, test_setup_prove 43, test_stage_transcribe_mlx 12, test_stage_transcribe 69, test_stage_diarize 76, test_runner 13, test_web_settings 54, all passed; grep MUTANT finds nothing. #3 is answered as far as one run per platform can answer it: GitHub CI run 35921408104 (2026-09-23), runners with no NVIDIA card, the install.py proof loaded large-v3-turbo through the same load_model path and completed with exit 0 (Windows cu128 torch on cpu/int8, 78 words in 49.3 s; Ubuntu cu128 on cpu/int8, 76 words in 57.7 s; macOS on MLX). A card-less machine can complete; one run cannot rule out an intermittent crash like the hidden card's two in three. #1, #4 and #5 wait on Robert's census run: .venv/Scripts/python scripts/task093_crash_census.py --out task093-census.jsonl (40-50 minutes), then send back the .jsonl and its summary.
<!-- SECTION:NOTES:END -->
