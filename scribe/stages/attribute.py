"""Give every word the speaker who was actually talking.

Whisper says what was said and when; pyannote says who spoke when. Neither
knows about the other, and their boundaries do not line up: a Whisper segment
routinely runs across a speaker change, and a diarization turn routinely ends
mid-word. This module is the join.

The rule is maximum temporal overlap, per word, not per segment. Attributing
whole segments is what makes cheap tools put two people's sentences under one
name; attributing words means a segment covering two speakers simply becomes
two speaker runs downstream.

Words are canonical here (spec section 2): this module labels words and
nothing else. Grouping labelled words into paragraphs or subtitle cues is a
render-time concern and deliberately lives elsewhere, so no stored grouping
can ever go stale.

Ported from WHYcast-transcribe's whycast/pipeline/attribution.py, adapted to
word dictionaries instead of Whisper segment objects.

The pure functions came first (plan Task 5) and the stage wrapper at the bottom
came with the pipeline that needed it (Task 7); everything above `run` is still
callable, and testable, without a database in sight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, NamedTuple, Sequence

from scribe import jobs

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

# Words between progress reports. The join is O(words x turns) - every word is
# checked against every turn - so a four-hour recording with a few thousand
# turns is genuinely minutes of work, not the instant this looks like at test
# sizes. That is why this stage reports a moving fraction instead of the
# "0 and then 1" the plan allows a stage that cannot measure itself: here it
# can, the count of words done is honest, and a frozen bar for two minutes is
# indistinguishable from a hang.
PROGRESS_CHUNK = 500


class Turn(NamedTuple):
    """One stretch of speech by one speaker, as diarization heard it."""

    start: float
    end: float
    speaker: str


def turns_from_diarization(diarization: Any) -> list[Turn]:
    """Normalise whatever diarization handed us into a list of turns.

    Accepts a pyannote ``Annotation``, a list of dicts, a list of tuples, or a
    list of Turns. Diarization output shape varies by pyannote version and by
    whether the caller went through a pipeline or a stored fixture, so it is
    normalised once here rather than at every call site.
    """
    if not diarization:
        return []

    if hasattr(diarization, "itertracks"):
        return [
            Turn(float(segment.start), float(segment.end), label)
            for segment, _, label in diarization.itertracks(yield_label=True)
        ]

    turns: list[Turn] = []
    for entry in diarization:
        if isinstance(entry, Turn):
            turns.append(entry)
        elif isinstance(entry, dict):
            speaker = entry.get("speaker") or entry.get("label")
            if speaker is None:
                # A turn without a speaker labels nothing; dropping it is
                # honest, inventing a name for it is not.
                continue
            turns.append(Turn(float(entry["start"]), float(entry["end"]), speaker))
        else:
            start, end, speaker = entry
            turns.append(Turn(float(start), float(end), speaker))
    return turns


def speaker_for_interval(start: float, end: float, turns: Sequence[Turn]) -> str | None:
    """The speaker who talks through most of ``[start, end]``.

    Returns None when the interval overlaps no turn at all - for a word, that
    means it fell in silence diarization skipped, and the caller decides what
    to do about it (see :func:`fill_unattributed`).

    Overlap, not midpoint containment: a word straddling a turn edge belongs to
    whoever was speaking for most of it, and a midpoint landing in a gap
    between two turns is not evidence of anything. Ties go to the earlier turn,
    which makes the result independent of dictionary ordering upstream.
    """
    best: str | None = None
    best_overlap = 0.0
    for turn in turns:
        overlap = min(end, turn.end) - max(start, turn.start)
        if overlap > best_overlap:
            best, best_overlap = turn.speaker, overlap
    return best


def attribute_words(words: Iterable[dict], turns: Sequence[Turn]) -> list[dict]:
    """Copy the words with a ``speaker`` key added (None where unknown).

    Never mutates the input: the caller may still need the unlabelled words,
    for instance to compare two diarization runs over the same transcription.
    """
    ordered = sorted(turns, key=lambda t: (t.start, t.end))
    return [{**word, "speaker": speaker_for_interval(word["start"], word["end"], ordered)} for word in words]


def fill_unattributed(words: Iterable[dict]) -> list[dict]:
    """Give words that overlap no turn to the speaker around them.

    A short word inside a pause - "yeah", a laugh, the tail of a sentence
    running past the end of a turn - can miss every turn. Leaving it unlabelled
    strands real text in the transcript under no name at all. The speaker
    before it is the far better guess; the speaker after it is the fallback
    when the gap opens the transcript.
    """
    out = [dict(word) for word in words]

    previous: str | None = None
    for word in out:
        if word.get("speaker") is not None:
            previous = word["speaker"]
        elif previous is not None:
            word["speaker"] = previous

    following: str | None = None
    for word in reversed(out):
        if word.get("speaker") is not None:
            following = word["speaker"]
        elif following is not None:
            word["speaker"] = following

    return out


def join(words: Iterable[dict], diarization: Any) -> list[dict]:
    """Attribute then fill: the whole join in one call.

    With no diarization every speaker stays None, which is how the renderer
    knows to write no speaker headings at all.
    """
    turns = turns_from_diarization(diarization)
    labelled = attribute_words(words, turns)
    return fill_unattributed(labelled) if turns else labelled


# --- the stage ------------------------------------------------------------------


def run(ctx: "RunnerContext") -> None:
    """The stage: label this job's words with the speakers diarize found.

    Nothing is written to the database here. The labelled words go back on
    `ctx.state` and finalize commits them with the rest of the run, so a job
    that dies between these two stages leaves no half-attributed transcript
    behind - and so the word rows are written exactly once.
    """
    words = ctx.state.get("words")
    if words is None:
        raise RuntimeError(
            "attribute needs the words the transcribe stage produced, "
            "but ctx.state['words'] is empty"
        )

    # Sorted once for every chunk below rather than once per chunk: the cost is
    # trivial either way, but it keeps the tie-break ("the earlier turn wins")
    # identical no matter where a chunk boundary happens to fall.
    turns = sorted(turns_from_diarization(ctx.state.get("turns")), key=lambda t: (t.start, t.end))

    labelled: list[dict] = []
    total = len(words)
    for start in range(0, total, PROGRESS_CHUNK):
        labelled.extend(attribute_words(words[start : start + PROGRESS_CHUNK], turns))
        ctx.report(len(labelled) / total)

    if turns:
        # Only with turns to carry: with none, every speaker is None and there
        # is nothing to fill from, which is a transcript with no speakers
        # rather than a transcript with a gap.
        labelled = fill_unattributed(labelled)

    ctx.state["words"] = labelled

    speakers = sorted({word["speaker"] for word in labelled if word["speaker"]})
    unattributed = sum(1 for word in labelled if word["speaker"] is None)
    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "attribute",
        n_words=total,
        n_turns=len(turns),
        speakers=speakers,
        unattributed=unattributed,
    )
    ctx.report(1.0)
