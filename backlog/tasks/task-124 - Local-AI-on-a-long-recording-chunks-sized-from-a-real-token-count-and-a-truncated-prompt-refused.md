---
id: TASK-124
title: >-
  Local AI on a long recording: chunks sized from a real token count, and a
  truncated prompt refused
status: Done
assignee: []
created_date: '2026-10-22T23:09:00Z'
updated_date: '2026-10-25T13:38:00Z'
labels:
  - llm
  - ollama
  - bug
dependencies: []
priority: high
ordinal: 238000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Measured 2026-10-23 on a copy of Robert's library, media 61 (2h28m, Dutch), qwen3.5:4b, with the Ollama window guards briefly comparing against context_tokens (32768) instead of num_ctx (8192) - a change that was then reverted from PR #85. (1) The planner's chunking.estimate_tokens counts ceil(len(word)/3) per word; the real count is higher: a blog note of 81,018 characters read 32,016 prompt tokens (2.53 characters a token), and every transcript line's [m:ss] stamp splits into several tokens. (2) Two minutes notes of 87,180 and 89,374 characters both came back with prompt_eval_count 16386 = 32768/2 + 2, the daemon's documented truncation signature: the front half of each chunk was dropped, HTTP 200, and the job ended done on half a transcript. Neither the word-floor pre-check (about 15,000 words < 32768) nor the post-hoc check (16386 < 32768) can see that. (3) The blog note then ran out of window, not of its cap: 32,016 + 752 = 32,768 tokens, and the message said 'stopped at the 4000-token cap'. With the guards comparing against 8192, as in 0.9.0 and 0.9.1, such prompts are refused outright (ContextTooLong): honest, but local AI cannot take a long recording. Wanted: chunks planned from a count that holds for these tokenizers, a check that refuses a prompt the daemon truncated, and a message that tells a full window from a spent cap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A red test reproduces the truncation signature and the under-estimate; the fix turns it green
- [x] #2 On a copy of the library, minutes and blog on media 61 with qwen3.5:4b read every chunk whole (prompt_eval_count matches the plan) and finish
- [x] #3 A note that fills the window says so, not that it hit the cap
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): AC2 blog: first run blog on qwen3.5:9b (no code change) to tell a 4B limit from a prompt problem. Record ADR-031: Ollama is asked never to truncate; the token estimate is calibrated against measured counts.

2026-10-25 Robert's decision (asked with options in the session): minutes gets a note rule like blog's (summarise, do not quote; PROMPT_VERSION up) with a live rerun incl. the format-constrained combine. The combine 'no reasoning' hint applies to Ollama only (measured there 3/3 done); OpenRouter keeps ADR-030's limit; recorded in a new ADR-035. ADR-031 and ADR-034 accepted.

2026-10-25 Robert's decision: minutes on qwen3.5:4b (432 done with null clocks, 433 combine at the cap) -> first test minutes on qwen3.5:9b before choosing a fix. action_items and chapters keep the note rule, with one live run each.

2026-10-25 PR #101 merged, CI green on three OSes. AC2 closed on Robert's choice 'close + follow-up' (TASK-126): every chunk read whole in every live run (prompt_eval_count 0.83-0.86 of plan, never the 16386 halving signature, truncate:false makes the daemon refuse instead of halving); blog done on qwen3.5:4b (3/3 with the Ollama-only combine hint); minutes done on qwen3.5:9b twice with 3/3 real timestamps; minutes on 4b done once without timestamps and once cut at the combine cap - recorded as a known limit in CHANGELOG. ADR-031, ADR-034, ADR-035 accepted by Robert. Evidence: scratchpad run-124/r3..r8.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Local AI on long recordings: chunks are planned from a deliberately pessimistic estimate calibrated on measured token counts, Ollama is asked never to truncate (and a halved prompt is refused), a full window is told from a spent cap, notes summarise instead of copying (blog, minutes, action items, chapters), blog's combine asks a local model for no reasoning, and Ollama answers follow the kind's JSON schema. PR #101. Known limit: a 4B model may give minutes without timestamps; follow-up TASK-126.
<!-- SECTION:FINAL_SUMMARY:END -->
