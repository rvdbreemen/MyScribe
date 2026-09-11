"""A second decode of the stretches where the first one failed by its own measure.

TASK-032. Whisper sometimes stores its own failure as the transcript, in two
shapes, and both leave a mark this module can see without listening:

* **A loop the timestamps give away.** A phrase repeated back to back with its
  words squeezed into no time: media 17's "wouldn't really" seven times in
  0.9 s, media 19's "I do" six times where the speaker went on to say what he
  does. The segment around it is long enough that its compression ratio stays
  under the threshold, so no temperature fallback ever fires.
* **A segment that failed every temperature.** Its compression ratio is still
  over the threshold at temperature 1.0, and faster-whisper keeps the attempt
  with the best average log-probability - which a loop wins. Media 32's
  "Hacker History is a production of Hacker History. Thank you. Thank you."
  over the outro music is this.

Each flagged stretch is decoded again, twice, from clips that start at two
different points before it, so each decode arrives with different context.
What the speaker said, both decodes say again; what the decoder invented, they
do not. Measured on every flag in the library 2026-09-11 (31 stretches, 21
recordings): loops of five or more and failed segments collapsed or cleared in
both second opinions, and the real repetitions held - media 20's "no, no, no,
no, no, wait" came back four times in both, so it stays as it was. Three-fold
repeats are left alone: "blah, blah, blah" came back as "blah, blah" and
"um, um, um" as nothing, and a repeat that short is as likely said as invented.

The stored stretch is replaced only when both opinions are clean there, and
then the whole of every segment it touches is replaced, so a segment's text
stays the join of its words; an opinion's edge word that the stored words
beside the stretch say again is dropped (`seams`, TASK-035). Every flag and
what became of it is returned as a record for the run's params: a transcript
changed after its first decode says so, and says what it said before.

Pure functions around two callables the stage hands in - one reads a clip of
the prepared wav, one decodes it into rows - so none of this needs a model to
test, and the model stays the stage's to load and free.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from scribe.stages import seams

# A loop is this many back-to-back repeats of a 1-3 word phrase, or more.
MIN_REPEATS = 5
# ... whose words take less than this each (median). Real stutters take real
# time; the loops measured took 0.00-0.01 s a word.
COLLAPSED_WORD_SECONDS = 0.05
# An opinion that still says the flagged phrase more often than this, back to
# back, heard it too - the stored text stays.
MAX_REPEATS_KEPT = 2
# Where the two clips start, before the stretch. Two different starts are two
# different contexts, which is what makes the opinions independent.
LEADS_SECONDS = (20.0, 7.0)
# How far each clip runs past the stretch. The end of a clip is, to Whisper,
# the end of the file, and that is where it hallucinates (TASK-030).
TAIL_SECONDS = 15.0
# A stretch longer than this is not re-decoded: that is a bad recording, not a
# loop, and a clip that long is the memory problem windows exist to avoid.
MAX_STRETCH_SECONDS = 90.0
# Flags this close together are one stretch.
MERGE_SECONDS = 1.0
# Before/after text kept in the record.
RECORD_CHARS = 300

Rows = tuple[list[dict], list[dict]]


@dataclass(frozen=True)
class Flag:
    """Where the first decode failed by its own measure, and how."""

    start: float
    end: float
    kinds: tuple[str, ...]  # "loop", "failed"
    phrase: str | None = None
    repeats: int = 0
    compression: float | None = None


# --- what gets a second opinion ------------------------------------------------------


def _token(text: str) -> str:
    return re.sub(r"\W", "", text.lower())


def _repeats_at(tokens: Sequence[str], i: int, n: int) -> int:
    gram = list(tokens[i:i + n])
    reps = 1
    while list(tokens[i + reps * n:i + (reps + 1) * n]) == gram:
        reps += 1
    return reps


def longest_repeat(tokens: Sequence[str], phrase: str) -> int:
    """How often `phrase` is said back to back at most; 0 when not at all."""
    gram = phrase.split()
    n = len(gram)
    best = 0
    for i in range(len(tokens) - n + 1):
        if list(tokens[i:i + n]) == gram:
            best = max(best, _repeats_at(tokens, i, n))
    return best


def loops(words: Sequence[dict]) -> list[Flag]:
    """Phrases of 1-3 words said MIN_REPEATS+ times back to back in no time at all."""
    said = [(token, word) for word in words if (token := _token(word["text"]))]
    tokens = [token for token, _ in said]
    found: list[Flag] = []
    for n in (1, 2, 3):
        i = 0
        while i + n <= len(tokens):
            reps = _repeats_at(tokens, i, n)
            if reps >= MIN_REPEATS:
                run = [word for _, word in said[i:i + reps * n]]
                if statistics.median(w["end"] - w["start"] for w in run) < COLLAPSED_WORD_SECONDS:
                    found.append(Flag(
                        run[0]["start"], run[-1]["end"], ("loop",),
                        phrase=" ".join(tokens[i:i + n]), repeats=reps,
                    ))
            i += max(1, (reps - 1) * n) if reps > 1 else 1
    return found


def failures(segments: Sequence[dict], threshold: float) -> list[Flag]:
    """Segments whose compression ratio stayed over the threshold: every temperature failed."""
    return [
        Flag(s["start"], s["end"], ("failed",), compression=s["compression_ratio"])
        for s in segments
        if s.get("compression_ratio") is not None and s["compression_ratio"] > threshold
    ]


def find_flags(segments: Sequence[dict], words: Sequence[dict], *, threshold: float) -> list[Flag]:
    """Every loop and every failed segment, overlapping ones merged, in time order."""
    merged: list[Flag] = []
    for flag in sorted(loops(words) + failures(segments, threshold), key=lambda f: f.start):
        if merged and flag.start <= merged[-1].end + MERGE_SECONDS:
            last = merged[-1]
            bigger = flag if flag.repeats * len((flag.phrase or "").split()) > last.repeats * len(
                (last.phrase or "").split()) else last
            compressions = [c for c in (last.compression, flag.compression) if c is not None]
            merged[-1] = Flag(
                last.start, max(last.end, flag.end),
                tuple(sorted(set(last.kinds) | set(flag.kinds), key=("loop", "failed").index)),
                phrase=bigger.phrase, repeats=bigger.repeats,
                compression=max(compressions) if compressions else None,
            )
        else:
            merged.append(flag)
    return merged


# --- judging an opinion --------------------------------------------------------------


def _midpoint(word: dict) -> float:
    return (word["start"] + word["end"]) / 2


def _within(words: Sequence[dict], r0: float, r1: float) -> list[dict]:
    return [w for w in words if r0 <= _midpoint(w) < r1]


def verdict(rows: Rows, flags: Sequence[Flag], r0: float, r1: float, threshold: float) -> str | None:
    """None when this opinion is clean over [r0, r1); otherwise why it is not."""
    segments, words = rows
    said = _within(words, r0, r1)
    tokens = [t for w in said if (t := _token(w["text"]))]
    for flag in flags:
        if flag.phrase and longest_repeat(tokens, flag.phrase) > MAX_REPEATS_KEPT:
            return f"still says {flag.phrase!r} {longest_repeat(tokens, flag.phrase)} times"
    if loops(said):
        return "has a loop of its own"
    for s in segments:
        if s["end"] > r0 and s["start"] < r1 and (s.get("compression_ratio") or 0.0) > threshold:
            return f"failed the compression check too ({s['compression_ratio']:.2f})"
    return None


def _sureness(rows: Rows, r0: float, r1: float) -> float:
    said = _within(rows[1], r0, r1)
    probabilities = [w["probability"] for w in said if w.get("probability") is not None]
    return sum(probabilities) / len(probabilities) if probabilities else 0.0


# --- the review ------------------------------------------------------------------------


def review(
    segments: list[dict],
    words: list[dict],
    *,
    read_clip: Callable[[float, float], np.ndarray],
    decode: Callable[[np.ndarray, float, float], Rows],
    duration: float,
    threshold: float,
    cancelled: Callable[[], None],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Give every flagged stretch a second opinion; returns (segments, words, records).

    `read_clip(start, end)` returns that stretch of the prepared wav;
    `decode(audio, offset, limit)` decodes it with the stage's parameters and
    returns segment and word rows in file time, dropping what lies past
    `limit` seconds into the clip. `cancelled()` raises when a cancel is up.

    Stretches are worked from the last to the first, so the list positions of
    the ones still to do do not move under them; indices are renumbered once,
    at the end.
    """
    flags = find_flags(segments, words, threshold=threshold)
    if not flags:
        return segments, words, []

    groups = _group_by_segments(segments, flags)
    records: list[dict] = []
    for g in range(len(groups) - 1, -1, -1):
        (i, j), group = groups[g]
        cancelled()
        # The stored words before this stretch are final only from where the
        # stretch before it ends: that one is worked next and may replace them.
        floor = _stored_span(segments, words, *groups[g - 1][0])[1] if g else 0
        segments, words, record = _second_opinion(
            segments, words, i, j, group, floor=floor, tag=g,
            read_clip=read_clip, decode=decode, duration=duration, threshold=threshold,
        )
        records.append(record)
    records.reverse()
    seams.renumber(segments, words)
    return segments, words, records


