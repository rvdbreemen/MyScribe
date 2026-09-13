---
id: TASK-033
title: >-
  ADR-002's forbid_import patterns match substrings, so prose like "rediscover"
  blocks a commit
status: In Progress
assignee: []
created_date: '2026-09-10 23:21'
updated_date: '2026-09-13 12:30'
labels:
  - adr
dependencies: []
ordinal: 74000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ADR-002 (Accepted) forbids "redis", "celery" and "rq" under scribe/** as forbid_import rules. adr-kit defines forbid_import as the same regex search over added lines as forbid_pattern (adr-kit templates/adr-kit-guide.md: "same engine as forbid_pattern; the separate name documents intent"), and its own example is anchored (^#include\s+<ArduinoJson\.h>). So this is not an adr-kit bug - an earlier note in this session said the fix belonged in the adr-kit repo; that was wrong. The patterns here are bare substrings. Reproduced 2026-09-11 with adr-judge 0.57.0, declarative pass only, on a three-line diff to scribe/x.py: a docstring containing "rediscover" is reported as an ADR-002 violation, as is a real "import redis". The anchored alternative ^\s*(?:import|from)\s+(?:redis|celery|rq)\b matches "import redis", "from rq import Queue" and "import celery.app" and not the prose. Nothing under scribe/ matches today; the cost is an occasional blocked commit and a reworded docstring. The ADR guide forbids rewriting an Accepted ADR: tightening the patterns means a Proposed successor and a human-gated supersede.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Robert decides: a successor ADR with anchored patterns, or keep ADR-002 as it is and live with the false positive
- [x] #2 If a successor is written, adr-judge on the reproduction diff reports the import lines and not the docstring
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Robert 2026-09-11: "Doe alles". Successor written as docs/adr/ADR-009 (Proposed), created with the kit's adr new, restating ADR-002 in full with anchored forbid_import patterns (^\s*(?:import|from)\s+(?:redis|celery|rq)\b per module) and ADR-002's two RLock rules unchanged. Checks: adr-lint --strict all pass; adr-quality 1.00 (A); adr-readiness ready-for-confirmation. Enforcement dry run (adr-judge --dry-run-enforcement, declarative, in a scratch copy of docs/adr with ADR-009 set to Accepted there only, because the judge enforces Accepted records only): fp.diff - ADR-002 2 findings (the "rediscover" docstring and the import), ADR-009 1 (the import); an 18-line probe - ADR-002 17, ADR-009 8, all seven prose or look-alike lines silenced (rediscover, redistribute, rqlite, redistools, celery_like, irq_count, pool = "celery"). ADR-009 misses "import os, redis" and importlib.import_module("redis"), named in its Negative consequences as the accepted trade; the LLM pass stays on. ADR-002 is byte-identical to HEAD: an adr relate that wrote its reciprocal side (including a status_history entry signed by the agent) was undone with relate --remove, and the link lives in prose, as ADR-008's does. What is left is human-gated and was not run: adr accept ADR-009 (with the human's --confirm), then adr supersede ADR-002 --by ADR-009.

Ready for Robert, 2026-09-13. Everything but the gate is done: ADR-009 is written and Proposed, adr-lint --strict passes, adr-quality 1.00 (A), adr-readiness says ready-for-confirmation, and the enforcement dry run reports the import lines rather than the docstring that started this (AC2). Two commands close it, and they are deliberately left to a person - accepting an ADR is the moment a human confirms the decision, and the same session that wrote it should not also approve it:

    bin/adr accept ADR-009
    bin/adr supersede ADR-002 --by ADR-009

After that, AC1 can be checked and the task set Done.
<!-- SECTION:NOTES:END -->
