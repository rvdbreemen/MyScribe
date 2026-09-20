---
id: TASK-089.17
title: >-
  python install.py installs a clone on Windows, macOS and Linux with one
  command
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-20 21:48'
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

install.py runs before the environment exists, so it has the launcher's physics: stdlib only, Python 3.9 syntax. It shares the launcher's sequence and not its code: decided by Robert on 2026-09-20 (brief: G2, which settles M11; ADR-015). Counted in the grill: about 100 of the launcher's lines are plumbing both doors need - the lock digest, the stamp, the sync decision, running a child, the setup call; the home, the environment and the tools differ per door. The launcher stays as it shipped in v0.5.0 and v0.5.1, with tests/test_launcher.py pinning it, and gains no CloneLayout and no branch only a clone reaches. Fetching the tools and checking their sha256 is not in the launcher at all - it copies them out of the payload - so install.py shares that part with packaging/build_payload.py. Against drift in the duplicated plumbing there is one contract test (criterion 17).

Decided by Robert on 2026-09-20 (brief: G5, which settles W2): `/health` says what it serves. First, why it needed deciding. The plan said install.py 'refuses to sync while MyScribe answers /health from this checkout'. That cannot be determined today. The launcher's single-instance rule is running_instance(): it asks one port for /health and reads `ok` (myscribe_launcher.py:306-312), and port_taken() tries to bind (:315-321). Both are keyed on a port alone. /health returns only ok and the version (scribe/app.py:296-298). The app writes no pid or run file, and it takes any --port (scribe/__main__.py:33-35) - CLAUDE.md itself documents a second copy on 4299. So the launcher's rule answers 'does a MyScribe answer on this port', which is enough for a release with one home, and not enough for a clone.

The mechanism: `/health` gains two fields, the source tree the app runs from and the data directory it serves (the design spec, section 3.9, proposes `app_dir` and `data_dir`). install.py asks the port it was given - 4242, or `--port` - and refuses to sync when the source tree is this checkout, goes on when it is another, goes on when nothing answers, and treats an answer without the two fields - an older MyScribe - as doubt, which refuses. That moves scribe/app.py:296-298 and the test that pins the body exactly (tests/test_app.py:45-48); the launcher reads only `ok` (myscribe_launcher.py:306-312) and notices nothing. It gives nothing away: the host check covers the whole app (scribe/guard.py:110), so a web page cannot read the answer, and a local process that can read it can read the file system as well. The blind spot is kept and named: an app on a port nobody mentioned is not seen, and this repository's own `--port 4299` is such a case. Weighed and set aside: a row in the database that the running app writes, which would see an app on any port and fits ADR-013, but needs a stale row handled after a crash and opens the live database from an installer; and a run file, which is the staleness ADR-013 and ADR-001 chose SQLite to avoid (docs/adr/ADR-013-...md:204-205; ADR-001 :143), and which `taskkill /T /F` (:376-383) leaves behind by design. A warning in place of the refusal was set aside because what a sync does to locked files on Windows has been measured by nobody: the hazard is still an inference, and criterion 8 measures it. When the lock stamp matches, no sync happens and none of this matters.

Declined by Robert on 2026-09-20, and not built here: a start script or shortcut for a clone.

