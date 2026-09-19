"""The glossary: the names this machine is told to get right, spent twice.

One list of terms, two entirely different jobs.

**Before the decode** it is a bias. `compose_hotwords` builds the string
faster-whisper takes as `hotwords`, which it re-injects into every decode
window, so a term the user cares about is more likely to come out spelled the
way they spell it. This half moved here from `scribe.stages.transcribe`
unchanged - the composition, the token budget and the "only capitalised words
are names" rule are the same functions, re-exported there so the Phase 2 tests
still call them where they always did. They live here now because the
correction pass has to read the same rows through the same regex, and two
spellings of one rule drift.

**After the decode** it is a correction pass, and that half is governed by
ADR-003: *a correction is never a rewrite*. `word.text` is what Whisper
produced and stays that way forever. A correction is a row in
`word_correction` keyed to `(run_id, word_idx)`, and the two readers of words -
the transcript view and the export document - apply the layer with one LEFT
JOIN (`CORRECTION_JOIN` below, so neither can drift from the other). Deleting
the rows restores the original transcript byte for byte; there is nothing to
undo because nothing was done.

There is a third reader and it is *not* corrected: full-text search matches
`segment_fts`, and a segment is not a word. So a name the glossary fixed is
findable only under the spelling Whisper produced. Carrying corrections into
the index means a migration, a backfill and a trigger; until somebody wants
that, the search page says which text it matched (`any_corrections`).

### How a correction is decided

`corrections_for` slides a window of one to three words over the transcript
and scores each window against every term and every variant. Three numbers,
all measured on this machine on 2026-09-03 rather than assumed:

* **`fuzz.WRatio` needs a processor.** Without `utils.default_process` it
  compares case-sensitively and scores "why cast" against "WHYcast" at 53.3 -
  below every threshold - so the flagship case, a term Whisper split in two,
  would never fire. With it, 93.3.
* **`WRatio` alone deletes words.** It is length-forgiving on purpose: a term
  merely *contained* in a longer window scores 90, so "met Marieke" would be
  replaced by "Marieke" and the word "met" would vanish from the transcript.
  Plain `fuzz.ratio` scores that pair 77.8 and is what guards it. Since
  `ratio <= WRatio` always, the guard is what actually decides; `WRatio`
  survives as the recorded `confidence`, which is the number the plan names.
* **The plan's phonetic example does not hold here.** "Marijke" against
  "Marieke" is WRatio 85.7 - a fuzzy hit - and their metaphones differ
  ('MRJK' vs 'MRK'). "Zafod" for "Zaphod" is a real phonetic-only match: 72.7,
  and 'SFT' both ways. That is the case the second rule exists for.

The two rules, in order: a window at or above `FUZZY_THRESHOLD` is corrected
as `fuzzy`; one between `PHONETIC_FLOOR` and that threshold is corrected as
`phonetic` only if it *sounds* the same (`jellyfish.metaphone`, whitespace
removed first so a term Whisper split still rhymes with itself).

Known and accepted: a plural of a term ("mariekes" against "Marieke", 93.3)
is corrected. A glossary is a statement that this spelling is wanted; the
layer is visible in the transcript and reversible in one DELETE.

### Where a multi-word correction lands

A window of *k* words writes *k* rows: the **first** carries the whole
replacement and the rest carry the empty string. Measured, because it is not
free: an empty word standing between two others used to swallow the sentence
break the word before it had earned, since `render._ends_sentence` asks
whether the next word starts with whitespace and the empty string does not.
`render.paragraphs` now looks past empty words for that question, which is the
only change this layer forced on anything else.

### What it costs

`rapidfuzz.process.cdist` in blocks of `_MATCH_BLOCK` windows, not a Python
loop: measured on the largest transcript in the real database (6,502 words)
against a 400-term glossary, `cdist` takes 2.7 s where a per-window
`process.extractOne` loop takes 22 s. The blocks keep the score matrix bounded
at a few megabytes however long the transcript is, for the same reason
`transcribe.iter_windows` exists.

Nothing here loads a model or touches the network (ADR-001): `rapidfuzz` and
`jellyfish` are string arithmetic, and they are imported inside
`corrections_for` rather than at module scope so the web process - which
imports this module for the settings page and never runs a match - does not
pay for them.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from scribe import db

# --- the two halves of a glossary --------------------------------------------------

# Whisper's prompt window is 224 tokens and hotwords are re-injected into every
# decode window, so anything past the cap is silently lost. 223 leaves room for
# the separator token the decoder adds; see estimate_tokens for how the budget
# is counted without the model's tokenizer, which has not loaded yet.
HOTWORD_TOKEN_LIMIT = 223

# A run of letters, apostrophes and hyphens: "Marieke", "d'Artagnan",
# "Jansen-de Vries" (as two). Digits are excluded on purpose - "MP3" and
# "2026" are not names. One definition, imported by `stages.transcribe` (for
# the filename's capitals) and by `ingest.urls` (for a video title's).
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)

# At or above this, a window is the term, misspelled. Below `PHONETIC_FLOOR`
# it is a different word and nothing is done. In between, only a word that
# sounds the same is corrected.
FUZZY_THRESHOLD = 85.0
PHONETIC_FLOOR = 70.0

# How many words a single term may have been split across. Three covers
# "why cast" and "Marieke Vermeulen"; more would mostly buy false positives,
# because a longer window has more ways to score well by accident.
MAX_WINDOW_WORDS = 3

RULE_FUZZY = "fuzzy"
RULE_PHONETIC = "phonetic"
RULE_MANUAL = "manual"
"""A correction a person typed over a word in the transcript view. Same
table, same join, same restore path as the glossary's rows - and the one
rule `store` leaves alone: the glossary pass rebuilds *its* layer, and a
word somebody fixed by hand is not the glossary's to take back."""

