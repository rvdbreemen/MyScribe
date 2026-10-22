---
id: TASK-115
title: >-
  The published 0.9.0 installers are run for real: Windows in Windows Sandbox,
  Linux AppImage in WSL
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T18:19:00Z'
updated_date: '2026-10-22T18:25:00Z'
labels:
  - packaging
  - release
dependencies: []
priority: high
ordinal: 229000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CI never runs the installer: --smoke starts the frozen launcher, and ISCC builds the .exe without starting it. So CI green says nothing about installing, updating or uninstalling. This task runs the release artifacts users download (gh release download, checked against SHA256SUMS) on this laptop: the Windows .exe in Windows Sandbox (own HKCU, Start menu and LOCALAPPDATA, so an uninstall while running is safe), and the Linux AppImage in WSL Ubuntu with a fake HOME and a port other than 4242. Evidence goes in the notes and in scratchpad itest/. Not provable here: a real login (Windows or XDG autostart); that stays named as not verified.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Linux: the 0.9.0 AppImage verifies against SHA256SUMS and starts with chmod +x alone, or the missing piece is named
- [ ] #2 Linux: 0.9.0 started over a home made by 0.8.6 updates it and /health says 0.9.0
- [ ] #3 Linux: Start at login writes a desktop entry whose Exec line starts MyScribe headless, and --stop on a running job requeues it
- [ ] #4 Windows Sandbox: a fresh install of 0.9.0 starts, serves /health and a page, with nothing else installed first
- [ ] #5 Windows Sandbox: the 0.9.0 installer over a running 0.8.6 with Start at login stops it, installs, and restarts it headless, shown by the install log
- [ ] #6 Windows Sandbox: an update with a job running requeues the job; an uninstall while running stops it, removes the program and the Run value, and keeps the home
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Linux in WSL: fake HOME/XDG dirs, port 4300; download v0.8.6 and v0.9.0 AppImages, verify sums; check libfuse2; first run 0.8.6 to /health, kill; 0.9.0 on same home to /health; windowed start screenshot on WSLg; enable login via setup/settings, read .desktop Exec, run it, queue a job, --stop, restart, see it requeued. 2. Robert reboots for Windows Sandbox (enabled 2026-10-22, needs restart). 3. Sandbox (.wsb: 8 GB RAM, read-only installers folder, writable results folder; drive with wsb.exe if present): A fresh install 0.8.6, login on, then 0.9.0 /VERYSILENT /LOG; B job running, install 0.9.0 again; C uninstall while running. 4. Evidence into notes of this task, TASK-112.04 and TASK-112.01; tick only what was seen.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-22 setup. Windows Sandbox (Containers-DisposableClientVM) enabled through an elevated prompt: State Enabled, a restart is needed before it runs. Artifacts: gh release download v0.9.0 and v0.8.6 (AppImage + windows exe + SHA256SUMS) into scratchpad itest/dl; sha256sum -c SHA256SUMS --ignore-missing: all four OK. Sandbox driver: itest/win/run-all.ps1 (steps A 0.8.6 -> 0.9.0 with login on, B job running during 0.9.0 -> 0.9.0, C uninstall while running, D fresh home --smoke), itest/myscribe-itest.wsb (8 GB, dl and win read-only, results writable, LogonCommand runs the script). Linux: both AppImages run --version with chmod +x alone (static-pie runtime; only libfuse3 on this Ubuntu, no libfuse2 needed): 0.8.6 and 0.9.0 printed, exit 0. Windows 4242 on the host was refused before the tests; WSL uses NAT networking (no mirrored mode in .wslconfig) and the AppImage tests use port 4300.
<!-- SECTION:NOTES:END -->
