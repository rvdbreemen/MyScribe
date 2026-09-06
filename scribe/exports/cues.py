"""Subtitle cues from words: the segmentation engine and its compliance report.

A subtitle is a sequence of cues, each a start, an end and one or two lines
of text. Nothing about a cue is stored (ADR-003): `build` derives them from
the run's words and the constraints in an `ExportOptions` every time an SRT,
VTT, CSV or cue-layout TXT is written, so a rename, a reassignment or a new
preset costs a re-run of this function and nothing else. The same words and
the same options always give the same cues - there is no randomness here and
nothing depends on the order of a dictionary - which is what lets a golden
file pin a writer's output byte for byte.

The constraints, all from the options: `cpl` characters per line, `max_lines`
lines per cue, `max_cps` characters per second of display, `min_duration`
and `max_duration` in seconds, and `cue_gap`, the silence kept between two
consecutive cues. Where the words make a constraint impossible - a single
word wider than a line, a burst of speech no cue could show slowly enough -
the engine keeps the words and writes a `Violation` into the report instead
of dropping or rewriting anything. The report is the user's compliance
summary; the cues are still the best the words allow.

The algorithm, in the order it runs:

1. **Walk the words in order, growing one candidate cue at a time.** A word
   joins the candidate while it shares the speaker, the candidate's duration
   (first start to this word's end) stays within `max_duration`, and the
   words still wrap into at most `max_lines` lines of `cpl` characters. The
   wrap used here is greedy, which gives the fewest lines any layout needs,
   so "fits" means exactly what the balanced layout in step 4 will confirm.
   A speaker change is a hard break, whether or not names are exported.

2. **When the next word does not fit, choose the break.** The candidate is
   cut after the last word that fits unless a better point lies in the last
   40% of it: sentence-final punctuation first (`render`'s own rule - the
   same one that ends a sentence in the transcript view), then a comma,
   semicolon or colon, then the largest silence between two words, ties going
   to the fullest cue. The window keeps a break from cutting a cue down to a
   fragment; the order keeps a break from landing mid-clause when a clause
   end is near. Words after the break start the next candidate.

3. **Time the cue.** It starts at its first word and ends at its last; a cue
   shorter than `min_duration` is held on screen up to `min_duration` as
   long as that leaves `cue_gap` before the next word (or stays within the
   media for the last cue). A cue whose last word runs into the next cue's
   first is trimmed to leave the gap - the previous cue gives, as subtitle
   tools do, because its word has already been spoken. And a cue never
   starts before the one before it ends: word times that run backwards (a
   speaker's first word stamped before the previous speaker's last) would
   otherwise make cues that overlap and sit out of time order, which a
   player cannot show; the later cue starts where the earlier ended, and
   the earlier keeps its zero length and its report.

4. **Lay the lines out.** With the fewest lines the words need, a dynamic
   programme chooses the break points that minimise the squared deviation of
   every line's width from the mean, in integers so that ties are exact; a
   tie goes to the shorter line on top. A word wider than `cpl` is a line of
   its own.

5. **Check.** Each cue is measured against the options and a `Violation`
   named by rule - `cpl`, `cps`, `min_duration`, `max_duration` - records
   each limit it breaks, with the value and the limit.

Widths are counted the way the words are joined everywhere else: the text
concatenated as stored and stripped once (`render.join_text`), so a language
written without spaces is measured without them. Characters per second count
what is on screen - the lines' characters, not the break between them. The
lines never include a speaker name; a writer that prefixes one owns that
width. Words whose text is blank are ignored, because they have nothing to
show - but their *time* is not: an empty word is how the glossary marks the
second half of a phrase it folded into one (`_spoken`), and a cue that ended
at the first word of such a window disappeared while it was still being said.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from scribe import render
from scribe.exports.doc import TranscriptDoc
from scribe.exports.options import ExportOptions

# The rules a Violation can name, in the order a cue's are reported.
RULES: tuple[str, ...] = ("max_duration", "min_duration", "cpl", "cps")

# Punctuation that ends a clause: the second choice for a break, after a
# sentence end. Checked like render's sentence rule - closers may trail it,
# whitespace must follow - so "3,14" stays whole.
CLAUSE_END = ",;:"

# The part of a candidate a break may fall in: its last 40%, as a fraction in
# integers so the window's size never depends on floating-point rounding.
WINDOW_NUMERATOR, WINDOW_DENOMINATOR = 2, 5

# Slack for comparing derived floats (an end computed as start + min_duration
# is not always min_duration away from start).
_EPS = 1e-9


@dataclass(frozen=True)
class Cue:
    """One subtitle: 1-based ``index``, seconds, lines of text, and the
    speaker's cluster label (None when the run was not diarized). Writers
    turn the label into a name with ``render.speaker_display``."""

    index: int
    start: float
    end: float
    lines: list[str]
    speaker: str | None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def chars(self) -> int:
        """Characters on screen: the lines' lengths, without the line break."""
        return sum(len(line) for line in self.lines)

    @property
    def cps(self) -> float:
        """Reading speed in characters per second of display time."""
        if self.duration <= 0:
            return float("inf") if self.chars else 0.0
        return self.chars / self.duration


