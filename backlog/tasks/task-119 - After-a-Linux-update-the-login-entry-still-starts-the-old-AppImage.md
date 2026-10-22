---
id: TASK-119
title: After a Linux update the login entry still starts the old AppImage
status: To Do
assignee: []
created_date: '2026-10-22T20:13:00Z'
labels:
  - packaging
  - linux
  - autostart
dependencies: []
priority: medium
ordinal: 233000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-22 in WSL (TASK-115, L4b). On Linux an update is a new file, MyScribe-<version>-linux-x64.AppImage, next to or instead of the old one. autostart.refresh() rewrites a login entry only when it already names this same launcher, so a second install cannot take over another's entry; for AppImages that means the desktop entry written by 0.9.0 (Exec=.../MyScribe-0.9.0-linux-x64.AppImage --at-login --no-browser --headless) was left as it was when 0.9.1 started from the new file. The next login starts the old version, and fails once the old file is deleted. Has been so since the first Linux login entry; not a 0.9.1 regression. A decision for Robert: treat an entry that names another MyScribe-*-linux-x64.AppImage in the same folder as ours; ship or recommend a stable name; or tell people in Settings and in docs/installation.md to switch Start at login off and on after replacing the file. TASK-112.06 (systemd user unit) touches the same entry.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Robert chose how an AppImage update reaches the login entry
- [ ] #2 After the chosen update path, the login entry starts the new version, shown in WSL
<!-- AC:END -->
