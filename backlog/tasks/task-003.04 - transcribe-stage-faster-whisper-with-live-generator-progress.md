---
id: TASK-003.04
title: 'transcribe stage: faster-whisper with live generator progress'
status: Done
assignee: []
created_date: '2026-09-02 02:32'
updated_date: '2026-09-02 20:16'
labels: []
dependencies: []
parent_task_id: TASK-003
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 Task 4. resolve_model (turbo cannot translate -> large-v3 with a recorded note), compose_hotwords (glossary first, 223-token cap), transcribe_audio consuming the generator lazily for segment.end/info.duration progress and checking cancelled() between segments, run row plus words and segments persisted with confidence fields.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 resolve_model substitutes large-v3 for translate and records why
- [x] #2 compose_hotwords respects the token cap, glossary first
- [x] #3 clip30.wav on tiny/CPU yields >10 words with probabilities and monotonic timestamps
- [x] #4 Cancelling mid-stream stops early
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Field bug 2026-09-02: a 40-minute interview (958 MB, 2443 s) died in faster-whisper's feature extractor with 'Unable to allocate 743 MiB' for a (1, 242298, 201) complex128 STFT - the whole file's spectrogram in one array, on a machine at 95% commit. Fixed in 10221ca: the stage feeds Whisper ten-minute windows read from the wav, cut at the quietest 100 ms before each boundary, offsets and indices carried across, language pinned after the first window. Evidence: 10 new tests (tests/test_stage_transcribe_windows.py), 499+414 green in halves, and the same interview re-run end to end on the RTX 3080: done in 500 s, 6502 words, 635 segments, 2 speakers, xRT 4.9, coverage 0-2438.8 s of 2443.3 s, no abnormal word gap at any 600 s boundary (largest gap in the file 2.72 s, at 1928 s), runner child peak RSS 3.9 GB total.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
transcribe stage: resolve_model with the turbo/translate substitution recorded, compose_hotwords under a 223-token cap with glossary first, lazy generator consumption for live progress, cancel between segments, run row plus words and segments with confidence fields. 57 tests; verifier mutation-tested the substitution and hotword wiring (3 and 2 tests caught). Commits e9d5257, 602244b.
<!-- SECTION:FINAL_SUMMARY:END -->
