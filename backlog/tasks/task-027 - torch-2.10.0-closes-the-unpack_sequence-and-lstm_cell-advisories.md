---
id: TASK-027
title: torch 2.10.0 closes the unpack_sequence and lstm_cell advisories
status: Done
assignee:
  - '@claude'
created_date: '2026-09-10 20:13'
updated_date: '2026-09-11 20:56'
labels:
  - dependencies
  - security
dependencies: []
references:
  - >-
    docs/adr/ADR-006-pin-the-cuda-stack-and-register-torch-s-dll-directory-before-ctranslate2-loads.md
priority: high
ordinal: 68000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dependabot flags torch 2.8.0 in requirements-gpu.txt and requirements-ml.txt for GHSA-vgrw-7cvw-pwgx (unpack_sequence, medium, fixed 2.9.1) and GHSA-qfhq-4f3w-5fph (lstm_cell, low, fixed 2.10.0). 2.10.0 is the highest torch that keeps CUDA 12 on both platforms without changing how anything is installed: the cu128 index ends at 2.11, PyPI's Linux torch moves to CUDA 13 at 2.11, and CTranslate2 4.8.2 imports cublas64_12.dll. torchaudio 2.10.0 is also the last torchaudio that pins torch exactly. ADR-006 anticipates this: any pin bump must re-run the doctor on real hardware. The doctor's gpu-smoke exercises CTranslate2 only, so the pyannote path needs a real diarized run as well.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 requirements-gpu.txt pins torch and torchaudio 2.10.0+cu128; requirements-ml.txt pins 2.10.0
- [x] #2 torch/lib still carries cublas64_12.dll and cudnn64_9.dll after the install (ADR-006's DLL mechanism intact)
- [x] #3 python -m scribe.doctor exits 0 with gpu-runtime and gpu-smoke green on the RTX 3080
- [x] #4 A real recording transcribes and diarizes through the app on torch 2.10.0
- [x] #5 The suite passes in halves, and the -m gpu tests pass
- [x] #6 Dependabot alerts 1, 2, 5 and 6 close after the push
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Stop the app (no jobs running). 2. Bump both files. 3. Install with the cu128 index. 4. Check torch/lib DLLs. 5. Doctor with model load. 6. Real transcribe+diarize. 7. -m gpu, then the suite in halves. 8. Commit, push, confirm alerts close.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Finalized 2026-09-11 from the evidence on record plus two new checks.
#1 pins: requirements-gpu.txt:135/138 torch and torchaudio 2.10.0+cu128; requirements-ml.txt:116/119 2.10.0 (read from the files).
#2, #3, #5 (Windows half): measured on the RTX 3080 by the session that made the change and recorded in commit d772289 - torch/lib still carries cublas64_12.dll and cudnn64_9.dll; torch 2.10.0+cu128, CUDA 12.8, cuDNN 9.10.2, pip check clean; doctor exit 0 with gpu-smoke 75 words from 30 s; -m gpu 3 passed (pipeline e2e + both diarize tests); suite in halves 1136 + 790 passed. Not re-run here: this session is on a Mac.
#4: a real two-speaker file transcribed and diarized through the app on torch 2.10.0 on an Apple M2 (job 2, community-1 on mps, 2 speakers; details in TASK-020). The CUDA pyannote path on 2.10.0 is covered by the -m gpu pipeline e2e above, which is the pipeline rather than the app.
#5 (macOS half): full suite 1654 passed, 18 skipped after the TASK-020 doctor fix; -m gpu 1 passed (diarize clip on mps), 9 skipped (7 LLM live tests with no provider, 2 need CUDA).
#6: gh api dependabot/alerts on 2026-09-11 - alerts 1, 2, 5, 6 state fixed. Alerts 3 and 7 (torch.jit.script) were dismissed by rvdbreemen on 2026-09-11 as not_used (TASK-028). Alerts 4 and 8 (lightning, CalVer range) are still open; d772289 says they can be dismissed as inaccurate now that main pins 2.6.6 - a human action.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
torch and torchaudio 2.8.0 -> 2.10.0 (+cu128 on Windows), the highest set that keeps CUDA 12 for CTranslate2 4.8.2; closes GHSA-vgrw-7cvw-pwgx and GHSA-qfhq-4f3w-5fph. Verified on the RTX 3080 at the time of the change (d772289: DLLs intact, doctor green, -m gpu 3 passed, suite in halves green), on an Apple M2 (full suite green, a diarized file through the app), and on GitHub: Dependabot alerts 1, 2, 5 and 6 are fixed.
<!-- SECTION:FINAL_SUMMARY:END -->