def _group_by_segments(segments: Sequence[dict], flags: Sequence[Flag]) -> list[tuple[tuple[int, int], list[Flag]]]:
    """Each flag's run of touched segments [i, j), with flags sharing segments together."""
    groups: list[tuple[tuple[int, int], list[Flag]]] = []
    for flag in flags:
        touched = [k for k, s in enumerate(segments) if s["end"] > flag.start and s["start"] < flag.end]
        if not touched:
            continue
        i, j = touched[0], touched[-1] + 1
        if groups and i < groups[-1][0][1]:
            (gi, gj), members = groups[-1]
            groups[-1] = ((gi, max(gj, j)), members + [flag])
        else:
            groups.append(((i, j), [flag]))
    return groups


def _stored_span(segments: Sequence[dict], words: Sequence[dict], i: int, j: int) -> tuple[int, int]:
    """The positions [a, b) of the words of segments [i, j): contiguous, because
    words are appended segment by segment in time order. Empty at where the
    first segment starts when those segments hold no words."""
    keys = {segments[k]["idx"] for k in range(i, j)}
    span = [k for k, w in enumerate(words) if w.get("segment_idx") in keys]
    if span:
        return span[0], span[-1] + 1
    a = next((k for k, w in enumerate(words) if w["start"] >= segments[i]["start"]), len(words))
    return a, a


