---
id: TASK-075
title: >-
  GET /media/{id}/audio runs ffmpeg and writes a file, contradicting the guard's
  stated rule
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:10'
updated_date: '2026-09-16 17:18'
labels:
  - review-2026-09-16
  - web
  - playback
dependencies: []
priority: low
type: bug
ordinal: 120000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low, and understated for one sub-case). scribe/guard.py says GETs are not guarded because 'reading changes nothing', and GET /media/{id}/audio makes a proxy with ffmpeg and writes it under MEDIA_DIR/proxy when a container a browser cannot play has none - the fallback for a library that predates the proxy stage. That is one transcode per recording, kept, and fine. The sub-case is not: a proxy the 50 ms duration check refuses is unlinked, so nothing remembers the refusal and the next GET - every page load, every seek that re-requests the source, and a cross-site page that embeds the address - runs the whole transcode again (80 s for 39 minutes). The verifier judged that sub-case understated.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A proxy the duration check refused is remembered next to the proxy path, and the next ensure_proxy for it raises the same reason without transcoding
- [x] #2 The audio route serves the original on the second GET without running ffmpeg again
- [x] #3 python -m scribe.proxies is the explicit retry: it forgets the refusal and transcodes again
- [x] #4 guard.py names the one GET that does work and the bound on it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_playback - a duration-refused proxy is remembered and the next ensure_proxy raises the same reason without transcoding; python -m scribe.proxies forgets the refusal and transcodes again. test_web_transcript - the second GET of a drift-refused audio serves the original without a second transcode.
2. Green: playback.refusal_path_for(proxy) = <proxy>.refused; ensure_proxy writes the reason there when the duration check fails and raises from it on the next call (only the duration refusal is remembered - an ffmpeg or disk failure is not a property of the file); main() unlinks it before trying, because the command is the retry. guard.py names the one GET that does work and its bound; the audio route's docstring says the same.
3. Run test_playback, test_web_transcript, test_guard, test_exports_cli one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_a_refused_proxy_is_remembered_and_not_transcoded_again failed (transcode ran twice); the broken-transcode and backfill-retry tests passed before the change and pin those paths; the route test could not run red on the first attempt (the Windows socketpair stall, 240 s) and passed green on the rerun. Green: test_playback 38, test_web_transcript 98, test_guard 11, test_exports_cli 26. AC #4 is the docstring diff in scribe/guard.py and scribe/web/transcript.py. The existing drift test's 'nothing bad is kept' assertion now allows the .refused note and still forbids a kept proxy.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
playback.ensure_proxy keeps a duration refusal beside the proxy path (<proxy>.refused) and raises from it on the next call without transcoding; ffmpeg and disk failures are still tried again; python -m scribe.proxies forgets the note first, so it is the retry the page promises; guard.py and the audio route say which GET does work and its bound. Verified red-to-green by four tests, 173 tests across four files.
<!-- SECTION:FINAL_SUMMARY:END -->
