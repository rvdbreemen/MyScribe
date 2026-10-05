---
id: TASK-107.02
title: The install guide's Gatekeeper steps match macOS 26
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 06:56'
updated_date: '2026-10-05 20:22'
labels:
  - docs
  - macos
dependencies: []
parent_task_id: TASK-107
ordinal: 203000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
On macOS 26 the refusal dialog has no Open Anyway; it is in System Settings > Privacy & Security after the first attempt, with Touch ID or a password. Control-click > Open gives the same refusal. Until approved the app is killed (exit 137).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 docs/installation.md and the dmg note describe the macOS 26 route
- [x] #2 The older route stays for earlier macOS, labelled as such
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Route per Hermes on macOS 26.0 (one outside source): the refusal has no Open button; Control-click > Open is refused the same way; System Settings > Privacy & Security > Open Anyway after one attempt, confirmed with Touch ID or a password; until then the app is killed at start (exit 137). That Control-click stopped working from macOS 15 is from my own knowledge of Apple's Sequoia change, not checked against a source here; the docs say 'macOS 15 and later' on that basis and keep Control-click for 14 and earlier. Changed: installation.md, troubleshooting.md, macos-acceptance.md, RELEASING.md, the dmg note (DMG_NOTE). New test test_the_dmg_note_leads_with_the_route_macos_26_takes: red on the old note, green on the new; guide tests green.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The Gatekeeper steps in the install guide, troubleshooting, the macOS acceptance list, RELEASING and the dmg note now lead with the route macOS 15+ takes (start once, then Privacy & Security > Open Anyway), with Control-click kept for macOS 14 and earlier.
<!-- SECTION:FINAL_SUMMARY:END -->
