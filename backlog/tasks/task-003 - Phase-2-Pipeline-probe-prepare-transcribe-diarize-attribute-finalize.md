---
id: TASK-003
title: 'Phase 2: Pipeline (probe, prepare, transcribe, diarize, attribute, finalize)'
status: Done
assignee: []
created_date: '2026-09-01 20:11'
updated_date: '2026-09-02 07:19'
labels: []
dependencies:
  - TASK-002
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Port WHYcast internals onto the Phase-1 runner: media ingest (sha256 content store, hardlink path ingest), ffprobe preflight, ffmpeg normalize with -progress, faster-whisper stage consuming the segment generator for live progress (large-v3-turbo default, large-v3 for translate, hotwords composition), pyannote community-1 with hook progress and return_embeddings (fallback 3.1 per pin rung), word-level max-overlap attribution (port with tests), finalize transaction (words, segments, FTS, embeddings). Detailed plan to be written when Phase 1 interfaces are frozen. Spec sections 2-3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A real audio file enqueued via API ends done with words, segments and FTS rows
- [x] #2 Per-stage progress visible in job row during a real run
- [x] #3 Integration test with tiny model on CPU passes without CUDA
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Phase 2 pipeline complete: 7 of 7 subtasks Done, review verdict SHIP with its two MEDIUMs closed in a follow-up commit (work-dir leak on failed/cancelled jobs, ETA model-key mismatch). 321 tests pass. A real file goes API -> probe -> prepare -> transcribe -> diarize -> attribute -> finalize into words, segments, speakers and a searchable FTS index, verified on GPU. Open item outside the code: accept the pyannote community-1 model terms on huggingface.co so diarization uses the spec's model instead of the assembled fallback.
<!-- SECTION:FINAL_SUMMARY:END -->
