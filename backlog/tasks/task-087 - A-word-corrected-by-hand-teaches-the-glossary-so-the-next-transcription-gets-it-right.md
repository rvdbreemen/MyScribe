---
id: TASK-087
title: >-
  A word corrected by hand teaches the glossary, so the next transcription gets
  it right
status: Done
assignee:
  - '@claude'
created_date: '2026-09-19 07:45'
updated_date: '2026-09-19 07:51'
labels: []
dependencies: []
references:
  - scribe/glossary.py
  - scribe/web/transcript.py
  - >-
    docs/adr/ADR-003-words-are-the-canonical-transcript-every-grouping-is-derived-at-render-time.md
priority: high
ordinal: 135000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-19: when words are corrected, pick them up in the wordlist automatically, so the autocorrection during transcribing gets better over time from human feedback.

Everything needed is already here and nothing connects it. The vocab table holds a term, a weight and its variants; glossary.correct_word writes a hand-made correction as a row in word_correction keyed to (run_id, word_idx); compose_hotwords spends the terms before the decode and corrections_for spends them after it. On this library the table is empty, so both halves have nothing to work with - and every correction a person types is thrown away the moment the transcript is rendered.

A hand-made correction is the best evidence there is about a term: somebody read what Whisper produced, knew it was wrong, and typed what it should say. Vermulen corrected to Vermeulen is exactly the shape vocab wants - the term is what was typed, the variant is what Whisper heard - which is the same pair corrections_for matches on.

Not every correction is a term. The glossary own rule is that only capitalised words are names (compose_hotwords), and teh corrected to the must not become a hotword biasing every future decode toward a common word. ADR-003 also has to hold: a correction is never a rewrite, and learning from one cannot change word.text or make a correction harder to undo.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A hand-made correction whose new text looks like a name adds or updates a term in vocab, with what Whisper produced kept as a variant
- [x] #2 A correction that is not a name - lower case, a common word, punctuation only - teaches nothing
- [x] #3 Correcting the same word twice, or correcting to a term that already exists, does not duplicate a row or lose the variants already there
- [x] #4 Nothing about ADR-003 changes: word.text is untouched, and taking the correction away leaves the transcript byte for byte as Whisper produced it
- [x] #5 The user can see what was learned and remove it - a learned term is a glossary term like any other
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
glossary.correct_word now calls learn_from_correction, which is the whole feature: the term is what the person typed and what Whisper produced becomes a variant - the same pair corrections_for scores against, so the next recording is fixed by machinery that had nothing to work with before, and the one after that may not need fixing because the term is a hotword by then.

looks_like_a_name applies the glossary own existing rule to a new source: one word, at least two letters, starting with a capital. teh corrected to the teaches nothing.

One guard came out of the existing suite rather than out of the plan. Somebody types Vermeulen-Smit over one word; they mean that word. Keeping Vermulen as its variant would make every other Vermulen in the library come out hyphenated, including the ones an existing Vermeulen already fixes - one correction rewriting a whole library. The name still earns its hotword; the variant is dropped when another term already answers for that spelling, asked through corrections_for so the question is the one that will actually be asked later.

Nothing raises into the correction it follows: a glossary that cannot be taught is a worse day than a correction that fails to save. ADR-003 holds untouched - nothing here writes word.text, and lifting the correction off restores the transcript byte for byte, which a test asserts.

Suite 2486 passed. 10 new tests.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A word corrected by hand adds its name to the glossary with the mishearing as a variant, so the correction pass and the hotword bias both improve from human feedback. Only names are learned, a term somebody weighted keeps its weight, and a spelling another term already answers for is left alone - the guard that keeps one correction from rewriting a library.
<!-- SECTION:FINAL_SUMMARY:END -->
