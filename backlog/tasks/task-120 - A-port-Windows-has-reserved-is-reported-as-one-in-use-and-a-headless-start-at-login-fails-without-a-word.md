---
id: TASK-120
title: >-
  A port Windows has reserved is reported as one in use, and a headless start at
  login fails without a word
status: To Do
assignee: []
created_date: '2026-10-22T20:45:00Z'
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
- [ ] #1 A bind refused with EACCES/WSAEACCES gets its own sentence naming the reservation and the way out; a test covers it
- [ ] #2 Robert decided how a failed headless login start is shown to a person
<!-- AC:END -->
