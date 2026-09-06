---
id: TASK-011
title: >-
  Settings page: category sidebar, one clean section at a time, explicit Save
  where a change persists
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 11:50'
updated_date: '2026-09-06 11:58'
labels:
  - web
  - ui
dependencies: []
ordinal: 52000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The settings page stacks every panel (defaults, LLM providers, export presets, watch folders, glossary, doctor) in one long scroll. Wanted: a left navigation of setting categories; clicking one shows that category's section on the right, clean and alone; every section that stores something has a Save (or confirm) button rather than saving on change. Keep the htmx self-refreshing panels and their routes; the switch must work without script (radio + :has(), like the job tabs) and deep-link by URL hash or query.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 sidebar lists every category; exactly one section visible at a time; switching needs no script
- [x] #2 a section is reachable by URL (query or hash) and the chosen one survives a panel refresh
- [x] #3 every persisting form has an explicit Save/confirm; nothing saves on change without a button
- [x] #4 keyboard: categories focusable, focus ring visible, section headings h2
- [x] #5 tests: sidebar entries, one-visible rule, deep link, save buttons present; screenshot before/after in docs/design
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. settings.py: SECTIONS (key, label, hint) + opening_section(request) reading ?section=. 2. settings.html: radios for the six categories OUTSIDE every partial (partials re-fetch themselves on save), a sidebar of labels, one wrapper div per category; This machine groups doctor + models + storage. 3. app.css: :has() rules light the active link and show the matching card; sections styled as cards; focus ring on the label the hidden radio names. 4. app.js: on radio change write ?section= with replaceState (courtesy only). 5. Tests: sidebar entries, one checked, radios not in partials, deep link + fallback, every form has a submit and nothing posts on change, a Save answers with the partial alone.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified in the browser on 4242: one section visible at a time (defaults on open; llm after clicking its label), URL becomes ?section=llm, ?section=machine opens the machine card with Doctor/Models/Storage as three cards, focus ring on the label when the hidden radio is focused, page fits one screen (1000 px). Tests: web_settings 33 passed (4 new). Every persisting form already had its own button; nothing posts on change (asserted). Screenshots docs/design/17-settings-before.png (the old 4500 px scroll), 18-settings-after.png, 19-settings-machine.png.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Settings is a sidebar of six categories with one card open on the right: radios outside the self-refreshing partials drive :has() rules, so a Save on one card keeps you on it; ?section= deep-links and app.js keeps the URL in step. Saving stays explicit per form. Verified by 4 new tests (33 passing) and browser checks with screenshots.
<!-- SECTION:FINAL_SUMMARY:END -->
