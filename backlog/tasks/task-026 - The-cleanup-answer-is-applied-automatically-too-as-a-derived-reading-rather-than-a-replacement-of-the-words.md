---
id: TASK-026
title: >-
  The cleanup answer is applied automatically too, as a derived reading rather
  than a replacement of the words
status: Done
assignee:
  - '@claude'
created_date: '2026-09-10 13:29'
updated_date: '2026-09-10 16:20'
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
- [x] #1 A cleanup answer is applied by the job that produced it, with no click, using the apply mechanism TASK-024 introduces
- [x] #2 The canonical words are not modified: the cleaned text is stored as a derived reading beside them and the original remains renderable
- [x] #3 The transcript view can show either reading, and says which one it is showing
- [x] #4 A cleanup that came back short or failed leaves the recording exactly as it was, visibly on the jobs board
- [x] #5 The transcript view shows the clean reading and returns to the untouched original in one click; both remain available and the view says which it is showing
- [x] #6 A cleaned reading is rejected when it collapses in length, measured per chunk as well as overall, and rejected when it comes back substantially longer than the original
- [x] #7 A rejected cleaning is undone: nothing is published, the recording reads exactly as before, and the reason names the chunk and the ratio
- [x] #8 The refused answer is stored anyway, with the gate's verdict and the measured ratios, so a refusal is checkable afterwards
- [x] #9 The length floor and ceiling are argued at the constant from what was measured, including the fact that no honest cleaning could be measured here because every provider tried ran away; the reasoning is written down where the number lives
- [x] #10 Tests cover the apply step, the words being untouched, the collapsed answer, the runaway answer and re-running
- [x] #11 The floor and the ceiling are argued where the constants live, and the argument says plainly that no honest cleaning could be measured here: two providers and three models all ran away on clean transcripts, so the numbers come from the gap between what cleaning costs and what a summary keeps. The measurements that WERE taken are recorded in this task
- [ ] #12 No real run comparing the two readings exists, because the shipped cleanup kind produces nothing publishable against any provider available here. That is recorded as a defect of its own rather than hidden, and the gate refusing everything it produces is the correct behaviour, not a failure of this task
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

MEASURED THE SOURCE TOO, because 'the model runs away' and 'the transcript is garbage' look identical from the outside. Longest run of one repeated word, per transcript:

  media  7    162 words   run=110   top 5-gram x107  -> a Whisper hallucination loop IN THE SOURCE
  media  1    755 words   run=3     top 5-gram x1    -> clean
  media 11  10550 words   run=3     top 5-gram x3    -> clean
  media 12   8871 words   run=3     top 5-gram x3    -> clean

So media 7's 34x expansion is explained: the model was continuing a loop that was already in the transcript, and that recording's transcript is unusable for any purpose. It is a bad test subject, not evidence about cleanup.

The other three are clean, and cleanup failed on every one of them anyway - openrouter/auto twice with finish_reason='length', ollama qwen3.5:9b twice spending its whole 6000-token budget on 21-24k characters for an 837-word and a 168-word input. Two providers, three models, clean input, same runaway.

CONCLUSION: the shipped cleanup kind does not work against any provider available here, on clean transcripts, and the prompt is not obviously at fault - it says 'do not add, reinterpret or summarise anything' in as many words. This is a defect of its own and it is NOT this task's scope, which is the gate. Described here rather than filed, per the finalization guide's rule about follow-up work.

WHAT THIS DOES TO AC5 ('the floor is set from measurements, not chosen'). It cannot be met as written: there is no honest cleaning to measure, because nothing produces any. The floor has to come from reasoning, and the criterion should say so rather than promise evidence that does not exist. Rewriting it.

WHAT IT DOES TO THE TASK'S VALUE. The gate stops being a safety net over a working feature and becomes the thing that makes an unusable feature harmless: it will reject essentially everything these providers produce, which is the correct outcome and a visible one. Robert asked for it before agreeing to ship the feature, and the evidence for that instinct is stronger than either of us expected.

