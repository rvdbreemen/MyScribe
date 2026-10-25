---
id: TASK-111
title: >-
  A short glossary term turns a lone function word into itself (het -> Hetty, op
  -> Opa)
status: Done
assignee: []
created_date: 2026-10-11 20:59
updated_date: '2026-10-25T11:20:00Z'
labels:
  - glossary
  - bug
  - data-loss
dependencies: []
priority: high
ordinal: 212000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-11 by the round-3 deletion review of TASK-103/110 (scratchpad task103/skeptic_del/dm.py, 45,315 sentences): with a short term in the glossary, the single-word phonetic rule respells an unrelated function word anywhere in a sentence: het -> Hetty, op -> Opa, in -> Ina, at 0.9 confidence. 376 of the 647 word losses left on the TASK-110 branch are this class; it is the same on main (pre-existing) and outside the multi-word window logic TASK-110 changes. Also recorded there, still open after TASK-110: a listed longer name completed by 'en' (Niels en -> Nielsen, Peter en -> Petersen) and a middle word inside a multi-word window ('Marieke en Vermeulen').
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A test over common Dutch and English function words with short glossary terms shows none respelled, red first
- [x] #2 The middle-word and name+en classes are fixed here or have their own decision recorded
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-24 Robert's decision (asked with options and consequences in the session): (1) option C: a vendored Dutch/English function-word list (source after an OSS check) plus a length guard: a lone word of 3 letters or fewer is never corrected unless it is an exact listed variant. (2) option b: no join when the leftover is a function word and the head word is itself a listed term. The middle-word class is fixed outright.

2026-10-25 Robert's decision (asked with options in the session): us/will/can/may/must are function words (never take a listed term's capitals); a listed term with two or more capitals restores a lone word's capitals otherwise.

2026-10-25 PR #100 merged, CI green on three OSes. Red first: 21 of 30 new tests on main (13 function-word pairs het/Hetty, op/Opa, in/Ina, is/Isa, er/Era, en/Ena, door/Doori, maar/Maars, the/Thea, and/Anda, it/Ito, on/Ona, at/Ata; Ari/Arie; middle word; name+en), then the middle-word side effect, acronyms, us/US and homonyms; 6/6 mutants killed. Green test_glossary 213 passed. Sweep on 34,878 synthetic sentences: changed 1,837 -> 60, deleted words 848 -> 64; split probes 1,822 -> 1,800 corrected, wrong outputs 8 -> 0. App run on 4430: api->API, gpu->GPU, nos->NOS corrected; het/op/in, Niels en Peter, Tell us kept. Middle-word class fixed outright; name+en per Robert's option b. Known: 'I saw' -> Isa unchanged from main; German/French not protected.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Short glossary terms no longer respell lone function words: a vendored Dutch/English function-word list plus a 3-letter length guard (exact variants still apply), acronym capitals restored by terms with two or more capitals, no join of a listed head word with a trailing function word when both are listed, and middle words must be the term's middle. PR #100.
<!-- SECTION:FINAL_SUMMARY:END -->
