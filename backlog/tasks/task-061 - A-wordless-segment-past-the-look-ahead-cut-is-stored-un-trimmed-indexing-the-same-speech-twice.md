---
id: TASK-061
title: >-
  A wordless segment past the look-ahead cut is stored un-trimmed, indexing the
  same speech twice
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 15:04'
updated_date: '2026-09-16 15:06'
labels:
  - review-2026-09-16
  - pipeline
dependencies: []
priority: medium
type: bug
ordinal: 106000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). In collect_segments both look-ahead trims are gated on the segment having words: 'if words and not kept: break' is dead on an empty list and 'len(kept) < len(words)' is 0 < 0 (scribe/stages/transcribe.py:451-457). A segment whose word list is empty therefore keeps the end and the text the decoder gave it, even when those run past the cut into the look-ahead the next window decodes again. faster-whisper does yield such segments: its yield filter drops only an empty text or a zero-length span. The same passage then sits in two segment rows, which is what search (segment_fts), the chat tool (llm/chunking.py:85) and the JSON export read; the transcript view and the text exports are built from word rows and show it once. Needs media longer than WINDOW_SECONDS, because the last window has no look-ahead and limit is then None.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A segment with no words is trimmed at the cut like any other: its end never exceeds the limit
- [x] #2 Its text no longer describes audio past the cut
- [x] #3 Segments that do carry words keep their current behaviour
- [x] #4 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_stage_transcribe_windows.py met Seg(8.0, 14.0, text) zonder woorden en limit=10.0.
2. Fix in scribe/stages/transcribe.py: de trim mag niet op de aanwezigheid van woorden hangen; een segment zonder woorden dat voorbij de grens eindigt wordt afgekapt op limit.
3. Draai tests/test_stage_transcribe_windows.py en tests/test_stage_transcribe.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/stages/transcribe.py: naast de twee bestaande trims een tak voor een segment zonder woorden dat voorbij de grens eindigt. Dat segment wordt niet meegenomen (break) in plaats van afgekapt. Reden: er is geen woordlijst om een kortere tekst uit op te bouwen, en een verzonnen tekst zou een tijdvak beschrijven dat niemand zo heeft getranscribeerd. Het volgende venster decodeert dit stuk audio toch opnieuw, mét woorden.

Rood: assert 14.0 <= 10.0 - het segment hield de end en de tekst die de decoder gaf, tot in de vooruitkijk.
Groen: tests/test_stage_transcribe_windows.py 28 passed, tests/test_stage_transcribe.py 66 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Een segment zonder woorden loopt niet langer de vooruitkijk in, zodat dezelfde spraak niet in twee segmentrijen belandt waar zoeken, de chattool en de JSON-export uit lezen. Geverifieerd met een test die eerst rood was op end 14.0 tegen grens 10.0 en daarna groen, plus 28 + 66 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
