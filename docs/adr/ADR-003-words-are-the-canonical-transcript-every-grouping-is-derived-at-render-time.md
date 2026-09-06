---
id: "ADR-003"
title: "Words are the canonical transcript; every grouping is derived at render time"
status: "Accepted"
date: "2026-09-03"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
topics:
  - "data-model"
  - "transcripts"
  - "exports"
aliases:
  - "words canonical"
  - "derived segmentation"
  - "no resegment button"
components:
  - "scribe.db"
  - "scribe.stages.transcribe"
  - "scribe.stages.attribute"
  - "scribe.render"
symbols:
  - "word"
  - "segment"
  - "speaker_label"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-003 Words are the canonical transcript; every grouping is derived at render time

## Status

Accepted, 2026-09-03.

## Status History

```yaml
status_history:
  - date: 2026-09-02
    status: Proposed
    changed_by: Claude Fable 5.1 (agent)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-09-03
    status: Accepted
    changed_by: "User: Robert van den Breemen"
    reason: "Accepted by Robert in an interactive session on 2026-09-03. Verified against the code: sixteen tables and none of them a grouping, the only UPDATE word SET statements touch speaker and never text (finalize.py:146, transcript.py:416), and grouping happens in render.py and the exporters. Measured: 6,477 words group into 125 paragraphs in 21 ms. The derived-output cache exception was kept on Robert's choice, now carrying that measurement and the reason it exists - to prescribe the key if a cache is ever needed."
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

The usual design stores segments with the text baked into them, and the
consequence is documented wherever it is used:
an edited or speaker-labelled transcript loses custom segmentation until the
user presses "Resegment". The stored grouping goes stale the moment anything
else changes. Whisper produces word-level timestamps and probabilities anyway;
most tools throw them away.

## Decision Drivers

* A rename, a speaker reassignment, or a new subtitle preset must never
  require re-running a model.
* Exports must be reproducible from stored data alone, in seconds, for the
  whole library.
* Confidence must survive to the UI (user interface).

## Considered Options

* Words as the canonical unit; paragraphs, sentences and cues computed at
  render/export time.
* Segments as the canonical unit, which is what most tools do.
* Store both and keep them in sync.

## Decision Outcome

Chosen option: **words are canonical**. The `word` table holds start, end,
text, probability, speaker cluster and an `edited_by_user` flag per word.
`segment` rows are kept as Whisper's own immutable output for a run — they
carry the FTS (full-text search) index and the per-segment confidence fields — but they are source
data, not user-facing segmentation. Speaker display names live in
`speaker_label` and are applied when rendering; renaming never rewrites text.
Paragraphs, sentences and subtitle cues are pure functions over words
(`scribe/render.py` and the exporters). A cache of such output may exist only
keyed by (run, rules version) and invalidated on any edit.

### Confirmation

`tests/test_pipeline_e2e.py` asserts a finished job persists words (with
probabilities and monotonic timestamps) and segments, and nothing grouped.
`tests/test_attribution.py` proves the speaker join labels words only.
`tests/test_render.py` (Phase 3) covers paragraph and sentence derivation.

## Decision Contract

### Must

* Persist every word with its probability and speaker.
* Compute paragraphs, sentences and cues from words at render/export time.
* Apply speaker display names from `speaker_label` at render time.

### Must Not

* Create a table for paragraphs, cues, sentences, or any other grouping as a
  source of truth.
* Rewrite word or segment text on rename or reassignment.
* Offer a "Resegment" action — there is nothing to resegment.

### Exceptions

* A cache of derived output is allowed, keyed by (run id, rules version) and
  invalidated on edit. None exists today and none is needed: measured on
  2026-09-03, grouping 6,477 words into 125 paragraphs takes 21 ms, and
  `render.py` says in its own docstring that there is nothing stored that
  could go stale. The exception is here to prescribe the key if a ten-hour
  transcript ever makes one worth having - a cache keyed on anything less
  is how the staleness this decision exists to prevent comes back.

### Verification

* `grep -n "create table" -i scribe/db.py` lists no paragraph, cue or
  sentence table.
* `tests/test_pipeline_e2e.py`, `tests/test_attribution.py`, `tests/test_render.py`.

## Consequences

### Positive

* Re-exporting the whole library after a rename costs seconds and no GPU
  (graphics processing unit) time at all.
* Confidence bands, word-level highlighting and subtitle rules are all cheap.
* The staleness class of bug cannot exist.

### Negative

* Rendering a ten-hour transcript groups roughly 100k words on every page
  load. Measured at the scale that exists: 6,477 words take 21 ms, so ten
  hours is on the order of 300 ms - noticeable, not a problem. The browser
  side is handled by `content-visibility: auto` (`scribe/static/app.css:430`),
  and the permitted cache is there if it ever stops being enough.

## Pros and Cons of the Options

### Words canonical

* Good, because every grouping rule is a function, testable without a GPU.
* Bad, because more rows: roughly 10k words per hour of audio. SQLite does not
  care.

### Segments canonical

* Good, because it is what Whisper emits.
* Bad, because it is exactly the model that produces the documented lockout
  above and makes subtitle presets a re-run.

### Store both, keep in sync

* Bad, because "keep in sync" is the bug generator this ADR exists to avoid.

## Open Questions

- [x] Should the derived-output cache exception stay, given that no cache exists? — **Answered 2026-09-03 by User: Robert van den Breemen:** Yes, it stays. Robert chose this on 2026-09-03. Unlike ADR-001's CPU-prework exception - which described machinery that existed and was never called - this one grants permission for something deliberately not built, and its value is the key it prescribes. Measured today: grouping 6,477 words takes 21 ms, so a cache buys nothing at present scale; render.py states there is nothing stored that could go stale. If a ten-hour transcript ever makes one worthwhile, the exception is what stops the next implementer from keying it on the run alone and reintroducing the staleness this decision exists to prevent.

## Related Decisions

* ADR-002 (where the rows live).

## References

* `scribe/db.py` schema v1; `scribe/stages/transcribe.py`, `scribe/stages/attribute.py`.
* `docs/superpowers/specs/2026-09-01-myscribe-design.md` §2.

## Enforcement

```json
{
  "forbid_import": [],
  "forbid_pattern": [
    {"pattern": "CREATE TABLE (paragraph|cue|sentence|caption)", "path_glob": "scribe/**", "message": "Grouping is derived from words at render time, never stored (ADR-003)."},
    {"pattern": "UPDATE (word|segment) SET text", "path_glob": "scribe/**", "message": "Rename and reassignment never rewrite transcript text (ADR-003)."}
  ],
  "require_pattern": []
}
```
