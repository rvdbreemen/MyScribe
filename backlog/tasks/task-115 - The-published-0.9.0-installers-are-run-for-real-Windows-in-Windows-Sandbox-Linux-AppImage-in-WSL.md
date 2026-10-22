---
id: TASK-115
title: >-
  The published 0.9.0 installers are run for real: Windows in Windows Sandbox,
  Linux AppImage in WSL
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T18:19:00Z'
updated_date: '2026-10-22T21:24:00Z'
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
- [x] #1 Linux: the 0.9.0 AppImage verifies against SHA256SUMS and starts with chmod +x alone, or the missing piece is named
- [x] #2 Linux: 0.9.0 started over a home made by 0.8.6 updates it and /health says 0.9.0
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

2026-10-22 Linux evidence (WSL Ubuntu, fake HOME ~/mstest/home with XDG dirs inside it, port 4300; scripts in scratchpad itest/scripts). L2: 0.8.6 first start headless: /health 0.8.6 after 8 min (20:23:21 -> 20:31:30); first-run questions not asked (no terminal). L3: 0.8.6 stopped by SIGTERM; 0.9.0 on the same home synced (pillow 12.3.0 -> 13.0.0, yarl 1.24.5 -> 1.25.1), /health 0.9.0, schema user_version 18 before and after. L5 (0.9.0): POST /settings/autostart 303; ~/.config/autostart/myscribe.desktop Exec=.../MyScribe-0.9.0-linux-x64.AppImage --at-login --no-browser --headless. L6 (0.9.0): --stop on a running job: 'MyScribe stopped. A job that was running is back at the front of the queue', job (1,'queued'); the login command started at once: 'Port 4300 is used by another program; MyScribe cannot start.' -> TASK-118. Candidate 0.9.1 (rehearsal run 39843057516, sha256 74bbd174...9400 matches its .sha256): L4a /health 0.9.1 on the old home; L4b FAIL: the login entry still names the 0.9.0 AppImage after the update -> TASK-119; L4d --stop requeued the running job; L5a/L5b: --stop, port 4300 then held by three TIME-WAIT connections, the same 0.9.1 started at once served (0 refusals); L5c job (1,'done'); L5d /media/1 contains panic, harmless, 42. Not verified: a real XDG autostart at a login; a windowed start on WSLg.

2026-10-22 after the reboot (boot 22:31): Robert's login entry fired but MyScribe did not run. D:/Data/MyScribe/logs/launcher.log: 'MyScribe stopped unexpectedly (exit code 1073807364)' (0x40010004, the app ended by the shutdown) then 'Port 4242 is used by another program; MyScribe cannot start.' Cause: the TCP dynamic port range on this machine started at 1024 (64511 ports, set before this session, by whom unknown); with the container feature enabled vmcompute/hns/winnat run and reserved 4084-4883 in blocks of 100 (netsh excludedportrange), which covers 4242; a bind to 127.0.0.1:4242 failed with 'forbidden by its access permissions' (WSAEACCES). SYSTEM CHANGE, with Robert's choice: netsh int ipv4 and ipv6 set dynamicport tcp start=49152 num=16384 (the Windows default), net stop/start winnat; afterwards the exclusions were 2869, 5357, 5985, 47001, 50000-50059 (administered), 50131 and nothing near 4242. Revert: netsh int ipv4 set dynamicport tcp start=1024 num=64511 and the same for ipv6. MyScribe started with the Run value's command served 0.9.0 after 16 s, launcher and both pythons hwnd=0. Windows Sandbox: Containers-DisposableClientVM reports Enabled, RestartNeeded False, but there is no WindowsSandbox.exe or wsb.exe, no appx (Get-AppxPackage -AllUsers *Sandbox* empty), no Start menu entry, winget/msstore none; not pursued further. TASK-112.01 AC4 (a real login) is still not shown: the entry fired, the start died on the reserved port.

2026-10-22 23:22, Robert's real machine, with his OK: the 0.9.1 rehearsal installer (run 39843057516, sha256 d1378ae2...a9e7) with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG over his running 0.9.0 (no job running or queued). Setup exit 0; setup log 'Setup version: Inno Setup version 6.7.1', 'RedirectionGuard status for current process: Not enabling' (no command-line flag); MyScribe-launcher.log 'setup: MyScribe is running (process 33548); asking it to stop.' and 'MyScribe stopped; a job that was running starts over at the next start.'; /health 0.9.1 after 15 s with launcher_pid 22404 = MyScribe.exe --at-login --no-browser --headless; launcher and both python children Enforce=0 hwnd=0; launcher.log 'Checked 124 packages' then 'MyScribe is running'. Log: scratchpad itest/results-real/setup-091-over-090.log. Next, with Robert's OK: a second local Windows user for the 0.8.6 -> published 0.9.0 reproduction, a job during an update, uninstall while running, and a fresh home.
<!-- SECTION:NOTES:END -->
