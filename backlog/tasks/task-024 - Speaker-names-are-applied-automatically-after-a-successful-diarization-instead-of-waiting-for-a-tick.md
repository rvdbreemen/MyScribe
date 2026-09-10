---
id: TASK-024
title: >-
  Speaker names are applied automatically after a successful diarization,
  instead of waiting for a tick
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-10 04:41'
labels: []
dependencies: []
type: enhancement
ordinal: 65000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-014 shipped the 'speakers' LLM kind: it reads a diarized transcript, maps SPEAKER_XX to names with evidence and confidence, and prefills a tick-to-apply form. Its AC3 was explicit that nothing is written to speaker_label until a person accepts. Robert asked on 2026-09-10 to reverse that: after transcribe and diarize succeed, the speaker analysis should run by itself, and on success the names should be applied. This task is that reversal, and it should be read as a deliberate change of a shipped decision rather than a missing feature. Measured on 2026-09-10, this is why it matters: 60 runs in the database carry diarized speaker clusters with stored embeddings, and speaker_label holds 3 names in total - every one of the 50 Hacker History episodes reads 'Speaker 1' and 'Speaker 2', while each is an interviewer and a guest who introduce themselves in the first minute. WHYcast's ADR-004 (D:/Users/Robert/Documents/GitHub/RvdB/WHYcast-transcribe/docs/adr/) is the design of record for the two-phase shape and its Must Not stands here too: the model produces a mapping, code applies it, and no prompt is allowed to rewrite transcript text. MyScribe is structurally safer than WHYcast on that point - the transcript is words in a table (ADR-003) and applying a mapping is an INSERT into speaker_label, not string replacement over prose, so WHYcast's content-loss check has no counterpart to port. Two adaptations agreed with Robert: a new run inherits the previous run's names for the same media when the transcript has not meaningfully changed, so a re-transcription does not pay a reasoning model to rediscover names that were already right (WHYcast solves this with a transcript fingerprint in a _speakers.json that the pipeline reads as an input); and a cluster the model cannot place keeps its 'Speaker N' rather than receiving a guess. Hooks: scribe/stages/__init__.py holds TRANSCRIBE_STAGES (probe, prepare, transcribe, diarize, attribute, correct, finalize); scribe/stages/diarize.py:685 writes speaker_embedding; scribe/web/transcript.py:371 is today's only writer of speaker_label; the 'speakers' kind lives in scribe/llm/tasks.py with its web side in scribe/web/ai_ui.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A successful diarization enqueues the 'speakers' analysis by itself, with no click, and a run without diarization enqueues nothing
- [ ] #2 On a successful analysis the names are written to speaker_label without waiting for a tick; a cluster the model could not place keeps its default 'Speaker N'
- [ ] #3 A name a person typed is never overwritten by a later automatic run: speaker_label records who chose the name
- [ ] #4 A re-transcription of the same media inherits the previous run's names when the transcript has not meaningfully changed, and re-analyses when it has
- [ ] #5 A failed or malformed analysis leaves the recording with its default speaker names and is visible on the jobs board, never silent
- [ ] #6 The existing tick-to-apply form from TASK-014 still works as the correction path
- [ ] #7 The privacy pin still holds: a private recording reaches no cloud provider
- [ ] #8 Tests cover the automatic enqueue, the do-not-overwrite-a-human rule, the inheritance across runs and the failure path; a real run over a Hacker History episode is in the notes, with the names it produced
<!-- AC:END -->
