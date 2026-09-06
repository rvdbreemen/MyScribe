"""Everything a reader sees that is not a word, computed from the words.

Words are the canonical transcript (ADR-003): the `word` table holds start,
end, text, probability and speaker per word, and nothing grouped is ever
stored. Paragraphs, sentences, timestamps, speaker names and confidence bands
are therefore pure functions in this module, evaluated when a page or an export
is rendered. A rename changes one `speaker_label` row and every heading on the
next render follows; a reassignment flips `word.speaker` on a range and the
paragraphs regroup themselves. There is no cache to invalidate and no
"Resegment" button, because there is nothing stored that could go stale.

The rules, as the spec states them (section 3, "derived-only segmentation"):

* A **paragraph** ends on a speaker change, on a silence of at least `gap`
  seconds between one word's end and the next word's start, or when it fills
  its character budget. The budget works like word-wrap: when the next word
  would take the paragraph past `max_chars`, the paragraph closes at the last
  sentence end it contains and the unfinished sentence carries over. Only a
  sentence that on its own outgrows the budget - a language written without
  full stops, a speaker who never uses them - is cut mid-way, so a ten-hour
  monologue still becomes something a browser can lay out.
* A **sentence** ends at sentence-final punctuation (`.?!…`, closing quotes
  and brackets allowed after it) followed by whitespace, at a paragraph break,
  or at the end of the transcript. The whitespace requirement is what keeps
  `3.14` and `e.g.` mid-sentence: faster-whisper puts each word's leading
  space on the word itself, so "followed by whitespace" means "the next word
  starts with one".
* **Text is joined with nothing in between.** Every word carries its own
  leading space when the language has them; concatenating and stripping once
  reproduces the transcript exactly, including in languages that put no space
  between words, where inserting one would be wrong.
* **A word with no text is not a word.** The glossary's correction layer
  (`scribe.glossary`, ADR-003) shows a term Whisper split in two as one word
  and an empty one, so "the next word" in the sentence-end rule means the next
  word that has text. Measured before it was written: without this, "Hallo
  daar. why cast." rendered as a single sentence, because `_ends_sentence`
  asks whether the following word starts with whitespace and the empty string
  does not.

Nothing here touches the database or imports from `scribe.web`; the callers
hand in rows (dicts or `sqlite3.Row`) and get view models back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# Spec section 3: speaker change / >2 s gap / ~700 chars.
PARAGRAPH_GAP = 2.0
PARAGRAPH_MAX_CHARS = 700

# What ends a sentence, and what may trail it without un-ending it.
SENTENCE_END = ".?!…"
SENTENCE_CLOSERS = "\"'’”)]»"

# Confidence bands for the transcript tint. Chosen so that the cycle of
# probabilities the seed helpers write lands one word in each band.
CONFIDENCE_HIGH = 0.85
CONFIDENCE_MID = 0.6

# pyannote's cluster labels. The number is zero-based; people are not.
_CLUSTER_LABEL = re.compile(r"SPEAKER_(\d+)")


@dataclass
class Sentence:
    """A run of words up to sentence-final punctuation (or a break)."""

    start: float
    end: float
    text: str
    words: list[dict]


@dataclass
class Paragraph:
    """One speaker's uninterrupted stretch, as a list of sentences.

    ``speaker`` is the diarization cluster label and ``display_name`` the name
    to show for it - None for both when the run was not diarized, which is how
    a template knows to write no heading at all.
    """

    speaker: str | None
    display_name: str | None
    start: float
    end: float
    sentences: list[Sentence]

    @property
    def words(self) -> list[dict]:
        return [word for sentence in self.sentences for word in sentence.words]

    @property
    def text(self) -> str:
        return join_text(self.words)


# --- the small pieces ----------------------------------------------------------


def join_text(words: Sequence[Any]) -> str:
    """The words' text, concatenated as stored and stripped once."""
    return "".join(str(_field(word, "text") or "") for word in words).strip()


def confidence_band(probability: float | None) -> str:
    """``high`` / ``mid`` / ``low`` for a word probability; ``unknown`` for None."""
    if probability is None:
        return "unknown"
    value = float(probability)
    if value >= CONFIDENCE_HIGH:
        return "high"
    if value >= CONFIDENCE_MID:
        return "mid"
    return "low"


