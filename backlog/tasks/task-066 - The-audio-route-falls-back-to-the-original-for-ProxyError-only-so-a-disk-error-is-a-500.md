---
id: TASK-066
title: >-
  The audio route falls back to the original for ProxyError only, so a disk
  error is a 500
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 15:20'
updated_date: '2026-09-16 15:22'
labels:
  - review-2026-09-16
  - web
dependencies: []
priority: medium
type: bug
ordinal: 111000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16. GET /media/{id}/audio makes the proxy on demand for a container the browser cannot play, and falls back to serving the original when ffmpeg refuses (scribe/web/transcript.py:683). It caught playback.ProxyError only, so a full disk, an unwritable proxy directory or Windows refusing to replace a file another process holds open became a 500 and a player with nothing in it - even though the original was still there and still playable. Same too-narrow except as the proxy stage had (TASK-062).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An OSError from the transcode serves the original with a warning, exactly as a ProxyError does
- [x] #2 The warning names the media and the reason
- [x] #3 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_web_transcript.py met een transcode die PermissionError opwerpt.
2. Fix: except (playback.ProxyError, OSError).
3. Draai tests/test_web_transcript.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/web/transcript.py: except (playback.ProxyError, OSError), dezelfde verbreding als in de proxystap (TASK-062). De terugval bestaat omdat een speler iets moet kunnen afspelen; een volle schijf of een geweigerde hernoeming laat het origineel even afspeelbaar als een ontbrekende codec dat doet.

Rood: PermissionError ontsnapte uit GET /media/{id}/audio.
Groen: tests/test_web_transcript.py 95 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Een schijffout tijdens het maken van de afspeelkopie levert niet langer een 500 met een lege speler, maar het origineel met een waarschuwing in het log. Geverifieerd met een test die eerst rood was op de ontsnapte PermissionError en daarna groen, plus 95 geslaagde tests in dat bestand.
<!-- SECTION:FINAL_SUMMARY:END -->
