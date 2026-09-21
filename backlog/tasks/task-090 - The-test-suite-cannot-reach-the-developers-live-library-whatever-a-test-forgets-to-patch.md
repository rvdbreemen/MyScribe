---
id: TASK-090
title: >-
  The test suite cannot reach the developer's live library, whatever a test
  forgets to patch
status: To Do
assignee: []
created_date: '2026-09-20 21:13'
updated_date: '2026-09-21 23:37'
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
- [ ] #1 Red first: a test that asserts, for every test in the suite, that scribe.paths.DATA_DIR, DB_PATH, WORK_DIR, MEDIA_DIR and MODELS_DIR all lie under that test's tmp_path. It fails today, and the failing output is kept.
- [ ] #2 tests/conftest.py gains an autouse fixture that points those five at tmp_path, the way _own_log_dir already does for LOGS_DIR, and points SCRIBE_ENV_FILE at a file that does not exist, so the developer's .env is never read by a test. A test that wants another place still patches it, and wins.
- [ ] #3 The 35 test files that patch a path themselves keep passing unchanged; where one only worked because it reached the real data directory, it is fixed and the notes say which.
- [ ] #4 Shown on a COPY of the repository with a library beside it, never on the live one: tests/test_doctor.py and tests/test_llm_chat.py are run, and a listing before and after shows that no file under the copy's data/ was created, changed or removed, and that its database's schema version did not move.
- [ ] #5 The suite is run one file per process and the summed totals are reported, equal to the totals before the change apart from the new test.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Widened on 2026-09-22 by what TASK-089.06's review found.

Two more reaches into this machine, both proven with a tripwire rather than by reading:
- Six tests in tests/test_doctor.py made real HTTP requests to the live Ollama daemon once check_ollama joined CPU_CHECKS. Fixed inside TASK-089.06 by stubbing it in conftest.
- Twenty tests that pre-date that task still reach the daemon by rendering the settings page: two in tests/test_web_ai.py (:1487, :1500) and eighteen in tests/test_web_settings.py. Not fixed there, because it is not that task's scope.

So this task's fixture has a second job beside the five paths: a test must not reach a service on this machine either. The daemon is read-only traffic and harmless in itself, but a suite that answers differently depending on whether Ollama happens to be running is a suite that tests the machine and not the code.
<!-- SECTION:NOTES:END -->
