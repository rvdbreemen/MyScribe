---
id: TASK-015
title: >-
  Transcript editor: double-click a word to correct it, and offer the same fix
  for every occurrence
status: Done
assignee:
  - '@claude'
created_date: '2026-09-06 19:16'
updated_date: '2026-09-06 20:51'
labels: []
dependencies: []
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
In the transcript view the player highlights words as they play, but a misheard word cannot be fixed in place. Robert asked (2026-09-06) for a double-click (or right-click) on a word that opens a small inline field, applies the correction to that word, and then offers to replace the same word everywhere in this transcript. ADR-003: words are canonical and corrections are a layer (word_correction, as the glossary pass writes), so this writes correction rows, never word.text.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Double-clicking a word in the transcript view opens an inline field prefilled with the word; Enter saves, Escape cancels
- [x] #2 The correction is stored as a word_correction row for that word_idx and the view re-renders with it
- [x] #3 After saving, the page offers 'Replace N other occurrences' and applies the same correction to every word with the same text in this run
- [x] #4 Exports and search use the corrected text, as they do for glossary corrections
- [x] #5 Tests cover the route and the replace-all count; a screenshot of the inline editor is in the notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. glossary: RULE_MANUAL, store() keeps manual rows and skips words that have one; correct_word / same_word / correct_same. 2. Route POST /media/{id}/words/{idx}/correct with scope one|all; scope one answers the panel plus an offer naming the other occurrences. 3. Panel: template for the inline form, offer bar; app.js dblclick/contextmenu opens it, Escape closes. 4. Tests at glossary and web level; browser run.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Browser (Playwright Chrome on 4299, media 1): dblclick on 'ORGA' opened the form prefilled 'ORGA' at /media/1/words/2/correct; saving 'ORGA-team' rendered title 'Edited: was ORGA' and the offer '1 other word … Replace it too'; pressing it: '2 words now say ORGA-team', both words carry data-was. Test edits removed from the database afterwards. Tests: test_glossary.py 69 passed, test_web_transcript.py + test_exports_doc.py 118 passed together. Search still matches segment_fts, i.e. the original spelling, as the glossary's own note already says. Commit above.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Inline word correction as a manual row in word_correction, with a replace-all offer; the glossary pass no longer touches hand-made rows. Verified by seven new tests and a browser run.
<!-- SECTION:FINAL_SUMMARY:END -->
