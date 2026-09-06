"""Chat with a transcript: what the model gets to read, and citations that seek.

The six preset tasks in `tasks.py` all ask the same question of the whole
recording. Chat is the one feature where the *input* is chosen at request time:
a question decides which parts of a long recording the model is shown. So this
module is mostly two things - retrieval, and what comes back attached to the
answer - and it leans on everything already built rather than repeating it.
Every call still goes through `llm.chat()`, so the private-mode pin, the
provider registry and the retry policy are the same ones the tasks use, and
chat runs as a *kind* of the `llm` job rather than a job type of its own, so it
queues behind the same GPU lease (ADR-001, Global Constraints).

**The context is the whole transcript whenever the whole transcript fits.**
Retrieval that runs when it does not have to is a way to lose the answer: a
model cannot cite a line it was never shown, and "the transcript does not say"
is a much worse failure than one extra chunk of tokens. Only when the timestamped
transcript is over budget does FTS5 pick the segments that match the question -
each with one neighbour either side, because a hit on its own is half a
sentence - and the parts that were left out are marked with a `…` line so the
model knows it is reading excerpts rather than a recording that jumps.

**The ids that come back are run-scoped `segment.idx`, not FTS rowids.** The two
number spaces look alike and are not (see `chunking.Chunk.segment_ids`, which
carries the same kind). `idx` is the one that means anything to a caller holding
a `TranscriptDoc`, and it is the only one in which "the neighbouring segment" is
a definition rather than a guess: rowid adjacency is an accident of insertion
order, `idx ± 1` is what "the line before" means.

**A citation is a link to a moment, so a citation that cannot be seeked is not
kept.** The system prompt asks for `[m:ss]` - the clock the transcript shows,
which is the clock the player already seeks by - and `parse_citations` reads
them back out of the answer and drops anything past the end of the recording. A
hallucinated `[99:00]` on a three-minute clip would otherwise render as a link
to nowhere, which is worse than no link at all.

**The prompts here are module constants, not files in `prompts/`.** That
directory holds the six templates `tasks.PROMPT_VERSION` keys, and every answer
stored under that version is a claim about the question those files asked; a
chat turn is stored with no prompt version at all (there is nothing to
regenerate later), so adding a seventh file would put an unrelated prompt inside
that key's digest. `tasks.SYSTEM` is reused verbatim, so how to read a
timestamped transcript is stated once for the whole package.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from scribe import db, llm, render
from scribe.exports import doc as docs
from scribe.llm import base, chunking, privacy, tasks
from scribe.llm.base import ChatRequest, ChatResponse
from scribe.llm.chunking import estimate_tokens, timestamped_line, transcript_text

CHAT_KIND = "chat"
"""The `llm` job kind this module answers. Deliberately not in `tasks.KINDS`:
the six there are one-shot outputs stored in `llm_output` and keyed by prompt
version, and a conversation is neither."""

USER = "user"
ASSISTANT = "assistant"
"""The whole `chat_message.role` vocabulary, and the CHECK in the v5 migration
(`db._SCHEMA_V5`): every reader switches on it, so a third value would be a row
nothing renders."""

GAP_MARKER = "…"
"""What stands where a stretch of transcript was left out.

One character, no brackets: everything in brackets in this context is a
timestamp the model is invited to copy, and a `[skipped]` marker would be the
one bracket that is not."""

NEIGHBOUR_SEGMENTS = 1
"""How far either side of a hit to widen. A matching segment on its own is
usually half of the exchange that answers the question - the question in one
segment and the answer in the next is the normal shape of speech."""

MAX_HITS = 40
"""How many FTS hits to consider before the budget decides. Past this the
ranking has stopped being about the question."""

MIN_TERM_CHARS = 2
"""Shorter than this and a term is punctuation or an initial, not a search."""

MAX_HISTORY_MESSAGES = 8
"""Four exchanges of memory. `ChatRequest` carries one user turn, so history is
folded into that turn and is spent out of the same window the transcript is;
without a cap, turn twelve of a conversation would quietly leave no room for
the recording it is about."""

HISTORY_CHARS = 1000
"""How much of one earlier turn is replayed. A long answer clipped in the middle
still carries what it was about, and `tasks.clip` says how much it dropped."""

MARKER_TOKENS = 1
"""What a `…` line costs, charged per chosen segment plus one during the fill.