# Windows scored per `cdist` call. Bounds the score matrix at
# _MATCH_BLOCK x len(terms) floats whatever the transcript's length.
_MATCH_BLOCK = 4096

# The one join that turns stored words into rendered words. Both readers -
# `scribe.web.transcript` and `scribe.exports.doc` - compose their SELECT from
# these two fragments, so the view and the export cannot disagree about what a
# word says. `w` is the `word` table, `c` the correction layer over it.
CORRECTION_JOIN = "LEFT JOIN word_correction c ON c.run_id = w.run_id AND c.word_idx = w.idx"
CORRECTED_TEXT = "COALESCE(c.corrected, w.text)"


@dataclass(frozen=True)
class Term:
    """One glossary entry: the spelling wanted, how hard to want it, and the
    misspellings already known to mean it.

    ``variants`` are looked for and corrected *to* ``term``; they are
    deliberately kept out of the hotwords, because biasing the decoder towards
    a misspelling works against the spelling we are asking for.
    """

    term: str
    weight: float = 1.0
    variants: tuple[str, ...] = ()
    id: int | None = None


@dataclass(frozen=True)
class Correction:
    """What one word says instead, and why. Never applied to `word.text`."""

    word_idx: int
    original: str
    corrected: str
    rule: str
    confidence: float


# --- the term list -------------------------------------------------------------------


