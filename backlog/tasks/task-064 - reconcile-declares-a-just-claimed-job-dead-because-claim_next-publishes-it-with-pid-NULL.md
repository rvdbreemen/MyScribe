---
id: TASK-064
title: >-
  reconcile declares a just-claimed job dead, because claim_next publishes it
  with pid NULL
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 15:14'
updated_date: '2026-09-16 15:14'
labels:
  - review-2026-09-16
  - concurrency
dependencies: []
priority: medium
type: bug
ordinal: 109000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass). claim_next sets status='running' with pid=NULL in one statement (scribe/jobs.py:135) and the supervisor writes the child's pid a moment later (scribe/supervisor.py:254). A reconcile landing in that gap saw a running row with no pid, read pid_alive(None) as False and flipped the job to interrupted - after which the runner's own verdict is refused, because finish() only moves a row that is still running. The row then says 'interrupted, finished 0 s ago' while a live child keeps advancing its stage. Reachable without any bug elsewhere: the project's own manual-check command starts a second app that runs reconcile on boot.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A job claimed but not yet spawned keeps its running status through a reconcile
- [x] #2 A leftover running row with neither pid nor started_at is still flipped, as before
- [x] #3 A test fails before the fix and passes after it
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_supervisor.py: jobs.claim_next gevolgd door supervisor.reconcile.
2. Fix in reconcile: een rij zonder pid overslaan, maar alleen wanneer started_at gezet is - claim_next is de enige bron van status running en stempelt die altijd.
3. Draai tests/test_supervisor.py, tests/test_app.py en tests/test_jobs.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/supervisor.py reconcile: een rij zonder pid wordt overgeslagen, maar alleen wanneer started_at gezet is. Dat onderscheid is inhoudelijk en geen trucje om een test te sussen: jobs.py:135 is de enige plek in de broncode die een taak op running zet, en die stempelt altijd started_at. Een running-rij zonder pid en zonder starttijd kan dus geen net geclaimde taak zijn en houdt het oude gedrag.

Onderweg brak mijn eerste, grovere poging (elke pid-loze rij overslaan) de bestaande test test_reconcile_flips_dead_and_absent_pid_running_jobs, die op regel 238 bewust pid=NULL neerzet en opruiming eist. Dat was terecht gedrag; de verfijnde regel bedient beide.

Rood: assert 'interrupted' == 'running' - de net geclaimde taak werd doodverklaard.
Groen: tests/test_supervisor.py 11 passed, tests/test_app.py 17 passed, tests/test_jobs.py 24 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Een taak die is geclaimd maar nog geen pid heeft, overleeft een reconcile, zodat het verdict van de runner niet meer wordt geweigerd voor werk dat gewoon liep. Een achtergebleven running-rij zonder starttijd wordt nog steeds opgeruimd. Geverifieerd met een test die eerst rood was en daarna groen, plus 11 + 17 + 24 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