There is at most one marker per contiguous stretch plus one at the front, and
there are never more stretches than segments, so charging this per segment is a
strict upper bound on what the markers actually cost. That is what makes "the
context never exceeds the budget it was given" true by construction rather than
by inspection of the rendered string."""


# --- what comes back --------------------------------------------------------------------


@dataclass(frozen=True)
class ChatAnswer:
    """One answer, and the moments in the recording it can be clicked through to.

    `citations` are seconds, already filtered to what the player can seek to.
    `segment_ids` are the run-scoped `segment.idx` values the answer was allowed
    to read - provenance, so a panel can say "answered from four excerpts" and a
    bug report can say which.
    """

    text: str
    citations: list[float] = field(default_factory=list)
    segment_ids: tuple[int, ...] = ()
    response: ChatResponse | None = None


_BRACKETED = re.compile(r"\[[^\[\]\n]{1,60}\]")
_CLOCK = re.compile(r"(?<!\d)(\d{1,3}):([0-5]\d)(?::([0-5]\d))?(?!\d)")


def parse_citations(text: str, duration: float | None = None) -> list[float]:
    """The seekable `[m:ss]` timestamps in `text`, in the order they appear.

    Brackets are the citation syntax, and only brackets: "we agreed at 1:23" in
    prose is the model narrating, while `[1:23]` is the model pointing. Anything
    inside a bracket is searched for clock shapes, so `[1:23]`, `[01:02:03]` and
    `[1:23-1:30]` all read, and `[abc]` yields nothing rather than an error.

    With a `duration`, a timestamp past the end of the recording is dropped:
    the citation renders as a seek link, and a link to nowhere is worse than no
    link. Without one, nothing is dropped - a caller that does not know how long
    the recording is has no business ruling on it.

    Repeats are dropped, keeping the first appearance: the list drives a set of
    links, and the same moment twice is one moment.
    """
    seen: list[float] = []
    for bracket in _BRACKETED.finditer(text or ""):
        for match in _CLOCK.finditer(bracket.group(0)):
            parts = [p for p in match.groups() if p is not None]
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
            if duration is not None and duration > 0 and seconds > duration:
                continue
            if seconds not in seen:
                seen.append(seconds)
    return seen


# --- what the model reads ---------------------------------------------------------------

_WORD = re.compile(r"\w+", re.UNICODE)


def fts_query(question: str) -> str:
    """The question as an FTS5 query: its words, quoted, ORed together.

    Two deliberate differences from `web.library.fts_query`, which ANDs the
    terms - and the reason this is a second function rather than an import
    (`scribe.llm` must not depend on `scribe.web`, and the two want opposite
    things):

    * **OR, not AND.** "What did they say about the budget?" ANDed matches
      nothing at all, because no single segment contains every word of a
      question. Retrieval wants the segments that match *any* of it, ranked.
    * **No stopword list.** bm25 already does what a stopword list does, and
      does it in whatever language the recording is in: a term that appears in
      every segment carries almost no weight in the ranking. A hand-written list
      would be an English one, applied to a Dutch transcript.

    Words are extracted rather than split, so an apostrophe or a question mark
    never reaches the query as syntax. An empty result means "do not run a
    query": `segment_fts MATCH ''` is an OperationalError, not an empty result.
    """
    terms: list[str] = []
    for word in _WORD.findall((question or "").lower()):
        if len(word) >= MIN_TERM_CHARS and word not in terms:
            terms.append(word)
    return " OR ".join(f'"{term}"' for term in terms)


def _widen(position: int, neighbours: int, count: int) -> list[int]:
    """A hit's own position first, then outwards a step at a time.

    Order matters under a budget: the segment that actually matched must never
    be the one dropped to make room for its neighbour.
    """
    positions = [position]
    for step in range(1, neighbours + 1):
        for candidate in (position - step, position + step):
            if 0 <= candidate < count:
                positions.append(candidate)
    return positions


def retrieve(
    conn: sqlite3.Connection,
    doc: docs.TranscriptDoc,
    question: str,
    *,
    budget_tokens: int,
    neighbours: int = NEIGHBOUR_SEGMENTS,
) -> list[int]:
    """The `segment.idx` values worth sending for `question`, in time order.

    Ranked by bm25, widened by `neighbours` either side, filled until the budget
    is spent, and only then sorted by time. Rank decides *what* is sent; time
    decides the order it is sent in, because a model reading `[3:00]` before
    `[1:00]` will narrate the recording backwards.

    A candidate that does not fit is skipped rather than ending the fill, so a
    short segment from a lower-ranked hit can still use the room a long one
    could not. Empty when the question has no searchable words or nothing
    matched - the caller decides what to send instead.

    **The run filter is inside the query, not applied to its result.**
    `segment_fts` is one index over every recording in the library and every run
    of each, so the limit has to count hits in *this* transcript - filtering
    afterwards would let a busy library push this recording's only hit past the
    fortieth row and return nothing. It is also what keeps a foreign hit from
    being read as a local one: `idx` means something only inside its own run,
    and `idx` 9 of another recording would otherwise select segment 9 of this
    one, building an answer on a passage nothing matched.
    """
    match = fts_query(question)
    if not match:
        return []

    with db.LOCK:
        rows = conn.execute(
            "SELECT s.idx AS idx FROM segment_fts"
            " JOIN segment s ON s.id = segment_fts.rowid"
            " WHERE segment_fts MATCH ? AND s.run_id = ?"
            " ORDER BY bm25(segment_fts) LIMIT ?",
            (match, int(doc.run["id"]), MAX_HITS),
        ).fetchall()
    if not rows:
        return []

    positions = {int(segment["idx"]): i for i, segment in enumerate(doc.segments)}
    costs = [estimate_tokens(timestamped_line(s)) + MARKER_TOKENS for s in doc.segments]
    count = len(doc.segments)

    chosen: set[int] = set()
    spent = MARKER_TOKENS  # the marker in front of the first excerpt
    for row in rows:
        start = positions.get(int(row["idx"]))
        if start is None:  # a hit from a segment this doc does not carry
            continue
        for candidate in _widen(start, neighbours, count):
            if candidate in chosen:
                continue
            if chosen and spent + costs[candidate] > budget_tokens:
                continue
            chosen.add(candidate)
            spent += costs[candidate]

    return [int(doc.segments[position]["idx"]) for position in sorted(chosen)]


def render_context(doc: docs.TranscriptDoc, segment_idxs: Sequence[int]) -> str:
    """The chosen segments as timestamped lines, with `…` where text is missing.

    The markers are for the model, not for looks: without them a jump from
    `[1:12]` to `[9:40]` reads as a recording where nothing was said for eight
    minutes, and an answer built on that is wrong in a way nobody can see.
    """
    positions = {int(segment["idx"]): i for i, segment in enumerate(doc.segments)}
    wanted = sorted(positions[idx] for idx in segment_idxs if idx in positions)
    if not wanted:
        return ""

    lines: list[str] = []
    previous: int | None = None
    for position in wanted:
        if previous is None:
            if position > 0:
                lines.append(GAP_MARKER)
        elif position != previous + 1:
            lines.append(GAP_MARKER)
        lines.append(timestamped_line(doc.segments[position]))
        previous = position
    if wanted[-1] < len(doc.segments) - 1:
        lines.append(GAP_MARKER)
    return "\n".join(lines)


def context_for(
    conn: sqlite3.Connection,
    media_id: int,
    question: str,
    *,
    budget_tokens: int,
    doc: docs.TranscriptDoc | None = None,
    run_id: int | None = None,
) -> tuple[str, list[int]]:
    """What to put under the question, and which segments it came from.

    The whole timestamped transcript when it fits, and only when it does not,
    the segments that match the question. When nothing matches - or the question
    has no searchable words in it - the opening of the recording is sent
    instead: an answerless question still deserves a recording to answer from,
    and sending nothing would have the model report that the transcript is
    empty, which is a lie about the recording rather than about the question.

    `doc` may be passed by a caller that has already loaded it (`plan_chat`
    has), which is the only reason this takes both a connection and a media id.
    """
    if doc is None:
        doc = docs.load(conn, media_id, run_id)
    if not doc.segments:
        raise docs.NoTranscript(
            f"media {media_id} has a transcript with no segments; there is nothing to ask about"
        )

    if not chunking.needs_chunking(doc, budget_tokens):
        return transcript_text(doc), [int(segment["idx"]) for segment in doc.segments]

    found = retrieve(conn, doc, question, budget_tokens=budget_tokens)
    if not found:
        opening = chunking.plan(doc, budget_tokens=budget_tokens)[0]
        return opening.text, list(opening.segment_ids)
    return render_context(doc, found), found


# --- the prompt -------------------------------------------------------------------------

SYSTEM = tasks.SYSTEM + (
    " You are answering questions about one recording, in a conversation, so an answer may "
    "refer to what was already asked. Answer only from the transcript you are given: where it "
    "does not say, say that it does not say. Cite what you used by copying the [m:ss] of the "
    "line it came from into your answer, in square brackets and next to the point it supports "
    f"- the reader clicks those to hear it. You may be shown only the parts of the recording "
    f"that match the question, with {GAP_MARKER} where something was left out; do not treat a "
    "gap as a silence, and do not cite a time you were not shown."
)

_ROLE_LABELS = {USER: "Question", ASSISTANT: "Answer"}


def recent(history: Sequence[dict]) -> list[dict]:
    """The last `MAX_HISTORY_MESSAGES` turns - the ones worth the window."""
    return list(history or [])[-MAX_HISTORY_MESSAGES:]


def render_history(history: Sequence[dict]) -> str:
    """Earlier turns as labelled lines, each clipped to `HISTORY_CHARS`."""
    lines = []
    for message in history:
        role = str(message.get("role") or USER)
        label = _ROLE_LABELS.get(role, role)
        lines.append(f"{label}: {tasks.clip(str(message.get('content') or ''), HISTORY_CHARS)}")
    return "\n\n".join(lines)


def source_label(title: str, duration: float, *, whole: bool) -> str:
    """The one line that says what the text under it actually is.

    The excerpt wording is not decoration: a model told it has the transcript
    will answer "that was never mentioned" about a passage it was not given.
    """
    if whole:
        return tasks.source_label(title, duration)
    return (
        f'The parts of the transcript of "{title}" ({render.format_ts(duration)}) '
        f"that match the question, in order, with {GAP_MARKER} where the rest was left out"
    )


def user_prompt(
    question: str,
    context: str,
    *,
    title: str,
    duration: float,
    whole: bool,
    history: Sequence[dict] = (),
) -> str:
    """One turn: what was said before, what to read, and what is being asked.

    The question goes last, after the transcript, because it is the thing the
    model should still have in mind when it starts writing.
    """
    parts = []
    if history:
        parts.append("Earlier in this conversation:\n\n" + render_history(history))
    parts.append(f"{source_label(title, duration, whole=whole)}:\n\n{context}")
    parts.append(f"The question to answer now:\n\n{question}")
    return "\n\n".join(parts)


def budget_for(
    *,
    question: str,
    title: str,
    duration: float,
    history: Sequence[dict],
    context_tokens: int,
    max_output_tokens: int,
) -> int:
    """How much transcript fits: the window, less everything else in the turn.

    History is subtracted here rather than hoped to be small. It folds into the
    user prompt - `ChatRequest` carries one - so it is spent out of the same
    window the transcript is, and a chat that ignored it would overflow the
    model on turn twelve instead of reading less. Measured against the prompt
    that will actually be sent, with the longer of the two source labels, so
    editing the wording moves the budget with it.
    """
    overhead = estimate_tokens(SYSTEM) + estimate_tokens(
        user_prompt(
            question,
            "",
            title=title,
            duration=duration,
            whole=False,
            history=history,
        )
    )
    budget = context_tokens - overhead - max_output_tokens
    if budget <= 0:
        raise ValueError(
            f"a {context_tokens}-token context leaves nothing for the transcript after "
            f"{overhead} tokens of question, history and instructions and "
            f"{max_output_tokens} tokens of answer; use a model with a bigger window, "
            f"a shorter question, or start a new conversation"
        )
    return budget


# --- a planned turn ----------------------------------------------------------------------


@dataclass(frozen=True)
class ChatPlan:
    """Everything the one call needs, decided before it is made.

    Separate from `answer` so the `llm` job can plan in one stage and call in
    the next: an unknown provider, a media with no transcript or a pinned
    recording then fails in `prepare`, before a token is paid for, and the jobs
    board says which stage it was.
    """

    media_id: int
    run_id: int
    question: str
    provider_name: str
    model: str
    system: str
    user: str
    segment_ids: tuple[int, ...]
    title: str
    duration: float
    budget_tokens: int
    max_output_tokens: int
    whole_transcript: bool

    @property
    def kind(self) -> str:
        return CHAT_KIND


def plan_chat(
    conn: sqlite3.Connection,
    *,
    media_id: int,
    question: str,
    provider_name: str,
    model: str | None = None,
    history: Sequence[dict] = (),
    run_id: int | None = None,
    context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    budget_tokens: int | None = None,
) -> ChatPlan:
    """Decide what to send, to which model, before anything is built.

    The order of the checks is the same as `tasks.plan_task`'s and for the same
    reason: an empty question costs nothing to catch, and the privacy check
    comes before the transcript is read, so a pinned recording's words are never
    loaded into this process for a call `llm.chat()` was going to refuse anyway.
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("a chat turn needs a question; there is nothing to answer without one")

    provider_cls = llm.provider_class(provider_name)
    privacy.assert_allowed(conn, media_id, provider_cls)

    chosen_model = (model or "").strip() or provider_cls.default_model
    if not chosen_model:
        raise ValueError(f"provider {provider_name!r} has no default model; name one explicitly")

    doc = docs.load(conn, media_id, run_id)
    kept = recent(history)
    output_tokens = max_output_tokens or tasks.DEFAULT_MAX_OUTPUT_TOKENS
    if budget_tokens is None:
        budget_tokens = budget_for(
            question=question,
            title=doc.title,
            duration=doc.duration,
            history=kept,
            context_tokens=context_tokens or tasks.context_tokens_for(provider_cls),
            max_output_tokens=output_tokens,
        )

    context, segment_ids = context_for(
        conn, media_id, question, budget_tokens=budget_tokens, doc=doc
    )
    # Every segment came back, so this is the transcript rather than excerpts.
    # Read off the result instead of asking `needs_chunking` again: that
    # question was just answered inside `context_for`, and asking it twice means
    # rendering and measuring a four-hour transcript twice to get the same
    # answer. Exact, not an approximation - retrieval only runs when the whole
    # transcript is over budget, and what it returns then is a strict subset.
    whole = len(segment_ids) == len(doc.segments)
    return ChatPlan(
        media_id=media_id,
        run_id=int(doc.run["id"]),
        question=question,
        provider_name=provider_name,
        model=chosen_model,
        system=SYSTEM,
        user=user_prompt(
            question,
            context,
            title=doc.title,
            duration=doc.duration,
            whole=whole,
            history=kept,
        ),
        segment_ids=tuple(segment_ids),
        title=doc.title,
        duration=doc.duration,
        budget_tokens=budget_tokens,
        max_output_tokens=output_tokens,
        whole_transcript=whole,
    )