def format_ts(seconds: float | None, style: str = "clock") -> str:
    """Seconds as ``m:ss`` (``h:mm:ss`` from an hour up) or as SRT ``hh:mm:ss,mmm``.

    The clock style floors to whole seconds, as every player's display does;
    the SRT style rounds to the millisecond, as the format expects. None comes
    back as an empty string: ``media.duration`` is NULL until probe has run,
    and a library row must still render.
    """
    if seconds is None:
        return ""
    if style == "clock":
        total = max(0, int(seconds))
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"
    if style == "srt":
        total_ms = max(0, round(seconds * 1000))
        hours, rest = divmod(total_ms, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        secs, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    raise ValueError(f"unknown timestamp style {style!r}; use 'clock' or 'srt'")


def speaker_display(labels: Mapping[str, str] | None, cluster: str | None) -> str:
    """The name to show for a cluster: its label, else ``Speaker N``.

    ``SPEAKER_03`` is the fourth speaker pyannote found, so it displays as
    ``Speaker 4``. A cluster that is not one of pyannote's (a ``USER_n`` label
    without a name yet) is shown as it is rather than guessed at; a blank label
    counts as no label, so clearing a name in the UI restores the default.
    """
    if cluster is None:
        return ""
    name = (labels or {}).get(cluster)
    if name is not None and str(name).strip():
        return str(name).strip()
    match = _CLUSTER_LABEL.fullmatch(cluster)
    if match:
        return f"Speaker {int(match.group(1)) + 1}"
    return cluster


# --- grouping --------------------------------------------------------------------


def paragraphs(
    words: Sequence[Any],
    labels: Mapping[str, str] | None = None,
    *,
    gap: float = PARAGRAPH_GAP,
    max_chars: int = PARAGRAPH_MAX_CHARS,
) -> list[Paragraph]:
    """Group ``words`` (in transcript order) into paragraphs of sentences.

    One pass, O(words). The rules are in the module docstring; the shape of the
    loop is that a paragraph is a list of closed sentences plus one sentence
    still being read, and each break closes one or both of them.
    """
    out: list[Paragraph] = []
    closed: list[Sentence] = []  # finished sentences of the paragraph being built
    open_words: list[Any] = []  # the sentence still being read
    closed_chars = 0
    open_chars = 0
    speaker: str | None = None
    previous: Any = None

    def close_sentence() -> None:
        nonlocal open_words, closed_chars, open_chars
        if open_words:
            closed.append(_sentence(open_words))
            closed_chars += open_chars
            open_words, open_chars = [], 0

    def emit_paragraph() -> None:
        nonlocal closed, closed_chars
        if closed:
            out.append(
                Paragraph(
                    speaker=speaker,
                    display_name=None if speaker is None else speaker_display(labels, speaker),
                    start=closed[0].start,
                    end=closed[-1].end,
                    sentences=closed,
                )
            )
        closed, closed_chars = [], 0

    for i, word in enumerate(words):
        length = len(str(_field(word, "text") or ""))
        current = _field(word, "speaker")

        if previous is not None and (
            current != speaker
            or float(_field(word, "start")) - float(_field(previous, "end")) >= gap
        ):
            close_sentence()
            emit_paragraph()
        elif closed_chars + open_chars + length > max_chars and (closed or open_words):
            if closed:
                # Break at the last sentence end; the open sentence carries over.
                emit_paragraph()
            else:
                # One sentence has outgrown the whole budget: cut it here.
                close_sentence()
                emit_paragraph()

        speaker = current
        open_words.append(word)
        open_chars += length
        if _ends_sentence(word, _next_with_text(words, i + 1)):
            close_sentence()
        previous = word

    close_sentence()
    emit_paragraph()
    return out


def _sentence(words: list[Any]) -> Sentence:
    return Sentence(
        start=float(_field(words[0], "start")),
        end=float(_field(words[-1], "end")),
        text=join_text(words),
        words=list(words),
    )


def _next_with_text(words: Sequence[Any], start: int) -> Any | None:
    """The first word at or after ``start`` that says anything, else None.

    A correction that folds two words into one leaves the second empty
    (`scribe.glossary`), and an empty word must not stand between a full stop
    and the space that proves it ended a sentence. The scan is bounded by the
    longest such run - two words - so this stays the single pass the module
    docstring claims.
    """
    for index in range(start, len(words)):
        if str(_field(words[index], "text") or ""):
            return words[index]
    return None


def _ends_sentence(word: Any, following: Any | None) -> bool:
    """Sentence-final punctuation on ``word``, followed by whitespace or by nothing."""
    raw = str(_field(word, "text") or "")
    text = raw.rstrip().rstrip(SENTENCE_CLOSERS)
    if not text or text[-1] not in SENTENCE_END:
        return False
    if following is None:
        return True
    next_text = str(_field(following, "text") or "")
    return raw[-1:].isspace() or next_text[:1].isspace()


def _field(row: Any, key: str, default: Any = None) -> Any:
    """``row[key]`` for a dict or an ``sqlite3.Row``, ``default`` when absent.

    Words arrive as dicts from the stages and as Rows from the database; the
    former raises KeyError for a missing key and the latter IndexError. Words
    that have not been through attribution have no ``speaker`` at all, and
    they still deserve paragraphs.
    """
    try:
        return row[key]
    except (KeyError, IndexError):
        return default
