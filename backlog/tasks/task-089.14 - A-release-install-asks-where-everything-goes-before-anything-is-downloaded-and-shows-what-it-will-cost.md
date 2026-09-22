---
id: TASK-089.14
title: >-
  A release install asks where everything goes before anything is downloaded,
  and shows what it will cost
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 17:29'
labels:
  - packaging
  - ux
  - windows
dependencies:
  - TASK-089.01
  - TASK-089.02
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 151000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A release user has no way to keep 12 to 15 GB off a small system drive, and nothing checks free space before the sync. Every folder hangs off one home: env/, python/, cache/, data/, logs/ and pycache/ are all properties of Layout.home (packaging/launcher/myscribe_launcher.py:98-105). The launcher forces SCRIBE_DATA_DIR to <home>/data for the app (:200-207), so the `.env` route that works in a clone does nothing here. The only way to move the home is the MYSCRIBE_HOME variable or `--home` (:59-71, :693), which nothing asks for and nothing persists. A grep for disk_usage in the launcher finds nothing. The doctor's 10 GB floor (scribe/doctor.py:42) runs after the sync and measures the data volume only.

The footprint, as the critic added it up on 2026-09-20 - estimates, not measurements: about 3 GB of environment, the uv cache and a managed Python, 1.6 to 3.1 GB of weights, and if Ollama is installed a 1.57 GB installer, about 4 GB of binaries and a 3.4 to 7.6 GB model. A reader found that Robert moved his own OLLAMA_MODELS to D:, so the concern is real on the reference machine.

Because everything hangs off the home, this is the one question that can only be asked BEFORE the sync (brief: M2, M3, U1, U6). It is therefore asked by the launcher itself and not by the engine, which does not exist yet at that moment. One question, before anything is downloaded: where everything goes, with the free space per volume, the total this install will download, and the disk it needs. Skipping keeps today's location.

The answer is persisted in a small pointer file - one JSON object - next to the default home, which home_dir() reads. That is stdlib-only, so it stays inside ADR-011; ADR-015 records it (TASK-089.02).

The clone door's free-space check before its own sync is a criterion of TASK-089.17.

