---
id: TASK-007.05
title: 'glossary: decode bias plus a reversible correction layer'
status: Done
assignee: []
created_date: '2026-09-02 16:34'
updated_date: '2026-09-03 13:20'
labels: []
dependencies: []
parent_task_id: TASK-007
ordinal: 48000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 6 Task 5. glossary.py with term CRUD, compose_hotwords moved from transcribe.py and extended with URL metadata terms, corrections_for using rapidfuzz WRatio then jellyfish metaphone for names, a correct stage writing word_correction rows without touching word.text, render and exporters applying the layer, settings UI with a library-wide re-run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A misspelling is corrected and a below-threshold word is left alone
- [x] #2 A user-edited word is never corrected
- [x] #3 Deleting the correction rows restores the original render exactly
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified 2026-09-03 by tests/test_glossary.py (111 passed with the watching suite; commits 544c17d, 36b1098). AC1 by test_a_word_below_both_thresholds_is_left_alone plus test_a_variant_spelling_maps_onto_the_canonical_term and test_punctuation_around_a_corrected_word_survives. AC2 by test_a_word_the_user_edited_is_never_corrected - it sets edited_by_user = 1 and asserts corrections_for returns [] - and by test_a_window_containing_an_edited_word_is_not_corrected, which extends the rule to a multi-word window rather than only the word itself. AC3 by test_deleting_the_corrections_restores_the_original_render_byte_for_byte, read in full because it is the whole promise: it captures both the transcript view and the export render before, applies corrections, asserts the rendered text actually changed - so the test cannot pass vacuously if corrections do nothing - then clears and asserts word texts, joined text, sentences and the export words all equal the originals. That is ADR-003 honoured: corrections are a layer over the canonical words, never a rewrite of them. Two review fixes rode along and both were real: 6571bdb stopped a subtitle ending early on a corrected phrase, and e77bb58 made search say which text it matched when corrections exist.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Glossary: decode bias plus a reversible correction layer over canonical words (commits 544c17d, 36b1098, 6571bdb, e77bb58). Verified by tests/test_glossary.py: a misspelling is corrected while a below-threshold word is left alone, a word the user edited is never touched, and deleting the correction rows restores both the transcript view and the export render exactly - checked against a before-snapshot that the test first proves had changed.
<!-- SECTION:FINAL_SUMMARY:END -->
