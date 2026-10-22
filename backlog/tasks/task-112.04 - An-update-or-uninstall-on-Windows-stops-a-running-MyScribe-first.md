---
id: TASK-112.04
title: An update or uninstall on Windows stops a running MyScribe first
status: In Progress
assignee:
  - '@claude'
created_date: 2026-10-19 05:51
updated_date: '2026-10-22T00:48:00Z'
labels:
  - packaging
  - autostart
dependencies:
  - TASK-112.03
references:
  - docs/reports/2026-10-19-background-server-research.md
  - packaging/windows/myscribe.iss
parent_task_id: TASK-112
ordinal: 217000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
myscribe.iss has no [UninstallRun] stop and no PrepareToInstall stop. A running headless server keeps serving the old code after an update, and its python.exe lives outside {app}, where Restart Manager does not look. The first update still finds the old MyScribe.exe without --stop, so the installer code calls the stop route itself with taskkill as fallback.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An update while MyScribe runs stops it, installs, and starts it headless again when the login item is on
- [ ] #2 An uninstall while MyScribe runs stops it before files are removed, and removes the Run value as today
- [x] #3 Both work against a server started by an older version that has no --stop
- [ ] #4 A real install over a running 0.8.x on Windows shows it, with the install log
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. packaging/windows/stop-myscribe.ps1 (PowerShell 5.1, testable from pytest): if /health names a launcher_pid running from InstallDir, POST /api/shutdown and wait for the app pid (60 s); then taskkill /T /F every process whose executable lies under InstallDir (a 0.8.x launcher and its python child, a windowed launcher left open). A server whose launcher is elsewhere (a clone, another install) is left alone. Exit 0 nothing / 10 stopped / 1 still running; lines to MyScribe-launcher.log.
2. myscribe.iss: the script ships in {app}; PrepareToInstall extracts and runs it (1 aborts with a sentence); ssPostInstall restarts {app}\MyScribe.exe --at-login --no-browser --headless when something was stopped and the HKCU Run value MyScribe exists; InitializeUninstall runs it from {app} and refuses on 1. MyAppId overridable by a define for test builds only.
3. Tests: the script against real processes and fake servers; text tests for the .iss; iscc compile; a test install over a running fake launcher.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence (scratchpad):
- Red: red-112.04.txt (script tests, 5 failed without the script), red-112.04-iss.txt (3 failed).
- Green: test_stop_myscribe_script 5 passed (route asked and pid waited; 0.8.x ended with its child; route too slow -> ended; clone left alone; nothing running); test_uninstall_text 8 passed.
- iscc 6 compiled the script with a dummy payload (exit 0).
- Test install (test AppId, scratchpad folder, setup-112.04.log): a fake 0.8.x launcher (cmd copy + ping child) running from the target folder was ended before the files ('setup: Ending MyScribe.exe (process 13004) and what it started.'), Restart Manager then found nothing in use, and afterwards '{app}\MyScribe.exe --at-login --no-browser --headless' ran (the real Run value exists on this machine). The real MyScribe was not running (4242 silent).
- Side effect, repaired: /GROUP was ignored (DisableProgramGroupPage=yes), so the test install rewrote the real Start menu shortcut MyScribe.lnk to the test folder. Restored its target, working dir and icon to %LOCALAPPDATA%\Programs\MyScribe\MyScribe.exe; test uninstall key and folder removed; real uninstall key and Run value untouched.
Not verified: an uninstall at runtime (a test uninstall would delete the real Run value MyScribe; covered by text tests and the script tests only); AC4, a real release installer over a running 0.8.x; a real app requeueing through the installer (the route path was tested against a fake server).

Release walk for Robert (agreed 2026-10-21), on the next release over the running 0.8.6, with Start at login on:
A. Update (AC1, AC4): 1) Check 0.8.6 runs: curl -s http://127.0.0.1:4242/health shows version 0.8.6. 2) Run the new installer with a log: MyScribe-<version>-windows-x64-setup.exe /LOG=%TEMP%\myscribe-setup.log. 3) Afterwards: the log names stop-myscribe.ps1 and %LOCALAPPDATA%\MyScribe-launcher.log has 'setup: Ending MyScribe.exe (process N)'; Restart Manager reports no application in use. 4) /health answers with the new version, and no window opened (Get-Process MyScribe shows it). 5) The Run value now ends in --headless (TASK-112.01).
B. Update over a new version with a job running (route path): queue a recording, run the installer again while it transcribes; MyScribe-launcher.log says 'asking it to stop', and after the install the job is queued again and starts over.
C. Uninstall (AC2): while MyScribe runs, Settings > Apps > MyScribe > Uninstall. Afterwards: no MyScribe.exe or home python.exe from it runs, %LOCALAPPDATA%\Programs\MyScribe is gone, the Run value MyScribe is gone, and %LOCALAPPDATA%\MyScribe (the library) is still there. Reinstall afterwards to keep using it.

2026-10-22: 0.9.0 is the release to walk on for AC #2 and AC #4. That means a real install of 0.9.0 over a running 0.8.6 on Windows, with the install log, and an uninstall while MyScribe runs. The release PR is release-0.9.0; status and AC left as they are until Robert's walk.

Correction to the release walk (2026-10-22): the Windows installer is MyScribe-<version>-windows-x64.exe (for this release: MyScribe-0.9.0-windows-x64.exe /LOG=%TEMP%\myscribe-setup.log), not ...-setup.exe. And step B (the running job requeued through the stop route) can only be seen when updating a 0.9.0 over a 0.9.0 or later: 0.8.x has no stop route, so an update from 0.8.6 ends it and its running job is marked interrupted (Retry starts it over).
<!-- SECTION:NOTES:END -->
