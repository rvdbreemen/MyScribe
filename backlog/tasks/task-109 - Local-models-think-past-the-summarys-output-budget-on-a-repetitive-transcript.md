---
id: TASK-109
title: Local models think past the summary's output budget on a repetitive transcript
status: Done
assignee: []
created_date: 2026-10-06 20:57
updated_date: '2026-10-25T07:43:00Z'
labels:
  - llm
  - bug
dependencies: []
priority: medium
ordinal: 210000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Seen 2026-10-06 while measuring TASK-105.01 on a synthetic Dutch transcript (six sentences repeated about eighty times, scratch library): with the summary's default reasoning on, qwen3.5:4b spent its whole 4000-token answer budget on 14,690 characters of thinking and wrote no answer; gemma4:12b the same (15,045 characters); qwen3.5:4b again with max_output_tokens=16000 (57,139 characters). The app's own error said so clearly (BadResponse, done_reason=length). Not known: whether real recordings trigger it - the transcript was synthetic and repetitive, which may be the cause. With reasoning off both answered in one call.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A real recording (not synthetic) is summarised by both local models with reasoning on, or the failure reproduced on one and the cause named
- [x] #2 If it reproduces: a fix or a documented limit, with the measurement
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): first a short live check: a summary job on a library copy with qwen3.5:4b, job events showing whether the first call or the ADR-029 retry answered; then close.

2026-10-24 live check (Robert's choice): on a fresh library copy (deleted afterwards), a summary job on media 3 ('episode38.mp3', 22:42, English podcast) with qwen3.5:4b ended done in 89 s: 1 chunk, prompt_eval_count 7,035, 332 completion tokens, finish stop, valid JSON summary. The first call was lost to thinking and the ADR-029 think:false retry answered: the output row has hint_sent=True (only the ADR-029 path sets it for summary, whose spec has reasoning_off=False) and the Ollama access log shows two POST /api/chat for this job (1m11s, then 5.2 s). Evidence: scratchpad run-124/evidence-r3-summary-4b.txt, ollama-log-r3.txt. Earlier: gemma4:12b jobs 429-431 and qwen3.5:4b jobs 435-437 (TASK-123).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Overtaken by ADR-029 (0.9.1): an Ollama answer lost entirely to thinking is asked once more with think:false. Shown live for the summary kind on a real recording (qwen3.5:4b, media 3 on a library copy): the first call was lost to thinking, the retry answered, the job ended done. Earlier evidence covers minutes, blog and speakers. No code change in this task.
<!-- SECTION:FINAL_SUMMARY:END -->
