---
id: TASK-077
title: >-
  Every plain-form save on the Settings page redirects to a card the user cannot
  see
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 17:30'
updated_date: '2026-09-16 17:37'
labels:
  - review-2026-09-16
  - web
dependencies: []
priority: low
type: bug
ordinal: 122000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass; severity low; the verifier names the root cause as code against TASK-011's own promise). The Settings page shows one category at a time: a radio group outside the partials and :has() rules in app.css, so it works without script, and the server honours ?section= to open a category by URL (TASK-011 AC #2). The plain-form answers - what a browser gets when scripting is off or before htmx loads - redirect to /settings#watch-folders, /settings#glossary, /settings#llm-providers or bare /settings. A fragment cannot check a radio, so the page opens on Defaults with the saved card display:none behind it; the person who pressed 'Watch this folder' sees the Defaults form and no sign their folder was added.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every plain-form save on the Settings page redirects to /settings?section=<its category> (with the card's anchor), so the browser lands on the card that was saved
- [x] #2 A test posts each such form without htmx headers and pins the Location
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_web_settings - plain posts to /settings, /settings/doctor, /settings/watch, /settings/glossary, /settings/llm/<p>/key and /settings/presets pin a Location of /settings?section=<key>#<anchor>; the two existing tests that pinned bare /settings move with it (test_web_settings, test_web_exports).
2. Green: settings._back_to(section) builds the redirect from SECTIONS and the card anchors; every plain-form branch uses it.
3. Run test_web_settings, test_web_exports, test_glossary, test_ingest_watching one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: five plain-post tests failed on Location (bare /settings or a fragment). Green after settings._back_to and its six call sites: test_web_settings 41, test_web_exports 52, test_glossary 69, test_ingest_watching 82. The two tests that pinned bare /settings moved with the fix.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every plain-form save on Settings answers 303 to /settings?section=<its category>#<its card>, so the radio the server checks from ?section= opens the saved card. Verified red-to-green by six tests across three files.
<!-- SECTION:FINAL_SUMMARY:END -->
