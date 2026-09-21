---
id: TASK-089.04
title: >-
  One resolver finds every credential, says where it found it, and every lookup
  uses it
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-21 19:53'
labels:
  - security
  - packaging
  - llm
dependencies:
  - TASK-089.03
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 141000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Requirement 7 is 'detect before asking', and today the detection disagrees with itself. The Hugging Face token lookup exists in four copies. diarize.hf_token reads the settings row, then HF_TOKEN and HUGGINGFACE_TOKEN from the process environment (scribe/stages/diarize.py:258-276). models.default_token reads the environment only (scribe/models.py:307-312). doctor.check_diarization calls hf_token(None), so it never sees the settings row (scribe/doctor.py:482). settings.hf_token_context is the fourth (scribe/web/settings.py:211). None reads the Windows registry, the hub's legacy HUGGING_FACE_HUB_TOKEN, or Hugging Face's own login file.

The two LLM keys do get a registry fallback (scribe/llm/base.py:260-268, through windows_env at :194-222; :255-258 is the settings row), and base.py:196-203 records why: this machine has OPENROUTER_TOKEN set machine-wide under HKLM, invisible to a process that predates it. The Hugging Face token has no such fallback.

What a user hits: they save the token in Settings as the error told them to, run the command the doctor printed, and get 'no Hugging Face token is set ... save a token in Settings' with exit 3. A reader reproduced that loop on a scratch database on 2026-09-20. The source label also cannot tell `.env` from the environment, and two sources holding different values is silent. The readers measured that on Robert's machine for HF_TOKEN - the process value equals HKLM, `.env` differs. Which of the two tokens is valid was tested by nobody.

A secret is never printed, never put on a command line, and never copied from where it was found to somewhere less safe. A token in Hugging Face's login file can rotate, and `.env` is less protected than that file, so a found credential is used in place.

