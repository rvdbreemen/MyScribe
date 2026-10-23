---
id: TASK-125
title: Every Windows install ends with MyScribe open in the browser
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-23T04:13:00Z'
updated_date: '2026-10-23T04:13:00Z'
labels:
  - packaging
  - windows
dependencies: []
priority: medium
ordinal: 239000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-10-23: 'Zorg dat aan het eind van installeren van myscribe altijd de browser opstart met myscribe.' Before: a [Run] checkbox 'Start MyScribe' (postinstall skipifsilent, so skipped by /SILENT and /VERYSILENT), and after an update with Start at login on a headless restart with --no-browser. Now CurStepChanged(ssPostInstall) starts it in one place: the login item's way without --no-browser when a stopped MyScribe had its Run value (the headless launcher opens the browser once it serves), otherwise MyScribe.exe with no arguments (the window; it opens the browser once it serves, or at once when one already runs). The [Run] section is gone, so two launchers never race for the port.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 myscribe.iss has no [Run] entry and starts MyScribe in CurStepChanged both ways; text tests fail without it
- [ ] #2 A real install of the candidate over a running MyScribe opens MyScribe in the browser, and a fresh install does too
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
tests/test_uninstall_text.py: test_setup_always_ends_with_myscribe_open_in_the_browser and the updated headless-restart test red first (2 failed), then 10 passed with test_installer_redirectionguard; ISCC compiles (exit 0). Docs: installation.md Updating, README update line, CHANGELOG Changed.
<!-- SECTION:NOTES:END -->
