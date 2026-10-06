---
id: TASK-107.07
title: Say that --no-supervisor also stops the watch folders
status: Done
assignee:
  - '@claude'
created_date: '2026-10-06 09:28'
updated_date: '2026-10-06 20:30'
labels:
  - watch
  - docs
dependencies: []
parent_task_id: TASK-107
priority: low
ordinal: 208000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Hermes, 2026-10-05: with --no-supervisor the start log reads supervisor false, watcher false, and a watch folder silently does nothing. Probably intended (the watcher lives with the supervisor), but nowhere visible: neither --help nor the Watch folders card says so.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 --help and the Watch folders card say that watching needs the supervisor, or the watcher runs without it
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_the_help_for_no_supervisor_says_it_stops_watch_folders_and_feeds, test_the_watch_card_says_when_nothing_is_watching, test_the_watch_card_says_nothing_of_the_kind_when_the_watcher_runs (KeyError 'watching'). Fix: --help now reads 'do not start the job supervisor thread, nor the watch folders and feeds that follow it: nothing is transcribed or looked at by itself'; watch_context gains watching = watcher is not None; the Watch folders card shows a warn banner (data-watching-off) when it is False. First name data-not-watching collided with the existing not-watching badge assertions - renamed. Real run: python -m scribe --port 4299 --no-supervisor on scratch data, GET /settings?section=watch shows the banner. The with-supervisor case is covered by the unit test only: starting a supervisor on the scratch data would claim its seeded running job and load a model. Green: test_ingest_watching 104, test_web_settings 56, test_app 17.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
--help and the Watch folders card say that --no-supervisor also stops watch folders and feeds.
<!-- SECTION:FINAL_SUMMARY:END -->
