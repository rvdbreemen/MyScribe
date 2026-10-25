---
id: "ADR-040"
title: "A note over its count, or cut off at its budget, is asked once more and then thinned in code"
status: "Proposed"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "llm"
  - "map-reduce"
  - "notes"
aliases:
  - "note count bound"
  - "at most 30 notes"
  - "MAX_NOTE_POINTS"
  - "second ask"
components:
  - "scribe/llm/tasks.py"
symbols:
  - "MAX_NOTE_POINTS"
  - "bounded_points"
  - "note_points"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-040 A note over its count, or cut off at its budget, is asked once more and then thinned in code

## Status

Proposed, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: Initial proposal
    changed_via: adr-kit
```

## Context and Problem Statement

A recording too long for one call is read in chunks, with notes per chunk and
one combine call over all of them. Blog, minutes, action items and chapters
ask their notes as "at most 30 notes" (TASK-124). On media 61 (2h28m, Dutch,
round-8 library copy, 2026-10-25) the request did not hold: `qwen3.5:9b` wrote
85 minutes notes on one chunk, and `qwen3.5:4b` wrote chapters fragments until
its 4000-token note budget ran out at 31:15 of a 60-minute chunk, so the job
failed. Wanted (TASK-126): the count held in code, so a chunk's notes cannot
outgrow the combine, and the chunk still read to its end.

## Decision Drivers

* The notes must reach the chunk's last minutes, not stop where the count did.
* No stored note may be cut mid-sentence (the refusal TASK-124 kept).
* The same rule on every provider; `speakers` keeps its quotes, unbounded.

## Considered Options

* **A.** Keep the first 30 lines of the reply.
* **B.** Ask once more, saying what the first notes did; a second reply still
  over the count keeps 30 lines spread evenly over it, first and last kept.
* **C.** An Ollama JSON schema with `maxItems: 30` on the notes.

## Decision Outcome

Chosen option: **B**. A keeps the first third of an 85-note chunk and drops
the rest; it cannot help a note cut off at its budget either, which never
wrote its second half. C applies to Ollama only, turns prose notes into JSON,
and bounds the count without making the model cover the excerpt. B gives the
model one chance to obey (as ADR-023 and ADR-029 ask once more), then holds
the bound itself without losing the chunk's end. Each kept line is untouched,
so it keeps its `[m:ss]`. A finished first note is never lost to its second
ask: when that one comes back empty or cut off, the first is kept and thinned.
Only a note cut off on both asks is refused, as before.

### Confirmation

Unit tests with a scripted provider, and one live chapters run on media 61
with `qwen3.5:4b` on a library copy (TASK-126, job 430 at ab378fa): done in
408 s where TASK-124's job 433 failed. Chunk 0's first ask stopped at the
4000-token budget; the second finished with 116 points, thinned to 30, the
last at 57:12 of a chunk ending 1:00:16. That run did not take the path where
the first note is kept.

## Decision Contract

### Must

* Count a note's points as its non-empty lines, by `note_points`.
* Ask a kind with a `note_rule` once more when its note has more than
  `MAX_NOTE_POINTS` points or stopped at its budget.
* Thin a note still over the count with `bounded_points`.
* Keep a finished first note when its second ask is empty or cut off.
* Render the number in every note rule from `MAX_NOTE_POINTS`.

### Must Not

* Keep only the first `MAX_NOTE_POINTS` lines.
* Store a note that stopped at its budget, asked again or not.
* Fail a chunk whose first note finished because its second ask did not.
* Ask or thin the notes of a kind without a `note_rule`.

### Exceptions

* None.

### Verification

* `tests/test_llm_tasks.py::test_an_85_note_chunk_is_asked_again_and_then_bounded`
* `tests/test_llm_tasks.py::test_a_note_cut_off_at_its_cap_is_asked_again_and_the_finished_one_stored`
* `tests/test_llm_tasks.py::test_bounded_points_spreads_the_kept_lines_over_the_whole_note`
* `tests/test_llm_tasks.py::test_a_finished_note_over_the_bound_is_kept_when_the_second_ask_fails`

## Consequences

### Positive

* A combine never reads more than 30 points a chunk, and the points it reads
  span the whole chunk.
* A note cut off at its budget gets a second chance before the job fails.

### Negative

* A disobeying chunk costs a second call (time not measured on its own).
* Thinning drops points by position, not by weight: a decision can be lost.
  The second ask exists so that this is the rare path.

## Related Decisions

* ADR-023 and ADR-029 (an answer lost to reasoning is asked once more);
  ADR-031 (truncation refused); ADR-034 (Ollama answers follow a schema).

## References

* TASK-124 round 8 and TASK-126 (session scratchpad evidence, not kept in the
  repo); `scribe/llm/tasks.py` (`_collect_notes`).
