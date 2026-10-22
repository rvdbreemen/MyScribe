---
id: TASK-116
title: >-
  A MyScribe the Windows installer starts cannot use uv's Python junction
  (RedirectionGuard, os error 448)
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T18:38:00Z'
updated_date: '2026-10-22T21:24:00Z'
labels:
  - packaging
  - windows
  - bug
dependencies: []
priority: high
ordinal: 230000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-22 on Robert's machine (TASK-115). Inno Setup 6.7.1 enables Windows RedirectionGuard on Setup by default (RedirectionGuard=yes). The MyScribe that Setup starts (the Start MyScribe checkbox, and the headless restart in CurStepChanged after an update with Start at login on) has ProcessRedirectionTrustPolicy Enforce=1, measured with GetProcessMitigationPolicy on the live process 28364, although Inno's help says the setting is not inherited. Under Enforce=1 uv cannot traverse its own minor-version junction python/cpython-3.12-windows-x86_64-none (created by a non-admin, pyvenv.cfg home points there): uv sync fails with 'Failed to query Python interpreter ... untrusted mount point (os error 448)' and env/Scripts/python.exe fails with 'uv trampoline failed to spawn Python child process'. Reproduced in a PowerShell that set SetProcessMitigationPolicy(16, Enforce=1): identical errors; the direct path cpython-3.12.14-... works. With the released 0.9.0 run as /NOREDIRECTIONGUARD the restarted launcher and both python children had Enforce=0 and served 0.9.0 headless. Starts from Explorer, the Start menu or the login Run value are not affected.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 myscribe.iss sets RedirectionGuard=no with the reason, and a test fails without it
- [ ] #2 In Windows Sandbox the MyScribe that the fixed installer restarts has Enforce=0 and serves; the released 0.9.0 without the flag reproduces Enforce=1 and the failed start
- [ ] #3 The CHANGELOG says who was hit and what to do on 0.9.0 (start MyScribe from the Start menu)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-22, Robert's machine. Robert installed 0.9.0 at 20:10 (InstallDate 20261022) with the Start MyScribe checkbox; pid 28364 (window 'MyScribe 0.9.0', no args) never served 4242. D:/Data/MyScribe/logs/launcher.log: 'error: Failed to query Python interpreter / Caused by: The path cannot be traversed because it contains an untrusted mount point. (os error 448) / uv sync failed with exit code 2'. GetProcessMitigationPolicy(policy 16) on pid 28364: Enforce=1; this shell and explorer: Enforce=0. From this shell python via the junction, via the direct path, and uv pip list --python env all worked. A PowerShell that called SetProcessMitigationPolicy(16, 1) (set=True now=1) reproduced it: env/Scripts/python.exe 'uv trampoline failed to spawn Python child process', uv pip list 'Failed to query Python interpreter ... (os error 448)', the direct cpython-3.12.14 path ok. Setup log of the published 0.9.0 run with /NOREDIRECTIONGUARD: 'Setup version: Inno Setup version 6.7.1', 'RedirectionGuard status for current process: Not enabling'; afterwards MyScribe.exe --at-login --no-browser --headless (21300) and both python children Enforce=0 hwnd=0, /health 0.9.0 after 11 s. CI builds every release since 0.8.0 with Inno Setup 6.7.1 (release run logs, Compiler engine version). GitHub #16 (Ramon, default install, 'Failed to create Python minor version link directory ... os error 448') is this cause; ADR-027 recorded the cause of 448 as unknown, and the retry it chose does not reach a guard that blocks traversal. Fix in PR #80: RedirectionGuard=no; test_setup_does_not_enable_redirectionguard_for_what_it_starts red (AssertionError) then green; ISCC compiles locally (exit 0).

2026-10-22 23:22, Robert's real machine, with his OK: the 0.9.1 rehearsal installer (run 39843057516, sha256 d1378ae2...a9e7) with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG over his running 0.9.0 (no job running or queued). Setup exit 0; setup log 'Setup version: Inno Setup version 6.7.1', 'RedirectionGuard status for current process: Not enabling' (no command-line flag); MyScribe-launcher.log 'setup: MyScribe is running (process 33548); asking it to stop.' and 'MyScribe stopped; a job that was running starts over at the next start.'; /health 0.9.1 after 15 s with launcher_pid 22404 = MyScribe.exe --at-login --no-browser --headless; launcher and both python children Enforce=0 hwnd=0; launcher.log 'Checked 124 packages' then 'MyScribe is running'. Log: scratchpad itest/results-real/setup-091-over-090.log.
<!-- SECTION:NOTES:END -->
