---
id: TASK-053.02
title: Follow-along at sixty samples a second
status: Done
assignee: []
created_date: '2026-09-14 21:08'
updated_date: '2026-09-14 22:16'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 93000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 2 of TASK-053. Highlighting hangs on timeupdate, which fires about four times a second. wordAt(t) returns the last word started by t, so words that begin between two ticks are never lit. Measured on media 20, run 146, 12,108 words: the median gap between word starts is 0.240s and 52.4% of words begin within 250ms of the previous one. More than half the recording is skipped, systematically. Drive the highlight from requestAnimationFrame while playing and fall back to timeupdate when paused; wordAt and its binary search do not change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Playback drives the highlight from animation frames, not from timeupdate
- [x] #2 timeupdate stays as the fallback for a backgrounded tab and keeps saving the resume position
- [x] #3 The arithmetic is measured on real word times: how many words 4 Hz lights against 60 Hz
- [x] #4 The page scrolls only when the followed word would leave the viewport's middle band
- [x] #5 Only one follow loop can run
- [ ] #6 Watched in a foreground browser: a word is lit for every word spoken
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The magnitude, measured on the real word times of media 20 rather than argued from the spec. Over five minutes of the conversation (120s-420s) the transcript holds 998 words; sampling wordAt at 4 Hz lights 784 of them and at 60 Hz all 998. So 214 words - 21.4% of that window - were never lit at all.

That corrects my own earlier framing. I had quoted 52.4%, which is the share of words that START within 250ms of the one before; it is not the share that gets skipped, because several words can fall inside one tick and only the ones in the middle are lost. 21.4% is the measured number and the one to use.

NOT verified, and I could not: that the requestAnimationFrame loop actually runs during playback. Chrome gives a backgrounded tab zero animation frames, and I drive the browser from the background - document.visibilityState reported 'hidden' on every attempt and the probe counted 1 frame in several seconds. So the loop is asserted in the source and its arithmetic is measured, but nobody has yet watched it light a word. Robert sees it in one click; it needs a foreground tab.

A second change came with it, and it is not cosmetic. At four samples a second, re-centring the page on every highlight move was fine. At sixty the highlight moves once per word - ten times a second in fast speech - and a smooth scroll restarted ten times a second never arrives anywhere. So the page now scrolls only when the followed word would leave the middle band of the viewport (FOLLOW_BAND_TOP/BOTTOM, 20%-80%), which also reads better: the text stays still while the highlight travels down it.

timeupdate stays wired on purpose. It saves the resume position, and a backgrounded tab plays on without animation frames, so it is the fallback that keeps the highlight roughly right until the tab comes back. Both paths call highlight, which returns early when the word has not changed, so they cannot fight.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The highlight is sampled finer than the words. timeupdate fires about four times a second and wordAt returns the last word started by t, so every word that began between two events was skipped - silently and systematically. Playback now drives the highlight from requestAnimationFrame; timeupdate stays wired because it saves the resume position and because a backgrounded tab plays on without animation frames, and both paths call the same function, which returns early when the word has not changed.

Measured on the real word times of media 20, over five minutes of the conversation: 998 words in the window, 784 lit at 4 Hz, all 998 at 60 Hz. 214 words - 21.4% - were never lit. That also corrects the 52.4% I quoted from the plan: that is the share of words starting within 250ms of the previous one, which is not the same as the share skipped.

The page now scrolls only when the followed word would leave the middle band of the viewport. At four samples a second re-centring on every move was harmless; at sixty the highlight moves once per word and a smooth scroll restarted ten times a second never arrives.

Verified: 4 new tests in tests/test_web_transcript.py (55 in the file), plus the 4 Hz against 60 Hz count taken in Chrome against a snapshot of the real library. Suites green: transcript 55, ai 97, scaffold 27. AC6 is left unchecked: Chrome gives a backgrounded tab zero animation frames and I drive it from the background, so nobody has yet watched the loop light a word during playback. It needs a foreground tab and one click.
<!-- SECTION:FINAL_SUMMARY:END -->
