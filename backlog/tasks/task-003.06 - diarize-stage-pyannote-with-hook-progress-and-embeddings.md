---
id: TASK-003.06
title: 'diarize stage: pyannote with hook progress and embeddings'
status: Done
assignee: []
created_date: '2026-09-02 02:32'
updated_date: '2026-09-02 14:14'
labels: []
dependencies: []
parent_task_id: TASK-003
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 2 Task 6. load_pipeline preferring local weights over the gated HF id (WeightsUnavailable with a hint otherwise), ProgressHook mapping pyannote steps to a 0..1 fraction, diarize returning turns plus mean embeddings (return_embeddings=True), VRAM freed after the stage.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ProgressHook yields non-decreasing fractions ending at 1.0 and tolerates unknown steps
- [x] #2 load_pipeline raises WeightsUnavailable with a hint when both sources are missing
- [ ] #3 GPU-marked test: clip30.wav yields at least one turn and one embedding per cluster
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC 3 (GPU run on clip30.wav) is met against segmentation-3.0 + wespeaker, which this account can reach, NOT against speaker-diarization-community-1: that repo returns GatedRepoError 403 for the HF token in .env (token valid, model terms not yet accepted on huggingface.co). Robert to accept the model conditions; until then the assembled pipeline is what runs.

Follow-up 5da7615: load_pipeline_with_source now falls back to speaker-diarization-3.1 assembled from segmentation-3.0 + wespeaker when community-1 is gated for the token, records the source and a plain-language note on the run, and flags fallback=true in the diarize event. Verified live: the job that failed at diarize now completes (761 words, 4 clusters, 63 turns, 57 s on the 3080). Accepting the community-1 terms on hf.co remains the way to get the better model.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
diarize stage: load_pipeline preferring local weights, ProgressHook mapping pyannote steps to a monotone 0..1 fraction, waveform-dict input to bypass the torchcodec/FFmpeg 8.1 [WinError 127] break, return_embeddings persisted, stale embeddings cleared before rewrite (f1ab330), VRAM freed after the stage. GPU acceptance proven on reachable weights (421ad35); community-1 blocked on gated access, see notes. Commit c8798fe.
<!-- SECTION:FINAL_SUMMARY:END -->
