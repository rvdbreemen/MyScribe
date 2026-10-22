---
id: TASK-117
title: >-
  Health names the launcher's own pid on Windows, so an update stops a running
  MyScribe through its stop route
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T18:38:00Z'
updated_date: '2026-10-22T18:54:00Z'
labels:
  - packaging
  - windows
  - bug
dependencies: []
priority: high
ordinal: 231000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-22 on Robert's machine (TASK-115). /health reports launcher_pid as os.getppid() of the app. On Windows the app's parent is uv's trampoline env/Scripts/python.exe, not MyScribe.exe: measured launcher_pid 35680 = D:/Data/MyScribe/env/Scripts/python.exe, whose parent 6384 is the launcher. stop-myscribe.ps1 asks the stop route only when launcher_pid runs from InstallDir, so on a real install it never does: it taskkills the tree instead, and a running job is marked interrupted rather than requeued (ADR-028). Launcher log of a real 0.9.0 over 0.9.0 update: 'setup: Ending MyScribe.exe (process 6384) and what it started.' with no 'asking it to stop'. The earlier test used a fake server without a trampoline. The launcher's --stop hard-stop fallback likewise ends the trampoline, which the launcher reads as its app dying (exit 70).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The launcher hands its own pid to the app, and /health reports it as launcher_pid; tests cover both
- [ ] #2 stop-myscribe.ps1 treats a server as this install's when a process under InstallDir is an ancestor of its app pid, so a running 0.9.0 is stopped through the route too; a test covers the trampoline chain
- [ ] #3 In Windows Sandbox an update with a job running logs 'asking it to stop' and the job is queued again and finishes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-22, Robert's machine: /health launcher_pid 35680 = D:/Data/MyScribe/env/Scripts/python.exe (parent 6384 = MyScribe.exe --at-login --no-browser); app 30248 below it. Published 0.9.0 over that running 0.9.0 (no job running): MyScribe-launcher.log '2026-10-22 20:36:14 setup: Ending MyScribe.exe (process 6384) and what it started.', no 'asking it to stop'. Fix in PR #80: launcher sets MYSCRIBE_LAUNCHER_PID, app _launcher_pid() reports it (getppid fallback for an older launcher), stop-myscribe.ps1 Get-OurAncestor walks the app pid's parents (8 steps). Red first: test_health_names_the_pid_the_launcher_gave_not_the_parent 'assert 46180 == 4321'; test_the_app_is_told_where_the_launcher_is KeyError MYSCRIBE_LAUNCHER_PID; test_a_myscribe_whose_parent_is_uvs_trampoline_is_asked_through_its_route '[] == [/api/shutdown]' with 'setup: Ending MyScribe.exe (process 45152)'. Green: stop script + RG + launcher env tests 10 passed; test_app -k health 4 passed.
<!-- SECTION:NOTES:END -->
