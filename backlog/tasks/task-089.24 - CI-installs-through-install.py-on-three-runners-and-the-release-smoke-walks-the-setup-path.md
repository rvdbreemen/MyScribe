---
id: TASK-089.24
title: >-
  CI installs through install.py on three runners and the release smoke walks
  the setup path
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-23 20:05'
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
- [ ] #1 ci.yml on all three runners installs with `python install.py --non-interactive` and adds .tools/bin to GITHUB_PATH, so `uv lock --check` still runs. 'Make room (Linux)' and the macOS `brew install ffmpeg` move in front of the install step. Today both run after it ('Install the locked environment' at .github/workflows/ci.yml:52-53, the ffmpeg step at :55-62 with apt for Linux and brew for macOS, 'Make room (Linux)' at :69-78), and that order no longer works: install.py ends on a proof that needs ffmpeg and the doctor's 10 GB floor (scribe/doctor.py:42). The Linux half of that step and the Windows ffmpeg step (:64-67) either move too or go, because install.py fetches the pinned ffmpeg there (TASK-089.17 criterion 4); the notes say which. The ci.yml diff is shown. The Doctor and Suite steps run in the environment that install.py made. The run is green, and the minutes before and after are reported.
- [ ] #2 release.yml's smoke walks sync, then setup with `{}` answers, then /health, on all three OSes. A probe branch with a deliberately broken setup turns it red, and the log is shown.
- [ ] #3 Both workflows use the pinned uv from packaging/tools.json, not the floating install script.
- [ ] #4 The Windows smoke is no longer mute: on failure the run prints <home>/logs/launcher-smoke.log. A deliberately failing probe shows the launcher's own lines in the Windows log.
- [ ] #5 release.yml runs build_release.py without first creating the project environment (`uv run --no-project`, or plain python; today :128 uses `uv run python`). The Windows job time before and after is reported. The expected saving of about 3 minutes was read off log timestamps in the design run and has not been measured.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. ci.yml: Make room (Linux) and brew ffmpeg (macOS) before the install; install with python install.py --non-interactive; .tools/bin on GITHUB_PATH so uv lock --check uses the pinned uv; Doctor, check-pin and Suite in .venv's python; apt and choco ffmpeg steps removed because install.py fetches the pinned ffmpeg on Windows and Linux. 2. release.yml: no uv install at all - build_release.py is stdlib only and uses the payload's pinned uv; run it with the runner's python. The smoke (sync, setup with {} over stdin, /health, a page) and the smoke-log print exist since TASK-089.15. 3. Push this branch, dispatch ci.yml and release.yml (publish only runs on a tag), read the logs and minutes. 4. Red probes for #2 and #4 on a separate probe branch, deleted afterwards.
<!-- SECTION:PLAN:END -->
