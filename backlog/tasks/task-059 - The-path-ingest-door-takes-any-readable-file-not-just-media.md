---
id: TASK-059
title: 'The path-ingest door takes any readable file, not just media'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 14:32'
updated_date: '2026-09-16 14:54'
labels:
  - review-2026-09-16
  - security
dependencies: []
priority: high
type: bug
ordinal: 104000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). POST /api/media with {"path": ...} checks only that the path lies under the browse roots (scribe/app.py:178 -> fsbrowse.is_allowed), and those roots default to the whole profile drive on Windows and $HOME on POSIX (fsbrowse.py:44-53). media.ingest_path then hashes the file and hardlinks it into the store before any probe can object (media.py:123-132), and the library's download route serves those bytes back. The boundary crossed is a second local principal - another account, a sandboxed or low-integrity process - that can reach 127.0.0.1 but cannot open the file itself: the web process reads it with the user's rights. That is the attacker scribe/app.py:33 already declares in scope. fsbrowse imports probe.MEDIA_EXTENSIONS but uses it only as a display hint in listdir (:130).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 POST /api/media with a path whose suffix is not in probe.MEDIA_EXTENSIONS is refused before the file is read or linked into the store
- [x] #2 The refusal names the reason and does not leak the file's contents or size
- [x] #3 The existing status codes stay as they are: a missing file is still 404 and a directory is still 400
- [x] #4 A path with a media suffix still ingests exactly as before, including the deduplication path
- [x] #5 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_pipeline_e2e.py: POST /api/media met een pad naar een niet-mediabestand binnen de wortels wordt geweigerd, en het bestand belandt niet in de store.
2. Fix in scribe/app.py _ingest_local_path: na de wortelcontrole en na de map-afhandeling een suffix buiten probe.MEDIA_EXTENSIONS weigeren met 415.
3. Volgorde bewaakt de bestaande codes: een map blijft 400 (test_pipeline_e2e.py:853), een ontbrekend .wav blijft 404 (:846).
4. Draai tests/test_pipeline_e2e.py, tests/test_params_door.py en tests/test_web_transcribe_dialog.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/app.py _ingest_local_path: na de wortelcontrole weigert de deur een suffix buiten probe.MEDIA_EXTENSIONS met 415, voordat media.ingest_path het bestand hasht en de store in linkt. Mappen worden overgeslagen, zodat de bestaande 400 uit de ingest zelf blijft komen; een ontbrekend .wav houdt zijn 404. probe is toegevoegd aan de bestaande stages-import (regel 14); dat trekt niets nieuws het webproces in, want fsbrowse importeerde MEDIA_EXTENSIONS daar al uit.

Rood: assert 201 == 415 - een bestand met de naam id_rsa werd gewoon opgenomen.
Groen: gerichte test passed; tests/test_pipeline_e2e.py 53 passed, tests/test_params_door.py 26 passed, tests/test_web_transcribe_dialog.py 54 passed. De test toetst ook dat er geen media-rij met die naam bestaat en dat de store leeg blijft.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
De pad-ingestdeur eist nu een audio- of videosuffix voordat zij een bestand leest, zodat een aanroeper die wel bij 127.0.0.1 kan maar niet bij het bestand, het niet meer door het webproces laat inlezen en via de downloadroute terugkrijgt. Bestaande codes blijven: 404 voor een ontbrekend bestand, 400 voor een map. Geverifieerd met 53 + 26 + 54 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
