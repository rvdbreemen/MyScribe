---
id: TASK-068
title: >-
  An AI card's two-second poll wipes the transcript's find highlights and its
  word selection
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 15:26'
updated_date: '2026-09-16 16:03'
labels:
  - review-2026-09-16
  - web
dependencies: []
priority: medium
type: bug
ordinal: 113000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass, which noted it is worse than first stated). app.js listens for htmx:afterSwap on document.body and, whenever a #transcript-panel exists anywhere on the page, calls clearSearch() and sets anchor = -1 (scribe/static/app.js:1665-1672). The AI region sits outside the transcript panel and every one of its cards polls with hx-trigger every 2s, targeting #ai-{kind} or #ai-region. So while an AI answer is pending, the reader's find highlights are stripped every two seconds - the query and the match count stay on screen, which makes it read as a bug in the search - and the shift-click anchor is thrown away, so the next shift-click selects from the wrong place. The listener checks whether the panel exists, not whether it was the thing that was replaced.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A swap whose target is outside the transcript panel leaves the find highlights and the anchor alone
- [x] #2 A swap of the transcript panel itself still clears both, because its words are new nodes
- [x] #3 The test drives the real app.js through the DOM harness rather than asserting on its source text
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rode test in tests/test_web_transcript.py via run_dom: pagina met #transcript-panel en een AI-kaart, markeringen zetten, htmx:afterSwap met detail.target op de kaart.
2. Fix in app.js: kijken of detail.target het paneel is of bevat, in plaats van of het paneel bestaat.
3. Draai tests/test_web_transcript.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in scribe/static/app.js (afterSwap-listener rond 1665): de listener leest event.detail.target en reset alleen wanneer dat het transcriptpaneel is of het bevat. Een swap zonder doelinformatie houdt het oude, conservatieve gedrag.

Over criterium 1: bewezen via het anker. Klik woord 0, swap op de AI-kaart, shift-klik woord 2: voor de fix 1 geselecteerd woord (anker weg), na de fix 3. De zoekmarkeringen zijn in dit harnas niet waarneembaar - de stub bouwt bewust geen tekstnodes en de zoekcode loopt daar volledig op. Structureel dekt dezelfde vroege return beide, want hij staat voor zowel clearSearch() als anchor = -1; waargenomen is alleen het anker.

Het DOM-harnas kreeg onderweg drie uitbreidingen die dit uberhaupt testbaar maakten: descendant-selectors, tagnamen met een cijfer (h3.speaker) en classList.toggle. Vier eerdere testpogingen faalden op die grenzen, niet op het gedrag.

Rood: assert 1 == 3 - de swap van de AI-kaart gooide het anker weg.
Groen: gerichte test 1 passed; tests/test_web_transcript.py 96 passed; harnasgebruikers tests/test_web_url_dialog.py 90, test_web_recorder.py 24, test_ingest_recording.py 39, test_web_settings.py 37 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Een swap van een AI-kaart, die elke twee seconden buiten het transcriptpaneel gebeurt, wist niet langer de woordselectie en de zoekstatus van de lezer; alleen een vervanging van het paneel zelf doet dat nog. Geverifieerd door de echte app.js in het DOM-harnas te draaien: eerst rood op 1 in plaats van 3 geselecteerde woorden na een shift-klik, daarna groen, plus 96 + 90 + 24 + 39 + 37 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
