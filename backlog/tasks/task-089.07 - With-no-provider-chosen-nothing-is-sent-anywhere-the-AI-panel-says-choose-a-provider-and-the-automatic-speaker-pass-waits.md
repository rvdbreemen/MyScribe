---
id: TASK-089.07
title: >-
  With no provider chosen nothing is sent anywhere: the AI panel says 'choose a
  provider' and the automatic speaker pass waits
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-21 06:11'
labels:
  - llm
  - security
  - ui
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 144000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Decided by Robert on 2026-09-20 (brief: R1, W3), and recorded in ADR-016: a missing provider row selects no provider, and nothing is sent until somebody has chosen. This task builds under ADR-016, which is decided before this code lands (TASK-089.02 criterion 8; brief: G1). Today a missing llm_provider row falls through to OpenRouter. DEFAULT_PROVIDER is OpenRouterProvider.name (scribe/llm/tasks.py:456), and default_provider() returns it whenever the stored name is missing or no longer registered (scribe/llm/__init__.py:164-175). Three more fall-throughs sit in the stage itself: `ctx.params.get("provider") or tasks.DEFAULT_PROVIDER` at scribe/stages/llm_stage.py:94, :271 and :345.

What a user hits: they press 'Skip for now', or install from a clone and are never asked. With no key, every AI action fails for lack of a credential for a cloud provider they never chose, while the dialog they skipped had Ollama preselected (packaging/launcher/myscribe_launcher.py:558). With an OpenRouter key anywhere on the machine - scribe/llm/base.py:196-203 records that this machine has one machine-wide under HKLM - every transcript not pinned private can go to OpenRouter without anybody having chosen it.

The largest trigger is not a click. sweep_speaker_passes (scribe/stages/finalize.py:155) runs at every app start from the lifespan (scribe/app.py:261), over the whole back catalogue, and its docstring says 'No ceiling on how many it queues'. It goes through queue_speaker_pass (:384), which asks llm.default_provider(conn) at :437. Point MyScribe at an existing library, skip the provider question, and the first start queues one cloud job for every diarized recording that was never asked. That is the path TASK-089.19 opens, so this lands first.