@dataclass(frozen=True)
class Violation:
    """A constraint a cue breaks: which cue, which rule, the measured value
    and the limit it exceeded (or fell short of, for ``min_duration``)."""

    cue_index: int
    rule: str
    value: float
    limit: float


@dataclass
class Report:
    """Every violation, in cue order; empty when the cues comply."""

    violations: list[Violation] = field(default_factory=list)


@dataclass
class CueSet:
    cues: list[Cue]
    report: Report


def build(doc: TranscriptDoc, options: ExportOptions) -> CueSet:
    """The cues for ``doc`` under ``options``, and the report of what could
    not be satisfied. Pure: the document is read, never changed."""
    words = _spoken(doc.words)
    cues: list[Cue] = []
    violations: list[Violation] = []
    previous_end = float("-inf")
    for number, (i, k) in enumerate(_segment(words, options), start=1):
        start, end = _timing(words, i, k, options, doc.duration, floor=previous_end)
        lines = _balance(words[i:k], options.cpl, options.max_lines)
        cue = Cue(number, start, end, lines, words[i].get("speaker"))
        cues.append(cue)
        violations.extend(_check(cue, options))
        previous_end = end
    return CueSet(cues, Report(violations))


def _spoken(words: Sequence[dict]) -> list[dict]:
    """The words that have something to show, each ending where its own
    silent tail ends.

    A word with no text is not a pause in the recording: it is the second or
    third word of a phrase the glossary folded into the first
    (`glossary._replacement` puts the whole replacement on the first word and
    empties the rest, so joining the run reproduces the phrase exactly once).
    Dropping them was right; dropping their *time* was not - a cue that ended
    on such a term ended at the first word of the window and disappeared while
    the phrase was still being said.

    Copies only the words whose end moves, so a transcript with no corrections
    is the same list of the same dicts it always was, and `build` stays the
    pure function its docstring promises.
    """
    out: list[dict] = []
    for word in words:
        if str(word.get("text") or "").strip():
            out.append(word)
            continue
        if not out:
            continue  # nothing to hand the time to
        end = word.get("end")
        if end is not None and float(end) > float(out[-1]["end"]):
            out[-1] = {**out[-1], "end": float(end)}
    return out


# --- step 1 and 2: where the cues break --------------------------------------------


def _segment(words: Sequence[dict], options: ExportOptions) -> list[tuple[int, int]]:
    """The cues as ``(first, past-the-last)`` word index pairs, covering every word."""
    spans: list[tuple[int, int]] = []
    n = len(words)
    i = 0
    while i < n:
        j = _grow(words, i, options)
        # Stopped by the budget (not by a speaker change or the end): the
        # break may move back into the window.
        overflowed = j < n and words[j].get("speaker") == words[i].get("speaker")
        k = _best_break(words, i, j) if overflowed else j
        spans.append((i, k))
        i = k
    return spans


def _grow(words: Sequence[dict], i: int, options: ExportOptions) -> int:
    """The largest ``j`` such that ``words[i:j]`` shares one speaker and fits
    the duration and the line budget; at least ``i + 1``, so a word that fits
    nothing still gets a cue of its own."""
    n = len(words)
    speaker = words[i].get("speaker")
    start = float(words[i]["start"])
    lines, line_start = 1, i
    j = i + 1
    while j < n and words[j].get("speaker") == speaker:
        if float(words[j]["end"]) - start > options.max_duration:
            break
        if _width(words, line_start, j + 1) > options.cpl:
            if lines == options.max_lines:
                break
            lines += 1
            line_start = j
        j += 1
    return j


def _best_break(words: Sequence[dict], i: int, j: int) -> int:
    """Where to cut the candidate ``words[i:j]`` when ``words[j]`` did not
    fit: the ``k`` (``i < k <= j``) of the best break in the last 40%."""
    count = j - i
    window = (WINDOW_NUMERATOR * count + WINDOW_DENOMINATOR - 1) // WINDOW_DENOMINATOR
    lowest = j - window + 1
    for k in range(j, lowest - 1, -1):
        if render._ends_sentence(words[k - 1], words[k]):
            return k
    for k in range(j, lowest - 1, -1):
        if _ends_clause(words[k - 1], words[k]):
            return k
    best, best_silence = j, _silence(words, j)
    for k in range(j - 1, lowest - 1, -1):
        silence = _silence(words, k)
        if silence > best_silence:
            best, best_silence = k, silence
    return best


