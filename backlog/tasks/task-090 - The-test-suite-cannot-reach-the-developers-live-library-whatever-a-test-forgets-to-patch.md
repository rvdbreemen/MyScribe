---
id: TASK-090
title: >-
  The test suite cannot reach the developer's live library, whatever a test
  forgets to patch
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 21:13'
updated_date: '2026-09-23 19:50'
labels:
  - tests
  - safety
dependencies: []
references:
  - tests/conftest.py
  - tests/test_doctor.py
  - tests/test_llm_chat.py
priority: high
type: bug
ordinal: 162000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found on 2026-09-20 while building TASK-089.03, and confirmed by reading the code: running the tests on a developer's machine touches the live library in `data/`.

tests/conftest.py fences two things for every test - free disk space (:15) and `paths.LOGS_DIR` (:31). It does not fence DATA_DIR, DB_PATH, WORK_DIR, MEDIA_DIR or MODELS_DIR; 35 of the 76 test files patch what they need themselves, and the rest rely on never reaching it. Two that do reach it:

* tests/test_doctor.py:30, :36, :42 and :86 call `doctor.checks(include_gpu=False)` with unpatched paths. `check_data_dir_writable` makes a probe file in `paths.DATA_DIR` (scribe/doctor.py:188-191) and `check_database` runs `db.connect()` and `db.migrate(conn)` on `paths.DB_PATH` (scribe/doctor.py:269-271). With the schema current that is a no-op. On a branch that bumps the schema it migrates the developer's real library, possibly under a running app of the older version. The live `data/` and `data/work` directories were seen to change their modification time during a test run of 2026-09-20.
* tests/test_llm_chat.py gives `runner.main()` a tmp database through DB_PATH only (:98). Its `finally` block always calls `paths.remove_job_work_dir(job_id)` (scribe/runner.py:296-300), which resolves against the live WORK_DIR. Test job ids are 1, 2, 3...; a live job with such an id and scratch still on disk would lose it.

tests/test_doctor.py:96-106 also calls `doctor.main()` without SCRIBE_ENV_FILE, so the developer's real `.env` - tokens included - is read into the pytest process.

Continuous integration runs from a clean checkout and loses nothing; this is about the machine the library lives on. It is the same rule the project already keeps for proofs: on a copy, never on the live library. Until this is fixed, run pytest with SCRIBE_DATA_DIR pointed at a scratch directory and SCRIBE_ENV_FILE at an empty file.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Red first: a test that asserts, for every test in the suite, that scribe.paths.DATA_DIR, DB_PATH, WORK_DIR, MEDIA_DIR and MODELS_DIR all lie under that test's tmp_path. It fails today, and the failing output is kept.
- [x] #2 tests/conftest.py gains an autouse fixture that points those five at tmp_path, the way _own_log_dir already does for LOGS_DIR, and points SCRIBE_ENV_FILE at a file that does not exist, so the developer's .env is never read by a test. A test that wants another place still patches it, and wins.
- [x] #3 The 35 test files that patch a path themselves keep passing unchanged; where one only worked because it reached the real data directory, it is fixed and the notes say which.
- [x] #4 Shown on a COPY of the repository with a library beside it, never on the live one: tests/test_doctor.py and tests/test_llm_chat.py are run, and a listing before and after shows that no file under the copy's data/ was created, changed or removed, and that its database's schema version did not move.
- [x] #5 The suite is run one file per process and the summed totals are reported, equal to the totals before the change apart from the new test.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Baseline at HEAD: collect-only and the per-file totals. 2. Red first: the check for the five paths and SCRIBE_ENV_FILE, alone. 3. The fence fixture in conftest. 4. Measure daemon reaches with a socket tripwire and close port 11434 at the socket. 5. Criterion 4 on two copies with a library beside them. 6. A mutant on a copy. 7. Per-file sweep with the env fence, then without it, the live data/ listed before and after.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Widened on 2026-09-22 by what TASK-089.06's review found.

Two more reaches into this machine, both proven with a tripwire rather than by reading:
- Six tests in tests/test_doctor.py made real HTTP requests to the live Ollama daemon once check_ollama joined CPU_CHECKS. Fixed inside TASK-089.06 by stubbing it in conftest.
- Twenty tests that pre-date that task still reach the daemon by rendering the settings page: two in tests/test_web_ai.py (:1487, :1500) and eighteen in tests/test_web_settings.py. Not fixed there, because it is not that task's scope.

So this task's fixture has a second job beside the five paths: a test must not reach a service on this machine either. The daemon is read-only traffic and harmless in itself, but a suite that answers differently depending on whether Ollama happens to be running is a suite that tests the machine and not the code.

## Built 2026-09-23 (orchestrator). Evidence under d0ea7837-…/scratchpad/ev/090/ and ev/sweep-090*/.

