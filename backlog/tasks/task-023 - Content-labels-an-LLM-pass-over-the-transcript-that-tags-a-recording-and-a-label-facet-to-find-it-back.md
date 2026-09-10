---
id: TASK-023
title: >-
  Content labels: an LLM pass over the transcript that tags a recording, and a
  label facet to find it back
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-10 04:40'
updated_date: '2026-09-10 14:44'
labels: []
dependencies: []
type: feature
ordinal: 64000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A recording is findable today by its folder, its title and full-text search over its words. None of those answer 'which of these are about lockpicking'. Add labels as a first-class thing on a media row: many-to-many, stored in the database, derived from the transcript by the LLM stage that already runs in a runner child, and usable as a facet in the library beside the folder tree. Robert's framing (2026-09-09/10): categories are really labels of the content, AI may derive them, and they exist to visualise and find content back - not just to file it. Decisions already taken with him: labels come from the TRANSCRIPT after transcription, not from feed metadata, because the title of a podcast episode says almost nothing about its content; the model is given the existing labels and must reuse them first, inventing at most 3 new ones per recording, so the vocabulary stays usable as a filter instead of growing into 120 near-duplicates; the 50 Hacker History episodes already in the library are labelled retroactively once this lands. Note the privacy consequence: llm.privacy.assert_allowed refuses a cloud provider for a private recording or one in a private folder, so private media get no labels unless a local provider (Ollama) is configured - that is fail-closed and correct, but it means coverage depends on the configured provider. Hooks named by a read of the code on 2026-09-09: scribe/stages/llm_stage.py is the only place that calls a model and it runs in the runner child; scribe/llm/tasks.py holds the task kinds and store_output; scribe/web/library.py has the single WHERE builder _where() at :206, the row query media_rows() at :223 and sidebar_context() at :262, with VIEWS at :60 and State at :93; the sidebar template is scribe/templates/_sidebar.html and the rows are _media_rows.html.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A 'labels' LLM task kind exists whose prompt receives the transcript and the labels already in the database, and returns labels for this recording: existing ones by preference, at most 3 new
- [ ] #2 Labels are their own table with a many-to-many link to media; a label carries who chose it (llm or human) so a human label is never overwritten by a later run
- [ ] #3 A malformed or empty answer stores the analysis and applies no labels, rather than failing the job
- [ ] #4 The library sidebar lists the labels with counts and filtering by one narrows the table, through the same WHERE builder the folder filter uses
- [ ] #5 A person can add and remove a label on a recording by hand, and that survives a re-run
- [ ] #6 The privacy pin is respected: a private recording reaches no cloud provider, and the refusal is visible rather than silent
- [ ] #7 Tests cover the prompt's reuse-first rule, the parser on a malformed answer, the facet query and the privacy gate; a real run over a Hacker History episode is in the notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Schema v11: label(id, name UNIQUE COLLATE NOCASE, created_at) and media_label(media_id, label_id, source, created_at) with source in ('llm','human'). NOCASE on the name is what stops 'Hacking' and 'hacking' becoming two rows; the composite primary key on the link stops a second run doubling a label.
2. tasks.py: a LabelGuess/Labels schema in the lenient house style (every field but the label itself defaulted, so a half-answer still validates), and a 'labels' TaskSpec with with_speakers=False - who spoke is noise for what it is about.
3. prompts/labels.md: the kind's own prompt, receiving the labels already in the database and told to prefer them. The 'at most 3 new' rule is NOT left to the prompt: the prompt asks, the code enforces (ADR-004's lesson in WHYcast - the model judges, code applies), so a talkative model cannot widen the vocabulary.
4. Thread the known labels into the prompt context. Only the sites that render the kind's own prompt over the transcript or over the combined notes need it (tasks.py:1198 and :1241, plus the budget estimate at :648/:692); the chunk-level map prompts do not, because notes from an excerpt are not where the vocabulary is chosen.
5. Apply: a function that takes the parsed answer and the known set, splits reuse from invention, caps the new ones at 3, and writes media_label with source='llm' - never touching a row whose source is 'human'.
6. library.py: labels as a facet. State/parse_state gain a label, _where() at :206 grows one clause (it is the single WHERE builder for the table, so the filter and the counts cannot drift), sidebar_context() at :262 gains the label counts, _sidebar.html lists them.
7. Hand editing: add and remove a label on a recording, written with source='human'.
8. Backfill: label the recordings that already have a current run, so the 50 Hacker History episodes get theirs.
9. Evidence: tests per slice, then a real run over a Hacker History episode with the labels it produced, and the suite in halves on Windows plus the web half on Linux.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Slice 1 (schema v11) green: tests/test_db.py 23 passed. Five tests written red first (no such table: label), then _SCHEMA_V11. label.name is UNIQUE COLLATE NOCASE so 'Hacking' cannot fork from 'hacking'; media_label's primary key is the pair so a re-run cannot double a label; source CHECK('llm','human') is what lets an automatic pass refuse to overwrite a person; the cascade is asymmetric on purpose - purging a recording drops its links and leaves the vocabulary.

