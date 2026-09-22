---
id: TASK-089.12
title: >-
  The doctor judges a CPU machine fairly, gives hints that exist, and can be
  read by a program
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 11:28'
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
- [x] #2 No fix hint names pip; the CPU-build hint says `uv sync`.
- [x] #3 The ffmpeg hint is per OS: winget, brew or apt.
- [x] #4 The closing line says what each skipped check means, for example 'speaker separation not set up' or 'weights not downloaded'. The words 'not wired yet' are gone.
- [x] #5 `--json` prints one object per check with name, ok, optional, detail and fix_hint. Exit codes are unchanged, and a test pins the shape.
- [x] #6 On a TTY each check's name is printed as it starts, so a cold gpu-smoke is not a blank terminal.
- [x] #7 NVIDIA hardware present and no device reachable stays a required FAIL with the driver hint, and when CUDA_VISIBLE_DEVICES is set the detail names it. One test. On Robert's machine `CUDA_VISIBLE_DEVICES=-1 python -m scribe.doctor` therefore still exits 1, and that output is shown: it is the broken-driver case behaving as ADR-012 wants.
- [x] #8 `torch.version.cuda is None` on Windows or Linux stays a required FAIL (ADR-012's Must); only its hint changes, to `uv sync`. One test. `python -m scribe.doctor` on Robert's card still exits 0, which is ADR-012's Verification, and the output is shown.
- [ ] #9 Needs a machine without NVIDIA hardware. GitHub's runners have none ('no runner has a card', .github/workflows/ci.yml:82), but the CI Doctor step skips gpu-runtime with `--no-gpu`, so they have not answered it either. One run of check_gpu_runtime alone on a windows or ubuntu runner, from a probe branch - billed minutes - or on a laptop Robert names. If neither was done, the notes say so and this box stays unticked.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. The seam: doctor.nvidia_hardware_present() -> bool, the one function a test replaces. It reads PCI vendor id 10DE, which is the ground truth and needs no driver: on Windows through winreg under HKLM SYSTEM\CurrentControlSet\Enum\PCI (measured here: 0.3 ms, 2 vendor keys on the 3080; wmic is gone in Windows 11 24H2 and a CIM call costs a second and a process), on Linux through /sys/bus/pci/devices/*/vendor, and on every OS shutil.which("nvidia-smi") plus its known install paths. Doubt is hardware present: an OpenKey or EnumKey that raises, a count that cannot be read, a missing or empty sysfs list, or an OS this function does not know, all return True. The registry loop enumerates exactly QueryInfoKey's count, so "access denied" is never read as "finished, none found".
2. Split the cannot-reach-a-device branch (:341-347). Hardware present, or CUDA_VISIBLE_DEVICES present in os.environ (membership, not truthiness: an empty value hides a card as well as -1), stays a required FAIL with the driver hint, and the detail quotes the variable when it is set. Only hardware absent AND the variable unset returns ok=True, detail "no NVIDIA GPU on this machine; transcription on cpu", no fix hint - the shape of the Apple Silicon branch. optional stays False, so the registered-and-required test still holds.
3. torch.version.cuda is None on Windows or Linux stays a required FAIL (ADR-012 Must). Only its hint changes, to the existing _PIN_HINT constant (one spelling of uv sync, not a second). The Apple branch's fix_hint=None becomes "" so --json never emits null.
4. The ffmpeg hint becomes per OS, taking the three commands from README.md:45: winget on Windows, brew on macOS, apt elsewhere.
5. The closing line: a SKIP_MEANINGS map from optional check name (yt-dlp, diarization, models, ollama) to a consequence in words, listed in the summary - "All required checks passed (skipped: speaker separation not set up; weights not downloaded)". The words "not wired yet" (:875) are gone. A name with no entry falls back to the name rather than raising.
6. --json prints a JSON array, one object per check, from asdict(Check): name, ok, optional, detail, fix_hint. Nothing else goes to stdout. The exit code is untouched - 1 when a required check failed, else 0 - and one test pins both shapes against both codes.
7. Progress: checks() gains on_start=None and calls it with a check's label before running it; CHECK_LABELS maps each registered function to the name its Check returns. main prints "checking <label>" to stderr, only when stderr is a TTY and not under --json, one plain line per check (no carriage returns or ANSI, so legacy conhost is fine). Stdout stays exactly what the 23 existing tests pin. settings.py:604 calls checks(include_gpu=False) and is unaffected.
8. Tests in tests/test_doctor.py, all with a fake torch injected through sys.modules and ensure_cuda_libs stubbed - no card touched, no model loaded: criterion 1 (no hardware, no device: ok=True, information wording, no nvidia-smi advice, main exits 0 with the smoke stubbed); criterion 7 (hardware present stays FAIL with the driver hint; with CUDA_VISIBLE_DEVICES set the detail names it); criterion 8 (cuda None stays FAIL, hint says uv sync); both "learned nothing" paths of the probe return True; CHECK_LABELS covers every registered check and agrees with the name each CPU check returns; the --json shape and both exit codes; the closing line for a SKIP; the ffmpeg hint per OS; and a source-text assertion that no fix hint in doctor.py names pip, in the style of test_smoke_timings_never_land_under_the_transcribe_stage. One existing test changes: the checks stub at :112 takes **kwargs.
9. Method: red first. The criterion 1 test runs against today's code with the probe stubbed absent, and its failing output is kept before anything is implemented. Every pytest process runs one test file with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE exported (TASK-090 fence): test_doctor.py, test_web_settings.py, test_cuda_setup.py and the runner tests. The bite check happens on a copy under the scratch build directory, one mutation per run, never in the repository.
10. Two real runs on this machine, both fenced - main() migrates the library through db.connect, which the live-library rule forbids, and the weights cache is not under DATA_DIR (hf_cache_dir reads HF_HUB_CACHE, HF_HOME or ~/.cache, and the default cache is present), so the smoke still finds its weights: python -m scribe.doctor exits 0 with the 3080 green (ADR-012 Verification, criterion 8) and CUDA_VISIBLE_DEVICES=-1 python -m scribe.doctor exits 1 with the variable named (criterion 7). Both outputs are kept.
11. Criterion 9 cannot be closed from here: no machine without NVIDIA hardware is reachable in this session. The notes will say that plainly and name what would settle it - one run of check_gpu_runtime on a windows or ubuntu GitHub runner from a probe branch (billed minutes), or a laptop Robert names. Criterion 1 is proven at check level plus a main() test with the smoke stubbed; the cold whole-command run on real no-NVIDIA hardware also depends on the weights being present, and that is criterion 9's job, not this one's.