WHAT CHANGED - tests/conftest.py only, plus one fixture parameter on three tests
- `_library_under_tmp_path` (autouse): DATA_DIR, DB_PATH, MEDIA_DIR, WORK_DIR and MODELS_DIR point under the test's tmp_path; SCRIBE_DATA_DIR is set to the same place for children and for `paths.refresh()`; SCRIBE_ENV_FILE names a file that does not exist. A test that wants another place patches it afterwards and wins.
- `_the_library_is_out_of_reach` (autouse, asks for the fence by name so it runs after it): the criterion-1 check, at setup of every test - all five paths lie under tmp_path, and SCRIBE_ENV_FILE names no existing file.
- `_no_daemon_answers_a_test` (autouse): a connection to port 11434 is refused at the socket, the way a stopped daemon refuses it. Closed at the socket rather than by stubbing a provider method, because the tests about those methods hand them a MockTransport, which opens no socket. Opt-outs: `ollama_ready` (new, shared: the daemon is up with qwen3.5:4b) and `ollama_port_open` (the real port, for a test that serves on it; nobody needs it today).

CRITERION 1 - red first
- The check alone, before the fence, with the env variables unset: ev/090/red-no-env.txt - `AssertionError: paths.DATA_DIR is D:\Users\Robert\Documents\GitHub\RvdB\MyScribe\data, outside this test's tmp_path`. tests/test_doctor.py plus tests/test_llm_chat.py: 91 errors (red-test_doctor-test_llm_chat.txt).
- Mutant on a copy, WORK_DIR left unfenced: tests/test_llm_chat.py 27 errors, the check naming the path (mut-work-dir-unfenced.txt). `grep -rn MUTANT scribe tests packaging install.py` finds nothing.

CRITERION 2 - the fixture, and the second job the notes above gave it
- The five paths and SCRIBE_ENV_FILE as specified.
- The daemon: measured with a socket tripwire (a -p plugin that records every connect to port 11434). Before: 61 tests in 11 files reached the real daemon - test_web_settings 22, test_web_ai 14, test_glossary 6, test_ingest_watching 5, test_library_row_meta 3, test_llm_task_providers 3, test_llm_tasks 2, test_stage_finalize 2, test_web_exports 2, test_pipeline_e2e 1, test_web_transcript 1 (red-daemon-reaches.txt). After: 0 successful connections in all 37 files that mention Ollama or the settings page, all green (green-daemon-reaches.txt).
- A first attempt stubbed `OllamaProvider.tags` and broke 33 tests that are about `tags` (they pass a MockTransport); that is why the port is closed at the socket instead.

CRITERION 3 - the 35 files that patch a path themselves
- Pass unchanged: no test file that patches a path was edited. Three tests were edited, and each only gained the `ollama_ready` parameter: tests/test_library_row_meta.py::test_labelling_a_selection_queues_one_job_per_file and ::test_a_file_without_a_transcript_is_skipped_and_said_so, tests/test_pipeline_e2e.py::test_a_re_transcription_asks_who_is_speaking_again. They were green only because this laptop's daemon answered: since TASK-089.10 and TASK-089.26 the bulk pass and the speaker pass ask whether Ollama can answer before they queue, and with the port closed the answer is no. No assertion moved.

CRITERION 4 - on a COPY with a library beside it (ev/../c090/)
Two copies of the repository, HEAD c00a1a5 and this change, each with a library in its own data/: a migrated database (user_version 17), work/1/normalized.wav and work/2/x.wav. The env variables unset, so the copy's own data/ is the default. A read-only lister (sqlite with immutable=1; an earlier mode=ro lister created -wal/-shm itself and was caught and replaced) listed name, size, sha256 and mtime before and after tests/test_doctor.py and tests/test_llm_chat.py.
- HEAD: 64 + 27 passed, and the diff is `< work\1\normalized.wav` - the runner's cleanup deleted live job 1's scratch, the exact defect this task names.
- This change: 64 + 27 passed, the listing identical, user_version 17 before and after.

CRITERION 5 - one file per process, totals
- Baseline, HEAD c00a1a5 with the env fence: 3293 collected (`pytest -q --collect-only tests`: 3293/3303, 10 deselected); the sweep before this change (ev/sweep/) was 3282 passed + 10 skipped, one test short of 3293 because the signer case was added after it.
- After the paths fence, env fence on: 3283 passed, 0 failed, 10 skipped (ev/sweep-090/) = 3293. Git status hash identical before and after.
- After the daemon fence, env variables UNSET, so conftest is the only fence (ev/sweep-090b/): 3283 passed, 0 failed, 10 skipped, 0 errors = 3293; git status hash identical. The live data/ was listed by name, size and mtime before and after (logs/ excluded, nothing opened, no app on 4242): 100 files, identical (ev/090/live-data-*.txt).
- No new test function was added: the check is a fixture that runs for every test, so the totals are equal to the baseline.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
tests/conftest.py now gives every test a library of its own under tmp_path (DATA_DIR, DB_PATH, MEDIA_DIR, WORK_DIR, MODELS_DIR, SCRIBE_DATA_DIR) and a SCRIBE_ENV_FILE that does not exist, checks all five at setup, and closes port 11434 at the socket so no test is answered by this machine's Ollama. Verified: red first (91 errors on two files), a copy of HEAD whose test run deleted a live job's scratch against a copy of this change whose library listing stayed identical, a socket tripwire from 61 daemon reaches to 0, a mutant, and two per-file sweeps of 3283 passed / 10 skipped = the 3293 collected, the second with no env variables at all and the live data/ unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
