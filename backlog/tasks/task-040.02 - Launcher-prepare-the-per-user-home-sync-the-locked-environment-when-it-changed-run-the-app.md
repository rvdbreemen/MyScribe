---
id: TASK-040.02
title: >-
  Launcher: prepare the per-user home, sync the locked environment when it
  changed, run the app
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 21:18'
updated_date: '2026-09-19 16:59'
labels:
  - packaging
dependencies:
  - TASK-029.01
parent_task_id: TASK-040
ordinal: 72000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-011: the executable a user double-clicks. It runs before any Python environment exists, so it is stdlib-only and frozen per OS; it must show progress through a first sync of about 3 GB on Windows, keep the app from being started twice, and give the user a way to quit - today the app has no quit path at all.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 First run creates the environment in the per-user home with the bundled uv and a uv-managed Python, showing progress; later runs skip the sync while the shipped uv.lock is unchanged and re-sync when it changed
- [x] #2 The app runs from that environment with SCRIBE_DATA_DIR and .env in the per-user home and the bundled ffmpeg first on PATH; the browser opens when it is up
- [x] #3 A second launch while MyScribe is running opens the browser instead of starting another server
- [x] #4 Quit stops the server and any runner child it started
- [x] #5 tests/test_launcher.py covers the paths, the sync decision and the single-instance rule without network access
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. scribe/env.py honours SCRIBE_ENV_FILE (failing test first). 2. packaging/launcher/myscribe_launcher.py, stdlib only: Layout (per-user home per OS, payload app/ + bin/), sync decision by a sha256 stamp of the shipped uv.lock, uv sync --frozen --no-dev with a uv-managed Python kept in the home, child env scrubbed of PyInstaller leaks, app started from the env with cwd = shipped source in its own process group, /health wait, browser, single instance by /health, Quit = SIGTERM/SIGKILL to the group (taskkill /T on Windows). Tk window with progress/Open/Folder/Quit; --headless, --sync-only, --doctor, --smoke for CI. 3. tests/test_launcher.py without network, incl. a real process tree for Quit. 4. Real run on the M2 from a payload with a fresh home.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Real run 2026-09-11 on the M2, unfrozen launcher, payload from packaging/build_payload.py (uv 0.12.13, ffmpeg 8.1.2 LGPL built from source), PATH reduced to Finder's (/usr/bin:/bin:/usr/sbin:/sbin), fresh home: first --sync-only 11 s (uv-managed CPython 3.12.14 + 126 packages; fast connection), second run 0 s (stamp matched). --doctor: all OK incl. gpu-smoke on mlx, using the bundled ffmpeg 8.1.2 not Homebrew's. --smoke on 4311: /health ok, / ok. Headless on 4242 with HF_TOKEN in the home's .env (created from .env.example on first run): job 1 (44 s dialogue) done, transcribe 14.4 s on mlx, diarize 64.5 s community-1 no fallback, 2 speakers, 0 unattributed - diarize slow on the cold first run (fresh bytecode prefix); job 2 warm: transcribe 12.3 s, diarize 29.7 s. The first real run found a bug the unit tests had not: a relative --payload broke uv's path because the sync runs with cwd = home; Layout now makes both roots absolute (test_relative_roots_become_absolute, red then green). tests/test_launcher.py: 19 passed. The Tk window is exercised in the frozen .app (TASK-029.03).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
De launcher doet wat hij moet doen en alle vijf de criteria zijn afgevinkt met bewijs. Eerste start maakt de omgeving in de per-user home met de meegeleverde uv en een uv-beheerde Python, latere starts slaan de sync over zolang uv.lock niet wijzigde; de app draait uit die omgeving met SCRIBE_DATA_DIR, .env en de meegeleverde ffmpeg vooraan op PATH; een tweede start opent de browser in plaats van een tweede server; afsluiten stopt de server en zijn runner-kind. tests/test_launcher.py dekt de paden, het sync-besluit en de single-instance-regel zonder netwerk. Daarbovenop is de launcher sinds 2026-09-19 op alle drie de platforms gestart door CI: release.yml draait build_release.py --smoke, die de zojuist gebouwde bevroren launcher tegen een verse home start - eerste sync, /health, een pagina, afsluiten - en dat was groen in run 35447454324 voor windows-x64, macos-arm64 en linux-x64.
<!-- SECTION:FINAL_SUMMARY:END -->
