---
id: TASK-089.22
title: 'The installer asks whether MyScribe starts at login, and the default is No'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-23 20:01'
labels:
  - packaging
  - ux
dependencies:
  - TASK-089.11
  - TASK-089.21
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 159000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 as one of three extra questions, with the default No (brief: R3). It is a thin layer over TASK-089.21, which has to exist first (brief: U3): the installer may only ask what Settings can also answer (scribe/setup.py:21-23).

Why it belongs in the sitting: it is the natural follow-up to the watch-folder question of TASK-089.20. A folder that ingests by itself only does so while the app runs, and a first-time user does not know that.

Default No, because starting a program at every login is something a person opts into. A model-loading app that holds VRAM is not something to find running by surprise.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The question is in `--plan` with default No, skippable, and its if_skipped sentence says that watch folders and feeds only work while MyScribe runs.
- [x] #2 Yes does exactly what the Settings switch of TASK-089.21 does, through the same function. A test shows the registered item is identical.
- [x] #3 No and skip register nothing. Skip is recorded as skipped (TASK-089.11) and does not return at every start.
- [x] #4 It is only present when the OS reports no login item for MyScribe. A re-run shows the current state instead of asking.
- [x] #5 `--non-interactive` and `{}` answers register nothing, and a test asserts that no OS call was made.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. scribe/setup.py, each addition in its own block marked TASK-089.22: import scribe.autostart; Answers gets a last field start_at_login: bool | None = None (None = not answered, like diarize); helper _login_entry() wraps autostart.status() and returns None when the OS read raises OSError/ValueError; helper _yes_no(value) maps yes/true/1 and no/false/0 (strings or booleans) to True/False, anything else to None.
2. _questions(): after fetch_models, question id start_at_login, kind yes-no, choices yes/no, default "no", present only when the entry is off AND there is a command to register (start_command not None). if_skipped: nothing is written; watch folders and feeds only work while MyScribe runs, so until it is started by hand nothing is watched. answer_later: Settings > Start at login.
3. plan(): append one found row of kind "login" (label Start at login, found = entry.on, source = where when on, note = the command as the OS holds it) so a re-run shows the current state; render() and the launcher already print a row with these keys generically. Left out of found_table(), which is the credentials view.
4. from_document(): start_at_login = _yes_no(given.get("start_at_login")); a null answer stays a skip through the existing path.
5. apply(): one branch before the download block. Yes or no both go to answered (a No must not come back at every start, AC3); yes calls autostart.enable() with no arguments - the exact call set_autostart in scribe/web/settings.py makes - and reports where it wrote; NothingToStart/OSError/ValueError become a note plus reopen, the sitting still ends.
6. tests/test_setup_plan.py, at the END under a TASK-089.22 comment: an autouse fixture that puts a recording in-memory mechanism in autostart.default_mechanism and a fixed command in autostart.start_command, so no test in this file reads or writes this machine's Run key. Tests: AC1 present/default no/skippable/if_skipped words, Enter at the console gives no; AC2 yes calls enable() with no arguments and the recorded write equals what autostart.enable() alone writes; AC3 no and null write nothing, null is stamped skipped and dropped by --unasked-only, no is stamped answered; AC4 entry on -> no question and the found row says on and where, nothing to start -> no question, an OS read that raises -> no question and the plan survives; AC5 {} and {"contract":2,"answers":{}} on --apply-stdin and the no-terminal sitting: the spy recorded no write, and for --apply-stdin no call at all.
7. Two mid-file one-liners in tests/test_setup_plan.py, flagged for the merge: LATER_ROUTES gains "start_at_login": ("POST", "/settings/autostart"); the plan-shape test's found-kinds set gains "login".
8. tests/test_web_settings.py, at the END under a TASK-089.22 comment: through its _FakeMechanism, POST /settings/autostart enabled=1 and setup.apply(Answers(start_at_login=True)) leave a byte-identical value - AC2's identical item, both doors on one fake OS.
9. Evidence in the scratch dir: red-first output for AC1/AC5 before the engine change; fenced per-file runs of test_setup_plan.py, test_web_settings.py, test_autostart.py, test_setup.py, test_launcher_sitting.py; one mutation on a copy (drop the enable() call) shows AC2 bites, grep MUTANT empty afterwards; a read-only winreg check that the real HKCU Run key holds no MyScribe value before and after.
10. Not closed here: a real login on macOS/Linux (TASK-089.21's unverified state, unchanged); no launcher or packaging file is touched.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
TASK-089.22 built in worktree MyScribe-wt-22 (branch task-089-installer-first-slice at ea33210); nothing committed.

Changed:
- scribe/setup.py: imports scribe.autostart; Answers.start_at_login (bool or None, last field); question 13 start_at_login (yes-no, choices yes/no, default no, if_skipped: nothing is written, watch folders and feeds only work while MyScribe runs; answer_later: Settings > Start at login) appended at the end of _questions() only while autostart.status() reports off AND a start command exists; helpers _login_entry() (OSError/ValueError from the OS read -> None: no question, no row, the plan survives), _login_row() (found row kind "login" with the usual keys plus on/where/command) and _yes_no(); plan() appends the login row so a re-run shows the state; from_document() maps the answer (null stays a skip); apply() records No as answered and on Yes calls autostart.enable() with no arguments - the call set_autostart in scribe/web/settings.py makes - with NothingToStart/OSError/ValueError becoming a note plus reopen. The _questions docstring no longer says start at login is not built here. CONTRACT not raised (see below).
- tests/test_setup_plan.py: block at the end (TASK-089.22) with _RecordingMechanism as an autouse fixture (no test in the file reads or writes the real Run key) and 8 tests / 10 cases for AC1-AC5; two mid-file one-liners: LATER_ROUTES gains start_at_login -> POST /settings/autostart, the plan-shape test's found kinds gain "login"; imports subprocess and autostart.
- tests/test_web_settings.py: block at the end, test_the_installer_and_the_switch_register_the_identical_item (POST /settings/autostart enabled=1 and setup.apply(Answers(start_at_login=True)) on one _FakeMechanism leave a byte-identical value).

Evidence in scratchpad/build/TASK-089.22/ (every pytest run fenced with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE, one test file per process, output to file):
- red, before the engine change: red-test_setup_plan.txt 9 failed / 111 passed; red-test_setup_plan-os-will-not-answer.txt 1 failed (after giving that test an open-first precondition, because it passed trivially before); red-test_web_settings.txt 1 failed / 54 passed (TypeError: Answers has no start_at_login).
- green: test_setup_plan 120 passed; test_web_settings 55 passed; test_autostart 22; test_setup 15; test_launcher_sitting 55; test_credentials 35; test_doctor 64; test_dotenv_commands 9 (4 warnings); test_env 43 (8 skipped); test_proxy 29; test_setup_prove 43; test_launcher 99 (1 skipped). green-final-test_setup_plan 120 and green-final-test_setup 15 after a docstring reword.
- mutation on a copy under the scratch dir (the enable() call dropped, the report still claims a write): mutant-enable-dropped-test_setup_plan.txt 1 failed (test_yes_calls_the_same_enable_the_settings_switch_calls_with_no_arguments: seen == [] instead of [((), {})]), 10 passed; mutant-enable-dropped-test_web_settings.txt 1 failed (value None instead of the switch's). grep -rn MUTANT scribe tests packaging in the worktree: nothing.
- real machine, read-only, fenced: real-plan.txt (python -m scribe.setup --plan) has the login row found=False, where=HKCU\...\Run\MyScribe, command naming this worktree's scripts/start.ps1, and the question with default no; real-render.txt (stdin on NUL) prints "Start at login  not found", lists the question, asks nothing and writes no stamp.
- hkcu-run-before.txt / hkcu-run-after.txt: the real HKCU Run key holds 14 values and no MyScribe value, both before and after (winreg EnumValue names only, nothing written).

For the orchestrator:
- CONTRACT stays 2. Its docstring says to raise it whenever a question is added, but TASK-089.18, .20 and .22 add questions in parallel and the number must rise once. Until it does, the launcher's gate does not reopen a finished contract-2 sitting for this question; plan --unasked-only still lists it and --setup asks it.
- Merge points outside the delimited blocks: the setup.py import line and the _questions docstring sentence (TASK-089.20 edits the same sentence); in test_setup_plan.py the two import lines, LATER_ROUTES and the found-kinds set.
- --non-interactive belongs to install.py (TASK-089.17) and is not in scribe.setup here; AC5 is tested on the engine-side shape (an --apply-stdin document with no answers: no OS call of any kind). The no-terminal sitting reads the entry - a plan has to, to show the state - and writes nothing.
- graphify update not run (rule: never touch graphify-out/). tests/test_ingest_recording.py and tests/test_web_transcribe_dialog.py mention autostart but scribe/autostart.py is unchanged, so they were not run.
- Not closed here: a real login on macOS or Linux (TASK-089.21's unverified state); no launcher or packaging file touched.

Merged 2026-09-23 by the orchestrator. Built in MyScribe-wt-20 and MyScribe-wt-22 on ea33210 and never committed; brought onto f729d96 (after TASK-089.17, .18, .10, .26 and 090) with a three-way apply. Conflicts were all append-beside-append in scribe/setup.py and tests/test_setup_plan.py, resolved by keeping both, plus: the import line (accel, autostart and scribe.llm.ollama together), plan()'s found row (089.22's list with the login row, and 089.18's ollama arguments), the found-kinds set (credential, proxy, watch_folder, login), and one _questions docstring sentence that each task wrote about the other. CONTRACT raised once, 3 -> 4, for the two questions together, as both build notes asked. Fenced, one file per process, after the merge and the bump: test_setup_plan 170 passed, test_web_settings 55, test_setup 15, test_setup_prove 43, test_launcher_sitting 55, test_launcher 102 passed 1 skipped, test_autostart 22, test_ingest_watching 84, test_install 56. The builders' mutants were run on their own trees; the merged blocks are unchanged from those. Known and accepted: the Tk sitting renders no text entry, so through the launcher's window the watch-folder question can only be skipped; the console, --apply-stdin and install.py take a path.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The sitting asks whether MyScribe starts at login, default No, only while the OS holds no entry, and a Yes makes the exact autostart.enable() call the Settings switch makes (a byte-identical registry value on one fake mechanism). No and skip register nothing and do not return; an empty document makes no OS call at all. The real HKCU Run key was read before and after and held no MyScribe value. Merged onto the release branch with CONTRACT 4.
<!-- SECTION:FINAL_SUMMARY:END -->
