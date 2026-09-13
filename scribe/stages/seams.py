"""Where two decodes meet, the same words can be written twice (TASK-035).

A transcript is put together from more than one decode: one per window
(transcribe's `iter_windows`), and one per second opinion spliced into the
first (`second_opinion`). Each decode hears past its own edge - a window its
look-ahead, an opinion its clip - and a word heard there can be kept by the
decode on one side while the decode on the other side says it again.

The mark it leaves is time. One decode never writes two consecutive words that
overlap; counted on 2026-09-11 over every current run in the library, all 31
pairs that do sit at a window cut or a splice edge. 18 were one word written
twice at a cut (media 17: "opportunities when when they are presented", when
595.82-596.72 and again 596.30-596.74), one was two words written twice at a
splice (media 34: "Yeah. And that And that was quite impressive."), and 12
were two different words. An echo is only the first two shapes: the same
words, overlapping. Which of two different words was said needs a listen, and
a word said twice with a pause between ("no, no") was said twice.

Pure functions over word and segment rows; the stages decide which copy goes.
"""

from __future__ import annotations

import re
from typing import Sequence

# The longest echo looked for, in words. Measured: one word at 18 window cuts,
# two at one splice.
ECHO_WORDS = 2


def _token(text: str) -> str:
    return re.sub(r"\W", "", text.lower())


def echo(left: Sequence[dict], right: Sequence[dict]) -> int:
    """How many of `left`'s last words are `right`'s first words written again; 0 for none.

    Both are word rows in file time, `left` ending where `right` begins. An
    echo of n words is n words that match, case and punctuation aside, where
    the left copy's last word overlaps its twin - the right side's n-th word.
    The twins are the proof: left[-1] against right[0] would take a one-word
    echo, or two different words overlapping, as proof of two ("no, no, no"
    lost a "no" that way in review). The longest such run wins, up to
    ECHO_WORDS.
    """
    for n in range(min(ECHO_WORDS, len(left), len(right)), 0, -1):
        ours = [_token(w["text"]) for w in left[-n:]]
        theirs = [_token(w["text"]) for w in right[:n]]
        if all(ours) and ours == theirs and left[-1]["end"] > right[n - 1]["start"]:
            return n
    return 0


def remove_words(segments: Sequence[dict], words: Sequence[dict], gone: range) -> tuple[list[dict], list[dict]]:
    """`words` without positions `gone`, and every segment that lost one said again.

    A segment that lost words keeps the ones left: its text becomes their
    join, the way collect_segments treats a segment the look-ahead cuts, and
    its start or end moves to theirs when the word at that edge went. A
    segment left with no words goes. New lists and new rows for what changed;
    the rows handed in are not touched. Indices keep their gaps until
    `renumber`.
    """
    removed = {id(words[k]) for k in gone}
    lost = {words[k]["segment_idx"] for k in gone}
    own: dict = {}
    for w in words:
        if w["segment_idx"] in lost:
            own.setdefault(w["segment_idx"], []).append(w)
    out: list[dict] = []
    for s in segments:
        if s["idx"] not in lost:
            out.append(s)
            continue
        had = own[s["idx"]]
        left = [w for w in had if id(w) not in removed]
        if not left:
            continue
        out.append({
            **s,
            "start": left[0]["start"] if id(had[0]) in removed else s["start"],
            "end": left[-1]["end"] if id(had[-1]) in removed else s["end"],
            "text": "".join(w["text"] for w in left).strip(),
        })
    return out, [w for k, w in enumerate(words) if k not in gone]


def renumber(segments: list[dict], words: list[dict]) -> None:
    """Indices 0..n in list order, and each word pointing at its segment's new index."""
    new_index = {}
    for k, s in enumerate(segments):
        new_index[s["idx"]] = k
        s["idx"] = k
    for k, w in enumerate(words):
        w["idx"] = k
        if w.get("segment_idx") in new_index:
            w["segment_idx"] = new_index[w["segment_idx"]]
