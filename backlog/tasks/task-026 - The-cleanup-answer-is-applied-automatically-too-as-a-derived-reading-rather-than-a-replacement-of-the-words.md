---
id: TASK-026
title: >-
  The cleanup answer is applied automatically too, as a derived reading rather
  than a replacement of the words
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-10 13:29'
updated_date: '2026-09-10 14:34'
labels: []
dependencies:
  - TASK-024
type: feature
ordinal: 67000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-10, alongside the speaker clarification: what is true for speaker identification is true for the cleanup task - once the analysis is done, the result should be applied, not left as an artifact nobody clicked. The mechanism is TASK-024's: scribe/stages/llm_stage.py task_store() stores an answer, emits an event and ends, so no llm kind applies anything today; a kind should be able to declare an apply step the runner performs after storing, inside the same job.

Cleanup differs from speakers in one way that decides its design. Applying a speakers answer writes a name per cluster into a column - bounded, reversible, and the transcript is untouched. Applying a cleanup answer changes WHAT WAS SAID, and this project has a position on that: ADR-003 makes words the canonical transcript with every grouping derived at render time. WHYcast walked this road first and its ADR-004 forbids a prompt that returns the whole rewritten transcript as the primary path, because it hallucinated and dropped lines, and requires a change analysis that diffs original against processed to catch content loss.

So the shape that satisfies both 'apply it automatically' and 'lose nothing': store the cleaned text as a DERIVED READING beside the canonical words rather than in place of them. The words stay what the model heard; the clean version is another way to render them, offered in the transcript view and in exports, and regenerable. Nothing is destroyed if a chunk came back short, and the comparison is available rather than lost. A replacement would put an unreviewed model rewrite where ADR-003 says the source of truth lives, and would need the content-loss gate WHYcast had to build.

Hooks: scribe/stages/llm_stage.py (task_store, STAGES), scribe/llm/tasks.py ('cleanup' TaskSpec, combine='concat', schema=None), scribe/exports/doc.py and the transcript view for where a second reading would surface, ADR-003 for the rule it must not break.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A cleanup answer is applied by the job that produced it, with no click, using the apply mechanism TASK-024 introduces
- [ ] #2 The canonical words are not modified: the cleaned text is stored as a derived reading beside them and the original remains renderable
- [ ] #3 The transcript view can show either reading, and says which one it is showing
- [ ] #4 A cleanup that came back short or failed leaves the recording exactly as it was, visibly on the jobs board
- [ ] #5 Re-running cleanup stores a new artifact and does not destroy the previous one
- [ ] #6 Tests cover the apply step, the words being untouched, the short-answer path and re-running; a real run over a Hacker History episode is in the notes with the two readings compared
- [ ] #7 The transcript view shows the clean reading and returns to the untouched original in one click; both remain available and the view says which it is showing
- [ ] #8 A cleaned reading is rejected when it collapses in length, measured per chunk as well as overall, and rejected when it comes back substantially longer than the original
- [ ] #9 A rejected cleaning is undone: nothing is published, the recording reads exactly as before, and the reason names the chunk and the ratio
- [ ] #10 The refused answer is stored anyway, with the gate's verdict and the measured ratios, so a refusal is checkable afterwards
- [ ] #11 The length floor is set from measurements over real episodes recorded in this task, not chosen; the constant cites them
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
DECIDED BY ROBERT 2026-09-10. Derived reading, with two additions.

1. ONE CLICK BACK. The clean text is what is shown, and a single click returns to the untouched transcribed text. Both readings stay available at all times; neither is a mode you get stuck in.

2. A GATE AFTER CLEANING, WITH AN AUTOMATIC UNDO. Cleaning too radically - or worse, summarising - is not allowed. A clean version must stay as close to the original as it can, and must not collapse in length. When it does, the cleaning is undone.

The undo is cheap precisely because of the derived-reading choice: nothing was replaced, so rejecting an answer means not publishing it. The canonical words were never at risk. The two requirements reinforce each other rather than competing.

WHAT THE GATE MEASURES, and the one number that must be measured rather than guessed.

Length in words, compared to the original, and checked PER CHUNK as well as overall. Per chunk matters because cleanup is combine='concat': one chunk that collapsed into a summary hides inside an otherwise healthy total, and the aggregate would pass while a page of the transcript had quietly become a paragraph.

A ceiling as well as a floor. A rewrite that comes back substantially LONGER than what went in did not clean anything - it invented, and that is the other direction of the same failure.

THE FLOOR IS NOT YET KNOWN AND MUST NOT BE GUESSED. Legitimate cleaning removes real words: filler, repetition, false starts, stutters. A floor at 90% would reject good work; a floor at 30% would pass a summary. The honest way to set it is to measure: 47 Hacker History episodes are transcribed in the library, so run cleanup over a handful, record the word-count ratio per chunk and overall for answers a person judges good, and put the floor below the worst good one with room to spare. Record those measurements here and cite them where the constant is defined, the way MAX_LISTED and DEFAULT_MAX_OUTPUT_TOKENS are.

A REJECTED ANSWER IS STILL STORED. The llm_output row is written whatever the gate decides, because the accountability requirement from TASK-024 applies here too: 'this cleanup was refused for shrinking the third chunk to 22% of its words' is only checkable if the answer that was refused survives. The gate's verdict and the ratios belong in that row's params_json.
<!-- SECTION:NOTES:END -->
