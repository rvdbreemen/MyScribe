---
id: TASK-052
title: 'The whale outlived its own rename: one source for the two tier icons'
status: Done
assignee: []
created_date: '2026-09-14 20:23'
updated_date: '2026-09-14 20:24'
labels:
  - ui
  - bug
dependencies: []
priority: high
ordinal: 90000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-14, with a screenshot of the Watch folders panel: "wrong icon in settings" - Turbo showed the new bolt and Maximaal still showed the whale.

TASK-049 replaced the dolphin and whale with a bolt and a target, and replaced them with a script that searched for U+1F42C and U+1F433. The templates carry U+1F42C DOLPHIN and U+1F40B WHALE. U+1F433 is SPOUTING WHALE - a different character. So the dolphin went everywhere and the whale went nowhere except the one macro I rewrote by hand.

The guard test written for exactly this failure looked for the same two constants, so it stayed green while four files kept the whale. A test that names the codepoints it expects cannot catch the case where the codepoint is the thing you got wrong.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 No sea-mammal character remains anywhere under scribe/
- [x] #2 The guard asks Unicode what a character is rather than comparing against a codepoint typed from memory
- [x] #3 The two icons are defined once and read by the templates and the jobs summary alike
- [x] #4 Red then green: the guard fails on the tree as it was, and the settings page is asserted to render both icons
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Turbo showed the new bolt and Maximaal still showed the whale, in three templates and the jobs summary. TASK-049's replacement script searched for U+1F42C DOLPHIN and U+1F433 SPOUTING WHALE; the templates carry U+1F42C and U+1F40B WHALE. The dolphin went everywhere, the whale went nowhere but the one macro rewritten by hand - and the guard test written for precisely this failure named the same two constants, so it stayed green through all of it.

Two changes, because the bug had two halves. The guard now asks unicodedata what each character is and fails on any name containing WHALE or DOLPHIN, so there is no codepoint left to get wrong. And the pair is defined once, as web.TIER_ICONS, published into the Jinja environment and imported by jobs_ui - five literals across four files is how one of them got missed in the first place.

Verified: both tests red first - the name-based guard failed on the tree as it stood, naming all four files, and the single-source test failed on the import that did not exist. Green after, 24 in tests/test_library_row_meta.py. Every suite that renders a tier icon green: settings 35, dialog 54, jobs 42, transcript 46, library 77, scaffold 27. Version 0.2.1.
<!-- SECTION:FINAL_SUMMARY:END -->
