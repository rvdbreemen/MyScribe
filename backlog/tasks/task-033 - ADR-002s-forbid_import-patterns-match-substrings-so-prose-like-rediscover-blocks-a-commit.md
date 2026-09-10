---
id: TASK-033
title: >-
  ADR-002's forbid_import patterns match substrings, so prose like "rediscover"
  blocks a commit
status: To Do
assignee: []
created_date: '2026-09-10 23:21'
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
- [ ] #2 If a successor is written, adr-judge on the reproduction diff reports the import lines and not the docstring
<!-- AC:END -->
