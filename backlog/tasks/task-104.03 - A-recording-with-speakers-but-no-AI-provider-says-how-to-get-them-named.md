---
id: TASK-104.03
title: A recording with speakers but no AI provider says how to get them named
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 14:43'
labels:
  - ui
  - llm
dependencies: []
parent_task_id: TASK-104
ordinal: 189000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
finalize skips the speaker pass silently when no provider is chosen (_speaker_pass_blocked returns empty), so a fresh install shows Speaker 1 and Speaker 2 with no hint that naming exists.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A failing test: a run with clusters and no provider carries a note naming the fix (choose a provider), rendered on the transcript page, red first
- [x] #2 No note when there are no clusters or the recording is private
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Red: finalize on a diarized run with no provider leaves a speaker_pass_note naming the fix (choose a provider in Settings > AI providers), and the transcript page renders it; a private recording and an undiarized run get none. Green: _speaker_pass_blocked returns that sentence for 'no provider' instead of silence; the note is cleared where it already is (queue_speaker_pass, which the startup sweep runs once a provider is chosen).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: finalize on a diarized run with no provider left no note ('' both in params and via transcript_ui.speaker_pass_note). Green: _speaker_pass_blocked returns NO_PROVIDER ('no AI provider is chosen yet - choose one in Settings > AI providers') when nobody chose, except on a private recording; the existing rendering in _transcript_panel.html shows it. Cleared where it already is: queue_speaker_pass, which the startup sweep runs once a provider is chosen. test_stage_finalize 39, test_llm_speakers 51, test_web_transcript 107 (one stall on the socketpair, then green).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A diarized recording finalized with no AI provider chosen now carries a note on the transcript page saying names were not given because no provider is chosen, and where to choose one; no note for undiarized or private recordings. Red then green.
<!-- SECTION:FINAL_SUMMARY:END -->
