---
id: TASK-117
title: >-
  Health names the launcher's own pid on Windows, so an update stops a running
  MyScribe through its stop route
status: To Do
assignee: []
created_date: '2026-10-22T18:38:00Z'
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
