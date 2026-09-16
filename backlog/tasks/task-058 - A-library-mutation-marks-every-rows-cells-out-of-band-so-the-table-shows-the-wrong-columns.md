---
id: TASK-058
title: >-
  A library mutation marks every row's cells out-of-band, so the table shows the
  wrong columns
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 14:32'
updated_date: '2026-09-16 14:54'
labels:
  - review-2026-09-16
  - web
dependencies: []
priority: high
type: bug
ordinal: 103000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). _after_change renders the whole table fragment with oob=True (scribe/web/library.py:474). _media_meta.html then marks both cells of every row with hx-swap-oob="true" (:16, :23), so htmx lifts them out of the response and swaps them by id instead of leaving them in the table it is replacing. The row that lands shows the upload date under Category and the duration under Labels, and the ids the status poll targets are gone until the next GET-rendered table. The poll does not need the flag from its caller: _media_status_poll.html:24 sets it itself with {% with oob = true %}.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A rename, move, label or private toggle answered over htmx returns a table whose folder- and labels- cells carry no hx-swap-oob attribute
- [x] #2 That same response still contains id="folder-N" and id="labels-N" for every row, so the status poll can keep targeting them
- [x] #3 The status poll's own answer is unchanged: it still carries both cells out of band
- [x] #4 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_web_library.py: een rename over htmx levert een tabel waarin de folder- en labels-cellen geen hx-swap-oob dragen, en hun id's nog wel.
2. Fix: scribe/web/library.py:474 geeft oob=False mee, zoals het GET-pad op :444.
3. Controleer dat de statuspoll ongemoeid blijft: _media_status_poll.html:24 zet oob zelf aan.
4. Draai tests/test_web_library.py en tests/test_web_transcript.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Oorzaak: _after_change gaf oob=True door aan een volledige tabelrender. In _media_rows.html gebruikt die vlag twee dingen tegelijk: de zijbalk en de flash als out-of-band aanhangsel (regels 131-132), en - via de include op regel 61 - de twee metacellen van elke rij. Htmx tilt die cellen dan uit het antwoord en plaatst ze op id, waardoor de tabel per rij twee cellen mist.

Eerste poging was te grof (oob=False bij de aanroeper) en brak twee echte gedragingen: de zijbalk-swap (test_web_library.py:537) en de flash na een htmx-upload (test_web_transcribe_dialog.py:414). De fix zit nu bij de include: _media_rows.html:61 neemt _media_meta.html op met {% with oob = false %}, precies zoals _media_status_poll.html:24 hem met true opneemt. library.py:474 blijft oob=True voor zijbalk en flash.

Rood: assert not [...] faalde met folder-3, labels-3, folder-1 en labels-1 die alle vier hx-swap-oob droegen.
Groen: tests/test_web_library.py 79 passed, tests/test_web_transcribe_dialog.py 54 passed, tests/test_web_transcript.py 94 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
De metacellen van een rij worden niet langer out-of-band gemarkeerd binnen een tabel die toch al in zijn geheel wordt vervangen; de zijbalk en de flash blijven dat wel. Twee nieuwe tests pinnen beide kanten vast: een mutatie levert cellen zonder hx-swap-oob met hun id's intact, en de statuspoll draagt ze nog steeds wel out-of-band. Geverifieerd met 79 + 54 + 94 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