Slice 2 (the kind) green: 248 passed over test_llm_tasks, test_web_ai, test_llm_labels, test_db, test_llm_speakers. Added: Labels/LabelGuess schemas, the 'labels' TaskSpec (with_speakers=False), prompts/labels.md, known_labels threaded onto TaskPlan so both the single-call and the combine site see one list per run, and vocabulary/apply_labels/labels_for. The cap on invented labels is enforced in apply_labels rather than asked for in the prompt, and the schema deliberately has no 'is this new' field - the thing being capped would otherwise be self-reporting. Protecting a hand-typed label needs no special case: the link is INSERT OR IGNORE, so an existing pair keeps its source. Three of the project's own tripwires fired and were right to: the sample-answer guard in test_web_ai, the canonical kind list, and the prompt digest. The digest one only anticipated an EDITED template; adding one has the opposite answer (record the digest, leave PROMPT_VERSION alone, because bumping would change the storage key of every other kind and orphan answers that are still answers to the question that was asked). That distinction is now in the test's docstring.

REAL RUN 2026-09-10, against the live library (schema migrated to v11 in passing; the app does that at boot and was down).

media 10 'The history of Michael Lenz', 9984 words, on an EMPTY vocabulary:
  ollama qwen3.5:4b -> ContextTooLong, refused in 0.1s before sending anything: 6 chunks, 24000 tokens of notes through an 8192-token window with room for 3477.
  openrouter/auto  -> 40.3s, 16084 prompt / 5651 completion. Six labels with [m:ss] evidence: cybersecurity careers, security conferences, community building, professional networking, incident response, computer forensics.
  apply_labels: created 3, dropped 3 ('professional networking', 'incident response', 'computer forensics').

media 11 'The history of Jeff Man', 10601 words, vocabulary now 3:
  openrouter/auto -> 28.8s, 16936 prompt / 2949 completion.
  apply_labels: reused ['cybersecurity careers', 'security conferences'], created ['hacker history', 'penetration testing', 'cryptography'], dropped none.
  Reuse-first works against a real model, not just a fake one.

TWO FINDINGS NO FAKE PROVIDER COULD HAVE GIVEN.

1. A LOCAL PROVIDER CANNOT LABEL A FULL EPISODE. qwen3.5:4b's 8192-token window cannot take the combine call for a 10k-word transcript, and the budget guard refuses before spending anything (correct behaviour, existing code). This matters beyond convenience: llm.privacy refuses cloud providers for a private recording, so a PRIVATE recording longer than a few thousand words cannot be labelled at all today. AC6 says the refusal must be visible rather than silent - it is, as a job failure - but the feature's coverage on private media is effectively zero unless a local model with a larger window is configured. qwen3.5:9b is installed and untested for this.

2. THE CAP HAS A COLD-START COST. On an empty vocabulary every label is new, so the first recording kept 3 of 6 good labels; by the second, reuse absorbed most of the answer and nothing was dropped. The cap self-corrects as the vocabulary fills, but the earliest recordings in a library end up thinner than the later ones - and on a 50-episode backfill the order decides which episodes are thin. Worth a decision before the backfill runs.

