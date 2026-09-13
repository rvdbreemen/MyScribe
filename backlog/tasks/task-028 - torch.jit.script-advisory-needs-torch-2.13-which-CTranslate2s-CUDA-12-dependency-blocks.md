---
id: TASK-028
title: >-
  torch.jit.script advisory needs torch 2.13, which CTranslate2's CUDA 12
  dependency blocks
status: Done
assignee: []
created_date: '2026-09-10 20:13'
updated_date: '2026-09-11 06:02'
labels:
  - dependencies
  - security
dependencies: []
references:
  - 'https://github.com/advisories/GHSA-rrmf-rvhw-rf47'
priority: low
ordinal: 69000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
GHSA-rrmf-rvhw-rf47 (torch.jit.script memory corruption, low, local attack only) affects torch <= 2.12.1 and is fixed in 2.13.0. MyScribe cannot take 2.13 yet without a second decision: CTranslate2 4.8.2 imports cublas64_12.dll (CUDA 12); the cu128 index ends at torch 2.11; torch >= 2.13 with CUDA 12 exists only on the cu126 index, which would change ADR-006's chosen option; and PyPI's Linux torch is CUDA 13 from 2.11 on, so requirements-ml.txt would need an index or platform markers too. torchaudio's last release is 2.11.0 (decoupled from torch, no version pin), so any 2.13 set pairs torch 2.13 with torchaudio 2.11. The alert is deliberately left open rather than dismissed, so it stays visible until one of the unblocking routes exists.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reachability of GHSA-rrmf-rvhw-rf47 from MyScribe's real code paths is established with static and runtime evidence
- [x] #2 Dependabot alerts #3 and #7 are resolved with that evidence, and the torch pin stays on the ADR-006 cu128 set
- [x] #3 The task records which torch release carries the fix and when to revisit the pin
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
What the cu126 route costs, checked 2026-09-11 against PyTorch v2.10.0's own build scripts (.ci/manywheel/build_cuda.sh; .ci/pytorch/windows/cuda126.bat and cuda128.bat) and the installed wheel's torch.cuda.get_arch_list(). cu126 builds carry sm_50/60(/61 on Windows)/70/75/80/86/90 kernels and only compute_90 PTX beyond that - no sm_100 or sm_120, so no native Blackwell (RTX 50-series) kernels. cu128 (what ADR-006 pins) carries 70/75/80/86/90/100/120 and drops Maxwell/Pascal. This machine (RTX 3080, sm_86) is in both lists, so here the move would change nothing measurable; the trade-off is for other machines: cu126 buys Maxwell/Pascal and loses Blackwell. Whether compute_90 PTX JIT-compiles usably on Blackwell was not tested - there is no Blackwell card here. This is the fact a successor to ADR-006 would have to weigh; the decision is a human's.

REPLACED ACCEPTANCE CRITERIA 2026-09-11, and why. The original three (CTranslate2 CUDA 13 or a cu126 successor to ADR-006; the new set through doctor/real run/gpu tests; alerts 3 and 7 closed by the upgrade) rested on GitHub's "patched in 2.13.0". That is wrong: the fix, pytorch/pytorch b90c949 "[JIT] Reject bare list/tuple value annotations" (#188779), is not in v2.13.0 (gh api compare v2.13.0...b90c949: diverged, ahead_by=988) and first ships in v2.14.0 (compare: behind, i.e. contained). A 2.13 bump would have closed the alert by version range and shipped the bug.
Reachability, measured by a four-angle workflow (advisory, own code, dependencies, runtime trace) plus adversarial refuters: the bug is in the TorchScript source compiler and is triggered by Python source handed to torch.jit.script (bare list/tuple annotations). scribe/ has no torch.jit or torch.compile use. A runtime trace with wrappers on 17 script/trace/load entry points and sys.monitoring, over import, the community-1 pipeline, inference and a faster-whisper transcribe, recorded 0 compiles/traces/loads (positive controls fired). Near misses that do not run: asteroid_filterbanks scripting.py:39 (gated by is_tracing(), always False), lightning to_torchscript (export API, never called), torch.distributed.optim scripted classes (never imported), the torch.load -> torch.jit.load dispatch (stock checkpoints are not TorchScript zips).
Decision: stay on ADR-006's cu128 set with torch 2.10.0; alerts #3 and #7 dismissed as not_used with that evidence in the comment. Revisit when torch >= 2.14 exists for CUDA 12 on a supported index, or CTranslate2 ships a CUDA 13 build.
Side finding, filed separately: a job's params (diarization_model) are not validated at the ingest and retry doors, so a local client can make pyannote open an arbitrary pipeline - see the new task.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Not reachable and not fixed where GitHub says: the fix is in torch 2.14.0, not 2.13.0. MyScribe never compiles TorchScript (runtime trace: 0 calls), so alerts #3 and #7 were dismissed as not_used and the pin stays on ADR-006's cu128 set. Revisit at torch >= 2.14 on CUDA 12 or CTranslate2 on CUDA 13.
<!-- SECTION:FINAL_SUMMARY:END -->