def terms(conn: sqlite3.Connection) -> list[Term]:
    """Every term, heaviest first - the order hotwords are filled in."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT id, term, weight, variants_json FROM vocab ORDER BY weight DESC, id"
        ).fetchall()
    return [
        Term(
            term=row["term"],
            weight=float(row["weight"]),
            variants=tuple(_variants(row["variants_json"])),
            id=row["id"],
        )
        for row in rows
    ]


def add(
    conn: sqlite3.Connection,
    term: str,
    *,
    weight: float = 1.0,
    variants: Iterable[str] = (),
) -> int:
    """Add one term; returns its id. A duplicate raises `sqlite3.IntegrityError`
    (the column is UNIQUE), which the settings route turns into a 409."""
    text = (term or "").strip()
    if not text:
        raise ValueError("a glossary term cannot be blank")
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO vocab(term, weight, variants_json) VALUES (?, ?, ?)",
            (text, float(weight), json.dumps(_clean(variants))),
        )
        conn.commit()
        return cur.lastrowid


def update(
    conn: sqlite3.Connection,
    term_id: int,
    *,
    term: str | None = None,
    weight: float | None = None,
    variants: Iterable[str] | None = None,
) -> None:
    """Change what was given and leave the rest of the row alone."""
    sets: list[str] = []
    values: list[object] = []
    if term is not None:
        sets.append("term=?")
        values.append(term.strip())
    if weight is not None:
        sets.append("weight=?")
        values.append(float(weight))
    if variants is not None:
        sets.append("variants_json=?")
        values.append(json.dumps(_clean(variants)))
    if not sets:
        return
    values.append(term_id)
    with db.LOCK:
        conn.execute(f"UPDATE vocab SET {', '.join(sets)} WHERE id=?", values)
        conn.commit()


def remove(conn: sqlite3.Connection, term_id: int) -> bool:
    """Delete one term; False when there was none with that id."""
    with db.LOCK:
        cur = conn.execute("DELETE FROM vocab WHERE id=?", (term_id,))
        conn.commit()
        return cur.rowcount == 1


def import_terms(conn: sqlite3.Connection, text: str) -> int:
    """One term per line; returns how many were new.

    A line that is already in the list is skipped rather than reset: the
    weights somebody tuned are worth more than the paste that arrived.
    """
    existing = {t.term.casefold() for t in terms(conn)}
    added = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.casefold() in existing:
            continue
        add(conn, line)
        existing.add(line.casefold())
        added += 1
    return added


def export_terms(conn: sqlite3.Connection) -> str:
    """The list as `import_terms` takes it back: one term per line."""
    return "\n".join(t.term for t in terms(conn))


def _variants(raw: str | None) -> list[str]:
    try:
        loaded = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(item).strip() for item in loaded if str(item).strip()] if isinstance(loaded, list) else []


def _clean(variants: Iterable[str]) -> list[str]:
    return [str(v).strip() for v in variants if str(v).strip()]


# --- the decode bias -------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """A deliberately pessimistic token count for a hotword string.

    Whisper's tokenizer ships inside the model, which has not been loaded when
    the hotwords are composed, so this is a budget rather than a measurement:
    three characters per whitespace-separated word, rounded up. Real English
    BPE averages closer to four, and the rare proper nouns a glossary is made
    of tokenize worse than that - so erring short costs a few terms at the tail
    and erring long would silently drop terms the user explicitly asked for.
    """
    return sum(max(1, math.ceil(len(word) / 3)) for word in text.split())


def glossary_terms(conn: sqlite3.Connection) -> list[str]:
    """The user's glossary, heaviest first.

    Phonetic variants are deliberately left out: hotwords bias the decoder
    towards a spelling, and feeding it the misspellings we want corrected would
    work against that. Variants belong to the post-pass.
    """
    return [t.term.strip() for t in terms(conn) if t.term.strip()]


def name_candidates(media_row: dict) -> list[str]:
    """Capitalised words from the title, the filename and any container tags.

    Only capitalised ones. A lowercase filename yields nothing rather than a
    guess - biasing the decoder towards "gesprek" would be noise at best, and
    the token budget it eats belongs to terms somebody actually chose.
    """
    sources: list[str] = [
        str(media_row.get("title") or ""),
        Path(str(media_row.get("orig_name") or "")).stem,
    ]
    tags = media_row.get("tags")
    if isinstance(tags, dict):
        sources.extend(str(value) for value in tags.values())

    out: list[str] = []
    for text in sources:
        for token in _WORD.findall(text):
            if len(token) >= 2 and token[0].isupper():
                out.append(token)
    return out


def compose_hotwords(
    conn: sqlite3.Connection,
    media_row: dict,
    extra_terms: Sequence[str] = (),
    limit_tokens: int = HOTWORD_TOKEN_LIMIT,
) -> str:
    """Glossary first, then the import's own metadata, then the filename's
    capitals, up to the token budget.

    Filled strictly in priority order and stopped at the first term that will
    not fit, so what survives a tight budget is always the front of the list -
    a user's glossary before a video's title, and a video's title before a
    filename's accidental capitals. ``extra_terms`` is what
    `ingest.urls.hotword_terms` pulled out of a downloaded video's metadata:
    a real title, uploader and chapter list beat a filename, and the info-json
    they came from is deleted with the download job's work directory, so they
    travel in the transcribe job's params.
    """
    chosen: list[str] = []
    seen: set[str] = set()
    budget = limit_tokens

    for term in (*glossary_terms(conn), *extra_terms, *name_candidates(media_row)):
        key = term.casefold()
        if key in seen:
            continue
        cost = estimate_tokens(term) + (1 if chosen else 0)  # + the separator
        if cost > budget:
            break
        seen.add(key)
        chosen.append(term)
        budget -= cost

    return ", ".join(chosen)


# --- the correction pass ------------------------------------------------------------


def words_of(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """The stored words of a run, as `corrections_for` wants them.

    Read from the table rather than taken from the stage's handoff because
    `text_edited_by_user` is only ever true in the database: a word a person
    retyped outranks any glossary, and the pass has to be able to see that
    whether it runs inside the pipeline or a month later from the settings
    page.

    That column and not `edited_by_user`, which is a fact about *speakers* -
    see `_windows` and db.py's v9 comment for the bug that distinction fixes.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT idx, text, text_edited_by_user FROM word WHERE run_id=? ORDER BY idx",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def corrections_for(
    words: Sequence[dict],
    terms: Sequence[Term | str],
    *,
    fuzzy_threshold: float = FUZZY_THRESHOLD,
    phonetic_floor: float = PHONETIC_FLOOR,
    max_window: int = MAX_WINDOW_WORDS,
) -> list[Correction]:
    """What this glossary would change about these words. Writes nothing.

    A pure function over rows, so every awkward case - a term the decoder
    split in two, a word somebody already fixed by hand, a term that is
    contained in a longer phrase - is a two-line test rather than a
    transcription run. The rules are in the module docstring.
    """
    rows = [dict(word) for word in words]
    lookups = _lookups(terms)
    if not rows or not lookups:
        return []

    windows = _windows(rows, max_window)
    if not windows:
        return []

    from rapidfuzz import fuzz, process, utils

    processor = utils.default_process
    needles = [needle for needle, _canonical in lookups]

    # start index -> the matches that begin there, best first.
    candidates: dict[int, list[tuple]] = {}
    for block in range(0, len(windows), _MATCH_BLOCK):
        chunk = windows[block : block + _MATCH_BLOCK]
        scores = process.cdist(
            [core for _start, _length, core in chunk],
            needles,
            scorer=fuzz.WRatio,
            processor=processor,
            score_cutoff=phonetic_floor,
            workers=-1,
        )
        for row, (start, length, core) in enumerate(chunk):
            for column, wratio in enumerate(scores[row]):
                # cdist zeroes everything under the cutoff; ratio <= WRatio, so
                # nothing acceptable is lost by narrowing on WRatio first.
                if not wratio:
                    continue
                needle, canonical = lookups[column]
                plain = fuzz.ratio(core, needle, processor=processor)
                rule = _rule(plain, core, needle, fuzzy_threshold, phonetic_floor)
                if rule is None:
                    continue
                replacement = _replacement(rows[start : start + length], canonical)
                if replacement == "".join(
                    str(w.get("text") or "") for w in rows[start : start + length]
                ):
                    continue  # already spelled the way the glossary wants it
                candidates.setdefault(start, []).append(
                    (plain, length, float(wratio), replacement, rule)
                )

    out: list[Correction] = []
    index = 0
    while index < len(rows):
        here = candidates.get(index)
        if not here:
            index += 1
            continue
        # Best score wins; a tie goes to the longer window, because a term that
        # scores the same over two words as over one was split, not shortened.
        _plain, length, wratio, replacement, rule = max(here, key=lambda c: (c[0], c[1]))
        confidence = wratio / 100.0
        for offset in range(length):
            word = rows[index + offset]
            out.append(
                Correction(
                    word_idx=int(word["idx"]),
                    original=str(word.get("text") or ""),
                    corrected=replacement if offset == 0 else "",
                    rule=rule,
                    confidence=confidence,
                )
            )
        index += length
    return out