Needs a real machine: Robert, at a Windows screen on a machine with two volumes (his has C: and D:). The Tk window is unverified, and the macOS and Linux locations have been run by nobody.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 On a first run with no pointer file and no environment, the location question appears before the tools are installed and before the sync. A test over the bootstrap order asserts that nothing was downloaded, and nothing written under any home, before it was answered.
- [x] #2 It shows the default home, the free space on each local volume, the total this install will download and the disk it needs. The total is computed, not a literal: the environment's size for this platform with the date it was measured, the weights from the catalogue for this platform, and Ollama with its model only as 'up to' figures. Each number names its source. The source for the Ollama figures, scribe/ollama_release.json, is created in TASK-089.18, which is built after this task. Until it lands they are labelled literals that carry their date and say they were read from Ollama's release page, not measured; TASK-089.18 criterion 4 switches them to the pin file. The '3 GB' in the launcher (:434-435) and in packaging/windows/myscribe.iss:51 agree with it or are gone.
- [x] #3 The numbers come from files in the payload, read as data. The launcher imports nothing from the app (ADR-011), and the stdlib-only test stays green.
- [x] #4 The answer is stored in a small pointer file, one JSON object, next to the default home: beside that folder, not inside it, so that a moved install leaves nothing under the default home. home_dir() honours it after MYSCRIBE_HOME and `--home`, which keep winning. Tests cover: no file; a file; an unreadable file; and a file naming a folder that no longer exists, which gives one sentence and the question again - never a silent fall-back to the default, which would look like an empty library. No test pins that the file holds one line or one fact: TASK-089.19 criterion 8 may add a second fact to it (ADR-015, Open Questions).
- [x] #5 Skipping keeps today's location and writes no pointer file. The question belongs to a first run only: once an environment exists under the home it is not asked again, and a test shows it.
- [x] #6 Red first: with less free space on the chosen volume than the install needs, today the sync starts anyway. After the change one sentence gives both numbers and no download is attempted. This check also runs when the question was skipped.
- [x] #7 A home that already holds an environment is never moved by this question. Moving an existing install is out of scope, and the sitting says so rather than half-doing it.
- [ ] #8 Needs a person at the screen, on a machine with two volumes: one recorded first run on Windows that chooses a folder on the second drive, and shows env/ and data/ under it, with the models in data/models (scribe/paths.py:40; the launcher's Layout has no models folder of its own, :98-105). The default home holds nothing or does not exist, and the pointer file sits beside it, as criterion 4 says. Robert does this.
- [x] #9 A chosen folder inside the install or payload directory is refused with one sentence, and the question is asked again. Accepted ADR-011's Must Not: 'Put the environment or the data directory inside the install directory' (docs/adr/ADR-011-...md:156-158). On Windows that is %LOCALAPPDATA%\Programs\MyScribe (packaging/windows/myscribe.iss:18), a folder that belongs to the installer, while the welcome text promises that the data is 'left alone when you uninstall' (:51). A relative path, a folder that cannot be written and a UNC path are refused the same way. The UNC refusal has its own reason: the library is SQLite in WAL mode (ADR-013), and SQLite's documentation says WAL does not work over a network filesystem (sqlite.org/wal.html, read for the design spec on 2026-09-20). A mapped drive letter hides the same problem and is not detected; the question's text says so. One test per case, and a refused answer writes no pointer file.
- [x] #10 Where the answer can be given later is said in the question's own text and by `--setup`: MYSCRIBE_HOME, `--home`, or the pointer file, after moving the folder by hand (criterion 7). There is no Settings counterpart. That is a stated exception to scribe/setup.py:21-23, with its reason: the home holds the environment the app runs from (Layout.env_dir, packaging/launcher/myscribe_launcher.py:98), so the running app cannot move it. TASK-089.11 criterion 4 allows this exception by name.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Where the question goes, settled: main(), not first_run. AC1 says nothing may be written under ANY home before it is answered, and prepare_home is the first write - so it cannot sit inside Launch.prepare at all. run_window also builds its title, its "Open data folder" button and its app.log path from the layout BEFORE the worker starts, so a home swapped on the worker thread would leave three widgets pointing at the default home. main() resolves the home, refuses, asks and checks free space, and only then builds Layout and Launch. Consequence, stated rather than discovered: the list in test_the_first_run_syncs_before_it_applies_the_answers does NOT move - the location is answered before Launch exists, so it is not a step of first_run. AC1 gets its own new test over main(), and its assertion sits INSIDE the fake asker: at the moment ask is called, neither the default home nor the chosen home exists on disk and the fake uv has not run. Asserting afterwards would prove nothing about the order.
2. New Tk-free seam, the shape first_run already uses: choose_home(default_home, ask, report, pointer) -> Path or None, with injected callables; only the asker knows Tk. run_window passes a Tk asker on the root it creates before begin(); run_headless and a no-tty door ask nothing, keep today's location and print one line saying nothing was asked and how to answer later (MYSCRIBE_HOME, --home, the pointer file) - and still run the free-space check. --sync-only, --doctor and --smoke ask nothing, honour the pointer, and check free space.
3. Pointer file (AC4): MyScribe.location beside the default home, one JSON object, home as its key; unknown keys ignored, no test pins that it holds one fact (TASK-089.19 may add a second). One low-level read_pointer(path) returning the object and the problem serves both readers so they cannot disagree. home_dir(platform, environ, pointer) stays pure, never raises and swallows every problem: MYSCRIBE_HOME, then the pointer, then the per-OS default; --home still outranks all three in main(). home_dir keeps returning a Path and not a pair - two existing tests compare it to a Path (:57, :59) - and which source supplied it is a separate helper, home_source(...), which AC10's --setup line and the question's text both use. The pointer path comes from a module-level pointer_path(platform, environ) - a parameter alone would not protect build_parser()'s help-text call to home_dir(), which would read a developer's real file - and an autouse fixture in tests/test_launcher.py points it at tmp_path (spec 3.13). Four tests: no file; a file; an unreadable file (and one that is not a JSON object); and a file naming a folder that is gone, which gives one sentence and asks again, never the default.
4. Refusals (AC9), in this order because resolve() would mask two of them: UNC first (drive starts with two separators; the sentence says a mapped drive letter hides the same problem and is not detected, with ADR-013 and WAL as the reason), then not absolute, then resolve both sides and compare normcase-folded parts against the install directory (a frozen sys.executable's parent) and payload_dir(), then not writable (mkdir plus a probe file, removed). Covered: relative, dot-dot, symlink (resolve follows it), UNC, install and payload containment, unwritable. Not covered and said so: a mapped drive letter, a junction whose target moves later, a case-insensitive network mount. One test per case; a refused answer writes no pointer file and asks again.
5. The total (AC2, AC3), all read as data from the payload, nothing imported from scribe. Weights: parse app/scribe/models.json and sum size per file - real bytes since TASK-089.16, not an estimate. The launcher cannot call accel.mlx_available, so it selects by platform: darwin with an arm64 or aarch64 machine takes the mlx rows, everything else the non-mlx rows, tier turbo, rows without a backends key (the diarization pipeline) always. Drift guard in the one test allowed to hold both, the pattern of test_the_contract_number_is_read_out_of_the_engine_that_ships: copy the real models.json into the payload and assert the launcher's total equals the sum of bytes_total over models.wanted_here per platform. Watch the name: this file already uses platform as a PARAMETER in home_dir and launcher_path, so the stdlib module is imported under another name and the machine string is passed in as an argument rather than looked up inside those functions.
6. New scribe/footprint.json (it ships: scribe is in build_payload APP_PATHS), per platform, every number carrying what it measured, its date and its source. The environment figure stays an ESTIMATE and says so: ADR-011's "about 3.2 GB" download of 2026-09-11 and the 5,321 MiB du of this repo's .venv on 2026-09-20 (dev group included; uv cache and managed Python not measured). macOS and Linux are nobody's measurement and the file says that word for word. The floor is read as TEXT from app/scribe/doctor.py the way setup_contract reads CONTRACT (TASK-089.11's reason transfers: the number lives in the file whose change forces it), with a test that it equals doctor.DISK_FLOOR_GB. Needs = unpacked footprint plus DISK_FLOOR_GB, measured where doctor.disk_probe_path() measures (the data directory, or its parent while it does not exist) so the two can never be about different volumes.
7. Ollama figures: carried as labelled literals, not omitted and not invented. OllamaSetup.exe at 1,569,993,232 bytes is a real measurement in the design spec (v0.34.2, GitHub API, 2026-09-20); the 3.4 GB model is the spec's qwen3.5:4b figure and its source field says so in those words. Shown as "up to X more if you say yes to Ollama", never inside the needs figure. TASK-089.18 criterion 4 replaces both from scribe/ollama_release.json. This diverges from the orchestrator's note to leave them absent; AC2 asks for exactly these labelled literals, and they are sourced in the repo rather than guessed.
8. Free space per volume: Windows by drive letter with ctypes GetDriveTypeW equal to DRIVE_FIXED (ctypes is already used in this file); macOS the root plus /Volumes entries; Linux the root plus HOME, /mnt and /media entries, deduplicated by st_dev. Only the Windows answer can be verified on this machine and the docstring says so. The enumerator is injectable - the question takes the volume list from a function the test replaces - because a test that really enumerates finds two volumes here and one in CI, which is the green-in-CI failure spec 3.13 flagged for the pointer file. shutil.disk_usage is called through the module, so conftest's autouse _plenty_of_disk keeps working.
9. AC6, red first: a test drives the front-end path with shutil.disk_usage low and asserts the fake uv never ran; today the sync starts anyway, so it fails first and that output is kept. After the change: one sentence with both numbers (needed including the floor, and free, naming the volume), no download attempted, and the same check when the question was skipped or never asked.
10. AC5 and AC7: wants_location(...) is a pure predicate like wants_setup - it asks only when there is no pointer, no MYSCRIBE_HOME, no --home and no environment under the default home. A home that already holds an environment is never moved; the question's text and the refusal both say moving an existing install is out of scope: quit, move the folder by hand, then write the pointer. Skipping writes no pointer file. Tests for both.
11. AC2 collateral, "agree with it or are gone", and the answer is gone for the installer: myscribe.iss WelcomeLabel2 (:51) says "about 3 GB" while the estimate carried here is ADR-011's "about 3.2 GB", so they already disagree, and a per-platform number does not belong in a Windows-only welcome text that cannot read json. The parenthetical number is dropped and the sentence stays; a test greps myscribe.iss for a GB figure and finds none. The launcher's own status line (today at :576) is built from footprint.json instead of the literal.
12. AC10: the question's own text and --setup both name the three routes (MYSCRIBE_HOME, --home, the pointer file after moving the folder by hand) and say there is no Settings counterpart, with the reason - the home holds the environment the running app runs from. --setup also prints the home in force and which of the four sources supplied it (spec section 7, row 1), through home_source from item 3. A test on the text.
13. Evidence and fences: red-first output kept for AC6; one pytest process per test file with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE exported (TASK-090); the files to run are test_launcher.py, test_models.py, test_disk_floor.py, test_doctor.py, test_setup.py, test_setup_plan.py. One mutation on a copy under mut/ (the containment check inverted) to show AC9's test bites, then grep -rn MUTANT over scribe and tests is empty. No new dependency. No flag literal containing token, key, secret or password is added, so ADR-015's forbid_pattern does not fire.
14. Cannot be closed by an agent, left unticked with the steps written into the notes: AC8 needs Robert at a Windows screen on a machine with two volumes (choose a folder on the second drive, show env/ and data/ under it with the weights in data/models, the default home empty or absent, the pointer file beside it). AC2's environment figure stays an estimate here: a fresh-home measurement needs a real uv sync --no-dev into a scratch home, and uv is forbidden to this agent; the exact command to replace the estimate goes in the notes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Built 2026-09-22 (agent). Criteria are NOT checked here; the orchestrator verifies.

