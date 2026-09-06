---
id: TASK-004
title: >-
  Phase 3: Web UI core (library, transcribe dialog, jobs dashboard, transcript
  view)
status: Done
assignee: []
created_date: '2026-09-01 20:11'
updated_date: '2026-09-02 13:46'
labels: []
dependencies:
  - TASK-002
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Jinja2 + vendored htmx: library with sidebar/folders/search, transcribe dialog (drag-drop multi, path picker, options), jobs dashboard with stage stepper, ETA, cancel/retry, SSE log tail on job detail, transcript view with player (Range/206 + AAC proxy), click-to-seek, highlight, speaker rename and reassignment, confidence tinting. Spec section 4. Plan follows Phase 2.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Upload through browser to finished transcript without CLI
- [x] #2 Jobs page shows live stage progress, queue position and ETA; cancel and retry work
- [x] #3 Transcript page: click-to-seek, rename speaker applied everywhere, reassignment persists with edited_by_user
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Phase 3 web UI complete: 8/8 subtasks Done, 587 tests pass. Hostile review found one CRIT (no Host/Origin guard: cross-site POST could trash the library or enqueue GPU jobs) and one HIGH (/api/media bypassed the fsbrowse roots); both fixed test-first in d802c88 and 076577f, plus 14 further review fixes. Every verifier also started the real server and fetched the pages.
<!-- SECTION:FINAL_SUMMARY:END -->
