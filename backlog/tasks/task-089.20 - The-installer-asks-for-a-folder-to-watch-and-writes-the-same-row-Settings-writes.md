---
id: TASK-089.20
title: >-
  The installer asks for a folder to watch, and writes the same row Settings
  writes
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-23 20:00'
labels:
  - ingest
  - settings
  - ux
dependencies:
  - TASK-089.11
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 157000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 as one of three extra questions (brief: R3, U4). 'How do recordings get in?' is the first thing a new user needs answered, and today the answer is a separate thing afterwards: find Settings, find the watch-folder form. His rule: rather a few extra questions during installation.

The counterpart in Settings exists already, which scribe/setup.py:21-23 requires before an installer may ask. Settings adds a `watch_folder` row through watching.add_folder (scribe/ingest/watching.py:284-304), and each row carries its own transcribe options, 'because nobody is at the dialog when the file lands' (scribe/web/settings.py:27-35). The question writes that same row through that same function, and nothing else.

The form refuses four things, each a mistake the user can fix from where they stand. Three of them, and a blank check, are in settings.parse_watch_path (scribe/web/settings.py:599-640): a path that is not an absolute, existing directory; a path outside the browse roots; a path inside the data directory. The fourth, a path already watched, is only in that function's docstring: the refusal itself is in the POST handler add_watch_folder, which turns the UNIQUE column's sqlite3.IntegrityError into a 409 '... is already being watched' (:664-667). parse_watch_path raises FastAPI's HTTPException, and the engine is not a web request. The installer applies the same four, and does not invent a fifth or skip one.

One of them bites on the reference machine. On Windows the default browse root is the drive the profile lives on, and nothing else (scribe/fsbrowse.py:44-53). A user whose recordings live on D: and whose profile is on C: is refused with 'outside the folders this app may read from; widen them under Settings'. That is the documented boundary of what the app may read, so the installer does not widen it by itself; it says the sentence and says where.

