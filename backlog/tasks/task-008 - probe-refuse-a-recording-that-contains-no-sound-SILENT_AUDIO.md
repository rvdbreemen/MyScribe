---
id: TASK-008
title: 'probe: refuse a recording that contains no sound (SILENT_AUDIO)'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-05 20:49'
updated_date: '2026-09-05 20:56'
labels:
  - pipeline
  - bug
dependencies: []
ordinal: 49000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A recording made on 2026-09-05 was 8.3 s of digital silence (peak -91 dBFS, 4335 bytes). probe accepted it, transcribe returned n_words=0, and the job finished 'done' with an empty transcript - a false success. probe should measure loudness (ffmpeg volumedetect) and refuse a file whose peak is below -60 dBFS with its own error code, so the board says what happened and Retry exists once the input device is fixed. Measured cost: 36 min mp3 -> 7.3 s; the scan is capped at the first 30 minutes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 probe emits a loudness event (mean_db, max_db, scanned_seconds) for every media
- [x] #2 a file whose peak over the scanned window is below -60 dBFS fails the job with error_code SILENT_AUDIO and a detail naming the peak and the input device as the thing to check
- [x] #3 a normal recording and a quiet-but-real one (peak -40 dBFS) pass
- [x] #4 the jobs board and job page show SILENT_AUDIO as a readable sentence
- [x] #5 tests cover the parser, the stage on a generated silent file and on a tone, and the error-code mapping
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. probe.measure_loudness: ffmpeg -t 1800 -vn -af volumedetect -f null; parse_volumedetect regex; None never means silence. 2. probe.SilentAudioError (own class, not NotMediaError) raised when max_db < -60 dBFS, detail names the peak, the window and the remedy. 3. runner._ERROR_CODES maps it to SILENT_AUDIO. 4. loudness event emitted for every file. 5. Tests: parser; measure on lavfi anullsrc / sine / sine at -40 dB; stage refuses silent, passes tone and quiet; code mapping; end-to-end via runner.main. 6. Cost measured: 36 min mp3 = 7.3 s; cap keeps a 4 h file at ~6 s.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified: tests/test_stage_probe.py 24 passed (parser, measure on lavfi silence/tone/-30dB, stage refuses silent + passes tone and quiet, code mapping, runner.main end-to-end -> failed SILENT_AUDIO). tests/test_web_jobs.py: board and page show SILENT_AUDIO + 'Check the input device'. Real data: media 5 (the 2026-09-05 recording) -> max -91.0 dB -> SILENT_AUDIO in 0.2 s; media 1 (WHYcast) -> max -3.1 dB -> passes. Cost: 36 min mp3 7.3 s uncapped; cap 1800 s. ffmpeg's sine peaks at -18 dBFS, not -3: the first test assumed a full-scale sine and was corrected to what the tool produces. Suites touched: prepare+urls 75, transcribe+web_jobs 94, probe 24.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
probe measures loudness with ffmpeg volumedetect over the first 30 min, emits a loudness event for every file, and raises SilentAudioError (runner code SILENT_AUDIO) when the peak is under -60 dBFS, with a detail naming the peak, the window and the remedy. Verified by 25 new/updated tests and by running the measurement on the real silent recording (-91 dB -> refused) and a real podcast (-3.1 dB -> passes).
<!-- SECTION:FINAL_SUMMARY:END -->