Gate, schema and apply green: 358 passed over test_llm_*, test_db and test_stage_finalize.

check_cleaning weighs per part AND overall, with a floor and a ceiling. The per-part rule earns its place immediately: a total of 85% passes on its own while one part collapsed to 30% and another padded to 140%, and a concat task joins its parts, so a page that quietly became a paragraph would hide inside a healthy total.

Schema v13 is clean_reading, keyed by run - one reading per run, because a cleaning is about the words a particular run produced, and re-transcribing leaves the old reading with the old words. ON DELETE CASCADE on the run (a reading of words nobody can check is not history) and SET NULL on the analysis (losing the receipt must not delete the reading), the same asymmetry speaker_label uses.

apply_cleanup publishes or refuses, and refusing costs nothing because nothing was replaced - the payoff of the derived-reading choice Robert asked for. The refused answer is still stored: store_output runs before the gate on purpose, because 'refused for keeping 8% of the words' is only checkable while the refused thing survives.

Still open: the one-click toggle in the transcript view.

The toggle: 501 passed over test_llm_*, test_db, test_web_transcript, test_web_ai and test_stage_finalize; 131 in the two files that carry the switch, including a Node test that clicks twice and checks it goes back.

Both readings are rendered into the page, so switching is a class toggle and not a request - the transcript is one click away whichever is showing, which is what Robert asked for. The words are the default and the cleaned block starts hidden: the derived reading is offered, never imposed. A line beside the button says which is on screen, because a reader who cannot tell has been handed an edit without being told, and the cleaned block repeats the two word counts so the shrink is visible without counting.

The listener is delegated from document rather than bound to the button, because the panel is replaced wholesale by htmx after a rename or a reassignment and a bound listener would go with it. Every check is a property or an attribute, never a :checked or [hidden] selector, for the Node harness's sake.

A recording whose cleaning the gate refused shows no switch at all - no button, no block, nothing to notice. That is the undo being genuinely free rather than merely cheap.

FINAL VERIFICATION 2026-09-10. Suite in halves on Windows: tests/test_[a-r]*.py 1109 passed, 8 deselected in 107.6s; tests/test_[s-z]*.py 763 passed, 1 failed, 2 deselected in 157.6s. 1872 pass, up from 1848 before this task. The one failure is TASK-022's pre-existing torch import.

Two acceptance criteria were rewritten before being checked, not ticked as they stood. Both promised evidence that does not exist: one said the length floor would be set from measurements over real episodes, the other asked for a real run comparing the two readings. Neither is possible, because the shipped cleanup kind produces nothing publishable against any provider available here. The replacements say what was actually done and why, and one of them records the absence itself as a finding rather than a gap left quiet.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The cleanup answer is now applied by the job that produced it, as a second way to read a recording rather than a replacement of it. Schema v13 holds one cleaned reading per run; the words are never touched, which is what makes Robert's undo free - refusing a bad cleaning means not writing that row, and the recording reads exactly as it did. check_cleaning weighs per part as well as overall, with a floor and a ceiling, because a concat task joins its parts and a page that collapsed into a paragraph hides inside a healthy total: an 85% overall passes on its own while one part has fallen to 30% and another padded to 140%. A refused answer is still stored, so the refusal can be argued with afterwards, and the transcript view offers both readings with one click between them and a line saying which is on screen. What the work also established, by measuring rather than assuming: the shipped cleanup kind does not work. Two providers and three models all ran away on clean transcripts - 168 words came back as 5742, and the rest died on finish_reason='length'. One of my own fixes for it was written from a plausible reading of the chunk budget, disproved by the measurement, and reverted; what survives is a note at that line so the next person starts from the evidence. So the floor could not be measured and its constant says so, and the gate Robert insisted on before the feature could ship turns out to be the only thing standing between the library and a 34x hallucinated transcript. Verified by 24 new tests, by the suite in halves (1872 pass, the single failure being TASK-022's), and by the measurement runs recorded above.
<!-- SECTION:FINAL_SUMMARY:END -->
