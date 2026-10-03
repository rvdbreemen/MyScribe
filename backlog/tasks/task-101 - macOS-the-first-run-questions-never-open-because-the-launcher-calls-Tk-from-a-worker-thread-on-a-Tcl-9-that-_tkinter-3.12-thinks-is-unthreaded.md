---
id: TASK-101
title: >-
  macOS: the first-run questions never open, because the launcher calls Tk from
  a worker thread on a Tcl 9 that _tkinter 3.12 thinks is unthreaded
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-03 19:37'
updated_date: '2026-10-03 21:21'
labels:
  - installer
  - macos
  - bug
dependencies: []
priority: high
ordinal: 175000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reported by an outside acceptance walk of the v0.8.0 macOS dmg on macOS 26.0 / Apple Silicon (Jim, 2026-10-03; report in docs/reports/2026-10-03-macos-0.8.0-acceptance.md once that patch lands). On a fresh home the window installs the environment (2,767 MB) and then hangs on "Installing the speech engine..." for good: no questions window, no app child, nothing on 4242. --setup on the same home hangs on "Preparing...". Three runs, identical. The console door on the same home works. A person cannot set MyScribe up on that Mac.

Cause (verified in three places, not yet reproduced here):
1. The sample of the hung process shows libtcl9.0.dylib, and the main thread in _tkinter_tkapp_mainloop -> __select: that is the non-threaded branch of the mainloop (Tcl_DoOneEvent(TCL_DONT_WAIT) + Sleep via select). The worker thread waits for ever in a PyThread lock (answered.get() in run_window's on_tk).
2. CPython 3.12's _tkinter decides "threaded" from tcl_platform(threaded), a variable Tcl 9.0 removed. On 3.12 with Tcl 9 every Tkapp is therefore treated as non-threaded, and a call from another thread is not marshalled to the Tk thread but runs directly in the caller.
3. CPython fixed this in gh-124111 (GH-128103) for 3.13 and later (TCL_MAJOR_VERSION >= 9 -> threaded = 1); the 3.12 branch does not have it. build_release.py freezes the launcher with uv-managed Python 3.12, whose macOS build carries Tcl 9.0.

So on_tk's root.after(0, run), called from the worker, registers a Tcl timer in the worker thread (Tcl timers are per thread), the Tk thread never runs it, and nothing ever answers. A windowed build has no stderr, so it leaves no trace. Windows' managed 3.12 carries Tcl 8.6 (threaded) and is not affected; Linux is not checked.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A failing test first shows that on_tk / the window's cross-thread requests reach the Tk thread without the worker calling any Tk or Tcl method itself
- [x] #2 Every Tk call the worker needs goes through the main thread's existing pump (or an equivalent main-thread queue), so the result no longer depends on how _tkinter judges Tcl's threading
- [x] #3 An exception on the worker or in a Tk callback is written to <home>/logs/launcher.log (threading.excepthook and Tk's report_callback_exception), so a windowed build leaves a trace
- [x] #4 Decided and recorded whether the launcher is also frozen with Python 3.13+, with the Tcl version per platform measured
- [ ] #5 Evidence: the suite's summary line, and a real windowed first start on a fresh home reaching the questions; on macOS this needs a person with a Mac (Jim), and the task says so plainly if that has not happened
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red first in tests/test_launcher_sitting.py: the fake Root records which thread calls after(); a first_run that calls ask() must get the dialog's answer back while every after() comes from the Tk (main) thread. Today on_tk calls root.after from the worker, so this fails.
2. on_tk no longer touches Tk: it puts the request on a plain queue.Queue that pump (already on the Tk thread, every 200 ms) drains and runs, answering on the request's own queue. No Tk or Tcl call is left on any worker, so how _tkinter judges Tcl's threading no longer matters.
3. A trace for a windowed build: in_a_worker logs an uncaught exception's traceback to launcher.log (redacted) and reports one line in the window; root.report_callback_exception does the same for a Tk callback. Tests for both, red first.
4. Python version: measure Tcl per platform of the managed 3.12 (Windows 8.6 measured; Linux via WSL); record whether moving the freeze to 3.13 is worth it, and leave that as a separate decision rather than doing it here.
5. Evidence: red/green output, the two launcher test files, the suite in halves; a real windowed start of the source launcher on a scratch home on Windows reaching the questions. macOS needs Jim; said plainly.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Tcl per platform in python-build-standalone 20261003, cpython 3.12.15 install_only (listed with tar -tzf): aarch64-apple-darwin libtcl9.0.dylib; x86_64-unknown-linux-gnu libtcl9.0.so; x86_64-pc-windows-msvc tcl86t.dll. So macOS and Linux freeze with Tcl 9 (Linux's window path is exposed to the same hang, not walked); Windows has threaded 8.6. Jim's sample shows libtcl9.0.dylib under the shipped 3.12.14. The repo .venv is python.org 3.12.9 (Tcl 8.6), so a local run cannot reproduce the hang.
Decision: the fix is in the code (no Tk call on any worker), which holds whatever Python freezes the launcher. Moving the freeze to 3.13+ (gh-124111 fixed there) is not done here; it would change the shipped interpreter for all three platforms and deserves its own task.
Real Tk on Windows (python.org 3.12.9, Tcl 8.6): run_window with a worker asking for the real ask_setup; the dialog 'Set up MyScribe' was drawn on MainThread, viewable, and the answer reached the worker after 3.56 s (the script closed it at 2.5 s).
Flaky, not this change: test_launcher.py running_instance tests failed 2 of 3 on main (stashed) and once on this branch, then passed 3 times in a row here.

Red (3 new tests, before the fix): worker_touching_tk 'Tk was called from [MainThread, myscribe-launch]'; worker_that_dies FileNotFoundError, no launcher.log; tk_callback_that_raises: no report_callback_exception. Green after: 3 passed.
AC3 is met by catching in in_a_worker (both worker threads) and root.report_callback_exception, not by a process-wide threading.excepthook: the two places cover every thread the window starts, and nothing global is left changed after a test.
Suite, one file per test file (the halves stalled on socketpair at 420 s each): 3573 passed, 9 skipped, 3 failed in test_models.py, test_web_phone.py timed out at 240 s. Alone and with nothing else running: test_models.py 49 passed; test_web_phone.py stalled once more, then passed 33 on the docs branch (no fix) and 33 on this branch. Neither file touches the launcher.
Open: AC5's macOS half. A windowed first start on a fresh home on macOS needs a frozen build with this fix and a Mac (Jim).

Released as stable 0.8.1 (Robert chose stable over 0.8.1b1: 0.8.0 does not work on a Mac at all, a beta would keep the download link there). PR #2 merged as 9a3abb4; ci.yml green on windows/ubuntu/macos (run 37153134731); tag v0.8.1 -> 9a3abb4; release.yml run 37154228681: version, three builds with smoke, publish all success; Release v0.8.1 is Latest, not a pre-release, 7 assets. Windows exe checked here: sha256sum -c SHA256SUMS OK, gh attestation verify -> refs/tags/v0.8.1. Still open: AC5 macOS, a person walking 0.8.1's first start on a Mac (Jim).
<!-- SECTION:NOTES:END -->
