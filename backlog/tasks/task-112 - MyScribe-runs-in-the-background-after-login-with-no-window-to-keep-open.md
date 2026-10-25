---
id: TASK-112
title: MyScribe runs in the background after login, with no window to keep open
status: To Do
assignee: []
created_date: 2026-10-19 05:49
updated_date: '2026-10-24T20:13:00Z'
labels:
  - packaging
  - autostart
dependencies: []
references:
  - >-
    docs/adr/ADR-026-myscribe-runs-in-the-background-as-a-per-user-login-item-started-headless-not-as-an-operating-system-service.md
  - docs/reports/2026-10-19-background-server-research.md
  - TASK-089.21
priority: medium
ordinal: 213000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert asked on 2026-10-19 that MyScribe on a laptop or MacBook starts by itself at login as a server, instead of a launcher window from the installer that has to stay open. ADR-026 (Proposed) recommends keeping the per-user login item of TASK-089.21 and starting the launcher with `--headless`, not an operating-system service. The subtasks carry the work; the first waits for Robert to accept ADR-026.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Robert has accepted, changed or rejected ADR-026, and the subtasks follow the option chosen
- [ ] #2 On Windows a real login starts MyScribe with no window, and /health answers without anybody starting it (closes TASK-089.21 criterion 6 for Windows)
- [ ] #3 The server can be stopped and is stopped before an update or uninstall
- [ ] #4 macOS and Linux walks are listed with their commands, and read not run until somebody runs them
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): close on its own 4 ACs (option A); the subtasks continue on their own.
<!-- SECTION:NOTES:END -->
