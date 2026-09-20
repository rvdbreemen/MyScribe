---
id: TASK-089.14
title: >-
  A release install asks where everything goes before anything is downloaded,
  and shows what it will cost
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-20 21:48'
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
- [ ] #1 On a first run with no pointer file and no environment, the location question appears before the tools are installed and before the sync. A test over the bootstrap order asserts that nothing was downloaded, and nothing written under any home, before it was answered.
- [ ] #2 It shows the default home, the free space on each local volume, the total this install will download and the disk it needs. The total is computed, not a literal: the environment's size for this platform with the date it was measured, the weights from the catalogue for this platform, and Ollama with its model only as 'up to' figures. Each number names its source. The source for the Ollama figures, scribe/ollama_release.json, is created in TASK-089.18, which is built after this task. Until it lands they are labelled literals that carry their date and say they were read from Ollama's release page, not measured; TASK-089.18 criterion 4 switches them to the pin file. The '3 GB' in the launcher (:434-435) and in packaging/windows/myscribe.iss:51 agree with it or are gone.
- [ ] #3 The numbers come from files in the payload, read as data. The launcher imports nothing from the app (ADR-011), and the stdlib-only test stays green.
- [ ] #4 The answer is stored in a small pointer file, one JSON object, next to the default home: beside that folder, not inside it, so that a moved install leaves nothing under the default home. home_dir() honours it after MYSCRIBE_HOME and `--home`, which keep winning. Tests cover: no file; a file; an unreadable file; and a file naming a folder that no longer exists, which gives one sentence and the question again - never a silent fall-back to the default, which would look like an empty library. No test pins that the file holds one line or one fact: TASK-089.19 criterion 8 may add a second fact to it (ADR-015, Open Questions).
- [ ] #5 Skipping keeps today's location and writes no pointer file. The question belongs to a first run only: once an environment exists under the home it is not asked again, and a test shows it.
- [ ] #6 Red first: with less free space on the chosen volume than the install needs, today the sync starts anyway. After the change one sentence gives both numbers and no download is attempted. This check also runs when the question was skipped.
- [ ] #7 A home that already holds an environment is never moved by this question. Moving an existing install is out of scope, and the sitting says so rather than half-doing it.
- [ ] #8 Needs a person at the screen, on a machine with two volumes: one recorded first run on Windows that chooses a folder on the second drive, and shows env/ and data/ under it, with the models in data/models (scribe/paths.py:40; the launcher's Layout has no models folder of its own, :98-105). The default home holds nothing or does not exist, and the pointer file sits beside it, as criterion 4 says. Robert does this.
- [ ] #9 A chosen folder inside the install or payload directory is refused with one sentence, and the question is asked again. Accepted ADR-011's Must Not: 'Put the environment or the data directory inside the install directory' (docs/adr/ADR-011-...md:156-158). On Windows that is %LOCALAPPDATA%\Programs\MyScribe (packaging/windows/myscribe.iss:18), a folder that belongs to the installer, while the welcome text promises that the data is 'left alone when you uninstall' (:51). A relative path, a folder that cannot be written and a UNC path are refused the same way. The UNC refusal has its own reason: the library is SQLite in WAL mode (ADR-013), and SQLite's documentation says WAL does not work over a network filesystem (sqlite.org/wal.html, read for the design spec on 2026-09-20). A mapped drive letter hides the same problem and is not detected; the question's text says so. One test per case, and a refused answer writes no pointer file.
- [ ] #10 Where the answer can be given later is said in the question's own text and by `--setup`: MYSCRIBE_HOME, `--home`, or the pointer file, after moving the folder by hand (criterion 7). There is no Settings counterpart. That is a stated exception to scribe/setup.py:21-23, with its reason: the home holds the environment the app runs from (Layout.env_dir, packaging/launcher/myscribe_launcher.py:98), so the running app cannot move it. TASK-089.11 criterion 4 allows this exception by name.
<!-- AC:END -->
