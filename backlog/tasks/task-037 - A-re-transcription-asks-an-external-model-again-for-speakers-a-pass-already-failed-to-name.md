---
id: TASK-037
title: >-
  A re-transcription asks an external model again for speakers a pass already
  failed to name
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 17:33'
updated_date: '2026-09-11 20:50'
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
- [x] #1 Robert's decision is recorded: does a re-transcription of a recording with a stored speakers answer ask again by itself?
- [x] #2 A recording whose pass never ran still gets one after its transcription, and a private recording never gets an external one (TASK-024 and the catch-up unchanged for those cases)
- [x] #3 Since Robert chose to ask again: a re-transcription queues a pass even when every name carried over. Names are inherited from the run that was current, a name a person typed and a role word are never written over it, and a red/green test holds each
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11: TASK-035's re-transcription held media 13, 20, 36 and 39 for this decision - each has a stored pass and an unnamed cluster, so a re-transcription would ask again. Their current runs keep a seam echo (13, 36, 39) or lack the second opinion (20) until then.

Robert, 2026-09-11 ~21:00: "Bij opnieuw transcriberen moet [er] opnieuw sprekeranalyse gedaan worden." A re-transcription asks again; the catch-up's 'only if it never ran before' is for recordings that were never asked, not for a re-transcription. Acted on: the hold on media 13, 20, 36, 39 is lifted, jobs 280-283 queued (0 private). Open: whether 'again' means always, or (as the code does now) only when a cluster has no name after inheritance - asked.

Robert chose 'Altijd opnieuw' (2026-09-11 ~21:05): a re-transcription asks again even when every name carried over. Built on branch task037-ask-again (worktree, so the running batch's runner children kept importing 2cde07b): 6c1eb83 finalize queues the pass with even_if_named=True; the catch-up keeps asking only where speakers were never assigned (test: a recording a person named whole is left alone). Red: two runs through finalize, both names inherited, queued [('speakers', 1)] only; green 102 passed; mutation 4/4. Adversarial review (2 lenses, each finding verified): 5 confirmed, 5 refuted. Fixed in df0775c, each red first (16 failed -> 118 passed, mutation 7/7): a role word in the name field ('Host' at 95) was written over an inherited name; names were inherited from the newest other run instead of the run that was current (a failed attempt in between lost a person's name); the sweep read only media.private and missed a private folder on a local provider; a rename landing between apply_speakers' read and write was overwritten. Refuted: redundant pass on a superseded run, library badge showing the pass, the e2e fixture claiming the LLM job, two stale docstrings (rewritten anyway). Not yet landed on feat/feed-episode-import: after the batch (jobs 259-283) finishes, then suites, then the 21 re-transcribed on 2026-09-11 get their pass through the transcript's own endpoint.

Landed 2026-09-11 ~22:15 on feat/feed-episode-import as 373f219 + 63a3c91 (cherry-picks of 6c1eb83 + df0775c) while job 280 was mid-run, so the passes queued after the batch (284+) run with the guarded apply_speakers; job 280's own finalize still ran 2cde07b's (it queued pass 284 because media 13 has an unnamed cluster). Worktree suite before landing: a-r 1234 passed; s-z stalled twice under the running GPU batch (the known Windows socketpair stall: CPU frozen at 119 s, killed) - full suites to be run on the branch after the batch. Retro passes for the 21 re-transcribed on 2026-09-11 (jobs 259-279) queued through the transcript's endpoint, POST /media/{id}/ai/speakers: jobs 285-305, openrouter, 0 private.

AC #2 ("If not: the finalize path skips the pass...") was removed on 2026-09-11 because Robert took the other branch ("Altijd opnieuw"). It is replaced by the AC for the branch he chose, so nothing is checked that was not built.

Live outcome, 2026-09-11 22:41. All 25 passes are done, 0 failed: job 284 from finalize, 285-305 through the transcript endpoint for the 21, and 306-308 from the finalize of 20, 36 and 39. openrouter/auto; 404,494 prompt + 19,534 completion tokens; 0 private recordings. Name effect on the 25 (old current run vs new): 39 named clusters unchanged, 12 full names shortened to a first name on 6 recordings, 2 lengthened (media 29), 1 newly named (media 20), 0 gone, 0 role words. The shortening follows the prompt itself (speakers.md asks for the first name) and is filed as TASK-039 for Robert. None of the 25 runs held a name a person typed, so the human guard was held by tests, not by this batch. Suites on 372f681: Windows 1234 + 838 passed; Linux 1224 + 821 passed (10 + 17 skipped, same as before). Targeted: 119 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Robert decided that a re-transcription always asks who is speaking again. finalize now queues the speakers pass with even_if_named=True (373f219); the catch-up sweep keeps asking only recordings that were never asked, and never private ones. The review fixes landed in 63a3c91. Names are inherited from the run that was current, not from a failed attempt. A role word never overwrites a name. A name a person typed survives a rename race. The sweep reads privacy through the folder chain. Evidence: red then green (16 failed -> 118 passed), mutation 7 of 7, suites green on Windows and Linux. Live: 25 passes, 0 failed, 404k + 19.5k tokens, 0 private. Side effect: 12 full names became first names, filed as TASK-039.
<!-- SECTION:FINAL_SUMMARY:END -->
