---
id: TASK-001
title: Design the local transcription app
status: Done
assignee:
  - '@robert'
created_date: '2026-09-01 18:29'
updated_date: '2026-09-05 10:25'
labels: []
dependencies: []
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Produce an approved design document for a local-first transcription app: upload or record audio/video, transcribe with faster-whisper on CUDA, diarize with pyannote 3.1, browse and export transcripts, and watch job/pipeline status in a web UI. WHYcast-transcribe is the source of ported code, not a dependency. Decisions already locked with the user: new standalone app in this repo (port code, do not refactor WHYcast); single user on localhost with no auth; transcript UI is read/search/export plus speaker renaming and reassignment; v1 includes folders, URL import via yt-dlp, browser microphone recording, and translate-to-English; the LLM post-processing step is pluggable across Ollama, OpenAI, and OpenRouter. Target hardware is one RTX 3080 Laptop with 16 GB VRAM, which forces GPU stages to run serialized.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Design document exists under docs/superpowers/specs/ and is committed
- [x] #2 Document names the pipeline stages and the job state machine explicitly
- [x] #3 Document states how the single GPU is shared and how queue position is shown to the user
- [x] #4 Document lists which WHYcast modules are ported and which are replaced
- [x] #5 Document names what is deliberately cut from v1 and why
- [x] #6 User has reviewed and approved the document
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Spec written and committed (2dd570a) after three research passes: a feature-parity inventory of the hosted transcription services (198 features), the open-source landscape (51 candidates, 17 verified, verdict: build), and five named projects deep-profiled. Key measurements on the 3080: model load 17-19s, ~5GB VRAM fp16, 30s clip in 12.4s GPU-verified. AC 6 (user review of the spec file) remains open.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Design spec written, self-reviewed and committed as docs/superpowers/specs/2026-09-01-myscribe-design.md (commit 2dd570a). All nine design sections approved by Robert in-chat in three parts; final approval given with 'bouw het' on 2026-09-01. Phase 1 implementation plan derived at docs/superpowers/plans/2026-09-01-phase1-spine.md.
<!-- SECTION:FINAL_SUMMARY:END -->
