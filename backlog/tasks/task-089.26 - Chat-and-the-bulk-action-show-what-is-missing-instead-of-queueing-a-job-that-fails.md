---
id: TASK-089.26
title: >-
  Chat and the bulk action show what is missing instead of queueing a job that
  fails
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 23:30'
updated_date: '2026-09-23 05:53'
labels:
  - llm
  - ux
  - bug
dependencies:
  - TASK-089.10
parent_task_id: TASK-089
priority: medium
ordinal: 167000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-089.10 gave POST /media/{id}/ai/{kind} a card for a provider that was chosen and cannot answer - no key, Ollama not running, the saved model never pulled - so a skipped question ends as a sentence naming the fix instead of an LLM_FAILED job on the board. Its criterion 1 named that one route and nothing else, and its fix round recorded, on 2026-09-23, that two more routes still write the job row: chat_ask (POST /media/{id}/chat) and the library's bulk action.

The machinery is built and reusable: llm.why_unavailable answers the readiness question against the model the job would use (the saved llm_model_ollama row, never the class default - TASK-089.06), and ai_ui._blocked_card renders it with the link. What is left is calling them from the two routes and rendering the answer in each screen's own shape. chat_ask has a provider select and can carry the same card. The bulk action's screen carries no provider select, so it needs NO_PROVIDER_YET's shape - one sentence and where to choose - rather than a per-provider card.

TASK-089.07 keeps its 400 with its two sentences on both routes for the no-provider case; this task adds the cannot-answer case beside it and does not re-litigate the refusal.

The check must stay what TASK-089.10 made it: key presence plus Ollama's bounded loopback probe with trust_env=False (TASK-089.05), no remote call, nothing loaded in the web process (ADR-001).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Red first: POST /media/{id}/chat with a chosen provider that cannot answer - each of the three shapes - comes back with the card naming the fix and where to do it, and no job row is created; a test counts the rows. Today it writes the row.
- [x] #2 Red first: the library's bulk action with a chosen provider that cannot answer queues nothing and says so in the shape its screen has - one sentence and where to fix it, no per-provider card - and a test counts the rows. Today it queues one job per recording.
- [x] #3 Both routes read the readiness through llm.why_unavailable against the model the job would use, and make no remote call: a transport that raises on any request proves it, the way TASK-089.10's tests do.
- [x] #4 A provider that can answer takes exactly the path it takes today on both routes; the existing chat and bulk tests stay green, with the same ollama_ready fixture TASK-089.10 introduced where a test now has a readiness precondition it did not have before.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red first: three chat shapes, the model the turn named, one probe and no remote call, the form keeping what was typed; four bulk tests including one probe for the batch. 2. chat_ask asks _blocked_card after TASK-089.07's 400 and the privacy refusal, and renders chat.html with the card on both paths. 3. The bulk pass asks once before the loop and answers with ai_ui.blocked_notice. 4. Measure which existing tests depend on this machine's daemon and keys, and give exactly those a fixture. 5. Mutants on a copy. 6. A real run on port 4299 against this machine's daemon.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Built 2026-09-23. Red first, then the code, then six mutants on a copy, then a real run.

Evidence under d0ea7837-…/scratchpad/ev/. Every pytest fenced (SCRIBE_DATA_DIR and SCRIBE_ENV_FILE on scratch), one file per process.

WHAT CHANGED
- scribe/web/ai_ui.py, chat_ask: after TASK-089.07's no-provider 400 and the privacy refusal, the model the job would carry is resolved once (the form's, else the saved row) and `_blocked_card` is asked about it. A card means chat.html comes back with the card, 200, on the htmx and the non-htmx path alike, and no job row. The form comes back holding the question, the provider and the model that were posted. Chat has no window check, so this is the only probe.
- scribe/web/ai_ui.py, blocked_notice(card): the card as plain text for a flash. "Nothing was queued. <the provider's sentence> (<Settings address>)." The address is there only when the fix is in Settings, the card's own rule.
- scribe/web/library.py, _enqueue_labels: one `_blocked_card` for the batch, before the loop, against the saved model the jobs would carry. No per-provider card: the screen has no select.
- scribe/templates/chat.html: the card above the form, with the Settings link only on a cloud reason; the textarea keeps `question`.
- TASK-089.07's 400 for no provider at all is untouched on both routes: the new check runs only once a provider is named.

RED (before the code)
- red-089.26-test_web_ai.txt: 6 failed, 2 passed. The card was missing and a job was written.
- red-089.26-test_web_library.txt: 4 failed, 4 passed. Rows were queued for a stopped Ollama, a missing key and an unpulled model.

GREEN
- green-089.26-test_web_ai.txt: 141 passed
- green-089.26-test_web_library.txt: 86 passed
- green-089.26-test_llm_chat.txt: 27 passed

