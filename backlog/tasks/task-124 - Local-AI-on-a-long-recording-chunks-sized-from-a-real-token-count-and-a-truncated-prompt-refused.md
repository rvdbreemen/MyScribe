---
id: TASK-124
title: >-
  Local AI on a long recording: chunks sized from a real token count, and a
  truncated prompt refused
status: To Do
assignee: []
created_date: '2026-10-22T23:09:00Z'
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
- [ ] #1 A red test reproduces the truncation signature and the under-estimate; the fix turns it green
- [ ] #2 On a copy of the library, minutes and blog on media 61 with qwen3.5:4b read every chunk whole (prompt_eval_count matches the plan) and finish
- [ ] #3 A note that fills the window says so, not that it hit the cap
<!-- AC:END -->
