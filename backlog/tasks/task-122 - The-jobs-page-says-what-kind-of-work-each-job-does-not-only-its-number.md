---
id: TASK-122
title: The jobs page says what kind of work each job does, not only its number
status: To Do
assignee: []
created_date: '2026-10-22T20:54:00Z'
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
