---
id: TASK-125
title: Every Windows install ends with MyScribe open in the browser
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-23T04:13:00Z'
updated_date: '2026-10-23T05:04:00Z'
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

Live on Robert's machine: the final candidate (cdbf7725) and then the published v0.9.1 installer (20b3cdba, matches SHA256SUMS) over his running MyScribe with Start at login on: exit 0, 'asking it to stop' / 'MyScribe stopped; a job that was running starts over', restarted as MyScribe.exe --at-login --headless (hwnd=0, no --no-browser), launcher.log 'Opening MyScribe in your browser. If no browser window appears, go to http://127.0.0.1:4242/'. Not run: a fresh install (the window path); webbrowser.open answering True is the launcher's evidence, the tab itself was not observed by me. Released in 0.9.1 (tag v0.9.1, tree 05defa7d, the same tree as the tested candidate 51ea3e2).
<!-- SECTION:NOTES:END -->
