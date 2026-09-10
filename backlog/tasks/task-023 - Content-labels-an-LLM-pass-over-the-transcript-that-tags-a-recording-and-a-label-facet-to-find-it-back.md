---
id: TASK-023
title: >-
  Content labels: an LLM pass over the transcript that tags a recording, and a
  label facet to find it back
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-10 04:40'
updated_date: '2026-09-10 04:56'
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
<!-- SECTION:NOTES:END -->
