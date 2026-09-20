---
id: TASK-089.03
title: >-
  The setup, models and doctor commands read .env the way the app does, and a
  blank variable never hides a real value
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-20 21:43'
labels:
  - packaging
  - bug
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 140000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Any installer that writes to `.env` breaks on two facts. scribe/setup.py:34, scribe/models.py:37 and scribe/doctor.py:36 import scribe.paths at module load, and scribe/paths.py:4 fixes DATA_DIR at that moment; each calls env.load_dotenv() only later (setup.py:170, doctor.py:814). scribe/__main__.py:82-86 does it the other way round and says why. So a SCRIBE_DATA_DIR set only in `.env` - the documented way to move the library (.env.example:30) - is honoured by the app and ignored by all three commands.

What the user hits: they move the data directory through `.env` and run setup. The token row, the provider row, the stamp and the downloaded weights land in ./data, and the app, reading the other directory, behaves as if setup never happened. A reader showed it with a scratch env file on 2026-09-20: the file said 'elsewhere', setup wrote to ./data, the app read 'elsewhere'.

Separately, load_dotenv applies the file with os.environ.setdefault (scribe/env.py:65-66). An empty variable exported by a shell, a compose file or a service unit therefore keeps a real token in `.env` from ever arriving.

write_token has the smaller fault its own docstring warns about. It reads utf-8 where env.py reads utf-8-sig, and matches only the exact prefix `HF_TOKEN=` (scribe/setup.py:95, :98). A file Notepad saved with a BOM, or one written as `HF_TOKEN = old`, ends up with two lines for one name.

This comes first and depends on nothing (brief: M9).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Red first: with SCRIBE_DATA_DIR only in a scratch env file (SCRIBE_ENV_FILE), `python -m scribe.setup --status`, `python -m scribe.models` and `python -m scribe.doctor --no-gpu` all use that directory. The scratch-run output is shown before and after.
- [x] #2 Red first: a blank or whitespace-only process variable no longer shadows a non-blank `.env` value. A non-blank process value still wins, and tests/test_web_scaffold.py:218-244 stays green.
- [x] #3 load_dotenv records which names it applied from the file, and a caller can ask which names those were.
- [x] #4 A write to `.env` reads utf-8-sig, matches `NAME = value` with spaces, and never leaves two lines for one name; the BOM case and the spaced case are red first. It goes through a temp file and os.replace, and creates the file 0600 on POSIX, shown in WSL. macOS is not measured, and the notes say so. The writer takes any NAME, not only HF_TOKEN. After TASK-089.09 no secret is written to `.env` any more (its criterion 7), so write_token (scribe/setup.py:84-106) loses its caller. What still writes `.env` is a line that is not a secret: SCRIBE_DATA_DIR, when a clone adopts a library (TASK-089.19), which is the documented way to move one (.env.example:30). If TASK-089.19 decides otherwise, the writer goes with write_token, and the notes say so.
- [x] #5 `<repo>/.tools/bin` is first on PATH for `python -m scribe`, scribe.setup, scribe.models and scribe.doctor when it exists. `.tools/` is in .gitignore.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Mechanism chosen (spec 3.4, option 1): scribe.paths learns to recompute. An AST probe of all 85 files under scribe/ and packaging/ found 0 import-time uses of a paths constant - module level, class body, decorator or default argument (evidence: plan_import_time_paths.txt; the default-argument search closes the gap the spec left open).

SAFETY, read before running anything: `python -m scribe.setup --status` and `python -m scribe.doctor` call db.connect + migrate. Unfixed, from the repository, that is the LIVE ./data. So every command-level test runs the command from a COPY of scribe/ in tmp_path (cwd = the copy, so -m resolves there and the default library is <copy>/data), and every red run happens in the scratch copy under build/TASK-089.03/, never in the repository.