Slice 3 (the cap made warm) and slice 4 (the library facet) green.

The cap now depends on how full the vocabulary is: MAX_NEW_LABELS_COLD=6 below VOCABULARY_ESTABLISHED=20 labels, MAX_NEW_LABELS=3 at or above it, via new_label_allowance(). Robert's call after seeing the real run drop three good labels into an empty library. Reuse stays uncapped either way - the ceiling was always on invention. VOCABULARY_ESTABLISHED is a judgement, not a measurement, and its docstring says so.

The facet: State.label, one more clause in _where (EXISTS, not a JOIN - that builder feeds a SELECT which already joins a job and a run, and a join would multiply the row list rather than narrow it), label_counts() beside folder_counts() with the same two rules (trash excluded, unused labels omitted), the heading, and a Labels list in _sidebar.html. label= is deliberately NOT whitelisted the way view= and sort= are: those name things the page offers so a bad value is a broken link, while a label is a word somebody may type, and an empty table is the honest answer.

Three of the facet tests were written weak first and caught in review: they asserted only that the labelled recordings appeared, which passes on a page that ignores the filter entirely. Fixed to assert the non-carrier is absent.

Evidence: tests/test_web_library.py 63 passed (12 new). Broad run over tests/test_web_*.py + test_llm_*.py + test_db.py: 827 passed, 1 failed - test_web_settings.py::test_the_web_process_never_imports_a_model_runtime, the pre-existing TASK-022 failure that reproduces on main.

Still open on this task: adding and removing a label by hand (AC5) and the backfill over the recordings already in the library.

Slices 5 and 6 green: 210 passed over test_web_library, test_web_ai, test_llm_labels and test_db (71 in the library file alone).

Hand editing: POST /media/{id}/labels and /labels/remove. Two rules written into the code rather than left implicit. A person is NOT rationed by MAX_NEW_LABELS - that ceiling exists because an automatic pass cannot be asked whether it is sure, and rationing a typed label would be the app second-guessing its user. And removing a label drops the link but keeps the word, because another recording may carry it and label_counts simply stops offering an unused one.

The backfill is a bulk action rather than a script: tick rows, choose Label, one llm job each - visible on the jobs board, cancellable, and paid for one at a time instead of in a sweep nobody can stop. Two guards. A recording with no transcript is skipped, because the pass reads words and a job that can only fail is not worth a row. And the privacy pin is checked for the WHOLE selection before anything is queued: queueing forty jobs and letting three fail on a refusal would spend real money to reach an error the check can see first. On a local provider a private recording is fine, and a test pins that too.

Honest note on method: the four bulk-action tests were written after the implementation, not before, so they are not red-first evidence the way the rest of this task is. Everything else here was written red first, and two rounds of facet tests had to be strengthened after review because they passed on a page that ignored the filter entirely.

CORRECTED BY ROBERT 2026-09-10, and his rule is better than the one I wrote. A private recording is never offered to an external service IN BULK; sending one out is always a conscious human decision. So in a bulk action it is an automatic skip, not a refusal of the batch.

I had built a 403 that refused the whole selection. The failure mode of that design is subtle: refusing only teaches the habit of adjusting the selection until the button works, and a bulk button must never be the thing that puts private words on somebody else's server. Skipping removes the possibility instead of guarding it. On a local provider nothing leaves the machine, so private recordings are queued like the rest, and a test pins that.

Silence was the other way to get it wrong, so the skip is reported: the route sets HX-Trigger scribe-notice, and app.js gained a body listener that flashes it in a neutral tone - a skip that was asked for is not a failure. Tests cover the skip, the notice naming the count and the provider, and the absence of a notice when nothing was skipped.

tests/test_web_library.py 77 passed. The Node harness still passes (test_web_url_dialog + test_web_recorder, 108) - worth checking, because the new listener binds to document.body at load time.
<!-- SECTION:NOTES:END -->
