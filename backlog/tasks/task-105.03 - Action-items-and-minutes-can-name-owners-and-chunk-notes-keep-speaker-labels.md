---
id: TASK-105.03
title: 'Action items and minutes can name owners, and chunk notes keep speaker labels'
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-06 21:05'
labels:
  - llm
dependencies: []
parent_task_id: TASK-105
ordinal: 193000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
action_items.md and minutes.md ask for an owner by name or speaker label but run with_speakers=False; map_chunk.md does not ask to keep SPEAKER_XX labels, so a long recording's speaker pass may lose them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 These kinds receive speaker display names, or the wording stops asking for labels; tested
- [x] #2 map_chunk asks to keep each line's speaker label; a test with notes that drop labels shows the effect
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
chunking.Speakers: False / True (cluster label) / 'names' (render.speaker_display: the name somebody set, else Speaker N). action_items and minutes now run with 'names', so an owner reads 'Arthur Dent' or 'Speaker 2', never SPEAKER_01 or nothing. map_chunk gains, only when the lines carry a speaker: 'Begin every note with the speaker of the line it came from'. The note call also gets the language line now (it used bare SYSTEM, so a long Dutch recording's notes had none - found while here). Red: owner tests for action_items and minutes (no speaker in the lines), map_chunk tests for True and 'names' (no instruction); green after; summary still plain (passes before and after). Digest moved within version 3, which was never released - recorded without a second bump, said in the digest docstring. Green: test_llm_tasks 169, chunking 32, chat 27, speakers 51, finalize 39, web_ai 147. Real run (gemma4:12b, local Ollama, scratch copy, a synthetic 300-line two-speaker Dutch recording forced into 4 chunks, speakers kind, reasoning off): notes WITHOUT the sentence 20/20 lines kept SPEAKER_xx, WITH it 46/46. So no effect measured on this model: the sentence is a guard, not a measured fix. Both runs also hit the note cap on one chunk because the measurement squeezed the context to 4000 tokens. Output: scratchpad notes-run.txt.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Action items and minutes see who speaks, by the names the page shows; long-recording notes are asked to keep the speaker (no loss measured on gemma4:12b either way); notes get the language line too.
<!-- SECTION:FINAL_SUMMARY:END -->
