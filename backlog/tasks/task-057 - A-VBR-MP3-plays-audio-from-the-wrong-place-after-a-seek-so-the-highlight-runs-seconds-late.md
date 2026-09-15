---
id: TASK-057
title: >-
  A VBR MP3 plays audio from the wrong place after a seek, so the highlight runs
  seconds late
status: Done
assignee:
  - '@claude'
created_date: '2026-09-15 18:05'
updated_date: '2026-09-15 19:03'
labels:
  - transcript
  - playback
  - bug
dependencies: []
ordinal: 102000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert saw the follow-along highlight lag behind the sound on some recordings, worst in Firefox. Measured on 2026-09-15 with screen capture plus WASAPI loopback of the laptop speakers against the MP3 envelope (scratchpad screen_sync.py): media 59 (The history of Paul Wouters) is a VBR MP3 with a Xing TOC. From the start it is in sync in both browsers. After a seek the browser plays audio from a different place than currentTime reports: Firefox 155 +2961 ms late after a seek to 900 s and +3724 ms after 1700 s; Chrome 152 +358 ms after 900 s. The word times are correct (forced alignment over the file: Whisper starts 65-99 ms early, flat). A CBR MP3 (media 20) does not do it (+50 ms after seeks). Serving media 59 as the AAC proxy the app already makes for other containers (same ffmpeg arguments) measured -10/-14/+13 ms in Firefox. 31 of the 52 MP3s in the library are VBR (Xing).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A VBR MP3 is played through a proxy whose seeking is exact, and a CBR MP3 is still served as the original
- [x] #2 The HTML export makes the same choice as the player
- [x] #3 After a seek, the highlight in Firefox and Chrome is within 100 ms of the sound on a VBR recording, measured the same way as the bug
- [x] #4 Red then green in tests
- [x] #5 New media get their proxy in the transcribe pipeline, and a failed proxy does not fail the transcription
- [x] #6 One command makes the missing proxies of the existing library, with a dry run that lists them first
- [x] #7 When a recording plays from an original that does not seek exactly, the page says so under the player
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Decided with Robert 2026-09-15: a VBR MP3 (or a download that is one) plays as AAC, so playback is always a format that seeks exactly. The original stays in the store untouched; the AAC sits beside it as the proxy under MEDIA_DIR/proxy.
1. scribe/playback.py (new, no web imports so the runner child can use it): the proxy code moves here from scribe/web/transcript.py (PLAYABLE, transcode, probe_duration, durations_agree, ensure_proxy, ProxyError), plus one rule, seeks_exactly(path): wav/m4a/mp4/ogg/opus/flac/webm yes; an MP3 only when its first frame carries the 'Info' (CBR) tag; Xing, VBRI or no tag no; raw ADTS .aac no (no seek index; reasoned, not measured - the library holds none).
2. The audio route: exact original -> original; else proxy on disk -> proxy; else a playable original (VBR MP3, .aac) -> the original at once, no 80 s transcode in the request, and the page says under the player that a jump can run the highlight late; an unplayable container keeps today's transcode-on-request.
3. The HTML export uses the same rule and makes a missing proxy, as it does for an MKV today.
4. New media: a 'playback' stage right after prepare in the transcribe pipeline makes the proxy (no-op for an exact file). A failed proxy is logged and does not fail the transcription.
5. Existing media: python -m scribe.playback [--dry-run] makes every missing proxy; Robert runs it in his own terminal (31 VBR MP3s, about 45 min CPU).
6. Red then green; then the screen+loopback measurement on media 59 through the new code on a copy of the library.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-15 implementation.
- scribe/playback.py (new, no web imports): the proxy code moved from scribe/web/transcript.py, plus seeks_exactly (MP3: 'Info' tag in the first frame after ID3 and side info, CRC-aware; wav/m4a/mp4/ogg/opus/flac/webm by suffix; .aac and unplayable containers no) and source(original, sha256).
- Audio route and HTML export use it. A VBR MP3 without a proxy plays the original at once; transcript.html shows a hint under the player (audio_inexact). An unplayable container still gets its proxy in the request.
- scribe/stages/proxy.py: stage 'proxy' after prepare; a ProxyError is logged (log + applog proxy.failed) and does not fail the job.
- python -m scribe.proxies [--dry-run] (scribe/proxies/__main__.py loads .env before scribe.paths, like scribe.export).
Evidence:
- Red before the code: 4 real assertion failures (route served the VBR original; no hint; export carried audio/mpeg and never asked for a proxy), test_playback red on the missing module.
- Green: test_playback 35, test_web_transcript 89, test_web_exports 52, test_exports_cli 26, test_glossary 69, test_stage_transcribe 66, test_stage_prepare 27, test_runner 13, test_web_jobs 42, test_web_library 77, test_pipeline_e2e 51 (1 deselected). Stage-list expectations in glossary/e2e/stage_transcribe updated for 'proxy'.
- The rule over the live library (read-only): 31 MP3s need a proxy (same 31 as the header search found), 21 CBR MP3s exact, 2 MKVs without proxy (ids 6, 8), 4 MKVs with one.
- Real run on a copy (scratch fix057): runner with probe/prepare/proxy: media 20 (CBR) proxy 0.0 s, none made; media 59 (VBR, 39 min) proxy 69.2 s, 28.4 MB, duration diff 0.4 ms. Dry run listed 3 and 59, then 3; the command made media 3 in 37 s; a second run: nothing to do.
- App on 4299 over the copy: /media/59/audio audio/mp4, /media/20 audio/mpeg no hint, /media/3 (proxy set aside) audio/mpeg with hint; screenshot scratch task057_notice_media3.png.
- adr-judge 0.57.0 over the code diff: 0 violations, 0 advisory (7 accepted ADRs, LLM pass).

