---
id: TASK-010
title: >-
  Resolve multi-reviewer review #3 findings (30) on theme, menus, record dialog,
  scope, silence detection and applog
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 08:42'
updated_date: '2026-09-06 09:28'
labels:
  - review
  - web
  - ops
dependencies: []
ordinal: 51000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Five-dimension review of d7358c6~1..bb92a15 produced 0 Critical, 4 High, 13 Medium, 13 Low (CR-001..CR-030; report in the session of 2026-09-06). Highs: applog._proc is a module global overwritten by the supervisor thread; the catch-up tail is unbounded (60k lines = 1.7 s Python); the 'No sound' warning exists only as canvas pixels; aria-live on the streaming job-log <pre>. Fix all thirty, tests first where a behaviour can be pinned.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 CR-001..004 (High) fixed with a test each
- [x] #2 CR-005..017 (Medium) fixed; contrast and focus fixes measured
- [x] #3 CR-018..030 (Low) fixed; CR-025 as a Proposed ADR on 'the application log is observation'
- [x] #4 CR-009 tautological test replaced by a behavioural one in the node harness; CR-022 greps fixed
- [x] #5 suite green in parts; live check of /logs, the popover menus and the recorder in a browser
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Groups: A applog core (CR-001 thread-local proc, 002 MAX_TAIL_BYTES + limit on both branches, 005 URL query/cookies redaction by shape, 007 docstring, 011 mutex before thread lock, 018 unknown level, 020 rotation inside mutex, 008 tail resync); B log page (012 DOM trim, 017 pause button); C supervisor (006 sweep_stderr 7 days, seek tail, shutdown/spawn-fail cleanup); D recorder (003 live region on change, 013 hidden-tab clamp, 023 tone cache, 028 reduced motion, 009 behavioural node test, 022 greps); E a11y (004 aria-live to status sentence, 014 badge id, 015 --line-control, 016 flash innerHTML oob, 019 implicit popover anchor, 026 popover aria-label, 027 aria-controls, 029 aria-sort, 030 dead selector); F probe (024 LOUDNESS_TIMEOUT -> not measured, comment; 021 relation asserts); G ADR-007 Proposed.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Browser (4299): popover anchors to its invoker with no inline styles (panel below/right of the button, outside .table-wrap), panel named, pressed look via .menu-button:has(+ [popover]:popover-open) after a frame; --line-control measured 4.34-5.95:1 on every surface; /logs trim keeps data-log-keep rows (3 kept of 9), Pause sets hx-trigger=none and no rows arrive, Resume restores. Suites: applog 14, web_logs 10, web_library 51, web_jobs 33, supervisor 9, probe 25, recorder 24 (CR-009 now behavioural: FakeRecorder.start throws -> data-busy cleared, Record shown, /cancel posted, track stopped).

CR-003 verified live with a real recording as the fake microphone: state sound -> live region 'Sound is reaching the microphone.'; gain 0 -> after 2.8 s state silent and 'No sound is reaching the microphone. Check the input device.'; gain back -> sound; Discard -> data-busy cleared, 'Recording discarded.'. ADR-007 created Proposed (adr new), retrieval metadata filled, adr-lint --strict passes with one advisory. Test server stopped.

Final suite, one part at a time: a-i 87+231+257, j-r 265+142, s-u 202, web 87/49/33/51/10/24/21/29/54/35/33 - all passing (web_ai timed out only when run beside another TestClient suite; alone 26 s). One pre-existing settings test pinned hx-swap-oob="true" and was updated to the innerHTML form.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All thirty review-#3 findings resolved. Highs: applog writer name is thread-local (supervisor no longer renames web lines); tail() capped at 1 MB with limit on both branches; the scope's silence verdict has a role=status twin written on change; aria-live moved from the streaming <pre> to the status sentence. Mediums/Lows across applog (mutex order, rotation under mutex, URL/cookies redaction, docstring, unknown level, rotation resync), log page (trim, pause), supervisor (stderr sweep, seek tail), recorder (hidden-tab clamp, tone cache, reduced motion, behavioural teardown test), probe (loudness timeout as not-measured, relation asserts), a11y (badge id, control contrast 4.3-6:1, flash innerHTML, popover names + implicit anchor, aria-controls, aria-sort, dead selector). ADR-007 Proposed for the observation boundary. Verified by 20 new/changed tests, live browser checks of menus, log page and the recorder's live region, and the suite in parts.
<!-- SECTION:FINAL_SUMMARY:END -->
