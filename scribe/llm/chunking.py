"""Fitting a transcript in a context window, on boundaries Whisper already drew.

An hour of speech is roughly thirteen thousand tokens, so most transcripts fit
in one call to a modern model and the interesting case is the one that does
not: a four-hour recording, or any recording at all in front of a local 4B
model whose window is eight thousand tokens. This module answers two questions
for those - "does it fit?" and "if not, where do the seams go?" - and answers
nothing else. It takes a `TranscriptDoc`, not a connection; it makes no
request; it decides nothing about prompts. That keeps it testable as
arithmetic, which is the only way the properties below can be *asserted*
rather than hoped for.

**The estimate is deliberately pessimistic**: three characters per token, at
least one token per whitespace-separated word - the same budget
`stages/transcribe.estimate_tokens` uses for hotwords, and for the same
reason. Real BPE averages closer to four characters, so this over-counts by
roughly a fifth. That asymmetry is chosen: erring long costs one extra chunk
boundary, and erring short costs a failed call after the money has been spent
and, on Ollama, a *silently truncated* prompt answered as though it were whole
(see `ollama._answer`). The count is duplicated rather than imported because
the hotword copy lives in a GPU stage module that loads Whisper machinery;
this one has to be importable anywhere, and the two budgets are free to drift
apart if Whisper's tokenizer and a chat model's ever disagree.

Being additive is the property everything here rests on: because the estimate
counts whitespace-separated words, and lines are joined with a newline, the
estimate of a chunk is exactly the sum of the estimates of its segments. So
`plan` can accumulate per-segment costs and know the total is the truth about
the text it is building, without re-counting a growing string on every step.

**The seams are segment boundaries, never anything else.** Whisper's segments
are its own view of where the speech pauses; cutting inside one would hand a
model half a sentence and get half an answer. A segment that is on its own
bigger than the whole budget is emitted alone and marked `oversized` rather
than split or raised over: a caller can then choose a bigger window, a
different provider, or to send it anyway - and one runaway segment does not
make a transcript unsummarisable.

**The plan is deterministic**, and its `index` values are `0..n-1` with no
gaps. Task 4 stores each chunk's answer as `llm_output` with
`kind=f"{kind}:chunk:{i}"` and resumes an interrupted job by making only the
calls whose rows are missing. That resume is only correct if the same document
and budget always plan the same way and the numbering always starts at zero,
so both are contracts of this module rather than accidents of the loop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scribe import render
from scribe.exports.doc import TranscriptDoc

CHARS_PER_TOKEN = 3
"""Pessimistic on purpose; see the module docstring."""

MAP_REDUCE_PROMPTS: dict[str, str] = {"chunk": "map_chunk", "combine": "map_combine"}
"""The two templates a chunked task needs beyond its own: one that answers
about a single chunk, one that combines those answers into the final output.
Named here so `tasks.py` reads them rather than spelling the strings in the
one place a rename would not reach."""


def estimate_tokens(text: str) -> int:
    """A pessimistic token count: three characters a token, one token minimum
    per whitespace-separated word.

    Additive over a whitespace join, which is what `plan` relies on:
    `estimate_tokens(a + "\\n" + b) == estimate_tokens(a) + estimate_tokens(b)`.
    """
    return sum(max(1, math.ceil(len(word) / CHARS_PER_TOKEN)) for word in text.split())


def timestamped_line(segment: dict, speaker: str | None = None) -> str:
    """One segment as `[m:ss] text`, or `[m:ss] SPEAKER_00: text` with a speaker.

    The timestamp is `render.format_ts`'s clock style - the same string the
    transcript view and every export print - so a model asked to cite a moment
    produces something the player's existing seek handler already understands.
    The speaker, when asked for, is the raw cluster label and never the display
    name: the task that wants it is the one working out what the name *is*.
    """
    who = f"{speaker}: " if speaker else ""
    return f"[{render.format_ts(float(segment['start']))}] {who}{segment['text']}"


def segment_speakers(doc: TranscriptDoc) -> list[str | None]:
    """The speaker of each segment: the cluster most of its words carry.

    Segments are Whisper's and know nothing of speakers; words carry the
    cluster the attribute stage joined on. A segment is credited to whichever
    cluster has the most words inside its span, ties to the earlier label, and
    None when no word in it has one - so an undiarized run gets plain lines.
    """
    words = doc.words
    out: list[str | None] = []
    cursor = 0
    for segment in doc.segments:
        start, end = float(segment["start"]), float(segment["end"])
        while cursor < len(words) and float(words[cursor]["end"]) <= start:
            cursor += 1
        counts: dict[str, int] = {}
        i = cursor
        while i < len(words) and float(words[i]["start"]) < end:
            label = words[i].get("speaker")
            if label:
                counts[label] = counts.get(label, 0) + 1
            i += 1
        out.append(max(sorted(counts), key=counts.__getitem__) if counts else None)
    return out


def transcript_lines(doc: TranscriptDoc, *, speakers: bool = False) -> list[str]:
    """One timestamped line per segment, with the cluster label when asked."""
    if not speakers:
        return [timestamped_line(segment) for segment in doc.segments]
    return [
        timestamped_line(segment, who)
        for segment, who in zip(doc.segments, segment_speakers(doc))
    ]


def transcript_text(doc: TranscriptDoc, *, speakers: bool = False) -> str:
    """The whole transcript, one timestamped line per segment.

    The text a chunk holds when the document fits in one, and the thing
    `needs_chunking` measures - so the question "does it fit" is asked about
    the exact string that would be sent, prefixes included.
    """
    return "\n".join(transcript_lines(doc, speakers=speakers))


@dataclass(frozen=True)
class Chunk:
    """One call's worth of transcript.

    `segment_ids` are the run-scoped `segment.idx` values, which is what
    `TranscriptDoc` carries - **not** `segment.id` rowids, which is what an
    FTS5 hit over `segment_fts` gives you. The two number spaces look alike and
    are not; anything joining chunk ids to database rows has to go through the
    run.

    `start`/`end` cover the segments in this chunk, overlap included, so the
    range describes the text that was actually sent. `tokens` is this module's
    estimate of `text`, not a count from a provider.
    """

    index: int
    start: float
    end: float
    text: str
    segment_ids: tuple[int, ...]
    tokens: int
    oversized: bool = False


def needs_chunking(doc: TranscriptDoc, budget_tokens: int, *, speakers: bool = False) -> bool:
    """Would the whole timestamped transcript exceed `budget_tokens`?

    The budget is what is left for the transcript *after* the system prompt,
    the instructions and the room the answer needs - the caller subtracts
    those, because only the caller knows which prompt it is about to use.
    """
    return estimate_tokens(transcript_text(doc, speakers=speakers)) > budget_tokens


def plan(
    doc: TranscriptDoc,
    *,
    budget_tokens: int,
    overlap_segments: int = 1,
    speakers: bool = False,
) -> list[Chunk]:
    """Cut `doc` into chunks of at most `budget_tokens`, on segment boundaries.

    Greedy and single-pass: fill a chunk with whole segments until the next one
    would not fit, emit it, then start the next chunk `overlap_segments`
    segments back so a sentence that straddles the seam is visible from both
    sides. A document under budget yields exactly one chunk holding
    `transcript_text(doc)`; a document with no segments yields no chunks at all,
    so a caller can distinguish "nothing to say anything about" from "one empty
    answer".

    The next chunk always starts at least one segment further on, whatever the
    overlap - an overlap wider than the chunk it follows would otherwise plan
    the same segments forever. So a large `overlap_segments` degrades into a
    dense sliding window rather than a hang, and the repeat is
    `min(overlap_segments, len(chunk) - 1)` segments in that case.

    Deterministic: same document, same budget, same overlap, same chunks -
    the same texts, in the same order, under the same indexes. Task 4's resume
    depends on it.
    """
    if budget_tokens <= 0:
        raise ValueError(f"budget_tokens must be positive, got {budget_tokens}")
    if overlap_segments < 0:
        raise ValueError(f"overlap_segments cannot be negative, got {overlap_segments}")

    segments = doc.segments
    if not segments:
        return []

    lines = transcript_lines(doc, speakers=speakers)
    costs = [estimate_tokens(line) for line in lines]

    chunks: list[Chunk] = []
    first = 0
    count = len(segments)
    while first < count:
        # The first segment is always taken, even when it alone is over budget:
        # there is no boundary inside it to cut on.
        last = first
        total = costs[first]
        while last + 1 < count and total + costs[last + 1] <= budget_tokens:
            last += 1
            total += costs[last]

        chunks.append(
            Chunk(
                index=len(chunks),
                start=float(segments[first]["start"]),
                end=float(segments[last]["end"]),
                text="\n".join(lines[first : last + 1]),
                segment_ids=tuple(int(s["idx"]) for s in segments[first : last + 1]),
                tokens=total,
                # Additivity makes `total` the chunk's own estimate, so this is
                # exactly "this chunk is over budget and there was nothing to
                # split" - the single segment case, and only that one.
                oversized=total > budget_tokens,
            )
        )

        if last + 1 >= count:
            break
        first = max(first + 1, last + 1 - overlap_segments)

    return chunks
