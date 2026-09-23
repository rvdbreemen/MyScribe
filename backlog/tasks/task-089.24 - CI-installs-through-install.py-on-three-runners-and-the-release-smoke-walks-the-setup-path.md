---
id: TASK-089.24
title: >-
  CI installs through install.py on three runners and the release smoke walks
  the setup path
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-23 21:44'
labels:
  - ci
  - release
  - packaging
dependencies:
  - TASK-089.15
  - TASK-089.17
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 161000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CI never executes the path every new user walks. `--smoke`, `--sync-only` and `--doctor` all return before run_window (packaging/launcher/myscribe_launcher.py:717-728), and the tests cover setup_command and setup_needed as pure functions only (tests/test_launcher.py:378, :410). That is how a first-run dialog that cannot complete shipped green: three builds, three smoke tests, a published release (TASK-089.01).

Both workflows install uv from its floating install script (.github/workflows/ci.yml:36-45; release.yml:77-86), although the project pins uv by version and sha256 in packaging/tools.json. A reader saw the last run get 0.12.17 against the pin of 0.12.13; that log was not re-read when this task was written. ci.yml then runs a bare `uv sync --frozen` (:52-53), which is not the install a user gets once TASK-089.17 exists.

A reader also found the Windows smoke mute: the launcher's own 'smoke: /health ok' line is in the Linux and macOS logs and missing from the Windows one. The cause is an inference, not a measurement: the binary is built windowed and has no console stdout.

This does not replace TASK-040.04, whose one open criterion waits on build attestations that a user-owned private repository cannot have. It changes what the existing jobs install with and what the smoke walks. Actions minutes are billed on this repository and macOS counts tenfold (TASK-040.04), so the minutes before and after are part of the evidence.

Needs a real machine: GitHub's three runners, which are billed; macOS minutes count tenfold. No hardware of Robert's.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ci.yml on all three runners installs with `python install.py --non-interactive` and adds .tools/bin to GITHUB_PATH, so `uv lock --check` still runs. 'Make room (Linux)' and the macOS `brew install ffmpeg` move in front of the install step. Today both run after it ('Install the locked environment' at .github/workflows/ci.yml:52-53, the ffmpeg step at :55-62 with apt for Linux and brew for macOS, 'Make room (Linux)' at :69-78), and that order no longer works: install.py ends on a proof that needs ffmpeg and the doctor's 10 GB floor (scribe/doctor.py:42). The Linux half of that step and the Windows ffmpeg step (:64-67) either move too or go, because install.py fetches the pinned ffmpeg there (TASK-089.17 criterion 4); the notes say which. The ci.yml diff is shown. The Doctor and Suite steps run in the environment that install.py made. The run is green, and the minutes before and after are reported.
- [x] #2 release.yml's smoke walks sync, then setup with `{}` answers, then /health, on all three OSes. A probe branch with a deliberately broken setup turns it red, and the log is shown.
- [x] #3 Both workflows use the pinned uv from packaging/tools.json, not the floating install script.
- [x] #4 The Windows smoke is no longer mute: on failure the run prints <home>/logs/launcher-smoke.log. A deliberately failing probe shows the launcher's own lines in the Windows log.
- [x] #5 release.yml runs build_release.py without first creating the project environment (`uv run --no-project`, or plain python; today :128 uses `uv run python`). The Windows job time before and after is reported. The expected saving of about 3 minutes was read off log timestamps in the design run and has not been measured.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. ci.yml: Make room (Linux) and brew ffmpeg (macOS) before the install; install with python install.py --non-interactive; .tools/bin on GITHUB_PATH so uv lock --check uses the pinned uv; Doctor, check-pin and Suite in .venv's python; apt and choco ffmpeg steps removed because install.py fetches the pinned ffmpeg on Windows and Linux. 2. release.yml: no uv install at all - build_release.py is stdlib only and uses the payload's pinned uv; run it with the runner's python. The smoke (sync, setup with {} over stdin, /health, a page) and the smoke-log print exist since TASK-089.15. 3. Push this branch, dispatch ci.yml and release.yml (publish only runs on a tag), read the logs and minutes. 4. Red probes for #2 and #4 on a separate probe branch, deleted afterwards.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built and proven 2026-09-23 on GitHub (orchestrator). Pushed to task-089-installer-first-slice only, as Robert allowed; no tag, no PR.

#1 ci.yml installs with install.py --non-interactive on all three runners; .tools/bin on GITHUB_PATH so uv lock --check uses the pinned uv; Make room (Linux) and brew ffmpeg (macOS) before it; apt and choco ffmpeg steps gone (install.py fetches the pinned ffmpeg on Windows and Linux). Doctor, check-pin and Suite run in .venv's python. Green: run 35921408104, all three jobs success. Getting there took two fix rounds, both about code written since the last CI run off Windows, and they are the most valuable result of this task: run 35913680905 had 8 test failures on macOS and a hang on Linux (test_launcher took the console door on a runner without Tk and waited on its stand-in app for good; found through pytest.ini's faulthandler_timeout traceback after cancelling); run 35919407583 had one left on both (a test patched sys.platform, and ssl then looked for the Windows certificate store). One of the macOS failures was a product inconsistency, not a test bug: ollama_setup.install_locations read sys.platform, so a Windows install plan built on a Mac named /Applications, and a test wrote a fake binary into the runner's real /Applications. Fixed in commits c93f72f and 1ff8ab9.
Minutes, CI, before (main, run 35533533972, 2026-09-20) and after (run 35921408104): ubuntu 5:11 -> 7:26, macOS 3:30 -> 6:06, Windows 12:32 -> 20:38. The install step itself took 2:21, 1:07 and 6:51 - it now fetches the pinned uv and ffmpeg and ends on the proof, which transcribes the 30-second clip on the CPU. The rest of the growth is the suite, which grew by several hundred tests since that run. macOS minutes count tenfold.
The check-pin step ran on GitHub for the first time in run 35913680905 and was green.
#2 release.yml's smoke (TASK-089.15) walks sync, the setup engine with {} over stdin, /health and a page. Red probe: commit 95d6c9b broke --apply-stdin on purpose, release run 35923728188 built that SHA: windows-x64, macos-arm64 and linux-x64 all failure, publish skipped; reverted in efaa349, whose tree equals 1ff8ab9's. Green without the probe: release run 35913685730, all three success.
#3 release.yml installs no uv at all (build_release.py is stdlib only and uses the payload's pinned uv); ci.yml uses the uv install.py fetched from packaging/tools.json. No floating install script in either workflow.
#4 The Windows smoke is not mute: in the probe run the Windows log prints the launcher's own log file, '--- ...\logs\launcher-smoke.log ---', then 'PROBE: the setup engine is broken on purpose' and 'smoke: applying an empty document exited 3'.
#5 build_release.py runs on the runner's python, not uv run. Windows release job before (v0.5.1 tag run 35458996001) 6:30, after (run 35913685730) 3:26: the roughly 3 minutes the design run read off timestamps, now measured.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
CI now installs a clone the way a person does - python install.py on all three runners - and the release workflow installs no floating uv and runs build_release.py on the runner's Python, 3 minutes faster on Windows. Proven on GitHub: CI green on all three (run 35921408104) after two rounds of fixes for failures the first runs off Windows found, including a Linux hang and a product inconsistency in Ollama's install paths; a deliberately broken setup turned the release smoke red on all three (run 35923728188) with the launcher's own lines in the Windows log, then reverted.
<!-- SECTION:FINAL_SUMMARY:END -->