The watch folder's own options include diarize, which defaults to True (scribe/options.py:42). A machine with no token would therefore lose whole transcriptions from a watched folder today. TASK-089.08 is what makes this question safe to skip the token beside; it is built first.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The question is skippable, default skip. Skipping writes nothing and is recorded as skipped (TASK-089.11). Its answer_later line names Settings > Watch folders.
- [x] #2 A given folder is written as a `watch_folder` row through watching.add_folder, with the current transcribe defaults as its options. A test shows the row is identical to the one POST /settings/watch writes for the same input.
- [x] #3 The same four refusals apply, with the same sentences: the three of settings.parse_watch_path (scribe/web/settings.py:599-640) - not an absolute existing directory, outside the browse roots, inside the data directory - and the duplicate refusal of add_watch_folder (:664-667), which the engine gets from the same sqlite3.IntegrityError out of watching.add_folder. The engine maps HTTPException.detail to its sentence, or the checks move into a plain function that both callers use; either way no FastAPI type reaches the asker. One test per refusal, and a refused folder writes nothing and reopens the question.
- [x] #4 A folder outside the browse roots is refused and not written, and the sentence says how to widen the roots in Settings. The installer never writes fsbrowse_roots by itself. On Windows a test covers a folder on a second drive.
- [x] #5 The question is only present when no watch_folder row exists yet, and a re-run shows the folders that are watched instead of asking.
- [x] #6 Nothing is ingested during the sitting. The watcher thread picks the folder up when the app starts, as it does for a row added in Settings, and a test shows a file dropped in the folder becomes a recording after the start.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. scribe/web/settings.py: one plain function `add_watched(conn, raw, options) -> Path` beside parse_watch_path that runs the three checks and turns watching.add_folder's sqlite3.IntegrityError into the 409 sentence; add_watch_folder (:691-709) calls it. The four refusals then live in one place (spec 3.10).
2. scribe/setup.py, own delimited block "12. the watch folder (TASK-089.20)": Answers gets `watch_folder: str = ""`; from_document reads text("watch_folder"); `_watch_folders(conn)` reads `SELECT path FROM watch_folder` on the read-only conn ([] for None or sqlite3.Error, like _row); `_questions` appends Question(id="watch_folder", kind="text", choices=[], default=None, answer_later="Settings > Watch folders") only when no row exists; found_table appends a row kind "watch_folder" listing the paths when rows exist (a re-run shows them, criterion 5).
3. apply(): one block before fetch_models. `add_watched(conn, answers.watch_folder, transcribe_dialog.read_defaults(conn))`; HTTPException is caught there and only its .detail goes to report["notes"], the id to report["reopen"] (stamp state "open", asked again); success -> wrote/answered. Nothing else is written; fsbrowse_roots never.
4. Existing tests that pin the contract get one-word extensions: test_setup_plan.py:122 found kinds + "watch_folder", :142 kinds + "text", LATER_ROUTES + ("POST", "/settings/watch").
5. New tests at the END of tests/test_setup_plan.py under "# --- the watch folder (TASK-089.20)": #1 present with default None and skip writes nothing + stamps skipped; #2 engine row == POST /settings/watch row byte-for-byte (path, enabled, options_json) with default_tier=max and default_diarize=0 stored first; #3 one test per refusal, sentence asserted equal to what settings.parse_watch_path raises / the route's 409 detail, no row, reopen; #4 outside roots names Settings, no fsbrowse_roots row, plus a Windows test on os.listdrives() minus the profile drive (drive root, no writes; skipped with one drive); #5 a library with a row asks nothing and found shows the path, render() prints it; #6 apply leaves media empty, then create_app(db_path, start_supervisor=False, start_watcher=True) under TestClient on the scratch data dir with a settled file dropped before start; poll <=15 s for the media row.
6. Why in-process for #6: `python -m scribe --no-supervisor` starts no watcher (app.py:238-239) and without the flag the supervisor hands the queued job to a runner child that loads a model on a scratch dir with no weights. The lifespan with start_watcher=True is the real thread and the real observer; the CLI has no switch for it (reported, not added).
7. Runs: fence exported (SCRIBE_DATA_DIR, SCRIBE_ENV_FILE), one file per process, output to build/TASK-089.20/*.txt: test_setup_plan.py, test_ingest_watching.py, test_web_settings.py, test_setup.py, test_launcher_sitting.py. Mutations on a copy under build/TASK-089.20/mut: (a) engine passes TranscribeOptions() instead of read_defaults -> #2 red; (b) the row-count predicate dropped -> #5 red; (c) refusal note replaced -> #3 red. grep MUTANT over the worktree empty afterwards.
8. Not closable here: a person typing a path in the Tk sitting - the launcher renders secret/yes-no/radio only (myscribe_launcher.py:2165-2200), so through that door the question can only be skipped; a `text` entry there is a launcher change outside this task. Console asker and --apply-stdin take the path.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built 2026-09-23 in worktree MyScribe-wt-20 (branch task-089-installer-first-slice at ea33210), test first. Not committed; criteria not checked; not Done.

WHAT CHANGED
- scribe/web/settings.py: one plain function `add_watched(conn, raw, options) -> Path` beside `parse_watch_path` runs its three checks and turns `watching.add_folder`'s IntegrityError into the 409 "... is already being watched" (spec 3.10). `add_watch_folder` calls it. Only behavioural change in the route: `parse_options` now runs before the path check, so when both the path and an option field are wrong the option is named first.
- scribe/setup.py, one delimited block per site, all marked "12. the watch folder (TASK-089.20)": `Answers.watch_folder: str = ""` (appended); `_watch_folders(conn)` beside `_row` ([] for no database or sqlite3.Error; every row counts, an off row reads "path (off)"); `found_table` appends one row kind "watch_folder", label "Watch folders", source = the paths; `_questions` appends Question(id="watch_folder", kind="text", choices=[], default=None, answer_later="Settings > Watch folders") only while no row exists; `apply()` calls `settings.add_watched(conn, answers.watch_folder, transcribe_dialog.read_defaults(conn))` after the tier block (so a tier chosen in the same sitting is the row's tier), catches HTTPException and puts only "watch_folder: <detail>" in notes and the id in reopen (stamp "open"); `from_document` reads text("watch_folder"). `scribe.web.settings` and HTTPException are imported inside the block, the way `_queue_doctor_job` does. fsbrowse_roots is never written by any path of the engine.
- tests/test_setup_plan.py: block at the end under "# --- the watch folder (TASK-089.20)" with its own imports and fixtures (roots_cover_tmp, inbox) and 12 tests (14 cases): question open with skip default; skip writes no row and stamps skipped; engine row == POST /settings/watch row byte-for-byte (default_tier=max, default_diarize=0 stored first); one test per refusal asserting the note equals "watch_folder: " + the sentence parse_watch_path itself raises (three inputs for refusal one) or the route's real 409 detail; outside-roots test asserts the roots row is unchanged; Windows second-drive test on os.listdrives() minus the profile drive (ran on this machine: D:\ refused, no roots row); refusal reopens through --apply-stdin and the next unasked-only plan; a library with a row (on, then off) is not asked and the found row names the path; render() prints "Watch folders"; criterion 6 with the real lifespan. Three existing pins widened by one word: found kinds + "watch_folder", question kinds + "text", LATER_ROUTES + ("POST", "/settings/watch").

CRITERION 6, FLAGS: the proof runs `create_app(db_path=paths.DB_PATH, start_supervisor=False, start_watcher=True)` under TestClient on the test's own data directory: the real Watcher thread, the real watchdog observer and the startup reconcile take a settled file (mtime 60 s old) in within the 15 s poll (took under 2 s), one media row and one queued transcribe job, never claimed. A `python -m scribe` child cannot show this: `--no-supervisor` switches the watcher off with it (app.py create_app, start_watcher follows start_supervisor) and without it the supervisor spawns a runner child that loads a model (ADR-001) on a scratch dir with no weights. The CLI has no watcher-without-supervisor switch; adding one is outside this task.

RUNS (all fenced: SCRIBE_DATA_DIR and SCRIBE_ENV_FILE exported; one file per process; outputs under scratchpad/build/TASK-089.20/)
- red-test_setup_plan.txt: 14 failed, 110 passed in 22.05s (TypeError unexpected kwarg watch_folder; question absent; found row absent).
- green-test_setup_plan.txt: 124 passed in 17.92s.
- importers of scribe.setup / scribe.web.settings: test_setup 15 passed; test_setup_prove 43 passed; test_credentials 35 passed; test_doctor 64 passed; test_dotenv_commands 9 passed, 4 warnings; test_env 43 passed, 8 skipped; test_proxy 29 passed; test_web_settings 54 passed; test_ingest_watching 84 passed; test_launcher 99 passed, 1 skipped; test_launcher_sitting 55 passed.
- mutations on a copy (mut/, scribe imports verified from the copy): (a) TranscribeOptions() instead of read_defaults -> 1 failed (byte-for-byte: tier turbo != max, diarize True != False); (b) row-count predicate dropped -> 2 failed (not asked / render); (c) refusal note replaced -> 8 failed (every refusal test). grep -rn MUTANT scribe tests packaging in the worktree: empty.
- e2e.txt, real doors on a scratch data dir (never the live library, no model, no network): --plan lists the question and creates no file; --apply-stdin with "D:\" under default roots prints "watch_folder: D:\ is outside the folders this app may read from; widen them under Settings", "still open: watch_folder", stamp open; with a real folder: "saved: watch_folder", one row with the stored defaults, media rows 0, fsbrowse_roots None, stamp answered; the next --plan has no watch_folder question and a found row; the bare run prints "Watch folders  <path>" under Found.

LEFT / FOR THE ORCHESTRATOR
1. CONTRACT not raised. Its docstring says "Raise it whenever a question is added to `_questions`"; three tasks (089.18, .20, .22) add questions in parallel, so one bump 2 -> 3 at the merge is the orchestrator's call, not three. Without it a machine stamped under contract 2 is not shown the new question at a start (the launcher's gate compares numbers); `--setup` still lists it.
2. The Tk sitting has no entry for kind "text" (launcher renders secret/yes-no/choice only): the question shows as a label with the skip box, and `answer_of` returns None for the empty StringVar, so through the window it is always stamped skipped. Console asker and --apply-stdin take a path. A text entry is a launcher change (TASK-089.15's file), outside this task.
3. graphify update not run: graphify-out/ is off limits under the hard rules.
4. ADR judge not run here (the orchestrator commits); the diff adds no import or pattern ADR-001/013/014/015 forbid.

Merged 2026-09-23 by the orchestrator. Built in MyScribe-wt-20 and MyScribe-wt-22 on ea33210 and never committed; brought onto f729d96 (after TASK-089.17, .18, .10, .26 and 090) with a three-way apply. Conflicts were all append-beside-append in scribe/setup.py and tests/test_setup_plan.py, resolved by keeping both, plus: the import line (accel, autostart and scribe.llm.ollama together), plan()'s found row (089.22's list with the login row, and 089.18's ollama arguments), the found-kinds set (credential, proxy, watch_folder, login), and one _questions docstring sentence that each task wrote about the other. CONTRACT raised once, 3 -> 4, for the two questions together, as both build notes asked. Fenced, one file per process, after the merge and the bump: test_setup_plan 170 passed, test_web_settings 55, test_setup 15, test_setup_prove 43, test_launcher_sitting 55, test_launcher 102 passed 1 skipped, test_autostart 22, test_ingest_watching 84, test_install 56. The builders' mutants were run on their own trees; the merged blocks are unchanged from those. Known and accepted: the Tk sitting renders no text entry, so through the launcher's window the watch-folder question can only be skipped; the console, --apply-stdin and install.py take a path.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The first-run sitting asks for a folder to watch, default skip, and writes the row Settings writes through the same checks: settings.add_watched now holds the four refusals for both doors. Proven by a byte-for-byte row test against POST /settings/watch, one test per refusal including a second drive, a re-run that shows the watched folders, and the real watcher taking a file in at start; three mutants killed. Merged onto the release branch with CONTRACT 4.
<!-- SECTION:FINAL_SUMMARY:END -->
