---
id: TASK-082
title: ADR-007 and ADR-009 no longer describe the code or guard what they say
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 19:58'
updated_date: '2026-09-16 19:58'
labels:
  - review-2026-09-16
  - architecture
  - adr
dependencies: []
priority: low
type: task
ordinal: 127000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three review findings of 2026-09-16, all documentation and enforcement drift in Accepted records: ADR-009 quotes a claim SQL that predates queue_seq and the one-runner-at-a-time clause (TASK-070), and its anchors moved (the permitted second lock is playback._proxy_lock, not transcript.py:648); ADR-009 carried a threading.RLock rule whose glob scribe/[!d]*.py the judge cannot read; ADR-007's reader rule over scribe/[!w]*/** matches no file at all. The kit's guide forbids rewriting an Accepted record, so each gets a Proposed successor with the corrected content and a rule the judge can apply, confirmed by a probe diff. Acceptance and supersession are human-gated.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ADR-013 (Proposed) restates ADR-009 with the claim SQL as the code has it, current anchors, and an RLock rule over scribe/** that a probe diff trips
- [x] #2 ADR-014 (Proposed) restates ADR-007 with a reader rule over scribe/** that a probe diff trips and the one reader marked on its lines
- [x] #3 adr-lint --strict passes; the index is regenerated; accept and supersede are left to Robert
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Successor records via adr new; bodies restate the decisions with corrected SQL, anchors and globs; probe diff through adr-judge --dry-run-enforcement on a scratch copy marked Accepted; adr-index; adr-lint --strict.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Probe (scratch copy marked Accepted): ADR-013 flagged threading.RLock() in scribe/supervisor.py and import redis in scribe/db.py, not the re-added LOCK = threading.RLock() line; ADR-014 flagged applog.tail( in scribe/supervisor.py, not the marked logs_ui line. adr-lint --strict: pass (2 advisory). test_web_logs 10 passed. Pending for Robert: adr accept ADR-013, adr supersede ADR-009 --by ADR-013; adr accept ADR-014, adr supersede ADR-007 --by ADR-014.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
ADR-013 and ADR-014 proposed as successors of ADR-009 and ADR-007 with working enforcement blocks, confirmed by probe; acceptance and supersession are Robert's.
<!-- SECTION:FINAL_SUMMARY:END -->
