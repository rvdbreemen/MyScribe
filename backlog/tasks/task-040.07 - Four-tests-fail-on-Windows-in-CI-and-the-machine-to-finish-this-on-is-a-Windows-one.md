---
id: TASK-040.07
title: >-
  Four tests fail on Windows in CI, and the machine to finish this on is a
  Windows one
status: Done
assignee: []
created_date: '2026-09-19 07:15'
updated_date: '2026-09-19 08:56'
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
- [x] #1 The four failing test names are captured from a Windows run and written into this task
- [x] #2 Each one is understood as either a real Windows bug in the app or a test that only holds on POSIX, and the task says which
- [x] #3 A Windows bug is fixed with a test that fails before and passes after; a POSIX-only assumption in a test is corrected without weakening what the test proves
- [x] #4 One full ci run on GitHub is green on all three operating systems, with its run URL in the notes
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

The names arrived from ubuntu rather than from Windows, and they change the diagnosis.

Run 35428397406, ubuntu-latest, 5 failed:
  tests/test_stage_loudness.py::test_a_louder_look_ahead_leaves_the_windows_features_alone
  tests/test_stage_loudness.py::test_the_floor_holds_whatever_the_look_ahead_says[0.02]
  tests/test_stage_loudness.py::test_the_floor_holds_whatever_the_look_ahead_says[0.2]
  tests/test_stage_loudness.py::test_the_floor_holds_whatever_the_look_ahead_says[1.0]
  tests/test_stage_prepare.py::test_to_wav_maps_the_position_ffmpeg_reports_onto_the_duration

The first four are one failure wearing four names, and they are almost certainly the same four Windows showed: the position estimate in this task pointed at test_stage_loudness.py, and the shape matches exactly - one, then three together (the parametrised trio).

So this was never a Windows bug. The tests asserted bit-equality (np.array_equal) on mel features, which is not a portable claim: the same audio through the same extractor is bit-identical on this Mac - measured, a difference of exactly 0.0 - and differs in the last bits wherever the FFT and the matrix multiply underneath come from a different library. Arithmetic noise, not a raised floor.

They now compare within UNTOUCHED = 1e-4, which sits between the two things being told apart: the bug TASK-036 exists for moves a feature by 0.484 on a scale of -1.2 to 0.8, four thousand times the tolerance. And the companion test that proves the pair cannot both pass by saying nothing was strengthened - it asserted 'not identical', which noise alone would satisfy, and now asserts the distance.

Not verified on Linux or Windows from here; this Mac cannot reproduce the failure, which is the whole point of it. The next ci run on the branch is what confirms it. AC3 and AC4 stay open until then.

Confirmed, and the diagnosis held: run 35430468828 on commit 944bb2c is green on all three - macos-latest 4m0s, windows-latest 22m33s, ubuntu-latest 5m25s. https://github.com/rvdbreemen/MyScribe/actions/runs/35430468828

So the four Windows failures were the four loudness tests after all, and there was never a Windows bug to fix: np.array_equal on mel features is a claim about a BLAS, not about the app. The lead in this task - counted from progress dots, with the uncertainty stated - pointed at the right file.

Windows takes 22 minutes against 4 and 5 for the others, which is the hardlink warning from uv plus the suite in halves. Worth watching, not worth fixing here.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Never a Windows bug. Four tests asserted bit-equality on mel features, which is a claim about whichever FFT and matrix multiply are underneath; they now compare within a tolerance that sits four thousand times below the effect they exist to detect. Confirmed by a green ci run on all three operating systems.
<!-- SECTION:FINAL_SUMMARY:END -->