Measurement through the new code (AC3), same rig as the bug (scratch screen_sync.py, WASAPI loopback of the Realtek laptop speakers + screen capture), app on 4299 over the library copy serving media 59 as the pipeline-made proxy (audio/mp4). Total = highlight on screen minus word start at the speaker, median [IQR]:
- Firefox 155: from start -0 ms [-8, +17] n=40; after seek to 900 s -13 ms [-23, -3] n=76; after seek to 1700 s at 0.75x +10 ms [-2, +33] n=63. Before (VBR original): +2961 and +3724 ms.
- Chrome: from start -38 ms [-51, -26] n=34; after 900 s -30 ms [-39, -21] n=71; after 1700 s at 0.75x +14 ms [+3, +19] n=61. Before: +358 ms after 900 s.
Output in scratch task057_measure.txt.

Whole suite, one file at a time (socketpair stall): 70 files passed, 0 failed; test_llm_live 7 deselected (live marker), GPU tests deselected by default. Output scratch task057_suite.txt.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A VBR MP3 now plays from the AAC proxy, so a seek lands where the player's clock says. scribe/playback.py holds the rule (seeks_exactly: an MP3 only with an 'Info' tag in its first frame) and the proxy code moved out of the web layer; the audio route, the HTML export and a new 'proxy' pipeline stage after prepare all use it, and python -m scribe.proxies [--dry-run] fills in the existing library. A VBR original without its proxy plays at once with a hint under the player. Verified: red then green (4 route/page/export assertions, new test_playback 35), whole suite green per file; real run on a library copy (proxy stage 69 s for media 59, 0.0 s for a CBR file, command made the rest, then nothing to do); Firefox -13/+10 ms and Chrome -30/+14 ms after seeks where the bug measured +2961/+3724 and +358 ms; adr-judge 0 violations. Robert runs python -m scribe.proxies on the live library: 31 VBR MP3s and 2 MKVs.
<!-- SECTION:FINAL_SUMMARY:END -->