def _ends_clause(word: dict, following: dict) -> bool:
    """Clause punctuation on ``word``, followed by whitespace - the shape of
    ``render._ends_sentence``, for the lesser punctuation."""
    raw = str(word.get("text") or "")
    text = raw.rstrip().rstrip(render.SENTENCE_CLOSERS)
    if not text or text[-1] not in CLAUSE_END:
        return False
    return raw[-1:].isspace() or str(following.get("text") or "")[:1].isspace()


def _silence(words: Sequence[dict], k: int) -> float:
    """The pause before ``words[k]``."""
    return float(words[k]["start"]) - float(words[k - 1]["end"])


def _width(words: Sequence[dict], a: int, b: int) -> int:
    """The characters ``words[a:b]`` take on one line."""
    return len(render.join_text(words[a:b]))


# --- step 3: timing --------------------------------------------------------------------


def _timing(
    words: Sequence[dict],
    i: int,
    k: int,
    options: ExportOptions,
    media_end: float,
    *,
    floor: float = float("-inf"),
) -> tuple[float, float]:
    """``(start, end)`` for the cue ``words[i:k]``: held to ``min_duration``
    when the next word allows, trimmed to leave ``cue_gap`` when it does not,
    and never starting before ``floor`` (the previous cue's end)."""
    start = max(float(words[i]["start"]), floor)
    raw_end = float(words[k - 1]["end"])
    if k < len(words):
        ceiling = float(words[k]["start"]) - options.cue_gap
    else:
        ceiling = max(raw_end, media_end)
    end = min(max(raw_end, start + options.min_duration), ceiling)
    return start, max(end, start)


# --- step 4: line layout ---------------------------------------------------------------


def _balance(words: Sequence[dict], cpl: int, max_lines: int) -> list[str]:
    """``words`` as the fewest lines of at most ``cpl`` characters that hold
    them, balanced. A word wider than ``cpl`` is a line by itself. The words
    must fit ``max_lines`` lines, as every cue from ``_grow`` does."""
    count = len(words)
    # From each word, the lines that may start there: (end, width) while the
    # width is within cpl; one word alone is always a line, however wide.
    spans: list[list[tuple[int, int]]] = []
    for a in range(count):
        row: list[tuple[int, int]] = []
        for b in range(a + 1, count + 1):
            width = _width(words, a, b)
            if width > cpl and b > a + 1:
                break
            row.append((b, width))
        spans.append(row)
    total = _width(words, 0, count)
    for lines in range(1, max_lines + 1):
        layout = _layout(spans, count, lines, total)
        if layout is not None:
            return [render.join_text(words[a:b]) for a, b in layout]
    raise ValueError(f"{count} words do not fit {max_lines} lines of {cpl}")


def _layout(
    spans: list[list[tuple[int, int]]], count: int, lines: int, total: int
) -> list[tuple[int, int]] | None:
    """The most balanced split of ``count`` words into exactly ``lines``
    lines, as ``(start, end)`` pairs, or None when no split is feasible.

    The cost of a line is its squared distance from the mean width, scaled
    by ``lines`` so it stays an integer: ``(lines * width - target) ** 2``
    where ``target`` is the total width less the breaks. Ties keep the first
    (shortest) candidate for the top line, so the cue reads bottom-heavy.
    """
    target = total - (lines - 1)
    cost: list[list[int | None]] = [[None] * (lines + 1) for _ in range(count + 1)]
    following: list[list[int]] = [[0] * (lines + 1) for _ in range(count + 1)]
    cost[count][0] = 0
    for a in range(count - 1, -1, -1):
        for remaining in range(1, lines + 1):
            best: int | None = None
            for b, width in spans[a]:
                rest = cost[b][remaining - 1]
                if rest is None:
                    continue
                candidate = (lines * width - target) ** 2 + rest
                if best is None or candidate < best:
                    best = candidate
                    following[a][remaining] = b
            cost[a][remaining] = best
    if cost[0][lines] is None:
        return None
    layout: list[tuple[int, int]] = []
    a, remaining = 0, lines
    while remaining:
        b = following[a][remaining]
        layout.append((a, b))
        a, remaining = b, remaining - 1
    return layout


# --- step 5: the report ----------------------------------------------------------------


def _check(cue: Cue, options: ExportOptions) -> list[Violation]:
    """The constraints ``cue`` breaks, in ``RULES`` order."""
    out: list[Violation] = []
    if cue.duration > options.max_duration + _EPS:
        out.append(Violation(cue.index, "max_duration", cue.duration, options.max_duration))
    if cue.duration < options.min_duration - _EPS:
        out.append(Violation(cue.index, "min_duration", cue.duration, options.min_duration))
    for line in cue.lines:
        if len(line) > options.cpl:
            out.append(Violation(cue.index, "cpl", len(line), options.cpl))
    if cue.cps > options.max_cps + _EPS:
        out.append(Violation(cue.index, "cps", cue.cps, options.max_cps))
    return out
