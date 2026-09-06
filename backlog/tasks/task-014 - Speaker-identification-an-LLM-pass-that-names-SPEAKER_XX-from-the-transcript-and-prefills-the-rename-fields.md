---
id: TASK-014
title: >-
  Speaker identification: an LLM pass that names SPEAKER_XX from the transcript
  and prefills the rename fields
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 19:16'
updated_date: '2026-09-06 20:23'
labels: []
dependencies: []
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A diarized recording shows Speaker 1, Speaker 2, Speaker 3 until someone renames them by hand; nothing in MyScribe reads the transcript to find out who is who. WHYcast-transcribe (D:/Users/Robert/Documents/GitHub/RvdB/WHYcast-transcribe/prompts/speaker_analysis_prompt.txt) has a speaker-analysis prompt that maps SPEAKER_XX to names or roles with evidence and confidence, and a speaker_assignment_prompt that applies the mapping. Port the analysis as an LLM task kind: run it over the transcript, store the analysis as an llm_output, parse the FINAL MAPPING block, and prefill the speaker rename fields (speaker_label.display_name) with the suggestions marked as suggestions, so a person confirms rather than types. Respect the privacy pin: a private recording may only use a local provider. Also port WHYcast's cleanup_prompt.txt as a 'cleanup' task kind.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A 'speakers' LLM task kind exists with a prompt derived from WHYcast's speaker_analysis_prompt, without the WHYcast-specific host names
- [x] #2 The FINAL MAPPING block is parsed into {cluster_label: name, confidence}; a malformed answer stores the analysis and suggests nothing rather than failing
- [x] #3 The transcript view offers 'Suggest names' and prefills the rename fields with the suggestions; nothing is written to speaker_label until the person accepts
- [x] #4 A 'cleanup' task kind exists with WHYcast's cleanup prompt
- [x] #5 Tests cover the parser and the privacy gate; a real run on a diarized recording is in the notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. chunking: speaker-labelled lines (cluster most of a segment's words carry) behind a flag. 2. tasks: Speakers schema (lenient defaults), TaskSpec.with_speakers/combine, 'speakers' (reduce) and 'cleanup' (concat) kinds before custom, _collect_parts for concat. 3. ai_ui: speakers view enriched with current names; template form with tick+name per cluster; POST /media/{id}/speakers/apply writes only ticked rows. 4. Tests at task and web level; real run on episode43.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Real run 2026-09-06: episode43 (media 4, 5429 words, 3 clusters), openai gpt-5.6, job 30: 1 call, 16215 prompt / 526 completion tokens, 20 s. Nancy host, Ott co-host (Whisper mishears 'Ad'), Sam guest, all high with [m:ss] quotes. Rows were unticked because Robert had already named them by hand - the intended behaviour. Malformed answers: a JSON object missing fields validates with defaults; prose fails as BadResponse carrying the text on the jobs board (framework rule; AC 2 read that way). Tests: test_llm_speakers.py 11 passed; test_llm_tasks + test_web_ai 94 passed; test_web_transcript, test_exports_doc, test_llm_chat, test_llm_task_providers 78 passed. Commit 2f3ec3b.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Two LLM kinds ported from WHYcast: 'speakers' maps SPEAKER_XX to names with evidence and confidence and offers a tick-to-apply form that writes speaker_label only for accepted rows; 'cleanup' rewrites the transcript chunk by chunk (concat). Verified by 11 new task tests, 3 new web tests, and a live run on episode43 with gpt-5.6.
<!-- SECTION:FINAL_SUMMARY:END -->
