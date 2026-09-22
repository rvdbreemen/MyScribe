---
id: TASK-089.11
title: >-
  The first-run sitting appears when it should: a finished old setup gets the
  new questions once, and a skipped question does not nag
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-22 09:31'
labels:
  - packaging
  - ux
dependencies:
  - TASK-089.09
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 148000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The gate that decides whether the sitting appears at all was never defined. Today it is 'setup.json exists': setup.done() (scribe/setup.py:60-61) and the launcher's setup_needed (packaging/launcher/myscribe_launcher.py:462-463). The stamp holds {provider, tier, hf_token, fetched} (setup.py:143-154) and no question ids.

If the gate stays as it is, everybody who already finished the old setup keeps their stamp and never sees the API-key question or the Ollama question. That includes the people who pressed Save on the preselected 'Ollama, on this machine' radio without having Ollama (myscribe_launcher.py:558) - the group this work is for.

If the gate becomes 'does --plan have open questions', every start pays for a Python child. A reader measured `--status` at about 4.6 s on Robert's machine on 2026-09-20; that was not re-run when this task was written. A question somebody skipped on purpose would then also come back on every start.

Robert's requirements (brief: M4). Reading the gate is cheap: the stamp only, and no child process. Criterion 2 adds one number out of the payload to that read, because the stamp alone cannot say whether this version has questions it never covered. People who finished the old setup are asked the NEW questions once. A question deliberately skipped is recorded as skipped and does not nag, and it stays reachable through `--setup`, the Setup button and Settings. The button does not exist yet: it is built in TASK-089.15, and its route is tested there. The launcher's own docstring already says that closing the window is '"ask me next time", not "never"' (packaging/launcher/myscribe_launcher.py:527-528, committed on 2026-09-19), and that stays. TASK-040.06 records four things Robert chose on 2026-09-18; this wording is not among them, so it is cited from the code and not as his decision.

