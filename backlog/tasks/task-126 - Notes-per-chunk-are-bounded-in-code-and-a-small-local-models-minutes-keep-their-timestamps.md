---
id: TASK-126
title: >-
  Notes per chunk are bounded in code, and a small local model's minutes keep
  their timestamps
status: Done
assignee:
  - '@claude'
created_date: '2026-10-25T12:36:00Z'
updated_date: '2026-10-25T21:29:00Z'
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
- [x] #1 A note longer than the bound is cut or refused in code with a sentence; a red test shows an 85-note chunk bounded
- [x] #2 Chapters on media 61 with qwen3.5:4b on a library copy finishes, every chunk read to its end
- [x] #3 Robert chose how 4B timestamps are handled, and minutes on 4b shows the chosen behaviour live
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-25 Robert's decision for AC3 (asked with three options): a documented limit - no behaviour change; Settings/docs say that minutes and action items with timestamps need qwen3.5:9b or larger on a local model.

2026-10-25 PR #110 merged, CI green on three OSes. ADR-040 accepted by Robert. AC1: notes over 30 points or cut off are asked once more, then thinned to 30 points spread over the part, each stamped point keeping its stamp (15 mutants killed, red-first tests). AC2: chapters on media 61 with qwen3.5:4b done in 408 s (TASK-124 r8 baseline failed), every chunk's notes reach its end (2-11% left). AC3 (Robert: documented limit): sentence in Settings under the Ollama model pick, docs/configuration.md and CHANGELOG; live minutes on 4b done in 483 s with null timestamps, as documented. New finding: the 4B chapters combine draws only on chunk 0 -> TASK-127.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Notes per chunk are bounded in code (asked once more, then thinned with stamps kept; ADR-040), chapters and minutes now finish on qwen3.5:4b on a 2h28m recording, and the 4B timestamp limit is documented where a local model is chosen. PR #110. Follow-up: TASK-127.
<!-- SECTION:FINAL_SUMMARY:END -->
