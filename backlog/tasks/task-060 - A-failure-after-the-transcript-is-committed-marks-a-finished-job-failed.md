---
id: TASK-060
title: A failure after the transcript is committed marks a finished job failed
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 14:32'
updated_date: '2026-09-16 14:56'
labels:
  - review-2026-09-16
  - pipeline
dependencies: []
priority: medium
type: bug
ordinal: 105000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). finalize.run does its post-commit work unguarded: inherit_speaker_names and queue_speaker_pass run after _commit has written the words and made the run current (scribe/stages/finalize.py:107, :126-134). queue_speaker_pass ends in jobs.enqueue, a write; if that raises - a locked database past the busy timeout, for instance - the exception leaves the stage, and runner.py:278-288 turns any exception into a failed verdict with an error_detail. The transcript itself survives intact, so the user sees a finished, current transcript under a job that says it failed, plus a Retry that burns a second full GPU pass and writes a run that supersedes a good one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 When the post-commit work raises, the job still reaches its normal verdict and the committed run stays current
- [x] #2 The failure is not silent: it is written to the application log and to the job's event stream
- [x] #3 The happy path is unchanged: the speaker pass is still queued and its event still emitted
- [x] #4 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_pipeline_e2e.py met de bestaande finalize_ctx-helper (:514): laat queue_speaker_pass falen en toon aan dat finalize.run afrondt en de run actueel blijft.
2. Fix in scribe/stages/finalize.py: het nawerk na _commit (inherit_speaker_names, queue_speaker_pass, de speakers-queued emit) in een try/except Exception, met applog.log op warn en een job-event, en daarna gewoon doorgaan.
3. Het gelukkige pad blijft ongewijzigd: de sprekersronde wordt nog steeds ingepland en het event nog steeds uitgezonden.
4. Draai tests/test_stage_finalize.py en tests/test_pipeline_e2e.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/stages/finalize.py: het nawerk na _commit (inherit_speaker_names, queue_speaker_pass en de speakers-queued emit) staat nu in een try/except Exception. De fout gaat naar applog op level warn en naar de gebeurtenissenstroom van de taak als speakers-queue-failed, en wordt niet opnieuw opgeworpen. Runner.py:278 maakt namelijk van elke ontsnapte uitzondering een failed-verdict, terwijl de woorden op dat moment al gecommit zijn en de run al actueel is.

ADR-007 blijft gerespecteerd: er wordt naar het log en naar de events geschreven, maar niets leest die terug om een besluit te nemen.

Rood: sqlite3.OperationalError: database is locked ontsnapte uit finalize.run.
Groen: gerichte test passed; tests/test_pipeline_e2e.py 53 passed, tests/test_stage_finalize.py 24 passed. De test toetst dat de run actueel blijft, dat de drie woorden er staan, dat speakers-queue-failed is uitgezonden en dat speakers-queued juist niet.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Een fout in het werk na de commit laat een voltooide, actuele transcriptie niet langer als mislukt eindigen. De mislukking verdwijnt niet: zij gaat naar het applicatielog en naar de events van de taak. Geverifieerd met een test die eerst rood was op de ontsnapte OperationalError en daarna groen, plus 53 + 24 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
