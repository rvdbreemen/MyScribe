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

**The text on a line is the words inside the seam, not the segment row.**
Words are the transcript (ADR-003): the glossary's corrections and a reader's
retypes live on the word rows, and the transcript view, the DOCX and the SRT
all print words. `segment.text` is what Whisper wrote and is never rewritten,
so a model handed it would be asked about a name the reader no longer sees.
`segment_texts` therefore partitions `TranscriptDoc.words` - already read
through the correction layer by `exports.doc` - over the segments, and the
segment row's own text is used only for a run that has no word rows at all.

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
from typing import Literal, Union

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


def timestamped_line(segment: dict, text: str, speaker: str | None = None) -> str:
    """One segment as `[m:ss] text`, or `[m:ss] SPEAKER_00: text` with a speaker.

    The timestamp is `render.format_ts`'s clock style - the same string the
    transcript view and every export print - so a model asked to cite a moment
    produces something the player's existing seek handler already understands.
    `text` is the caller's - `segment_texts` derives it from the words - and
    never `segment["text"]`, for the reason the module docstring gives. The
    speaker is whatever the caller passes: the raw cluster label for the task
    working out what the name *is*, the display name for the kinds that ask
    who took something on (`transcript_lines`).
    """
    who = f"{speaker}: " if speaker else ""
    return f"[{render.format_ts(float(segment['start']))}] {who}{text}"


def segment_words(doc: TranscriptDoc) -> list[tuple[dict, ...]]:
    """The words of each segment: `doc.words` partitioned over `doc.segments`.

    One pass in time order: a segment takes every word not yet taken that
    starts before the segment ends, so a word in the silence between two
    segments goes to the one that follows, and whatever is left after the last
    segment's end goes to the last. Every word lands in exactly one segment -
    nothing a correction changed is dropped on a boundary, nothing is sent
    twice - which is the partition `plan`'s "nothing lost, nothing doubled"
    holds for words as well as for segments. Empty tuples for a document that
    carries no word rows.
    """
    words = doc.words
    out: list[tuple[dict, ...]] = []
    cursor = 0
    for segment in doc.segments:
        end = float(segment["end"])
        first = cursor
        while cursor < len(words) and float(words[cursor]["start"]) < end:
            cursor += 1
        out.append(tuple(words[first:cursor]))
    if out and cursor < len(words):
        out[-1] = out[-1] + tuple(words[cursor:])
    return out


def segment_texts(doc: TranscriptDoc) -> list[str]:
    """What each segment says: its words, read through the correction layer.

    `TranscriptDoc.words` already carries the glossary's corrections and the
    reader's retypes (the one LEFT JOIN in `exports.doc`), so this is what
    makes the model read the transcript the reader reads - joined the way
    `render.join_text` joins it everywhere else. `segment["text"]` is Whisper's
    own row and is the text only for a run with no word rows at all; the
    documents the chunking tests build by hand are that case.
    """
    if not doc.words:
        return [str(segment["text"]) for segment in doc.segments]
    return [render.join_text(words) for words in segment_words(doc)]


def segment_speakers(doc: TranscriptDoc) -> list[str | None]:
    """The speaker of each segment: the cluster most of its words carry.

    Segments are Whisper's and know nothing of speakers; words carry the
    cluster the attribute stage joined on. A segment is credited to whichever
    cluster has the most of its words (the `segment_words` partition, so the
    same words whose text the line carries), ties to the earlier label, and
    None when no word in it has one - so an undiarized run gets plain lines.
    """
    out: list[str | None] = []
    for words in segment_words(doc):
        counts: dict[str, int] = {}
        for word in words:
            label = word.get("speaker")
            if label:
                counts[label] = counts.get(label, 0) + 1
        out.append(max(sorted(counts), key=counts.__getitem__) if counts else None)
    return out


Speakers = Union[bool, Literal["names"]]
"""How a line names its speaker: not at all (False), by cluster label (True,
for the task that works out who the labels are) or by the name the page
shows (`"names"`: the one somebody set, else Speaker N - for the kinds that
ask who took something on, TASK-105.03)."""


def transcript_lines(doc: TranscriptDoc, *, speakers: Speakers = False) -> list[str]:
    """One timestamped line per segment, with its speaker when asked.

    Aligned with `doc.segments` by position, which `plan` and the chat tool's
    excerpt rendering both index into.
    """
    texts = segment_texts(doc)
    whos = segment_speakers(doc) if speakers else [None] * len(texts)
    if speakers == "names":
        whos = [render.speaker_display(doc.labels, who) if who else None for who in whos]
    return [
        timestamped_line(segment, text, who)
        for segment, text, who in zip(doc.segments, texts, whos)
    ]


def transcript_text(doc: TranscriptDoc, *, speakers: Speakers = False) -> str:
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


def needs_chunking(doc: TranscriptDoc, budget_tokens: int, *, speakers: Speakers = False) -> bool:
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
    speakers: Speakers = False,
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