Needs a real machine: Robert's Windows machine: its registry and `.env` hold the conflicting tokens that the real-run criterion is about. The Hugging Face login file was reproduced by nobody; the machine has none.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 scribe/credentials.py imports nothing from scribe.llm or scribe.stages, and a test asserts it. base.api_key, diarize.hf_token, models.default_token, doctor.check_diarization and settings.hf_token_context all delegate to it. The resolver returns structured sources: the kind, the variable name, and the path or the hive. base.api_key maps them back to its two legacy strings - the bare variable name, and '<NAME> (Windows registry)' (scribe/llm/base.py:263, :266) - so its callers and tests/test_llm_providers.py:735 and :747 stay unchanged. The fuller wording of the later criteria ('HF_TOKEN (environment)', the hive named, the `.env` path) belongs to find_all and the found table only. One vocabulary per consumer, and a test pins the mapping.
- [x] #2 The lookup order per credential is: the settings row; then, per variable name in the provider's order, the process environment (snapshotted before `.env` is applied), the `.env` file (parsed but not applied, with the source naming the path), and the Windows registry with the hive named, user before machine. For Hugging Face only, after those: HUGGING_FACE_HUB_TOKEN through the same three, then the login file at huggingface_hub.constants.HF_TOKEN_PATH, read in place.
- [x] #3 find_all(conn) returns found, source, also_in and conflict per credential. A table-driven test plants a SENTINEL in every source and asserts it appears in no repr, str or JSON output.
- [x] #4 Blank or whitespace counts as missing in every source. An unreadable login file (PermissionError, UnicodeDecodeError) reports 'not readable' and does not raise.
- [x] #5 A token found in the login file is never written to `.env` or to a settings row. A test asserts both are untouched.
- [x] #6 Red first: with the token only in the hf_token settings row, the doctor's diarization line and `python -m scribe.models --fetch --only pyannote/speaker-diarization-community-1` both see it. Today they answer 'no token' and exit 3.
- [x] #7 tests/conftest.py gains an autouse fixture that stubs the registry and HF_TOKEN_PATH; today its two autouse fixtures (:15, :31) touch neither. It is shown that tests/test_stage_diarize.py:518-526, tests/test_setup.py:80-97 and tests/test_doctor.py:151-153 pass on the machine whose HKLM holds HF_TOKEN.
- [x] #8 A real run on Robert's machine, values never printed, names the source of each credential, and says which other places define it and whether they disagree. The readers expected on 2026-09-20, in find_all's wording: Hugging Face from 'HF_TOKEN (environment)' with a conflict note about `.env`; OpenRouter from 'OPENROUTER_TOKEN (environment)', also in the machine hive; OpenAI from `.env`. If the machine has changed, the notes say what it shows now.
- [x] #9 The tests are shown to bite: on a COPY of the repo one .strip() is dropped, and the red output is shown.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red first, kept: with the token only in the hf_token row of a fenced database and HF_TOKEN/HUGGINGFACE_TOKEN/HUGGING_FACE_HUB_TOKEN cleared and SCRIBE_ENV_FILE on an empty file, run the doctor's diarization line and `python -m scribe.models --fetch --only pyannote/speaker-diarization-community-1`; keep both outputs (exit 3, "no token") before a source file is touched.
2. New scribe/credentials.py: a table of credentials (huggingface, openrouter, openai) with their setting key and variable names, plus windows_env moved here from llm/base.py (base re-exports it, its `registry=` seam unchanged). Imports nothing from scribe.llm or scribe.stages; huggingface_hub is imported lazily inside the login-file reader, behind a `_login_file()` helper so a fixture can patch HF_TOKEN_PATH. A test asserts the import rule and pins the table against llm.PROVIDERS' key_env_vars.
3. Order per criterion 2: settings row; then per variable name the process environment, `.env` (parsed, never applied, path named), the registry (HKCU then HKLM, hive named); for Hugging Face then HUGGING_FACE_HUB_TOKEN through those three and the login file, read in place, never copied to `.env` or a row. "Snapshotted before `.env` is applied" is reconstructed from env.applied(): a name load_dotenv put in os.environ is attributed to `.env`, not to the environment. The docstring says why.
4. resolve() returns the value behind a value-free repr (ResolvedKey's manners); find_all(conn) rows hold found/source/also_in/conflict and no value at all - safe in repr, str and json.dumps by construction. Blank or whitespace is missing everywhere; an unreadable login file (PermissionError, UnicodeDecodeError) reports "not readable" and does not raise; a conn without a `setting` table is "no row", never an error.
5. Delegation: diarize.hf_token, models.default_token, doctor.check_diarization and settings.hf_token_context call the resolver. check_diarization and models.main gain a way to reach the settings row (an optional conn, opened the way doctor's other checks do, a missing database being simply "no row"). base.api_key maps sources back to its two legacy strings - the bare variable name (also for a `.env` hit) and "<NAME> (Windows registry)" for either hive - and a test pins that mapping; test_llm_providers.py:735 and :747 stay unchanged.
6. tests/conftest.py gains one autouse fixture stubbing the registry reader and HF_TOKEN_PATH, so this machine's HKLM HF_TOKEN cannot reach a test. New tests: sentinel table test (criterion 3), blank/unreadable (4), login file never copied (5), order and mapping (1, 2).
7. Green: the criterion-1 test file plus every test file that names a credential (test_doctor, test_setup, test_stage_diarize, test_env, test_dotenv_commands, test_launcher, test_llm_providers, test_llm_task_providers, test_llm_ollama, test_llm_privacy, test_web_ai, test_web_settings, test_web_scaffold, test_applog) and two heavy unrelated files, one file per pytest process, fenced with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE. Then repeat step 1's two commands for the green half, showing the source label is the settings row and not this machine's HKLM.
8. Criterion 8, read-only: copy data/myscribe.db with its -wal and -shm into the scratchpad, point SCRIBE_DATA_DIR at the copy, keep the real `.env`, and print find_all's table - names, sources, also_in, conflict, never a value. Measured today, names and booleans only: HF_TOKEN process == HKLM and `.env` differs (conflict), OPENROUTER_TOKEN process == HKLM (no conflict), OPENAI_API_KEY only in `.env`, no login file - the 2026-09-20 expectation still holds.
9. Criterion 9: copy scribe/, tests/ and pytest.ini to the scratchpad mut/ directory, drop one .strip() there with a script that asserts the old text first, run the affected test file there with the project's venv python, keep the red, and check that "MUTANT" appears nowhere in the repository afterwards.
10. Not in scope, reported not done: huggingface_hub also exposes HF_STORED_TOKENS_PATH (absent here); the spec names only HF_TOKEN_PATH. The orchestrator checks the criteria and sets the task Done.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
### Review fix pass, 2026-09-21 (the build was killed by a session restart; verification, criterion 9 and the fix pass are this run)

**Evidence that was missing and now exists**, in <scratchpad>/build/TASK-089.04/:
- `green-6-seam.txt` - criterion 6 without the network: fenced, the three HF environment names removed in the child, the token only in the `hf_token` row. `credentials.resolve` -> source='settings'; `doctor._diarization_token` and `models.default_token` -> found=True. Lengths only, never a value.
- `green-6-doctor.txt` / `green-6-models.txt` - the same two commands as the reds. The doctor's diarization line moved from "no local pipeline and no Hugging Face token" to "HTTP 401 ... for this token"; `python -m scribe.models --fetch --only pyannote/...` moved from models.py:253 ("no Hugging Face token is set ... save a token in Settings") to models.py:218, the branch only a request that carried a token reaches. Both exit 3 because `main` returns 3 for every reason="token" error - the message, not the code, is what separates them, and the files say so.
- `criterion-8-found-table.txt` - a real read-only run on this machine: the library's database copied first, the real `.env`, `load_dotenv` before the question. The 2026-09-20 expectation holds in all four parts (Hugging Face from HF_TOKEN (environment) with a conflict, OpenRouter from the environment also in HKLM, OpenAI from `.env` by its path, no login file). A second block gives each distinct value a letter: the process token and the HKLM token are one value, `.env` holds another - 2 values across 6 places. No value printed; `Found` carries none.

**Criterion 9**: the first pass's fourteen mutations stand (mut-verify-SUMMARY.txt, 12 of 14 caught). Both survivors are now caught - #12 `library_db` in three files, #13 the Windows case rule - and the three behaviours this review changed have their own mutations: #15 the short-circuit, #16 short_source's login-file branch (with test_llm_providers staying green, which is the proof it cannot move api_key's two legacy strings), #17 the doctor's environment fallback. mutate-fix-pass.py sits beside mutate.py rather than replacing it.

**Fixed in the repository**: `short_source` gained a LOGIN_FILE branch (the settings page would have said a token is in effect "from" nothing); `_sources` gained `first_only` so a found credential no longer reads the `.env` file, both hives and the login file - which imports huggingface_hub, the very thing the module's docstring cites ADR-001 for; `doctor._diarization_token` falls back to the environment when the database will not open; `tests/conftest.py` also stubs `credentials.library_db` (the new reach into this machine's 100 MB library from an unfenced test_doctor run) and empties `env._applied`, with a `library_db_unstubbed` fixture for the three tests that are about it; `test_stage_diarize`'s "no token anywhere" deletes HUGGING_FACE_HUB_TOKEN too. 13 new tests.

**Rejected, with evidence**: delenv-ing every credential name in the autouse fixture would turn tests/test_llm_live.py's `-m gpu` smoke tests into silent skips (they resolve this machine's real OPENROUTER_TOKEN from os.environ; the registry is already stubbed) - the narrow fix was taken and the docstring now says os.environ is deliberately not stubbed. An autouse `paths.DB_PATH` patch would break tests/test_paths.py:8, which asserts DB_PATH.parent == DATA_DIR. "No mutation ran" was stale: both read-only verifiers ran before the mutation verifier. The task has NINE criteria, not the ten the computed text claimed - `git diff` shows none was removed.

**Outside the criteria, reported not done**: doctor.py:555 answers 401 and 403 with the same sentence while its own docstring says the fixes differ (surfaced by green-6-doctor.txt); POST /settings/llm/{provider}/key still accepts a key for Ollama that api_key now reads back as none (pinned by a test, not changed); `credentials._spelled` duplicates the rule scribe/env owns.

**Runs**: 19 test files, fenced (SCRIBE_DATA_DIR=<build>/fence, SCRIBE_ENV_FILE=<build>/empty.env), one file per process, output to files, all green - test_credentials 34, test_doctor 19, test_models 15, test_llm_providers 52, test_stage_diarize 68, test_web_settings 46, test_env 43/8 skipped, test_setup 9, test_dotenv_commands 9, test_paths 6, test_applog 14, test_launcher 25, test_llm_ollama 39, test_llm_privacy 19, test_web_ai 121, test_web_scaffold 27, test_llm_task_providers 9, test_app 17, test_pipeline_e2e 54. `grep -rn MUTANT scribe tests` finds nothing. Nothing committed; criteria unchecked and the status untouched - the orchestrator's. The conftest change reaches every file, so any full suite started before this pass is stale. Full account: FIX-PASS-SUMMARY.txt in the build directory.

Addendum, same pass: mutation 18 (find_all stops early too, which would silently empty also_in and conflict - criterion 3's subject and criterion 8's whole run) is caught by three tests in tests/test_credentials.py; kept as mut-verify-18.txt. The library_db_unstubbed fixture now asserts the stub is really gone, because two of the three tests using it assert that no database is no connection - exactly what the stub answers, so a fixture-ordering change would have left them green and proving nothing. test_credentials 34, test_doctor 19, test_models 15, all green afterwards; grep -rn MUTANT over scribe and tests still finds nothing.

Verified independently by the orchestrator on 2026-09-21, after the session restart that killed this task's build workflow midway.

What the restart cost: the plan and the implementation were on disk and complete, but the verification, the mutation proof and the fix pass had never run. Those were run as their own workflow rather than by restarting the build, so the implementation was not redone.

The whole suite, one file per process, fenced at C:\ms-f: 2612 passed, 0 failed, 10 skipped over 77 files. That is 13 more than the 2599 measured before the fix pass, which matches the 13 tests it says it added. An earlier run of mine, before the fix pass, is superseded: the conftest change reaches every test file.

Checked by the orchestrator rather than taken from the report:
- The leak probe: a sentinel planted in the settings row, the environment and .env, then every object the module returns rendered as repr, str, f-string and json.dumps(asdict(...)) - 99 renderings over 30 objects. Found, the display object, leaks by no route including asdict. The only carriers were asdict() of Resolved, whose reason to exist is to hold the value, and grep shows nothing in scribe/ asdicts one. Two mistakes of mine on the way: the first probe inherited this machine's real credentials rather than planting its own, and I assumed Found.credential was an object where it is a string.
- short_source now has its login-file branch (credentials.py), so the settings page no longer renders an empty source for a token huggingface-cli wrote.
- conftest.py stubs credentials.library_db, captured at import before any fixture, with a library_db_unstubbed fixture for the three tests whose subject it is. That closes the new reach into the live library this task introduced through doctor.py:519.
- The repository is clean of mutation markers.
- The task has nine criteria, not ten: the fixer corrected both a verifier and me on that, from the task file's own git diff.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
One resolver finds every credential and says where it found it, without ever showing one.

The Hugging Face token had four lookups that disagreed. diarize.hf_token read the settings row and two environment variables, models.default_token read the environment only, the doctor called hf_token(None) and so never saw the row at all, and the settings page was a fourth. A user who saved the token where the error told them to, then ran the command the doctor printed, was told there was no token.

scribe/credentials.py is now the one table and the one order: the settings row, then per variable name the process environment, the .env file (by path, so it can be told apart from the environment), and the Windows registry with the hive named - user hive before machine. For Hugging Face, after those, the hub's own HUGGING_FACE_HUB_TOKEN and then the login file huggingface-cli writes, read in place and never copied. All six lookups delegate to it, and base.api_key keeps its legacy source strings so its callers and their tests do not move.

find_all() reports what was found, where, what else defines it and whether they disagree - and carries no value at all, so its rows are safe in a print, a log and a JSON body by construction. On this machine it finds two real conflicts: HF_TOKEN differs between .env and the HKLM registry, and OPENROUTER_TOKEN sits in both the environment and HKLM.

Three bugs the review found rather than the code reading wrong: the settings page would have rendered an empty source for a token from the login file; the doctor would have reported no token at all if the database would not open, even with HF_TOKEN plainly set; and every lookup did all the work even when the settings row had already answered, importing huggingface_hub for nothing.

Verified: 18 single-behaviour mutations on copies of the tree, all caught by the tests about them; 29 tests in test_credentials.py plus 13 added by the review; the whole suite fenced at 2612 passed, 0 failed; and a leak probe over 99 renderings finding no value anywhere it should not be. The build workflow was killed by a session restart after the implementation and before the verification, so the verification ran as its own pass.
<!-- SECTION:FINAL_SUMMARY:END -->