NEW TESTS
- tests/test_web_ai.py: test_a_chat_turn_to_a_cloud_provider_with_no_key_answers_with_a_card_and_no_job, test_a_chat_turn_to_an_ollama_that_is_not_running_answers_with_a_card_and_no_job, test_a_chat_turn_about_a_model_never_pulled_answers_with_the_pull_command_and_no_job, test_a_chat_turn_is_checked_against_the_model_it_named, test_a_blocked_chat_turn_makes_one_bounded_probe_and_no_remote_call, test_a_blocked_chat_turn_keeps_what_was_typed_and_answers_without_a_redirect.
- tests/test_web_library.py: test_bulk_label_to_an_ollama_that_is_not_running_queues_nothing_and_says_so, test_bulk_label_to_a_cloud_provider_with_no_key_queues_nothing_and_names_settings, test_bulk_label_about_the_saved_model_never_pulled_queues_nothing, test_a_blocked_bulk_label_asks_once_for_the_batch_and_nothing_remote.

CRITERION 4 - which existing tests gained a readiness fixture, and why
Measured first, not assumed: tests/test_web_library.py with a stub plugin that stops Ollama and removes every *_API_KEY and *_TOKEN from the environment (conftest already stubs the registry) gave 6 failed (nomachine-test_web_library.txt). Those six are the bulk tests that expect rows to be queued; after this change they need a provider that can answer, and on this laptop they were answered by the real daemon and environment. Each gained a fixture and nothing else - no body, no assertion changed:
- ollama_ready (new in tests/test_web_library.py, the same shape as TASK-089.10's): test_bulk_label_queues_one_pass_per_chosen_recording, test_bulk_label_skips_a_recording_with_no_transcript, test_nothing_is_reported_when_nothing_was_skipped, test_bulk_label_of_a_private_recording_is_fine_on_a_local_provider.
- openai_key (new, a fake key in the environment; no client is ever built): test_a_private_recording_is_skipped_by_a_bulk_pass_to_an_external_model, test_the_skipped_private_recordings_are_reported_rather_than_dropped_quietly.
- tests/test_web_ai.py: test_asking_a_question_enqueues_a_chat_job_carrying_the_question gained ollama_ready. test_a_chat_turn_that_names_a_provider_is_somebody_choosing swapped no_ollama for ollama_ready: under a stopped daemon it asserted that a job WAS queued, which is exactly what this task ends. Its intent (naming a provider in the form counts as choosing, ADR-016) is kept. The same swap TASK-089.10 made on the rail's twin test.
After the fixtures, the same stub plugin: test_web_ai 141 passed, test_web_library 86 passed (nomachine-after-*.txt). The suite no longer depends on what this laptop has running.

MUTANTS, on a copy; `grep -rn MUTANT scribe tests packaging install.py` in the repo finds nothing
- chat-no-check (the chat check never blocks) -> 6 failed, every new chat test.
- chat-class-default (check the saved model, not the posted one) -> test_a_chat_turn_is_checked_against_the_model_it_named.
- chat-no-keep (the form forgets the question) -> test_a_blocked_chat_turn_keeps_what_was_typed_and_answers_without_a_redirect.
- bulk-no-check -> 4 failed, every new bulk test.
- bulk-per-row (the check moved into the loop, a blocked row skipped) -> 4 failed; the batch test says `assert 3 == 1` on the probe count.
- bulk-always-settings (the Settings address on every notice) -> test_bulk_label_to_an_ollama_that_is_not_running_queues_nothing_and_says_so.

REAL RUN, 2026-09-23, this machine's own Ollama daemon (evidence in ev/realrun/)
`python -m scribe --port 4299 --no-supervisor --no-browser` with SCRIBE_DATA_DIR on scratch; one recording seeded with tests/seed.py; llm_provider=ollama and llm_model_ollama=vogon-poetry:70b. /health answered with app_dir and data_dir (TASK-089.17's fields, live).
- POST /media/1/chat -> 200, card: "Ollama is running at http://127.0.0.1:11434 but 'vogon-poetry:70b' is not pulled: run `ollama pull vogon-poetry:70b` (pulled: …)", the question kept in the form.
- POST /media/1/ai/summary -> 200, the same card (TASK-089.10's route, same run).
- POST /media/bulk action=label -> 200, notice: "Nothing was queued. Ollama is running at http://127.0.0.1:11434 but 'vogon-poetry:70b' is not pulled: run `ollama pull vogon-poetry:70b` (pulled: …)."
- job rows after those three: 0.
- POST /media/1/chat with model=qwen3.5:4b, which is pulled -> 200 and exactly one row: `1 llm queued {'media_id': 1, 'kind': 'chat', 'question': 'What about the towel?', 'provider': 'ollama', 'model': 'qwen3.5:4b'}`. The app was stopped afterwards; 4299 answers nothing.

Validation: sweep 2026-09-23: 84 test files, one per process, fenced - 3282 passed, 0 failed, 10 skipped; test_llm_live has only live tests (exit 5, all deselected); git status hash identical before and after. Evidence: d0ea7837-…/scratchpad/ev/sweep/.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Chat and the library's bulk pass now refuse a provider that cannot answer, the way the rail's POST does since TASK-089.10: chat shows the card on its own page and keeps what was typed; the bulk pass asks once for the batch and answers with one sentence and, when the fix is in Settings, its address. No job row in either case. Verified red first (6 + 4 failed), green (141, 86), six mutants killed, a no-daemon-no-key measurement, a real run on port 4299 against this machine's Ollama (0 rows blocked, 1 row for a pulled model), and the full per-file sweep.
<!-- SECTION:FINAL_SUMMARY:END -->
