---
id: TASK-089.01
title: A first run's 'Save and start' works on a machine that has no environment yet
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
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
- [ ] #1 A test in tests/test_launcher.py drives the first-run sequence on a layout with no environment and a fake uv. It is red on today's code (FileNotFoundError, launch.run never called) and green after. Both outputs are shown.
- [ ] #2 The sequencing lives in a plain function that imports no Tk and takes its ask and report callables as arguments; begin() only calls it.
- [ ] #3 With answers given, the order of effects is prepare_home, install_tools, sync, scribe.setup, app start. The test asserts that order.
- [ ] #4 A non-zero exit or an exception from scribe.setup is reported as an 'error' state with its exit code, and the app still starts. It is never silent.
- [ ] #5 Needs a person at the screen: one recorded real first run on Windows with a fresh --home - dialog, 'Save and start', app running. Robert does this. If it was not done, the task notes say so plainly and this box stays unticked.
<!-- AC:END -->
