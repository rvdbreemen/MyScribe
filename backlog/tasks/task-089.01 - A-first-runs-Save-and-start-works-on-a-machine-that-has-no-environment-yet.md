---
id: TASK-089.01
title: A first run's 'Save and start' works on a machine that has no environment yet
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 04:25'
labels:
  - packaging
  - bug
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 138000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every release user walks this path, and it cannot work. begin() shows the dialog and then starts one thread whose target is `(run_setup(...), launch.run())` (packaging/launcher/myscribe_launcher.py:651-668). run_setup (:488-497) runs the command that setup_command builds, `layout.env_python -m scribe.setup` (:466-485, the command itself at :474), but that python exists only after the sync, and the sync lives inside Launch.run() (:433-438). On a fresh home the Popen raises FileNotFoundError on the worker thread, the tuple is never finished, and launch.run() is never called. Nothing catches it, and a windowed build has no stderr.

What the user sees: they answer the four questions, press 'Save and start', and the window stays on 'Saving your answers...' for ever. Only 'Skip for now' gets them an app. Because skipping writes no stamp, the dialog returns on the next start, where Save then works - so the happy path only works on the second attempt, after a skip.

Three readers established this at function level on 2026-09-20, by calling the launcher's own run_setup on a layout without an environment: FileNotFoundError (WinError 2), and launch.run not called. Nobody has seen it in a real Tk window. CI cannot see it either: `--smoke` returns before run_window (:717-728).

This ships alone as a patch release. It lives under this parent only, not also under TASK-040 (brief: W7).

