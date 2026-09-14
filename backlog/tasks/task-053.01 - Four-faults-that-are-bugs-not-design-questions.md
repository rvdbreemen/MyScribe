---
id: TASK-053.01
title: 'Four faults that are bugs, not design questions'
status: Done
assignee: []
created_date: '2026-09-14 21:08'
updated_date: '2026-09-14 21:28'
labels:
  - ui
  - bug
dependencies: []
parent_task_id: TASK-053
ordinal: 92000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 1 of TASK-053, and none of it moves anything on screen.

1. The speed buttons lie. There is no ratechange listener anywhere in app.js; setSpeed is the only writer of aria-pressed. Change speed through the player's own kebab menu - browser chrome this app cannot remove - and the audio plays at 2x while 1x still reads aria-pressed=true.
2. A finished answer is not announced. aria-live=polite sits on the "Working..." hint inside _ai_output.html, which is removed when the answer arrives - so the region that would announce it is gone by the time there is anything to say. It belongs on the .ai-output section.
3. Search lies in the cleaned reading. runSearch only ever walks #transcript .para. With the clean reading showing, the words are hidden, yet the box still counts matches, still highlights them and still scrolls to elements nobody can see. The <pre> holding the clean text is never searched.
4. Three comments say the AI panel offers six questions. KINDS has nine.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Changing the rate through the browser's own menu re-syncs the speed buttons
- [x] #2 A finished answer is announced to a screen reader, and the region that announces it survives the swap
- [x] #3 With the cleaned reading on screen, search counts and reveals matches in the text that is actually visible
- [x] #4 No comment claims six questions
- [x] #5 Red then green, one test per fault
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Proved in a browser, because this repository has no harness that runs app.js. The comment at app.js:969 refers to a Node harness; grep finds none - only pytest reading the file as text. So the tests assert the source and the behaviour is measured in Chrome against a snapshot of the real library.

Speed buttons. Setting playbackRate from script is the same path the player's own overflow menu takes, so no playback was needed. Before, simulated in the same browser by replacing the audio element with a clone - listeners do not survive cloneNode - rate 2, button '1' lit. After: rate 2, button '2' lit; rate 1.25, button '1.25' lit. The rule is now that the buttons read the element rather than remember what was pressed, and setSpeed no longer writes aria-pressed at all, so the two paths cannot disagree.

Search. media 7 has a cleanup answer but clean_reading was empty - the answer was produced and never applied - so no recording in the library currently renders the second reading. Seeded one into the throwaway snapshot to exercise it (the live database was opened read-only). Searching 'la': showing the words, 114 matches and 114 highlight ranges, all inside #transcript, none in #clean-reading; switching reading, 5740 matches and 5740 ranges, all inside #clean-reading, none in the hidden words. Before the fix both readings reported 114 and highlighted text nobody could see.

That recording is also the 'La, la' loop from TASK-036 - 162 words becoming 5742 - which made it a good stress case by accident.

aria-live. Nine answer sections each carry aria-live=polite and aria-busy, and the Working hint no longer carries a live region: the hint is removed when the answer arrives, so a region attached to it was gone before there was anything to announce.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Four faults that were bugs rather than matters of taste, none of which moves anything on screen.

The speed buttons read the audio element instead of remembering what was pressed. The player's own overflow menu is browser chrome this app cannot remove, and a rate chosen there left 1x reading aria-pressed=true while the audio ran at 2x. setSpeed no longer writes the buttons at all - a ratechange listener does - so the two paths cannot disagree.

The answer sections are the live region, not the Working hint inside them. The hint is removed when the answer lands, so a region attached to it was gone by the time there was anything to announce. aria-busy says whether a section is still being written.

Search looks at whichever reading is on screen. With the cleaned reading showing it used to count matches in the hidden words, highlight them where nobody could see and scroll to them - failing in exactly the mode a reader picks when they want to read rather than verify.

And no comment claims six questions; KINDS has been nine for a while.

Verified: 5 new tests in tests/test_web_transcript.py, plus a browser run against a snapshot of the real library - rate 2 lights the 2x button where a cloned element without the listener still lit 1x, and searching 'la' put 114 highlight ranges in the words and 5740 in the cleaned reading, none in the hidden one either way. Suites green: transcript 51, ai 97, scaffold 27, app 17.
<!-- SECTION:FINAL_SUMMARY:END -->
