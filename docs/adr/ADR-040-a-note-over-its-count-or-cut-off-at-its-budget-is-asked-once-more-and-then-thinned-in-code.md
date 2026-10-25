---
id: "ADR-040"
title: "A note over its count, or cut off at its budget, is asked once more and then thinned in code"
status: "Accepted"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
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
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-040 A note over its count, or cut off at its budget, is asked once more and then thinned in code

## Status

Accepted, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-25
    status: Proposed
    changed_by: Claude (agent, session 2026-10-25)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-25
    status: Accepted
    changed_by: Robert van den Breemen (said yes in session 2026-10-25; recorded by Claude)
    reason: Robert accepted ADR-040 in the session of 2026-10-25
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

A recording too long for one call is read in chunks, with notes per chunk and
one combine call over all of them. Blog, minutes, action items and chapters
ask their notes as "at most 30 notes" (TASK-124). On media 61 (2h28m, Dutch,
round-8 library copy, 2026-10-25) the request did not hold: `qwen3.5:9b` wrote
85 minutes notes on one chunk, and `qwen3.5:4b` wrote chapters fragments until
its 4000-token note budget ran out at 31:15 of a 60-minute chunk; the job
failed. Wanted (TASK-126): the count held in code, each chunk read to its end.

## Decision Drivers

* Notes reach the chunk's last minutes; none is stored cut off (TASK-124).
* The same rule on every provider; `speakers` keeps its quotes, unbounded.

## Considered Options

* **A.** Keep the first 30 lines of the reply.
* **B.** Ask once more, saying what the first notes did; a second reply still
  over the count keeps 30 points spread evenly over it, first and last kept.
* **C.** An Ollama JSON schema with `maxItems: 30` on the notes.

## Decision Outcome

Chosen option: **B**. A keeps the first third of an 85-note chunk and drops
the rest; it cannot help a note cut off at its budget either, which never
wrote its second half. C applies to Ollama only, turns prose notes into JSON,
and bounds the count without making the model cover the excerpt. B gives the
model one chance to obey (as ADR-023 and ADR-029 ask once more), then holds
the bound itself without losing the chunk's end. A point is a stamped line
and the unstamped lines under it; text before the first stamp is a lead-in,
not a point; a note stamped on under a third of its lines counts by line.
Kept points are untouched, so a stamped point keeps its `[m:ss]`. A finished
first note is never lost to its second ask (empty or cut off): it is kept and
thinned. A cut-off note whose second ask is cut off or empty is refused.

### Confirmation

Unit tests, and live runs on media 61 with `qwen3.5:4b` on library copies.
Chapters (ab378fa): done in 408 s where TASK-124's job 433 failed; chunk 0's
second ask wrote 116 points, thinned to 30, the last at 57:12 of 1:00:16. The
notes reached each chunk's end; the result did not: all 11 chapters started
by 54:40 of 2:28:04, so they are not usable chapters. Minutes (5138248): done
in 483 s; chunk 1 wrote 34 points, 39 when asked again, thinned to 30.

## Decision Contract

### Must

* Count points by stamp with `note_points`, a stamped line with its sub-lines.
* Ask a kind with a `note_rule` once more when its note has more than
  `MAX_NOTE_POINTS` points or stopped at its budget.
* Thin a note still over the count with `bounded_points`.
* Keep a finished first note when its second ask is empty or cut off.
* Render the number in every note rule from `MAX_NOTE_POINTS`.

### Must Not

* Keep only the first `MAX_NOTE_POINTS` points.
* Store a note that stopped at its budget, asked again or not.
* Fail a chunk whose first note finished because its second ask did not.
* Ask or thin the notes of a kind without a `note_rule`.

### Exceptions

* None.

### Verification

* `tests/test_llm_tasks.py::test_an_85_note_chunk_is_asked_again_and_then_bounded`
* `tests/test_llm_tasks.py::test_a_note_cut_off_at_its_cap_is_asked_again_and_the_finished_one_stored`
* `tests/test_llm_tasks.py::test_a_finished_note_over_the_bound_is_kept_when_the_second_ask_fails`
* `tests/test_llm_tasks.py::test_forty_two_line_points_are_thinned_by_point_not_by_line`
* `tests/test_llm_tasks.py::test_one_point_over_the_bound_is_asked_again_and_cut_to_it`

## Consequences

### Positive

* A combine never reads more than 30 points a chunk, and the points it reads
  span the whole chunk.
* A note cut off at its budget gets a second chance before the job fails.

### Negative

* A disobeying chunk costs a second call, paid for on a cloud provider (time
  not measured); its row names the unstored ask's tokens in `other_ask`.
* Thinning drops points by position, not by weight: a decision can be lost.
  It is not rare: in both live runs a count-over's second ask stayed over 30
  (116, 39) and thinning held the bound; only a cut-off note gained from it.

## Related Decisions

* ADR-023, ADR-029 (an answer lost to reasoning is asked once more); ADR-031
  (Ollama never truncates the prompt); ADR-034 (Ollama answers follow a
  schema). Refusing a cut-off note is TASK-124's code, not an ADR.

## References

* TASK-124 round 8 and TASK-126 (session scratchpad evidence, not kept in the
  repo); `scribe/llm/tasks.py` (`_collect_notes`).
