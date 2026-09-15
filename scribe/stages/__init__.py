"""Pipeline stages (Phase 2). Registered into scribe.runner.STAGES.

The list below is the transcribe pipeline, in the order the spec's stage
table names it: probe -> prepare -> [enhance] -> transcribe -> diarize ->
attribute -> correct -> finalize, with proxy after prepare (TASK-057).
`enhance` is deliberately reserved and stays absent until it ships with its
A/B harness, which is the only thing that makes an enhanced transcript
reviewable rather than merely different.

The order is not a preference. probe is the gate that decides what counts as
media at all; prepare gives every model afterwards the same samples on the same
clock; proxy makes the copy a browser seeks exactly when the original is not
one, beside prepare because both are ffmpeg over the original and neither
needs the card; transcribe and diarize are the two that want the whole card to
themselves and so must not overlap; attribute needs both of their outputs;
correct is the glossary's second look at the words, and it comes last because
it reads them back from the table (a word somebody edited outranks any
glossary) and writes only a layer over them (ADR-003); and finalize is the only
stage allowed to say which run a user sees, so it stays at the end - a run
becomes current with its corrections already in place.
"""

from typing import Callable

from scribe.stages import (
    attribute, correct, diarize, finalize, prepare, probe, proxy, transcribe,
)

TRANSCRIBE_STAGES: list[tuple[str, Callable]] = [
    ("probe", probe.run),
    ("prepare", prepare.run),
    ("proxy", proxy.run),
    ("transcribe", transcribe.run),
    ("diarize", diarize.run),
    ("attribute", attribute.run),
    ("correct", correct.run),
    ("finalize", finalize.run),
]
