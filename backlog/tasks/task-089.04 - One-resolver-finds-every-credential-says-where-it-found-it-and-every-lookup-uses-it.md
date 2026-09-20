---
id: TASK-089.04
title: >-
  One resolver finds every credential, says where it found it, and every lookup
  uses it
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
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
- [ ] #1 scribe/credentials.py imports nothing from scribe.llm or scribe.stages, and a test asserts it. base.api_key, diarize.hf_token, models.default_token, doctor.check_diarization and settings.hf_token_context all delegate to it. The resolver returns structured sources: the kind, the variable name, and the path or the hive. base.api_key maps them back to its two legacy strings - the bare variable name, and '<NAME> (Windows registry)' (scribe/llm/base.py:263, :266) - so its callers and tests/test_llm_providers.py:735 and :747 stay unchanged. The fuller wording of the later criteria ('HF_TOKEN (environment)', the hive named, the `.env` path) belongs to find_all and the found table only. One vocabulary per consumer, and a test pins the mapping.
- [ ] #2 The lookup order per credential is: the settings row; then, per variable name in the provider's order, the process environment (snapshotted before `.env` is applied), the `.env` file (parsed but not applied, with the source naming the path), and the Windows registry with the hive named, user before machine. For Hugging Face only, after those: HUGGING_FACE_HUB_TOKEN through the same three, then the login file at huggingface_hub.constants.HF_TOKEN_PATH, read in place.
- [ ] #3 find_all(conn) returns found, source, also_in and conflict per credential. A table-driven test plants a SENTINEL in every source and asserts it appears in no repr, str or JSON output.
- [ ] #4 Blank or whitespace counts as missing in every source. An unreadable login file (PermissionError, UnicodeDecodeError) reports 'not readable' and does not raise.
- [ ] #5 A token found in the login file is never written to `.env` or to a settings row. A test asserts both are untouched.
- [ ] #6 Red first: with the token only in the hf_token settings row, the doctor's diarization line and `python -m scribe.models --fetch --only pyannote/speaker-diarization-community-1` both see it. Today they answer 'no token' and exit 3.
- [ ] #7 tests/conftest.py gains an autouse fixture that stubs the registry and HF_TOKEN_PATH; today its two autouse fixtures (:15, :31) touch neither. It is shown that tests/test_stage_diarize.py:518-526, tests/test_setup.py:80-97 and tests/test_doctor.py:151-153 pass on the machine whose HKLM holds HF_TOKEN.
- [ ] #8 A real run on Robert's machine, values never printed, names the source of each credential, and says which other places define it and whether they disagree. The readers expected on 2026-09-20, in find_all's wording: Hugging Face from 'HF_TOKEN (environment)' with a conflict note about `.env`; OpenRouter from 'OPENROUTER_TOKEN (environment)', also in the machine hive; OpenAI from `.env`. If the machine has changed, the notes say what it shows now.
- [ ] #9 The tests are shown to bite: on a COPY of the repo one .strip() is dropped, and the red output is shown.
<!-- AC:END -->
