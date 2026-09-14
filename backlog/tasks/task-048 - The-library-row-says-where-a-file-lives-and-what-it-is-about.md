---
id: TASK-048
title: The library row says where a file lives and what it is about
status: Done
assignee: []
created_date: '2026-09-14 16:55'
updated_date: '2026-09-14 17:14'
labels:
  - ui
  - library
dependencies: []
priority: medium
ordinal: 86000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-14, with a screenshot of the All files table: "Can you add the category after the title, and the labels (up to three labels)".

Today a row is title, uploaded, duration, mode, status. Which folder a file sits in and what it is about are both in the sidebar - as a tree and as a label list - but never on the row, so "where does this one live" and "what is this one about" need a click or a guess. The information exists: media.folder_id, and media_label joined to label (v11, TASK-023).

Two things the shape has to get right. The row already carries seven columns and a popover menu, so this goes under the title as a meta line rather than as new columns. And the labels must not cost a query per row: media_rows returns up to RECENT_LIMIT rows, and one SELECT per row would be a query storm on a 55-file library and worse later - one grouped query over the ids that are already on screen.

Three is the cap Robert named; a file with more says so rather than hiding the rest silently.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The folder a file sits in is a column of its own beside the file, and reads Uncategorized when it has none
- [x] #2 Up to three of the file's labels are a second column, in a stable order that does not shift when other files change
- [x] #3 A file with more than three labels says how many more rather than hiding them silently, and names them where a person can see
- [x] #4 The folder and each label are clickable and land on the same filtered view the sidebar's own links do
- [x] #5 Rendering N rows costs a fixed number of queries, not one per row - proved by counting statements, not by reading the code
- [x] #6 Neither column pushes the columns beside it off the row, whatever the label names are
- [x] #7 Red then green: tests for the folder name, the cap, the overflow count, the links, the query count and the bounding box
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Shape corrected mid-implementation, on Robert's word: "The left navigation is like a filter system, while the information should be in the table next to each file." The first cut put both under the title as a meta line, reasoning that the table already carried seven columns; that was the wrong trade. Category and Labels are columns now, and the acceptance criteria were rewritten to say so rather than left describing a shape nobody asked for.

Query cost. media_rows gains a LEFT JOIN on folder for the name, and _attach_labels asks once for the whole page rather than once per row - the difference is invisible on the three rows a test seeds and a storm on a real library. The ids are chunked at 900 because SQLite's host-parameter ceiling is a compile-time option and this is the one place a row count reaches a query. Pinned by counting statements through conn.set_trace_callback: 2 rows and 20 rows cost the same number of SELECTs.

Order is alphabetical, COLLATE NOCASE, deliberately. Ordering by how popular a label is across the library would reshuffle a row whenever an unrelated recording was labelled; the three names beside a title should be the same three tomorrow.

One bug found by looking rather than by reading, which is the whole reason for the real run. The first screenshot showed the label chips running straight over the Uploaded column and swallowing the "+2": a max-width on a <td> does nothing in a table that sizes itself to its content, and .badge carries white-space: nowrap. The chips live in a flex box inside the cell now. test_the_chips_sit_in_a_box_that_can_bound_them holds the structure the stylesheet needs - the only part of a layout bug a test can hold onto.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The library table says what a file is, beside the file. Two columns after Title: Category, the folder it lives in (Uncategorized when it has none), and Labels, up to three of them with a +N for the rest - the names of the rest in the title attribute, so the cap never quietly misrepresents a recording carrying eight. Both link to the same filtered views the sidebar's own links do, so the row and the tree cannot disagree about what a click means; the sidebar goes on being the filter and the table now carries the information.

The labels cost one query for the whole page, not one per row, and that is asserted by counting statements rather than by reading the code: 2 rows and 20 rows issue the same number of SELECTs. Alphabetical order, so the three names beside a title do not reshuffle when an unrelated recording is labelled.

Verified: tests/test_library_row_meta.py, 14 tests, 11 red first. Real run against a consistent snapshot of Robert's own library (sqlite3 backup API; the live database was opened read-only and never written): 55 files, and the one file carrying five labels renders three chips plus +2 with 'penetration testing, security conferences' on hover. That run is also what found the layout bug - the chips overran the Uploaded column until they were given a flex box to sit in, which no green suite would have shown. Suite green: 1345 passed, 1 skipped; 861 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