def _lookups(terms: Sequence[Term | str]) -> list[tuple[str, str]]:
    """(what to look for, what to write) for every term and every variant."""
    out: list[tuple[str, str]] = []
    for entry in terms:
        canonical = (entry.term if isinstance(entry, Term) else str(entry)).strip()
        if not canonical:
            continue
        out.append((canonical, canonical))
        for variant in entry.variants if isinstance(entry, Term) else ():
            text = str(variant).strip()
            if text:
                out.append((text, canonical))
    return out


def _windows(rows: Sequence[dict], max_window: int) -> list[tuple[int, int, str]]:
    """(start, length, comparable text) for every window worth scoring.

    A window stops at a word whose *text* a person edited rather than skipping
    over it: a correction spanning a hand-typed word would replace it too.

    `text_edited_by_user`, never `edited_by_user`. The two were one column
    until v9, and the guard read the wrong fact: `edited_by_user` is written
    only by the speaker-reassignment route, which by ADR-003 changes a word's
    cluster and not one character of its text. So fixing mislabelled
    diarization by hand used to make that range permanently uncorrectable -
    `store` deletes the layer and re-inserts, and the skipped range
    regenerated nothing. Pinned by
    `tests/test_glossary.py::test_reassigning_speakers_does_not_make_a_range_uncorrectable`.

    Nothing writes `text_edited_by_user` today, because no route in this app
    edits a word's text; the guard is here for the one that eventually does.
    """
    out: list[tuple[int, int, str]] = []
    for start, word in enumerate(rows):
        if word.get("text_edited_by_user"):
            continue
        for length in range(1, max_window + 1):
            if start + length > len(rows):
                break
            span = rows[start : start + length]
            if span[-1].get("text_edited_by_user"):
                break
            core = _core("".join(str(w.get("text") or "") for w in span))
            if core:
                out.append((start, length, core))
    return out


