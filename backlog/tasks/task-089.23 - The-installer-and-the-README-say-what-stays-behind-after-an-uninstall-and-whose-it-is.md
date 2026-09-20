---
id: TASK-089.23
title: >-
  The installer and the README say what stays behind after an uninstall, and
  whose it is
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - packaging
  - windows
  - release
dependencies:
  - TASK-089.17
  - TASK-089.18
  - TASK-089.21
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 160000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The Inno Setup welcome text promises 'about 3 GB' and says 'Your recordings, transcripts and models are kept in %LOCALAPPDATA%\MyScribe and are left alone when you uninstall' (packaging/windows/myscribe.iss:51). After this work three parts of that stop being the whole truth (brief: M8).

MyScribe may have installed Ollama and a model of 3.4 to 7.6 GB (TASK-089.18); those sizes are a reader's figures from Ollama's library in the design run of 2026-09-20, not in the repository and not re-verified. Both survive an uninstall, and the user is never told that they now belong to them. The home may have been moved by the pointer file of TASK-089.14, so '%LOCALAPPDATA%\MyScribe' is only the default, and the wizard speaks BEFORE that question is asked. And a login item may exist (TASK-089.21).

From a clone, `.tools/` holds the pinned uv and ffmpeg that TASK-089.17 fetched, and nothing mentions it in any cleanup.

Two drifts in the same texts, found by a reader and confirmed when this task was written. The README says Windows installs 'into %LOCALAPPDATA%\MyScribe' (README.md:87-89), while the .iss installs the program into %LOCALAPPDATA%\Programs\MyScribe (myscribe.iss:18). And the .iss header still cites ADR-008 (myscribe.iss:1), which was renumbered to ADR-011.

Needs a real machine: A real install and uninstall on Windows (Robert, or a VM) for the last-but-one criterion. The macOS and Linux sections are written from reading, and say so, until somebody runs them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The Inno welcome text says what the first start will ask, that the location can be chosen there and that %LOCALAPPDATA%\MyScribe is only the default, and what is left alone on uninstall. Its size figure agrees with the total of TASK-089.14 or is gone. The diff of myscribe.iss is shown.
- [ ] #2 It says in one sentence that an Ollama and a model that MyScribe installed survive an uninstall and belong to the user, and how to remove them with Ollama's own uninstaller.
- [ ] #3 The README has a short 'Uninstalling' section per OS: what the uninstaller removes, what stays (the home or the folder the pointer file names, the pointer file itself, Ollama and its models), and how to remove each by hand.
- [ ] #4 For a clone the README documents `.tools/`: what is in it, that deleting it is safe, and that `python install.py` fetches it again.
- [ ] #5 An uninstall on Windows removes the login item of TASK-089.21. Needs a real uninstall: Robert runs it on Windows or in a VM and shows what is left on disk and in the login items. If it was not run, the notes say so.
- [ ] #6 README.md:87-89 and myscribe.iss:18 agree about where the program is installed, and the .iss header cites ADR-011.
<!-- AC:END -->
