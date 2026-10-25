---
id: TASK-126
title: >-
  Notes per chunk are bounded in code, and a small local model's minutes keep
  their timestamps
status: To Do
assignee: []
created_date: '2026-10-25T12:36:00Z'
labels:
  - llm
  - ollama
dependencies: []
priority: medium
ordinal: 242000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up of TASK-124 (Robert, 2026-10-25). Measured on media 61 (2h28m Dutch) on a library copy: the note instruction "at most 30 points" is a request models do not keep - qwen3.5:9b wrote 85/32/37 notes on minutes chunks (and copied 44 of 77 lines on a small-talk chunk), qwen3.5:4b wrote ~53 fragment notes on chapters chunk 0 and hit the 4000-token note cap halfway through the chunk. Separately, qwen3.5:4b gives no timestamps (evidence_ts null) on minutes and action items, every call first lost to thinking then answered by ADR-029's think:false retry; qwen3.5:9b gave real clocks. Evidence: session scratchpad run-124/r7, r8 (live-summary.txt). Wanted: the note count enforced in code (not only asked), so a chunk's notes cannot outgrow the combine, and a decision on 4B timestamps (thinking on for the combine, a different model, or documented limit).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A note longer than the bound is cut or refused in code with a sentence; a red test shows an 85-note chunk bounded
- [ ] #2 Chapters on media 61 with qwen3.5:4b on a library copy finishes, every chunk read to its end
- [ ] #3 Robert chose how 4B timestamps are handled, and minutes on 4b shows the chosen behaviour live
<!-- AC:END -->
