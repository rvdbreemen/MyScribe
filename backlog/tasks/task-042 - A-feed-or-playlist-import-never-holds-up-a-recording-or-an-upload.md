---
id: TASK-042
title: A feed or playlist import never holds up a recording or an upload
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 22:05'
updated_date: '2026-09-11 22:07'
labels:
  - ingest
  - jobs
dependencies: []
ordinal: 76000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On 2026-09-12 the user found recording "not working" on the MacBook. The recordings had arrived fine (16.4 s at mean -47 dB), but a pasted podcast feed (WHYcast, 47 episodes) had fanned out first, and the queue runs one job at a time in priority-then-FIFO order, so the two recording transcriptions sat behind 47 episode transcriptions - hours. Everything is enqueued at priority 0 today. Work the user starts one item at a time should go ahead of a bulk import.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Jobs fanned out from a playlist or feed, and the transcribe jobs they queue, have a lower priority than the default
- [x] #2 A recording, an upload or a single URL queued after a bulk import is claimed before the remaining bulk jobs
- [x] #3 A retry keeps the priority of the job it retries (as today)
- [x] #4 Tests cover the fan-out priority, the queued transcribe priority and the claim order
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. url_stage.BULK_PRIORITY = -10; _fan_out enqueues children at it. 2. register queues the transcribe job at the ingest job's own priority (bulk for a feed episode, 0 for a single link). 3. claim_next already orders priority DESC, id; the board's queue already sorts the same way. Tests first.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Diagnosis 2026-09-12 on the MacBook (launcher instance on 4242): recording worked - media 50 is 16.4 s at mean -47 dB / max -19 dB (media 49 a 1.3 s near-silent start/stop) - but a pasted WHYcast feed had queued 47 episode transcriptions at priority 0 ahead of the two recording jobs (98, 99). Worked around there by raising 98/99 to priority 10. Red then green: 3 of 4 new tests failed before the change (the single-URL baseline passes). Full suite 1688 passed, 18 skipped. Seen on the way, not changed here: the MLX backend hands the whole file to mlx_whisper.transcribe in one call, so a 100-minute episode shows no progress until it is done (cancel still works through the supervisor's 10 s kill grace).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Playlist and feed entries, and the transcriptions they queue, now run at BULK_PRIORITY (-10), so a recording, upload or single link started later is claimed first; retries keep their priority as before. Found from a user report that recording did not work on the MacBook: the recordings were fine but queued behind 47 feed episodes. Verified with 4 new tests (3 red first) and the full suite (1688 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
