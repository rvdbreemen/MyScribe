---
id: TASK-053.07
title: The player gets a structure ribbon
status: Done
assignee: []
created_date: '2026-09-14 21:08'
updated_date: '2026-09-15 00:19'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 98000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 7 of TASK-053. The player is a 52px strip with no link between the playhead and the shape of the recording. A ribbon carrying chapter ticks where chapters exist, speaker bands, the playhead and search hits makes an hour legible at a glance and turns chapters from an answer you read into a way to move. Explicitly NOT a waveform: nothing stores peaks and decoding a 64-minute file in the browser costs minutes of CPU, so promising one would be a lie. Geometry must come from the timing attributes, never from measured boxes - content-visibility:auto makes off-screen reads unreliable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The player carries a band per speaker turn, placed by time
- [x] #2 Each band says where it goes and who is speaking, and a click seeks there
- [x] #3 Positions come from the word timings, never from a measured box
- [x] #4 A recording with no duration gets no ribbon rather than a guessed one
- [x] #5 No waveform is promised
- [x] #6 Red then green, and seen in a browser
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Two corrections came out of looking rather than reading, and neither test caught either.

Per TURN, not per paragraph. The first version drew one band per paragraph and the screenshot was a picket fence: a silence of a few seconds starts a new paragraph even when nobody else has spoken, so this recording has 208 of them. Merging consecutive paragraphs that share a speaker gives 143 turns, which is the shape of the conversation rather than the shape of the pauses.

And a tone per SPEAKER, not per band. The colour was meant to come from speaker_label.color, and on this recording every speaker has none - so every band fell back to one tone and it was a fence again. A tone numbered by first appearance stands in, and media 20 now reads 72 bands in one tone against 71 in the other: two people, alternating, with Dan holding almost all the width.

A third thing found by looking, and this one was a real bug: editing inside a {# ... #} block left an unbalanced closer, and half a comment rendered as visible text above the player - "would be a promise this app cannot keep. #}". Every template test passed. There is now one assertion over five pages that no comment delimiter reaches the browser.

Explicitly not a waveform: nothing stores peaks, and decoding a 64-minute file in the browser costs minutes of CPU. A test asserts AudioContext, decodeAudioData and getChannelData appear nowhere in app.js, because that is the obvious thing for a later hand to add.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The player carries the shape of the recording: one band per speaker turn, placed by time, coloured by speaker, click to go there. An hour of transcript stops being a wall of text with no way to see who talks when.

Per turn and not per paragraph, which is the difference between a ribbon and a picket fence - a silence of a few seconds starts a new paragraph even when nobody else has spoken, so media 20 has 208 paragraphs and 143 turns, and the first attempt drew all 208 and read as noise. A tone per speaker rather than per band, because diarization had assigned no colours here and every band fell back to one tone: 72 against 71 now, two people alternating with one holding almost all the width.

Every position is a percentage computed on the server from the word timings, and the test asserts getBoundingClientRect appears nowhere in the playhead code: content-visibility: auto makes off-screen paragraphs unreliable to measure, and the ones worth jumping to are always off screen.

No waveform, and a test keeps it that way - nothing stores peaks and decoding a 64-minute file in the browser costs minutes of CPU, so promising one would be a lie.

Verified: 7 new tests in tests/test_web_transcript.py (83 in the file), and three browser looks that each changed the design - the fence, the single tone, and a leaked template comment rendering as visible text above the player, which every template test had passed. Suites green: transcript 83, ai 97, scaffold 27.
<!-- SECTION:FINAL_SUMMARY:END -->
