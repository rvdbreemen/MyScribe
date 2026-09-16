---
id: TASK-062
title: 'The proxy stage promises its failure is not the job''s, but only for ProxyError'
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
ordinal: 107000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). scribe/stages/proxy.py:50 catches playback.ProxyError only. Its own docstring (:14-17) promises that a proxy ffmpeg cannot make is logged and the job goes on, because the words do not depend on the proxy. An OSError from the same call - a full disk, a directory it may not write, Windows refusing to replace a file another process holds open while the web process makes the same proxy for an export - walks out of the stage instead, and runner.py:278 turns it into a failed transcription.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An OSError from ensure_proxy is logged like a ProxyError and the stage returns normally
- [x] #2 The log line and the applog record still name the media and the reason
- [x] #3 A ProxyError keeps behaving exactly as before
- [x] #4 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_pipeline_e2e.py: proxy.run met een ensure_proxy die PermissionError opwerpt.
2. Fix in scribe/stages/proxy.py: except (playback.ProxyError, OSError).
3. Draai tests/test_pipeline_e2e.py en tests/test_playback.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/stages/proxy.py: except (playback.ProxyError, OSError). De stap belooft in zijn eigen docstring (regels 14-17) dat zijn falen niet dat van de taak is, en een volle schijf, een map waarin niet geschreven mag worden of Windows dat weigert een bestand te vervangen dat een ander proces openhoudt, zijn falen van precies dezelfde soort: de woorden hangen niet van de proxy af.

Rood: PermissionError ontsnapte uit proxy.run.
Groen: gerichte test passed; tests/test_pipeline_e2e.py 53 passed eerder in deze ronde, tests/test_playback.py meegedraaid als regressie.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
De proxystap vangt nu ook een schijffout, zodat een kopie die alleen het afspelen dient geen transcriptie meer laat mislukken. Geverifieerd met een test die eerst rood was op een ontsnapte PermissionError en daarna groen.
<!-- SECTION:FINAL_SUMMARY:END -->
