---
id: TASK-112.05
title: The macOS LaunchAgent starts the binary itself, headless, with its own log
status: In Progress
assignee:
  - '@claude'
created_date: 2026-10-19 05:51
updated_date: '2026-10-25T19:16:00Z'
labels:
  - packaging
  - autostart
  - macos
dependencies:
  - TASK-112.01
  - TASK-112.02
references:
  - docs/reports/2026-10-19-background-server-research.md
parent_task_id: TASK-112
ordinal: 218000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The plist starts `/usr/bin/open -a <bundle>` (scribe/autostart.py:317), which returns at once, so launchd watches nothing and logs nothing. The research note gives the plist: ProgramArguments to Contents/MacOS/MyScribe, KeepAlive SuccessfulExit false, ProcessType Interactive, AssociatedBundleIdentifiers, StandardOutPath in ~/Library/Logs/MyScribe.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The plist written matches the one in the research note, and a test asserts its keys
- [x] #2 An app running from an AppTranslocation path is not registered, and Settings says why
- [ ] #3 Switching on loads the agent now with launchctl bootstrap, switching off unloads it with bootout
- [x] #4 The macOS walk (log out and in, /health, no window) is added to docs/macos-acceptance.md and reads not run until it is run
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): switch-off removes the plist and runs launchctl bootout, so MyScribe stops now (job requeued, ADR-028); the card warns first. The plist also gets an ExitTimeOut longer than the launcher's longest stop (TASK-112.09).

2026-10-25 PR #96 merged, CI green on three OSes (merged with TASK-119 into one App Translocation mechanism). AC1: plist keys asserted (binary in Contents/MacOS, StandardOut/ErrorPath, KeepAlive SuccessfulExit=false, ExitTimeOut derived from the launcher's LAUNCHER_STOP_WAIT). AC2: a translocated copy is not registered and the card says to move it into Applications (tests). AC4: docs/macos-acceptance.md section 6 holds the walk and reads not run. AC3 stays open: bootstrap on switch-on and bootout on switch-off are built and tested with a recording launchctl only; nothing has run on a Mac or real launchd. The setup sitting's Yes only writes the plist (loads at the next login) so a first start cannot start a second MyScribe. The card now says a running job goes back unless it finishes first (TASK-112.09 branch).
<!-- SECTION:NOTES:END -->
