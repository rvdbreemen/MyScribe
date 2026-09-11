---
id: TASK-037
title: >-
  A re-transcription asks an external model again for speakers a pass already
  failed to name
status: To Do
assignee: []
created_date: '2026-09-11 17:33'
labels:
  - speakers
  - llm
  - privacy
dependencies: []
ordinal: 78000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-11 by the verification of the 43 re-transcriptions. finalize.queue_speaker_pass (TASK-024) queues a speakers pass whenever a cluster of the new run is unnamed after inheritance (TASK-031) - even when a speakers answer is already stored for the recording. The startup catch-up (1d6cf02) follows Robert's rule the other way: run the pass for a file only if it never ran before, and never for a private file; one stored answer is enough, "because asking again is a decision". The two paths disagree.

Measured: the 10 passes after the batch (jobs 249-258, llm_output 65-74) cost 167,081 prompt and 15,972 completion tokens and named 2 clusters (media 3 SPEAKER_01 "Ad", media 31 SPEAKER_02 "Josh Bressers"); 8 clusters stay unnamed. openrouter/auto routed 9 to gpt-5.6-luna and 1 to deepseek-v4-flash, and three clusters got a different answer on unchanged words when the model changed. Human names are never overwritten. As of 2026-09-11 every one of the 25 media TASK-035 re-transcribes already has a stored pass; four of them (13, 20, 36, 39) carry an unnamed cluster, so their re-transcription queues a pass again. 0 media are private, so no private words went out.

Whether the finalize path should follow the catch-up rule is Robert's decision; this task holds the question and the numbers.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Robert's decision is recorded: does a re-transcription of a recording with a stored speakers answer ask again by itself?
- [ ] #2 If not: the finalize path skips the pass when a speakers answer is stored for the recording (any run), a person can still ask from the transcript, and a red/green test holds both
- [ ] #3 A recording whose pass never ran still gets one after its transcription, and a private recording never gets an external one (TASK-024 and the catch-up unchanged for those cases)
<!-- AC:END -->
