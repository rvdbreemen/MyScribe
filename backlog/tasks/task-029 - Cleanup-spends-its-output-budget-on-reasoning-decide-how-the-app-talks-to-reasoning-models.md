---
id: TASK-029
title: >-
  Cleanup spends its output budget on reasoning: decide how the app talks to
  reasoning models
status: Done
assignee: []
created_date: '2026-09-10 20:50'
updated_date: '2026-09-11 12:47'
labels:
  - llm
  - bug
dependencies: []
references:
  - scribe/llm/tasks.py
  - scribe/llm/openai_like.py
  - scribe/llm/ollama.py
priority: medium
ordinal: 70000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The cleanup kind fails with 'no message content (finish_reason=length)' on real transcripts. Measured 2026-09-10 on media 1 (WHYcast ep29, 5 min, clean Dutch, 837 words, one chunk) by sending the exact request the app builds straight to OpenRouter: openrouter/auto served deepseek/deepseek-v4-flash-0731 twice; both answers were good cleanings (97% and 90% of the words, finish 'stop') but spent 22,515 and 21,512 reasoning tokens of 23,918 and 22,589 completion tokens, in 63-84 s, straight through max_tokens=6000. With reasoning {enabled:false} the same model answered in 8.5 s with 1,171 tokens (0 reasoning) and 94% of the words, keeping the [m:ss] and speaker labels. With reasoning {effort:low} it took 7.9 s and 1,417 tokens but dropped the timestamps and labels - a reason not to reach for effort:low. A model that counts reasoning against the cap (gpt-5.6-luna, served for media 12's chunks) hits 'length' instead: media 12 chunk 0 succeeded with 4,946 of 6,000 tokens, leaving ~1,000 for thinking, and the later chunks did not. The earlier conclusion recorded at the concat-budget comment in tasks.py ('the model runs away on this prompt whatever it is given') rested on media 7, whose transcript is a 112-word 'La, la' loop, and on these reasoning exhaustions; it is corrected there. The prompt works. What is open is a design decision: a per-kind reasoning hint in TaskSpec mapped per provider (OpenRouter reasoning.enabled, OpenAI reasoning_effort, Ollama think), and/or cutting concat chunks to a fraction of the output cap so reasoning has room. Only one provider and one model are measured; auto-routing does not reliably hand out the model that failed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A decision is recorded on how cleanup (and other mechanical kinds) limit reasoning, per provider
- [x] #2 Cleanup of media 1 and media 12 through run_task succeeds against openrouter/auto and the local Ollama default, each run's served model, reasoning tokens and ratio reported
- [x] #3 A model that ignores the hint either finishes each chunk inside its cap with measured headroom, or fails before anything is stored; on an endpoint that does not enforce the cap only the hint bounds reasoning, and each row records which happened
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Design by workflow (understand the seam and the provider APIs from primary docs; one design, three critics, a final design), implemented red/green (3c679fd), verified live on a DB copy, reviewed adversarially; follow-ups fixed red/green with the step-0 measurement (c2a5814). Decision recorded as ADR-010 (Proposed).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11: the same failure hit the speakers kind in the startup sweep - job 154 (media 28) 'openrouter answered with no message content (finish_reason=length)' at max_output_tokens 8000, 1 of the first 14 sweep jobs. So the decision covers speakers too, not only cleanup.

Robert 2026-09-11: "Doe alles". Built and measured.
Decision (ADR-010, Proposed): reasoning is a per-kind hint. cleanup sends it (OpenRouter reasoning {enabled:false} via extra_body; OpenAI reasoning_effort "none"; Ollama think:false); speakers, labels and the generative kinds keep the provider default until measured; effort:low is used nowhere. A 400 with the hint on gets one retry without it. Every row records hint_sent, reasoning_tokens, reasoning_chars, upstream. Cleanup chunks are capped at CONCAT_CHUNK_TOKENS=3000. The cloud cleanup cap stays 6,000 (not raised: a runaway would spend up to the new cap, row 15 spent all 12,000 of its cap, and raising helps only models that ignore the hint).
AC2 (live, on a sqlite-backup copy; media 1 and 12 private=0): openrouter/auto served deepseek-v4-flash-0731 (Wafer) for all 8 calls with the hint honoured - 0 reasoning tokens, finish stop; media 1 words kept 0.988 (8.3 s), media 12 7 parts 0.865 (60 s). ollama qwen3.5:4b think:false: media 1 0.914, media 12 11 parts 0.894 and 0.902, no thinking returned. Spend about $0.019.
AC3 REPLACED 2026-09-11, openly: as written ("a model that ignores a reasoning hint still cannot exhaust a chunk's cap on reasoning alone") it cannot hold - openai/gpt-5-mini refuses the hint (HTTP 400, reasoning mandatory), then spent 5,632 of 6,000 tokens reasoning on media 12's largest 3,000-token chunk and ended length, in the one call tried; the part was refused before storage. What holds, and replaces it, is ADR-010's wording.
Step 0 (live, $0.36): at 3,000-token chunks gpt-5.6-luna on Azure finished every answered call stop - no hint 18/18 with at least 3,010 tokens left, hint 19/19 with 0 reasoning; at 6,000 luna left as little as 997 (no hint) and 1,147 (hint). gemini-3.5-flash-lite refused the hint on every call, 0 reasoning reported. CONCAT_CHUNK_TOKENS=3000 kept.
Review findings fixed (c2a5814): cleaned_parts took another model's parts (existing bug, red then green); a cleaning where every part came back verbatim is refused; refused parts and notes name their spend; two tests that did not test; two headroom-script defects; docstrings and ADR-010 corrected to what was measured (claim-by-claim check, 180 claims).
Verification: Windows halves 1218 + 811, Linux 1208 + 794; adr-lint --strict passes (one advisory: ADR-010's two open questions - effort:low and the stamps; the copy-rule option b).
Human-gated and not run: adr accept ADR-010.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Cleanup now asks the model not to reason (per provider), retries once without the hint when a provider refuses it, and records on every row whether the hint was honoured and what the call spent; chunks are capped at 3,000 tokens so a model that ignores the hint has room or fails before storing. Verified live on a library copy (media 1 and 12, openrouter/auto and Ollama: 0 reasoning, gate passed), by a paid step-0 measurement ($0.36) that kept the chunk cap, a gpt-5-mini run that failed loudly and stored nothing, adversarial reviews whose findings were fixed red/green, and suite halves on Windows and Linux. Decision in ADR-010 (Proposed); AC3 was replaced openly because the original could not hold.
<!-- SECTION:FINAL_SUMMARY:END -->
