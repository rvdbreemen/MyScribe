---
id: TASK-009
title: 'applog: an append-only application log with a live page in the top menu'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-05 20:49'
updated_date: '2026-09-05 21:57'
labels:
  - web
  - ops
dependencies: []
ordinal: 50000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Job events live in SQLite per job and are blind before a job exists (record start/chunks/finish, upload, enqueue) and when the runner child dies before its first stage - supervisor spawns the runner with no stdout/stderr, so a DLL or import failure vanishes. One JSONL file under logs/app.log written by web, supervisor and runner; transitions in the app log, progress in the event log, no double bookkeeping. Retention: cap 32 MB with one previous file kept (decided 2026-09-05 after the user left the question open twice). Exposed as Log in the top nav: last ~300 lines on open, live tail every 2 s while the page is shown, filter by level/text, the file path shown for the filesystem.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 scribe/applog.py: log(event, **fields) appends one JSON line with ts, pid, proc, level; secrets (key|token|secret|password|authorization) are redacted by field name
- [x] #2 two processes appending 1000 lines each produce 2000 parseable lines on Windows (no interleaving)
- [x] #3 rotation: at 32 MB app.log becomes app.log.1 (one kept), never unbounded
- [x] #4 instrumented: record start/chunk/finish, recording.finalize (remux exit code), upload/path/url ingest, jobs.enqueue, supervisor claim/spawn(pid)/exit code, runner start/stage begin+end/exit, runner stderr captured into the log
- [x] #5 GET /logs page in the nav; GET /logs/tail?after=<offset> appends new lines every 2 s while open; level and text filter; path shown
- [x] #6 tests cover redaction, rotation, concurrent append, the tail route, and that runner stderr reaches the log
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. scribe/applog.py: JSONL under logs/app.log; log(event, level, **fields); redaction by field name; O_APPEND write under a one-byte msvcrt/flock lock (O_APPEND alone splices on Windows - shown by the two-process test before the lock); rotate at 32 MB keeping app.log.1; tail(after, limit) reads back from the end. 2. Instrument: app.start/stop; jobs.enqueue; supervisor claim/spawn(pid)/exit with runner stderr captured to logs/runner-<job>.stderr and its tail in runner.exited; runner start/stage.begin/stage.end/job.failed/exit/crashed; record start/chunk(debug)/finish/failed, remux; ingest.upload/path. 3. scribe/web/logs_ui.py: /logs (last 300, filters), /logs/tail?after= (only new rows + OOB offset), Log in NAV. 4. tests/conftest.py autouse fixture redirects LOGS_DIR per test (190 test lines had reached the real log). 5. Tests: applog 10, web_logs 7, supervisor stderr 2.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decision 2026-09-05: retention 32 MB + one previous file (user: '32mb is voldoende'). Windows finding: os.open O_APPEND + os.write still interleaves across processes (CRT seek+write); fixed with msvcrt.locking on byte 0 as a shared mutex. Live check on 4299: /logs page 200, Log in nav, poll appended a record.start row within 2.6 s (rows 1->2, offset 230->362). Real app.log had 190 lines from test runs before the autouse fixture; file removed and stays at 1 line after suites.

Lock moved to app.log.lock: Windows byte-range locks are mandatory (a read of app.log failed with Permission denied while byte 0 was held) and LK_LOCK waits in 1 s steps up to 10 s; web_ai went 29 s -> >200 s and back to 28.7 s after the move to a separate lock file with 2 ms non-blocking retries (2 s cap, then write anyway). Verified: applog 10 (incl. 2x1000 lines from two processes, all parsed), web_logs 7, supervisor 7 (fake runner printing 'DLL load failed while importing ctranslate2' found in runner.exited.stderr), live tail on 4299 (1->2 rows in 2.6 s), full suite green in parts: a-i 571, j-r 407, s-u 199, web 416.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
scribe/applog.py writes one JSON line per event from web, supervisor and runner into logs/app.log (32 MB cap, one previous file, secrets redacted by field name, cross-process mutex on app.log.lock). Instrumented: app start/stop, enqueue, supervisor claim/spawn/exit with the runner's stderr captured, runner start/stage/exit/crash, recorder start/chunk/finish/remux, upload and path ingest. /logs page in the nav shows the last 300 lines, filters on the server, and appends new rows every 2 s via /logs/tail. Verified by 19 new tests, a live browser poll, and a fake runner whose stderr reason appears in the log.
<!-- SECTION:FINAL_SUMMARY:END -->