Today 'Skip for now' destroys the window and writes nothing (myscribe_launcher.py:590). The same dialog returns at every start until Save is pressed once, and no single question can be skipped on its own.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The stamp carries the contract number, when the sitting ended, and per question id its state: answered, skipped, not needed because the answer was found, or still open after a failure. Booleans and source names only; a SENTINEL test finds no secret in it.
- [x] #2 Deciding whether to show the sitting reads setup.json and one number out of the payload, as data, and nothing else. The number is the contract number, raised whenever a question is added. It is a number and not a list of question ids, so that the launcher learns nothing about questions; the design spec and ADR-015 say the same. How it ships - a one-number JSON file under app/scribe/ (the spec proposes scribe/setup_contract.json), or a constant read as text the way app_version reads the version (packaging/launcher/myscribe_launcher.py:119-126) - and its name are settled here and written into the notes. A test asserts that no child process is started, and the time it takes on Robert's machine is reported next to the 4.6 s it replaces.
- [x] #3 Red first: an old-format stamp {provider, tier, hf_token, fetched} makes the sitting appear once. It shows only the questions that stamp never covered and that are still open, keeps the old answers as answered, and does not reset the provider or the tier.
- [x] #4 A question that was skipped is not asked again at the next start. It is asked again by `--setup`, and its answer can be given later by the route its answer_later line names: Settings for every question that has a counterpart there, and the stated exceptions where it has none - the location (TASK-089.14: MYSCRIBE_HOME, `--home` or the pointer file) and the library (TASK-089.19: `--setup` and the Setup button only, with its reason). One test per route. The Setup-button route is not tested here: no such button exists today - the window has three, 'Open MyScribe', 'Open data folder' and 'Quit' (packaging/launcher/myscribe_launcher.py:613-623) - and TASK-089.15, which creates it, depends on this task. Its test is TASK-089.15 criterion 8.
- [x] #5 'Ask me next time' on the whole sitting writes no stamp, so the sitting returns at the next start, and its label says so. Skip on one question records that question as skipped. The two are different controls, and a test covers both.
- [x] #6 When a later version adds a question, a machine with a current stamp is asked that one question once. The launcher notices by comparing the stamp with the contract number of criterion 2; it starts no child to find out, and only when they differ does it pay for one `--plan`. A test adds a fake question id and shows it.
- [x] #7 A state that became open again after it was answered - a token removed, weights deleted - does not reopen the sitting by itself. `--plan` and the doctor report it. The notes record that this is deliberate: the gate is about what was asked, `--plan` is about the machine now.
- [x] #8 The launcher reads the stamp as JSON and the contract number of criterion 2 as data, and imports nothing from the app (ADR-011's Must: 'Keep the launcher stdlib-only'). tests/test_launcher.py:378 is updated rather than deleted, and its new name says what it pins.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Contract number (AC2), settled: the launcher reads CONTRACT out of scribe/setup.py as TEXT, the way app_version reads the version (myscribe_launcher.py:123). No scribe/setup_contract.json. Reason for the notes: adding a question means editing _questions in scribe/setup.py, so the number that must rise lives in the file whose change forces it; a separate file puts the bump away from its cause, which is how two numbers drift. New launcher helper setup_contract(layout) -> int | None, regex anchored at line start (CONTRACT also appears mid-line in f-strings in main). Unreadable number = fail open: show the sitting.
2. Stamp format (AC1), one writer stays _write_stamp: {"contract": N, "ended": <epoch>, "questions": {id: "answered"|"skipped"|"not_needed"|"open"}}. provider/tier/hf_token/fetched go: the stamp holds states, never a chosen value (spec section 2). "open" = a credential the service refused (report reopen) and a download that failed (the ModelError path, where fetch_models is answered today). "not_needed" is derived from credentials.find_all(conn) only - found and not asked - so no ollama probe is paid at the end of a sitting; it is informational and does NOT count as "put".
3. Reader + migration (AC3) in scribe/setup.py: states() normalises three shapes - new one as written; pre-089.09 {provider,tier,hf_token,fetched} read as contract 1 (provider -> llm_provider answered, tier -> default_tier, hf_token true -> hf_token, fetched -> fetch_models); anything with a contract and no "ended", unreadable, or not a dict -> {} and the gate fires (spec line 539). plan(unasked_only=True) takes "put" = answered + skipped from states().
4. Gate (AC2, AC6, AC8) in the launcher: setup_needed(layout) becomes stamp-missing OR unreadable OR stamp contract < payload contract. json and re only, no import from scribe, no child process. Tests: no Popen/run is reached (monkeypatched to raise), and setup_contract(layout) == scribe.setup.CONTRACT.
5. AC6 second half: add --unasked-only to scribe.setup main() (plan(unasked_only=) exists, nothing calls it from the CLI). A test injects a fake question id, raises the contract, and shows exactly that one question comes back. The launcher's own --plan child is TASK-089.15's rendering work and is NOT wired here.
6. AC5: rename the launcher's "Skip for now" to "Ask me next time" and drive it on the existing fake-tkinter harness (tests/test_launcher.py:497-601): pressing it returns None, first_run applies nothing, no stamp. Per-question skip is the engine side: a document with a null answer records that id as skipped. Red first for both.
7. AC4: unasked_only drops skipped ids; a bare plan (--setup) still lists them; one test per answer_later route that the route it names exists. The Setup button is TASK-089.15 AC8; the location (TASK-089.14) and library (TASK-089.19) questions do not exist yet and are reported not run.
8. AC7: no code change - the gate never consults needed(). A test: finish a sitting, remove the token, gate stays shut, and a bare --plan still shows the question open. The reason goes in the notes: the gate is about what was asked, --plan is about the machine now.
9. AC1 security: a SENTINEL sitting (token + api key) leaves no SENTINEL in the stamp text.
10. Collateral, stated rather than surprising: tests/test_setup_plan.py:152 writes a stamp with no contract, which the new reader takes as pre-089.09 and whose answered list it ignores - its stamp is rewritten to the new shape. Three launcher tests write "{}" as a stamp (:423, :915, :972); :423 is renamed to say it pins the contract gate (AC8's cited :378 is the ffmpeg test today), the other two get a current-contract stamp. Never weaken the gate to keep them green.
11. Evidence: red-first output kept for AC3 and AC5; pytest one file per process with the TASK-090 fence exported (test_setup.py, test_setup_plan.py, test_launcher.py, plus every file that imports scribe.setup); one mutation on a copy under mut/ - setup_needed back to "not stamp.exists()" - to show the gate test bites, then grep -rn MUTANT over scribe and tests is empty.
12. Measurement (AC2), two labelled numbers: setup_needed() in process over N iterations (the launcher is already running, so no interpreter start), and a cold child --plan and --status re-measured under the fence. Said plainly: the 4.6 s was measured against Robert's real library, --status opens it through db.connect and is off limits here, so the fenced number is an empty database and not like for like.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (2026-09-22, agent session)

### What changed

**scribe/setup.py**
- `STATES = ("not_needed", "skipped", "answered", "open")` and `PUT = ("answered", "skipped")`. STATES is in precedence order, weakest first, and `_states` applies it by writing the four sources in that order so a later line wins. A token typed, saved and then found reads `answered`, not `not_needed`; one the service refused reads `open` although it was typed.
- `_write_stamp(answers, report, answered, conn)` now writes `{"contract": N, "ended": <epoch>, "questions": {id: state}}`. `provider`, `tier`, `hf_token` and `fetched` are gone: the stamp is what the gate reads, and the gate has no business knowing anybody's provider. It gained `conn` because `not_needed` is read off `credentials.find_all(conn)` - no network, no Ollama probe.
- `_states` seeds from the previous stamp, so a re-run that touches one question does not erase what earlier sittings recorded (spec section 2, "carries the known states over"). `not_needed` uses `setdefault` so it can never downgrade an answer somebody gave.
- The `models.ModelError` path appends `fetch_models` to `report["reopen"]` before stamping, so a failed download stands as `open` rather than `answered`. `main` returns on that exception before it prints the reopen list, so nothing printed changes.
- `states()` reads three shapes: this writer's (has `ended`), the pre-TASK-089.09 `{provider,tier,hf_token,fetched}` via `_before_the_contract` (read as contract 1), and anything else - unreadable, not a document, or a contract with no `ended` - as `{}`.
- `plan(conn, unasked_only=True)` now drops what `states()` says was PUT; `--unasked-only` added to `main()` (the function took the keyword since .09 and nothing called it).
- `question_of(credential)` builds the two credential question ids in one place, and `_questions` uses it, so the stamp cannot name a question the plan never asks.
- `CONTRACT` and `STAMP` docstrings now say what was settled instead of "TASK-089.11's".

**packaging/launcher/myscribe_launcher.py**
- `setup_contract(layout) -> int | None`: `CONTRACT` read as TEXT out of the payload's `scribe/setup.py`, regex anchored at line start. Unreadable = None.
- `setup_needed(layout)`: stamp missing, unreadable, not a document, no `ended`, or a contract lower than the payload's. Fail open in every case it cannot work out. json + re only, nothing imported from the app (ADR-011), no child.
- The dialog's second button is "Ask me next time" (was "Skip for now").

### Criterion 2, settled: a constant read as text, not scribe/setup_contract.json

The number ships as `CONTRACT` in `scribe/setup.py` and the launcher reads that line as text, the way `app_version` reads `scribe/__init__.py`. Reason: adding a question means editing `_questions` in that same file, so the number that must rise lives beside the change that forces it. A separate JSON file puts the bump away from its cause, which is how two numbers drift apart. The spec proposes `scribe/setup_contract.json` (line ~497) and allows the constant in the same paragraph; this task takes the constant, so that spec sentence describes a proposal the code did not take.

The regex is anchored at line start on purpose: `CONTRACT` also appears mid-line in sentences `main()` prints, and a loose match would compare the stamp against a number out of an f-string. A test writes exactly such a line and asserts the reader answers None.

### Criterion 2, measured (2026-09-22, this machine, under the TASK-090 fence)

    gate  setup_needed()  1000 calls x5: 784.1, 784.7, 775.6, 962.4, 1195.3 microseconds each (median 784.7)
    child --plan                 cold: 4332, 4333, 4256 ms (median 4332)
    child --plan --unasked-only  cold: 4283, 4329, 4323 ms (median 4323)
    child --status               cold: 4183, 4168, 4223 ms (median 4183)

So 0.78 ms against 4183 ms: about 5300 times cheaper than the `--status` it replaces. Said plainly: the 4.6 s in the task description was measured on 2026-09-20 against Robert's real library and was never re-run. These numbers are from an empty database inside the fence, because `--status` opens the library through `db.connect` (a write - it sets WAL) and the hard rules forbid that on the live one. It is therefore not like for like, and the honest reading is "both are seconds, the gate is under a millisecond". Script: scratchpad .../TASK-089.11/measure_gate.py, output timing.txt.

### Criterion 7, deliberate non-behaviour

No code change, and the gate never calls `needed()`. A state that became open again - a token removed, weights deleted - does not reopen the sitting: the gate is about what was asked, `--plan` is about the machine now, and the doctor reports the machine (TASK-089.12). The launcher could not do otherwise without a child; the engine test pins both halves at once (the question is open in a bare `--plan` and absent from `--unasked-only`).

### Criterion 8, the cited line number was wrong

The criterion names tests/test_launcher.py:378; that line is `test_the_tools_are_installed_into_the_home_and_the_app_uses_them` (ffmpeg, nothing to do with the stamp). The test it means is `test_setup_is_needed_until_the_app_says_it_is_done` at :423-433, which wrote `{}` as a stamp and asserted the gate shut. It is updated rather than deleted and renamed to `test_the_sitting_appears_until_a_finished_sitting_of_this_contract_says_otherwise`, which says what it pins. Two further launcher tests wrote `{}` (:915 `test_setup_reopens_the_sitting_although_the_stamp_is_there`, :972 `test_a_start_at_login_leaves_a_due_sitting_for_the_next_start_by_hand`); both now write a current-contract stamp. The gate was never weakened to keep a test green.

### Evidence

All runs one test file per process, with `SCRIBE_DATA_DIR` and `SCRIBE_ENV_FILE` exported at the fence (TASK-090). Files in scratchpad .../build/TASK-089.11/.

Red first (kept):
- red-setup_plan.txt - `6 failed, 21 passed, 74 deselected in 7.73s`. The old-format stamp (AC3): `assert 'hf_token' not in ['hf_token', 'llm_provider', ...]`. The null answer (AC4/5): `KeyError: 'questions'`. The added question (AC6): `SystemExit: 2` - `--unasked-only` did not exist.
- red-launcher.txt - `6 failed, 2 passed, 46 deselected in 7.24s`. The gate: `assert False is True` on a stamp nobody can read. `setup_contract`: `AttributeError: module 'myscribe_launcher' has no attribute 'setup_contract'`. The button (AC5): `KeyError: 'Ask me next time'`.

Green:
- green-test_setup_plan.txt - `101 passed in 11.54s`
- green-test_launcher.txt - `53 passed, 1 skipped in 23.04s`
- green-test_setup.txt - `15 passed in 5.67s`
- green-test_env.txt `43 passed, 8 skipped`, green-test_proxy.txt `28 passed`, green-test_credentials.txt `35 passed`, green-test_dotenv_commands.txt `9 passed` - every other file that imports `scribe.setup` or loads the launcher, found with grep and not guessed.

Mutation, on a copy at scratchpad .../TASK-089.11/mut (never the repository): `setup_needed` put back to `return not setup_stamp(layout).exists()`. mutant-launcher.txt: `2 failed, 51 passed, 1 skipped` - the gate test and the fail-open test both bite. `grep -rn MUTANT scribe tests packaging` in the repository finds nothing.

### Collateral, stated rather than surprising

Six existing assertions moved with the stamp shape, each one expected: tests/test_setup.py `test_finishing_writes_the_stamp_so_it_is_asked_once` (`stamp["provider"]` -> the question states, plus "ollama" is not in the file at all); tests/test_setup_plan.py `test_a_start_is_asked_only_what_the_last_sitting_never_put` (:152, its stamp rewritten to the new shape), `test_an_all_skipped_sitting_writes_no_row_and_stamps_every_id`, `test_a_failed_download_still_ends_the_sitting` (now asserts `fetch_models: open`), `test_a_sitting_at_a_terminal_saves_what_was_answered`, and `test_a_typed_secret_is_written_to_its_row_and_to_no_file` (gained: every value in the stamp is one of the four states, so there is no field a secret could arrive in).

One assertion in tests/test_setup.py is deliberately written as a set of what was PUT rather than the whole document: that file does not empty the environment, so this machine's real HF_TOKEN shows up as `not_needed` beside the answers. The comment says so.

### Deliberate non-changes

- `done()` is still "a stamp exists". It gates the tier question in `_questions` and feeds `needed()["asked_before"]`; making it contract-aware is a behaviour change nobody asked for. `states()` is the new reader.
- The stamp is still written with a plain `write_text`, not the temp file + `os.replace` the spec's paragraph mentions. No criterion asks for it, and a half-written file is not valid JSON, so both `states()` and the launcher's gate already read it as "no stamp" and ask again. Reported rather than done.
- The launcher does not yet run `--plan` when the numbers differ. That child, and the dialog drawn from it, are TASK-089.15's rendering work (spec section 3.11). This task ships the gate that tells .15 when to do it, and `--unasked-only` for it to call.

### Not run, with the task that owns it

- Criterion 4's location route (MYSCRIBE_HOME / `--home` / the pointer file, TASK-089.14) and library route (`--setup` and the Setup button only, TASK-089.19): those questions are not in `_questions` yet, so there is nothing to test. Reported, not faked.
- Criterion 4's Setup-button route is TASK-089.15 criterion 8, as this task says twice.
- The six routes that do exist have one test each (`LATER_ROUTES` in tests/test_setup_plan.py), and each asserts the handler is really in `scribe/web/settings.py`'s router, not that the sentence contains the word Settings. `fetch_models` names `--fetch-models` and has its own test that the flag reaches the download.

### Found outside this task's criteria, not fixed

`setup_command` (myscribe_launcher.py:684) still builds `--hf-token <value>`, which TASK-089.09 made exit 2 with nothing written. Somebody who types a token and presses "Save and start" today saves nothing and is told nothing. ADR-015's enforcement note gives that line to TASK-089.15, so it is reported here and left alone - but the gap between .11 and .15 is user-visible.

ADR-015's forbid_pattern over `scribe/setup.py` was not hit: the one flag added is `--unasked-only`.

### Review pass (same session)

Two things a reviewer of `_states` will ask, answered here rather than in code:

1. An `open` an earlier sitting recorded is never downgraded. If somebody's key was refused in sitting 1 (recorded `open`) and they later put it in `.env` instead, the stamp keeps the stale `open` for good: the base carries it, and the `not_needed` pass is a `setdefault` that will not touch a key that exists. It costs nothing - the question is no longer in `_questions`, so no plan lists it and no gate reads it - and clearing it would mean probing credentials at stamp time for a field that is informational. Left as it is, on purpose.

2. `test_a_question_a_later_version_adds_is_asked_exactly_once` now really raises the number: the sitting is held at the contract the stamp records, then `setup.CONTRACT` is raised by one before `--plan --unasked-only`, and the test asserts the plan's number is above the stamp's - the two numbers the launcher compares - before asserting the one question. Without that it injected a question at the same contract and would have passed with no comparison in the code at all. Re-run: tests/test_setup_plan.py `101 passed in 11.96s`.

## Review round (2026-09-22): what three verifiers changed

Fourteen findings, no blockers. Six changed code or tests, five corrected a sentence
in these notes, one was rejected, and the two majors are a release-ordering hand-off
that this task may not fix itself.

### Changed here

**The stamp goes down in one piece** (`scribe/setup.py`, `_write_stamp`). It was a
truncating `write_text`, and the reason given for skipping the spec's atomic write
(section 2, "a temp file, then `os.replace`") described the wrong failure. Under this
contract the file is the only record of what somebody skipped on purpose and of what
the 0.5.x migration carried over: a write that fails half-way does not cost one extra
sitting, it loses those states for good. Now a `setup.json.tmp` beside it and
`os.replace` onto it; `import os` is the only new name. Red first, kept in
`red-atomic-stamp.txt`: with `os.replace` monkeypatched to raise, the old stamp was
gone and the new one was there - `{'hf_token': 'skipped', 'default_tier': 'answered',
'llm_provider': 'answered'} != {'hf_token': 'skipped', 'default_tier': 'answered'}`.
Test: `test_a_stamp_that_could_not_be_written_leaves_the_one_that_was_there`.

**The gate's two type guards are pinned** (`tests/test_launcher.py`, the gate test).
`not isinstance(stamp, dict)` and `not isinstance(spoke, int)` had no test: a verifier
removed both and the whole file still passed. Three lines added to the gate test - a
JSON list, a JSON string and a contract of `"7"` - each asserting the sitting appears.
The docstring's "anything it cannot work out asks" is now a claim a test makes.

**`not_needed` never overwrites an answer, across sittings**
(`tests/test_setup_plan.py`). The `setdefault` in `_states` was reached by every test
through loop order inside one sitting, so the case it exists for was untested. The new
test answers the token in sitting one and something else in sitting two: by then the
row sitting one wrote is a source `credentials.find_all` reports, and only `setdefault`
keeps `answered` in the stamp. Test:
`test_a_credential_this_machine_has_does_not_overwrite_the_answer_that_saved_it`.

**A second mutant isolates the contract comparison.** The first mutation run put
`setup_needed` back to "the file exists" and died at the earlier "a stamp nobody can
read" assertion, so it never reached the contract-6-against-7 lines. On a fresh copy at
scratchpad `.../TASK-089.11/mut2` (never the repository), three one-line mutants, each
biting exactly one test:

    a  `or spoke < shipped` removed     -> tests/test_launcher.py   1 failed, 52 passed, 1 skipped
       (fails at "finished, but before this payload's questions"; every guard above it passes)
    b  `setdefault` -> assignment       -> tests/test_setup_plan.py 1 failed, 102 passed
    c  `os.replace` -> truncating write -> tests/test_setup_plan.py 1 failed, 102 passed

Output in `mut2-a-contract-comparison.txt`, `mut2-b-setdefault.txt`,
`mut2-c-truncating-write.txt`. `grep -rn MUTANT` over `scribe`, `tests` and `packaging`
in the repository finds nothing.

**Four docstrings that said more than the code does.** `question_of` claimed the stamp
"cannot come to name a question the plan never asks"; it guarantees the spelling, not
the asking - `_states` writes `not_needed` for every credential this machine has,
without asking `_questions`, so a machine with an OpenAI key and Ollama chosen stamps an
`llm_key_openai` nobody was asked. Inert (`not_needed` is not in `PUT`, and the gate
reads `contract` and `ended` only), and now said plainly. `default_diarize` is the same
class: `apply` appends it to `answered` although no question has that id. Left as it is
- a non-question id can never match a plan entry - and named rather than claimed away.
`_before_the_contract` promised that a bare-run stamp gets its questions back; it counts
nothing as answered, but `default_tier` is offered only while no stamp exists at all, so
it does not return through any door. Said there, with the reason `done()` was left
alone. And `test_skipping_the_questions_starts_the_app_and_runs_no_setup` still named
"Skip for now", a button the dialog no longer has.

### Corrected in the notes above

- Criterion 1's security sentence credited one assertion with all the work. Three do it:
  the SENTINEL scan reads the whole file text, `set(...questions.values()) <= STATES`
  covers the values inside `questions`, and the exact three-key top level is pinned by
  `test_a_finished_sitting_records_one_state_per_question`.
- Criterion 4: the route tests assert, per question, that the handler this table pairs
  with it exists in `scribe/web/settings.py`. The pairing itself is by hand - nothing
  derives the route from the question's own `answer_later` sentence. Stronger later: put
  the route on the `Question` dataclass, so the sentence and the route come from one
  place. `fetch_models` is the one pinned end to end.
- Criterion 6: the launcher's `spoke < shipped` was pinned by reading and by the
  contract-6-against-7 assertions; mutant a above now pins it dynamically.
- The deviation note about `question_of` is corrected above.

### The two majors: a release-ordering gate, not a defect here

Both are consequences of shipping this gate before TASK-089.15 draws the sitting from
`--plan`, and this task may not build that: it owns the gate, .15 owns the door, and
this task says so twice.

1. **The token path becomes a nag loop for upgraders.** An old-format stamp has no
   `ended`, so the gate opens the sitting - which is criterion 3 working. The launcher
   then renders its four hardcoded questions, and a typed Hugging Face token makes
   `setup_command` build the refused flag, which TASK-089.09 made exit 2 before `.env`
   is read and before anything is written: no row, and no stamp. So the gate fires
   again at the next start, and at every start after. Before this task `setup_needed`
   was "the stamp exists", which kept that path unreachable for anybody who had
   finished the old setup. Correcting what these notes said earlier: such a person is
   not "told nothing" - `_apply_setup` reports *Saving your answers failed with exit
   code 2* (packaging/launcher/myscribe_launcher.py:776) - they are told an exit code
   and lose the sitting.
2. **One sitting of the old questions can shut the gate on the new ones.** The dialog
   still asks the four it has, and answering it stamps contract 2 with `ended`. At the
   next start `2 < 2` is false, so `llm_key_openrouter`, `llm_key_openai` and
   `llm_model_ollama` - the group the parent task names - were never put, have no state,
   and cannot be put again once .15 wires its door. It does not self-heal.

Recommended, and handed to TASK-089.15 rather than done here: **raise `CONTRACT` in .15
when the door is wired.** One line plus a sentence in the `CONTRACT` docstring, and the
sitting reopens once for everybody stamped at 2 in between - including anybody who ran a
build off this branch. Either way, .11 should not reach a real machine ahead of .15; on
the branch, together, it is fine.

### Rejected

`packaging/build_payload.py` does not require `scribe/setup.py` in a payload, so one
without it would give `setup_contract` None and a sitting nobody can stop. Not changed:
the same verifier writes "I did not find a shipping path where this happens - APP_PATHS
copies all of scribe/ - so this is defence, not a live bug", and the scope rule says to
report work outside the criteria rather than do it. Worth one string in that tuple in
whichever task next touches the payload.

### Test runs after the review (one file per process, TASK-090 fence exported)

    final-test_setup_plan.txt        103 passed in 9.33s
    final-test_launcher.txt          53 passed, 1 skipped in 22.15s
    final-test_setup.txt             15 passed in 5.59s
    final-test_env.txt               43 passed, 8 skipped in 4.24s
    final-test_credentials.txt       35 passed in 10.10s
    final-test_proxy.txt             28 passed in 16.28s
    final-test_dotenv_commands.txt   9 passed, 2 warnings in 21.36s

## Verification (orchestrator, 2026-09-22)

Measured here, fenced, not taken from the build report.

### Criterion 2 - the gate is cheap, and reads a number as data

    setup_contract read as text: 2
    scribe.setup.CONTRACT      : 2
    equal                      : True

The text read and the constant cannot drift, which is the whole point of the
anchored regex: `CONTRACT` also appears inside sentences the engine prints, and
an unanchored pattern would have compared the stamp against a number out of an
f-string.

No child, proven rather than read: with `subprocess.Popen` and `subprocess.run`
replaced inside the launcher module by something that raises, 200 calls to
`setup_needed` all answered.

    gate, no child, median: 0.153 ms over 200 calls

The build reports 0.78 ms as its median of its own run; mine is faster, most
likely a warm file cache, and both are the same answer next to a cold `--plan`
child at about 4.2-4.3 s. The 4.6 s figure this replaces was measured against
the real library on 2026-09-20 and is not like for like - it opened a real
database through `db.connect`, which the fence forbids here. Both numbers are
reported rather than one chosen.

`grep -nE "^\s*(import|from)\s+scribe" packaging/launcher/myscribe_launcher.py`
finds nothing (exit 1), so ADR-011's Must holds: the launcher reads `json` and
`re` from the standard library and imports nothing from the app.

### Criteria 1 and 5 - the stamp

A sitting answering the provider and skipping the tier, on a scratch directory
whose `.env` holds a planted token:

    {
      "contract": 2,
      "ended": 1790068654.6413715,
      "questions": {
        "hf_token": "not_needed",
        "llm_key_openrouter": "not_needed",
        "default_tier": "skipped",
        "llm_provider": "answered"
      }
    }
    SENTINEL in stamp: False

Exactly `{contract, ended, questions}`, one state per id, no chosen value and
no provider or tier name - `provider`, `tier`, `hf_token` and `fetched` are
gone from the document. `not_needed` is the state for a credential the
resolver already found. A null answer records that one question as skipped
while the rest of the sitting still ends, which is criterion 5's per-question
control.

### Criterion 3 - an old-format stamp

    old: {"provider": "openrouter", "tier": "max", "hf_token": true, "fetched": ["x"]}
    states() -> {'llm_provider': 'answered', 'default_tier': 'answered',
                 'hf_token': 'answered', 'fetch_models': 'answered'}

So somebody who finished the old setup keeps every old answer as answered and
is asked only what that stamp never covered. Nothing resets the provider or
the tier, because `apply()` never writes what nobody answered.

### Criterion 7 - a deliberate non-behaviour

The gate reads the stamp only and never calls `needed()`. A token removed
after the sitting makes `--plan` list it open again and does not reopen the
sitting. The reason is in `setup_needed`'s docstring, in the gate test and in
the build's notes: the gate is about what was asked, `--plan` is about the
machine now.

### Criterion 8 - the stale reference

The criterion cites `tests/test_launcher.py:378`. That line is
`test_the_tools_are_installed_into_the_home_and_the_app_uses_them`, an ffmpeg
test. The test it means is `test_setup_is_needed_until_the_app_says_it_is_done`
at `:423-433`, which wrote `{}` as a stamp and asserted the gate closed. It was
renamed and gained assertions rather than losing any.

### Whole suite

One file per process, fenced: **2866 passed, 10 skipped, 0 failed, 0 errors** over 81 files. It reconciles exactly with collection - `pytest -q --collect-only tests` reports "2876/2886 tests collected (10 deselected)" and 2866 + 10 = 2876 - so nothing stalled or collected short. That is +20 on the run after TASK-089.09 (2856 collected): 16 in `tests/test_setup_plan.py` (87 -> 103) and 4 in `tests/test_launcher.py` (49 -> 53).

### Criterion 4, ticked with what it could not reach

The behaviour is pinned: a skipped question is not asked at the next start and is asked again by `--setup`. Six `answer_later` routes are tested against the handlers in `scribe/web/settings.py`, and `fetch_models` end to end. Three routes could not be tested because the questions do not exist yet: the location (TASK-089.14), the library (TASK-089.19) and the Setup button (TASK-089.15 criterion 8, which the task text places there twice). Nothing derives a route from a question's own sentence - the table is hand-written, and the notes say so rather than claiming more.

### Release ordering - the chain is now three tasks long

TASK-089.09 already may not ship alone, and this task joins it. The gate here
correctly opens the sitting for an old-format stamp; the launcher then renders
its four hard-coded questions, and a typed Hugging Face token makes
`setup_command` build the flag TASK-089.09 refuses with exit 2 before anything
is written. No settings row, no stamp, so the gate fires again at every start
and the person is told only "Saving your answers failed with exit code 2".

**TASK-089.09, TASK-089.11 and TASK-089.15 ship together or not at all.**
TASK-089.15 is what removes the pass-through and draws the dialog from
`--plan`; it needs TASK-089.14 and TASK-089.13 first.

### Left as the build left it, and why

* `setup.json.tmp` survives a failed atomic write. Deliberate: deleting it in a
  `finally:` would throw away the only copy of the states that could not be
  written, and the SENTINEL test's rglob already reads every file under the
  data directory, so it cannot hide a secret.
* `tests/test_setup.py`'s fixture does not clear this machine's credential
  variables the way `tests/test_setup_plan.py`'s autouse fixture does, so a
  real `HF_TOKEN` shows in its stamp as `not_needed`. Harmless here and
  another task's call.
* `packaging/build_payload.py` does not require `scribe/setup.py` in a
  payload. One string at `build_payload.py:96` for whichever task next touches
  the payload; a defence, not a live bug.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The gate that decides whether the first-run sitting appears is now defined, and it is cheap. The stamp becomes {contract, ended, questions: {id: answered|skipped|not_needed|open}} - states and nothing else, no chosen value and no provider or tier name - and the launcher decides from that stamp plus one number it reads as text out of scribe/setup.py, the way it already reads the version. Settled here and recorded: the number ships as the CONTRACT constant and not as a separate scribe/setup_contract.json, because adding a question means editing _questions in that same file, so the number that must rise lives beside the change that forces it. Verified by the orchestrator: the text read equals scribe.setup.CONTRACT (both 2), the gate starts no child - proven with subprocess.Popen and subprocess.run replaced by a raiser inside the launcher module, 200 calls - and costs 0.153 ms median here against a cold --plan child at about 4.3 s; the build measured 0.78 ms on its own run and both are reported rather than one chosen. A real sitting writes exactly the three top-level keys with a planted sentinel absent from the file; an old {provider, tier, hf_token, fetched} stamp maps to four answered states, so somebody who finished the old setup keeps every answer and is asked only the new questions, once. The gate deliberately does not ask about the machine: a token removed later makes --plan list it open and does not reopen the sitting. The launcher imports nothing from scribe (ADR-011), checked by grep. Whole suite over 81 files: 2866 passed, 10 skipped, 0 failed, reconciling exactly with 2876 collected. Criterion 8's cited line was stale - tests/test_launcher.py:378 is an ffmpeg test; the test meant was at :423-433 and was renamed rather than deleted. MUST NOT REACH A REAL MACHINE AHEAD OF TASK-089.15: the gate correctly opens the sitting for an old stamp, the launcher then renders its hard-coded questions, and a typed token builds the flag TASK-089.09 refuses with exit 2 - no row, no stamp, so it fires again at every start. TASK-089.09, TASK-089.11 and TASK-089.15 ship together.
<!-- SECTION:FINAL_SUMMARY:END -->
