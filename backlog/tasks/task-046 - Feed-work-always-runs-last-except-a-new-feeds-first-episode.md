---
id: TASK-046
title: 'Feed work always runs last, except a new feed''s first episode'
status: To Do
assignee: []
created_date: '2026-09-13 20:59'
updated_date: '2026-09-13 21:29'
labels:
  - feeds
  - jobs
dependencies:
  - TASK-044
ordinal: 84000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-13: "Zorg ervoor dat feeddownloading altijd de laagste prioriteit heeft, zodat het nooit handmatige toevoegingen [ophoudt]. Alleen de eerste aflevering van een feed van een nieuwe feed wordt op normale prioriteit gedownload."

Since the macOS merge (d1a7b50) a fanned-out entry queues at url_stage.BULK_PRIORITY = -10 and its transcription inherits that, and the two enqueue_many sites (the watcher, the ticked dialog) pass it too. That is the mechanism; this task sets the policy on top of it.

Two things it does not do yet: the first episode of a brand-new feed should arrive at the normal priority, because a person is standing there waiting to hear whether the feed is any good (TASK-044 queues exactly that one); and "always lowest" should be a rule the code states, not a default a later caller can forget - the watcher, the dialog and the fan-out all have to agree.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every feed-queued job and its transcription runs at the lowest priority the queue uses, whichever path queued it: the watcher, the dialog, or a fan-out
- [ ] #2 TASK-044's first episode of a new feed is queued at the normal priority instead, and only that one
- [ ] #3 A recording, an upload or a pasted single link is never behind feed work, proved by a test that claims jobs in order
- [ ] #4 The priority is set in one place the three paths share, so a fourth caller cannot queue a feed at the default by omission
- [ ] #5 Red then green, with the existing priority tests in tests/test_ingest_urls.py extended rather than duplicated
- [ ] #6 BULK_PRIORITY is the lowest priority the app itself queues at; a test asserts no code path enqueues below it, so the rule survives a fourth caller
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Robert chose 2026-09-13 what "lowest" means: BULK_PRIORITY (-10) is the floor the system itself uses, not a floating one. Nothing the app queues by itself goes below it, so feed work is always behind everything the app queues at 0.

A person may still go lower by hand through TASK-047, and that is a deliberate act: a job parked at -20 sits behind feed work, which is the point of parking it. The rule to write down is therefore "the app never queues below BULK_PRIORITY", not "feed work is always last whatever anyone does" - a floating floor would keep sinking and would fight the manual control.
<!-- SECTION:NOTES:END -->
