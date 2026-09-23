---
id: TASK-092
title: >-
  MyScribe never falls back to the CPU behind a broken driver without being told
  to
status: In Progress
assignee:
  - '@gpu-lane'
created_date: '2026-09-22 10:32'
updated_date: '2026-09-23 22:01'
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
- [x] #1 Red first: on a machine where nvidia hardware is present and torch.cuda.is_available() is False, a transcribe job is refused rather than run on the CPU. The refusal names the driver and nvidia-smi, says the switch exists and where, and never starts a model. Today the job runs to completion on the CPU with nothing said; that output is shown.
- [x] #2 A machine with no NVIDIA hardware is unchanged: the job runs on the CPU, nothing is refused and nothing extra is said. A test covers both directions off the same seam, so a change that refuses everything cannot pass.
- [x] #3 Settings carries one switch, "Transcribe on the CPU when the GPU is unavailable", off by default and written as its own row. With it on, the job runs on the CPU and every run records which backend it used, so a transcript made on the CPU can be told apart afterwards. With it off or absent, the job is refused. A test per state, and a test that a missing row means off (no row, no fallback - the shape ADR-016 uses for the provider).
- [x] #4 The same rule covers the speaker pass: diarization_device() does not answer cpu behind present-but-unreachable hardware unless the switch is on. One test, and the notes say whether a job that transcribed on the GPU may still diarize on the CPU or whether the two move together.
- [x] #5 nvidia_hardware_present() moves to scribe/accel.py and the doctor reads it from there, so the runtime does not import scribe.doctor. TASK-089.12's tests move with it rather than being duplicated, and the diff is shown. If the move turns out to cost more than it saves, the notes say why it was left where it is.
- [x] #6 CUDA_VISIBLE_DEVICES stays what it is: somebody who sets it is choosing, so a job is not refused for it. A test pins that setting it to -1 on a machine with hardware runs on the CPU without the switch, and the notes say why this is not a hole - it cannot be set by accident, and TASK-089.12 made the doctor say out loud when it is set.
- [x] #7 The refusal is not a crash: the job ends in the failed state with its sentence in error_detail, the board shows it, and nothing is half-written. Exit codes follow the runner's existing meanings and a test pins which one this is.
- [ ] #8 Needs a real machine: nobody has run this behind an actually broken driver. The code and the seam are built and tested with the hardware probe and torch.cuda.is_available() replaced; the notes say plainly that no real broken-driver run has happened, and name what would settle it - Robert disabling the display adapter in Device Manager on his own machine, which is reversible.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. ADR check: no record of this decision in docs/adr (grep for fallback/driver finds only ADR-004's hidden small model). Draft ADR-018 (number pending the lead) as Proposed, signed as the agent; never accept.
2. #5 first, as a pure move: nvidia_hardware_present, its helpers and constants go from scribe/doctor.py to scribe/accel.py; the doctor calls accel.nvidia_hardware_present() by attribute (no re-export). The probe tests move from tests/test_doctor.py to a new tests/test_accel.py with only the module prefix changed; test_doctor's _hardware helper and test_setup_prove.py:627 retarget to accel. Diff shown with --color-moved.
3. Red first for #1: a transcribe job through runner.main with the real load_model and the real accel gate, only cuda_available, the probe, ensure_cuda_libs and faster_whisper.WhisperModel replaced. Today: done on cpu. Keep that output.
4. The gate in accel: GpuUnreachable(RuntimeError) with the sentence (driver, nvidia-smi, the switch and where). transcription_backend(*, cpu_fallback=False) and diarization_device(*, cpu_fallback=False) raise it when CUDA is unreachable, the probe says hardware, CUDA_VISIBLE_DEVICES is not set, the platform is not macOS, and the switch is off. describe() says the refusal in words instead of raising (the doctor's accel check runs under --no-gpu too).
5. The switch: setting row cpu_fallback, exactly "1" is on; missing, "0" or anything else is off (ADR-016's shape). accel.cpu_fallback_allowed(conn). A card of its own in Settings > This machine with POST /settings/cpu-fallback.
6. transcribe.run reads the switch and passes cpu_fallback through transcribe_audio to load_model, which refuses before ensure_cuda_libs and before any WhisperModel. diarize.run resolves the device after the diarize=False skip with the same rule.
7. runner._ERROR_CODES gets GpuUnreachable -> GPU_UNREACHABLE; exit 1 (failed) pinned by a test. gpu_smoke gives the refusal its own hint instead of the cuDNN one.
8. Tests per criterion (#1-#7), the existing tests that now need the probe stubbed changed one by one and listed; mutants on a copy; notes with #8's Device Manager run for Robert.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Build notes (gpu-lane, 2026-09-23) - not committed, criteria not ticked

EVIDENCE = scratchpad/build/gpu/092 (session d0ea7837). Worktree: MyScribe-wt-gpu.

### What changed and why
- scribe/accel.py: the hardware probe moved in from scribe/doctor.py as a pure move (#5). New: SETTING_CPU_FALLBACK = "cpu_fallback", CPU_FALLBACK_ON = "1", CPU_FALLBACK_LABEL, CPU_FALLBACK_WHERE = "Settings > This machine", GpuUnreachable(RuntimeError), REFUSAL (the sentence), cpu_fallback_allowed(conn), _refuse_the_cpu(). transcription_backend() and diarization_device() take a keyword cpu_fallback=False and raise GpuUnreachable instead of answering cpu when all of these hold: CUDA cannot reach a device, the switch is off, CUDA_VISIBLE_DEVICES is not in the environment (membership, so an empty value counts), the platform is not macOS, and the probe finds NVIDIA hardware. describe() never raises. It prints "jobs refused: ..." so the doctor's accel check, which also runs under --no-gpu, still renders the table on the machine this task is about.
- scribe/stages/transcribe.py: run() reads the switch and passes cpu_fallback through transcribe_audio() to load_model(), which asks accel only when no device is named. The refusal is raised before ensure_cuda_libs(), before WhisperModel and before _persist: no model, no run row.
- scribe/stages/diarize.py: run() resolves the device after the diarize=False skip with the same rule and passes it down. An explicit params device is still never second-guessed.
- scribe/runner.py: _ERROR_CODES gains accel.GpuUnreachable -> GPU_UNREACHABLE. Without it a RuntimeError subclass lands as RUNTIME.
- scribe/doctor.py: calls accel.nvidia_hardware_present() by attribute, with no re-export, so a stub left on doctor is an AttributeError and not a silent no-op. gpu_smoke gives GpuUnreachable its own hint (nvidia-smi, or the switch) instead of the cuDNN one.
- scribe/web/settings.py, scribe/templates/_settings_cpu_fallback.html, scribe/templates/settings.html: one card in Settings > This machine, POST /settings/cpu-fallback. On writes the row "1". Off deletes the row, so off and never-asked are one state.
- docs/adr/ADR-018-a-machine-with-an-nvidia-card-never-falls-back-to-the-cpu-unless-the-settings-switch-is-on.md: Proposed, signed "Claude (agent, session 2026-09-23)". No ADR recorded this decision: a grep of docs/adr for fallback and driver found only ADR-004's hidden small model. The lead confirmed the number 018 for this lane and regenerates the index once at the merge. So ADR-INDEX.json, ADR-INDEX.md and docs/adr/README.md were NOT regenerated: adr new rewrote them and I reverted those three generated files. There is no related: field, so ADR-012 and ADR-016 are untouched; the links are prose. adr-lint --strict with all acceptance gates over docs/adr passes (EVIDENCE/adr-lint-018.txt). One open question in it for Robert: the task says he was asked with three options, but their wording is not in the repository, so the ADR's option list is my reconstruction.

### Criteria and proof
- #1 red: EVIDENCE/red-test_cpu_fallback.txt. Its first failure reads "exit 0, job done None None, models built [('large-v3-turbo', 'cpu')], runs ['cpu']": today the job runs to done on the CPU with nothing said. Green: test_a_card_that_cuda_cannot_reach_refuses_the_job_instead_of_running_on_the_cpu (EVIDENCE/green-test_cpu_fallback.txt, 19 passed). It drives the real transcribe.run and load_model through runner.main; only cuda_available, the probe (with a witness), ensure_cuda_libs and faster_whisper.WhisperModel are replaced. It asserts exit 1, failed, GPU_UNREACHABLE, zero model constructions, zero run rows, and a sentence naming driver, nvidia-smi, the switch label and "Settings > This machine" (the label read from settings.SECTIONS).
- #2: test_the_same_seam_runs_a_cardless_machine_on_the_cpu_and_refuses_a_broken_card, parametrized no-nvidia-hardware and card-behind-broken-driver off one seam; test_a_cardless_machine_is_told_nothing_extra_by_accel (describe unchanged).
- #3: test_only_the_switch_turned_on_lets_a_job_run_on_the_cpu with no-row, off, anything-else and on; test_the_switch_reads_off_without_a_row; test_settings_shows_the_switch_off_by_default_and_turns_it_on_and_off. With the switch on, the run's params_json.device and the transcribe job event both say cpu. That record already existed; it is now pinned rather than added.
- #4: test_the_speaker_pass_is_refused_behind_an_unreachable_card_unless_the_switch_is_on. The two stages move together: both ask the same cuda_available() and the same switch, so a job that transcribed on the GPU diarizes on the GPU. The only split is a driver that dies between the two stages. That job fails at diarize with GPU_UNREACHABLE after the run row is written, as any diarize failure does today.
- #5: EVIDENCE/diff-move-5.txt is the diff with --color-moved. test_doctor.py had 60 test functions before; now it has 52, and the new tests/test_accel.py has 8, changed only in the module they name. test_doctor's _hardware helper and tests/test_setup_prove.py:627 retarget their stub to accel; setup_prove belongs to the setup lane and the change is one line. Green: EVIDENCE/green-move-test_accel.txt (8 passed), green-move-test_doctor.txt (56), green-move-test_setup_prove.txt (43). The precise claim: accel and the transcribe and diarize stages do not import scribe.doctor (test_the_stages_and_accel_do_not_import_the_doctor, in a fresh interpreter). That test passes at HEAD too, so it is a guard against a future import, not a red-first test. The proof that the doctor now reads the probe from accel is test_doctor's _hardware witness: it stubs accel.nvidia_hardware_present and asserts the stub was called. scribe/runner.py still imports scribe.doctor, as before, for DOCTOR_STAGES, CheckFailed and NotEnoughDisk; moving those is not this task.
- #6: test_hiding_the_card_with_cuda_visible_devices_runs_on_the_cpu_without_the_switch for "-1" and "". Not a hole: the variable has to be set on purpose in the process environment, and the doctor names it out loud when it is set (TASK-089.12). Nothing in MyScribe sets it: grep -rn CUDA_VISIBLE_DEVICES over scribe/ finds three code lines, all reads (scribe/accel.py:260 and scribe/doctor.py:382 and :399, membership and the value for the detail), and no assignment, setdefault or putenv.
- Real machine, card visible: python -m scribe.doctor --no-gpu from the worktree, fenced, prints "[OK  ] accel        transcription on cuda, diarization on cuda", exit 0, 0 bytes on stderr (scratchpad/build/gpu/093/nogpu-wt/). The same line as HEAD's copy: the change is silent on a working card.
- #7: test_the_refusal_is_exit_one_and_the_board_shows_its_sentence (exit 1 is the runner's "failed"; /jobs shows GPU_UNREACHABLE and nvidia-smi; the job page shows the switch label) and test_the_error_code_is_its_own_entry_not_the_runtime_catch_all. EVIDENCE/ui-look.txt holds the rendered card (Off, then On with row "1") and the board's error cell. Nothing half-written: no run row. Probe, prepare and proxy have run by then and their output stays, as for any transcribe-stage failure.
- Mutants on a copy, one per run (scratchpad/build/gpu/mut-*.txt, summary in mut-summary.txt): never-refuse 10 failed; refuse-every-cpu 4; missing-row-is-on 8; any-value-is-on 1; env-var-ignored 2; no-mac-exemption 1; transcribe-ignores-switch 1; diarize-ignores-switch 1; no-error-code 7; describe-raises 1. grep -rn MUTANT over scribe, tests, packaging and scripts in the worktree finds nothing.

### Deviations and consequences
- macOS is exempt in the gate, not in the probe. The probe answers True for an OS it does not know, which would have refused every Mac without MLX or MPS. Mac behaviour is unchanged and the mps question stays open, as the task says (test_a_mac_is_not_refused_whatever_the_probe_says).
- Every doubt in the probe still counts as a card, per Robert's rule. Consequence: a Linux box whose /sys cannot be read, or an OS the probe was not taught, gets refusals until somebody turns the switch on. The sentence says where.
- The doctor's gpu-smoke does not read the switch. Behind a broken driver it reports the refusal with its own hint; gpu-runtime is already red there.

### NOT done: #8 is Robert's
Nobody has run this behind a really broken driver. Every test above replaces the probe and torch.cuda.is_available(). What settles it, reversibly, on Robert's machine:
1. Device Manager > Display adapters > the RTX 3080 > Disable device. Leave CUDA_VISIBLE_DEVICES unset.
2. Run .venv/Scripts/python -m scribe.doctor --no-gpu. Expect the accel line "jobs refused: an NVIDIA card is present but CUDA cannot reach it, and 'Transcribe on the CPU when the GPU is unavailable' is off". The probe should still find VEN_10DE under Enum\PCI, and nvidia-smi is still on disk.
3. Start the app and transcribe a short file. Expect a failed job with GPU_UNREACHABLE and the sentence naming nvidia-smi and Settings > This machine, and no transcript.
4. Turn the switch on in Settings > This machine and retry. Expect done, and the run's params_json device "cpu".
5. Turn the switch off, enable the device again, and run the doctor without --no-gpu to see gpu-smoke back on cuda.
If step 2 shows "transcription on cpu" instead, the probe did not see the disabled card, and that is the finding.

### Existing tests that changed, and the suite
- tests/test_stage_transcribe_mlx.py: two tests asked accel for "cpu" with CUDA stubbed off but the real probe left in place. On this machine the registry has VEN_10DE, so they now met the refusal (EVIDENCE/suite1/test_stage_transcribe_mlx.txt: 2 failed, GpuUnreachable). Each now stubs nvidia_hardware_present to False before asking for "cpu"; no assertion changed. Diff: EVIDENCE/diff-test_stage_transcribe_mlx.txt. They now depend on that stub, measured: without it they fail here and would pass on a card-less machine.
- tests/test_doctor.py and tests/test_setup_prove.py retarget their probe stub from doctor to accel (see #5). No assertion changed.
- No autouse stub was added to conftest, on purpose: the rule has to stay visible to the tests about it.
- Every test file that imports a changed module, one pytest process each (68 files; EVIDENCE/suite1/_summary.txt): 2847 passed and 10 skipped in 66 files; test_llm_live has only deselected live tests (exit 5, no tests collected); test_stage_transcribe_mlx 2 failed before the stub and 12 passed after (EVIDENCE/green-final-test_stage_transcribe_mlx.txt). Total 2859 passed, 10 skipped, 0 failed. The TASK-093 doctor change landed while that run was going, so every file that names the doctor was run again afterwards: all green (EVIDENCE/green-final-*.txt).

Verified 2026-09-24 by the orchestrator in MyScribe-wt-gpu, fenced, one file per process: test_cpu_fallback 19, test_accel 8, test_doctor 56 (8 moved to test_accel), test_task093_census 7, test_setup_prove 43, test_stage_transcribe_mlx 12, test_stage_transcribe 69, test_stage_diarize 76, test_runner 13, test_web_settings 54, all passed; grep MUTANT finds nothing. ADR-018 is Proposed and signed as the agent; accepting it, and its open question about the wording of the three options Robert was shown, are his. #8 stays open for his reversible run: disable the display adapter in Device Manager and follow the five steps above.
<!-- SECTION:NOTES:END -->
