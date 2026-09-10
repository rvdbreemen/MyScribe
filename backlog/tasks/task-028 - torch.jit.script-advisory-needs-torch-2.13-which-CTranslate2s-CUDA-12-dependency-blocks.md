---
id: TASK-028
title: >-
  torch.jit.script advisory needs torch 2.13, which CTranslate2's CUDA 12
  dependency blocks
status: To Do
assignee: []
created_date: '2026-09-10 20:13'
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
- [ ] #1 Either CTranslate2 ships a CUDA 13 build and the stack moves to torch >= 2.13 on a CUDA 13 index, or a Proposed successor to ADR-006 moving to cu126 is accepted by a human and implemented
- [ ] #2 The chosen set passes the doctor, a real transcribe+diarize run, and the -m gpu tests on real hardware
- [ ] #3 Dependabot alerts 3 and 7 (GHSA-rrmf-rvhw-rf47) close
<!-- AC:END -->
