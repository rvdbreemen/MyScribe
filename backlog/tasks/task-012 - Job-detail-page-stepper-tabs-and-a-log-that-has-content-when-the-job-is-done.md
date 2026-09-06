---
id: TASK-012
title: 'Job detail page: stepper, tabs and a log that has content when the job is done'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 19:16'
updated_date: '2026-09-06 19:23'
labels: []
dependencies: []
ordinal: 53000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The job page's stepper turns every stage the same flat green when done, the three tabs (Live log / Events / Parameters) look like an afterthought, and the Live log tab is empty on a finished job because the stream starts after the newest event the page already rendered - on a done job that is nothing. Robert asked (2026-09-06) for a better look for the stepper, a better tab design, and a log tab that shows the job's log after it has finished, or drops the tab.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The stepper distinguishes done, active, failed and pending stages with more than a colour swap (icon or fill) and reads well in both themes
- [x] #2 The tabs read as part of the page (aligned with the status block, same visual family as the settings sidebar)
- [x] #3 On a finished job the log tab shows the job's event log as text instead of an empty box, or the tab is absent
- [ ] #4 A screenshot of a done job and of a running job is in the task notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. The log <pre> is prefilled server-side with the events the page already has (same one-line format app.js uses), for running and finished jobs alike; the stream then only adds what comes after last_seq. Tab is 'Log', status line says finished/live.
2. Stepper: state icons via ::before (done check, active dot, failed cross, todo dot), pending steps dimmed, so state is more than a colour.
3. Job tabs: content-sized segmented control with the event count on the Events tab; the open panel gets a top border so tabs and panel read as one control.
4. Tests: log prefill on a done job contains the events' text; screenshot in notes.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified in the browser on port 4299, job 23 (done): the Log tab shows all 13 events plus the end line; stepper shows ticks and connectors; tabs show Events 13 and Parameters 4. AC 4 only half met: no running job was available for a screenshot; the running-state look (pulsing dot, Live log label) is covered by test_job_detail_log_is_prefilled_with_the_events_the_page_already_has only. tests/test_web_jobs.py: 34 passed. Commit 0d411f3.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The job log <pre> is rendered with the events the page already has and the stream only adds what follows; the stepper marks state with a glyph and connectors; the tabs are content-sized with counts. Verified with the jobs web tests (34 passed) and a browser screenshot of job 23 on the 4299 instance. Running-job screenshot not taken (none running).
<!-- SECTION:FINAL_SUMMARY:END -->
