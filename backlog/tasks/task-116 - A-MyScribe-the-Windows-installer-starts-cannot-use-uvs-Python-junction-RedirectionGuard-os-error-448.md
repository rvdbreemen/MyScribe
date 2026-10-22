---
id: TASK-116
title: >-
  A MyScribe the Windows installer starts cannot use uv's Python junction
  (RedirectionGuard, os error 448)
status: To Do
assignee: []
created_date: '2026-10-22T18:38:00Z'
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
- [ ] #1 myscribe.iss sets RedirectionGuard=no with the reason, and a test fails without it
- [ ] #2 In Windows Sandbox the MyScribe that the fixed installer restarts has Enforce=0 and serves; the released 0.9.0 without the flag reproduces Enforce=1 and the failed start
- [ ] #3 The CHANGELOG says who was hit and what to do on 0.9.0 (start MyScribe from the Start menu)
<!-- AC:END -->
