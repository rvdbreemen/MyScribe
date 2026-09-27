---
id: TASK-097
title: >-
  The installer asks where the library goes, apart from the program and the
  speech engine
status: To Do
assignee: []
created_date: '2026-09-27 08:29'
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
- [ ] #2 Leaving the page unchanged gives the same layout as today; nothing is ever put under the program folder
- [ ] #3 A folder that already holds a MyScribe library is recognised and used as that library (the adoption of ADR-019), with its numbers shown before it is used; a folder that holds something else is refused with a sentence
- [ ] #4 The choice survives an update and an uninstall: installing a new version keeps it without asking again, and uninstalling never deletes the library
- [ ] #5 macOS and Linux, which have no installer page, ask the same question at first start before anything is downloaded, with the same default
- [ ] #6 An ADR records the split between library and home and the default, and README and docs/RELEASING.md say where the library lives
- [ ] #7 Tests red first for each behaviour, including an installer-to-launcher handoff test on Windows; a real install on this laptop with the library on D:\Data\MyScribe
<!-- AC:END -->
