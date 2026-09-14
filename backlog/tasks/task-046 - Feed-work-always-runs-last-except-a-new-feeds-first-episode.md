---
id: TASK-046
title: 'Feed work always runs last, except a new feed''s first episode'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 20:59'
updated_date: '2026-09-14 02:17'
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
- [x] #1 Every feed-queued job and its transcription runs at the lowest priority the queue uses, whichever path queued it: the watcher, the dialog, or a fan-out
- [x] #2 TASK-044's first episode of a new feed is queued at the normal priority instead, and only that one
- [x] #3 A recording, an upload or a pasted single link is never behind feed work, proved by a test that claims jobs in order
- [x] #4 The priority is set in one place the three paths share, so a fourth caller cannot queue a feed at the default by omission
- [x] #5 Red then green, with the existing priority tests in tests/test_ingest_urls.py extended rather than duplicated
- [x] #6 BULK_PRIORITY is the lowest priority the app itself queues at; a test asserts no code path enqueues below it, so the rule survives a fourth caller
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Robert chose 2026-09-13 what "lowest" means: BULK_PRIORITY (-10) is the floor the system itself uses, not a floating one. Nothing the app queues by itself goes below it, so feed work is always behind everything the app queues at 0.

A person may still go lower by hand through TASK-047, and that is a deliberate act: a job parked at -20 sits behind feed work, which is the point of parking it. The rule to write down is therefore "the app never queues below BULK_PRIORITY", not "feed work is always last whatever anyone does" - a floating floor would keep sinking and would fight the manual control.

Implemented 2026-09-14. Every feed path now queues through one builder (feeds._entry_params) and one constant: the watcher, the ticked-episode dialog, the fan-out and the answer to the question all pass url_stage.BULK_PRIORITY, and a downloaded episode hands its transcription the ingest job own priority. The single exception is a new feed first episode, which queues at the default because somebody is standing there waiting to hear whether the feed is any good (TASK-044).

AC6 is an AST walk over scribe/ rather than a runtime guard: the thing to prevent is someone writing a lower number, and a runtime guard would only catch the paths a test happens to exercise. tests/test_feed_first_episode.py::test_no_code_path_enqueues_below_the_floor parses every module and fails on any enqueue/enqueue_many call whose priority literal is below BULK_PRIORITY; a companion test asserts the feed modules name the constant rather than a copy of -10.

Robert decided 2026-09-13 what "lowest" means: BULK_PRIORITY is the floor the app itself uses, and a person may still park a job below it by hand through TASK-047 - which is what parking means.

AC6's guard was weaker than it read. The AST walk used pathlib.Path('scribe'), relative to the working directory, with no assertion that it had inspected anything: run from any other directory it reported '1 passed' having parsed zero files - a guard that goes quiet instead of red. It is anchored to the test file now and asserts what it read, so a wrong anchor fails loudly (proved both ways: the cwd-relative version fails from elsewhere, the anchored one passes).

It also read every positional argument for a negative number, so jobs.enqueue(conn, T, -11) - where -11 is a media id - would have been reported as a violation. It reads the priority slot only, by keyword or by position, with the signatures asserted so a reorder cannot fool it. Both halves verified with decoys: a -11 media id passes, a real enqueue_many at -20 fails.

AC1's 'and its transcription' holds in source, not only in tests: url_stage enqueues the transcribe job at int(ctx.job.get('priority') or 0), so a feed episode's transcription inherits BULK_PRIORITY and a new feed's first episode keeps 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Feed work runs last. url_stage.BULK_PRIORITY = -10 is the floor the app itself queues at, named once and read by all four paths that queue feed work - the watcher's poll, the answer to a new feed's question, the ticked dialog and a playlist fan-out - so a fifth caller cannot land at the default by omission. A feed episode's transcription inherits its parent's priority, so the whole chain stays down there.

The one exception is TASK-044's first episode of a new feed, at the normal priority, because somebody is standing there waiting to hear whether the feed is any good.

Robert confirmed -10 as the bottom of the system, so a test walks scribe/ and asserts no code path enqueues below it - an AST walk rather than a runtime guard, because the thing to prevent is someone writing a lower number, and a runtime guard only catches paths a test happens to exercise.

Verified: the existing priority tests in tests/test_ingest_urls.py extended rather than duplicated, plus tests/test_feed_first_episode.py. Proved through the running app (TASK-047's AC6 run): a ticked import of 8 episodes queued at -10 while a pasted recording sat at 0 above them. The AST guard was checked against three decoys - a cwd-relative anchor now fails instead of silently passing, a -11 media id is not a false alarm, and a real enqueue_many at -20 is caught. Suite green: 1331 passed, 1 skipped, and 861 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
