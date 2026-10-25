---
id: TASK-097
title: >-
  The installer asks where the library goes, apart from the program and the
  speech engine
status: In Progress
assignee:
  - '@claude'
created_date: 2026-09-27 08:29
updated_date: '2026-10-25T19:16:00Z'
labels:
  - installer
  - library
  - windows
dependencies: []
priority: medium
ordinal: 171000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-27: the data that belongs to the app (the database myscribe.db, recordings, models, work files, the app log) must live apart from where the software is installed, and a person chooses that folder during installation. Today the Windows installer only asks for the program folder (%LOCALAPPDATA%\Programs\MyScribe); the first start then asks one question, 'Where should MyScribe keep everything?', for a home that holds the speech engine, caches and the library together (packaging/launcher/myscribe_launcher.py Layout; scribe/paths.py puts myscribe.db, media, work, logs and models under DATA_DIR). A library can only be put elsewhere by adopting an existing one (ADR-019), so a first install cannot choose it. Decided with Robert: when nobody changes it, the library defaults to the per-user folder as now (Windows %LOCALAPPDATA%\MyScribe, macOS ~/Library/Application Support/MyScribe, Linux ~/.local/share/MyScribe), never the install directory, because Program Files, a .app and an AppImage are read-only or replaced on update. The engine and caches, which can be downloaded again, may stay in the home.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The Windows installer shows a page for the library folder, separate from the program folder, prefilled with the per-user default; the chosen folder is where myscribe.db, media, models, work and the app log land
- [x] #2 Leaving the page unchanged gives the same layout as today; nothing is ever put under the program folder
- [x] #3 A folder that already holds a MyScribe library is recognised and used as that library (the adoption of ADR-019), with its numbers shown before it is used; a folder that holds something else is refused with a sentence
- [ ] #4 The choice survives an update and an uninstall: installing a new version keeps it without asking again, and uninstalling never deletes the library
- [ ] #5 macOS and Linux, which have no installer page, ask the same question at first start before anything is downloaded, with the same default
- [x] #6 An ADR records the split between library and home and the default, and README and docs/RELEASING.md say where the library lives
- [ ] #7 Tests red first for each behaviour, including an installer-to-launcher handoff test on Windows; a real install on this laptop with the library on D:\Data\MyScribe
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): option A: the installer page (and /LIBRARY= for silent installs) records a library_request in MyScribe.location only when no home/data is set; an empty or new folder is used by the launcher, a folder with a database is adopted by the engine after the download, numbers first (new ADR amending ADR-019). The separate engine-home question is dropped (per-user default; MYSCRIBE_HOME/--home remain). A chosen folder that holds other things gets a MyScribe subfolder.

2026-10-25 Robert's decision (asked with options in the session): ADR-036 accepted. A door without a window (console, at login) whose library request cannot be used starts on <home>\data and says the request was not used (instead of refusing).

2026-10-25 PR #105 merged (merge 12255ac), CI green on three OSes. Built per Robert's option A: an Inno page after the program folder (prefilled {localappdata}\MyScribe) and /LIBRARY= write a library_request into MyScribe.location only on a first install (no pointer, no env); the launcher uses an empty or new folder (a folder with other files gets a MyScribe subfolder, Robert 2026-10-24 - this replaces AC3's 'refused with a sentence' for that case; refused only when the subfolder also holds other files), a folder with myscribe.db/scribe.db is adopted by the engine after the download with its numbers first; the engine-home question is gone; a headless start over an unusable request starts on <home>\data and says so (Robert 2026-10-25); a windowed re-ask happens only while <home>\data holds no recordings. Disk check splits across volumes. ADR-036 accepted. Evidence: 12/12 mutants, red-first tests (test_launcher_library_request 41, test_installer_library_page 9, test_launcher 230 passed), ISCC compile exit 0, a real launcher+engine run on scratch pointers (empty, new, folder with other files, existing synthetic db with 3 recordings, refused). Open: AC1/AC4/AC7 need a real install, update and uninstall (the Windows test-user run, with a COPY of the library rather than D:\Data\MyScribe itself); AC5 shown by unit tests for darwin/linux only.
<!-- SECTION:NOTES:END -->
