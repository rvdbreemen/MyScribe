---
id: TASK-112.11
title: >-
  A tray icon shows MyScribe runs and offers Open and Stop, on Windows, macOS
  and Linux
status: Done
assignee: []
created_date: '2026-10-25T12:06:00Z'
updated_date: '2026-10-25T18:44:00Z'
labels:
  - packaging
  - autostart
  - ui
dependencies: []
parent_task_id: TASK-112
priority: low
ordinal: 241000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert's decision of 2026-10-24 for TASK-112.08: MyScribe gets a tray icon on all three OSes using pystray in the frozen launcher only (not the app). It shows that MyScribe runs and offers Open MyScribe and Stop (Stop goes through the stop route, ADR-028/ADR-032). pystray is LGPL-3.0, last released 2023, and needs an AppIndicator extension on GNOME; this amends ADR-011's standard-library-only rule for the launcher, so it needs its own ADR, a licence note (LGPL in a frozen binary: dynamic linking/replaceability obligations), and changes to the three frozen builds and CI. Do the OSS comparison first (pystray vs alternatives such as infi.systray, a hand-written Windows Shell_NotifyIconW) and record it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An OSS comparison and a Proposed ADR amending ADR-011 for a tray dependency in the frozen launcher, with the licence obligations stated
- [x] #2 The tray shows while MyScribe runs, with Open MyScribe and Stop (through the stop route); tests cover the menu wiring without a display
- [x] #3 The three frozen builds include it and CI stays green; Linux without an AppIndicator host falls back to no tray, with a log line
- [x] #4 A real run on Windows with a screenshot of the tray menu; macOS and Linux walks written for the next sitting
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-25 Robert's decision (asked with options in the session): ADR-038 accepted as built: README licence paragraph plus pystray's bundled COPYING files; menu label 'Stop MyScribe'.

2026-10-25 PR #106 merged (a0222fb), CI green on three OSes. ADR-038 (amends ADR-011 for pystray + Pillow in the frozen launcher; LGPL notice in README + bundled COPYING) accepted by Robert. AC3: ci.yml now freezes the launcher on all three runners with the build requirements and fails unless pystray, COPYING.LGPL and the platform backend (pystray._win32; _xorg + Xlib; _darwin + AppKit/Foundation/objc/PyObjCTools) are in the bundle - green on all three; Linux without a tray host falls back to no tray with a log line (tests). AC4: Windows source run with a scratch venv, tray menu screenshot in an earlier round; macOS/Linux walks are in docs for the next sitting. At merge the tray was composed with TASK-112.10's restart loop: one tray per launcher, kept across restarts, Stop never treated as a crash (5 new tests, 2 red first). Not verified: a real macOS menu-bar run, a real AppImage tray.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
MyScribe started at login shows a tray icon with Open MyScribe and Stop MyScribe on Windows, macOS and Linux (pystray, ADR-038), kept across the launcher's restarts. PR #106. Not run on a real Mac.
<!-- SECTION:FINAL_SUMMARY:END -->