1. Red, criterion 1: tests/test_dotenv_commands.py - per command a subprocess in a tmp copy, SCRIBE_DATA_DIR only in the SCRIBE_ENV_FILE file, not in the child's environment. setup --status: myscribe.db lands in the named directory and <copy>/data is not created. doctor --no-gpu: the data-dir and database lines name the directory. models: HF_HUB_CACHE pointed at an empty directory, pyannote files of the pinned sizes (33 MB, truncate) under <named>/models, and the row reads `have`. Keep the red output and a listing of both directories.
2. Red, criteria 2-5: tests/test_env.py (blank and whitespace-only process variable; a non-blank one still wins; a blank value in the file is not exported - spec 3.4; applied(); write_env; bootstrap) and two tests in tests/test_setup.py for write_token (BOM, `HF_TOKEN = old`). Keep the red output.
3. scribe/env.py: load_dotenv fills a key whose process value is missing or blank, skips blank file values, and adds the name to a module-level set; applied() returns the names as a frozenset - names only, never a value, and it accumulates because setup calls load_dotenv twice. The return value stays "everything the file defines". The docstring's "the environment wins" becomes "a non-blank environment wins".
4. scribe/env.py: write_env(name, value, path=None) - reads utf-8-sig, recognises a line the way parse() does (one definition of "a line for this name"), replaces the first and drops later ones, other lines untouched; refuses a name that is not an identifier and a value with a newline; writes a temp file in the same directory, then os.replace. A new file is 0600 on POSIX; an existing file keeps its mode. setup.write_token becomes a call to it, so apply() and its tests are unchanged; it goes in TASK-089.09.
5. scribe/env.py: bootstrap() = load_dotenv() plus REPO_DIR/.tools/bin first on PATH when it is a directory, not added twice. scribe/paths.py: refresh() sets DATA_DIR and the five constants derived from it again from the environment.
6. Wiring. scribe/__main__.py main() calls env.bootstrap() where it calls load_dotenv today. setup, models and doctor call env.bootstrap() and paths.refresh() in their `if __name__ == "__main__":` block, NOT in main(): tests/test_doctor.py:96-106 monkeypatches paths and calls doctor.main() in-process, and a refresh inside main() would point that test at the live library. This departs from the spec's "in the three main()s", for that reason; the main()s keep their load_dotenv. Library importers still load no file. A runpy test with `--help` shows each block calls both before main() and touches nothing.
7. .gitignore gets `.tools/`. CHANGELOG [Unreleased] / Fixed gets the entries.
8. Green, one file per process, output to build/TASK-089.03/: first in a scratch copy of the fixed tree, only then in the repository - test_env, test_dotenv_commands, test_setup, test_web_scaffold (:218-244 must stay green untouched), test_doctor, test_models, test_app, test_launcher.
9. Mutation in build/TASK-089.03/mut/: put setdefault back; drop the refresh from one __main__ block; read utf-8 in write_env; drop the PATH prepend. Each must turn a named test red. Then grep MUTANT in scribe/ and tests/ finds nothing.
10. Scratch run for criterion 1, before and after, from the scratch copies with a scratch env file: the three commands' output plus a listing of both directories. No value of any variable is printed.
11. WSL (Ubuntu, on ext4 under ~, not /mnt/d where modes are faked): copy scribe/env.py only, never .env; write_env to a new file, `stat -c %a` reads 600 under umask 022; an existing 0644 file keeps 644. macOS is not measured; the notes will say so.
12. Notes for the orchestrator: what was and was not measured, and the findings outside scope (below in the report), none of them built.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
IMPLEMENTED 2026-09-20 (not committed, criteria not checked - the orchestrator's). Evidence: scratchpad build/TASK-089.03/.

WHAT CHANGED
- scribe/env.py: load_dotenv fills a key whose process value is missing or blank, skips a blank value in the file, records the name; applied() -> frozenset of names (never values, accumulates). _pair() is the one definition of "a line for this name", shared by parse() and write_env(). write_env(name, value, path=None): utf-8-sig, first match replaced and later ones dropped, other lines and their line endings untouched (BOM not written back), refuses a non-variable name and any value parse() would split (\n, \r, U+2028 ...) without repeating the value, quotes a value only when the bare form would not read back, mkstemp in the same directory (0600 from the first byte) + fsync + os.replace, a failed replace is raised and the temp file removed. bootstrap() = load_dotenv() + REPO_DIR/.tools/bin first on PATH when it is a directory, moved to the front rather than added twice.
- scribe/paths.py: refresh() = importlib.reload of the module. One definition of the constants instead of a second list, a constant added later cannot be forgotten, and no existing line moved (ADR-015 cites paths.py:40).
- scribe/setup.py, models.py, doctor.py: env.bootstrap() + paths.refresh() in the `if __name__ == "__main__":` block, NOT in main() (tests/test_doctor.py runs main() against paths of its own). The main()s keep their load_dotenv. setup.write_token is now one call to env.write_env.
- scribe/__main__.py: env.bootstrap() where load_dotenv was; the runner children inherit PATH (supervisor._spawn passes no env=).
- .gitignore: `.tools/`. CHANGELOG [Unreleased]: Added + Fixed. tests/test_llm_live.py: one docstring sentence that named os.environ.setdefault.
- New tests: tests/test_env.py (45), tests/test_dotenv_commands.py (9); two added to tests/test_setup.py.

RED FIRST (unfixed copy of scribe/ in build/TASK-089.03/before, never the repository)
- red-test_env.txt: 39 failed, 1 passed, 5 skipped. The one pass is "a process value that says something still wins" - behaviour that must not move. Blank: `assert '' == 'from-the-file'`; whitespace: `assert ' \t ' == 'from-the-file'`.
- red-test_setup.txt: 2 failed, 7 passed - BOM: ['HF_TOKEN=old', 'HF_TOKEN=new']; spaced: ['HF_TOKEN = old', 'HF_TOKEN=new'].
- red-test_dotenv_commands.txt: 6 failed, 3 passed. setup: myscribe.db not in the named directory; doctor: data-dir and database lines name <clone>\data; models: `[MISSING] pyannote/...`. The 3 passes are the library-import guards (green before and after by design; mutation M5 shows they bite).
- scratchrun-before.txt / scratchrun-after.txt: the three commands by hand with SCRIBE_DATA_DIR only in a scratch env file, plus a listing of both directories. Before: myscribe.db in <tree>\data, models MISSING, doctor names <tree>\data. After: <tree>\data does not exist, myscribe.db in `elsewhere`, pyannote `have`, doctor names `elsewhere`.

GREEN, in the repository, one file per process (green-<file>.txt, summaries in green-summary-*.txt)
- test_env 40 passed, 5 skipped (POSIX modes x4 and symlink: run in WSL below); test_dotenv_commands 9 passed; test_setup 9 passed; test_web_scaffold 27 passed, untouched (:218-244 green); test_doctor 17; test_models 13; test_app 17; test_launcher 25 passed, 1 skipped.
- Every other test file that imports a changed module (grep, 36 files): 35 exit 0. test_llm_live: 7 deselected, exit 5 - gpu-marked at module level, NOT run (-m gpu is off limits here).
- 44 files in all: 43 exit 0, 1 exit 5 as explained. Nothing failed.

MUTATION (copy in build/TASK-089.03/mut, mutate.py asserts the old text is there exactly once; mutation-summary.txt + mut-M*.txt)
- M1 setdefault back -> 4 red in test_env (blank, whitespace, blank file value, applied). M2 no refresh in the doctor's block -> doctor subprocess test + runpy test red. M3 writer reads plain utf-8 -> BOM test red. M4 no PATH prepend -> 4 bootstrap tests red. M5 models bootstraps on import -> library guard red. M6 exact-prefix match -> spaced test in test_setup red. M7 write in place -> os.replace test and failed-move test red. M8 app calls load_dotenv, not bootstrap -> order test red.
- mutant-grep.txt: grep -rn MUTANT scribe tests -> nothing (exit 1). mut/ removed.

POSIX 0600, MEASURED IN WSL (wsl-measure.txt): Ubuntu, Python 3.12.3, ext4 under ~ (findmnt), only scribe/__init__.py, __main__.py, env.py, paths.py and tests/test_env.py copied - no .env. New file under umask 022: stat -c %a -> 600 (a plain file beside it: 644). Existing 644/640/600 keep 644/640/600. No temp file left. tests/test_env.py there: 45 passed, the five Windows skips included.
NOT MEASURED: macOS. Nothing here ran on a Mac; the mode tests are skipif win32 only, so CI's macOS runner will run them, but I have not seen that run.
check-ignore.txt: git check-ignore -v .tools/x -> .gitignore:70.

DECISIONS TO CONFIRM
- An existing .env keeps its mode; only a new file is 0600 (criterion says "creates"). Always tightening would surprise somebody who chose 0640 for a service group.
- A blank value in .env is no longer exported (spec 3.4; not literally in the criteria). .env.example ships `HF_TOKEN=`.
- write_env follows a symlinked .env to the real file (os.path.realpath) so the link survives; the old write_token wrote through the link, and a bare os.replace would have been a regression. Tested on Linux, skipped on Windows without symlink rights.
- If TASK-089.19 decides a clone does not write SCRIBE_DATA_DIR to .env, write_env loses its last caller once TASK-089.09 removes write_token, and goes with it.

OUT OF SCOPE, FOUND, NOT BUILT
- scribe/export/__main__.py:11 and scribe/proxies/__main__.py:12 load .env themselves (fine for SCRIBE_DATA_DIR) but do not get .tools/bin on PATH; `python -m scribe.proxies` would not find an installer's ffmpeg. For TASK-089.17.
- tests/test_doctor.py:96-106 calls doctor.main() with no SCRIBE_ENV_FILE, so it reads the developer's real .env into the pytest process. Older than this task.
- A blank SCRIBE_DATA_DIR in the process with nothing in .env still makes Path('') - the current directory - the library (paths.py:4).
- tests/test_paths.py:4-10 reloads scribe.paths and never puts it back, so the rest of that process keeps a tmp DATA_DIR. Older than this task.
- The spec (3.4) says refresh is called "by the three main()s"; it is in the __main__ blocks, for the reason above. The spec text could follow.

CRITERION 5, REAL RUN (pathrun-after.txt, path_run.py): the four entry points each in a child process from the scratch copy of the fixed tree, the real __main__ block via runpy; for python -m scribe only uvicorn.Server.run is stubbed (no server, no port). Without <tree>/.tools/bin: PATH unchanged, 4 of 4. With it: first PATH entry is .tools/bin and it occurs once, 4 of 4 - also for setup and the doctor, whose main() loads .env a second time. NOT DONE: graphify update (the build rules say never touch graphify-out/); no commit; criteria left unchecked.

REVIEW 2026-09-20 - what the three verifiers' findings changed (fixer; not committed, criteria not checked). Evidence: scratchpad build/TASK-089.03/ - fix-*.txt, final-*.txt, fix/.

CHANGED
- Windows, one name in two spellings (finding 5, reproduced: fix/probe-f5-before.txt). A file holding `hf_token=old` kept that line when HF_TOKEN was written, and load_dotenv went on filling the variable from it - the value just written lost. write_env now compares names the way the environment spells them (`_spelled`, capitals where `_CASELESS` = os.name == "nt"), and applied() records that spelling, so `"HF_TOKEN" in env.applied()` is true for a file that says `hf_token`. On Linux and macOS nothing moved: `hf_token` stays another variable, and a test says so. Red first: fix-red-test_env.txt (2 failed, on the assertions, not on a missing attribute). After, for real on this machine: fix/probe-f5-after.txt.
- POSIX, owner and group (finding 6, measured - the verifier had only read it). Replace-by-rename gave `.env` to whoever ran the command. fix-wsl-owner-root.txt: as root, a robert:robert 600 file became root:root 600 and the user's load_dotenv died on PermissionError; after the fix it stays robert:robert 600 and reads. It also bit without sudo: fix-wsl-owner-user.txt, a robert:adm 640 file came back robert:robert 640 - the mode kept and the group it was chosen for gone. write_env now hands the temp file to the old owner and group before the move, best effort (OSError ignored: only root may give a file away). Three tests, skipif win32 like the mode tests; on ext4 in WSL: 4 failed before, 51 passed after.
- "A BOM is not written back" (finding 7) was a sentence without a test. One assertion on the bytes in the BOM test; mutation M10 (the verifier's surviving V13) now fails it.
- CHANGELOG [Unreleased]: the two sentences that describe the above.

MUTATIONS ADDED (fix/mutate_fix.py, copy in mut/, removed afterwards; fix-mutation-summary.txt)
- Findings 3 and 9, the halves without a mutant of their own: M9 the file always wins -> "a process value that says something still wins" red, and test_web_scaffold's :242; M2s / M2m no refresh in setup's / models' block -> their subprocess and runpy tests red; M5s / M5d setup / the doctor bootstrap on import -> their library guards red.
- M11 writer tells case apart on Windows too, M12 case folded everywhere, M13 applied keeps the file's spelling: one test each, the right one. M14 no chown: survives on Windows (all skipped there - finding 8, inherent), 2 red on ext4. M15 a refused chown raised: 1 red on ext4.

FINAL, in the repository, one file per process (final-<file>.txt): test_env 43 passed, 8 skipped; test_setup 9 passed; test_dotenv_commands 9 passed; test_web_scaffold 27 passed; test_models 13 passed; test_doctor 17 passed. Each run had SCRIBE_DATA_DIR pointed at a scratch directory (test_doctor also SCRIBE_ENV_FILE) - see the next paragraph for why - and the live data/ and data/work mtimes were read before and after each: unchanged.

NOT CHANGED, AND WHY
- Finding 2 is a pointer, and right: for criterion 4's "red first" cite red-test_setup.txt plus mut-M3/M6, not red-test_env.txt (red there only because write_env did not exist).
- Finding 4: Ubuntu is Stopped again (WSL idles out); my measurements started it and left it running.
- Finding 8: inherent. The POSIX half of criterion 4 is proven by the WSL runs and CI's Linux runner, never by a green Windows run.

KNOWN LIMITS, NOT BUILT
- A NEW `.env` made by root is root's 0600 (there is no owner to keep). A first setup under sudo makes the database and the folders root's as well, so this is not the file's problem alone.
- Where the chown is refused - a writer who is not the owner, or a group the owner is not in - the file ends up the writer's. Only writing in place would keep it, and that gives up the atomic move.
- macOS: not measured, for the owner tests either.

FINDING 1, ATTRIBUTED - needs a follow-up task, nothing built here
- tests/test_doctor.py :30, :36, :42 and :86 call doctor.checks(include_gpu=False) without patching paths. That runs check_data_dir_writable (a NamedTemporaryFile made and deleted in paths.DATA_DIR - which is what moved data/'s mtime at 22:17:57) and check_database (db.connect() + migrate on paths.DB_PATH). Run bare in the repository, that is the live library. Shown by running with the fence up: fix-sandbox-listing.txt - the scratch directory got a 225280-byte myscribe.db and a moved mtime inside the test_doctor window. Older than this task (file last committed 2026-09-19).
- tests/test_llm_chat.py patches only DB_PATH before runner.main(), whose finally removes data/work/<job id> from the live WORK_DIR (read, not run).
- Until that is fixed: run tests/test_doctor.py only with SCRIBE_DATA_DIR set to a scratch directory (fix/run_final.sh does).

CORRECTION to the review note above (same fixer, same day): "FINDING 1, ATTRIBUTED" says more than was measured. What was shown: the mechanism. Four tests in tests/test_doctor.py reach check_data_dir_writable and check_database with unpatched paths (read), and with SCRIBE_DATA_DIR fenced to a scratch directory that directory got a myscribe.db born inside the test_doctor window and a moved mtime (run: fix-sandbox-listing.txt). What was NOT shown: that this is what moved the live data/ at 22:17:57. test_doctor.py was never run bare here, on purpose, so the link to that timestamp is the verifier's window plus this mechanism - the best-supported explanation, not a confirmed one. Not ruled out: another workflow agent was active in this repository during the review (a staged rename of ADR-015 and new ADR-016/017 files appeared that this task did not make). One observation does not fit neatly and was not chased: data/myscribe.db-wal still exists with a 19:57:33 mtime, where a sole connection closing would normally checkpoint and remove it - so something else may hold the database open, or the doctor test's connection was not the only one. The data/work half (test_llm_chat.py) is read only, never run. The advice stands either way: the mechanism is real, so run tests/test_doctor.py only with SCRIBE_DATA_DIR fenced.

Verified independently by the orchestrator on 2026-09-20 before the criteria were checked.

The whole suite, one file per process, 76 files, with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE fenced to a scratch directory: 2440 passed, 1 failed, 10 skipped. The six files this task touches match the fixer's numbers exactly (test_env 43 passed 8 skipped, test_dotenv_commands 9, test_setup 9, test_web_scaffold 27, test_models 13, test_doctor 17).

The one failure is tests/test_feed_first_episode.py:150, and it is the fence and not this task: the disk-floor refusal is stored as result[:200] (scribe/ingest/feeds.py:458), the path sits in the middle of it, and a data directory over 141 characters pushes '10 GB' off the end. My fence path is 166. The same file passes with a short fenced path: 13 passed. Recorded as TASK-091.

Also confirmed: the live library was not touched. data/ and data/myscribe.db carry the same modification times before and after the run (data/ 2026-09-20 22:17:57, the database 2026-09-15 23:19:54). The hazard the verifier found - tests that reach the real library when nothing fences them - is real and older than this task; it is TASK-090, and until it is fixed every pytest run here is fenced.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The three commands read .env the way the app does, and a blank value no longer hides a real one.

scribe.setup, scribe.models and scribe.doctor import scribe.paths before anything reads .env, so a SCRIBE_DATA_DIR that only the file named arrived too late and all three used the wrong library. They now call env.bootstrap() and paths.refresh() in their __main__ block - not in main(), because tests call main() against paths of their own and a refresh there would send them to the developer's library. paths.refresh() reloads the module, so the six constants keep one definition. An AST probe over 85 files found nothing that copies a constant out at import, which is what makes the reload safe.

load_dotenv no longer uses os.environ.setdefault: a blank or whitespace-only process value is filled from the file, a real one still wins, and a blank value in the file is not exported (.env.example ships 'HF_TOKEN=' lines). It records which names it applied, and applied() returns them - names only, spelled the way the environment spells them, which on Windows is in capitals whatever the file says.

setup.write_token became env.write_env(name, value), which any name may use: it reads utf-8-sig, recognises a line the way the reader does (so 'HF_TOKEN = old' and a file with a BOM are replaced, not duplicated), keeps other lines and their line endings, refuses a value that would become a second line, and goes through a temp file and os.replace with fsync. A new file is 0600 on POSIX, an existing one keeps its mode, and - found by the review - its owner and group, because replace-by-rename gave a sudo-written .env to root and locked the user out of their own token.

env.bootstrap() also puts <repo>/.tools/bin first on PATH for the four entry points, where TASK-089.17's installer will put the tools it fetches. .tools/ is gitignored.

Verified: red first for every behaviour, 15 mutations on copies of the tree (each failing exactly the tests about it), the POSIX halves measured in WSL on ext4 including the sudo case, and the whole suite fenced at 2440 passed. The one failure is TASK-091, a message truncated by a long path, not this change.
<!-- SECTION:FINAL_SUMMARY:END -->
