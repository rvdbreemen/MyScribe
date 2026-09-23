---
id: TASK-089.19
title: >-
  An existing MyScribe library is found and offered, and adopting it never sends
  its back catalogue to a provider nobody chose
status: Done
assignee: []
created_date: '2026-09-20 18:40'
updated_date: '2026-09-23 21:55'
labels:
  - library
  - packaging
  - ux
dependencies:
  - TASK-089.17
  - TASK-089.14
  - TASK-089.11
  - TASK-089.07
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 156000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 as one of three extra questions (brief: R3, M5). Today this is a 'separate thing afterwards', and a quiet one. Robert's own machine is the example: a clone with a live library in <repo>/data (scribe/paths.py:4), and a release install that would start on an empty %LOCALAPPDATA%\MyScribe\data, because the launcher forces SCRIBE_DATA_DIR to <home>/data (packaging/launcher/myscribe_launcher.py:200-207). Nothing says 'a MyScribe library already exists at X - use it, or start fresh?'. The user sees an empty app and concludes their recordings are gone. A reader noted that %LOCALAPPDATA%\MyScribe did not exist on this machine on 2026-09-20, so the released installer has never had a first run here.

A release cannot discover that clone by itself. It has no <repo>: its default data directory resolves inside the payload (scribe/paths.py:4, with app_dir = payload/app at myscribe_launcher.py:95), the launcher forces SCRIBE_DATA_DIR (:200-207), and a clone that uses the default ./data names its library in no environment layer. From a release, a library inside a clone is therefore found only when the user names the folder.

The app already treats an older installation as something to adopt, not abandon: adopt_legacy_db renames a `scribe.db` from before the rename (scribe/paths.py:12-32). This question is the same manners, one level up.

It interacts with R1, which is why TASK-089.07 lands first. The lifespan runs sweep_speaker_passes at every start (scribe/app.py:261), with no ceiling. Adopt a library with diarized recordings, skip the provider question, and today the first start queues one OpenRouter job per recording.

Two facts make adoption less innocent than it looks. db.migrate is a forward-only ladder: `range(version + 1, SCHEMA_VERSION + 1)` (scribe/db.py:551-558). A library NEWER than the app gives an empty range, and the app then runs on a schema it does not know, without a word. And merely looking is a write if done carelessly: db.connect switches the file to WAL (scribe/db.py:544) and, by default, renames a legacy database (:540).

An open point this task settles, with a test: how a release points at an adopted library, given that the launcher forces SCRIBE_DATA_DIR. The pointer file of TASK-089.14 moves the whole home, which is not the same thing as using a library that lives inside a clone. ADR-015 fixes the principle and leaves the rest to this task, as below the level of a decision record. That is the agent's proposal in the grill of 2026-09-20 and not Robert's decision; he sees it in ADR-015's acceptance packet and can overturn it there. The principle: where things live is kept in a small pointer file that the launcher reads with the standard library before anything else exists, the launcher writes it only on the engine's instruction and never decides its content, and the app is never asked to move a library. Whether that file holds a second fact for a library adopted in place is the design spec's proposal until this task has built and measured it (criterion 8). 'Is a MyScribe serving that library right now?' is answered by the two fields `/health` gains in TASK-089.17: decided by Robert on 2026-09-20 (brief: G5, which settles W2). Adopting migrates, so this task depends on TASK-089.17 and carries criterion 11: a library is not migrated under a MyScribe that is serving it.

Skip means: start fresh, leave the old library untouched, and say where it is.

