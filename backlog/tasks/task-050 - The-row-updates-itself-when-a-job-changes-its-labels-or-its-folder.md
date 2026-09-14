---
id: TASK-050
title: The row updates itself when a job changes its labels or its folder
status: Done
assignee: []
created_date: '2026-09-14 19:46'
updated_date: '2026-09-14 20:06'
labels:
  - ui
  - library
dependencies: []
priority: medium
ordinal: 88000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-14, after the bulk labelling ran: "Update het scherm als je nieuwe labels hebt toegevoegd of een category."

The two columns TASK-048 added are written by things nobody clicks. The labels pass runs in a runner child and writes labels at the end of the job; an import drops a file in a folder. The row went on showing what was true when the page loaded, so labelling 55 files left the table looking untouched until a reload.

There is already a poller for exactly this window: _media_status.html asks for its own cell every two seconds while the row's latest job is queued or running, and stops when the status is terminal. The job that writes the labels IS that job, so the window is already the right one. What was missing is that the answer carried only the status.

The rule the existing design sets and this must not break: it is the cell that refreshes, not the table. Swapping #media-table on a timer would close an open row menu and throw away a half-typed rename, and a run is exactly when someone is tidying the rest of the library.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A label written while the row's job is running appears on the row without a reload
- [x] #2 A folder change reaches the row the same way
- [x] #3 The answer that reports a terminal status is the one carrying the final result, and polling stops after it
- [x] #4 No new timer: the refresh rides the poll that already exists, and the table is never swapped out from under an open menu
- [x] #5 Red then green, and a real run in a browser - an out-of-band swap of a table cell is the kind of thing that works in a test and not in a page
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The shape: the folder and label cells moved into _media_meta.html, which the table includes plainly and _media_status_poll.html includes with oob=true. The status route grew a folder join and a call to _attach_labels, and answers with the poll fragment.

No new timer, deliberately. The status cell already asks for itself every two seconds while the row's latest job is queued or running, and that job IS the one writing the labels - the window was already right, only the answer was too small. Terminal status still returns a fragment with no polling attributes, so it all stops on its own.

A race worth checking rather than assuming: runner.main calls jobs.finish('done') after every stage has returned, so the labels are committed before the status the poll reads turns terminal. The answer that switches polling off is the one carrying the result. Pinned by test_the_last_poll_carries_the_result_and_then_stops.

Verified in a browser, because an out-of-band swap of a <td> is exactly the kind of thing that works in a TestClient assertion and not in a page - htmx has to parse a bare table cell out of the response. On a snapshot of Robert's own library with a queued labels job on media 11: inserting a label straight into the database put 'live swap proof' on the row within one poll, no reload; moving the same file to another folder changed its Category cell from Hacker History to WHYcast the same way.

One setup mistake worth recording: seeding the job as 'running' does not work, because app startup reconciles a running job with no live process to 'interrupted' - correctly - and an interrupted row does not poll. Queued is the state to seed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A library row keeps itself current. The folder and the labels are written by things nobody clicks - the labels pass runs in a runner child, an import drops a file in a folder - and the row used to show what was true when the page loaded until something else made it re-render. Labelling 55 files left the table looking untouched.

It rides the poll that already existed rather than adding a timer: the status cell asks for itself every two seconds while the row's latest job is queued or running, which is exactly the job that changes these two things and exactly the window in which they change. The answer now carries the folder and label cells as out-of-band swaps beside the status cell. Terminal status returns no polling attributes, so everything stops on its own, and the last answer is the one carrying the result - runner.main finishes the job only after every stage has returned.

It is still two cells and not the table, for the reason the status cell already gives: swapping #media-table on a timer would close an open row menu and throw away a half-typed rename, and a run is exactly when someone is tidying the rest of the library.

Verified: tests/test_library_row_meta.py, 23 tests, 5 of them new and red first. Real run in Chrome against a snapshot of Robert's own library - a label inserted straight into the database appeared on the row within one poll without a reload, and a folder change moved the Category cell from Hacker History to WHYcast the same way. That run is the evidence that matters here: an out-of-band swap of a bare table cell is the kind of thing that passes a TestClient assertion and fails in a page. Suite green: 1355 passed, 1 skipped; 292 + 479 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
