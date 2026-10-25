---
id: TASK-112.06
title: >-
  Linux starts MyScribe from a systemd user unit, with the XDG autostart file as
  fallback
status: Done
assignee: []
created_date: 2026-10-19 05:51
updated_date: '2026-10-25T11:20:00Z'
labels:
  - packaging
  - autostart
  - linux
dependencies:
  - TASK-112.01
  - TASK-112.02
references:
  - docs/reports/2026-10-19-background-server-research.md
parent_task_id: TASK-112
ordinal: 219000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Today Linux writes ~/.config/autostart/myscribe.desktop, which nothing supervises. A systemd user unit restarts a crash and logs to the journal. The user manager does not read .profile, so an XDG_DATA_HOME set only in the shell would make the background server open another library. Depends on the Linux open question of ADR-026.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 With a systemd user session, switching on writes ~/.config/systemd/user/myscribe.service and enables it; off disables and removes it
- [x] #2 Without one, the XDG autostart file is written as today
- [x] #3 An XDG_DATA_HOME set at registration is written into the unit as Environment=, and a test asserts it
- [x] #4 The unit restarts only on a crash exit code and stops after five failures in five minutes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): restart only on exit 70 (5 in 5 min) and a clone stays on the XDG file, as built.

2026-10-25 PR #92 merged, CI green. Evidence in the PR: WSL Ubuntu, systemd 255 PID 1, scratch user msunit, published 0.9.1 AppImage: unit written+enabled and verified (systemd-analyze verify exit 0); next login served /health headless with nothing started by hand; kill -9 -> status 70 -> restart (NRestarts=1); five crashes -> 'Start request repeated too quickly'; disable removed unit and wants link; no user session -> desktop file byte-identical. Environment=XDG_DATA_HOME with a space parsed by the manager. Not verified: a graphical login.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Linux Start at login is a systemd user unit where a user session runs (restart on exit 70 only, 5 in 5 min), XDG autostart file otherwise and for a clone. Proven in WSL on a scratch user with the published AppImage. PR #92.
<!-- SECTION:FINAL_SUMMARY:END -->