def _core(text: str) -> str:
    """A window's text as it is compared: no surrounding space or punctuation."""
    return text.strip().strip("\"'“”‘’.,;:!?…()[]{}«»-–—")


def _replacement(span: Sequence[dict], canonical: str) -> str:
    """What the first word of ``span`` says instead.

    The window's own leading whitespace and its own trailing punctuation are
    kept, because they are not part of what was misheard: " Vermulen," becomes
    " Vermeulen,". The remaining words of the window go empty, so joining the
    run reproduces the corrected phrase exactly once.
    """
    first = str(span[0].get("text") or "")
    last = str(span[-1].get("text") or "")
    lead = first[: len(first) - len(first.lstrip())]
    trail = re.search(r"[^\w]*$", last, re.UNICODE).group(0)
    if len(span) == 1 and len(lead) + len(trail) >= len(first):
        # A one-word window that is all padding has no core to replace; the
        # window builder skips those, and this keeps the arithmetic honest.
        trail = ""
    return lead + canonical + trail


def _rule(
    plain: float, core: str, needle: str, fuzzy_threshold: float, phonetic_floor: float
) -> str | None:
    """Which rule fires for this pair, or None for neither.

    ``plain`` is `fuzz.ratio`, not `WRatio`: see the module docstring for the
    "met Marieke" case that makes the difference.
    """
    if plain >= fuzzy_threshold:
        return RULE_FUZZY
    if plain >= phonetic_floor and _sounds_alike(core, needle):
        return RULE_PHONETIC
    return None


