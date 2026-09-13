---
id: TASK-027
title: torch 2.10.0 closes the unpack_sequence and lstm_cell advisories
status: Done
assignee: []
created_date: '2026-09-10 20:13'
updated_date: '2026-09-13 12:29'
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
2026-09-10: bumped both files to 2.10.0 (aefcc16, pushed). Windows evidence: torch/lib keeps cublas64_12.dll + cudnn64_9.dll; torch 2.10.0+cu128 CUDA 12.8 cuDNN 9.10.2; pip check clean; doctor exit 0 (gpu-smoke 75 words/30 s, same as 2.8); -m gpu pipeline e2e + both diarize tests 3 passed; suite 1136 + 790 passed. Dependabot alerts only close once the change is on main (the default branch) - main still pins torch 2.8.0 and lightning 2.6.5. Lightning alerts #4/#8 were dismissed as inaccurate and then REOPENED the same hour: main's 2.6.5 is genuinely vulnerable, the CalVer range only becomes a false positive once 2.6.6 is on main. Pending: real transcribe+diarize through the app (jobs 185-194 queued), Linux venv on 2.10, and the path to main.

Verified 2026-09-11. AC4: 10 real recordings transcribed and diarized through the app on torch 2.10.0+cu128 (jobs 185-196: cuda, pyannote community-1, 2-4 speakers each, 7.7-10.0x realtime). AC5: Windows 1136 + 793 and -m gpu 3 passed; Linux venv on torch 2.10.0 (PyPI, pip check clean) 1126 + 776 passed. AC6 open: Dependabot reads main, which still pins torch 2.8.0 and lightning 2.6.5; nothing closes until the pins land there.

AC6 verified 2026-09-13. PR #1 is merged, so main now pins torch==2.10.0 and lightning==2.6.6 (read from origin/main:requirements-ml.txt). Dependabot on the default branch: alerts 1 and 5 (GHSA-vgrw-7cvw-pwgx, unpack_sequence, medium) and 2 and 6 (GHSA-qfhq-4f3w-5fph, lstm_cell, low) are all "fixed" - the four this task set out to close. Alerts 3 and 7 (GHSA-rrmf-rvhw-rf47, torch.jit.script) are dismissed as not_used and belong to TASK-028, which explains why 2.13 is out of reach.

Left open, and not this task's: alerts 4 and 8, both GHSA-qqmf-gpg7-g8gw on lightning, high. They are the CalVer false positive - GitHub's range reads "< 2022.6.15", which 2.6.6 can never satisfy numerically, while 2.6.6 is exactly the release that added the _ALLOWED_INSTANTIATORS allowlist. They can only be closed by a person dismissing them as inaccurate; the push of 2026-09-13 still reported them ("2 vulnerabilities on the default branch").

Robert decided 2026-09-13 to leave alerts 4 and 8 open rather than dismiss them as inaccurate. They stay visible as high on the default branch, which is honest about what GitHub reports even though the installed lightning 2.6.6 carries the fix (the _ALLOWED_INSTANTIATORS allowlist, read in the wheel on 2026-09-10). Nothing further is pending on this task: it is a standing choice, not an open item. If GitHub ever corrects the CalVer range, they close by themselves.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
torch and torchaudio go to 2.10.0 (+cu128 on Windows), which is the ceiling CTranslate2 and the cu128 index allow. Verified on the RTX 3080: torch/lib keeps cublas64_12.dll and cudnn64_9.dll, doctor exit 0 with gpu-smoke green, 10 real recordings transcribed and diarized through the app at 7.7-10.0x realtime, suites in halves plus -m gpu green on Windows and green on Linux. With PR #1 merged, main carries the pins and Dependabot marks alerts 1, 2, 5 and 6 fixed. The two lightning alerts that remain are a CalVer false positive that only a person can dismiss, and the third torch advisory stays visible as TASK-028 rather than being waved away.
<!-- SECTION:FINAL_SUMMARY:END -->