Needs a real machine: A COPY of Robert's library, on his machine. Never the live one: that is his own rule.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The sitting looks for a myscribe.db, and for a legacy scribe.db from before the rename (scribe/paths.py:8-9), in <repo>/data when it runs from a clone, in the per-user home, and in a SCRIBE_DATA_DIR found in any environment layer other than the target. It also accepts a folder the user names, typed or browsed, because a release cannot discover a clone. A scribe.db is shown as a library from before the rename; looking does not rename it (criterion 2), and adopting does, through adopt_legacy_db. For each one found it shows the path, the number of recordings and the size on disk.
- [x] #2 Looking changes nothing. The found database is opened read-only and not through db.connect. A test shows that no file next to it is created, renamed or modified: no -wal, no -shm, no migration.
- [x] #3 Skip starts fresh, leaves the found library untouched, and says where it is. It is recorded as skipped (TASK-089.11) and does not return at every start.
- [x] #4 Adopting never copies or moves a library silently. Before it adopts, the sitting says that this version will migrate the library, and that an older MyScribe must not open it afterwards.
- [x] #5 Red first: a found library whose user_version is newer than this app's SCHEMA_VERSION is refused with one sentence. Today migrate() does nothing for that case and the app would run on it (scribe/db.py:551-558).
- [x] #6 Red first, on a COPY of a library with N diarized, never-asked recordings and an OpenRouter key in the environment: adopting it and skipping the provider question queues N llm jobs on the first start today. After TASK-089.07 it queues none, and the test counts the job rows.
- [x] #7 Before adopting, the sitting says how many diarized recordings have never been asked who is speaking, and that choosing a cloud provider later will send each of them once. Nothing else about the library is sent or changed.
- [x] #8 How a release points at an adopted library is settled here, with a test, before the adoption is built; the decision and its reason are in the notes. It stays inside the principle ADR-015 fixes: a small pointer file that the launcher reads with the standard library before anything else exists; the launcher writes it only on the engine's instruction and never decides its content; the app is never asked to move a library. The design spec's proposal is the candidate until this task has built and measured it: the engine's result names the data directory, the launcher writes it into the pointer file as a second fact, and Layout.data_dir then prefers it. The test: a release layout whose pointer names an adopted library starts the app on that library and on no other, through the SCRIBE_DATA_DIR the launcher forces (packaging/launcher/myscribe_launcher.py:203); with no such fact it starts on <home>/data, as today. That this is this task's to settle is the agent's proposal of 2026-09-20 and not Robert's decision (ADR-015, Open Questions). ADR-015 needs no amendment while the result stays inside that principle; if it cannot, the notes say why and the record is amended before the code lands.
- [x] #9 Every run in this task is on a COPY of a library, never the live one, and names the copy it used.
- [x] #10 Where the answer can be given later: `--setup` and the Setup button, and in a clone also SCRIBE_DATA_DIR in `.env` or `--data-dir`, which is the documented manual route (.env.example:30). There is no Settings counterpart. That is a stated exception to scribe/setup.py:21-23, with its reason: the settings rows live in the library's own database, so switching libraries from inside the running app would change the database under the process that serves it. Settings > This machine already shows the store's path (scribe/templates/settings.html:100-104) and gains one line saying how to change it, and the question's answer_later line says the same. Robert can overturn this by asking for a Settings counterpart, which is then built first, as TASK-089.21 is for TASK-089.22. TASK-089.11 criterion 4 allows this exception by name.
- [x] #11 A library that a running MyScribe is serving is not migrated under it: the lifespan connects and migrates at start (scribe/app.py:247-248), and the ladder only goes forward (scribe/db.py:551-558). The mechanism is the one Robert decided on 2026-09-20 (brief: G5) and TASK-089.17 builds (its criteria 8 and 9): `/health` names the source tree the app runs from and the data directory it serves, and this task reads the data directory. It invents no mechanism. When it says the found library is being served, adoption is refused with 'stop that MyScribe first', and nothing is migrated. A MyScribe that answers /health without saying what it serves - an older one - is doubt, and doubt refuses the same way, as it does for the sync in TASK-089.17. What the sitting cannot see at all - an app on a port nobody named, this repository's own `--port 4299` among them - is said in the sentence that asks for the explicit yes before adopting. A job row in state `running` in the found database is read without writing and refuses in any case; it needs no port. One test per case, and each asserts that the found database's user_version did not move.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Fence first: an autouse conftest fixture makes machine discovery of libraries (<repo>/data, the per-user home, SCRIBE_DATA_DIR in a layer) return nothing, so no test can open a real library, even read-only. Tests opt in with their own candidates.
2. Criterion 8 before any adoption: the launcher's pointer file gains a second fact, "data". Layout takes an optional data directory; main() reads it from the pointer; a "data" naming a missing folder stops with a sentence, never falls back to <home>/data. A writer merges "data" in without dropping "home". Tests: with the fact, app_environment carries exactly that SCRIBE_DATA_DIR; without it <home>/data; missing folder stops. Decision and reason in the notes; ADR-019 drafted as Proposed.
3. A read-only inspector of its own (scribe/library.py), never db.connect and not setup.read_only: immutable open when no -wal stands beside the database, and a temp copy of db/-wal/-shm when one does. Reports path, legacy name, recordings, size on disk, user_version, diarized recordings never asked, running job rows. Before/after listing test on a clean db, one held open by a writer, and a lone legacy scribe.db.
4. The question: `library`, asked first and alone when found; a release asks even when none is found. Choices: new (default), one per found library (the choice is the explicit yes, its note carries the migration sentence, the older-MyScribe warning, the blind spot and the never-asked count), and `named` with a `library_folder` text question. A named folder is inspected and listed as a candidate on the next plan (`--plan --library PATH`), because #7 needs its count shown before the yes.
5. Apply: adopting writes nothing else from the document and stamps only the library question; refuses a newer schema (#5), a running job row, a /health that serves it and a /health without data_dir (#11), each asserting user_version did not move; adopts a legacy scribe.db through adopt_legacy_db (given a path); migrates only after the yes. Release: prints a relocate event line the launcher reads and writes into the pointer, then re-plans against the adopted library. Clone: SCRIBE_DATA_DIR written into .env. Skip: stamped skipped, target untouched, sentence says where the found library is.
6. #6: a lifespan run on a built library with N diarized never-asked recordings and an OpenRouter key in the environment counts llm job rows; zero after TASK-089.07, and the pre-089.07 llm/__init__.py on a mutation copy is the red.
7. #10: a line in Settings > This machine and in answer_later saying how to change the library; a comment at the setup.py rule naming the exception.
8. Tk: a text question gets an entry and a Browse button (the folder is typed or browsed; TASK-089.20's watch folder had the same gap).
9. CONTRACT 4 -> 5. Red first throughout, mutants on a copy, evidence in the scratch folder, notes via the CLI.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
### Found while building TASK-089.09, reproduced by the orchestrator (2026-09-22)

`python -m scribe.setup` orphans a library from before the 2026-09-06 rename,
silently, and this is pre-existing rather than new: `conn = db.connect(paths.DB_PATH)`
stands at `scribe/setup.py:1168` today, at `:163` on HEAD before TASK-089.09,
and at `:172` in the v0.5.1 tag.

`db.connect()` adopts only when it is given no path - `if path is None:
paths.adopt_legacy_db()` (`scribe/db.py:539-541`) - and setup passes the path
explicitly, so the adoption never runs from that door.

Reproduced on a scratch directory, fenced, with a legacy `scribe.db` holding a
row and no `myscribe.db`:

    before: ['.env', 'scribe.db']
    setup exit 0
    after : ['.env', 'logs', 'media', 'models', 'myscribe.db', 'scribe.db',
             'setup.json', 'work']
    paths.adopt_legacy_db() now returns: False

The old library keeps its data and is never opened again: `adopt_legacy_db`
refuses for ever once `myscribe.db` exists (`scribe/paths.py:25`). Whoever
upgrades from a pre-rename install and runs setup before starting the app
loses sight of their recordings, with no message.

TASK-089.09's own `--plan` door is safe - it opens read-only and treats a
legacy database as no library at all, deliberately, because adopting means
renaming and a plan may not write (`scribe/setup.py:239-245`). It is the
writing door that needs the fix, and adoption is this task's subject.

Not fixed by TASK-089.09: outside its twenty criteria, and changing which
database setup opens is an adoption decision, not an engine one.

### Built by the setup-lane agent, 2026-09-23 (not committed, no criterion ticked)

EVIDENCE below means C:\Users\rvdbr\AppData\Local\Temp\claude\D--Users-Robert-Documents-GitHub-RvdB-MyScribe\d0ea7837-cd8e-4a70-958e-8536dd2e4652\scratchpad\build\setup\089.19. Every test run was one file per process with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE fenced in that folder.

**What changed**

- New `scribe/library.py`: finds a library (`<repo>/data`, the per-user home, SCRIBE_DATA_DIR in the process, `.env` and both registry hives), looks at it without a write (`looked_at`), counts what the sitting shows, refuses (newer schema, running job row, `/health` that serves it or does not say), and adopts in place (`paths.adopt_legacy_db`, then `db.migrate`).
- `scribe/setup.py`: CONTRACT 4 -> 5. Two questions, `library` (choice) and `library_folder` (text, shown_if library=named), first in the plan. `--plan --library PATH` lists a named folder. `_library_door` handles a document that chose or named a library and writes nothing else. `MYSCRIBE_SETUP_RESULT` is how the engine tells the launcher; without it the engine writes SCRIBE_DATA_DIR into `.env`. `main` reads the answers before anything is written, and the writing door now adopts a legacy `scribe.db` first.
- `scribe/paths.py`: `adopt_legacy_db(new=None)` takes the database to adopt into.
- `packaging/launcher/myscribe_launcher.py`: the pointer's `"data"` fact (`adopted_data`, `write_pointer_data`), `Layout(data=, pointer=)`, the result file (`setup_environment`, `take_setup_result`), `Launch.adopt`, one more pass in `open_sitting` and `console_sitting`, `--library` on the plan command. A `text` question is an entry with a Browse button in the Tk sitting.
- Settings > This machine: one line, `library.CHANGE_LIBRARY` (`scribe/web/settings.py`, `scribe/templates/settings.html`).
- `tests/conftest.py`: autouse `_no_library_found_on_this_machine`. No test can find a library on the machine that runs it, not even read-only: in the main checkout `<repo>/data` is Robert's live library.
- Tests: new `tests/test_library.py` (15), `tests/test_setup_library.py` (21), `tests/test_launcher_library.py` (12); two appended to `tests/test_launcher_sitting.py`.
- `docs/adr/ADR-019-...md`, Proposed and signed as the agent; ADR index regenerated.

**Decisions, with reasons**

1. **#8, how a release points at an adopted library: the spec's candidate, built.** The engine decides; the launcher writes `"data"` into `MyScribe.location` only from the engine's result; `Layout.data_dir` prefers it and the forced SCRIBE_DATA_DIR follows. A `"data"` whose folder is gone stops the launcher with a sentence and never falls back to `<home>/data`. `--home` and MYSCRIBE_HOME keep everything under that home, so the pointer's `"data"` is not read under them, and an adoption there says it holds for this start only. It stays inside ADR-015's principle, so ADR-015 needs no amendment. The one thing the spec did not have is the channel: a result file named in `MYSCRIBE_SETUP_RESULT`. The console door hands the terminal to the engine and never reads its stdout, so a stdout event line would reach one door of two. That is a durable interface, so ADR-019 records it as Proposed. It has no `related` field on purpose: `adr relate` would edit the Accepted ADR-011 and ADR-015 in this worktree while another worktree adds ADR-018. The orchestrator can relate them after the merge.
2. **The library question is asked first, in the full list, not alone (a deviation from spec section 2).** A first step that stamps ends the sitting, so the gate never reopens and the rest of the questions go unasked at every later start. A first step that does not stamp brings the library question back. So there is one list. A document that adopts or names a library writes nothing else and stamps nothing, the engine says so (`OTHER_ANSWERS_WAIT`), and the door plans again against the library now in use, whose own stamp decides what is open. The cost: somebody who adopts answered the other questions once for nothing. It is ADR-019's open question for Robert.
3. **Choosing a found library is the explicit yes.** `shown_if` can only say "equals one value", so a separate yes question cannot be tied to "any found library". The note under each choice carries criteria 1, 4, 5, 7 and 11: path, recordings, bytes, where found, the migration and older-MyScribe sentence, the never-asked count, and the blind spot including `--port 4299`. Enter takes "new", never an adoption.
4. **A named folder takes two passes (#7).** Its never-asked count only exists after the engine looks. Applying `library=named` looks read-only, writes `{"library": folder}` to the result file and adopts nothing. The launcher then plans again with `--library folder`, which lists it with its numbers. The console asker does the same pass inside the engine.
5. **Looking uses a copy when a `-wal` exists.** `setup.read_only`'s WAL branch writes the wal-index (its own docstring), and an immutable open with a `-wal` reads the state before the last checkpoint, which could report an old `user_version` and let a newer schema past #5. `looked_at` copies db, `-wal` and `-shm` to a temporary folder and reads the copy. That costs the size of the database.
6. **"The target already holds a library" is read from its recordings**, not from the file, because every apply creates an empty, migrated database first.
7. **Release or clone is read from `.git` beside `env.REPO_DIR`.** A release's `app/` has none. A release asks the library question even when nothing is found; a clone asks only when it found one.
8. **The orphan bug in this task's notes is fixed here**, red first: the writing door calls `paths.adopt_legacy_db()` before `db.connect(paths.DB_PATH)`. Changing which database setup opens is an adoption decision, and that note handed it to this task.
9. **In a release with no launcher listening, adopting is refused before anything moves.** Run by hand inside a release's environment, a `.env` line would be overridden by the SCRIBE_DATA_DIR the launcher forces, and the result would be a migrated library that nothing uses. `::test_in_a_release_with_no_launcher_listening_nothing_is_adopted`, red first: EVIDENCE/red-release-without-launcher.txt.
10. **The Tk sitting renders `kind: text`.** Until now it fell into the radio branch and drew a group with no buttons, so a folder could be neither typed nor browsed. TASK-089.20's watch-folder question had the same gap.

**Criteria and their proof**

- #1: `test_library.py::test_a_library_is_looked_for_in_the_checkout_the_home_and_every_layer`, `::test_the_per_user_home_is_the_launchers_own`, `::test_the_numbers_shown_are_recordings_size_and_the_ones_never_asked`, `::test_looking_at_a_library_from_before_the_rename_does_not_rename_it`; `test_setup_library.py::test_a_found_library_is_offered_with_what_choosing_it_means`, `::test_a_release_asks_even_when_nothing_is_found_because_it_cannot_see_a_clone`, `::test_adopting_a_library_from_before_the_rename_goes_through_adopt_legacy_db`; typed or browsed: `test_launcher_sitting.py::test_a_text_question_is_an_entry_with_a_browse_button_and_hands_over_what_was_typed`. EVIDENCE/green-test_library-2.txt, green-test_setup_library-2.txt, green-test_launcher_sitting.txt; red on the old Tk branch: red-tk-text-kind.txt (2 failed).
- #2: `test_library.py::test_looking_at_a_cleanly_closed_library_leaves_every_file_as_it_was`, `::test_looking_at_a_library_a_writer_holds_open_reads_the_log_and_touches_nothing`, `::test_looking_at_a_library_from_before_the_rename_does_not_rename_it` (a listing of every file with size, mtime_ns and sha256, before and after); `test_setup_library.py::test_planning_does_not_touch_a_found_library`. A real `--plan` child: EVIDENCE/realrun-adopt-a-copy.txt, step 1, "unchanged: True; target created: False". Mutants mut-looked-at-plain-ro and mut-looked-at-no-copy are killed.
- #3: `test_setup_library.py::test_skipping_starts_fresh_leaves_the_found_library_alone_and_says_where` (stamped skipped, not in `--unasked-only`, still in `--setup` while the target is empty), `::test_choosing_new_is_an_answer_and_changes_nothing_elsewhere`. Mutant mut-skip-not-said is killed.
- #4: the choice note (decision 3) and `adopt` in place; `::test_adopting_migrates_in_place_writes_nothing_else_and_names_the_library` asserts the folder holds exactly `myscribe.db` afterwards and the target was never created.
- #5, red first: `::test_a_library_newer_than_this_app_is_refused_with_one_sentence`, `::test_a_library_newer_than_this_app_is_shown_refused`, `test_library.py::test_a_library_newer_than_this_app_is_refused_in_one_sentence`. Red without the guard: EVIDENCE/red-no-newer-schema-guard.txt (1 failed). Honest order: the guard was written with the door and the red was produced afterwards on a copy with only the guard removed, not before the code existed.
- #6, red first: `::test_the_first_start_of_an_adopted_back_catalogue_queues_no_llm_job` (4 diarized never-asked recordings, OPENROUTER_TOKEN set, provider skipped, the app's lifespan through TestClient, `job WHERE type='llm'` counted: 0). TASK-089.07 had already made it zero. The red is `default_provider` put back to its pre-089.07 fall-through to openrouter on a copy: EVIDENCE/red-before-089.07.txt (1 failed). A real app process on port 4299: realrun-adopt-a-copy.txt step 4, "llm job rows after the first start: 0 (5 diarized recordings never asked)".
- #7: the never-asked count and sentence in the note; `test_library.py::test_a_recording_under_a_folder_pinned_private_is_not_counted_as_one_to_send`; `test_setup_library.py::test_a_named_folder_is_looked_at_and_offered_before_anybody_says_yes`. Mutant mut-never-asked-no-folder-pin is killed. mut-never-asked-private survives and is equivalent: the per-recording privacy check excludes what the SQL clause excluded, as in the sweep itself.
- #8: `tests/test_launcher_library.py`, 12 tests. Red before the build: EVIDENCE/red-test_launcher_library.txt (3 failed of 7) and red-launcher-adopt-door.txt (1 failed, 4 errors). Green: green-test_launcher_library-2.txt. Mutants killed: mut-layout-ignores-data, mut-gone-falls-back, mut-pointer-overwrites, mut-console-deaf, and mut-launcher-decides (killed by the 120 s faulthandler timeout: adopting on the answer loops for ever).
- #9: every library in every test is built by `tests/test_library.py::build` under tmp_path with the app's own migrations; the real run built three in EVIDENCE/realrun (clone/data at schema 10 with 5 diarized and 1 plain recording, served/data, release-home/data). No copy of anybody's `data/` was made or opened. The conftest fence makes sure of that for the suite.
- #10: `CHANGE_LIBRARY` is the question's `answer_later` and a line in Settings > This machine: `::test_settings_this_machine_says_how_to_use_another_library_beside_the_stores_path` (red with the old template on a copy: red-settings-line.txt). The exception is written at the top of `scribe/setup.py`, beside the rule it is an exception to.
- #11: `::test_a_library_the_app_on_the_port_serves_is_refused`, `::test_an_older_myscribe_that_does_not_say_what_it_serves_is_doubt_and_refuses`, `::test_a_running_job_row_refuses_without_asking_a_port`, each asserting `user_version` did not move on a schema-10 library that a migration would have moved; `::test_the_port_asked_is_the_one_given`. Red without the guards: red-no-serve-guards.txt (4 failed). Mutant mut-doubt-passes is killed. A real app on 4299 serving a library: realrun step 2, "Stop that MyScribe first; nothing was migrated", user_version 17 -> 17.

**Not done, and who can do it**

- **A run on a copy of Robert's own library** (the task's "Needs a real machine"). Robert, with MyScribe stopped, in PowerShell from the checkout:

      robocopy D:\Users\Robert\Documents\GitHub\RvdB\MyScribe\data D:\scratch\copy\data /E
      $env:SCRIBE_DATA_DIR = "D:\scratch\empty-target"; $env:SCRIBE_ENV_FILE = "D:\scratch\empty.env"
      .venv\Scripts\python -m scribe.setup --plan --library D:\scratch\copy\data

  Expect `library` as the first question and a choice for D:\scratch\copy\data whose note gives its recordings, its bytes and how many diarized recordings were never asked. The live `data\` must be unchanged. Then:

      $env:MYSCRIBE_SETUP_RESULT = "D:\scratch\result.json"
      '{"contract": 5, "answers": {"library": "D:\\scratch\\copy\\data"}}' | .venv\Scripts\python -m scribe.setup --apply-stdin --port 4299

  Expect "MyScribe now uses the library at D:\scratch\copy\data (N recordings), migrated in place." and `{"data_dir": ...}` in result.json. Finally start `python -m scribe --port 4299 --no-supervisor --no-browser` with SCRIBE_DATA_DIR=D:\scratch\copy\data and no provider row. `SELECT COUNT(*) FROM job WHERE type='llm'` must be 0.
- **The real Tk window**, and a frozen release launcher adopting a clone's library end to end: a person at the screen, once per OS. Not run.
- **macOS and Linux**: not run. `default_home` is the launcher's own spelling, and a test compares the two.
- **The console asker asks `library_folder` even when `library` is not `named`.** The engine's `ask` ignores `shown_if`, which is existing behaviour and applies to the key questions too. Pressing Enter skips it.
- **`--setup`'s location line** (`location_note`) names the home and not an adopted library.

**Suite and final runs (2026-09-23)**

- Every test file, one process each, fenced: 87 files passed, test_llm_live fully deselected (its marker), no failure and no error. EVIDENCE/suite/summary.txt and one file per test file beside it. That run started before the last change to scribe/setup.py (the release-without-launcher refusal) and before a line-ending rewrite of tests/conftest.py, so the touched files were run again on the final code: EVIDENCE/final/ - test_setup 15, test_setup_plan 170, test_setup_prove 43, test_setup_library 22, test_library 15, test_install 56, test_launcher 102 (+1 skipped), test_launcher_sitting 57, test_launcher_library 12, test_web_settings 55, test_paths 6, all passed.
- `grep -rn MUTANT scribe tests packaging` in the worktree finds nothing.
- `adr-judge --diff` on the whole working diff, new files included: 0 violations, 0 advisory; the LLM pass did not run (no backend configured), declarative checks only. EVIDENCE/adr-judge.txt.
- `adr-lint --strict docs/adr`: all pass. The ADR index was regenerated here with ADR-019; it must be regenerated again after ADR-018 from the other worktree is merged, and ADR-019 can then be related to ADR-011 and ADR-015 with `adr relate`.
- A release's `app/` has no `.git`, which `_is_release()` rests on: `packaging/build_payload.py` copies only `git ls-files` output into it (`tracked_files`), and that never lists `.git`. Read in the code, not measured on a built payload. A source tree downloaded as a zip has no `.git` either: it reads as a release, asks the library question with nothing found, and `install.py` (which sets no result variable) then gets the refusal "only the launcher can point MyScribe at another library". Not handled.
- CRLF: tests/conftest.py and tests/test_launcher_sitting.py were normalised back to CRLF after appending; the new files are LF and autocrlf converts them on commit.

Verified 2026-09-23 by the orchestrator in MyScribe-wt-setup, fenced, one file per process: test_library 15, test_setup_library 22, test_launcher_library 12, test_setup_plan 170, test_launcher_sitting 57, test_launcher 102 (1 skipped), test_web_settings 55, test_setup 15, test_uninstall_text 5, all passed; grep MUTANT finds nothing. For Robert before a release: one run of the adoption on a COPY of his own library (the exact commands are in the build notes above), and ADR-019 is Proposed, his to accept. Deviation to note: the library question comes first in the full list rather than alone; a document that adopts writes nothing else and the door plans again against the adopted library (ADR-019's open question).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A first sitting finds an existing MyScribe library - in a clone's data/, the per-user home, any SCRIBE_DATA_DIR, or a folder the person names - opens it strictly read-only to show recordings, size and how many diarized recordings were never asked who is speaking, and offers to adopt it. A newer schema is refused, a library a running MyScribe serves is refused, and adopting never sends the back catalogue to a provider nobody chose (0 llm jobs on a real app with 5 such recordings). A release points at an adopted library through the pointer file's second fact, ADR-019 (Proposed). CONTRACT 5. Verified red first, 18 mutants, a full per-file suite, and a real run on port 4299 against a copy.
<!-- SECTION:FINAL_SUMMARY:END -->
