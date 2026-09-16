---
id: TASK-071
title: >-
  clean_reading is a stored derivation keyed by run alone and never invalidated
  on an edit
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 16:49'
updated_date: '2026-09-16 16:53'
labels:
  - review-2026-09-16
  - architecture
  - adr-003
dependencies: []
priority: low
type: bug
ordinal: 116000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16 (confirmed by an adversarial second pass, severity low). A cleaned reading is written once per run (scribe/db.py:390, tasks.apply_cleanup) and shown by the transcript page under 'Cleaned for reading: N words became M. The transcript above is unchanged.' Correct a word - by hand, or by the glossary pass - and the pane still shows text cleaned from the words as they were, with nothing saying so. The verifier's correction: ADR-003's cache exception (keyed by run and rules version, recomputed on edit) is the wrong fix, because a reading is not a free recomputation - it is a paid model answer with a receipt. It must stay, and say honestly that the words changed after it was made.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A published reading records a fingerprint of the corrected words it was made from, and tasks.clean_reading reports stale when the run's corrected words no longer match it
- [x] #2 The transcript page says, next to a stale reading, that the words were edited after it was made and how to refresh it; a reading from before the fingerprint existed is not flagged
- [x] #3 Cleaning again after the edit makes the reading current
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: test_llm_cleaning_gate (stale after a glossary correction, current again after re-cleaning; a NULL-hash reading is not stale), test_web_transcript (page carries data-reading-stale and the sentence only for a mismatching hash), test_db (v17 adds words_hash).
2. Green: schema v17 ALTER TABLE clean_reading ADD COLUMN words_hash TEXT; tasks.words_fingerprint(words) = sha1 over the corrected word texts in idx order; plan_task computes it from the doc it loads and carries it on TaskPlan.words_hash; apply_cleanup stores it; clean_reading compares it with run_words_fingerprint(conn, run_id) (same SQL as the correction layer) and adds stale; the panel says the words were edited after the reading was made and how to refresh, keeping the text.
3. Run test_llm_cleaning_gate, test_llm_tasks, test_web_transcript, test_db one at a time; commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: test_a_reading_says_when_the_words_changed_after_it_was_made and test_a_reading_from_before_fingerprints_is_not_called_stale (KeyError: no stale), test_the_page_says_when_the_words_were_edited_after_the_reading (no such column words_hash), test_schema_v17 (SCHEMA_VERSION 16). Green after schema v17, TaskPlan.words_hash from the loaded doc, apply_cleanup storing it, clean_reading comparing it with run_words_fingerprint and the panel's data-reading-stale hint: test_llm_cleaning_gate 25, test_db 39, test_web_transcript 97, test_llm_tasks 120, test_llm_speakers 43, test_web_ai 107. Decision: not ADR-003's cache key - a reading is a paid answer with a receipt, so it stays and says the words moved; the fingerprint is taken when the doc is loaded, so a correction landing during the model call still marks the reading stale.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
clean_reading carries words_hash (v17), a sha1 over the corrected word texts the cleaning was cut from; tasks.clean_reading adds stale by comparing it with the run's corrected words now, and the transcript panel says the words were edited after the reading was made and how to refresh it, keeping the text. Readings without a hash are never stale. Verified red-to-green by four tests and 431 tests across six files.
<!-- SECTION:FINAL_SUMMARY:END -->