def _sounds_alike(left: str, right: str) -> bool:
    """Metaphone equality, whitespace removed first.

    Removed because the case this rule is for is a name, and a name Whisper
    split into two words ("Za phod") has to still rhyme with the one word it
    should have been.
    """
    return _metaphone(left) == _metaphone(right)


def _metaphone(text: str) -> str:
    import jellyfish

    return jellyfish.metaphone(re.sub(r"\s+", "", text))


# --- the layer in the database ----------------------------------------------------


def store(
    conn: sqlite3.Connection, run_id: int, corrections: Sequence[Correction]
) -> int:
    """Replace this run's correction layer; returns how many rows it now has.

    Replace, never append: the layer is derived from the glossary as it is
    now, so re-running the pass after a term was removed has to take that
    term's corrections away with it.

    The DELETE and the INSERTs are one transaction, and that is what makes it
    safe across processes as well as within one (ADR-002 - `db.LOCK` is
    in-process and settles nothing between two app instances). SQLite takes the
    write lock at the DELETE and holds it to the commit, so a second writer
    waits out `busy_timeout` and then does its own delete-and-insert over these
    rows; it cannot see the layer half written, and the UNIQUE index cannot
    fire against a run being rewritten. Measured in
    `tests/test_glossary.py::test_a_second_writer_waits_for_the_first_instead_of_doubling_the_layer`.
    """
    now = time.time()
    with db.LOCK:
        try:
            # A hand-made correction outranks the glossary on its word: the
            # row stays, and a glossary correction aimed at the same word is
            # not written (the UNIQUE index would refuse it anyway; skipping
            # is the honest version of that refusal).
            manual = {
                int(row[0])
                for row in conn.execute(
                    "SELECT word_idx FROM word_correction WHERE run_id=? AND rule=?",
                    (run_id, RULE_MANUAL),
                )
            }
            kept = [fix for fix in corrections if fix.word_idx not in manual]
            conn.execute(
                "DELETE FROM word_correction WHERE run_id=? AND rule != ?", (run_id, RULE_MANUAL)
            )
            conn.executemany(
                "INSERT INTO word_correction(run_id, word_idx, original, corrected,"
                " rule, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id, fix.word_idx, fix.original, fix.corrected,
                        fix.rule, fix.confidence, now,
                    )
                    for fix in kept
                ],
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return len(kept)


# --- corrections a person types -----------------------------------------------------


def _word_row(conn: sqlite3.Connection, run_id: int, idx: int) -> dict:
    with db.LOCK:
        row = conn.execute(
            "SELECT idx, text FROM word WHERE run_id=? AND idx=?", (run_id, idx)
        ).fetchone()
    if row is None:
        raise LookupError(f"run {run_id} has no word {idx}")
    return dict(row)


