---
id: TASK-031
title: >-
  Speaker names survive a re-transcription when, and only when, the voice under
  the label is the same
status: Done
assignee: []
created_date: '2026-09-10 23:06'
updated_date: '2026-09-10 23:07'
labels:
  - bug
  - speakers
dependencies: []
ordinal: 72000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
finalize.inherit_speaker_names copied names from the previous run when the named labels were exactly the new cluster set. Two defects, both found 2026-09-11 while sizing the re-transcription of 43 older runs: (1) a matching set is not the same people - a pipeline change that renumbers clusters would put a name (possibly one a person typed) on the wrong voice; (2) a run where the speaker pass named some clusters and not others never matched, so every name on it was dropped: 12 of the 53 named recordings, 10 of them among the 43.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Names whose label now carries another voice (same label set, voices traded) are not inherited
- [x] #2 A label that merged another old cluster into itself is not inherited
- [x] #3 Names on a partly named run are inherited for the labels whose voice held, and the pass is still queued for the rest
- [x] #4 A new cluster the old run did not have does not stop the others inheriting
- [x] #5 On the real library no label that held its voice is refused (measured shares against the threshold)
- [x] #6 A real re-transcription of a partly named recording keeps its names (old code drops them, new code keeps them), on a copy of the library
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Measure label stability between consecutive runs on the real library. 2. Red tests for traded voices, a merge, a partly named run, a new cluster. 3. Replace the set condition by a per-label, two-way word-overlap share (_voices_kept) with VOICE_KEPT = 0.8. 4. Real run on a library copy (SCRIBE_DATA_DIR), old code from a HEAD worktree vs new code. 5. Suite halves on Windows and Linux, adr-judge.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence 2026-09-11.
Label stability, 11 media re-transcribed (old->new run): per-label two-way share 0.9895-1.000 (lowest media 46 SPEAKER_00); 0 refused at VOICE_KEPT=0.8.
Red (unit, old code): traded voices inherited both names; 3-voice partial swap inherited all three; partly named / new-cluster runs inherited nothing. After step 1 (set check dropped, one-way share only) the merge test was still red: SPEAKER_01 kept "Ford" over Zaphod words - the reason for the two-way share.
Green: tests/test_stage_finalize.py 21 passed; Windows suite 1136 + 797 passed; Linux (WSL, py3.12.3, torch 2.10.0) 1126 + 780 passed (skips are Windows-only tests).
Real run on a copy of the library (SCRIBE_DATA_DIR, live library only read), media 3 (1363 s, 3 clusters, Danny/Nancy named, SPEAKER_01 unnamed), job 205:
  old code (HEAD 5d19d76 worktree): run 77, clusters 00/01/02, labels on new run [] - names dropped, speaker pass 206 queued.
  new code: run 77, same clusters, labels [SPEAKER_00 Danny llm, SPEAKER_02 Nancy llm], pass 206 still queued for SPEAKER_01.
adr-judge 0.57.0 --snapshot worktree: 0 violations, 0 advisory.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
inherit_speaker_names now carries a name over per label when that label covers the same words in both runs, both ways (_voices_kept, VOICE_KEPT 0.8), instead of when the named labels equal the cluster set. That stops a renumbering or a merge from putting a name on the wrong voice, and stops partly named runs (12 of 53) from losing every name on re-transcription. Verified: red/green unit tests, suite halves on Windows and Linux, measured shares on 11 real re-transcriptions (lowest 0.9895), and a real run of media 3 on a library copy where old code dropped Danny/Nancy and new code kept them.
<!-- SECTION:FINAL_SUMMARY:END -->