def answer(conn: sqlite3.Connection, plan: ChatPlan, **provider_kwargs: Any) -> ChatAnswer:
    """Make the call, and read the citations back out of what it said.

    `llm.chat` and not a provider directly: the pin, the registry and the retry
    policy all live behind it, and a caller that goes around it goes around all
    three.
    """
    response = llm.chat(
        conn,
        media_id=plan.media_id,
        provider_name=plan.provider_name,
        request=ChatRequest(
            system=plan.system,
            user=plan.user,
            model=plan.model,
            max_output_tokens=plan.max_output_tokens,
        ),
        **provider_kwargs,
    )
    text = (response.text or "").strip()
    if not text:
        raise base.BadResponse(
            f"the model returned nothing for the question {tasks.clip(plan.question, 200)!r}; "
            "there is no answer to store"
        )
    return ChatAnswer(
        text=text,
        citations=parse_citations(text, plan.duration),
        segment_ids=plan.segment_ids,
        response=response,
    )


# --- the conversation --------------------------------------------------------------------


def store_exchange(
    conn: sqlite3.Connection, *, media_id: int, question: str, answer: ChatAnswer
) -> tuple[int, int]:
    """Write the question and its answer; returns their two ids.

    One transaction, because half an exchange is not a thing that happened: a
    question with no answer under it would render as a turn the app forgot to
    reply to.
    """
    now = time.time()
    with db.LOCK:
        try:
            asked = conn.execute(
                "INSERT INTO chat_message(media_id, role, content, citations_json, created_at)"
                " VALUES (?, ?, ?, '[]', ?)",
                (media_id, USER, question, now),
            ).lastrowid
            answered = conn.execute(
                "INSERT INTO chat_message(media_id, role, content, citations_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    media_id,
                    ASSISTANT,
                    answer.text,
                    json.dumps(list(answer.citations)),
                    now,
                ),
            ).lastrowid
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return int(asked), int(answered)