12. Corrections to step 8 after a second read of the file: criterion 2 is asserted over rendered fix_hint values, not source text - collect the hints from the CPU checks and from all three gpu-runtime branches under fake torch and assert no match for the word pip (word boundary; a plain substring hits "pipeline" at :534 and the docstring at :310, which is explanatory prose and stays). CHECK_LABELS is pinned for both GPU checks without a card: gpu-runtime through fake torch, gpu-smoke through gpu_smoke(clip=<missing path>), which returns at :366 before the transcribe stage is imported.
13. Decided rather than left open: the skip explanations appear in BOTH closing branches, not only the passing one (today the suffix is only on the pass at :872-876). A machine that fails ffmpeg must still be told why diarization was skipped. The failing-plus-skipped case gets its own test.
14. Before touching code the implementer runs /adr-kit:context (CLAUDE.md), and /adr-kit:judge is the orchestrator's gate at commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
IMPLEMENTATION 2026-09-22. Changed: scribe/doctor.py, tests/test_doctor.py. Nothing else. No dependency added, no commit.

THE SEAM (criterion 1). New doctor.nvidia_hardware_present() -> bool, plus _nvidia_in_windows_pci() and _nvidia_in_sysfs(). Signals, in order: shutil.which("nvidia-smi") or one of _NVIDIA_SMI_LOCATIONS existing; then PCI vendor id 10de - winreg under HKLM SYSTEM\CurrentControlSet\Enum\PCI on Windows, /sys/bus/pci/devices/*/vendor on Linux. nvidia-smi is only ever looked for, never run: on a broken driver it exits non-zero while the card is still in the machine, and reading that as "no hardware" would soften the one case ADR-012 wants red. Every doubt returns True and it is in the code, not in a comment: OpenKey or EnumKey raising, winreg not importable, a missing or empty sysfs list, a vendor file that will not open, an OS this function does not know. The registry loop enumerates exactly QueryInfoKey's count, so access-denied raises out of it instead of ending it.

check_gpu_runtime's no-device branch now returns ok=True, optional=False, detail "no NVIDIA GPU on this machine; transcription on cpu" and no hint ONLY when the probe says absent AND "CUDA_VISIBLE_DEVICES" is not in os.environ (membership, not truthiness). Otherwise it stays a required FAIL with the nvidia-smi hint, and the detail quotes the variable when it is set. The CPU-only-build branch (ADR-012's Must) is untouched except its hint, which is now _PIN_HINT (uv sync). The Apple branch's fix_hint=None became "" so --json emits no null.

Also: _ffmpeg_hint() per OS from README.md:45 (winget / brew / apt); SKIP_MEANINGS listed in BOTH closing branches so a machine that fails ffmpeg still learns why diarization was skipped, and "not wired yet" is gone from the module; --json prints json.dumps of asdict(Check) and nothing else on stdout, exit codes untouched; checks() gained on_start and CHECK_LABELS, and main prints "checking <label>" to stderr only when _stderr_is_a_terminal() and not under --json. One existing stub changed to match main's new call: tests/test_doctor.py's checks stub now takes on_start=None.

EVIDENCE. All in C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/96fe3055-12a8-4348-a17a-5699a082adf4/scratchpad/build/TASK-089.12/. Every pytest process ran one test file with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE exported (TASK-090 fence).

RED FIRST, red-doctor.txt: 24 failed, 27 passed. The criterion 1 test failed on behaviour and not on a missing name - the probe was stubbed with raising=False, today's code ignored it, and the assertion that failed is "assert check.ok is True" with the old Check printed beside it: ok=False, detail "cannot reach a device", hint "Check the NVIDIA driver with nvidia-smi".

GREEN: green-test_doctor.txt 51 passed. Every other file that imports the module or its callers, one process each: green-test_web_settings.txt 54 passed (settings.py:604 calls checks(include_gpu=False)), green-test_runner.txt 13 passed, green-test_cuda_setup.txt 6 passed (ADR-012's own), green-test_disk_floor.txt 9 passed, green-test_ingest_urls.txt 112 passed, green-test_models.txt 15 passed, green-test_llm_selftest.txt 14 passed.

MUTATION, mutant-doctor.txt: on a copy under scratch (mut/), the soften branch made unconditional - "if True:  # MUTANT" in place of the probe-and-variable test, which is exactly the failure mode this task names (making the no-GPU case pass by making every case pass). Result: 3 failed, 48 passed, and the three are precisely the criterion 7 tests - the broken driver and both CUDA_VISIBLE_DEVICES values. Not too coarse, not too fine. grep -rn MUTANT over scribe, tests and packaging in the repository finds nothing.

REAL RUNS ON THIS MACHINE, fenced, one at a time, nothing else running.
1. real-3080-green.txt - python -m scribe.doctor, exit 0. gpu-runtime OK on the RTX 3080 Laptop GPU (16.0 GB), gpu-smoke large-v3-turbo on cuda/float16, 75 words from 30s in 14.9s. That is ADR-012's Verification and criterion 8's whole-command half. Closing line reads "All required checks passed (skipped: weights not downloaded)."
2. Criterion 7's whole command could NOT be run: CUDA_VISIBLE_DEVICES=-1 python -m scribe.doctor SEGFAULTS (exit 139, no output). It does so at HEAD too - real-hidden-card-at-HEAD.txt, a copy of the tree with scribe/doctor.py restored from HEAD - so it is not this change. diag-cpu-smoke-segfault.txt locates it: CTranslate2 4.8.2 reports 0 cuda devices, loads large-v3-turbo on cpu/int8 fine, and crashes inside the first model.transcribe() call. diag-cpu-with-card-visible.txt rules out "CPU transcription is broken here": with the card NOT hidden and device='cpu' asked for directly, the same clip transcribes, 75 words in 64.7s. So the crash is specific to hiding the card from a CUDA-enabled CTranslate2.
   What was shown instead, real machine, real variable: check_gpu_runtime alone returns ok=False, optional=False, detail "torch 2.10.0+cu128 (CUDA 12.8) cannot reach a device; CUDA_VISIBLE_DEVICES='-1' is set here", and the probe answers nvidia_hardware_present() = True on this machine. And real-hidden-card-red.txt: doctor.main([]) with CUDA_VISIBLE_DEVICES=-1, the whole table, exit 1, "1 required check(s) failed (skipped: weights not downloaded)", with ONLY the crashing gpu-smoke replaced by a stub that says so in its own line.
3. real-json.txt - python -m scribe.doctor --no-gpu --json, exit 0, 12 objects parsed, keys detail/fix_hint/name/ok/optional, no nulls, and stderr was 0 bytes (the non-TTY half of criterion 6, on a real run).

WHAT IS OWED, AND NOBODY HAS DONE IT.
* Criterion 9: no machine without NVIDIA hardware has been tried by anybody. Not by me - this machine has a 3080 and its nvidia-smi is on PATH, so the probe answers True here and every no-hardware test replaces the probe. What would settle it: one run of check_gpu_runtime on a windows or ubuntu GitHub runner from a probe branch (billed minutes; CI today skips it with --no-gpu, ci.yml:82), or a laptop Robert names. The known blind spot stays: a card the OS no longer enumerates under Enum\PCI - a dead slot, a removed device - reads as absent. The driver-never-installed case is covered, because PCI enumeration does not need a driver.
* That run should also run the WHOLE command there, not only the check. The segfault above makes it a real question whether gpu-smoke survives on a card-less machine with this cu128 CTranslate2, which is the same 0-cuda-devices situation. Hypothesis, not a finding: nobody has run it.
* Criterion 6's TTY half is proven only with _stderr_is_a_terminal() replaced. The non-TTY half is proven for real (0 bytes on stderr above). winpty here refuses without a tty on stdin, so no pseudo-terminal run was possible from this session; one command in Robert's own terminal settles it.

OUT OF SCOPE, FOUND AND NOT FIXED.
* scribe/models.py's inventory is not platform-aware: models.status() asks about mlx-community/whisper-large-v3-turbo, which is the Apple Silicon repo and legitimately absent on Windows, while the pipeline loads mobiuslabsgmbh/faster-whisper-large-v3-turbo, which IS in the cache. So this machine is told "1.6 GB still to download" in the same run in which gpu-smoke transcribes with those weights. Checked: .env sets no HF_HOME or HF_HUB_CACHE, so the fence did not cause it.
* CUDA_VISIBLE_DEVICES=-1 plus gpu-smoke is a segfault at HEAD (see above). It makes the -1 simulation unusable for the whole command and deserves its own task.
* scribe/doctor.py's module docstring still says the GPU checks are "optional until the CUDA pin set is frozen (plan Task 8)". They have been required for some time - tests/test_doctor.py pins check_gpu_runtime as not optional. Stale prose, left alone.

NOT DONE HERE, DELIBERATELY: no acceptance criteria ticked, no status change, no commit.

ONE RISK TO READ BEFORE TICKING CRITERION 1, moved up out of the out-of-scope list because it decides what criterion 9's run must cover. This change makes gpu-runtime exit 0 on a machine with no NVIDIA card. It does not make the WHOLE command exit 0 there, because gpu-smoke still runs. Measured here today: with CUDA_VISIBLE_DEVICES=-1, CTranslate2 4.8.2 reports 0 cuda devices, loads the model on cpu/int8 and then segfaults inside the first model.transcribe() call (diag-cpu-smoke-segfault.txt); with the card visible and device='cpu' asked for directly the same clip transcribes in 64.7s (diag-cpu-with-card-visible.txt), so CPU transcription itself is fine and the crash is tied to a CUDA-enabled CTranslate2 seeing zero devices. A real card-less Windows or Linux machine installs that same cu128 CTranslate2 and also sees zero devices. Whether it crashes the same way is a HYPOTHESIS - nobody has run it. So criterion 9's run must be the whole command, not only check_gpu_runtime, and if it does crash, criterion 1's check-level fix is necessary but not sufficient and a second task is owed.

Also noted after review: the label coverage test now asserts the fake torch version in the gpu-runtime detail, so a fake that failed to take is a red test rather than one that quietly asked the real card. And CHECK_LABELS is only exercised through main() - settings.py:604 calls checks(include_gpu=False) without on_start - so the coverage test proves the labels agree, not that the web path uses them.

REVIEW PASS 2026-09-22 (fixer). Changed: tests/test_doctor.py, nothing else. scribe/ is untouched by this pass. No criteria ticked, no status change, no commit.

THREE FINDINGS FIXED, each proven by a mutation that did not bite before.

1. THE SEAM HAD NO TEETH (major). `_hardware()` replaced `doctor.nvidia_hardware_present` with `raising=False`, so a probe that was renamed or bypassed left the stub parked on the module as a dead attribute and three tests passed for the wrong reason: on this machine the real probe answers True, so the broken-driver and both hidden-card tests stayed green; on the card-less machine criterion 9 wants, the mirror image - the real probe answers False and the two no-card tests pass vacuously. `raising=False` is gone from all five seam replacements (the probe, `_NVIDIA_SMI_LOCATIONS`, three `_SYSFS_PCI_DEVICES`); every one of those names exists, so the flag bought nothing and cost the seam its teeth. `_hardware()` now also returns the answers the probe gave, asserted where the check must ask it: `asked == [False]` in the no-card test, `asked == [True]` in the broken-driver test. Mutation 11 (the probe renamed): was 9 failed with those three green, now 16 failed with all three red. Mutation 14, new (the name kept and the probe bound at import, so a test's replacement never reaches the check): the broken-driver test falls on the witness alone - "assert [] == [True]" - while ok, detail and fix_hint all still pass. That is precisely the vacuity being closed.

2. THE HINT TEST TOLERATED BLANKS (major). `assert len(named) >= 10` over 12 non-empty hints let two failing checks lose their fix_hint without a word, in the test whose whole job is criterion 2. It now collects (case, hint) pairs and asserts the empty ones are exactly ["gpu-runtime: no NVIDIA hardware"] - the single legitimate blank, because that machine has nothing to fix. The cases are named by hand rather than keyed on check.name: three gpu-runtime branches all report the same name, and the failure message has to say which one went missing. Mutation 13 (check_ffprobe's failing hint set to ""): was 51 passed, now 1 failed, and the message names ffprobe.

3. TWO PRODUCTION BEHAVIOURS HAD NO PIN (minor, both from the mutation pass). The `_NVIDIA_SMI_LOCATIONS` arm of the probe could be deleted with every test still green - and it is the only signal left on a machine whose card the OS has stopped enumerating under Enum\PCI, which is this probe's named blind spot. New test_the_driver_tool_off_the_path_still_counts_as_a_card: a listed location that exists answers True, one that does not answers False. Mutation 12: was 51 passed, now 1 failed. And the Apple Silicon healthy arm's `fix_hint=""`, the thing that keeps --json free of nulls, could drift back to None unnoticed; nothing on this machine reaches that branch. New test_a_healthy_apple_silicon_leaves_an_empty_hint_and_never_a_null. Mutation 10: was 51 passed, now 1 failed.

ALSO FIXED (minor, code review). The hint test claimed "every check is driven into the branch where its hint lives" and missed three: check_python, check_sqlite and check_data_dir_writable. All three are collected now - an older sys.version_info, an older sqlite_version_info, a NamedTemporaryFile that refuses - each inside its own monkeypatch.context() so the fakes do not follow the other checks into their branches. 16 cases where there were 13, and the docstring now says what the test actually covers: one failing branch per check, and all four of gpu-runtime's.

NOT CHANGED, AND WHY.
* CRITERION 1'S WHOLE-COMMAND HALF STAYS OPEN and is deliberately not fixed here. Two verifiers landed on the same point: check_gpu_smoke is required (optional is False on every return) and this task never touched it, so on a machine with no card `python -m scribe.doctor` exits 0 only if the smoke independently survives - and the measurement on this machine (CTranslate2 4.8.2 seeing 0 cuda devices, exit 139 inside the first model.transcribe()) points the other way. No code change rescues it: a segfault is not an exception, and it kills the process whatever the exit-code logic says. So criterion 1 is proven at CHECK level - red first, the seam replaced, gpu-runtime reading as information with no nvidia-smi advice - and the whole-command half moves to criterion 9, whose run must be `python -m scribe.doctor` entire and not check_gpu_runtime alone. If that run exits 139, a task on gpu-smoke is owed before the installer may claim a CPU machine passes the gate. TASK-092 is NOT that task - it is the CPU-fallback-behind-a-broken-driver decision - so the segfault follow-up is still unfiled.
* The module docstring still says the GPU checks are "optional until the CUDA pin set is frozen (plan Task 8)". Stale, and now doubly wrong since check_gpu_runtime has a required-and-green path. Outside this task's criteria, so it is left for the orchestrator's cleanup rather than smuggled into this diff.
* real-hidden-card-at-HEAD.txt stays nine bytes. What establishes "the segfault is pre-existing" is three files together, not that one: head/, whose scribe/doctor.py was diffed byte-identical to `git show HEAD:scribe/doctor.py`; real-hidden-smoke.txt, the same crash reached through gpu_smoke on the cpu path; and diag-cpu-smoke-segfault.txt, which locates it inside the first model.transcribe(). Re-running buys hygiene, not evidence. The follow-up task should record its command line beside its exit code.

CORRECTION to the implementation notes above: "the 27 pre-existing tests still pin stdout" is wrong. There are 23 pre-existing tests in tests/test_doctor.py; 27 was the pass count of the red run - 23 old plus the 4 new tests that legitimately pass at HEAD.

EVIDENCE FOR THIS PASS, all under .../scratchpad/build/TASK-089.12/, every process fenced with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE. final-test_doctor.txt: "53 passed in 13.89s" - the 51 from before plus the two new tests. No other test file was re-run, because nothing under scribe/ changed in this pass. Bite runs in mut-fix/, a fresh copy of the working tree taken AFTER these fixes (diff against the working tree printed byte-identical, and `import scribe.doctor` proven to resolve inside the copy), one mutation per process, restored to pristine afterwards: mutfix-0-baseline.txt 53 passed; mutfix-10, -12, -13 one failure each; mutfix-11 16 failed; mutfix-14 4 failed. `grep -rn MUTANT` over the repository's scribe, tests and packaging finds nothing.

## Verification (orchestrator, 2026-09-22)

Run here, fenced (`SCRIBE_DATA_DIR=C:\ms-f\doc`), not taken from the report.

### Criterion 8 and ADR-012's Verification line, on the real card

    .venv/Scripts/python -m scribe.doctor          (card visible)
    [OK  ] accel        transcription on cuda, diarization on cuda
    [OK  ] gpu-runtime  torch 2.10.0+cu128 CUDA 12.8 on NVIDIA GeForce RTX 3080 Laptop GPU (16.0 GB)
    [OK  ] gpu-smoke    large-v3-turbo on cuda/float16: 75 words from 30s in 16.5s
    All required checks passed (skipped: weights not downloaded).
    exit=0

So the change did not buy the card-less case by softening every case, which
was the failure mode to watch for.

### Criterion 7, on a real run rather than a unit test

    CUDA_VISIBLE_DEVICES=-1 .venv/Scripts/python -m scribe.doctor
    [FAIL] gpu-runtime  torch 2.10.0+cu128 (CUDA 12.8) cannot reach a device;
                        CUDA_VISIBLE_DEVICES='-1' is set here
                        -> Check the NVIDIA driver with nvidia-smi; ...

And `doctor.nvidia_hardware_present()` answered True on this machine. A card
that is present and unreachable stays a required failure, which is the rule
Robert stated on 2026-09-22: a broken driver must be impossible to miss.

### Criteria 2, 3, 4, 5, 6

    ffmpeg hint, with _run stubbed to fail:
      win32    Install ffmpeg and put it on PATH (winget install Gyan.FFmpeg).
      darwin   Install ffmpeg and put it on PATH (brew install ffmpeg).
      linux    Install ffmpeg and put it on PATH (apt install ffmpeg).
    no hint over any CPU check names pip (word boundary): none

    --json --no-gpu:  12 rows, keys exactly
                      ['detail', 'fix_hint', 'name', 'ok', 'optional'],
                      no null fix_hint, 0 bytes on stderr, exit 0

    --no-gpu:         exit 0, closing line
                      "All required checks passed (skipped: weights not downloaded)."

"not wired yet" is gone and the closing line says what the skip costs, on a
real run and not only in a test.

### Criterion 1 is NOT ticked, and why

Its first half is built and tested the way the criterion itself allows - the
hardware question is asked of a function the test replaces. Its second half,
`python -m scribe.doctor` exits 0, is proven only with `gpu_smoke` stubbed,
and I now have evidence that the unstubbed command is unreliable in the
neighbouring configuration. Measured here, three runs of the same command with
the card hidden:

    run 1  exit=139   0 bytes of output
    run 2  exit=1     the full table, gpu-smoke OK on cpu/int8,
                      75 words from 30s in 62.3s
    run 3  exit=139   0 bytes of output

**Against the build's own report.** It concluded that a card-less machine
cannot reach exit 0 because gpu-smoke segfaults, from a single exit=139. That
is too strong: the crash is intermittent, and the run that survived did the
work. What I ruled out, each with a run of its own - CPU mode as such (card
visible, `device='cpu'`, the large model builds), the model's size (card
hidden, `tiny` transcribes), `torch.cuda.is_available()` alone (card visible,
probe first, builds), and the hidden card alone (card hidden, `device='cpu'`
passed so the probe never runs, builds). A reduced reproduction of the
combination did not crash in two attempts. `faulthandler` puts the frame in
`faster_whisper/transcribe.py:689 in __init__` through
`scribe/stages/transcribe.py:583`, so it is the model's constructor and not
the transcription.

Pre-existing, not this task's: the diff touches `scribe/doctor.py` only, and
within it the check logic, the hints, the rendering, `--json` and the
progress lines - `gpu_smoke`'s body, `load_model` and `cuda_setup` are
untouched and `scribe/stages/transcribe.py` is not in the diff.

Recorded as **TASK-093** with the measurement and five criteria, the first of
which is to characterise the crash before anybody fixes it. Whether a machine
with no NVIDIA card at all crashes the same way is exactly criterion 9's
question and is settled there.

### Criterion 9 stays open

No machine without an NVIDIA card is reachable from this session; this one has
an RTX 3080. What would settle it: one run on a windows or ubuntu GitHub
runner from a probe branch (billed, and CI passes `--no-gpu` today,
`ci.yml:82`), or a laptop Robert names.

### Seen in the same real output, out of scope

`[SKIP] models  1.6 GB still to download: whisper-large-v3-turbo` stands two
lines above `gpu-smoke large-v3-turbo ... 75 words`, which used those very
weights out of the cache. The inventory looks for a repo id the cache does not
carry under that name - the cache holds
`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, 1,621,667,008 bytes. So the
doctor tells this machine to download something it already has. The build
reported it as out of scope and it stays that way; it is named here because it
is visible in this task's own evidence.

### Whole suite

One file per process, fenced: **2896 passed, 10 skipped, 0 failed, 0 errors** over 81 files, reconciling exactly with "2906/2916 tests collected (10 deselected)" since 2896 + 10 = 2906. That is +30 on the run after TASK-089.11 (2876 collected), all of them in `tests/test_doctor.py`, which goes from 23 tests to 53.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The doctor no longer fails a machine for not having an NVIDIA card. A CUDA build that cannot reach a device reads as information - "no NVIDIA GPU on this machine; transcription on cpu", no nvidia-smi advice - but only when a new probe says the machine has no NVIDIA hardware at all. That probe reads nvidia-smi on PATH and in its known locations plus the PCI vendor id the firmware enumerated, never CUDA_VISIBLE_DEVICES (which simulates a missing card and a broken driver identically), and every doubt answers "hardware present". So Robert's rule of 2026-09-22 holds: a card behind a broken driver stays a required failure and cannot be missed. ADR-012's Must is untouched - a CPU-only torch build is still red, only its hint changed. Hints now name tools that exist: uv sync instead of pip anywhere, and winget, brew or apt per OS. The closing line says what each skipped check costs and "not wired yet" is gone. --json prints one object per check with name, ok, optional, detail and fix_hint and no nulls; each check's name is printed as it starts on a TTY, so a cold gpu-smoke is no longer a blank terminal. Verified on real runs by the orchestrator: the card visible gives exit 0 with gpu-smoke at 75 words from 30s in 16.5s on cuda/float16 (ADR-012's Verification line); the card hidden gives a required FAIL naming the variable, with the hardware probe answering True; --json gives 12 rows with exactly the five keys and nothing on stderr; the ffmpeg hint differs per OS and no hint names pip. Whole suite over 81 files: 2896 passed, 10 skipped, 0 failed, reconciling with 2906 collected; test_doctor.py goes from 23 tests to 53. Criterion 1 is NOT ticked: its whole-command half is proven only with gpu_smoke stubbed, and three real runs with the card hidden gave exit 139, exit 1, exit 139 - an intermittent access violation in the model's constructor, pre-existing and recorded as TASK-093 against the build's own too-strong conclusion that it was deterministic. Criterion 9 stays open: no machine without an NVIDIA card is reachable from this session.
<!-- SECTION:FINAL_SUMMARY:END -->
