---
id: TASK-040.07
title: >-
  Four tests fail on Windows in CI, and the machine to finish this on is a
  Windows one
status: To Do
assignee: []
created_date: '2026-09-19 07:15'
updated_date: '2026-09-19 07:15'
labels:
  - ci
  - windows
dependencies: []
references:
  - .github/workflows/ci.yml
  - 'https://github.com/rvdbreemen/MyScribe/actions/runs/35427936790'
parent_task_id: TASK-040
priority: high
type: bug
ordinal: 134000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Picked up from a session on a Mac on 2026-09-19, where Windows could not be reached. CI now runs on three operating systems (TASK-040.04) and windows-latest is the only OS still red; ubuntu and macOS were red for reasons already fixed - a float compared exactly against ffmpeg's own reported position (0.4992 on ffmpeg 6.1.1, 30.0 here) and a 30 s wait for the app to come up on a cold runner.

What is known about Windows, from run 35427936790, job 105857055839:

* The first half, tests/test_[a-r]*.py, is green: 1506 passed, 1 skipped, 8 deselected in 216.93 s.
* The second half, tests/test_[s-z]*.py, showed four failures early - one at roughly test 101 of 972 and three together at roughly 109-111 - and then the job was cancelled by a later push before pytest printed its summary, so the names were never captured.
* By position those land in tests/test_stage_finalize.py and tests/test_stage_loudness.py. Treat that as a lead and not as fact: the arithmetic is from counting progress dots.
* Everything before the suite passed on Windows: uv installed, uv lock --check, uv sync --frozen, ffmpeg via choco, and the doctor with all required checks green.

The halves are not a workaround for this. CLAUDE.md documents that the whole-suite run stalls intermittently on Windows - CPython's socketpair emulation behind TestClient, see pytest.ini - and the halves are how that is avoided; the four failures are inside a half that ran to its end.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The four failing test names are captured from a Windows run and written into this task
- [ ] #2 Each one is understood as either a real Windows bug in the app or a test that only holds on POSIX, and the task says which
- [ ] #3 A Windows bug is fixed with a test that fails before and passes after; a POSIX-only assumption in a test is corrected without weakening what the test proves
- [ ] #4 One full ci run on GitHub is green on all three operating systems, with its run URL in the notes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
How to pick this up on a Windows machine, without needing the session this came from.

1. git fetch && git switch feat/feed-discovery-and-model-provisioning
2. uv sync --frozen           (no pip in the venv; ADR-012)
3. .venv\Scripts\python -m scribe.doctor --no-gpu      (proves the machine before the suite; it was green on the runner)
4. .venv\Scripts\python -m pytest -q tests/test_[s-z]*.py
   Never a bare : that is C:\Python312 without the dependencies, and the failure it produces names a missing module rather than the real mistake (CLAUDE.md).
   If the run passes about four minutes without finishing, kill it and split further - the stall is the socketpair one pytest.ini describes, not these failures.
5. Put the four names in this task before fixing anything, so the next person reads facts rather than an estimate.

Getting them from CI instead, without a Windows machine at hand:
  gh run list --branch feat/feed-discovery-and-model-provisioning
  gh run view --job <windows job id> --log | grep FAILED
Only once the run has finished - a cancelled run has no logs, which is how the names were lost the first time. Pushing to the branch cancels any run in flight (concurrency: cancel-in-progress in ci.yml), so let a run end before pushing again if its output is what you are waiting for.

Likely shapes, from what this codebase already knows about Windows, all worth checking before assuming a real bug: a path compared as a string where Windows uses backslashes; a file held open being replaced (the proxy stage and the audio route have hit this); a timing assumption on a slower filesystem; a temporary directory removed while something still has it open. scribe/stages/loudness.py and scribe/stages/finalize.py are where the positions point.
<!-- SECTION:NOTES:END -->
