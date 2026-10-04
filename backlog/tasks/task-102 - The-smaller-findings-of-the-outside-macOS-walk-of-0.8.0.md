---
id: TASK-102
title: The smaller findings of the outside macOS walk of 0.8.0
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 07:15'
labels:
  - macos
  - installer
dependencies: []
priority: medium
ordinal: 176000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Jim's acceptance walk of 0.8.0 on macOS 26.0 (docs/reports/2026-10-03-macos-0.8.0-acceptance.md) found one blocker, fixed in 0.8.1 (TASK-101), and eight smaller things. Each is a subtask; each ships with its evidence. Robert, 2026-10-04: record them and fix them.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Released as 0.8.3 (PR #5 merged as 018b90a, tag v0.8.3, release.yml run 37184817480 all success). The published SHA256SUMS lists exactly the three artifacts; Windows exe: sha256sum -c OK, attestation -> refs/tags/v0.8.3. Follow-up found on the way: TASK-103 (a user glossary term 'MyScribe' turns 'My Scribe' into 'My MyScribe').
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All eight smaller findings of Jim's outside macOS walk of 0.8.0 fixed in 0.8.3, each its own subtask with red-then-green evidence: bundle version (.01), dmg sizes (.02), SIGTERM stops the app (.03), the app's name corrected without hotwords (.04), install guide verification and model location (.05, .06), SHA256SUMS artifacts only (.07), sync-only output and log (.08). CI green on three OSes, release rehearsal and release built and smoked; not walked on a Mac by a person.
<!-- SECTION:FINAL_SUMMARY:END -->
