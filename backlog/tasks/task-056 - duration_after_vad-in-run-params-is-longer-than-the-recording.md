---
id: TASK-056
title: duration_after_vad in run params is longer than the recording
status: Done
assignee:
  - '@claude'
created_date: '2026-09-15 17:13'
updated_date: '2026-09-15 20:24'
labels:
  - transcribe
dependencies: []
ordinal: 101000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 146 (media 20) records duration 3866.6 s and duration_after_vad 4030.9 s. scribe/stages/transcribe.py sums faster-whisper per-window duration_after_vad, and its own comment says look-ahead audio is counted in two windows. The value is metadata, but a speech duration longer than the file is wrong on its face and misleads anyone reading the run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The overcount is explained and quantified against run 146 from code and data
- [x] #2 duration_after_vad never exceeds the recording duration, or is replaced by a value whose meaning is documented
- [x] #3 A test pins the value for a multi-window transcription with look-ahead
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Decided 2026-09-15 (workflow finding, confirmed in code):
Cause: each decode is handed its window plus a 30 s look-ahead, and the stage adds faster-whisper's duration_after_vad for that whole input, so speech in every look-ahead counts twice. Replica of run 146: 4030.904 s stored = 3852.216 s speech inside the windows + 178.688 s inside the 6 look-aheads; excess 164.27 s.
1. Keep the key, give it one meaning: seconds of speech faster-whisper's VAD (default VadOptions, as the decode uses) finds in the recording, counted on each window's own audio. Nothing reads it; the JSON export carries it.
2. loudness.speech_chunks(audio) public; _speech and scale_lookahead take optional precomputed chunks, so the count reuses the VAD pass the look-ahead scaling already pays for (no extra pass).
3. transcribe_audio: a window with a look-ahead counts its own chunks; a window without one (last, or single) takes faster-whisper's number, which then has the same definition; a backend without VAD (MLX, info None) records None. mlx_backend reports duration_after_vad None.
4. The 87 inflated historical runs stay as they are (a VAD pass per recording on the live library for a metadata field is not worth it).
5. Red first in tests/test_stage_transcribe_windows.py and test_stage_loudness.py; then the replica on media 20's audio through the new code must give 3852.216 s.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-15 implementation and evidence.
Cause (AC1): transcribe_audio handed each decode its window plus the 30 s look-ahead and added faster-whisper's duration_after_vad for that whole input, so each look-ahead's speech counted twice. Replica of run 146 on media 20's audio (sha256 = media.sha256): old sum 4030.904 s = stored value exactly; speech inside the windows 3852.216 s + inside the 6 look-aheads 178.688 s; duration 3866.6295 s. 87 of 154 runs in the snapshot exceed their duration; left as history.
Fix: the key keeps its name with one meaning - seconds of speech by faster-whisper's default VAD, each window counted on its own audio. loudness.speech_chunks/speech_seconds are public and scale_lookahead/_speech take the chunks, so the count reuses the VAD pass the look-ahead scaling already makes. A window without look-ahead takes faster-whisper's own number; a backend without VAD (MLX) records None, and mlx_backend reports None.
Red before the fix: 30.999999999999996 == 25.0, 0.0 is None, 30.99 is None, 2.4 is None (x2), speech_chunks/speech_seconds missing. Two guards (no speech counts 0, no extra VAD pass) green before and after.
Green: test_stage_transcribe_windows 27, test_stage_loudness 13, test_stage_transcribe_mlx 10, test_stage_transcribe 66, test_stage_transcribe_second_opinion 17, test_pipeline_e2e 51 (1 deselected), test_exports_rich 45. loudness is imported only by the transcribe stage.
Real data: media 20's audio through the shipped loop with a stand-in that computes duration_after_vad exactly as faster-whisper does: new value 3852.216 s (= VAD on each window alone, diff 0.000000), 14.4 s under the duration, 37.8 s CPU (scratch wf-dav/dav_after.py, task056_realdata.txt).
Mutation on a copy of the code (scratch mut056, repo untouched): look-ahead windows add faster-whisper's number again; count the audio _speech hands back; drop the last window's number; a second VAD pass for the loudness; MLX counts zero instead of None - all 5 caught.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
duration_after_vad now means the seconds of speech faster-whisper's VAD finds in the recording, each window counted on its own audio, instead of a sum that counted every look-ahead's speech twice (run 146: 4030.9 s for a 3866.6 s file). The count reuses the VAD pass the look-ahead scaling already makes; MLX, which runs no VAD, records None. Verified: red then green (7 test files), media 20's real audio through the new loop gives 3852.216 s where the old sum gave the stored 4030.904 s, and 5 of 5 mutants on a copy are caught. Historical runs keep their old values.
<!-- SECTION:FINAL_SUMMARY:END -->