Needs a real machine: Robert's machine for Windows and WSL. A real Mac for the macOS run: the Mac is somebody else's (brief: G9, decided by Robert on 2026-09-20); its points are bundled for the Mac's owner in TASK-089 criterion 10, and until that sitting reported as not run. Nobody has run anything on macOS.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `python install.py` in a fresh clone on Windows and in WSL ends with the proof report and the start command. Full transcripts are shown, and `git status --short` is clean afterwards, so uv.lock was not touched. The same run on macOS needs a real Mac. The Mac is somebody else's, decided by Robert on 2026-09-20 (brief: G9), so this point is not asked on its own: it goes into the bundled macOS list of TASK-089 criterion 10 with its command and its expected output, and reads 'not run' until that sitting. If it is not run, the box stays unticked and the parent's final summary lists it.
- [ ] #2 It refuses Python older than 3.9, and any platform or architecture outside the three [tool.uv] environments (pyproject.toml:68-72), in its first second, with one sentence.
- [ ] #3 Only the pinned uv from packaging/tools.json is used. It is sha256-verified and placed in .tools/bin. The output on Robert's machine names both versions. The sync always runs `--frozen` with UV_NO_CONFIG=1.
- [ ] #4 On Windows and Linux the pinned ffmpeg and ffprobe go into .tools/bin when none answers on PATH. On macOS there is no pinned download - the release builds its own (release.yml:104) - so one line gives the command `brew install ffmpeg`. MyScribe does not run brew: ADR-017's rule allows only a pinned, sha256-verified artifact, and a package manager is neither (the pinned ffmpeg of packaging/tools.json itself stays under ADR-011).
- [ ] #5 It does not set UV_CACHE_DIR, UV_PYTHON_INSTALL_DIR or PYTHONPYCACHEPREFIX, and does not export SCRIBE_DATA_DIR unless --data-dir was given. A test shows that a `.env` which moves the data directory is honoured by setup, the proof and the start alike.
- [ ] #6 The clone keeps the dev group by default, and --no-dev is available. The release keeps --no-dev and --python-preference only-managed, so tests/test_launcher.py:125 stays green.
- [ ] #7 Before the sync it checks free space on the volume the environment lands on. When it is too small, one sentence gives both numbers and nothing is downloaded (brief: M3).
- [ ] #8 W2 is settled: decided by Robert on 2026-09-20 (brief: G5), `/health` says what it serves, and doubt refuses the sync. The notes quote that decision before any refusal is built. The hazard behind it is still an inference and is reproduced first, on a COPY: what does `uv sync` do to a .venv whose interpreter is running on Windows? The output is shown, and the notes say what it means for the refusal. A warning in place of the refusal was set aside because nobody had measured exactly this; turning the refusal into a warning stays Robert's call.
- [ ] #9 The mechanism is the two `/health` fields (brief: G5): the source tree the app runs from and the data directory it serves. scribe/app.py:296-298 and the test that pins the body exactly (tests/test_app.py:45-48) are in the change, with the diff shown; the launcher reads only `ok` (packaging/launcher/myscribe_launcher.py:306-312) and its tests stay green, unchanged. install.py asks the default port and the one given with `--port`, and compares paths the way the design spec says (section 3.9: after os.path.normcase(os.path.realpath(...))). Four behaviours are tested. A MyScribe running from another source tree on the same port does not block the sync. One running from this checkout does, with 'stop it first, or use --no-sync'. Nothing answering does not block it. A MyScribe that answers without the fields - an older one - is doubt, and doubt refuses the sync the same way. What install.py cannot see is said in one sentence, not implied: an app on a port nobody mentioned, this repository's own `--port 4299` among them.
- [ ] #10 build_payload.fetch writes its cache atomically and deletes a cached file whose sum is wrong; today a bad cached file fails every later run (packaging/build_payload.py:49-58). A network failure is reported as one sentence, not a traceback.
- [ ] #11 It hands the terminal to `.venv python -m scribe.setup`, then runs the proof. When there is no TTY, one line gives the winpty or PowerShell hint and the run continues unattended.
- [ ] #12 The flags --non-interactive, --answers FILE, --no-dev, --no-sync, --data-dir, --check, --start and --port all work; --port is the one the /health probes ask besides the default. Re-running is idempotent and asks only what is open and was not skipped (TASK-089.11): the start scripts send people back to `python install.py` after every pull (criterion 13), and that must not nag. A skipped question is reached again with a bare `python -m scribe.setup`, which is the clone's `--setup`. `--check` is the report of TASK-089.13 and nothing else, and its help text says what it touches.
- [ ] #13 The lock-sha stamp is written into .venv. scripts/start.sh and scripts/start.ps1 compare it and say 'run python install.py'. The pip and requirements hints are gone there and in scripts/mac-acceptance.sh.
- [ ] #14 tests/test_install.py holds a stdlib allow-list check by AST (sys.stdlib_module_names) over install.py, packaging/build_payload.py and the launcher. All three parse with feature_version=(3,9). Fake-uv tests cover the order of steps.
- [ ] #15 The README's 'From a clone' section (README.md:113-125) is the two commands. install.py is not in build_payload.APP_PATHS.
- [ ] #16 It creates no shortcut, no desktop entry and no new start script; the existing scripts/start.* are corrected, not replaced. Where the library lives stays a flag, `--data-dir`, with one line saying that `git clean -fdx` deletes a library inside the clone.
- [ ] #17 install.py shares the launcher's sequence and not its code: decided by Robert on 2026-09-20 (brief: G2). It imports nothing from packaging/launcher/, and the launcher gains no CloneLayout and no branch only a clone reaches. Fetching the tools and checking their sha256 is shared with packaging/build_payload.py (criteria 3, 4 and 10), which is where that code lives: the launcher copies its tools out of the payload. One contract test drives both doors against a fake uv, gives them the same uv.lock and the same stamp, and demands the same sync decision from each - a sync when the environment or the stamp is missing or the lock digest differs, none when it matches (needs_sync, packaging/launcher/myscribe_launcher.py:136-144). It is shown to bite on a COPY of the repo: one door's decision is changed there, and the red output is shown.
<!-- AC:END -->
