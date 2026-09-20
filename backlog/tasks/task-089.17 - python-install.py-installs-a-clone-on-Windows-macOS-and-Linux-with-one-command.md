---
id: TASK-089.17
title: >-
  python install.py installs a clone on Windows, macOS and Linux with one
  command
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - packaging
  - ux
dependencies:
  - TASK-089.11
  - TASK-089.13
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 154000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
This is requirement 6. A clone user is never asked anything today. scribe.setup takes flags only and has no input() or getpass (scribe/setup.py:158-168); by a reader's grep its only caller outside the tests is the frozen launcher (packaging/launcher/myscribe_launcher.py:474). The README's 'From a clone' is a bare `uv sync` with whatever uv is on PATH (README.md:121-125), while the pin is 0.12.13 (packaging/tools.json). Robert's own note records that the uv on his PATH is 0.5.9 and that it rewrites uv.lock.

The start scripts are fossils from before ADR-012. A fresh clone that runs scripts/start.ps1 first is told to `pip install -r requirements.txt` (scripts/start.ps1:48-56; scripts/start.sh:35-40; scripts/mac-acceptance.sh:42-47). No requirements file is tracked any more, and an improvised pip install on Windows ends in a CPU-only torch.

install.py runs before the environment exists, so it has the launcher's physics: stdlib only, Python 3.9 syntax. Whether it shares the launcher's code or only its sequence is ADR-015's decision (TASK-089.02, brief: M11); the criteria below hold either way.

Open question (brief: W2). The plan said install.py 'refuses to sync while MyScribe answers /health from this checkout'. That cannot be determined today. The launcher's single-instance rule is running_instance(): it asks one port for /health and reads `ok` (myscribe_launcher.py:306-312), and port_taken() tries to bind (:315-321). Both are keyed on a port alone. /health returns only ok and the version (scribe/app.py:296-298). The app writes no pid or run file, and it takes any --port (scribe/__main__.py:33-35) - CLAUDE.md itself documents a second copy on 4299. So the launcher's rule answers 'does a MyScribe answer on this port', which is enough for a release with one home, and not enough for a clone.

Two candidates, neither tested. A field on /health that names the environment the app runs from (changes scribe/app.py) removes the false alarm - a release on 4242 would no longer block a clone's sync - but still only sees the ports it is told to ask. A run file written at start would supply the port, but the launcher stops the app on Windows with `taskkill /T /F` (:376-383), so the lifespan's cleanup never runs and the file is stale by design. It also has to be weighed against two Accepted records. ADR-013's Must Not forbids 'a lock file or a pid file for job state or for "one runner at a time"' (docs/adr/ADR-013-...md:204-205), and ADR-001 counts 'No lock files' among what it wanted (:143). A run file that says which environment serves is not job state, so the letter of that prohibition does not cover it. Its reason does: a file that outlives the process it describes is the staleness those records chose SQLite to avoid. It is listed so that it is argued and not ignored, and it is the weaker candidate. The hazard itself is also an inference: nobody has shown what `uv sync` does to a .venv whose interpreter is running. When the lock stamp matches, no sync happens and none of this matters.

Declined by Robert on 2026-09-20, and not built here: a start script or shortcut for a clone.

Needs a real machine: Robert's machine for Windows and WSL. A real Mac for the macOS run: Robert, if the Mac of TASK-040.07 (a session on 2026-09-19) is still his to use - not confirmed; otherwise reported as not run. Nobody has run anything on macOS.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `python install.py` in a fresh clone on Windows and in WSL ends with the proof report and the start command. Full transcripts are shown, and `git status --short` is clean afterwards, so uv.lock was not touched. The same run on macOS needs a real Mac. Robert answers it if the Mac that TASK-040.07 records a session on (2026-09-19) is still his to use; that was not confirmed when these tasks were written, and no Mac was available in the design run. If it is not run, the box stays unticked and the parent's final summary lists it.
- [ ] #2 It refuses Python older than 3.9, and any platform or architecture outside the three [tool.uv] environments (pyproject.toml:68-72), in its first second, with one sentence.
- [ ] #3 Only the pinned uv from packaging/tools.json is used. It is sha256-verified and placed in .tools/bin. The output on Robert's machine names both versions. The sync always runs `--frozen` with UV_NO_CONFIG=1.
- [ ] #4 On Windows and Linux the pinned ffmpeg and ffprobe go into .tools/bin when none answers on PATH. On macOS there is no pinned download - the release builds its own (release.yml:104) - so one line gives the command `brew install ffmpeg`. MyScribe does not run brew: ADR-015's rule allows only a pinned, verified artifact.
- [ ] #5 It does not set UV_CACHE_DIR, UV_PYTHON_INSTALL_DIR or PYTHONPYCACHEPREFIX, and does not export SCRIBE_DATA_DIR unless --data-dir was given. A test shows that a `.env` which moves the data directory is honoured by setup, the proof and the start alike.
- [ ] #6 The clone keeps the dev group by default, and --no-dev is available. The release keeps --no-dev and --python-preference only-managed, so tests/test_launcher.py:125 stays green.
- [ ] #7 Before the sync it checks free space on the volume the environment lands on. When it is too small, one sentence gives both numbers and nothing is downloaded (brief: M3).
- [ ] #8 The W2 question is settled and written into the notes before any refusal is built. The hazard is reproduced first, on a COPY: what does `uv sync` do to a .venv whose interpreter is running on Windows? The output is shown.
- [ ] #9 Whatever mechanism is chosen, three behaviours are tested. A MyScribe running from another environment on the same port does not block the sync. One running from this environment does, with 'stop it first, or use --no-sync'. And what install.py cannot see is said in one sentence, not implied. If the mechanism adds a field to /health, scribe/app.py is in the change, and there is a fourth behaviour: a MyScribe that answers without the field - an older one - is doubt, and doubt refuses the sync the same way.
- [ ] #10 build_payload.fetch writes its cache atomically and deletes a cached file whose sum is wrong; today a bad cached file fails every later run (packaging/build_payload.py:49-58). A network failure is reported as one sentence, not a traceback.
- [ ] #11 It hands the terminal to `.venv python -m scribe.setup`, then runs the proof. When there is no TTY, one line gives the winpty or PowerShell hint and the run continues unattended.
- [ ] #12 The flags --non-interactive, --answers FILE, --no-dev, --no-sync, --data-dir, --check, --start and --port all work; --port is the one the /health probes ask besides the default. Re-running is idempotent and asks only what is open and was not skipped (TASK-089.11): the start scripts send people back to `python install.py` after every pull (criterion 13), and that must not nag. A skipped question is reached again with a bare `python -m scribe.setup`, which is the clone's `--setup`. `--check` is the report of TASK-089.13 and nothing else, and its help text says what it touches.
- [ ] #13 The lock-sha stamp is written into .venv. scripts/start.sh and scripts/start.ps1 compare it and say 'run python install.py'. The pip and requirements hints are gone there and in scripts/mac-acceptance.sh.
- [ ] #14 tests/test_install.py holds a stdlib allow-list check by AST (sys.stdlib_module_names) over install.py, packaging/build_payload.py and the launcher. All three parse with feature_version=(3,9). Fake-uv tests cover the order of steps.
- [ ] #15 The README's 'From a clone' section (README.md:113-125) is the two commands. install.py is not in build_payload.APP_PATHS.
- [ ] #16 It creates no shortcut, no desktop entry and no new start script; the existing scripts/start.* are corrected, not replaced. Where the library lives stays a flag, `--data-dir`, with one line saying that `git clean -fdx` deletes a library inside the clone.
<!-- AC:END -->