Needs a real machine: Robert, at a Windows screen with a fresh --home. The real Tk window has never been run by anybody.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A test in tests/test_launcher.py drives the first-run sequence on a layout with no environment and a fake uv. It is red on today's code (FileNotFoundError, launch.run never called) and green after. Both outputs are shown.
- [x] #2 The sequencing lives in a plain function that imports no Tk and takes its ask and report callables as arguments; begin() only calls it.
- [x] #3 With answers given, the order of effects is prepare_home, install_tools, sync, scribe.setup, app start. The test asserts that order.
- [x] #4 A non-zero exit or an exception from scribe.setup is reported as an 'error' state with its exit code, and the app still starts. It is never silent.
- [ ] #5 Needs a person at the screen: one recorded real first run on Windows with a fresh --home - dialog, 'Save and start', app running. Robert does this. If it was not done, the task notes say so plainly and this box stays unticked.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Split Launch.run() in two without changing its contract: prepare() does running_instance/port_taken, prepare_home, install_tools, _release_frozen_dll_directory and the sync (setting self.serving when another instance answers); start_app() does AppProcess + wait_ready + browser. run() stays 'prepare() -> serving, else start_app()' so run_headless, --sync-only/--doctor/--smoke and every existing test see the same behaviour. No existing test is expected to move; one that does moves with a stated reason.
2. New plain module function first_run(launch, ask, report, force_setup, at_login) -> bool. Order: launch.prepare(); if wants_setup(...) then answers = ask(); if answers, apply them; then launch.start_app(). It imports no Tk and touches no Tk object (ADR-011 + ADR-015: the launcher renders and hands over, it decides nothing).
3. run_setup returns the exit code instead of a bool (one caller, no test pins it). first_run reports ('error', text naming the exit code) on non-zero and ('error', ...) on OSError from the Popen, then starts the app anyway; its own return value is start_app()'s. Never silent (AC #4).
4. run_window: begin() becomes one thread whose target is first_run; ask is a callable that marshals ask_setup onto the Tk thread with root.after(0, ...) and blocks the worker on a queue until the dialog closes. No sequencing left in begin() (AC #2).
5. Tests in tests/test_launcher.py, on a layout with no environment and the existing fake uv: (a) the order prepare_home, install_tools, sync, scribe.setup, app start, asserted as a recorded list (AC #1, #3) - setup_command is faked to [sys.executable, -c, ...] because _fake_uv creates no runnable env python, so the real run_setup/run_streaming path is still exercised; (b) a setup exiting non-zero: an 'error' report carrying the code, and the app started anyway (AC #4); (c) a setup whose Popen raises: same, not silent (AC #4); (d) Skip (ask returns None) starts the app and runs no setup; (e) first_run's source imports no Tk (AC #2, the half a test can reach).
6. Red first, and two reds because one test cannot be both: the new order test is red on today's code with AttributeError (there is no first_run); the bug the criterion names is reproduced separately against the unmodified launcher - run_setup on an env-less layout raises FileNotFoundError (WinError 2) and launch.run is never reached. Both outputs kept and quoted in the notes.
7. Prove the order test bites by mutating a copy of the repo (scribe/, tests/, packaging/, pytest.ini) outside the repository - move the setup call before the sync - and running tests/test_launcher.py there.
8. AC #5 needs a person at the screen: the code is built, the exact steps and expected output go in the notes (fresh --home, a real Tk window, Save and start, app running), and the box stays unticked until Robert has run it.
9. Out of scope, reported not fixed: ask_setup still hard-codes provider=ollama and tier=turbo, so the first Save now writes a provider row nobody chose - that is TASK-089.25 and it becomes reachable for the first time because of this fix. Also out of scope: --headless still asks nothing, and Quit while the dialog is open (TASK-089.15, M10).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (agent, 2026-09-22)

Evidence directory: `C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/96fe3055-12a8-4348-a17a-5699a082adf4/scratchpad/build/TASK-089.01/`.
Every pytest run below had `SCRIBE_DATA_DIR` and `SCRIBE_ENV_FILE` pointed at that directory (TASK-090 fence).

### What changed

`packaging/launcher/myscribe_launcher.py`

- `Launch.run()` split in two without changing its contract. `prepare()` does
  running_instance / port_taken / prepare_home / install_tools /
  `_release_frozen_dll_directory` / sync and returns False when there is nothing to
  start; the already-serving case sets the new `self.serving` and returns True.
  `start_app()` does AppProcess + wait_ready + browser. `run()` is now
  `prepare() -> serving? -> start_app()`, so `run_headless`, `--sync-only`,
  `--doctor`, `--smoke` and the existing second-launch test see the same behaviour.
  No existing test moved.
- New module function `first_run(launch, ask, report, force_setup, at_login)`:
  prepare, then (if `wants_setup`) ask, then apply, then `start_app()`. It imports
  no Tk and touches no Tk object (ADR-011 stdlib-only; ADR-015 front-ends only
  render - `ask` and `report` are the only things that draw anything).
- `run_setup` returns the exit code instead of a bool. The new `_apply_setup`
  reports `('error', ...)` naming the code on non-zero and `('error', ...)` naming
  the reason on an `OSError` from the Popen, then lets the app start anyway. It
  never raises and never falls silent (AC #4).
- `run_window`: `begin()` is one thread whose target is `first_run`. `ask` marshals
  `ask_setup` onto the Tk thread with `root.after(0, ...)` and blocks the worker on
  a queue until the dialog closes; `tk.TclError` / `RuntimeError` (Quit during the
  sync, no mainloop left to hand the call to) returns None instead of killing the
  worker.

`tests/test_launcher.py` - six new tests, one new helper `_drive_first_run`.

### Red first, twice - one test cannot be both

1. `red-bug-todays-code.txt` - **the bug**. `reproduce_bug.py` builds the exact
   worker-thread target `begin()` started on 2026-09-20,
   `lambda: (run_setup(...), launch.run())`, on a layout with no environment:
   `FileNotFoundError: [WinError 2] The system cannot find the file specified` at
   `myscribe_launcher.py:327` (`run_streaming`'s Popen), `launch.run reached: []`.
   That is the 'Saving your answers...' that never ends. Run against the
   unmodified file, before any edit.
2. `red-test-first-run.txt` - **the test**. The six new tests on today's code:
   `AttributeError: module 'myscribe_launcher' has no attribute 'first_run'`.
   `6 failed, 33 passed, 1 skipped in 11.74s`.

### Green

    .venv/Scripts/python -m pytest tests/test_launcher.py -q --no-header -p no:cacheprovider
    39 passed, 1 skipped in 15.65s          -> green-test_launcher.txt

    .venv/Scripts/python -m pytest tests/test_proxy.py -q --no-header -p no:cacheprovider
    28 passed in 15.68s                     -> green-test_proxy.txt

`tests/test_proxy.py` is the only other file that loads the launcher module
(`grep -rn myscribe_launcher tests/`). Baseline before this work was 33 passed,
1 skipped; the 6 that were added are the 6 new ones. Nothing was deleted or loosened.

### The order test bites (mutation, outside the repository)

`scribe/`, `tests/`, `packaging/` and `pytest.ini` copied to `.../TASK-089.01/mut/`;
`mutate.py` asserts the text is there and moves the sitting back in front of
`launch.prepare()` with a `# MUTANT` marker. Run there with the project's venv python:

    2 failed, 37 passed, 1 skipped in 16.37s        -> red-mutation.txt
    assert order == [...]  ->  At index 0 diff: 'scribe.setup' != 'prepare_home'
    assert asked == []     ->  assert [1] == []

`grep -rn "MUTANT" scribe tests packaging` in the repository finds nothing.

### What the tests do and do not prove

- `test_the_first_run_syncs_before_it_applies_the_answers` asserts the recorded list
  `["prepare_home", "install_tools", "sync", "scribe.setup", "app start"]` (AC #1, #3).
  `setup_command` is faked to `[sys.executable, "-c", ...]`: `_fake_uv` cannot build a
  runnable environment python, so the real command would raise FileNotFoundError after
  the fix too, for the wrong reason. The real `run_setup` / `run_streaming` path still
  runs - the test asserts the streamed line `setup: answers saved` came back. The
  docstring says so; do not read it as proof that the real command line was exercised.
- AC #2's real proof is that these tests drive `first_run` end to end with no Tk root
  anywhere. `test_the_sequence_is_free_of_tk` only guards the source against an
  `import tkinter` creeping back. "begin() only calls it" cannot be reached by any
  automated test (the closure needs a Tk root): it is verified by the diff and by AC #5.

### AC #5 - not done, nobody has run it

Needs Robert at a real Windows screen. No agent can drive a modal Tk dialog here, and
`--smoke` returns before `run_window`, so CI cannot see it either. The box stays unticked.

Steps:

1. Build, or run from the checkout with a payload:
   `.venv/Scripts/python packaging/launcher/myscribe_launcher.py --home D:\tmp\MyScribe-firstrun --port 4299`
   (a `--home` that does not exist yet, and a port that is not 4242).
2. Expected, in this order: the window opens on "Preparing...", then the headline
   "Installing the speech engine. The first start downloads about 3 GB..." with uv's
   lines in the log - several minutes.
3. Only then does "Set up MyScribe" open. Fill in a token or leave it blank, press
   **Save and start**.
4. Expected: the headline becomes "Saving your answers...", `scribe.setup`'s lines
   appear in the log, then "Starting MyScribe..." and finally
   "MyScribe is running at http://127.0.0.1:4299/", with "Open MyScribe" enabled.
5. Quit. `D:\tmp\MyScribe-firstrun\data\setup.json` exists, so a second start opens no
   dialog.

The visible change to name in the release notes: the dialog now comes *after* about
3 GB of download instead of before it. That is the design's answer (spec §4, steps 2-3):
the answers can only be applied by a python the sync creates.

### Found, not fixed (outside this task's criteria)

- **TASK-089.25 becomes reachable on the happy path for the first time.** `ask_setup`
  hard-codes `provider="ollama"` and `tier="turbo"`, so the first successful
  'Save and start' now writes `llm_provider=ollama` for somebody who chose nothing -
  what ADR-016 / R1 forbids. Before this fix a first run never reached `run_setup`.
  This ships alone as a patch release, so it would reach every release user.
- **Quit while the dialog is open** still starts the app with no window left:
  `root.destroy()` releases `wait_window`, the answers go on the queue and the worker
  carries on. The same hole exists today (`begin()` starts its thread regardless), so
  it is not a regression - TASK-089.15 (M10).
- **`--headless` and a Tk-less start ask nothing.** `run_headless` calls
  `launch.run()`, which has no sitting. Spec §3.11 gives the console asker to TASK-089.15.

### Addendum: three things the runs above do not cover

- `tests/test_launcher.py` collected 34 tests before this work and 40 after
  (`--collect-only -q`), which is the anchor for the 33/39 passed lines: one test is
  skipped off macOS. The brief's "27 tests" was already stale before this task.
- **No test has ever exercised `run_headless`** (`grep -rn run_headless tests/` finds
  nothing). Its path through the new `prepare()`/`start_app()` split is verified by
  reading and by `run()`'s unchanged shape - `launch.app` is still set only by
  `start_app()`, so the "another instance is serving" check still reads True - not by a
  test. Stated, not claimed as covered; adding that test is outside these criteria.
- The apply step is its own function `_apply_setup` so `first_run` reads as five steps
  and the try/except does not bury them. The sequencing itself is still one plain
  Tk-free function, which is what AC #2 asks.
- The `ask()` marshalling in `run_window` (`root.after(0, ...)` from the worker, blocking
  on a queue) has never run against a real Tk root. AC #5 is its only verifier - the same
  as for "begin() only calls first_run".

## Review round (agent, 2026-09-22)

Three verifiers read the change. Five findings were fixed, four rejected or left
with a reason. No acceptance criterion was ticked and nothing was committed.

### Changed

1. **`--setup` against a running MyScribe is no longer dropped in silence**
   (major, found twice). `first_run` returned at the serving branch before
   `wants_setup` was ever consulted, so a flag somebody typed did nothing and
   the launcher reported success. It now says so instead (`:693`): "MyScribe is
   already running, so --setup did not open the questions: quit MyScribe first
   and start it again with --setup."

   That is a deliberate product change and belongs in the release note: until
   this task the dialog came first and `scribe.setup` ran beside the serving
   app. Re-opening it there was weighed and rejected on evidence, not on
   taste - `prepare` has already reported `done`, and the window's pump does
   `root.after(3000, root.destroy)` on that state (`:874`), so the dialog would
   be pulled away three seconds after it opened and the worker would wait on
   its answer queue for ever. Making it work needs the pump to stop
   auto-closing while a sitting is pending, which is TASK-089.15's dialog and
   Robert's call.

   Red first: `red-review-setup-while-serving.txt` - `assert []`, no report
   named `--setup`; 1 failed, 41 passed, 1 skipped. Test:
   `test_setup_against_a_myscribe_that_already_serves_is_said_out_loud`.

2. **`--setup` and `--at-login` are pinned all the way to the gate** (major).
   Nothing stopped `first_run` dropping them: the mutation that replaced
   `wants_setup(layout, force=..., at_login=...)` with `wants_setup(layout)`
   left the suite green, and a login would have opened the modal sitting
   TASK-089.21 landed to prevent. Two tests now bite it, one per direction:
   `..._at_login_asks_nothing_all_the_way_through_the_sequence` and
   `test_setup_reopens_the_sitting_although_the_stamp_is_there`. The hop above
   them, `run_window` -> `first_run`, still needs Tk and is still untested.

3. **`_apply_setup` catches every exception** (`:720`), not the Popen's
   `OSError` alone: its docstring promises 'never raises', and on a worker
   thread of a windowed build anything that escapes is a window that stopped
   with nothing written anywhere. A malformed command raises `ValueError`, and
   `test_a_setup_that_fails_in_a_way_nobody_expected_is_reported_too` drives
   exactly that. `KeyboardInterrupt` and `SystemExit` still travel.

4. **`run_window`'s `ask()` always answers** (`:896`). The old guard covered
   scheduling the dialog, not running it: an exception inside the Tk callback
   went to Tk's own handler - stderr, which a windowed build has not got - and
   the worker waited for ever. The callback is now a named function that puts
   exactly one value on the queue on every path. No timeout was added: a person
   may sit at those questions for minutes. Verified by reading only; no test
   reaches `run_window`.

5. **The skip test sees the absence instead of inferring it.** `run_setup` is
   what `_drive_first_run` records now, so 'no setup ran' is an assertion
   (`"scribe.setup" not in order`) rather than the lack of an error report.

### Not changed, with the reason

* `test_the_sequence_is_free_of_tk` only greps the source for 'tkinter'. Left
  as it is: its docstring already says that is all it guards, and the real
  proof is the behaviour tests driving `first_run` with no Tk root. An AST
  guess dressed as a guard would be worse.
* AC #1's literal wording (the pytest red carries `AttributeError`, not the
  `FileNotFoundError` the criterion names). Nothing to build: the two reds are
  named above and a verifier confirmed the bug proof resolves to HEAD's own
  line numbers.
* An `error` text is overwritten in the status line seconds later by 'Starting
  MyScribe...' and survives in the log pane; `pump` gives no state special
  treatment. Real, and outside these criteria - it is a change to how the
  window renders states.
* `ask_setup` still hard-codes `provider="ollama"` and `tier="turbo"`
  (TASK-089.25). Confirmed again, still the orchestrator's release gate: this
  fix is what makes that row reachable on a first run.

### Evidence

Every run with the TASK-090 fence exported, one test file per process, output
to `.../TASK-089.01/`.

    .venv/Scripts/python -m pytest tests/test_launcher.py -q --no-header -p no:cacheprovider
    43 passed, 1 skipped in 20.50s          -> final-test_launcher.txt

    .venv/Scripts/python -m pytest tests/test_proxy.py -q --no-header -p no:cacheprovider
    28 passed in 15.40s                     -> final-test_proxy.txt

`test_proxy.py` was re-run because the earlier `green-test_proxy.txt` was
written before the last edit to the launcher. The two files are still the only
ones that load the launcher module (`grep -rln myscribe_launcher tests/`).

The mutation copy was rebuilt from this tree (`mut-verify/`, outside the
repository) and four one-behaviour mutations were run there: baseline 43
passed; 5 (a skip applied as an answer), 9 (the two flags dropped), 10 (the
catch narrowed to `OSError`) and 11 (the `--setup` sentence removed) each turn
exactly their own test red - `mut-verify2-0-baseline.txt`, `-5`, `-9`, `-10`,
`-11`. `grep -rn MUTANT scribe tests packaging` in the repository finds nothing.

### AC #5, amended for whoever runs it

The recipe above still holds for the first run. Three additions, none of which
any automated test can see:

* With MyScribe running, start it again with `--setup`: expect no dialog, the
  sentence 'MyScribe is already running, so --setup did not open the
  questions...' on the status line, and the window closing about three seconds
  later.
* Quit while the dialog is open, and Quit during the sync: `ask()`'s
  marshalling is the one mechanism AC #5 alone verifies.
* `--setup` is windowed-only. `main()` plumbs it to `run_window` and nowhere
  else, so a machine without tkinter falls through to `run_headless` and is
  asked nothing. Pre-existing, reported not fixed.

One more for the release note: the error sentence interpolates the exception,
and today `setup_command` still carries `--hf-token <value>`. No exception
`subprocess` raises here is known to carry the argument list, but the surface
closes for good when TASK-089.15 moves the answers to stdin.

### Two corrections to the round above (same session)

* The refusal sentence was shortened to "MyScribe is already running; quit it
  first and start again with --setup to answer the questions." It lives for the
  three seconds the `done` state gives the window, and the log pane is not teed
  to a file until TASK-089.15, so the sentence has to be readable in one look.
  Whoever runs AC #5 should confirm it is: that is the whole of 'not silent'
  here.
* `wants_setup`'s docstring said `--setup` always wins, which this change makes
  untrue at one branch. It now carries the exception. The predicate itself is
  unchanged and its test still pins `wants_setup(layout, force=True) is True`.

Evidence after both: `final-test_launcher.txt` 43 passed, 1 skipped in 20.03s;
mutation 11 re-run against the new sentence, `mut-verify2-11.txt`, 1 failed,
42 passed, 1 skipped - the same test and no other.

## Verification (orchestrator, 2026-09-22)

Checked by hand, not taken from the build report.

**AC #1's two reds.** The pytest red is the new test being new
(`AttributeError`, there is no `first_run` yet). The `FileNotFoundError` the
criterion names is in `reproduce_bug.py`'s output, and its traceback lands on
HEAD's own code, which I resolved myself:
`git show HEAD:packaging/launcher/myscribe_launcher.py | sed -n '636,640p;325,329p'`
gives `code = run_streaming(` at 638 and `proc = subprocess.Popen(` at 327 -
the two frames in the traceback. Its last lines read "thread alive after 30s:
False / raised: FileNotFoundError(2, ...) / launch.run reached: []". Both halves
of the criterion are shown, on today's code, by two files. Ticked.

**AC #2.** `first_run` is at `myscribe_launcher.py:671`. Grepping its body for
`tk|tkinter|root|dialog` finds two hits and both are prose in the docstring
("Tk-free on purpose", "would mean a dialog"); no Tk name is touched.
`begin()` is one `threading.Thread` whose target is `first_run` and holds no
sequencing at all (:922-932). Ticked. The `ask` closure inside `run_window` is
verified by reading only - no test reaches it - and the notes above say so.

**AC #3.** `test_the_first_run_syncs_before_it_applies_the_answers` asserts
`order == ["prepare_home", "install_tools", "sync", "scribe.setup", "app start"]`
and also that `("busy", "setup: answers saved")` came back, so the command
really ran through `run_streaming` rather than being counted as run. Ticked.

**AC #4.** `_apply_setup` catches every `Exception` and reports the exit code
or the reason, then `first_run` returns `launch.start_app()` either way
(:705, :713-729). Three tests, one per shape. Ticked.

**AC #5 stays unticked.** Nobody has run the real Tk window. It needs Robert
at a Windows screen with a fresh `--home`; the steps are in the notes above.

**`Launch.run()`'s contract is unchanged**, which `run_headless` (:732) and the
`--sync-only/--doctor/--smoke` paths depend on: `run()` is now
`prepare() -> serving? -> start_app()`, and `_release_frozen_dll_directory()`
still sits in `prepare()` before the sync (:572), where it was.

**Tests, by me, with the TASK-090 fence exported** (`SCRIBE_DATA_DIR=C:\ms-f`,
`SCRIBE_ENV_FILE=C:\ms-f\empty.env`), one file per process:
`tests/test_launcher.py` 43 passed, 1 skipped in 20.83s;
`tests/test_proxy.py` 28 passed in 16.13s. Whole suite, 80 files, each in its
own process: 2747 passed, 10 skipped, 0 failed, 0 errors. That reconciles exactly with collection: `pytest -q --collect-only tests` in one process reports "2757/2767 tests collected (10 deselected)", and 2747 + 10 = 2757, so every collected test ran and none was lost to a stall. `tests/test_launcher.py` is the only test file this task changed (`git status`), and it carries the four new tests.

**Not fixed, and it gates the release.** `ask_setup` still has
`provider = tk.StringVar(value="ollama")` (`myscribe_launcher.py:788`) and
`tier` on "turbo". This fix is what makes a first run reach `scribe.setup` on
the happy path for the first time, so the first "Save and start" now writes
`llm_provider=ollama` for somebody who chose nothing - which ADR-016 forbids
and TASK-089.25 exists to remove. This must not be released before TASK-089.25
lands. Verified by reading the line, not taken from the report.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A first run's 'Save and start' now starts something. Launch.run() is split into prepare() and start_app() with its contract unchanged, and a new Tk-free first_run(launch, ask, report, force_setup, at_login) runs the sequence prepare, install, sync, ask, apply, start; run_window's begin() is one thread that only calls it, with ask marshalling the dialog onto the Tk thread and always answering. run_setup returns its exit code and _apply_setup reports any failure as an 'error' naming the code or the reason, then starts the app anyway. Verified: the FileNotFoundError [WinError 2] is reproduced against HEAD's own code (traceback frames resolved by hand to HEAD lines 638 and 327), the new test is red with AttributeError before and green after, and four mutations on a copy outside the repository each kill their own test. Suite with the fence, one file per process: 2747 passed, 10 skipped, 0 failed, which reconciles exactly with 2757 collected. AC #5 stays unticked - nobody has run the real Tk window; the steps are in the notes. Gate for the release: ask_setup still preselects provider=ollama (myscribe_launcher.py:788), and this fix is what first makes a first run reach scribe.setup, so this must not ship before TASK-089.25.
<!-- SECTION:FINAL_SUMMARY:END -->
