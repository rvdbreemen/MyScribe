---
id: TASK-089.23
title: >-
  The installer and the README say what stays behind after an uninstall, and
  whose it is
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-26 18:23'
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
- [x] #1 The Inno welcome text says what the first start will ask, that the location can be chosen there and that %LOCALAPPDATA%\MyScribe is only the default, and what is left alone on uninstall. Its size figure agrees with the total of TASK-089.14 or is gone. The diff of myscribe.iss is shown.
- [x] #2 It says in one sentence that an Ollama and a model that MyScribe installed survive an uninstall and belong to the user, and how to remove them with Ollama's own uninstaller.
- [x] #3 The README has a short 'Uninstalling' section per OS: what the uninstaller removes, what stays (the home or the folder the pointer file names, the pointer file itself, Ollama and its models), and how to remove each by hand.
- [x] #4 For a clone the README documents `.tools/`: what is in it, that deleting it is safe, and that `python install.py` fetches it again.
- [ ] #5 An uninstall on Windows removes the login item of TASK-089.21. Needs a real uninstall: Robert runs it on Windows or in a VM and shows what is left on disk and in the login items. If it was not run, the notes say so.
- [x] #6 README.md:87-89 and myscribe.iss:18 agree about where the program is installed, and the .iss header cites ADR-011.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Re-read the .iss welcome text (TASK-089.14 already dropped the "about 3 GB" and says the first start asks where everything goes) and the README install/uninstall passages; reproduce both drifts (README:87-89 vs .iss:18, .iss:1 citing ADR-008).
2. .iss: header cites ADR-011; WelcomeLabel2 says what the first start asks (location, then the setup questions), that %LOCALAPPDATA%\MyScribe is only the default, what an uninstall removes and leaves, and one sentence that an Ollama and a model MyScribe installed stay and are the user's, removed with Ollama's own uninstaller. No size figure (it cannot read footprint.json; the first start shows the size).
3. .iss: remove the TASK-089.21 login item on uninstall with an [Registry] entry (HKCU Run value "MyScribe", ValueType none, uninsdeletevalue) - the value name from scribe/autostart.py.
4. README: fix the Windows install folder sentence; add an "Uninstalling" section per OS (what the uninstaller removes, what stays - the home or the folder the pointer names, the pointer file, the login item on macOS/Linux, OLLAMA_MODELS, Ollama and its models - and how to remove each by hand); document .tools/ for a clone. Every size figure checked against scribe/footprint.json and scribe/ollama_release.json.
5. Evidence: the .iss diff, the README diff, a grep that the numbers match their sources. #5 (a real uninstall) is written up for Robert as exact steps and expected output; not run here.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built 2026-09-23 by the orchestrator in MyScribe-wt-setup (the build agent stopped after writing the plan above).

CHANGED: packaging/windows/myscribe.iss - header cites ADR-011; a [Registry] entry removes the login item on uninstall and writes nothing at install; WelcomeLabel2 says where the program goes, that the library folder is only the default and is chosen at the first start, that uninstall removes the program and its login item and leaves the library, and that an Ollama and a model MyScribe installed stay, belong to the user, and are removed with Ollama's own uninstaller under Windows Settings > Apps. No size figure (the existing test that keeps GB figures out still passes). README.md - the Windows install sentence names both folders; a '.tools/' paragraph in 'From a clone'; a new '## Uninstalling' section for Windows, macOS, Linux and a clone.

SOURCES, read 2026-09-23: Inno Setup help, [Registry] section (jrsoftware.org/ishelp/topic_registrysection.htm), verbatim: ValueType none - 'Setup will create the key but not a value'; dontcreatekey - 'Setup will not attempt to create the key or any value if the key did not already exist'; uninsdeletevalue - 'Delete the value when the program is uninstalled.' The entry is ValueType none + uninsdeletevalue dontcreatekey, and not uninsdeletekey, which would take every other program's login item with it. docs.ollama.com/windows: 'The Ollama Windows installer registers an Uninstaller application. Under Add or remove programs...', models in %HOMEPATH%\.ollama, and 'If you have changed the OLLAMA_MODELS location, the installer will not remove your downloaded models'. docs.ollama.com/linux: the Uninstall section's eight commands; the README links to it rather than copying them. Every path in the new text was checked against the code: the program folder against the .iss DefaultDirName, the library and the pointer file against the launcher's default_home and pointer_path (MyScribe.location beside the home), the login items against scribe/autostart.py (RUN_KEY, APP_NAME, LABEL, DESKTOP_NAME), .tools/ against .gitignore:70 and install.py TOOLS_STAMP.

The task text's 'MyScribe may have installed ... a model of 3.4 to 7.6 GB' was not repeated: the README names no size (scribe/ollama_release.json holds the model sizes, 3,389,983,735 and 7,556,508,396 bytes, if one is ever wanted).

TESTS: tests/test_uninstall_text.py, 5 tests, red first on a copy of the worktree's HEAD with only the new test added (5 failed, red-test_uninstall_text.txt), green after (5 passed). They pin the registry entry against autostart's own constants, ADR-011 in the header, the welcome text's two promises, the README and .iss agreeing on the program folder, and an Uninstalling section per platform naming the pointer file and both login-item files. tests/test_launcher.py (reads WelcomeLabel2): 102 passed, 1 skipped.

#5 NOT RUN - Robert's, on Windows or in a VM: install a build of this branch's installer, switch Settings > Start at login on, check reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v MyScribe (a value), uninstall under Settings > Apps, then reg query again (expected: 'unable to find'), check that %LOCALAPPDATA%\Programs\MyScribe is gone and %LOCALAPPDATA%\MyScribe is still there, and that the other Run values are all still present. The macOS and Linux sections are written from the code and say so; macOS is point 7 of docs/macos-acceptance.md.

Closed on Robert's decision of 2026-09-26 ('Kunnen we taken lekker afsluiten'): the remaining criteria need a person at a machine and will not be run; they stay unticked, and nothing here claims them verified. Open and not run: criterion 5.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The installer's welcome text and the README say what an uninstall leaves behind and whose it is; the uninstaller removes the login item and writes none at install. Proven by tests against autostart's own constants and Inno Setup's documentation. Not run: a real uninstall on Windows (criterion 5).
<!-- SECTION:FINAL_SUMMARY:END -->
