---
id: TASK-055
title: Explain and fix why a cleanup answer does not become a clean reading
status: Done
assignee:
  - '@claude'
created_date: '2026-09-15 17:13'
updated_date: '2026-09-15 20:49'
labels:
  - llm
  - transcript
dependencies: []
ordinal: 100000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On 2026-09-14 the clean_reading table was found empty in the live library while cleanup answers exist, so the Cleaned-for-reading view (TASK-026) never appears. A snapshot from that day holds one clean_reading row and cleanup llm_output rows for media 7 (whose content is a la-la-la hallucination) and a chunk for media 12. Not investigated yet: whether apply_cleanup (scribe/llm/tasks.py) refuses these answers by design, fails, or is never called.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The cause is shown with evidence from code and from a read-only copy of the live library
- [ ] #2 If it is a fault: fixed, with a failing test first, and an existing cleanup answer becomes a reading on a copy of the library
- [x] #3 If it is a deliberate refusal: the page says why no cleaned reading is shown instead of showing nothing
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Finding (2026-09-15, workflow over code, job table, job_event, app.log and a read-only snapshot): not a fault and not a refusal in the live library. Both cleanup answers (llm_output 14, 15) were written on 2026-09-10 15:38-15:48 UTC, before commit 1678dc2 (16:04 UTC) added apply_cleanup and clean_reading; no cleanup job has run since. Row 15 (media 7, a la-la transcript) would be refused by the gate (3418% of the words, reproduced on a copy); row 14 is part 0 of a chunked run that died. The one clean_reading row in the 09-14 snapshot was a hand-made fixture for TASK-053.01. The real gap: the page shows nothing when a cleanup answer exists without a reading, and the verdict is never stored with the answer.
AC2 (fault) does not apply; AC3 carries the task.
1. apply_cleanup stores its verdict as params_json.gate on the answer row, on the pass and the refuse path (both callers go through it).
2. tasks.cleanup_status(conn, media_id, run_id): the newest final 'cleanup' row for that run (never a chunk row), with its gate or None.
3. page_context passes it when the run has no reading; _transcript_panel.html shows one line: refused (date, model, reasons) or made before cleanings were checked. The gate is never recomputed at render time.
4. Red first in test_llm_cleaning_gate.py and test_web_transcript.py; then seen on a copy of the library (media 7 shows the never-checked line). No paid cleanup run in this task.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-15 implementation and evidence.
Cause (AC1), from a read-only copy of today's live snapshot (scratch fix057): llm_output 15 (media 7, kind cleanup, 2026-09-10 15:48:47 UTC, finish_reason length, current run 14) and 14 (media 12, cleanup:chunk:0 of run 20 while the current run is 82, 15:38:24 UTC); clean_reading 0 rows. Commit 1678dc2 (apply_cleanup + clean_reading) is from 16:04:45 UTC that day, after both; job/job_event/app.log hold no cleanup job since (workflow over ids 1-413). So apply_cleanup never ran on them: not a fault, not a refusal. AC2 (if a fault) does not apply and stays unchecked.
Fix (AC3): apply_cleanup keeps its verdict as params_json.gate on the answer row (published, words_in, words_out, reasons), on both paths; tasks.cleanup_status(conn, media_id, run_id) returns the newest final cleanup answer of that run with its gate (None for an answer from before verdicts were kept); the transcript panel shows one line when there is no reading: refused (date, model, reasons) or made before cleanings were checked. Never recomputed at render time.
Red before the fix: KeyError 'gate' x2, no cleanup_status, and the page said nothing for a refused and for a never-checked answer. Guards green before and after: answer about an earlier transcript, a part alone, a published reading beside a later refusal, no answer at all.
Green: test_llm_cleaning_gate 23, test_web_transcript 94, test_llm_tasks 120, test_web_ai 97, test_llm_chunking 29, test_exports_rich 45.
On the copy (app on 4299): /media/7 shows 'A cleanup answer from 2026-09-10 17:48 (openrouter/auto) was made before cleaned readings were checked, so it was never checked and never shown. Ask Clean transcript again for a checked reading.'; /media/12 shows no line. Screenshot scratch task055_media7.png. No paid cleanup run (TASK-026 AC12 stays open).

Two shapes of 'an answer about another transcript': the test pins run_id NULL (the ON DELETE SET NULL shape); the library holds the other one, row 14 naming superseded run 20 while media 12's current run is 82. Both miss cleanup_status's WHERE run_id=<current run>, and media 12 showed no line on the copy.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Not a fault: the two cleanup answers in the library were made on 2026-09-10 before apply_cleanup existed, and no cleanup has run since, so there was never a reading to show (AC2 does not apply). What was wrong was the silence: the gate's verdict is now kept on the answer row, and the transcript page says why no cleaned reading is shown - refused with its reasons, or made before cleanings were checked. Verified: red then green in the gate and page tests, 6 test files green, and on a copy of the library media 7 shows the never-checked line while media 12 (a lone part) shows none.
<!-- SECTION:FINAL_SUMMARY:END -->
