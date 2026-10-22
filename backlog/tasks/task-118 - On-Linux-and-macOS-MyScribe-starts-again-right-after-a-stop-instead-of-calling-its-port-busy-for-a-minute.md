---
id: TASK-118
title: >-
  On Linux and macOS MyScribe starts again right after a stop, instead of
  calling its port busy for a minute
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T19:21:00Z'
updated_date: '2026-10-22T20:13:00Z'
labels:
  - packaging
  - linux
  - macos
  - bug
dependencies: []
priority: high
ordinal: 232000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-22 in WSL with the published AppImages (TASK-115). After a server on the port stops, the TCP connections it closed stay in TIME_WAIT on its port for about a minute. The launcher's port_taken() binds without SO_REUSEADDR, which on Linux (and other POSIX systems) fails while such a TIME_WAIT exists, so a start in that minute says 'Port 4300 is used by another program; MyScribe cannot start.' and exits; uvicorn itself sets SO_REUSEADDR and would have bound. Seen twice: 0.9.0 started right after 0.8.6 stopped, and the login command started right after 0.9.0's own --stop. Measured: ss showed 'TIME-WAIT 127.0.0.1:4300 127.0.0.1:39746' and the port was free 56 s after /health went. Windows is not affected the same way and must not get SO_REUSEADDR, which there lets a socket take a port that is in use.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 port_taken sets SO_REUSEADDR on POSIX only, so a port left in TIME_WAIT is free and a listening port is still taken; a POSIX test fails without it
- [x] #2 In WSL, --stop followed at once by a start serves again
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Merged in PR #81 (CI green on windows/macos/ubuntu). Linux before: FAILED test_a_port_a_stopped_server_left_in_time_wait_is_free; after: 4 passed; Windows 3 passed 1 skipped. Real run with the 0.9.1 rehearsal AppImage in WSL: right after --stop, ss showed three TIME-WAIT connections on 127.0.0.1:4300, and the same AppImage started at once served /health 0.9.1 with 0 'is used by another program' lines; the job --stop had requeued ran and ended done.
<!-- SECTION:NOTES:END -->
