---
id: TASK-049
title: 'Show every label on a row, and let the library label a whole selection'
status: Done
assignee: []
created_date: '2026-09-14 17:44'
updated_date: '2026-09-14 18:21'
labels:
  - ui
  - library
dependencies: []
priority: medium
ordinal: 87000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two things Robert asked for on 2026-09-14, after TASK-048 put Category and Labels in the table.

"Also show all labels that are with a episode": the cap of three plus a +N was his earlier instruction and he has changed it having seen it. The cell wraps, so a recording with eight shows eight.

"How to run through the full list of files and add labels?": it already works and cannot be reached. library.BULK_ACTIONS has carried 'label' the whole time - bulk() queues one llm job of kind 'labels' per selected file, skips private recordings when the provider is not local, and skips files with no transcript, reporting both. The select in library.html offers move, trash and retranscribe and never offered it. The gap is one option element, not a feature.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A row shows every label the recording carries, not the first three
- [x] #2 The bulk action bar offers labelling, and it queues one job per selected file
- [x] #3 A selection that includes private recordings or files without a transcript still reports what was skipped and why
- [x] #4 Red then green: a test for a recording with more labels than the old cap, and one that drives the bulk action through the route
- [x] #5 The tier icons say what the tiers trade - speed against accuracy - and carry a label a screen reader can read
- [x] #6 The old pair is gone from every surface, not just the one that was looked at
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Grew by two asks while it was open, both from Robert on 2026-09-14.

"Replace the icons from TurboScribe with two icons that mean completed large and speedy turbo." They were a dolphin and a whale, carried over from the app this one was modelled on; a sea mammal says nothing about a transcription model. Searched rather than guessed: the lightning bolt is the settled UI convention for power, speed and instant action, so Turbo is the bolt and Maximaal the target - which is the trade the two tiers actually make. UX Content Collective's emoji guidance says an emoji carrying meaning on its own needs role=img and an aria-label, or the Mode column announces 'high voltage'; both are on the span now. ADR-004 names the tiers and is Accepted, so 'Turbo' and 'Maximaal' did not move.

The pair appeared as literals in five places - the macro, the jobs summary, and three option lists - which is exactly how half a rename ships. A test walks scribe/ and fails on either old character, so the settings page cannot keep the whale while the table shows the bolt. The dated design documents under docs/superpowers/ still show the old pair on purpose: they record what was decided then, the way an ADR does.

"Also bump the version from time to time." 0.1.0 -> 0.2.0: this batch carried the macOS merge, the disk floor, the feed question, the queue controls and these two columns. The number lived in scribe/__init__.py and pyproject.toml with nothing tying them together, so bumping one and forgetting the other was a silent drift - and the stale one would be the header a person reads. tests/test_app.py now asserts they match.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Four things, all of them Robert's after seeing TASK-048 on screen.

A row shows every label the recording carries, not the first three - the cap and its +N are gone, and the cell wraps, so a recording holding five shows five. The bulk bar offers 'Label with the AI': library.BULK_ACTIONS has carried 'label' since the labels pass existed and the select never offered it, so the only way to label a library was one file at a time on its own page. One llm job per selected file, and what it skips it reports - a private recording when the provider is not local, and anything with no transcript to read.

The tier icons are a lightning bolt for Turbo and a target for Maximaal, replacing the dolphin and whale inherited from the app this one was modelled on: speed against accuracy is what the tiers actually trade, and the bolt is the settled convention for fast. role=img and aria-label, because an emoji carrying meaning alone would otherwise be announced as 'high voltage'. ADR-004's names did not move.

Version 0.1.0 -> 0.2.0, and the two places that hold it are now tied together by a test.

Verified: tests/test_library_row_meta.py, 18 tests, and tests/test_app.py, 17. Real run against a snapshot of Robert's own library: the file carrying five labels shows five chips over two lines, the Mode column shows the bolt, the bulk menu lists all four actions and the header reads v0.2.0. Every suite that touches the icons green - transcript 46, library 77, jobs 42, settings 35, dialog 54, scaffold 27.
<!-- SECTION:FINAL_SUMMARY:END -->