def correct_word(conn: sqlite3.Connection, run_id: int, idx: int, text: str) -> str | None:
    """Put ``text`` over word ``idx`` by hand; returns what the word now says.

    The typed text replaces the word's core only: the leading space Whisper
    gives every word and the punctuation that trails it are kept, so "Vermulen,"
    typed over as "Vermeulen" becomes " Vermeulen,". An empty ``text`` takes
    the hand-made correction away and the word says what Whisper said - which
    is the restore path this layer promises (ADR-003), one row at a time.
    """
    word = _word_row(conn, run_id, idx)
    core = text.strip()
    with db.LOCK:
        if not core:
            conn.execute(
                "DELETE FROM word_correction WHERE run_id=? AND word_idx=?", (run_id, idx)
            )
            conn.commit()
            return None
        corrected = _replacement([word], core)
        conn.execute(
            "INSERT INTO word_correction(run_id, word_idx, original, corrected, rule,"
            " confidence, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?)"
            " ON CONFLICT(run_id, word_idx) DO UPDATE SET"
            "   original = excluded.original, corrected = excluded.corrected,"
            "   rule = excluded.rule, confidence = NULL, created_at = excluded.created_at",
            (run_id, idx, word["text"], corrected, RULE_MANUAL, time.time()),
        )
        conn.commit()
    learn_from_correction(conn, word["text"], core)
    return corrected


LEARNED_WEIGHT = 1.0
"""The weight a term learned from a correction starts at.

The same as a term typed into Settings, because the evidence behind it is at
least as good: somebody read what Whisper produced, knew it was wrong, and
typed what it should say. A term a person already weighted is never demoted to
this - `learn_from_correction` adds the variant and leaves the weight alone."""


def looks_like_a_name(text: str) -> bool:
    """Is this worth biasing the next decode towards?

    The glossary's existing rule, applied to a new source: only capitalised
    words are names (`name_candidates`), and at least two letters. "teh"
    corrected to "the" must not become a hotword - the decoder knows that word,
    and the token budget it would eat belongs to terms somebody chose.

    One word only. A correction spanning several words is a phrase, and a
    phrase is a thing a person can add to the glossary deliberately; learning
    it here would fill the list with sentence fragments.
    """
    core = _core(text)
    tokens = _WORD.findall(core)
    return len(tokens) == 1 and tokens[0] == core and len(core) >= 2 and core[0].isupper()


def _already_claimed(heard: str, known: Sequence[Term]) -> bool:
    """Would any term already here correct ``heard``?

    Asked through `corrections_for`, the same pass that will run over the next
    transcript, so the question is the one that actually matters rather than a
    second opinion about it.
    """
    if not known:
        return False
    word = [{"idx": 0, "text": f" {heard}"}]
    return bool(corrections_for(word, known))


def learn_from_correction(conn: sqlite3.Connection, heard: str, typed: str) -> int | None:
    """Teach the glossary what a person just corrected. Returns the term id.

    The correction is the lesson (TASK-087): the term is what was typed, and
    what Whisper produced becomes a variant - which is exactly the pair
    `corrections_for` scores against, so the next recording is fixed by the
    same machinery that had nothing to work with before. Before the decode it
    is a hotword, so the recording after that may not need fixing at all.

    Never raises into the correction it follows. A glossary that cannot be
    taught is a worse day than a correction that fails to save, and ADR-003
    puts `word.text` out of reach either way: nothing here writes a word, and
    lifting the correction off still restores the transcript byte for byte.
    """
    try:
        term = _core(typed)
        variant = _core(heard)
        if not looks_like_a_name(term):
            return None

        known = terms(conn)
        existing = next((t for t in known if t.term.casefold() == term.casefold()), None)
        keep = variant if variant and variant.casefold() != term.casefold() else None

        # Do not take a spelling another term already answers for. A person who
        # types "Vermeulen-Smit" over one word means that word; keeping
        # "Vermulen" as its variant would make every other "Vermulen" in the
        # library - including the ones an existing "Vermeulen" already fixes -
        # come out hyphenated. The name is still worth a hotword; the variant
        # is what would have rewritten other people's sentences.
        if keep and _already_claimed(keep, [t for t in known if t is not existing]):
            keep = None

        if existing is None:
            return add(conn, term, weight=LEARNED_WEIGHT, variants=[keep] if keep else [])
        if keep and keep.casefold() not in {v.casefold() for v in existing.variants}:
            # The weight is the user's business: a term somebody set to 5.0
            # stays at 5.0 and gains a spelling.
            update(conn, existing.id, variants=[*existing.variants, keep])
        return existing.id
    except Exception:  # noqa: BLE001 - teaching must never fail the correction
        return None


