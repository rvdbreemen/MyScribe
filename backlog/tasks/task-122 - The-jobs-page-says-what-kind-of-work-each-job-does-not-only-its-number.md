---
id: TASK-122
title: The jobs page says what kind of work each job does, not only its number
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T20:54:00Z'
updated_date: '2026-10-22T21:13:00Z'
labels:
  - ui
  - jobs
dependencies: []
priority: medium
ordinal: 236000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-10-22, with a screenshot of /jobs: the running card reads 'Audio Terrein sessie . running job 426' with stages prepare, generate, store, apply - which is an AI job, not a transcription - and the queue rows show # FILE OPTIONS QUEUED PRIORITY with an empty OPTIONS cell for two jobs on the same file. Nothing on the page says what a job is going to do. The set of job types is small and known (job.type: transcribe, the AI kinds, ingest_url, ...); each should have a short human name and show as a type badge on the running card, in the queue and in the history, so a glance answers 'which job is this'. The page's intro line also names only the transcription stages.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every job shows its kind on the running card, in the queue, in the history and on its page
- [x] #2 A test fails without the kind; a screenshot on synthetic jobs shows it
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
jobs_ui.job_kind(type, params): Transcribe, AI · <task label> (AI when the kind is unknown), Download link, Glossary corrections, Machine check, else the type itself. Badge on the running card and job page, Job column in queue and history; page intro now says the stages listed are a transcription's. Tests red first (2 failed: board and job page), then tests/test_web_jobs.py 54 passed. Screenshots (scratchpad railbug/): jobs-after.png with a running AI · Clean transcript, queued AI · Summary / Transcribe / Download link, history Transcribe / Glossary corrections; job-detail-after.png shows AI · Summary beside queued.
<!-- SECTION:NOTES:END -->