scribe/setup.py:13-14 already argues the decision: 'sending a private recording to a cloud model is not a default anybody should inherit'. Once every question can be skipped, the skip path is the default most people get. It reverses the recorded spec decision 'Defaults: commercial providers (user decision)' (docs/superpowers/specs/2026-09-01-myscribe-design.md:219), so the spec and the CHANGELOG each get a line. Recordings pinned private are protected either way, and that rule does not move.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Red first: with no llm_provider row, default_provider(conn) answers 'openrouter' today. After the change it answers 'no provider'. A stored name that is no longer registered does the same without raising, and a transcript page with such a row still renders 200.
- [x] #2 sweep_speaker_passes, red first, on a scratch database with N diarized, never-asked, non-private recordings and no llm_provider row: the app's lifespan queues N llm jobs for openrouter today. After the change it queues none, and the test counts the job rows. The run is on a scratch or copied library, never the live one.
- [x] #3 The pass waits; it is not lost. Once a provider is saved, the same recordings are queued no later than the next app start, and a test shows it. The notes say whether saving the provider also triggers the sweep.
- [x] #4 Each of the 8 default_provider() call sites handles 'no provider', with one test per site: scribe/stages/finalize.py:437; scribe/web/ai_ui.py:650, :837, :953, :1016, :1181 and :1202; scribe/web/library.py:994, which goes through the alias at ai_ui.py:143.
- [x] #5 Each of the three fallbacks at scribe/stages/llm_stage.py:94, :271 and :345 refuses a job that names no provider, with a sentence, instead of sending it to OpenRouter. One test per fallback. A grep shows no remaining fall-through to tasks.DEFAULT_PROVIDER.
- [x] #6 POST /media/{id}/ai/{kind} with no row and no provider in the form creates no job row - the test counts them - and sends nothing. The refusal names the fix and where it lives: 'choose a provider in Settings > AI providers', with the anchor. A provider picked in the panel for that one request still works: that is somebody choosing. Rendering that refusal as a card with a working link is TASK-089.10's, which builds the same card for a chosen provider that cannot answer; splitting it here on 2026-09-21 kept one card in one place instead of two.
- [x] #7 A machine whose row exists behaves exactly as before. The existing tests for a stored provider and for the private pin stay green, unchanged.
- [x] #8 docs/superpowers/specs/2026-09-01-myscribe-design.md:219 gains a line saying 'Defaults: commercial providers' was reversed by Robert on 2026-09-20, and why. CHANGELOG.md gains a line under [Unreleased]. Both diffs are shown.
- [x] #9 The sweep test is shown to bite: on a COPY of the repo the fall-through is put back, and the red output is shown.
- [x] #10 What the panel shows, not only what the POST answers. Red first: on GET of the transcript page with no llm_provider row, the provider select (scribe/templates/_ai_region.html:102) has no provider preselected, and the panel shows the 'choose a provider' sentence with the link to Settings > AI providers. Today ai_context takes default_provider(conn) as the chosen one (scribe/web/ai_ui.py:650-652), so the page renders OpenRouter as selected on a machine where nobody chose it. One test reads the rendered HTML.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Baseline first, fenced (SCRIBE_DATA_DIR + SCRIBE_ENV_FILE), one file per pytest process, output to a file: test_stage_finalize, test_web_ai, test_web_library, test_app, test_llm_chat, test_llm_speakers, test_llm_tasks, test_web_settings, test_render. Keep it, so "which test needed a provider row" is evidence and not a surprise.
2. Red first (#1, #2, #10): default_provider answers "" for a missing row and for a stored name no longer registered, without raising; the transcript page with such a row still renders 200; the app lifespan on a scratch db with N diarized, never-asked, non-private recordings queues 0 llm job rows; the rendered transcript page preselects nothing and carries the "choose a provider" sentence with the link. Keep the failing output of all four.
3. scribe/llm/__init__.py:175 answers "" instead of the shipped default; the docstring says why an unregistered name must not raise either. "" is the value everywhere: falsy, what setting_value already returns, and default_model(conn, "") already degrades to "".
4. scribe/llm/tasks.py:456 DEFAULT_PROVIDER is then unreferenced and its docstring would be a lie ("what an llm job uses when its params name no provider"). Remove it, after a grep proves nothing else reads it.
5. finalize.queue_speaker_pass:437 returns None on "" BEFORE llm.provider_class(), which raises ValueError on "". The sweep's loop (:215) is not wrapped in try/except and runs from the lifespan (app.py:261), so the order of those two lines is the difference between "queues nothing" and "no app start". It marks nothing asked, so the next start after somebody chose catches up (#2, #3). One test for the raise, beyond #2's job count.
6. llm_stage.py:94, :271, :345 fail a job whose params name no provider with a sentence, the way _kind and _media_id already refuse (#5). They do not ask default_provider(): that would be the same fall-through through another door, and a job written before this change keeps openrouter in its params (ADR-016, Negative). Then the ADR's grep returns nothing.
7. The panel (#6, #10): provider_choices gains a "choose a provider" entry, _ai_region.html:102 marks it selected (a browser picks the first option when none is), and the sentence with the link to Settings > AI providers goes OUTSIDE the collapsed <details class="options">, or nobody reads what the test finds. ai_run and chat_ask answer with that card and write no job row when neither the form nor the row names a provider; a provider picked in the form still works.
8. The settings page (#4): settings_context:1181 and _settings_llm.html:53-55 get the same placeholder and sentence; effective_llm:1202 says "no provider chosen" rather than rendering an empty label.
9. library.py:994 (the bulk labels pass) queues nothing and says why, instead of a 500 out of provider_class("").is_local (#4).
10. One test per call site (8) and per fallback (3), named for the site (#4, #5).
11. #3: save the row, run the lifespan again, count N jobs. The notes will record what was read, not built: save_llm_defaults (settings.py:883) writes the row and does not call the sweep, so the catch-up is the next app start.
12. #8: the [Unreleased] CHANGELOG entry from edbc16c already ends "Nothing is built yet"; that bullet is amended rather than contradicted by a second one. 2026-09-01-myscribe-design.md:219 gains the reversal line. Both diffs shown.
13. #9: copy scribe/, tests/ and pytest.ini to the scratch mut/ dir, put the fall-through back THERE with a script that asserts the new text is present, run the sweep test with the project's venv python, keep the red output; afterwards grep MUTANT over the repo finds nothing.
14. #7 and the gate: re-run the baseline files, and report which of them needed a provider row added - none of the stored-provider or private-pin tests. The ADR judge is run by hand: this checkout has no pre-commit hook installed (.git/hooks holds only .sample files) and ADR-016's front matter says binding: false, gate: null, so "live in the pre-commit judge" is checked rather than assumed.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (2026-09-21, agent)

Built ADR-016. Every pytest run below was fenced (SCRIBE_DATA_DIR +
SCRIBE_ENV_FILE pointed at a scratch dir), one test file per process, output
kept in the scratchpad build/TASK-089.07 directory.

### What changed

* `scribe/llm/__init__.py` - `default_provider()` answers `""` for a missing row
  and for a stored name this version no longer registers, without raising.
* `scribe/llm/tasks.py` - `DEFAULT_PROVIDER` removed (nothing read it any more;
  grepped over scribe/, tests/, docs/, packaging/, scripts/). The `openai_like`
  import went with it: that constant was its only use in the module.
* `scribe/stages/llm_stage.py` - new `_provider(ctx)` beside `_kind`/`_media_id`
  refuses a job whose params name no provider, with a sentence. Used at :94,
  :271 and :345. It does NOT consult `default_provider()`: that would be the
  same fall-through through another door.
* `scribe/stages/finalize.py` - `queue_speaker_pass` returns None on the empty
  name BEFORE `llm.provider_class()`, which raises on it. The sweep's loop has
  no try/except and runs from the lifespan, so the order of those two lines is
  the difference between "queues nothing" and "no app start". Nothing is marked
  asked, so the next start catches up.
* `scribe/web/ai_ui.py` - `SETTINGS_ANCHOR` + `NO_PROVIDER_YET` (one spelling);
  `_chosen_provider(conn, asked)` refuses with 400 before any enqueue, used by
  `ai_run` and `chat_ask`; `effective_llm` answers label "no provider chosen".
* `scribe/web/library.py` - `_enqueue_labels` queues nothing and returns the
  sentence, before `provider_class("")` could turn a bulk button into a 500.
* Templates `_ai_region.html`, `chat.html`, `_settings_llm.html` - an
  empty-value placeholder that CARRIES `selected`, and the sentence with a real
  link to /settings#llm-providers OUTSIDE the collapsed `details.options`.
* `CHANGELOG.md` [Unreleased] - the ADR-016 bullet amended ("Nothing is built
  yet" removed) plus a second bullet for what landed.
* `docs/superpowers/specs/2026-09-01-myscribe-design.md` section 6 - the
  reversal line under "Defaults: commercial providers", naming Robert and
  2026-09-20 and why.

### Evidence

Baseline - all 17 test files that grep showed touch this code, green BEFORE the
change: test_app 17, test_llm_chat 27, test_llm_labels 19, test_llm_live_log 10,
test_llm_privacy 19, test_llm_selftest 14, test_llm_speakers 43,
test_llm_task_providers 9, test_llm_tasks 153, test_pipeline_e2e 54 (+1
deselected), test_render 52, test_setup 9, test_stage_finalize 25, test_web_ai
111, test_web_library 81, test_web_settings 46, test_web_transcript 98.

Red first (kept as red-*.txt), against today's code:
* test_llm_tasks: 5 failed, 154 passed - default_provider answered 'openrouter',
  and the three runner jobs finished instead of being refused.
* test_stage_finalize: 4 failed, 25 passed - queue_speaker_pass returned a job
  id, and one app lifespan over 3 diarized, never-asked recordings queued 3 llm
  jobs whose params said provider openrouter.
* test_web_ai: 8 failed, 113 passed - no empty option in the select, no
  sentence, and both POSTs answered 200 and enqueued.
* test_web_library: 1 failed, 81 passed - the bulk pass queued 2 openrouter jobs.

Green after (kept as green-*.txt): all 17 files pass. New totals where they
moved: test_llm_tasks 159, test_stage_finalize 29, test_web_ai 121,
test_web_library 82.

ADR-016's own verification grep after the change:
grep -rnE --include=*.py "(or|else) tasks\.DEFAULT_PROVIDER" scribe/ - no
matches. It returned four lines on 2026-09-20.

Mutation (criterion 9): scribe/, tests/ and pytest.ini copied to the scratch
mut/ directory. The copy ran GREEN unmutated first (29 passed), so a later red
is a bite and not an import error. mutate.py asserts the new text is present
before putting the fall-through back; test_stage_finalize on the mutated copy
then gave 4 failed, 25 passed - exactly the four new sweep tests.
grep -rn MUTANT over the repository's scribe/ and tests/ finds nothing.

ADR judge, run by hand over `git diff -- scribe/` (adr-kit declarative pass):
exit 0, verdict ok, 11 ADRs checked, 0 violations, 0 advisories.

### Facts read rather than built

* Saving the provider does NOT trigger the sweep. save_llm_defaults
  (scribe/web/settings.py:883) writes the row and returns _llm_answer; there is
  no call to finalize.sweep_speaker_passes. The catch-up is therefore the next
  app start, and that is the path the test proves. No trigger was implemented -
  it is outside the criteria.
* save_llm_defaults already ignores an empty `provider` field (`if wanted:`),
  so posting the new placeholder does not clear a row somebody already saved.
* The task says ADR-016's Enforcement "is live in the pre-commit judge". Not in
  this checkout: .git/hooks holds only .sample files, and ADR-016's front matter
  says binding: false, gate: null. The judge was run by hand instead. Its
  "added lines only" behaviour was taken on the record's word - adr-judge's
  source is not in this repository and was not read.

### Tests that needed a provider row added (criterion 7)

Three existing tests leaned on the fall-through and now write the row
explicitly. None is a stored-provider or private-pin test: all 19 private-pin
tests in tests/test_llm_privacy.py and every existing sweep test (which already
called _set_provider) stayed green unchanged.

1. tests/test_web_ai.py::test_the_collapsed_block_says_who_will_answer_and_whether_it_leaves
   - asserts "OpenRouter" in the collapsed summary line; with no row that line
   now reads "no provider". Given llm_provider = openrouter.
2. tests/test_web_ai.py::test_the_privacy_warning_stays_out_of_the_collapsed_block
   - needs a non-local provider selected for the warning to render. Same row.
3. tests/test_pipeline_e2e.py::test_a_re_transcription_asks_who_is_speaking_again
   - asserts two speaker passes are queued; with no row none is. Given
   llm_provider = ollama.

### Deviations from the recorded plan, and why

* The placeholder lives in the TEMPLATES, not in provider_choices(). Plan step 7
  put it in provider_choices. Two facts read from the code say not to:
  _ai_region.html:63-67 loops over the choices for the privacy warning and would
  have rendered "Choose a provider is not on this machine: the transcript is
  sent to it"; and tests/test_web_ai.py:308 asserts
  summary_line([], "") == "no provider", which a selected placeholder would
  change - a test criterion 7 protects. A one-line
  {% if not ai.provider %}<option value="" selected> in each of the three
  templates does the same job and touches nothing else.
* Criterion 6's "card" is the app's EXISTING refusal shape, not a bespoke 200.
  ai_run and chat_ask answer 400 with detail = NO_PROVIDER_YET, which carries
  both the words "choose a provider" and /settings#llm-providers. That is the
  same shape as the 403 private pin and the 409 no-transcript, and app.js
  (wireHtmxErrors, :365-380) already shows a 4xx's JSON detail to the person in
  the page's flash line. The rendered anchor is on the panel itself
  (criterion 10). A 200 for a refusal was rejected as the dishonest option.
  Flagged for the orchestrator to judge.

### Something that went wrong, and was fixed

The FIRST red run of the three llm_stage tests made real calls to OpenRouter.
With no provider named the fall-through picked openrouter, a key resolved from
this machine's registry without anybody typing one, and all three jobs finished
(exit 0, 38.5s wall). What was sent was tests/seed.py's fake transcript, not any
real recording - but it was a live, billed call from a test, and it was my
mistake, not a property of the tests as they now stand. The three tests now
register a fake under that name for the length of the test
(_nothing_leaves_the_machine), the red was re-taken hermetically (5 failed, 154
passed, 15.3s) and that hermetic run is the red kept as evidence. After the
change the tests fail before any provider is built, so no call is possible.

Two other machine facts worth recording: tests/test_pipeline_e2e.py and
tests/test_web_transcript.py each hit the documented Windows socketpair stall
once during the baseline (faulthandler dump at 120s, in
_fallback_socketpair -> accept). Both passed on a straight retry; the stall is
intermittent and unrelated to this change.

### Not done, and why

* Jobs the fall-through already queued keep openrouter in their parameters and
  are not recalled. ADR-016 records that as a Negative; out of scope here.
* Acceptance criteria are left unchecked and the status unchanged, per the
  orchestrator's instruction.

## Addendum (2026-09-21, agent) - three corrections and one thing found

### 1. Criterion 1's "no provider" is the empty string, not that literal text

`default_provider()` returns `""`, not the words "no provider". Pinned in the
plan and kept: a display string in that slot would be read as a provider name
by everything downstream. `provider_choices`, `default_model` and the templates
all treat `""` as absence, and `provider_class("")` raises - which is why the
guards sit in front of it. The words "no provider" appear where a person reads
them: `summary_line` (unchanged), `effective_llm`'s label, and the three
templates.

### 2. Who WRITES the llm_provider row (ADR-016's Must, checked)

Every earlier grep in these notes was for readers. The write side, from
`grep -rn "llm_provider\|PROVIDER_SETTING" --include=*.py scribe/ packaging/ scripts/`:

* `scribe/web/settings.py:898` - `save_llm_defaults`, and only `if wanted:`.
  Somebody pressed Save on a form whose label is "Default provider". Honours
  the Must.
* `scribe/setup.py:113` - `apply()`, and only `if answers.provider:`. An empty
  answer writes nothing. Honours the Must.

Nothing else writes it. Those are the only two.

### 3. Out of scope, found while checking 2: the launcher writes a preselection

`packaging/launcher/myscribe_launcher.py:558` sets the provider radio group to
`tk.StringVar(value="ollama")`, and `save()` at :578-583 passes
`provider=provider.get()` unconditionally, which :477 turns into
`--provider ollama`. So pressing "Save and start" WITHOUT touching the provider
radios writes `llm_provider = ollama` - a row written from a default rather
than from an answer somebody gave, under a heading that reads "Answers about a
transcript" and does not say it chooses the provider. ADR-016's Must says only
an answer somebody gave writes the row, "to a question whose text says it
chooses the provider".

Not a leak: Ollama is local, so nothing leaves the machine either way. Not
fixed here either - it is the launcher and ADR-015's territory, and TASK-089.07's
criteria are about a machine with NO row. Reported for the orchestrator.

The good half of the same reading: "Skip for now" (:590) calls `win.destroy()`
without `save()`, so `answers` stays empty, the dialog returns None, no
`--provider` is passed and `setup.apply` writes nothing. The skip path now
sends nothing, which is exactly what this task was for.

### 4. The OpenRouter calls in the first red run: what is fact and what is inference

Fact: the three jobs reached `done` (`runner.main` returned 0) and that run took
38.5s against 15.3s for the hermetic re-run - a 23-second difference with no
other change. Inference from those two: the fall-through built the real
`OpenRouterProvider`, a key resolved from this machine's registry, and three
requests went out.

Not confirmed: I went looking for the `llm_output` rows to prove it and the
pytest tmp databases for that run had already rotated out
(`pytest-of-Robert` keeps the last three basetemps). So read the claim as a
strong inference, not as something I verified in the data.

## What the review changed (2026-09-21, fixer)

Three verifiers reported ten findings. Two led to a code change; the rest are
rejected or recorded, with the evidence, in the report to the orchestrator.

* **The refusal sentence promised a control one screen does not have.**
  `NO_PROVIDER_YET` ended "or pick one for this request", and the library's
  bulk action posts `action` and `ids` only - that page has no provider
  select, so a person who ticked forty rows was sent looking for one. Split in
  `scribe/web/ai_ui.py`: `_NO_PROVIDER_LEAD` holds the half that is true on
  every screen (one spelling, which was the original argument), `NO_PROVIDER_YET`
  closes it for the bulk notice, and `NO_PROVIDER_FOR_REQUEST` adds the clause
  for the panel and the chat form, which each carry a select. Red first:
  fix-red-test_web_library.txt, "'for this request' is contained here: pick one
  for this request." The panel and chat refusals now assert the clause
  positively, so the two constants cannot quietly drift back into one.
* **Two existing tests that the implementation's file set missed.**
  `tests/test_library_row_meta.py` posts a bulk label and leaned on the
  fall-through, like the three already named in the notes above. Both failed
  against the change - fix-red-test_library_row_meta.txt, `assert [] == [1, 2, 3]`
  for `test_labelling_a_selection_queues_one_job_per_file` and a second for
  `test_a_file_without_a_transcript_is_skipped_and_said_so`. Given
  `_set_provider(conn, "ollama")`, the helper the sibling label tests next door
  already use. That file was not among the 17 run before, and neither were
  test_params_door, test_web_exports and test_llm_ollama.

Re-ran 16 files after the fixes, fenced, one per process (final-*.txt), all
green: test_web_ai 121, test_web_library 82, test_library_row_meta 24,
test_llm_tasks 159, test_web_url_dialog 90, test_ingest_watching 82,
test_glossary 69, test_web_transcribe_dialog 54, test_web_exports 52,
test_llm_providers 51, test_web_jobs 43, test_llm_ollama 39, test_web_scaffold
27, test_params_door 26, test_web_recorder 24, test_setup 9. test_web_ai hit
the documented Windows socketpair stall once (faulthandler in TestClient
__enter__); the process was stopped and the file re-run.

Not changed, reasons in the report: criterion #6's "card" (the design puts the
card in TASK-089.10, installer-design.md:900-905, and a 200 carrying a partial
for a refused request would contradict the app's own 403 and 409 shapes);
`default_provider()` answering "" rather than the words "no provider"; the
three existing tests edited earlier; the launcher radio at
packaging/launcher/myscribe_launcher.py:558; and installer-design.md:150-151,
which sits under "What exists today (every line below was opened on
2026-09-20)" with no heading in between, so it is a dated survey and not a
present-tense claim.

### The split is shown to bite

The negative half had a real red (the bulk notice). The positive half -
"for this request" IS in the panel's and the chat form's refusal - passed
before the fix and after it, so it needed its own proof.

A fresh copy of the fixed `scribe/`, `tests/` and `pytest.ini` in the
scratchpad `mut-fix/` ran green unmutated first (mut-fix-clean-test_web_ai.txt,
121 passed in 35.43s). One mutation then collapsed the split - `_chosen_provider`
raises `NO_PROVIDER_YET`, the bulk action's sentence, instead of
`NO_PROVIDER_FOR_REQUEST` - with a script that asserts the current text is
present first, so a no-op mutation is impossible. Expected kills were written
down by name before the run. mut-fix-red-test_web_ai.txt: "2 failed, 119
passed", and the two are exactly
test_asking_with_nobody_chosen_writes_no_job_and_says_where_to_choose and
test_a_chat_turn_with_nobody_chosen_writes_no_job_and_says_where_to_choose.
The repository was never mutated: `grep -rn MUTANT` over scribe/ and tests/
finds nothing, and scribe/web/ai_ui.py:753 still raises
`NO_PROVIDER_FOR_REQUEST`.

One note on timestamps: the first attempt at final-test_web_ai.txt stalled
(the documented Windows socketpair hang in TestClient.__enter__) and that file
held a faulthandler dump until the re-run overwrote it with the green summary.
The green run is the later one, not the earlier.

Verified independently by the orchestrator on 2026-09-21 before the criteria were checked.

The whole suite, one file per process, all 76 files, fenced: 2572 passed, 1 failed, 10 skipped. Against the same run before this task (2440 passed, 1 failed) that is 132 tests more and the same single failure - tests/test_feed_first_episode.py, TASK-091, a data path of 164 characters truncating a feed's refusal past the floor it names. The same file passes with a short fenced path: 13 passed. Not this change.

ADR-016's own verification grep, run by the orchestrator: grep -rnE --include=*.py '(or|else) tasks\.DEFAULT_PROVIDER' scribe/ returns nothing, where it returned four lines on 2026-09-20. adr-judge: 0 violations over 11 records.

The live library was not touched: data/ and data/myscribe.db carry the same modification times before and after (2026-09-20 22:17:57 and 2026-09-15 23:19:54).

Criterion #6 was amended before it was checked, and the amendment is visible in its own text. It asked for 'a card that links to Settings'. What was built refuses with HTTPException(400) whose detail carries the sentence and the anchor, and app.js renders a detail with textContent, so the anchor is inert - the safety half is proven and mutation-proven, the presentation half is not built. The fixer rejected this finding by saying the design spec assigns the card to TASK-089.10. Checked: the spec (lines 900-905) and TASK-089.10 criterion #1 both cover a provider that IS chosen and cannot answer, which is a different case, so the card for 'nobody has chosen' was assigned nowhere. It is now TASK-089.10's sixth criterion, with a note there saying what it inherits, and #6 here was narrowed to the safety half. One card, in one place.

One thing left undone and not in these criteria: packaging/launcher/myscribe_launcher.py:558 preselects the provider radio to ollama and save() passes it unconditionally, so 'Save and start' writes llm_provider from a default rather than from an answer. Not a leak, Ollama being local, but in tension with ADR-016's Must that only an answer somebody gave writes the row. It is launcher territory (ADR-015) and needs its own task.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
With no provider chosen, nothing is sent anywhere.

A missing llm_provider row fell through to OpenRouter (scribe/llm/tasks.py:456, scribe/llm/__init__.py:175, and three more in the runner). default_provider() now answers the empty string for a missing row and for a stored name this version no longer registers, without raising, and DEFAULT_PROVIDER is gone. Each of the eight call sites handles it: the panel preselects an empty placeholder and says 'choose a provider in Settings > AI providers', Settings says the same, the bulk action's notice says it in the words that screen can act on, and a job whose parameters name no provider ends in the runner with a sentence instead of a request.

The widest path was never a button. sweep_speaker_passes runs at every app start over every diarized recording nobody was asked about, with no ceiling; on a machine that adopted a back catalogue it would have queued one cloud job per recording for a provider nobody chose. It now queues nothing and marks nothing asked, so the first start after somebody chooses catches the whole catalogue up - proven on a scratch library: three rows before, none after, three again once the row exists.

A machine whose row exists behaves exactly as before, and the private pin is untouched.

Verified: 18 tests red first, 21 added, the whole suite fenced at 2572 passed against 2440 before, ADR-016's own grep returning nothing where it returned four lines, and mutation proof on copies for both the sweep and the refusal. The one failure is TASK-091, a long path truncating an unrelated message.

Criterion #6 was narrowed before it was checked: the refusal names the fix but is not yet a card with a clickable link, and that card is now TASK-089.10's sixth criterion so there is one card in one place. Left open and noted: the launcher preselects Ollama and writes that row without anybody answering, which needs its own task.
<!-- SECTION:FINAL_SUMMARY:END -->
