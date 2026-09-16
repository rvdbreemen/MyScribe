---
id: TASK-081
title: >-
  A green golden run cannot distinguish 'the goldens match' from 'the goldens
  were rewritten during this run'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:45'
updated_date: '2026-09-16 17:46'
labels:
  - review-2026-09-16
  - tests
dependencies: []
priority: low
type: bug
ordinal: 126000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low). check_golden in tests/test_exports_text.py writes the golden first when SCRIBE_UPDATE_GOLDENS=1 and compares afterwards, so with the variable set every golden test passes whatever the exporter produced. A developer who exports the variable in their shell, rather than prefixing the one regeneration command CLAUDE.md shows, then has a suite that blesses every export change silently - and the working agreement says a golden never moves without its diff being shown.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 With SCRIBE_UPDATE_GOLDENS=1, a golden whose content changed is rewritten and the test fails with the diff, so the run is red exactly where a golden moved; a golden whose content did not change passes
- [x] #2 Without the variable, behaviour is unchanged: a mismatch fails with the diff, a match passes
- [x] #3 The docstring and CLAUDE.md say that an update run is red where goldens moved and that a plain run afterwards is the proof
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: two tests over check_golden against a tmp golden dir - with UPDATE_GOLDENS a changed golden is rewritten and the test fails with the diff, then passes on the next call; without it a mismatch fails and writes nothing.
2. Green: check_golden reads first, and under SCRIBE_UPDATE_GOLDENS=1 writes and fails with the diff only when the content moved; the module docstring and CLAUDE.md describe the two-run flow.
3. Run test_exports_text and test_exports_rich one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_an_update_run_is_red_where_a_golden_moved failed with DID NOT RAISE (an update run passed silently); the plain-run test passed before the change and pins that path. Green: test_exports_text 74, test_exports_rich 45; and a run under SCRIBE_UPDATE_GOLDENS=1 with unchanged goldens stays 74 passed and rewrites nothing (git status tests/golden clean). AC #3 is the docstring and CLAUDE.md diff.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
check_golden reads first and, under SCRIBE_UPDATE_GOLDENS=1, rewrites a golden that moved and fails with the diff, so an update run is red exactly where goldens changed and a green run always means they matched. Verified red-to-green by two tests, 119 tests across two files, plus an update run over unchanged goldens.
<!-- SECTION:FINAL_SUMMARY:END -->