### What changed

- `packaging/launcher/myscribe_launcher.py`
  - `default_home()` (today's `home_dir` body), `pointer_path()`, `read_pointer()`,
    `write_pointer()`, `home_dir(platform, environ, pointer)` and `home_source()`.
    `home_dir` still returns a plain `Path`, never raises, and orders
    MYSCRIBE_HOME, the pointer, the per-OS default. `--home` outranks all three in `main`.
  - A cost section read as data out of the payload: `footprint()`, `weights_bytes()`,
    `disk_floor_gb()`, `install_size()`, `download_note()`, `disk_probe_path()`,
    `free_gb()`, `local_volumes()`, `enough_disk()`.
  - The question: `wants_location()`, `install_dir()`, `_inside()`, `refuse_location()`,
    `location_question()`, `location_note()`, `locate_home()`, and the Tk `ask_location()`.
  - `Launch.prepare` refuses the sync below the needed space and builds its status line
    from `footprint.json` instead of the "3 GB" literal. `main` settles the location
    before it builds a `Layout`, prints the home and its source under `--setup`, and
    checks the space in the `--sync-only/--doctor/--smoke` branch too.
- `scribe/footprint.json` (new). Windows: 3.2 GB download (ADR-011, 2026-09-11) and
  5.1 GB unpacked (this repository's .venv, 5,459,898,687 bytes over 47,416 files,
  measured 2026-09-22). macOS and Linux are `null` with "nobody has measured this
  platform" in the file, word for word - a Mac installs neither CUDA torch nor the
  CTranslate2 build, so the Windows figure is not an estimate of it. Ollama:
  OllamaSetup.exe 1,569,993,232 and Ollama.dmg 197,873,582 bytes (Ollama's own release
  v0.34.2 through the GitHub API, 2026-09-20) and qwen3.5:4b at 3,389,983,735 bytes
  (this machine's Ollama, same day). Every number carries what it measured, its date
  and its source.
- `packaging/windows/myscribe.iss`: the "(about 3 GB)" is gone, and the welcome text
  now says the first start asks where everything goes. A test greps the line for a GB
  figure.
- `tests/test_launcher.py`: +34 tests, an autouse fixture that points `pointer_path`
  at `tmp_path`, and a paragraph in
  `test_the_first_run_syncs_before_it_applies_the_answers` saying why the location is
  not one of its steps. Its order list is unchanged.
- `docs/superpowers/specs/2026-09-20-installer-design.md`: the two questions section 2
  left to this task are settled in place (the floor is read as text; the pointer's path
  is a module-level function as well as a parameter).

### The question, on this machine (read-only, nothing installed)

`.venv/Scripts/python scratchpad/.../show_question.py` copied footprint.json,
models.json and doctor.py into a scratch payload and printed what a first run would
show here. Full output: `real-question-on-this-machine.txt`.

    default home      C:\Users\rvdbr\AppData\Local\MyScribe
    pointer file      C:\Users\rvdbr\AppData\Local\MyScribe.location (exists: False)
    local volumes     C:\, D:\        <- GetLogicalDrives + GetDriveTypeW, both DRIVE_FIXED
    downloads         about 3.2 GB of speech engine (an estimate...) and 1.5 GB of
                      speech models for this machine (measured, from the catalogue)
    needs             about 17 GB free - the install unpacked plus the 10 GB floor
    up to             4.6 GB more for Ollama and qwen3.5:4b
    free now          C:\ 118.7 GB, D:\ 28.8 GB

### Evidence

All runs: one test file per process, SCRIBE_DATA_DIR and SCRIBE_ENV_FILE fenced at
`scratchpad/build/TASK-089.14/fence` (TASK-090). Files in that directory.

- RED first, `red-ac1-ac6.txt`: 2 failed, 54 deselected.
  `test_too_little_room_refuses_the_sync_with_both_numbers` - `assert True is False`:
  today `prepare()` starts the sync on a volume that cannot hold the install.
  `test_the_location_is_asked_before_anything_is_written_or_downloaded` -
  `assert 0 == 1`: today nothing asks.
- GREEN `green-test_launcher.txt`: 89 passed, 1 skipped (the macOS quarantine test,
  skipped here since it was written). `green-test_proxy.txt`: 28 passed - the only
  other file that loads the launcher. `green-test_doctor.txt`: 56 passed,
  `green-test_models.txt`: 49 passed, `green-test_disk_floor.txt`: 9 passed - the
  files about the two numbers this reads.
- MUTATION `red-mutant-containment.txt`, on a copy under `mut/` and never the
  repository: `_inside` reduced from "is or is under" to "is". 5 failed, 3 passed -
  the path that climbs back in, the link, the install directory, the payload and the
  door that asks again all bite. `grep -rn MUTANT scribe tests packaging` in the
  repository finds nothing.
- ADR tripwires: `grep -nE '^\s*(import|from)\s+(scribe|torch|...)' packaging/launcher/**`
  is empty. The only `--...token` literal in the launcher is the pre-existing line in
  `setup_command`, which this change does not touch (`git diff | grep -c hf.token` = 0);
  ADR-015's pattern needed no working around.

### Decisions worth a reviewer's eye

1. **The question lives in `main`, not in `first_run`.** AC1 says nothing may be
   written under any home before it is answered, and `prepare_home` is the first write.
   `run_window` also builds its title, its "Open data folder" button and its app.log
   path from the layout before the worker starts, so a home settled on that thread
   would leave three widgets on the default home. Consequence, stated rather than
   discovered: the order list in `test_the_first_run_syncs_before_it_applies_the_answers`
   does NOT move - the location is answered before `Launch` exists, so it is not a
   step of `first_run`. That test's docstring now says so.
2. **The free-space check sits at the two `needs_sync` gates** (`Launch.prepare` and
   `main`'s quiet branch), not at the door. Every door with a window, the console, a
   start at login and `--sync-only/--doctor/--smoke` therefore pass through it, and
   `--doctor` still runs on a full disk - refusing the tool that diagnoses a full disk
   would be a poor joke.
3. **It measures where the doctor measures**, but walks further up: the doctor's
   `disk_probe_path` goes one level, and before a first run neither `D:\MyScribe\data`
   nor `D:\MyScribe` exists, so one level still raises. Tested on a home two levels deep.
4. **The floor is read as text** out of the shipped `doctor.py`
   (`^DISK_FLOOR_GB = (\d+)$`), not copied into footprint.json: one number, in the file
   whose change should move it. A test holds it against `doctor.DISK_FLOOR_GB`.
5. **The Ollama figures are labelled literals**, which diverges from the orchestrator's
   note to leave them absent. AC2 asks for exactly that until TASK-089.18's pin file
   lands, and both numbers are sourced in this repository rather than guessed. If that
   is wrong, the fix is one key in footprint.json, not a redesign.
6. **`weights_bytes` picks its rows by platform**, because the launcher cannot call
   `accel.mlx_available()`: darwin on arm64/aarch64 takes the mlx rows, everything else
   the rest, tier turbo, rows without `backends` always. The drift test copies the real
   `models.json` into the payload and compares per platform against
   `models.wanted_here`, and asserts the two platforms differ, so a selector that
   returned everything would not pass.

### Covered, and not covered, in the refusals (AC9)

Covered, one test each: a relative path; `..` that climbs back into the install
directory; a link into it (a symlink where the OS allows one, and on this machine an
NTFS junction, because a plain symlink wants a privilege Windows does not hand out -
WinError 1314 here on 2026-09-22 - and `resolve()` follows both); a UNC path, refused
before any filesystem call so nothing on the network is touched; the install directory;
the payload; a folder that cannot be written; and a folder whose name merely starts the
same (`D:\MyScribeData` is not inside `D:\MyScribe`), which is why the comparison is
part by part after `normcase` and never a string prefix.

NOT covered, deliberately: a mapped drive letter, which is a share with a letter in
front of it - the question's text and the UNC refusal both say MyScribe cannot detect
it; a junction whose target is moved after the check; a case-insensitive network mount.

### What is left

- **AC8 stays unticked. It needs Robert at a Windows screen on a machine with two
  volumes.** No agent can produce the recorded first run, and the hard rules forbid
  creating a folder on a real second volume. The steps:
  1. Have no pointer file and no environment: `%LOCALAPPDATA%\MyScribe.location` absent
     and `%LOCALAPPDATA%\MyScribe\env` absent (rename them aside rather than delete).
  2. Start the release launcher (the frozen one, or
     `.venv/Scripts/python packaging/launcher/myscribe_launcher.py --payload <build>`).
  3. Expect the question above, with C: and D: and their real free space.
  4. Choose a folder on the second drive, e.g. `D:\MyScribe`, and let it run.
  5. Expect afterwards: `D:\MyScribe\env` and `D:\MyScribe\data` exist, the weights in
     `D:\MyScribe\data\models`, `%LOCALAPPDATA%\MyScribe` absent or empty, and
     `%LOCALAPPDATA%\MyScribe.location` holding `{"home": "D:\\MyScribe"}`.
  Note from this machine on 2026-09-22: D: had 28.8 GB free and the install needs about
  17 GB, so it fits - but only just, and a `--sync-only` there would be the cheapest
  version of the test.
- **The environment figure is still an estimate**, and the file says so. A real one
  needs a release-shaped install into a fresh home:
  `uv sync --no-dev --frozen --project <payload>/app` with `UV_PROJECT_ENVIRONMENT`,
  `UV_PYTHON_INSTALL_DIR` and `UV_CACHE_DIR` under a scratch home, then measure that
  home. `uv` is forbidden to this agent by the hard rules (the uv on PATH is 0.5.9 and
  rewrites uv.lock), and the run is several GB.
- **`scribe/footprint.json` is untracked until the orchestrator commits it.**
  `build_payload.APP_PATHS` carries `scribe` through `tracked_files()`, so a payload
  built before that commit ships without it. The tests are unaffected (they write the
  file into their own payload); a build is not.
- **The Tk dialog itself is unverified.** `ask_location` has no test - Tk needs a
  screen - exactly as `ask_setup` had none until somebody looked at it. AC8's run is
  what proves it.
- **`local_volumes` is verified on Windows only** (C: and D: found here today). The
  macOS and Linux branches are the platforms' convention and nobody's measurement,
  which the docstring says in the manner `launcher_path` already uses.
- `graphify update .` was not run: the hard rules for this task forbid touching
  `graphify-out/`.

### One boundary a reviewer should know about

`--home` and `MYSCRIBE_HOME` are not put through `refuse_location`. They outrank the
pointer and the question by design (section 2 of the spec, AC #4), they are typed by
somebody who means it, and the whole test suite and CI point them at temporary folders.
AC #9 is about the folder the question is *given*, and that is what is refused. If a
`--home` inside the install directory should also be refused, that is a change of one
call in `main` plus a test - say so and it is done.

### Three checks the first pass had not made

- **CI now goes through the free-space check.** `.github/workflows/release.yml:128` runs
  `packaging/build_release.py --smoke`, which at `:228` starts the frozen launcher as
  `<binary> --home <repo>/build/smoke-home-<platform> --smoke`. `--home` skips the
  question (nothing is asked on a runner), but `--smoke` goes through `main`'s quiet
  branch, so the sync there is now refused when that volume has less free than the
  install needs: **16.6 GB on Windows** (5.1 environment + 1.54 weights + 10 floor) and
  **11.5 GB on macOS and Linux**, where the environment figure is `null` and is left
  out of the total. AC #6 asks for the check in that branch, so this is the intended
  behaviour and not something to weaken - but if a hosted runner is tighter than that,
  the job will fail with "There is not enough room" and it will read as a disk problem
  rather than as this commit. Nobody has measured a runner's free space here.
- `tests/test_web_transcribe_dialog.py` (the one test file that walks the `scribe`
  directory) passes with the new `scribe/footprint.json`: 54 passed.
- ADR-001, ADR-013 and ADR-014 carry Enforcement patterns over `scribe/**` about model
  imports, `threading.RLock()` and reading `app.log`. The only change under `scribe/` is
  a JSON data file, so all three are clean; ADR-011's and ADR-015's patterns over
  `packaging/launcher/**` were checked with grep and are clean too.

Two things a review by a stronger model turned up, both fixed and covered:
`location_question` now always shows the disk the default home is on, whatever the
volume enumerator found (its docstring had promised that and the code did not); and
`ask_location` destroys its Tk root after `mainloop()`, because closing that window with
its X ends the loop without destroying the interpreter and `run_window` creates a second
`Tk()` in the same process moments later.

## Review round 2026-09-22 (fixer). Criteria are still NOT checked here.

Three verifiers read the first pass. Seven code changes and six test changes came out of
it; four findings were rejected or deliberately left. Every behavioural fix was red
first, and every new test was re-checked with a mutation that it now kills.

### Two corrections to the notes above

- The Evidence line said "89 passed, 1 skipped" for `tests/test_launcher.py`; the file
  said 90. After this round it is **97 passed, 1 skipped** (`final-test_launcher.txt`).
- The CI paragraph's figures were wrong for the state the commit is made from, and the
  numbers are now measured rather than reasoned (`ci-numbers.txt`, the launcher's own
  `install_size` against the shipped `models.json` and `doctor.py`):
  **while `scribe/footprint.json` is untracked** - and `build_payload.APP_PATHS` goes
  through `tracked_files()`, so a payload built before the commit does not carry it -
  `needed_gb` is 11.5 GB on **all three** platforms. **Once it is tracked**, Windows
  moves to 16.6 GB and macOS and Linux stay at 11.5, because their environment figure
  is null. The gate's threshold therefore moves with the same commit that adds the
  file. Nobody has measured a hosted runner's free space; `release.yml` runs
  `build_release.py --smoke`, which passes through the gate. Two ways to settle it,
  both the orchestrator's: add a `df -h` / `Get-PSDrive` step to `release.yml` in this
  commit, or accept the risk knowingly. If a runner is tight the job says "There is not
  enough room", which reads as a disk problem and not as this commit.

### Fixed, with the red kept (`fix-red.txt`: 6 failed, 4 passed)

1. **`write_pointer` could kill the launcher** (blocker). It is reached exactly on the
   failure AC4 enumerates - a pointer that cannot be read is usually one that cannot be
   written - and `write_text` had nothing around it. Behind `--windowed` that is a death
   with no console and no window, moments after the person answered. It now returns a
   sentence, `locate_home` reports it and carries on with the folder they chose.
   Red: `PermissionError` out of `pathlib.py:1013`.
2. **The free-space gate ran after `prepare_home` and `install_tools`**, which copy uv,
   ffmpeg and ffprobe into the home. On a volume that is genuinely out of room the
   friendly refusal lost to an ENOSPC. Moved above both, in `Launch.prepare` and in
   `main`'s quiet branch. Neither `needs_sync` nor the probe needs the home to exist.
   Consequence, stated: the refusal now names the nearest folder that exists (the home
   does not yet), which is the better sentence; the test asserts `disk_probe_path`.
3. **A start at login over an unplugged drive said nothing at all.** `console` is
   `print`, and behind PyInstaller's `--windowed` `sys.stdout` is None and `print`
   returns silently. New `show_error()` - Tk imported inside the function, so
   `test_the_sequence_is_free_of_tk` stays true - shown by `main` when the windowed
   door has to stop.
4. **`wants_location` asked the filesystem, not the file.** A pointer that is valid JSON
   with no `home` key suppressed the question forever - the trap AC4 wrote itself
   against, since TASK-089.19 may write its own fact there first. It now asks
   `read_pointer` whether a home is named.
5. **`refuse_location` created the folder it was testing**, with `parents=True`, while
   the question was still open: a typed path left an empty tree behind on a disk nobody
   meant to touch. It now probes the nearest folder that already exists and creates
   nothing. `exists()` and not `is_dir()`, so a file where a folder should be still
   fails the probe.
6. **`install_size` did arithmetic on unvalidated scalars.** `_mapping` made the objects
   safe and the numbers inside them went straight into `+`, so a hand-edited `"3.2"`
   was a TypeError against `footprint()`'s "never raises". New `_number()`.
7. **"Use this folder" over an empty box meant "keep the default".** It now returns the
   empty string, so `refuse_location`'s "Nothing was typed" branch is reachable from the
   one door that has a window. Not tested - the Tk dialog needs a screen (AC8).

### Tests only, each proven by a mutation that survived before and dies now

`mut2-*.txt`, one pytest process per mutation, fenced, from a fresh copy of the working
tree at `.../TASK-089.14/mut2/`. Baseline 97 passed, 1 skipped; each mutation 1 failed.

- `test_no_pointer_is_written_while_the_question_is_still_open` - AC9's own sentence was
  unenforced: the accepted answer overwrote the refused one, so the end state said
  nothing. The asker now looks between the two answers. (mutation 8)
- `test_a_key_the_launcher_did_not_write_survives_the_answer` - the write side of
  "unknown keys are kept" had no test. (mutation 1)
- `test_every_source_of_the_home_can_be_named` - one line with `--home` and
  `MYSCRIBE_HOME` both set; the order of the two branches was free. (mutation 6)
- `test_a_volume_that_cannot_be_measured_does_not_refuse` - a rule `enough_disk` states
  and nothing held. (mutation 7)
- `test_a_pointer_that_cannot_be_read_says_so_and_asks_again` overclaimed: it asked
  nothing, it called `read_pointer`. Replaced by
  `test_a_pointer_that_cannot_be_read_asks_again_and_a_failed_save_says_so`, which
  drives the door end to end over a pointer path that is a directory - the same test
  that covers fix 1.
- The AC1 test now also sets `LOCALAPPDATA` under `tmp_path`. See the leak below: at red
  time `default_home` and `pointer_path` did not exist yet, so both patches were
  no-ops. `LOCALAPPDATA` needs no seam. Windows-only - macOS's default home has no such
  hook - and Windows is where this test runs `main()`.

Mutations 9 to 13 cover this round's own fixes (the save raises again, `wants_location`
back on `exists()`, the probe creating its tree, the gate back below `install_tools`, the
window door silent). All five die.

### Rejected, or left deliberately

- **The 10 GB floor inside the refusal** (two verifiers suggested splitting refuse from
  warn). Not changed: implementation plan item 6 is "Needs = unpacked footprint plus
  DISK_FLOOR_GB", so the threshold is the plan of record and moving it is a design
  change the orchestrator owns. Neither verifier's primary fix was the split either, and
  a hosted runner cannot be measured from here - a guess would replace a documented
  choice. Reported above instead.
- **macOS and Linux understate `needed_gb`** because their environment figure is null.
  Left: inventing a number would be worse than the honest null. The fix is a real
  measurement per platform; the command is in the notes above.
- **`README.md:92` and `:119` still say "about 3 GB".** Outside this task's criteria (AC2
  names the launcher and `myscribe.iss`), and the hard rules say report, not fix. They
  now disagree with the only dated estimate there is. Orchestrator's call: fold in here,
  or a follow-up.
- **The leftover `%LOCALAPPDATA%\MyScribe`** (`.env`, `bin/.tools.json`,
  `env/.myscribe-sync.json`, all stamped 2026-09-22 16:28) written by the first AC1 red
  run. Not removed here: deleting outside the scratch directory is not this agent's to
  do. Verified untouched by this round's runs (mtime still 16:28) and it holds no
  credential (the `.env` is 11 bytes, `HF_TOKEN=` with no value). AC8's step 1 renames
  the default home aside anyway - it now has something to rename.

### Evidence

In `...\scratchpad\build\TASK-089.14\`:
- `fix-red.txt` - 6 failed, 4 passed, 88 deselected
- `final-test_launcher.txt` - 97 passed, 1 skipped; `final-test_proxy.txt` - 28 passed;
  `final-test_disk_floor.txt` - 9 passed
- `mut2-baseline.txt` plus `mut2-1` to `mut2-13` - baseline green, every mutation 1 failed
- `ci-numbers.txt` - the two CI thresholds, measured

`grep -rn MUTANT scribe tests packaging` is empty; so is the ADR-011 import grep over the
launcher, and no `--flag` literal naming a token, key, secret or password was added
(ADR-015's forbid_pattern does not fire).

### Addendum: the window that shows the sentence uses plain Tk, not `messagebox`

`show_error` first used `tkinter.messagebox`. That is a tkinter submodule no other
launcher path imports, the freeze has no hidden-imports list (`build_release.freeze`
is PyInstaller flags only, no `.spec`), and the one door it serves is `--windowed`,
where a failed import would be as silent as the `print` it replaces. It now builds a
Label and a Close button on a `tkinter.Tk` of its own - the same widgets `ask_location`
and `run_window` already prove survive the freeze. Untested, like every Tk path here:
AC8's run is where it is first seen. `final-test_launcher.txt` re-run after the change:
97 passed, 1 skipped.

`grep -rn write_pointer packaging/ scribe/ tests/` finds the definition and the single
call site in `locate_home`; the return type change from `None` to `str` reaches nothing
else.

## Verification (orchestrator, 2026-09-22)

### The CI question the build left open, decided and built

The new gate runs for `--smoke` too, so it gates the release build. I measured
what it asks for rather than taking the report's figures, by calling the
launcher's own `install_size` against the shipped `models.json` and
`doctor.py`:

    disk floor read from doctor.py: 10 GB
    win32   needed 16.6 GB  (weights 1.5, environment 5.1, floor 10)  complete: True
    darwin  needed 11.5 GB  (weights 1.5, environment unmeasured, floor 10)  complete: False
    linux   needed 11.5 GB  (weights 1.5, environment unmeasured, floor 10)  complete: False

`release.yml` clears disk for **Linux only** - its own comment says a runner
"arrives with about 5 GB free" - and there is no such step for Windows or
macOS. So a hosted Windows runner would have been asked for 16.6 GB it very
likely does not have, and a release that used to build would start failing.

Put to Robert with the three options and their consequences. His answer on
2026-09-22: **drop the floor for `--smoke`.** The 10 GB is
`doctor.DISK_FLOOR_GB`, the room MyScribe keeps free to *run* with a library
of recordings; `--smoke`, `--sync-only` and `--doctor` install and then stop
and keep no library, so it is not theirs to demand. A person installing this
is still asked for all of it.

Built as `enough_disk(layout, report, *, floor=True)`, with the quiet branch
passing `floor=False`. The cost is written into the docstring rather than left
implicit: a future smoke that does need the working room is no longer covered
here.

`tests/test_launcher.py::test_the_working_room_is_asked_of_a_person_and_not_of_ci`
asserts both directions in one test on purpose, at a free-space figure between
the two, so a later change that drops the floor for everybody passes the CI
half and fails the person's half. Proven to bite on a copy outside the
repository - the subtraction removed gives `assert False is True` - and
`grep -rn MUTANT scribe tests packaging` is clean afterwards.

### The stray home in the real %LOCALAPPDATA%

Reported by the build against itself, and confirmed here. Its first red run
for criterion 1 wrote into Robert's own profile:

    C:\Users\rvdbr\AppData\Local\MyScribe   created 2026-09-22 16:28, 3.0 KB
      .env (11 bytes: "HF_TOKEN=" with no value), bin/, data/, env/, logs/  - all empty

No credential and nothing else of his was touched, but it is a fence escape
and it is outside this agent's scratch directory, so it was left for Robert to
remove rather than deleted from under him. **It must go before criterion 8's
run**: `wants_location` asks only when no environment exists under the default
home, so a leftover home changes what that run measures.

### Whole suite

One file per process, fenced, after the `floor=False` change: **2989 passed, 10 skipped, 0 failed, 0 errors** over 81 files, reconciling exactly with "2999/3009 tests collected (10 deselected)" since 2989 + 10 = 2999. That is +45 on the run after TASK-089.16 (2954 collected), all of them in `tests/test_launcher.py`, which goes from 54 collected to 99.

### Criterion 8 stays open

A recorded first run on Windows on a machine with two volumes, choosing a
folder on the second drive and showing `env/` and `data/` under it with the
weights in `data/models`, the default home empty or absent and
`MyScribe.location` beside it. Robert's, and the steps are in the build's
notes above.

### Carried, not fixed here

* The environment figure for macOS and Linux is `null` in
  `scribe/footprint.json` and the question says so rather than showing a
  number nobody measured. Both therefore understate `needed_gb`. The honest
  null is deliberate; a real figure needs `uv sync --no-dev` into a fresh home
  on each platform.
* `README.md:92` and `:119` said "about 3 GB". Folded into this commit after
  all: the installer's welcome text lost the same literal for the same stated
  reason - a number no file feeds cannot be kept true - and leaving two of
  them in the README would have been the half-done sweep. Both now point at
  `scribe/footprint.json` and `scribe/models.json`, which carry what each
  figure measured and when. `grep -n "about 3 GB" README.md` finds nothing.
* Two Tk paths - `ask_location`'s empty box and `show_error`'s window - are
  untested because Tk needs a screen. Criterion 8's run is the first sighting
  of both.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A release install now asks where everything goes before anything is written under any home or downloaded. The question is the launcher's own and not the engine's, because env/, python/, cache/, data/ and the weights all hang off the home and it has to be settled before the environment that would answer it exists; it is asked in main(), before a Layout is built, so nothing on a worker thread can swap a home three widgets were already drawn from. It shows the default home, the free space per volume, what this install will download and the disk it needs - the weights as real bytes out of models.json (TASK-089.16's pins), the environment and Ollama out of a new scribe/footprint.json where every figure carries what it measured, its date and its source, and a null where nobody has measured rather than a number somebody guessed. The answer is kept in MyScribe.location beside the default home, honoured after MYSCRIBE_HOME and --home; a pointer that is unreadable, not an object, or names a folder that is gone gives one sentence and the question again, never a silent fall-back to a default that would look like an empty library. A folder inside the install or payload directory is refused (ADR-011's Must Not, and on Windows that folder belongs to the installer), as is a UNC path, a relative path and one that cannot be written; the notes say which cases are covered and which are not. A home that already holds an environment is never asked about, so nothing moves an existing install. Free space is checked before prepare_home and install_tools, not after. The fix round found three shipping defects: write_pointer with no error handling (an uncaught PermissionError in a --windowed build), the space gate running after the tools were already unpacked, and a failure printed to a stdout that does not exist behind --windowed - a start at login over an unplugged drive exited 1 in complete silence. One decision was put to Robert with measured numbers: the gate also runs for --smoke, and Windows needs 16.6 GB of which 10 is the room the app keeps free to run with a library, while release.yml clears disk for Linux only and a hosted Windows runner has less. He chose to drop that floor for --smoke, --sync-only and --doctor, which install and then stop; a person keeps the full gate. One test asserts both directions at a free-space figure between them, so a change that drops the floor for everybody fails the person's half; proven to bite on a copy outside the repository. Whole suite over 81 files: 2989 passed, 10 skipped, 0 failed, reconciling with 2999 collected; tests/test_launcher.py goes from 54 to 99. Criterion 8 stays unticked - a recorded first run on a two-volume Windows machine is Robert's. Reported against the build itself: its first red run wrote an empty home into the real %LOCALAPPDATA%; it holds no credential, it was left for Robert rather than deleted from under him, and it must go before criterion 8's run.
<!-- SECTION:FINAL_SUMMARY:END -->
