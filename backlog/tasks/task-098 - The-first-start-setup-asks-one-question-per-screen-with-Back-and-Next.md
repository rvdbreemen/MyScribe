---
id: TASK-098
title: The first-start setup asks one question per screen, with Back and Next
status: Done
assignee:
  - '@claude'
created_date: 2026-09-27 08:37
updated_date: '2026-10-24T18:23:00Z'
labels:
  - installer
  - setup
  - ui
dependencies: []
priority: medium
ordinal: 172000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-27, on the 0.7.0 installer: the Set up MyScribe window is one long scrolling form - what was found, the library, which folder, the AI provider, transcription quality, the watch folder, start at login - each with its options, their explanations, a Skip checkbox and a paragraph about skipping. Too long and too many questions at once. He wants one question per screen and a Next button to go to the next one. ADR-015 holds: the setup engine owns the questions and their order (the JSON contract); the launcher's Tk window only renders it, so this is a change to how the window renders, not to which questions exist. The same applies to any other front-end that renders the contract.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The window shows one question per screen, with Back and Next; the last screen shows a short summary of the answers and a Finish that applies them
- [x] #2 What MyScribe found on this machine is its own first screen, shorter than today, and a question whose answer was found is not shown as a question
- [x] #3 A question that depends on an earlier answer appears only when it applies (the library folder only after 'I already have one')
- [x] #4 Each screen fits without scrolling at the window's size on a 1080p display; the skip explanation is one line, with the details behind a link or a smaller text
- [x] #5 Skip is a button on the screen, not a checkbox, and moves on; nothing is written for a skipped question, as today
- [x] #6 The engine contract and its tests are unchanged; the launcher's rendering tests cover paging, Back keeping answers, conditional screens and Finish; a real run on this laptop with a screenshot per screen
- [x] #7 Closing the window asks first, and only a confirmed close is 'ask me next time'
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Cause of 'cannot get through': ask_setup grids every question into one Toplevel with no scrollbar; with seven questions the window runs past the bottom of a 1080p screen and 'Save and start' is off-screen, so closing ('ask me next time') is the only way out.
2. Rendering only (ADR-015): pages inside the same Toplevel - an intro page (intro + found), one page per question the plan lists and shown_if admits given the answers so far, a summary page. Nav: Back, Skip, Next; Save and start on the summary only; Ask me next time on every page.
3. Skip becomes a button that records the skip and moves on; touching the question again clears it. Skip and Later text stays, smaller, under the question.
4. Closing the window asks first: nothing chosen is saved and the next start asks again.
5. Tests red first in tests/test_launcher_sitting.py: one page at a time, Back keeps answers, Skip moves on and records None, shown_if pages appear only when their condition holds, summary lists answers, Save and start only on the summary, closing asks. Adapt the fake-Tk harness (one nav bar, protocol, messagebox) and the existing tests to paging.
6. Real run: the frozen launcher is not rebuilt locally; run the source launcher's window against a scratch home, screenshot each page. Then release 0.7.1 per docs/RELEASING.md.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-27. Cause of "I cannot get through the setup": ask_setup gridded every question into one Toplevel with no scrollbar; with the real plan (library, library_folder, llm_provider, default_tier, watch_folder, start_at_login) the window ran past the bottom of the screen and "Save and start" was below it, so the close box - "ask me next time" - was the only way out. Robert closed it and the library he had named (D:\Data\MyScribe) was not adopted; the app started on a new empty library at D:\Data\MyScribe\data.
Built: pages in the same Toplevel - intro (intro text + found), one frame per question named q_<id>, a summary; nav Ask me next time | Back, Skip, Next, and Save and start on the summary only. shown_if decides the sequence at each step (ADR-015 unchanged; engine and contract untouched). Skip records the skip and moves on; answering again takes it back; a secret shows as "given" on the summary. The close box asks first (messagebox.askyesno).
Evidence: tests/test_launcher_sitting.py red first (13 failed, 53 passed), then 67 passed. test_launcher 103 passed 1 skipped, test_launcher_library 12, test_setup 15, test_setup_library 22, test_setup_plan 181, test_setup_prove 43. Mutants on a copy: 8 of 8 killed (skip-records-nothing survived first; test_skip_after_an_answer_drops_the_answer added and kills it). Real Tk run of the source ask_setup with the real plan from the installed 0.7.0 engine (read-only --plan), DPI-aware, walked by a script with a screenshot per step: intro 565x639, library 560x775 (tallest), provider 560x547, others 560x360, summary 560x412 physical px; every step shows its buttons; it handed over {"library": "D:\Data\MyScribe", "llm_provider": null, "default_tier": null, "watch_folder": null, "start_at_login": "no"}.
Not done: the found step is as long as the engine's found lines (criterion 2), and the skip explanation is the engine's full sentences in a smaller font, not one line (criterion 4) - both are the engine's text, not this renderer's.

2026-10-24, branch feat/task-098-shorter-found-and-skip, PR #89 (renderer only, ADR-015; scribe/ untouched, test_setup_plan 183 passed). AC2: found step one line per found thing plus one 'Not found:' line; this machine's plan 7 lines/453 chars -> 5/224, intro step 595 -> 420 px. Credentials, a watched folder and an enabled login entry are not asked again (engine, unchanged); a found library is still asked with its path as a choice - adopting it is consent, Robert agreed 2026-10-24. AC4: tallest step DPI-aware 150% 1084 -> 721 px (875 with Details open) under the 1008 px limit; 96 dpi 486 -> 392 (482). The skip line is 'If you skip: ' + the first sentence of the engine's if_skipped (Robert's choice 2026-10-24, after the reviewer found the generic 'Skip writes nothing' line contradicted the library step); the rest behind Details. A full stop after a lone letter is an initial, so C:\Users\J. Smith is not cut (red: 'If you skip: A new library is started at C:\Users\J.'). Green: test_launcher_sitting 79 passed; test_launcher + test_setup_plan 317 passed, 10 skipped. Independent reviews sound. Not verified: the frozen launcher; a physical 1080p panel (limits computed on 2560x1600 at 150%).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The setup window renders one question per screen with a short found step and one skip sentence per question taken from the engine's own if_skipped (details behind Details), renderer only per ADR-015. Tallest step 1084 -> 721 px at 150 % (875 with Details), fits the 1008 px 1080p limit. A found library is still asked (adoption is consent, Robert 2026-10-24). PR #89, merged; CI green on three OSes. Not verified: frozen launcher, a physical 1080p panel.
<!-- SECTION:FINAL_SUMMARY:END -->