def _second_opinion(segments, words, i, j, flags, *, floor, tag, read_clip, decode, duration, threshold):
    """One stretch: decode it twice, and replace segments [i, j) if both agree.

    `floor` is where the stored words that will stay begin (the end of the
    stretch before this one, still to be worked); `tag` keys this opinion's
    segments apart from every other opinion's until they are renumbered.
    """
    a, b = _stored_span(segments, words, i, j)

    # Bounded at the middle of the pause either side, so a word at the edge
    # lands on the same side in the stored run and in the opinion.
    first = words[a]["start"] if b > a else segments[i]["start"]
    last = words[b - 1]["end"] if b > a else segments[j - 1]["end"]
    r0 = (words[a - 1]["end"] + first) / 2 if a > 0 else min(first, segments[i]["start"])
    r1 = (last + words[b]["start"]) / 2 if b < len(words) else max(last, segments[j - 1]["end"])

    record = {
        "start": round(r0, 2),
        "end": round(r1, 2),
        "flag": "+".join(sorted({k for f in flags for k in f.kinds}, key=("loop", "failed").index)),
        "phrase": next((f.phrase for f in flags if f.phrase), None),
        "repeats": max((f.repeats for f in flags), default=0),
        "compression": max((f.compression for f in flags if f.compression is not None), default=None),
        "before": "".join(w["text"] for w in words[a:b]).strip()[:RECORD_CHARS],
    }

    if r1 <= r0 or r1 - r0 > MAX_STRETCH_SECONDS:
        record["outcome"] = f"kept: stretch of {r1 - r0:.1f} s is not one a clip should hold"
        return segments, words, record

    opinions: list[Rows] = []
    for lead in LEADS_SECONDS:
        start = max(0.0, r0 - lead)
        end = min(duration, r1 + TAIL_SECONDS) if duration > 0 else r1 + TAIL_SECONDS
        rows = decode(read_clip(start, end), start, r1 - start)
        why = verdict(rows, flags, r0, r1, threshold)
        if why is not None:
            record["outcome"] = f"kept: the opinion from {lead:.0f} s before {why}"
            return segments, words, record
        opinions.append(rows)

    best = max(opinions, key=lambda rows: _sureness(rows, r0, r1))
    new_segments, new_words = _cut_to(best, r0, r1, tag=tag)
    # The clip ran past the stretch both ways, so the opinion can open with the
    # stored word before it, or end with the ones after it, said again - media
    # 34's "And that And that" (TASK-035). The stored words stay as they were;
    # the opinion's copy goes. Only words that will stay count as stored: the
    # ones after the stretch are final already, the ones before it from `floor`.
    head = seams.echo(words[max(floor, a - seams.ECHO_WORDS):a], new_words)
    tail = seams.echo(new_words[head:], words[b:b + seams.ECHO_WORDS])
    echoed = new_words[:head] + new_words[len(new_words) - tail:]
    if tail:
        new_segments, new_words = seams.remove_words(
            new_segments, new_words, range(len(new_words) - tail, len(new_words)))
    if head:
        new_segments, new_words = seams.remove_words(new_segments, new_words, range(head))
    if echoed:
        record["echo"] = "".join(w["text"] for w in echoed).strip()
    record["after"] = "".join(w["text"] for w in new_words).strip()[:RECORD_CHARS]
    record["outcome"] = "replaced"
    return (
        segments[:i] + new_segments + segments[j:],
        words[:a] + new_words + words[b:],
        record,
    )


