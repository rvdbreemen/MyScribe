---
id: TASK-120
title: >-
  A port Windows has reserved is reported as one in use, and a headless start at
  login fails without a word
status: Done
assignee: []
created_date: '2026-10-22T20:45:00Z'
updated_date: '2026-10-25T16:09:00Z'
labels:
  - packaging
  - windows
  - bug
dependencies: []
priority: medium
ordinal: 234000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-22 on Robert's machine (TASK-115). With Hyper-V or the container feature on and a TCP dynamic port range starting at 1024, Windows reserved 4184-4283 (netsh int ipv4 show excludedportrange protocol=tcp), so binding 127.0.0.1:4242 fails with WSAEACCES (10013). port_taken() treats every OSError as taken, and the launcher said 'Port 4242 is used by another program; MyScribe cannot start.' - nobody is using it, and the advice that follows from that sentence is wrong. Started at login headless, the only trace is that line in launcher.log: MyScribe simply is not there. The sentence should name the reservation and what to do (netsh show excludedportrange; another port with --port, or the dynamic range back to 49152), and a login start that cannot serve should leave something a person sees next time (Settings, or the window of a manual start).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A bind refused with EACCES/WSAEACCES gets its own sentence naming the reservation and the way out; a test covers it
- [x] #2 Robert decided how a failed headless login start is shown to a person
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): option C: always a failure record shown at the next start and on the Settings card (dismissable, cleared after a clean start), plus an immediate notification at login on Windows.

2026-10-25 Robert's decision (asked with options in the session): the Windows login notice stays; record it in ADR-039 (amends ADR-026's no-window Must for a failed login start) and accept it.

2026-10-25 PR #104 merged, CI green on three OSes. AC1: a refused bind where nothing answers a connect gets the reserved sentence (netsh int ipv4 show excludedportrange, --port, the dynamic range back to default); a program that answers (incl. SO_EXCLUSIVEADDRUSE on 0.0.0.0, measured) is 'used by another program' - the reviewer's false-diagnosis finding fixed red first; troubleshooting.md has the row. AC2: Robert chose option C; built on TASK-112.07's login note: the next start by hand and the Settings card show it (dismissable), a clean start clears it, and on Windows a MessageBox shows at a failed login start, closing itself after 600 s; ADR-039 (amends ADR-026's no-window Must) accepted by Robert. Not verified: a real logout/login, the released exe, a real 10-minute box.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A port Windows reserved is named as reserved with the way out, and a login start that cannot serve says so: a box at login on Windows, the next start and the Settings card everywhere. ADR-039. PR #104.
<!-- SECTION:FINAL_SUMMARY:END -->