def history_for(
    conn: sqlite3.Connection, media_id: int, limit: int = MAX_HISTORY_MESSAGES
) -> list[dict]:
    """The last `limit` turns of this recording's conversation, oldest first.

    The newest turns are the ones worth the window, and the order they are
    replayed in is the order they happened - hence the inner ORDER BY id DESC
    to choose them and the outer one to read them.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM chat_message WHERE media_id=? ORDER BY id DESC"
            " LIMIT ?) ORDER BY id",
            (media_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def ask(
    conn: sqlite3.Connection,
    *,
    media_id: int,
    question: str,
    provider_name: str,
    model: str | None = None,
    history: Sequence[dict] = (),
    run_id: int | None = None,
    context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    budget_tokens: int | None = None,
    **provider_kwargs: Any,
) -> ChatAnswer:
    """Plan, ask, record the exchange; returns the answer.

    The three steps are separate functions because the `llm` job runs them as
    three stages, and one composed function is the only way the job and a direct
    caller cannot drift apart - the same arrangement as `tasks.run_task`.

    `history` is passed in rather than read here: a caller that wants the stored
    conversation asks for it with `history_for`, and one asking a one-off
    question about a recording should not have to explain that it does not want
    the last chat replayed at it.
    """
    plan = plan_chat(
        conn,
        media_id=media_id,
        question=question,
        provider_name=provider_name,
        model=model,
        history=history,
        run_id=run_id,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        budget_tokens=budget_tokens,
    )
    result = answer(conn, plan, **provider_kwargs)
    store_exchange(conn, media_id=plan.media_id, question=plan.question, answer=result)
    return result
