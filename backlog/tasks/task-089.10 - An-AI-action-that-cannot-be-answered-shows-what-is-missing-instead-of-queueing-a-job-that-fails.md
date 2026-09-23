---
id: TASK-089.10
title: >-
  An AI action that cannot be answered shows what is missing instead of queueing
  a job that fails
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-23 05:53'
labels:
  - llm
  - web
  - ui
dependencies:
  - TASK-089.06
  - TASK-089.07
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 147000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ai_run checks the kind, the transcript, the private pin, the prompt and the window, and nothing about a key or about Ollama (scribe/web/ai_ui.py:815-870). Its own docstring says the checks in front of the row are 'the ones that cost nothing and would otherwise become a failed job somebody has to read'. A missing key is exactly that, and it is not among them.

So a skipped key, a stopped Ollama or a model that was never pulled becomes an LLM_FAILED job on the board - after three retries in Ollama's case, in a reader's scratch run on 2026-09-20. queue_speaker_pass has the same blind spot for the automatic pass (scribe/stages/finalize.py:437-452).

This is the safety net that makes skipping honest. Every 'decide later' in the sitting ends here, as a card that names the fix, and not as a failed job. TASK-089.07 handles 'no provider chosen'; this handles 'chosen, but cannot answer'.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 POST /media/{id}/ai/{kind} answers with the panel and a card when the chosen provider cannot answer: no key, Ollama not running, or the saved model not pulled. The card names the fix and where to do it. No job row is created, and a test counts the rows. Each of the three is red first.
- [x] #2 The check uses the model the job would use: the saved row, not the class default (TASK-089.06).
- [x] #3 The check makes no remote call: key presence and the loopback probe only, bounded the way available() already is. Nothing is loaded in the web process (ADR-001).
- [x] #4 The automatic speaker-naming pass in finalize skips with a run note when the provider cannot answer, and sweep_speaker_passes queues nothing for it. The note says what to fix.
- [ ] #5 A provider that can answer behaves exactly as before. The existing ai_run tests stay green, unchanged.
- [x] #6 A request that names no provider at all - no row, nothing in the form - comes back with the same card, naming the same fix (choose a provider in Settings > AI providers) and creating no job row. TASK-089.07 proved the safety half on 2026-09-21 and refuses with those words; what it does not have is the card and a link a person can click, and the card is built here so there is one of it. Red first against the plain 400 that task left behind.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. The card lives in one place: `_ai_output.html` renders `panel.blocked` (sentence + the `/settings#llm-providers` anchor as a real link). `panel_for` takes `blocked=` and only the POST fills it, so a page render and a poll still probe nothing.
2. The check is `ai_ui` asking `available()`, which already owns the three fix sentences: build `llm.provider_class(name)(conn)`, point it at the model the job would use (only the local runtime's availability depends on a model - `openai_like.available` is key presence, :165-172), `try/finally close()`, swallow `base.LlmError` the way `provider_rows` does. No remote call, nothing loaded: key presence plus Ollama's PROBE_TIMEOUT loopback GET, `trust_env=False` kept (ADR-001, TASK-089.05). "Answered oddly" blocks too - it is also a provider that cannot answer.
3. `ai_run`: the check sits after `_refuse_if_private`, once `params["model"]` is resolved, before `_refuse_if_window_too_small` and before the enqueue. A reason means the panel with the card, 200, no job row - on the non-htmx path as well, because the 303 redirect would land on a page with neither job nor card.
4. Criterion 6: an empty provider is answered with the same card inside `ai_run`, before `_chosen_provider`. That function is untouched, so chat and the library's bulk action keep TASK-089.07's 400 and its two sentences.
5. Criterion 4: `queue_speaker_pass` asks the same question in its existing lazy-import block and returns None, so the sweep queues nothing for free. `finalize.run` writes the note on `run.params_json` and clears it when the pass is queued (the shape `diarize._note_run` already uses); the sweep writes none - it would rewrite one per catalogue row at every start.
6. The note surfaces beside the diarization note: `transcript.page_context` and `_transcript_panel.html` (TASK-089.08: a note nobody reads is not a fix). That template is the one file outside scribe/web/* and finalize.py.
7. Red first in tests/test_web_ai.py, three shapes, each counting `job` rows and asserting the anchor: no key (the test deletes every `key_env_vars` name itself - conftest does not stub `os.environ`), Ollama not running (`tags` raises NothingAnswered), the saved model not pulled (`tags` answers with another model). Plus criterion 6's card, red against today's plain 400.
8. Red first in tests/test_stage_finalize.py: a provider chosen that cannot answer queues nothing and leaves the note on the run; and in tests/test_web_transcript.py: that note is on the page.
9. Criterion 5 is proven by a fixture that makes the named provider read as ready, plus the unchanged params assertion of `test_posting_an_action_enqueues_an_llm_job_with_the_right_params`.
10. FINDING, for the orchestrator to rule on before the code is written: criterion 5's second sentence cannot hold. The eight ai_run tests post provider=ollama with models nobody pulled and request no fixture, so today they are answered by this machine's daemon; `_refuse_if_window_too_small` already probes it through `window_for_model`. They need the ready fixture. Criterion 6 itself moves `test_asking_with_nobody_chosen_writes_no_job_and_says_where_to_choose` from 400 to the card. About sixteen tests in tests/test_stage_finalize.py set a provider and expect a job, and need the same fixture.
11. Method: every pytest process with the TASK-090 fence exported, one test file per process, output to a file. The tests bite on a copy outside the repository, one mutation per run.

12. Correction to 5: `finalize.run` asks the readiness question itself, before `queue_speaker_pass`, and writes the note only on that answer; `queue_speaker_pass` keeps a check of its own as the sweep's guard. Its None already means five things (no clusters, all named, no provider, private, cannot answer), and a caller reading None would put "fix your Ollama" on every undiarized run - the false note TASK-089.08 is a lesson about. Both call one module-private helper in finalize.py; widening the return type would move every test that asserts `is None`.
13. Criterion 2 needs a test that discriminates, or it is proven by nothing: the class default is pulled and chat-capable, the saved `llm_model_ollama` row is not. A check reading `cls.default_model` goes green there and this one red - the TASK-089.06 failure exactly.
14. The anchor belongs to the reason, not to the card: Settings for no provider and no key, and for a daemon that is down or a model that was never pulled the fix is a command on this machine, so the card carries that and no Settings link that would send a reader to the wrong screen (criterion 1: the fix and where to do it).
15. Criterion 3's proof is shaped like `test_pressing_test_asks_no_provider_anything_in_the_web_process` (tests/test_web_ai.py:1274): the cloud path builds no client and makes no request at all, the Ollama path makes exactly one GET through the `tags` seam.
16. Line numbers in the description have moved: `ai_run` is at ai_ui.py:857, `queue_speaker_pass` at finalize.py:384, `_chosen_provider` at :747, the two sentences at :151-167. And `ai_run` does not make "no provider call" today - `_refuse_if_window_too_small` reaches the daemon through `context_tokens_for` -> `OllamaProvider.window_for_model` -> `cls()`, which is why the existing tests are already answered by this machine.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Scope added on 2026-09-21 by the orchestrator, with its reason.

TASK-089.07 built ADR-016's refusal: a request that names no provider creates no job row and sends nothing, which is mutation-proven. What it could not finish is how that refusal reaches the reader. It raises HTTPException(400) whose detail carries the right sentence and the Settings anchor, but app.js renders a detail with textContent (wireHtmxErrors), so the anchor is inert text - no card, no link.

Its criterion #6 asked for the card. This task already builds one, for a provider that is chosen but cannot answer (design spec, section 3.5, lines 900-905, and criterion #1 here). That is a different case, so the card for 'nobody has chosen' was assigned nowhere. Rather than build two cards in two tasks, the card half moved here as a new criterion and TASK-089.07's #6 was narrowed to the safety half, saying so in its own text.

What this task inherits: scribe/web/ai_ui.py:742-754 _chosen_provider raises the 400; the two sentences are NO_PROVIDER_YET (screens with no picker, such as the library's bulk action) and NO_PROVIDER_FOR_REQUEST (the panel and the chat form, which carry a select) at :150-165.

Built 2026-09-23. Red first, then the code, then seven mutants on a copy outside the repository.

WHAT CHANGED
- scribe/llm/__init__.py: why_unavailable(conn, provider, model) - "" when the provider can
  answer, else its own sentence. One spelling of "build it, ask available(), swallow LlmError,
  close it" for both callers; no remote call and nothing loaded (ADR-001). model reaches only a
  local provider's constructor, because openai_like takes no model= and answers on key presence.
- scribe/web/ai_ui.py: NO_PROVIDER_IN_CARD (the same words without the bare URL, since the card
  carries a real anchor); _named_provider split out of _chosen_provider; _blocked_card, which puts
  the Settings anchor on a cloud reason and no anchor on a local one; panel_for/_one_panel take
  blocked=; _blocked_panel answers 200 with the panel on the htmx AND the non-htmx path. In ai_run
  the check sits after _refuse_if_private, once params["model"] is resolved, and BEFORE
  _refuse_if_window_too_small - that ordering is the whole of the "one probe" promise, because the
  window check reaches the daemon itself through window_for_model.
- scribe/templates/_ai_output.html: the card, with the anchor only when the fix is in Settings.
- scribe/stages/finalize.py: _cannot_answer (the shared question), _speaker_pass_blocked (the same
  question behind the guards that keep the note honest), _note_run (merge/clear on run.params_json,
  the diarize._note_run shape), SPEAKER_PASS_NOTE. queue_speaker_pass refuses, so the sweep is
  covered by the same line; run() writes or clears the note; sweep_speaker_passes asks once before
  its loop as well (see DEVIATION 2).
- scribe/web/transcript.py: speaker_pass_note() beside diarization_note(), both on _run_note.
- scribe/templates/_transcript_panel.html: the note, beside the diarization one.

EVIDENCE (fence exported on every run; one test file per process; output in
C:/Users/rvdbr/AppData/Local/Temp/claude/D--Users-Robert-Documents-GitHub-RvdB-MyScribe/96fe3055-12a8-4348-a17a-5699a082adf4/scratchpad/build/TASK-089.10/)
- red-test_web_ai.txt          9 failed, 124 passed
- red-test_stage_finalize.txt  3 failed, 31 passed
- red-test_web_transcript.txt  1 failed, 105 deselected
- green-test_web_ai.txt        134 passed
- green-test_stage_finalize.txt 34 passed
- green-test_web_transcript.txt 106 passed
- 27 more files that import a changed module, all green: test_web_library 82, test_pipeline_e2e 54,
  test_llm_tasks 159, test_llm_providers 52, test_llm_ollama 55, test_llm_speakers 43, test_llm_chat
  27, test_llm_privacy 19, test_llm_selftest 14, test_llm_chunking 32, test_llm_labels 19,
  test_llm_cleaning_gate 25, test_llm_task_providers 9, test_setup 15, test_setup_plan 109,
  test_setup_prove 43, test_ollama_setup 19, test_proxy 29, test_credentials 35,
  test_ingest_watching 84, test_ingest_recording 39, test_runner 13, test_web_jobs 43,
  test_web_recorder 24, test_glossary 69, test_params_door 26, test_stage_diarize 76,
  test_stage_prepare 27, test_stage_transcribe 69, test_web_url_dialog 90, test_exports_text 74.

MUTANTS, all on a copy; `grep -rn MUTANT scribe tests packaging` finds nothing
  class-default    build the provider bare      -> test_the_check_asks_about_the_model_this_request_named
  after-window     check after the window check -> test_a_blocked_post_costs_one_bounded_probe_and_no_remote_call
  poll-probes      derive the card on a render  -> test_a_panel_render_and_its_poll_probe_nothing
  false-note       read None as "cannot answer" -> test_a_run_with_no_clusters_gets_no_note
  no-privacy-guard note on a pinned recording   -> test_a_private_recording_gets_no_note_either
  no-queue-check   drop the enqueue guard       -> test_the_sweep_queues_nothing_for_a_provider_that_cannot_answer
  always-settings  Settings link on every card  -> test_an_ollama_that_is_not_running_answers_with_a_card
  no-daemon        a machine with no Ollama     -> measurement, not a mutant: see FINDING 1

FINDING 1, for the orchestrator - criterion 5's second sentence did not hold, and why it is
weaker than it looks. 19 existing tests gained an `ollama_ready` fixture; NO test body or
assertion changed, only the parameter list. On THIS machine only two of them actually went red
(measured: after-web-change.txt) - test_asking_the_same_kind_of_a_different_model_is_a_different_job
and test_a_provider_picked_in_the_panel_is_somebody_choosing. The other 17 were green here for the
wrong reason: conftest stubs ollama_setup.state and never OllamaProvider.tags, so they were
answered by this laptop's daemon, which happens to have qwen3.5:4b pulled. Measured on a copy with
the daemon stubbed away (mut-no-daemon-finalize.txt): 10 tests in tests/test_stage_finalize.py fail
on a machine without Ollama, before any of my changes to that file. After the fixture, the same
copy is 34 passed (mut-no-daemon-finalize-after.txt) and tests/test_web_ai.py is 133 passed. The
fixture removes a machine dependency; it does not loosen a single assertion.
  test_web_ai.py (9): posting_an_action_enqueues..., a_custom_action_carries_the_typed_prompt...,
  clicking_the_same_action_again..., a_finished_action_can_be_asked_again,
  a_different_question_is_a_different_job, asking_the_same_kind_of_a_different_model...,
  posting_an_action_answers_with_a_panel_that_polls..., a_private_media_still_allows_the_local_provider,
  a_provider_picked_in_the_panel_is_somebody_choosing (was no_ollama, now ollama_ready).
  test_stage_finalize.py (10): clusters_mean_a_speaker_pass_is_queued,
  a_private_recording_is_identified_by_a_local_provider, the_pass_names_the_run_it_was_asked_about,
  a_partly_named_run_keeps_the_names_it_had, a_run_every_name_is_on_is_asked_about_only_when_asked_to,
  a_partly_named_run_still_asks_about_the_rest, the_first_run_of_a_recording_inherits_nothing_and_asks,
  the_sweep_asks_about_a_recording_that_was_never_asked,
  a_second_sweep_finds_nothing_because_the_first_left_a_job,
  the_pass_waits_and_catches_up_at_the_first_start_after_somebody_chose.
One test changed its assertions, and criterion 6 asked for exactly that:
test_asking_with_nobody_chosen_writes_no_job_and_says_where_to_choose, from 400 + JSON detail to
200 + the card and a real anchor. chat_ask and the library's bulk action keep TASK-089.07's 400 and
its two sentences, untouched.

DEVIATION 1 - the criterion-2 test is not the shape the plan recorded. The plan's discriminator
(class default pulled, saved row not) proves nothing: OllamaProvider.__init__ is
`model or self._saved_model() or self.default_model`, so a bare `cls(conn)` also reads the saved
row and goes red there too. The shape that actually discriminates is the form naming a model that
is not pulled while BOTH the saved row and the class default are - only a check pointed at
params["model"] goes red. Mutant `class-default` confirms it. The plan's shape is kept as
test_a_saved_embedder_blocks_even_though_it_is_pulled, which is the TASK-089.06 regression.

DEVIATION 2 - sweep_speaker_passes asks once before its loop as well as through
queue_speaker_pass. queue_speaker_pass keeps its own check, which is what makes the sweep correct;
the early return is what keeps it quick. A refused loopback port is instant, but a firewall that
drops rather than refuses costs PROBE_TIMEOUT per candidate row, and this runs from the app's
lifespan at every start - 60 catch-up rows would be two minutes of a hung start.

DEVIATION 3 - the note carries no link. The fix travels in the provider's sentence and it is not
always a screen: "start it" and `ollama pull ...` are typed in a terminal, and an anchor under
those would name a page that can do neither. Same rule in the card: the Settings anchor is there
for a cloud provider (a key is saved in Settings) and absent for a local one. Mutant
`always-settings` is what holds it.

WHAT IS LEFT
- Criterion 5 is "partly" at best: the substance holds (a provider that can answer takes exactly
  the path it took, proven by the unchanged params assertion of
  test_posting_an_action_enqueues_an_llm_job_with_the_right_params and by
  test_a_queued_post_does_reach_the_window_check), but "unchanged" does not, for the 19 tests above.
- OUT OF SCOPE, reported not done: chat_ask (POST /media/{id}/chat) has the same blind spot and
  still enqueues a job for a provider that cannot answer. Criterion 1 names only
  POST /media/{id}/ai/{kind}, so it was left alone. The same applies to the library's bulk action.
- The non-htmx blocked POST answers with the panel fragment rather than a full page. A full page
  would mean computing `blocked` in panel_context, which is exactly the loopback-GET-per-render
  that test_a_panel_render_and_its_poll_probe_nothing forbids.
- No real run of the app: the criteria are all unit-testable and a real run would have to start the
  app, which this agent does not do on this machine.

ADDENDUM, same day, after review. Three corrections to the note above.

1. A DEFECT FOUND AND FIXED - the note outlived the fix on the path that fixes it.
   `test_the_note_is_cleared_once_the_pass_is_queued` only covered clearing through
   `finalize.run`, which is a re-transcription. The path that actually recovers is the sweep:
   Ollama is down when the recording finishes and the note is written; Robert starts the daemon and
   restarts the app; `sweep_speaker_passes` queues the pass; the speakers get named - and nothing
   ever took the note off, because the sweep's own `NOT EXISTS (… kind='speakers')` means it never
   looks at that recording again. The transcript page would have gone on saying "Ollama is not
   running" for good, which is precisely the TASK-089.08 defect this criterion exists to avoid.
   Fix: `_note_run` takes the connection instead of the runner context, and `queue_speaker_pass`
   clears the note where the enqueue succeeds - one place every caller's success goes through.
   `run()` now only ever writes.
   Red first: red-sweep-clears-note.txt, 1 failed.
   New test: test_the_sweep_clears_the_note_when_it_finally_asks.
   New mutant `no-sweep-clear` (drop the clear in queue_speaker_pass) kills two tests:
   test_the_note_is_cleared_once_the_pass_is_queued AND the new one. mut-no-sweep-clear.txt.
   The other three finalize mutants were re-run against the changed code and still kill:
   false-note, no-privacy-guard, no-queue-check.

2. VERIFIED, not assumed - the model id is form input and it now reaches rendered markup through
   `available()`'s sentence. Jinja's autoescape holds on that path: posting
   `model=<script>alert(1)</script>` at a blocked provider puts `&lt;script&gt;` in the card and
   the raw tag nowhere in the body. Pinned by
   test_a_model_id_from_the_form_reaches_the_card_escaped, the shape
   test_a_free_text_answer_containing_a_script_tag_is_escaped_in_the_html already uses.

3. CORRECTION to the evidence block above: that list is 31 files, not 27.

FINAL COUNTS: test_web_ai 135 passed, test_stage_finalize 35 passed, test_web_transcript 106
passed, test_pipeline_e2e 54 passed, test_ingest_watching 84 passed, test_llm_speakers 43 passed.
`grep -rn MUTANT scribe tests packaging` finds nothing.

NOT MINE, seen in `git status --short` at the end and left alone: backlog/tasks/task-089.17 is
modified. The parallel agent's packaging/, tests/test_launcher*.py and task-089.15 changes that
were in the tree when this started are no longer listed, so they appear to have been committed
while this ran.

REVIEW ROUND, 2026-09-23. Three verifiers; what their findings changed.

FIXED, major - the note outlived its own fix on the one path it recommends.
`apply_speakers` writes speaker_label rows and never touched `run.params_json`, so a manual
pass from the AI panel - the thing SPEAKER_PASS_NOTE tells the reader to press - named the
clusters and left "Ollama is not running" standing beside them for good: the sweep skips any
recording that has a `speakers` answer, so nothing visits that run again. The same defect as
the sweep one fixed earlier in this task, one path further along. Red first
(fix-red-test_llm_speakers.txt, 2 failed / 43 passed). Fix: `finalize.clear_speaker_pass_note`
is public and is called from both places a pass stops being un-asked - `queue_speaker_pass`
(queued) and `tasks.apply_speakers` (answered). Unconditional, not gated on a confident name:
what shuts the sweep out is the `llm_output` row, so a vague answer would leave the note
standing. Both shapes tested.
SCOPE STRETCH, reported not assumed: this touches scribe/llm/tasks.py and
tests/test_llm_speakers.py, outside the file set this task named.

FIXED, minor - the note ran two sentences together. `available()` ends none of its four
answers with a full stop, so the page read "... (start it, then reload) The transcript is
complete ...". Red first (fix-red-test_stage_finalize.txt). The stop is added in
`_speaker_pass_note()`, beside the template, and pinned by an assertion in the existing note
test rather than shipping unpinned.

FIXED, minor - `_blocked_card` asked `provider_class` twice, the second time unguarded, after
`why_unavailable` had already swallowed that ValueError and returned its text as the reason.
Unreachable from `ai_run` today (`_provider` validates first), but the helper is advertised
for reuse and the next caller with a stale saved name would get a 500 out of a route that
exists to replace one. The class is resolved once; a name that does not resolve gets the
Settings anchor, because choosing a provider is a Settings action.

FIXED, minor - the section comment in tests/test_stage_finalize.py was silent on the clear the
same change added. Reworded: only `run` writes a note, `queue_speaker_pass` clears one. The
original sentence was true about writing; what it lacked was the other half.

CORRECTED, criterion 5 - the earlier note above is wrong, and this is the record. It said 17
of the 19 tests were "green here for the wrong reason - answered by this laptop's daemon" and
that the fixture "removes a pre-existing machine dependency". A verifier measured HEAD with
every Ollama call refused and got 124 passed and 29 passed - exactly the recorded baselines.
The mechanism agrees: at HEAD nothing in `ai_run` depended on the daemon answering, because
`_refuse_if_window_too_small` swallows every exception (ai_ui.py:922). So: THIS CHANGE
INTRODUCES THE DEPENDENCY AND THE `ollama_ready` FIXTURE STATES IT EXPLICITLY. The earlier
measurement (mut-no-daemon-finalize.txt) ran the new scribe code against the old test file, so
it measured the new gate, not HEAD. The corrected input does NOT move the recommendation: a
readiness gate necessarily makes tests that never modelled it depend on it, the fixture
loosens no assertion, and reverting it would put the suite back to being answered by whatever
this laptop has pulled. Criterion 5 stays "partly", for the orchestrator to rule on.

CORRECTED - one of the 19 was not a parameter-list addition.
`test_a_provider_picked_in_the_panel_is_somebody_choosing` swapped `no_ollama` for
`ollama_ready`. Its world was inverted on purpose: under a stopped daemon it asserted a job
WAS queued, which is the behaviour this task deliberately ends. The body is untouched and its
intent - naming a provider in the form counts as choosing, ADR-016 - is preserved.

RECORDED, deliberately not tested - the sweep's early return
(`if rows and chosen and _cannot_answer(...)`) is pinned by nothing, and that is right rather
than a coverage hole: `queue_speaker_pass` checks per row, so deleting the early return
changes no observable behaviour by construction, and a verifier deleted it and the file stayed
green. Its only contract is startup latency - PROBE_TIMEOUT per catch-up row behind a firewall
that drops rather than refuses - which no unit test can see. Not dead code.

STILL OPEN, needs a follow-up task before TASK-089 closes: `chat_ask` (POST /media/{id}/chat)
and the library's bulk action still write a job row for a provider that cannot answer. Out of
criterion 1's scope, which names only POST /media/{id}/ai/{kind}. The card machinery
(`llm.why_unavailable`, `_blocked_card`) is there to reuse; the bulk action needs
NO_PROVIDER_YET's shape, because its screen carries no provider select. Not created here: a
new task under TASK-089 is the orchestrator's call.

EVIDENCE after the review - fence exported, one test file per process, output under
scratchpad/build/TASK-089.10/: final-test_llm_speakers 45 passed, final-test_stage_finalize 35
passed, final-test_llm_tasks 159 passed, final-test_web_ai 135 passed,
final-test_web_transcript 106 passed, final-test_pipeline_e2e 54 passed and 1 deselected,
final-test_ingest_watching 84 passed, final-test_db 39 passed.
`grep -rn MUTANT scribe tests packaging` exits 1 - nothing found.

RE-RUN, and why: the parallel agent wrote tests/conftest.py at 01:18:24 while this ran - an
autouse fixture stubbing `accel.memory` for every test in the suite (TASK-089.18). Every green
above was re-taken after that write, against the tree as it stands, and `git status --short`
hashed identically before and after the re-run. Also modified by that agent and left alone:
README.md, scribe/accel.py, scribe/app.py, scribe/footprint.json, scribe/ollama_setup.py,
packaging/build_payload.py, scripts/start.ps1, scripts/start.sh, scripts/mac-acceptance.sh,
tests/test_app.py, tests/test_launcher.py, tests/test_ollama_setup.py, tests/test_setup_plan.py,
and the task-089.17 and task-089.18 task files.

Orchestrator, 2026-09-23. Criterion 5 stays unticked on purpose. Its substance holds (a provider that can answer takes the same path, proven by the unchanged params assertion), but its words say the existing tests stay green 'unchanged', and 19 gained a fixture because this change introduces the readiness dependency. Whether the fixture counts as unchanged is Robert's call, not the agent's; the recommendation is to accept it, since no assertion moved. The follow-up the notes asked for is TASK-089.26, now Done. sweep 2026-09-23: 84 test files, one per process, fenced - 3282 passed, 0 failed, 10 skipped; test_llm_live has only live tests (exit 5, all deselected); git status hash identical before and after. Evidence: d0ea7837-…/scratchpad/ev/sweep/.
<!-- SECTION:NOTES:END -->
