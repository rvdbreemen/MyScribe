---
id: TASK-109
title: Local models think past the summary's output budget on a repetitive transcript
status: To Do
assignee: []
created_date: '2026-10-06 20:57'
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
- [ ] #1 A real recording (not synthetic) is summarised by both local models with reasoning on, or the failure reproduced on one and the cause named
- [ ] #2 If it reproduces: a fix or a documented limit, with the measurement
<!-- AC:END -->
