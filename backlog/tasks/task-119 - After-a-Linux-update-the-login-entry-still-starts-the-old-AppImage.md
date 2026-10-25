---
id: TASK-119
title: After a Linux update the login entry still starts the old AppImage
status: Done
assignee: []
created_date: '2026-10-22T20:13:00Z'
updated_date: '2026-10-25T09:19:00Z'
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
- [x] #1 Robert chose how an AppImage update reaches the login entry
- [x] #2 After the chosen update path, the login entry starts the new version, shown in WSL
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): options A + C: an entry naming MyScribe-<version>-linux-x64.AppImage in the same folder as the running AppImage is ours and is rewritten at start; the Settings card also says when the entry starts another file. Applies to TASK-112.06's systemd unit ExecStart too.

2026-10-25 PR #95 merged (CI green on three OSes). Sibling rule (A) in refresh()/migrate() for the XDG file and the systemd unit, Linux only; card line (C) when the entry starts another file; macOS App Translocation refused on switch-on (review finding). Red: 9 failed on main; 7 mutants killed. Green: test_autostart + test_web_settings 139 passed; with three more files 384 passed. WSL scratch user, app on 4460: desktop file and unit rewritten 0.9.0 -> 0.9.1 (autostart.rewritten), a 0.9.0 desktop file migrated into the unit (autostart.migrated), another folder left alone with the card line; origin/main reproduced the bug. Not verified: an AppImage built from the branch, a real login executing the rewritten entry.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
An AppImage update in the same folder now takes the Linux login entry over (desktop file and systemd unit), and the Settings card says when the entry starts another file. Shown in WSL on a scratch user; the bug reproduced on main. Consequence: the last AppImage started in a folder owns the entry.
<!-- SECTION:FINAL_SUMMARY:END -->