def same_word(conn: sqlite3.Connection, run_id: int, idx: int) -> list[dict]:
    """The other words of the run that say what word ``idx`` says (its core,
    case-insensitively, as Whisper wrote it) - the candidates for "replace
    every occurrence". Read from `word.text`, not through the correction
    layer: what was misheard once was misheard the same way elsewhere, and
    a word already corrected by hand to the same thing is left out."""
    target = _core(_word_row(conn, run_id, idx)["text"]).casefold()
    if not target:
        return []
    with db.LOCK:
        rows = conn.execute(
            f"SELECT w.idx, w.text, c.rule AS rule, {CORRECTED_TEXT} AS shown"
            f" FROM word w {CORRECTION_JOIN} WHERE w.run_id=? AND w.idx != ? ORDER BY w.idx",
            (run_id, idx),
        ).fetchall()
    return [
        dict(row) for row in rows
        if _core(str(row["text"])).casefold() == target and row["rule"] != RULE_MANUAL
    ]


def correct_same(conn: sqlite3.Connection, run_id: int, idx: int, text: str) -> int:
    """Put ``text`` over word ``idx`` and every other word that says the same;
    returns how many words now carry it."""
    core = text.strip()
    if not core:
        return 0
    others = same_word(conn, run_id, idx)
    correct_word(conn, run_id, idx, core)
    for other in others:
        correct_word(conn, run_id, int(other["idx"]), core)
    return 1 + len(others)


def clear(conn: sqlite3.Connection, run_id: int) -> int:
    """Delete this run's corrections; returns how many went. The transcript
    that comes back is the one Whisper produced, to the byte."""
    with db.LOCK:
        cur = conn.execute("DELETE FROM word_correction WHERE run_id=?", (run_id,))
        conn.commit()
        return cur.rowcount


def stored(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """This run's correction rows, in transcript order."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT * FROM word_correction WHERE run_id=? ORDER BY word_idx", (run_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def any_corrections(conn: sqlite3.Connection) -> bool:
    """Whether this library has a correction layer at all, anywhere.

    Asked by the search page, which is a *third* reader of the transcript that
    this module's docstring does not count: it matches `segment_fts`, and no
    correction touches a segment. So a term the glossary fixed is findable
    only under the spelling Whisper produced, and a search for the corrected
    one finds nothing. Teaching the index the corrected text means a
    migration, a backfill and a trigger; until then the page says so, and
    only where it can bite - a library with no corrections is not misleading
    anybody and should not be told it might be.
    """
    with db.LOCK:
        row = conn.execute("SELECT 1 FROM word_correction LIMIT 1").fetchone()
    return row is not None


def media_with_transcripts(conn: sqlite3.Connection) -> list[int]:
    """Every media that has a current run - what a library-wide re-run covers.

    Current runs only, and that is the whole scope of a re-run. A
    re-transcription leaves the run before it in place with its correction
    layer, and that layer is not refreshed by anything: it belongs to that run,
    it was right when it was made, and no route in this app renders a run that
    is not current - `web.transcript.current_run` is the only chooser, and
    every export path calls `exports.doc.load` without a run id. The single
    caller that passes one is the llm stage, with the run its own plan just
    resolved.

    Said out loud because `doc.load(conn, media_id, run_id)` is a public path:
    whoever wires a route to an older run gets that run's corrections as they
    were, not as the glossary is now. The layer is derived, so the fix if that
    day comes is to widen this query, not to keep two of them in step.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT DISTINCT m.id FROM media m JOIN run r ON r.media_id = m.id"
            " WHERE r.is_current=1 AND m.trashed_at IS NULL ORDER BY m.id"
        ).fetchall()
    return [int(row["id"]) for row in rows]
