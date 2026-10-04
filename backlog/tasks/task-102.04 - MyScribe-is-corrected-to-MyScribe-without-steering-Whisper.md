---
id: TASK-102.04
title: 'MyScribe is corrected to MyScribe, without steering Whisper'
status: Done
assignee:
  - '@claude'
created_date: '2026-10-04 05:27'
updated_date: '2026-10-04 05:46'
labels:
  - transcription
dependencies: []
parent_task_id: TASK-102
ordinal: 180000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The app's own name came back as MiScribe; the glossary was empty. Robert, 2026-10-04: a built-in term for the correction pass only, not for the hotwords, so a recording that never says the name is not biased towards it. Report section 3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A transcript word MiScribe (and My Scribe) gets a correction row to MyScribe through the existing correction layer (ADR-003), with no glossary row needed, red first
- [x] #2 compose_hotwords does not contain MyScribe unless a user's own glossary has it; a test says so
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Measured first: a plain Term('MyScribe') through corrections_for also turns 'scribe' and 'Scribe' into MyScribe and makes 'My Scribe' into 'My MyScribe', so a built-in term on the fuzzy rules is too coarse for something nobody chose. So: glossary.built_in_corrections(words), an exact list of the spellings Whisper produces for the app's name (MiScribe, Myscribe, Mi Scribe, My Scribe; capitalised two-word forms only), rule 'built-in', punctuation kept via _replacement. The correct stage merges it under the user's glossary: a word the glossary already corrects is not touched. Hotwords unchanged. Red first in tests/test_glossary.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Measured before choosing: Term('MyScribe') through corrections_for also corrects 'scribe' and 'Scribe' and turns 'My Scribe' into 'My MyScribe' (it picks the one-word window 'Scribe'). That last one is a quirk of the existing fuzzy rules for any user term whose first part is a common word; not fixed here, noted for a follow-up. Built-in = exact, case-sensitive spellings (MiScribe, Myscribe, Miscribe, Mi Scribe, My Scribe), rule 'built-in', below the user's glossary (with_built_ins skips a match any glossary correction touches). Red: AttributeError, then the stage test 'Een opname voor MiScribe.' != '... MyScribe.'; the hover-label test fails with the old template (stash). Green: test_glossary.py 75, test_glossary_learning.py 10, exports 233 passed. Hotwords: compose_hotwords with an empty glossary has no MyScribe (test). The transcript hover says 'App name: was ...' instead of 'Glossary'.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The correct stage now also fixes the app's own name where Whisper spelled it MiScribe, Myscribe, Miscribe, Mi Scribe or My Scribe - exact spellings, a correction row with rule 'built-in' (ADR-003, reversible), below anything in the user's glossary, and never a hotword. The transcript labels it 'App name'. Red then green in tests/test_glossary.py.
<!-- SECTION:FINAL_SUMMARY:END -->
