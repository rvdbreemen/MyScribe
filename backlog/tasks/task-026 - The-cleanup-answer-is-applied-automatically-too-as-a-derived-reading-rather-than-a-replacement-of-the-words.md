---
id: TASK-026
title: >-
  The cleanup answer is applied automatically too, as a derived reading rather
  than a replacement of the words
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-10 13:29'
updated_date: '2026-09-10 15:50'
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

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. MEASURE FIRST, then set the floor. Run the shipped cleanup kind over a handful of Hacker History episodes against a real model, record the word-count ratio per chunk and overall for answers that read well, and put the floor below the worst good one with room. The number goes in a constant whose docstring cites the run, the way MAX_LISTED and DEFAULT_MAX_OUTPUT_TOKENS do. Guessing it is the one thing this task must not do: too tight rejects honest disfluency removal, too loose passes a summary.

2. Schema v13: a derived reading beside the canonical words. A table keyed by run holding the cleaned text plus the llm_output it came from and the measured ratios, so the reading and its receipt live together. The words themselves are never touched - ADR-003 makes them canonical and this is the whole reason the derived shape was chosen.

3. The gate, as a function that takes the original and the cleaned text and returns a verdict with numbers: per chunk and overall, a floor and a ceiling. A ceiling because a rewrite that comes back substantially longer did not clean, it invented.

4. apply_cleanup as the kind's apply hook (TASK-024's mechanism): run the gate, publish when it passes, publish nothing when it fails. The undo is trivial because nothing was replaced - that is the payoff of the derived-reading choice.

5. The refused answer is stored anyway, with the verdict and the ratios in the llm_output params, because 'this cleanup was refused for shrinking chunk three to 22 percent' is only checkable if what was refused survives.

6. The transcript view shows the clean reading with one click back to the untouched original, and says which one it is showing.

7. Evidence: tests per slice including a deliberately over-cleaned answer and a padded one, plus a real run with both readings compared side by side.
<!-- SECTION:PLAN:END -->

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

MEASURED 2026-09-10, and the measurement could not be taken: the shipped cleanup kind cannot complete against openrouter/auto on a real episode. Two attempts, both 'openrouter answered with no message content (finish_reason=length)' - media 11 (4 chunks, 11838 words) after 29s and media 12 (4 chunks, 10067 words) after 88s.

This is the same failure that bit the speakers kind an hour earlier, but the fix is NOT the same and a blind cap raise would be wrong.

tasks.py:951-955 sizes a concat chunk like this:

    if spec.combine == "concat":
        # Each chunk comes back about as long as it went in, so a chunk may
        # not be bigger than the answer cap
        budget_tokens = min(budget_tokens, output_tokens)

The chunk's INPUT is capped at the OUTPUT cap. That assumption is right about the answer and silent about the thinking, which comes out of the same output budget - so a chunk sized to fill the cap leaves a reasoning model exactly zero room, and openrouter/auto routes to reasoning models. Raising max_output_tokens alone makes the chunks grow in lockstep and moves the failure rather than fixing it.

The fix is to give the input a share of the output room rather than all of it: budget_tokens = min(budget_tokens, output_tokens * SHARE), with the remainder left for whatever the model does before it starts writing. The share and the cap both want a measurement, and the same run that sets them can finally measure the length ratios this task needs.

So the first slice of this task is not the gate: it is making cleanup able to finish at all. Nothing about the gate's floor can be measured until an answer comes back.

MY FIRST DIAGNOSIS WAS WRONG, and the measurement is what said so. Recording it because the wrong answer is instructive.

I read tasks.py:951 (budget_tokens = min(budget_tokens, output_tokens)) and concluded the chunk input was sized to the whole output cap, leaving a reasoning model no room to think. I gave the input a share of the cap and raised cleanup's cap to 12000, and it changed nothing: still 4 chunks, still finish_reason='length'. The plan showed why - budget_tokens was already 6000 from the context window, so the share bound nothing.

Then the isolating run, which is the real finding:

  media 1  837 words, ONE chunk  -> finish_reason='length'
  media 7  168 words, ONE chunk  -> 168 words in, 5742 words out = 3418%

A 168-word recording came back as 5742 words. The model is not running out of room to think; it is RUNNING AWAY, and the length failures are the same runaway hitting the cap. Chunk size is irrelevant - one chunk of 168 words did it. More cap makes it worse, not better: it buys the runaway more room before it dies.

Both speculative changes reverted. What is left in the code is an eight-line note at that line, so the next person to suspect it has the measurement instead of the hypothesis.

TWO CONSEQUENCES FOR THIS TASK.

1. Robert's gate is not a safety net here, it is the only thing standing between him and a 34x hallucinated transcript. His instinct to demand one before the feature ships was right, and the evidence is stronger than either of us expected.

2. 'Apply cleanup automatically' cannot deliver much until cleanup itself works against a real provider. The gate will correctly reject nearly everything openrouter/auto produces for this prompt. That is the safe outcome and the honest one, but it means the feature's value depends on fixing cleanup - a pinned model, or a prompt that constrains length - which is a separate question from the gate and is not in this task's scope as written.
<!-- SECTION:NOTES:END -->
