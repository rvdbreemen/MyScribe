---
id: TASK-053.05
title: 'The answer panel is bounded, collapsible, and remembers its tab'
status: Done
assignee: []
created_date: '2026-09-14 21:08'
updated_date: '2026-09-14 23:11'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 96000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The second half of the shape in TASK-053.04: the panel's own behaviour.

Bounded, because 'blog' allows 6000 output tokens and 'cleanup' is as long as the input - so an unbounded panel above the transcript would open every later visit on screens of answer, which is the fault this rebuild exists to remove, moved rather than fixed. A maximum height with its own scroll means the transcript starts in the same place forever.

Collapsible, because a reader who wants the words should be able to put the answers away without losing them.

And it remembers which tab was open, per recording, so coming back to a transcript you were working through does not drop you on Summary every time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The menu of what to ask sits above the transcript, not in the rail
- [x] #2 A tab per kind, and the tab strip says which hold an answer or are working
- [x] #3 Every card stays in the document so it keeps polling; only one is shown
- [x] #4 Asking switches to its own tab before the request leaves
- [x] #5 The answer panel is bounded and rests closed, so the transcript starts in the same place
- [x] #6 An answer being written still survives a rename
- [x] #7 Red then green, and seen in a browser
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The answer panel is bounded at min(38dvh, 24rem) with its own scroll, and rests closed unless something is being written.

Bounded because 'blog' allows 6000 output tokens and 'cleanup' is as long as the input: an unbounded panel above the transcript would open every later visit on screens of answer, which is the fault this rebuild removes, moved rather than fixed.

Closed because the transcript is the page - Robert's rule - and an open panel costs it 300px of a 800px window on every visit. The tab strip stays either way, so which kinds hold an answer is visible without opening one. A running job opens it, because that is the one you are waiting for; the browser then overrides that from what was left open for this recording, and only when a choice was actually made.

Verified with the same browser run as TASK-053.04: 236px closed against 548px open, and the closed panel is hidden by the stylesheet rather than removed from the markup, so every card keeps polling while it is shut.
<!-- SECTION:FINAL_SUMMARY:END -->