def _cut_to(rows: Rows, r0: float, r1: float, *, tag: int) -> Rows:
    """The opinion's segments and words over [r0, r1), by word midpoint.

    A segment cut by the bounds keeps the words inside and says only those,
    the way collect_segments treats a segment the look-ahead cuts. Segment
    keys are (tag, idx) tuples until seams.renumber, the tag being the
    stretch's own number, so they collide neither with the stored keys nor
    with another opinion's - each opinion numbers its segments from 0.
    """
    segments, words = rows
    kept_words = _within(words, r0, r1)
    by_segment: dict[int, list[dict]] = {}
    for w in kept_words:
        by_segment.setdefault(w["segment_idx"], []).append(w)
    out_segments: list[dict] = []
    out_words: list[dict] = []
    for s in segments:
        own = by_segment.get(s["idx"])
        if not own:
            continue
        whole = len(own) == sum(1 for w in words if w["segment_idx"] == s["idx"])
        key = (tag, s["idx"])
        out_segments.append({
            **s,
            "idx": key,
            "start": max(r0, s["start"] if whole else own[0]["start"]),
            "end": min(r1, s["end"] if whole else own[-1]["end"]),
            "text": s["text"] if whole else "".join(w["text"] for w in own).strip(),
        })
        out_words.extend({**w, "segment_idx": key} for w in own)
    return out_segments, out_words
