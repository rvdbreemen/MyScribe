---
id: TASK-121
title: The right rail ends above the player bar, so This transcript is reachable
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-22T20:54:00Z'
updated_date: '2026-10-22T20:54:00Z'
labels:
  - ui
  - bug
dependencies: []
priority: medium
ordinal: 235000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-10-22, with a screenshot: on a transcript with five speakers the card This transcript at the bottom of the right rail is cut off. Cause: .rail is sticky at the top with max-height calc(100dvh - 1rem), and the player bar is sticky at the bottom (z-index 5) over it, so the rail's last ~player height lies under the bar and scrolling the rail to its end cannot bring it out. Measured on a synthetic library (five speakers named Arthur, Ford, Trillian, Zaphod, Marvin; 400 lines), headless Chrome via CDP, page scrolled to the middle and the rail to its end: 1280x760 last card bottom 752, bar top 679, 73 px hidden. Fix: app.js wirePlayerHeight publishes the bar's height as --player-h (ResizeObserver, since the bar wraps), and .rail subtracts it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Scrolled to its end, the rail's last card ends above the player bar at several window sizes, measured and shown in screenshots
- [x] #2 A test fails when the rail's max-height stops subtracting the player's height
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence (scratchpad railbug/): before-1280x760.png - last card bottom 752, bar top 679, 73 px under the bar, rail max-height 744px. After the fix, same page and scroll: 1280x760 bottom 671 / bar 679; 1280x1000 911 / 919; 1100x700 611 / 619; hidden 0 in all three, --player-h 81.375px (after-*.png). Test test_the_rail_ends_above_the_player_bar red without the fix (AssertionError: 'var(--player-h' in ' calc(100dvh - 1rem)'), green with it; all nine test files that load app.js: 632 passed. At 900 px the rail stacks under the transcript and is not sticky; not affected.
<!-- SECTION:NOTES:END -->
