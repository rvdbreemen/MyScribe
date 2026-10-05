"""The six preset outputs: a template, a schema, and a resumable map-reduce.

A "task" here is one question asked of a transcript - summarise it, pull the
action items out of it, chapter it - and this module is everything between the
transcript and a stored answer. It reads a `TranscriptDoc`, renders a prompt,
calls `llm.chat()`, checks that what came back is the shape that was asked for,
and writes an `llm_output` row.

Four decisions shape the whole file.

**One key, and never an overwrite.** A row is identified by
`(media, kind, provider, model, prompt_version)`. A better model next month is
a *new row*: the old one is evidence about what that model said on that day,
and a table that quietly replaced it would be a claim about the present rather
than a record. `PROMPT_VERSION` is part of the key for the same reason - an
edited template is a different question, and its answers should not be mixed in
with answers to the old one.

**A long transcript is a map-reduce, and its intermediate answers are rows.**
When the transcript does not fit, `chunking.plan` cuts it on segment
boundaries; each chunk is answered separately as *notes* and stored as
`kind:chunk:{i}`, and one final call combines the notes into the shape the kind
promises. Storing the notes is what makes the job resumable: an interrupted
run over a four-hour recording resumes at the chunk it died on and pays for
each of the others exactly once. The resume key includes the run, because the
seams move with the segments - chunk 3 of a re-transcribed recording is not
chunk 3 of the old one.

Only the *combine* call has to produce valid JSON. Asking each chunk for a
partial object and merging them would mean N chances to fail validation and a
merge nobody can explain; asking for notes and combining them once is one
chance to fail, in the call that has seen everything.

**The answer is validated before it is stored, once.** Fences get stripped, an
object embedded in an apology gets found, the result is validated against the
kind's pydantic model, and the row holds the canonical JSON of that model. So
every reader afterwards - the panel, an export, the next version of this app -
can `json.loads` a row and trust it. A model that will not produce the shape
fails as `BadResponse` **carrying its own text**, because "it did not parse" is
unactionable and the model's actual words are the only thing that tells a user
whether to change the model, the prompt or the question.

**Nothing here decides whether a call is allowed.** Every request goes through
`llm.chat()`, which asserts the private-mode pin before a client exists.
`plan_task` also refuses early, before the transcript is even read into this
process - not as the enforcement point, but so a pinned recording's words are
not loaded for a request that was never going to be made.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Callable, Sequence

import jinja2
from pydantic import BaseModel, BeforeValidator, ValidationError

from scribe import db, glossary, llm, render
from scribe.exports import doc as docs
from scribe.llm import base, chunking, ollama, privacy
from scribe.llm.base import ChatRequest, ChatResponse
from scribe.llm.chunking import Chunk, estimate_tokens

PROMPT_VERSION = "2"
"""Bumped whenever a template in `prompts/` changes, because it is part of the
stored key: answers to an edited question are not answers to the old one, and a
panel showing both without saying so would be comparing two different things."""

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)),
    # These render prompts, not HTML. Autoescaping would send a model
    # `don&#39;t` for every apostrophe in the transcript.
    autoescape=False,
    # A misspelled variable must be an error rather than an empty string: a
    # prompt silently missing its transcript is a call that costs money and
    # answers about nothing.
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


# --- what a good answer looks like -----------------------------------------------------


def _clock_seconds(value: Any) -> float:
    """Seconds from a number, or from a `m:ss` / `h:mm:ss` clock string.

    Models cite `[1:23]` because `[1:23]` is what the transcript shows them; a
    schema that only accepted a number would turn the most likely correct
    answer into a `BadResponse`. Brackets are tolerated for the same reason.
    """
    if isinstance(value, bool):  # bool is an int; a timestamp is not a flag
        raise ValueError(f"not a timestamp: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().strip("[]").strip()
        if text:
            try:
                return float(text)
            except ValueError:
                pass
            parts = text.split(":")
            if 2 <= len(parts) <= 3:
                total = 0.0
                for part in parts:
                    total = total * 60 + float(part)
                return total
    raise ValueError(f"not a timestamp: {value!r}")


def _optional_clock_seconds(value: Any) -> float | None:
    """As above, but "no timestamp" is a real answer and stays None.

    An action item nobody can point at one line for is still an action item;
    forcing a number here would invite an invented one.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _clock_seconds(value)


Seconds = Annotated[float, BeforeValidator(_clock_seconds)]
OptionalSeconds = Annotated[float | None, BeforeValidator(_optional_clock_seconds)]


# Pydantic ignores unknown keys by default, and that default is wanted here: a
# model that adds a field nobody asked for has still answered the question, and
# failing on it would trade a usable answer for a tidier error.
class ActionItem(BaseModel):
    text: str
    owner: str | None = None
    evidence_ts: OptionalSeconds = None


class Summary(BaseModel):
    paragraph: str
    bullets: list[str] = []


class ActionItems(BaseModel):
    items: list[ActionItem] = []


class Chapter(BaseModel):
    start: Seconds
    title: str


class Chapters(BaseModel):
    chapters: list[Chapter] = []


class Minutes(BaseModel):
    agenda: list[str] = []
    decisions: list[str] = []
    actions: list[ActionItem] = []


class Blog(BaseModel):
    title: str
    body: str


SPEAKER_CONFIDENCE_THRESHOLD = 90.0
"""How sure the pass must be before a name is written without being asked.

Robert's number (TASK-024). Worth being honest about what it is: a model
answering 96 is answering a question about its own certainty, and nothing
trained it to answer that well. This is a policy dial, not a probability, and
the real check on an applied name is the quote with its [m:ss] that the answer
carries and `speaker_label.llm_output_id` points back to."""

_WORD_CONFIDENCE = {"high": 85.0, "medium": 50.0, "low": 20.0}
"""What the older three-word scale maps to.

Every one of them lands BELOW the threshold on purpose. A model that answered
"high" where a number was asked for has not given the evidence an automatic
write needs, so its guess is shown and can be accepted by hand, but nothing is
written unattended on the strength of a word. Tolerated rather than refused,
because refusing would turn a model that answered the older way into a failed
job while its answer is still perfectly useful to a person."""


def _confidence(value: Any) -> float:
    """A confidence as a number out of a hundred; anything unreadable is zero.

    Zero rather than a middling default: an answer that did not say how sure it
    was has not earned a write, and a default that could clear a threshold is
    how a gate stops being one.

    A fraction is read as a percentage. 0.96 and 96 mean the same thing to the
    person reading them, and that must not be the difference between naming a
    speaker and leaving them anonymous.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return 0.0
        if 0.0 < number <= 1.0:
            number *= 100.0
        return max(0.0, min(100.0, number))
    if isinstance(value, str):
        text = value.strip().rstrip("%").strip()
        word = _WORD_CONFIDENCE.get(text.lower())
        if word is not None:
            return word
        try:
            return _confidence(float(text))
        except ValueError:
            return 0.0
    return 0.0


Confidence = Annotated[float, BeforeValidator(_confidence)]


class SpeakerGuess(BaseModel):
    cluster: str
    name: str = ""
    role: str = ""
    confidence: Confidence = 0.0
    evidence: str = ""
    notes: str = ""


class Speakers(BaseModel):
    """Who each diarization cluster is. Every field but `cluster` has a
    default, so an answer that names half of what was asked still validates
    and the panel shows what it did say; only an answer that is not this
    object at all is refused, and then with its text kept (`parse_answer`)."""

    format: str = ""
    speakers: list[SpeakerGuess] = []


class LabelGuess(BaseModel):
    label: str
    confidence: str = "low"
    evidence: str = ""


class Labels(BaseModel):
    """What a recording is about, in words the library can be filtered by.

    Deliberately no `new` flag for the model to set. Whether a label is one the
    library already holds is a fact about the database, not a claim the answer
    gets to make, and asking the model to self-report it would put the cap on
    invented labels at the mercy of the thing being capped. `apply_labels`
    decides reuse against the vocabulary it read, and enforces the ceiling
    itself - the prompt asks, the code guarantees.
    """

    labels: list[LabelGuess] = []


# --- the six kinds -----------------------------------------------------------------------

DEFAULT_MAX_OUTPUT_TOKENS = 4000
"""Room for an answer, per call, and a measured floor rather than a round
number.

Measured through this exact code path on 2026-09-03, summarising the same
four-segment transcript with the shipped local default (`qwen3.5:4b`, a
thinking model, `num_ctx` 8192):

* 2000 tokens -> HTTP 200 with an empty `content` and `done_reason: "length"`,
  refused as a `BadResponse` by `ollama._answer`. 37 s spent on nothing.
* 4000 tokens -> a correct summary in 34.6 s, having spent **2674 completion
  tokens**. That number is the explanation: the model thinks for more than two
  thousand tokens before it writes a word, so a 2000-token cap cannot reach the
  answer, and no amount of prompting changes that.

It is a cap, not a spend: a cloud provider bills what was generated, so
carrying the floor a local model needs costs a cloud call nothing. The price is
paid in the transcript budget instead - `budget_for` subtracts this from the
context window - which is why it is not simply set enormous."""


@dataclass(frozen=True)
class TaskSpec:
    """One kind: its template, the shape it must answer in, and what it is for."""

    kind: str
    label: str
    template: str
    schema: type[BaseModel] | None
    goal: str
    """One line, handed to the chunk-level prompt so the notes taken from an
    excerpt are notes for *this* task rather than a generic summary."""
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    needs_prompt: bool = False
    with_speakers: bool = False
    """Whether each transcript line carries its cluster label (`SPEAKER_00:`).
    Off for the summary kinds - the label is noise to them and the display
    name is not decided yet - and on for the task whose question is what the
    labels stand for, and for the rewrite that must keep who said what."""
    combine: str = "reduce"
    """How a transcript that does not fit one call is handled. `reduce`: notes
    per chunk, one call combining them into the kind's shape. `concat`: the
    kind's own prompt over each chunk and the answers joined in order - for a
    task whose answer *is* the transcript, rewritten, where notes would lose
    the words."""
    reasoning_off: bool = False
    """Ask the model not to reason first (`ChatRequest.reasoning_off`), on
    every call the kind makes. TASK-029's decision: on for `cleanup`, a
    mechanical rewrite for which deepseek-v4-flash spent 22,515 reasoning
    tokens on 837 words where 1,171 tokens without reasoning gave the same
    cleaning. Off - the provider's default - for the rest: `speakers` and
    `labels` wait for an A/B of their own, because they are queued without a
    person and `speakers` writes names; the others have no recorded length
    failure. `effort: low` is used nowhere: on media 1 it dropped the [m:ss]
    stamps and the speaker labels."""
    apply: Callable[[sqlite3.Connection, "TaskPlan", Any, int], dict] | None = None
    """What to do with the answer once it is stored, for the kinds that change
    something rather than only report.

    Until TASK-024 there was no such thing: a job stored its answer and ended,
    so `speakers` produced a mapping nobody applied and `labels` produced labels
    nobody attached, and every kind that *did* something needed a person to
    press a button afterwards. An artifact nobody applied was a reachable state
    of the system, and this is what stops it being one.

    Called as `(conn, plan, payload, output_id)` and returns a small report the
    job emits. The whole plan rather than a media id, because what an answer
    belongs to is the *run* it was made from: `speaker_label` hangs off a run,
    and a re-transcription that landed while the analysis was in flight must
    not have its clusters named from an answer about the previous one. It runs inside the job that produced the answer, on purpose:
    a failure to apply then lands on the jobs board beside the analysis it came
    from, rather than in a second job that can be cancelled or lost."""


TASKS: dict[str, TaskSpec] = {
    "summary": TaskSpec(
        kind="summary",
        label="Summary",
        template="summary",
        schema=Summary,
        goal="write a summary of the whole recording: one paragraph and a handful of bullets",
    ),
    "action_items": TaskSpec(
        kind="action_items",
        label="Action items",
        template="action_items",
        schema=ActionItems,
        goal="list everything somebody agreed to do, with who took it on and when it was said",
    ),
    "chapters": TaskSpec(
        kind="chapters",
        label="Chapters",
        template="chapters",
        schema=Chapters,
        goal="mark where the subject changes, with the time each new stretch starts",
    ),
    "minutes": TaskSpec(
        kind="minutes",
        label="Minutes",
        template="minutes",
        schema=Minutes,
        goal="write the minutes: what was discussed, what was decided, what was agreed to do",
    ),
    "blog": TaskSpec(
        kind="blog",
        label="Blog post",
        template="blog",
        schema=Blog,
        goal="gather the material a blog post about this recording would be written from",
        # The one kind whose answer is meant to be long: a post is prose, not a
        # list, and the floor above only buys the thinking that precedes it.
        max_output_tokens=6000,
    ),
    "speakers": TaskSpec(
        kind="speakers",
        label="Who is speaking",
        template="speakers",
        schema=Speakers,
        goal=(
            "work out who each SPEAKER_XX label is: names that are said, who says them and who "
            "answers to them, and what role each speaker plays (host, guest, expert), each with "
            "the quote and its time"
        ),
        with_speakers=True,
        # Measured against openrouter/auto on 2026-09-10, over five Hacker
        # History episodes: the answer itself is tiny - two or three names with
        # a quote each - but the thinking in front of it is not, and it varies
        # by more than a factor of ten depending on which model auto picks.
        # 347 completion tokens on one episode, 3779 on another with the same
        # two clusters, and two outright failures at the 4000 default:
        # "openrouter answered with no message content (finish_reason=
        # 'length')". A three-cluster episode was one of them.
        #
        # Twice the default, for the reason DEFAULT_MAX_OUTPUT_TOKENS gives:
        # this is a cap, not a spend. A cloud provider bills what was
        # generated, so carrying room for a model that thinks costs a model
        # that does not exactly nothing - while the failure it prevents costs
        # the whole call and leaves the speakers unnamed.
        #
        # Doubled again on 2026-09-27 (TASK-099, with Robert): a 2h28m
        # recording with five clusters spent all 8,000 reasoning at BaseTen,
        # twice (jobs 418 and 419), and came back empty. A call that still
        # loses everything to thinking is asked once more with the reasoning
        # hint (`openai_like._ask_again_with_the_hint`). A small local window
        # clamps this cap (`fit_output_tokens`).
        max_output_tokens=16000,
    ),
    "labels": TaskSpec(
        kind="labels",
        label="Labels",
        template="labels",
        schema=Labels,
        goal=(
            "name what this recording is about in a handful of short labels, reusing the ones "
            "the library already has wherever they fit"
        ),
        # Who spoke is noise here: a label describes the subject, and a
        # transcript carrying SPEAKER_00 in front of every line spends budget
        # on something the answer never mentions.
        with_speakers=False,
    ),
    "cleanup": TaskSpec(
        kind="cleanup",
        label="Clean transcript",
        template="cleanup",
        schema=None,
        goal="rewrite the transcript as clean, readable text without losing anything that was said",
        # The answer is as long as the input: the cap is what one chunk may
        # come back as, and `plan_task` cuts the chunks to fit under it.
        max_output_tokens=6000,
        with_speakers=True,
        combine="concat",
        reasoning_off=True,
    ),
    "custom": TaskSpec(
        kind="custom",
        label="Ask something else",
        template="custom",
        # No schema: a free question has no shape to promise, and forcing JSON
        # onto "what did they say about the budget?" would make the answer worse.
        schema=None,
        goal="answer the question the user asked about the recording",
        needs_prompt=True,
    ),
}
"""The kinds, in the order the rail offers them; `custom` stays last because
the rail gives it the question field."""

KINDS: tuple[str, ...] = tuple(TASKS)

CHUNK_KIND_SEPARATOR = ":chunk:"

LOCAL_CONTEXT_TOKENS = ollama.DEFAULT_NUM_CTX
"""The window a local call actually gets. Not a guess: `OllamaProvider` asks
`/api/chat` for exactly this many tokens, and planning for more would mean
sending a prompt the daemon truncates (it answers 200 either way - see
`ollama._answer`, which is why that check exists)."""

CLOUD_CONTEXT_TOKENS = 128_000
"""What a cloud call is planned against when nobody says otherwise.

A floor rather than a guess, and it matters more since OpenRouter's default
became `openrouter/auto`: the router picks per request, so at planning time
nobody knows which window the answer will have. Planning short costs a chunk
boundary; planning long costs a failed call after every map call has been paid
for. 128k is what the current cloud models hold at the low end - `gpt-4o-mini`
sits exactly there and `openai/gpt-5.6-luna` at 1.05M is far above it. A caller
who does know the model's real window passes `context_tokens` and gets it."""

MIN_TRANSCRIPT_TOKENS = 1500
"""The least transcript a call is worth making with.

`max_output_tokens` is a cap, not a spend - every spec says so - but nothing
enforced that reading, so `speakers` at 8000 could eat a whole 8192-token local
window and leave the transcript negative (TASK-084). This is the other side of
that cap: whatever a kind asks to be allowed to say, this much of the recording
goes in first. 1500 tokens is roughly ten minutes of speech, which is a chunk
worth summarising; below it the chunking is producing calls, not answers."""

MIN_OUTPUT_TOKENS = 512
"""The least answer worth asking for. A window that cannot hold this *and*
`MIN_TRANSCRIPT_TOKENS` is refused in words rather than quietly truncated."""

MIN_NOTE_FLOOR = 256
"""Below this a note is not a shorter note, it is nothing.

`MIN_NOTE_TOKENS` is what a note is worth asking for and this is what one has
to be to exist: a couple of sentences, a name with the line that proves it.
Between the two, `plan_notes` takes whatever the window affords - a terse note
is a real answer where a refusal is not. Below it, the configuration genuinely
cannot do the job and says so."""


def suggest_bigger_window(context_tokens: int) -> str:
    """One sentence naming something that would actually work.

    "Use a bigger model" is not help. This looks at what is installed here and
    names a model whose window is larger, because the fastest fix for a local
    user is usually a model they already have. Guarded and cheap: a daemon
    that is down leaves the generic advice, which is still true.
    """
    try:
        from scribe.llm import ollama

        provider = ollama.OllamaProvider()
        try:
            roomier = []
            for name in provider.models():
                window = provider._model_window(name)
                if window and window > context_tokens:
                    roomier.append((window, name))
        finally:
            provider.close()
        if roomier:
            window, name = max(roomier)
            return (
                f"On this machine {name} reports a {window}-token window, which would fit; "
                f"choose it under Settings, or raise the local cap ({ollama.MAX_NUM_CTX})."
            )
    except Exception:  # noqa: BLE001 - a suggestion is a nicety, never a second failure
        pass
    return (
        "A provider with a bigger window would fit this - the cloud ones are roughly sixteen "
        "times this size - or ask about a shorter recording."
    )


MIN_NOTE_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS
"""The floor under a chunk's note budget, and the reason a task can be refused.

Notes are sized so all of them together fit the one combine call that has to
read them: `combine_capacity` divided by the number of chunks. The floor is
what stops that arithmetic asking for notes nothing can write - a chunk's
notes are an answer like any other, and the measurement in
`DEFAULT_MAX_OUTPUT_TOKENS` applies to them unchanged: below roughly a
thousand tokens the shipped local default spends its whole budget thinking and
returns an empty `content`.

So on a small window the two numbers meet: a recording that needs many chunks
asks for `chunks x 4000` tokens of notes, and an 8k window cannot hold them.
That configuration has no answer, and `plan_task` says so **before the first
call** rather than after the last one.

Measured 2026-09-03 across all six kinds, this is where the line falls. At the
shipped local `num_ctx` of 8192 the transcript budget is about 3,770 tokens
(1,753 for `blog`, whose answer budget is larger), so **any** recording that
needs a second chunk - roughly twenty minutes of speech - is refused for the
preset tasks. At the cloud planning window of 128,000 it holds to 16 chunks,
about 2M tokens of transcript; at `openai/gpt-5.6-luna`'s real 1.05M it does
not bite at all. Chat is unaffected either way: `chat_tool` retrieves the
segments it needs rather than reading the whole recording.

That local ceiling is a real narrowing and it is the honest one - what used to
happen instead was a combine call silently written from half its input, or a
`ContextTooLong` after every map call had been paid for. Raising `num_ctx`
would move the line, and it is a constructor argument nothing wires today
(same story as `host`); a bigger local model or a cloud provider are the two
routes that exist, and neither is a choice this module may make for the user.

Where that refusal comes from has now moved, and the history is worth keeping
because two earlier guesses were wrong. It was first credited to
`ollama._answer`, which compares `prompt_eval_count` against `num_ctx`;
measured 2026-09-03, the daemon at 8192 answers a too-big prompt with
`prompt_eval_count: 4098` - *below* the window - so that check never fired and
a truncated combine came back looking like an answer. The pre-flight word
count in `ollama._refuse_a_prompt_that_cannot_fit` fixed that (measured
through `plan_task`: the combine prompt is 9672 words at 3 chunks and 25297 at
8, against a window of 8192), but it fires in the *last* call of the job, once
every map call has been paid for - about 35 s each on `qwen3.5:4b` - and the
notes it leaves behind are cached, so every retry refused instantly and made
no new calls. Unrecoverable, by the mechanism meant to make retries cheap.

`combine_capacity` is that same arithmetic done in `plan_task`, where nothing
has been spent yet. Ollama's guard stays as the backstop it should always have
been: this one knows the plan, that one knows the wire."""

CONCAT_CHUNK_TOKENS = 3000
"""The most transcript one part of a concat kind (cleanup) is cut to, in
estimated tokens - so that a model reasoning inside the answer cap has room.

The arithmetic, derived rather than measured (two samples, scaled to the gate):
deepseek cleaned media 1 in 1,403 tokens for a 1,557-token chunk (0.90) and
luna media 12's first part in about 4,722 for 5,968 (0.79). Scaled to
`CLEAN_MAX_RATIO` (1.15) a part is at most 1.04-1.07 x its chunk. A 5,999-token
chunk - what `min(budget, cap)` planned for media 12 - then needs about 6,420
tokens from a 6,000 cap: no room even without reasoning. A 3,000 chunk needs
about 3,210 and leaves about 2,790 (46%) for reasoning and hidden tokens.
Planned read-only on 2026-09-11: media 12 goes from 4 chunks (1,605-5,999) to
7 (1,722-2,993; the tail is the short one); media 1 stays one chunk of 1,557.
Local runs are unchanged: the 8,192 window binds first, at 1,798-1,799.

Its own constant rather than a share of the cap, so a later cap change buys
reasoning room instead of bigger chunks.

Step 0 kept it. The rule it had to pass: every counted chunk ends 'stop' with
content and spends at most half its reserve (about 1,395 tokens) reasoning;
had luna failed, this constant and its `min()` in `plan_task` would have gone.
Measured 2026-09-11 with `scripts/task029_headroom.py` on media 12, $0.36 in
all. At 3,000 (7 chunks): gpt-5.6-luna on Azure without the hint ended 'stop'
with content on 18 of 18 answered calls - 134-597 reasoning tokens on the six
full chunks, 504-1,301 on the 1,722-token tail, at least 3,010 tokens left
under the cap; with the hint, 19 of 19 and 0 reasoning. 5 of luna's 42 calls
at 3,000 got OpenRouter's rate-limit envelope (HTTP 200, no usage), not a cap
failure.
gemini-3.5-flash-lite on Google refused the hint every time (the drop worked
on all 21 calls here, and on the 4 at 6,000) and reported 0 reasoning: 21 of
21 'stop', at least 2,895 left, the
largest part 3,105 tokens for a 2,974-token chunk (1.04x, inside the band
above). That Google holds the cap comes from a manual probe (60 completion
tokens at max_tokens 64, 'length'), not from the script's pin check, which
read Google's blocked answer as enforced (ADR-010, Chunks).

The deciding reason is margin, not a luna failure. At 6,000 (4 chunks,
1,605-5,999) luna finished every chunk too, hinted and not, but left only
997-1,801 tokens on the full chunks, about a third of the room at 3,000. The
2026-09-10 'length' on luna's later chunks did not reproduce. gemini's second
6,000 chunk did end 'length', at 5,996 of 6,000 with 0 reasoning tokens
reported - so, as far as its usage shows, the answer alone overran the cap:
the case this constant exists for, seen once.

What it does not show: one recording, one upstream per model (the account's
routing filtered the others out), so nothing about an endpoint that ignores
the cap (TASK-029's case 3). And the script's per-chunk rule cannot judge
6,000: its reserve there, 6,000 - 1.07 x 5,999, is negative and fails at any
reasoning, so finish and tokens left were read instead."""

TRUNCATED_FINISH = "length"
"""How both provider families say "I stopped because I ran out of room".

OpenAI and OpenRouter return `finish_reason: "length"`; Ollama returns
`done_reason: "length"`. One spelling, so the check below is a comparison
rather than a table of provider quirks - and a provider added later that
invents its own word simply will not match, which is the safe direction: it
falls back to today's behaviour rather than refusing good answers."""

RAW_TEXT_LIMIT = 2000
"""How much of a bad answer travels in the error. It ends up in
`job.error_detail`, on the jobs board and in whatever a user pastes into a bug
report; enough to see what the model did, not a megabyte of it."""


def task_spec(kind: str) -> TaskSpec:
    """The spec for `kind`, or a `ValueError` naming the ones that exist."""
    try:
        return TASKS[kind]
    except KeyError:
        known = ", ".join(KINDS)
        raise ValueError(f"unknown task kind {kind!r}; known kinds: {known}") from None


QUESTION_DIGEST_CHARS = 12
"""How much of the sha256 of a question goes in a chunk row's key. 48 bits: two
different questions colliding is not a thing that happens, and the whole
question would make a key nobody can read in a table."""


def question_digest(question: str) -> str:
    """A short, stable name for one question. See `chunk_kind`."""
    return hashlib.sha256(question.strip().encode("utf-8")).hexdigest()[:QUESTION_DIGEST_CHARS]


def chunk_kind(kind: str, index: int, question: str | None = None) -> str:
    """The `kind` an intermediate chunk answer is stored under.

    `question` is the `custom` task's prompt, and it is part of the key because
    it is part of the work: `map_chunk.md` tells each reader what the notes are
    for, so notes taken for "what did they decide about the budget?" are notes
    about the budget and nothing else. Without it in the key, a second question
    about the same recording would find those notes, skip every chunk call and
    answer from them - a specific answer built from a reader who was asked
    something else, with nothing on the panel to say so.

    Only `custom` has a question, so every other kind's key is unchanged and
    the notes stored before this existed are still found.
    """
    key = f"{kind}{CHUNK_KIND_SEPARATOR}{index}"
    asked = (question or "").strip()
    return f"{key}@{question_digest(asked)}" if asked else key


def is_chunk_kind(kind: str) -> bool:
    """True for an intermediate row. A reader listing outputs wants the final
    ones; the chunk rows are working notes that happen to be durable."""
    return CHUNK_KIND_SEPARATOR in kind


# --- prompts -------------------------------------------------------------------------------

SYSTEM = (
    "You work on transcripts of recordings. Every line you are given begins with the time "
    "it was spoken, written as [m:ss] - the same clock the player shows, so a timestamp you "
    "copy is one the reader can click to hear it. Use only what the transcript says: leave a "
    "gap open rather than filling it with something plausible. The transcript is machine-made, "
    "so a word may be wrong; where a name or a number matters and looks garbled, say what you "
    "see rather than tidying it into something else."
)

JSON_SYSTEM = (
    " Answer with one JSON object and nothing else: no explanation before it, no code fence "
    "around it, no remark after it."
)


def system_for(spec: TaskSpec) -> str:
    """The system prompt for `spec`.

    The JSON instruction is added whenever the kind has a schema - including
    for providers that also take a `response_format`. That is belt and braces
    on purpose: `response_format` constrains the grammar, and the sentence is
    what stops a model that has no such constraint from wrapping a perfectly
    good object in an apology.
    """
    return SYSTEM + (JSON_SYSTEM if spec.schema is not None else "")


def chunk_goal(spec: TaskSpec, custom_prompt: str | None = None) -> str:
    """What the chunk-level reader is told the notes will be used for.

    For five of the six kinds that is `spec.goal`, a constant, and it is the
    whole instruction: "list everything somebody agreed to do" needs nothing
    added. For `custom` the goal *is* the user's question, and the constant
    ("answer the question the user asked about the recording") names a question
    it does not show - so the reader takes generic notes and the combine call
    answers something specific out of them. The question goes in.
    """
    asked = (custom_prompt or "").strip()
    if spec.needs_prompt and asked:
        return f"{spec.goal}, which is: {asked}"
    return spec.goal


def source_label(title: str, duration: float, *, notes_from: int = 0) -> str:
    """The one line that tells the model what the text under it actually is."""
    if notes_from:
        return f'Notes taken from {notes_from} consecutive excerpts of "{title}", in order'
    return f'Transcript of "{title}" ({render.format_ts(duration)})'


def render_prompt(name: str, **context: Any) -> str:
    """One template from `prompts/`, rendered."""
    return _env.get_template(f"{name}.md").render(**context)


def user_prompt(
    spec: TaskSpec,
    *,
    transcript: str,
    title: str,
    duration: float,
    custom_prompt: str | None = None,
    notes_from: int = 0,
    known_labels: Sequence[str] = (),
) -> str:
    """The kind's own prompt over `transcript`.

    Used twice: over the transcript when it fits in one call, and over the
    combined notes when it did not. The task is the same either way, which is
    why there is one template rather than two that can drift.

    `known_labels` is handed to every template and read by the one that asks
    for it, the same way `prompt` is: a template that does not mention it
    renders exactly as before.
    """
    return render_prompt(
        spec.template,
        transcript=transcript,
        source_label=source_label(title, duration, notes_from=notes_from),
        prompt=(custom_prompt or "").strip(),
        known_labels=list(known_labels),
    )


def strict_json_schema(schema: dict) -> dict:
    """A pydantic JSON Schema the structured-output endpoints will accept.

    Measured against the live API on 2026-09-03: `Chapters.model_json_schema()`
    sent as-is to `openai/gpt-5.6-luna` through OpenRouter came back **HTTP
    400** - *"Invalid schema for response_format 'action_items': In context=(),
    'additionalProperties' is required to be supplied and to be false."* The
    endpoint validates a `json_schema` response format against OpenAI's strict
    subset whether or not `strict` was asked for, and pydantic emits neither
    that keyword nor a `required` list covering optional fields.

    So two rewrites, applied to every object in the tree including the `$defs`
    a nested model produces:

    * `additionalProperties: false` - the endpoint refuses a schema without it;
    * `required` = every declared property. Strict mode has no optional keys.
      An "optional" field stays optional in the only way that survives: its type
      is already a union with null (`OptionalSeconds`, `owner`), so the model
      must supply the key and may answer null. `default` is dropped for the same
      reason - a default is a statement about a key that may be missing, and
      here none may be.

    The rewrite happens here rather than in the models because it is a property
    of the wire format, not of the data: `parse_answer` still validates with
    pydantic's own rules, which is what makes a provider *without* constrained
    decoding produce the same objects as one with it.
    """
    node = json.loads(json.dumps(schema))  # a copy; the model's own schema is cached

    # The walk knows which dicts are schemas and which are name->schema maps,
    # rather than recursing into everything and hoping. A field called
    # `default` would otherwise be deleted from `properties`, and one called
    # `properties` would have `additionalProperties` injected into it - both
    # silent, and both invisible to a test that only checks the keywords are
    # there. No kind names such a field today; the next one might.
    MAPS = ("properties", "$defs", "definitions", "patternProperties")

    def rewrite(value: Any) -> None:
        """`value` is a schema node."""
        if not isinstance(value, dict):
            if isinstance(value, list):  # anyOf, oneOf, prefixItems
                for item in value:
                    rewrite(item)
            return

        if value.get("type") == "object" or "properties" in value:
            value["additionalProperties"] = False
            value["required"] = list(value.get("properties") or {})
        value.pop("default", None)

        for key, child in value.items():
            if key in MAPS and isinstance(child, dict):
                for schema_node in child.values():
                    rewrite(schema_node)
            elif key not in MAPS:
                rewrite(child)

    rewrite(node)
    return node


def schema_payload(spec: TaskSpec, supports_json_schema: bool) -> dict | None:
    """The `json_schema` for a `ChatRequest`, or None to ask in words instead.

    The capability is read off the provider class (`Provider.supports_json_schema`),
    never off its name: Global Constraints forbid an `if provider == ...` in a
    caller, and a provider added later gets whichever branch its own attribute
    asks for.

    `strict` is declared because the schema is now strict-shaped, and declaring
    it is the difference between the endpoint validating the schema and the
    endpoint constraining the model to it.
    """
    if spec.schema is None or not supports_json_schema:
        return None
    return {
        "name": spec.kind,
        "schema": strict_json_schema(spec.schema.model_json_schema()),
        "strict": True,
    }


# --- budgets --------------------------------------------------------------------------------


def context_tokens_for(provider_cls: type[base.Provider], model: str | None = None) -> int:
    """How much context to plan against for this provider, by locality.

    `is_local` rather than a table of names: what makes the local number small
    is that the window is this machine's VRAM, and what makes the cloud number
    large is that it is not. A caller with a specific model in hand overrides it.

    The provider is asked first (`window_for_model`). A local runtime can read
    the model's real window off the daemon, and the constants below are what
    answers when nobody can: before this asked, a 262144-token model was
    planned against 8192 because that is what the table said (TASK-084).
    """
    known = provider_cls.window_for_model(model)
    if known:
        return known
    return LOCAL_CONTEXT_TOKENS if provider_cls.is_local else CLOUD_CONTEXT_TOKENS


def fit_output_tokens(
    spec: TaskSpec,
    *,
    context_tokens: int,
    max_output_tokens: int | None = None,
    title: str = "",
    duration: float = 0.0,
    custom_prompt: str | None = None,
) -> int:
    """The answer budget this window can actually afford for ``spec``.

    A spec's `max_output_tokens` is a ceiling chosen so a model that thinks is
    not cut off mid-answer, and it was measured where windows are large. In a
    small window the same number is a claim on space the transcript needs, so
    it is clamped to what is left after `MIN_TRANSCRIPT_TOKENS` - the cap stays
    a cap, and the call happens. A window too small to hold even
    `MIN_OUTPUT_TOKENS` beside that floor is refused here, in words, rather
    than by `budget_for` subtracting its way to a negative number.
    """
    wanted = spec.max_output_tokens if max_output_tokens is None else max_output_tokens
    overhead = estimate_tokens(system_for(spec)) + estimate_tokens(
        user_prompt(spec, transcript="", title=title, duration=duration, custom_prompt=custom_prompt)
    )
    affordable = context_tokens - overhead - MIN_TRANSCRIPT_TOKENS
    if affordable < MIN_OUTPUT_TOKENS:
        raise ValueError(
            f"a {context_tokens}-token context cannot hold the {spec.kind!r} task: "
            f"{overhead} tokens of prompt, at least {MIN_TRANSCRIPT_TOKENS} of transcript and "
            f"{MIN_OUTPUT_TOKENS} of answer need {overhead + MIN_TRANSCRIPT_TOKENS + MIN_OUTPUT_TOKENS}. "
            f"Choose a model with a bigger window"
        )
    return max(MIN_OUTPUT_TOKENS, min(wanted, affordable))


def budget_for(
    spec: TaskSpec,
    *,
    context_tokens: int,
    max_output_tokens: int,
    title: str = "",
    duration: float = 0.0,
    custom_prompt: str | None = None,
) -> int:
    """How much transcript fits in one call: the window, less everything else.

    Measured against the prompt that will actually be sent - the template
    rendered with an empty transcript - rather than a guessed constant, so
    editing a template moves the budget with it. `chunking.estimate_tokens` is
    pessimistic by about a fifth, and that is the whole safety margin: erring
    long costs a chunk boundary, erring short costs a failed call.
    """
    overhead = estimate_tokens(system_for(spec)) + estimate_tokens(
        user_prompt(
            spec,
            transcript="",
            title=title,
            duration=duration,
            custom_prompt=custom_prompt,
        )
    )
    budget = context_tokens - overhead - max_output_tokens
    if budget <= 0:
        raise ValueError(
            f"a {context_tokens}-token context leaves nothing for the transcript after "
            f"{overhead} tokens of prompt and {max_output_tokens} tokens of answer; "
            f"use a model with a bigger window or a smaller answer budget"
        )
    return budget


def combine_capacity(
    spec: TaskSpec,
    *,
    context_tokens: int,
    max_output_tokens: int,
    chunks: int,
    title: str = "",
    duration: float = 0.0,
    custom_prompt: str | None = None,
) -> int:
    """How many tokens of notes the one combine call can actually hold.

    The mirror of `budget_for`, for the other prompt a chunked task sends. The
    combine call reads `map_combine.md` wrapped around the kind's own template,
    with the notes where the transcript would be - so it is measured the same
    way, by rendering the real templates with an empty body, and editing either
    of them moves the number with it.

    This is the constraint the whole map-reduce has to satisfy and the one
    nothing used to check: N chunks' notes have to come back through a window
    that only ever held one chunk's worth of transcript in the first place.
    """
    overhead = estimate_tokens(system_for(spec)) + estimate_tokens(
        render_prompt(
            chunking.MAP_REDUCE_PROMPTS["combine"],
            count=chunks,
            body=user_prompt(
                spec,
                transcript="",
                title=title,
                duration=duration,
                custom_prompt=custom_prompt,
                notes_from=chunks,
            ),
        )
    )
    return context_tokens - overhead - max_output_tokens


# --- a planned task -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskPlan:
    """Everything the calls need, decided before the first one is made.

    Separate from `generate` so the `llm` job can plan in one stage and call in
    the next: a bad kind, an unreachable transcript or a pinned media then fails
    before any money is spent, and the jobs board says which stage it was.
    """

    media_id: int
    run_id: int
    spec: TaskSpec
    provider_name: str
    model: str
    chunks: tuple[Chunk, ...]
    title: str
    duration: float
    budget_tokens: int
    max_output_tokens: int
    custom_prompt: str | None = None
    words_hash: str = ""
    """`words_fingerprint` of the words the chunks were cut from, taken when
    the document was loaded - so a cleaning records what it read even if a
    correction lands while the model is answering (TASK-071)."""
    known_labels: tuple[str, ...] = ()
    """The vocabulary the library already uses, for the kinds whose prompt
    offers it. Read once when the plan is made rather than at each call, so
    every call in one run sees the same list - a chunked recording that grew
    its own vocabulary halfway through would be asked to reuse labels it had
    just invented, from a run that has not finished deciding them yet."""
    provider_supports_schema: bool = False
    note_budget: int = 0
    """The answer cap for one chunk's notes, decided here rather than in
    `_collect_notes` so that the number `plan_task` proved would fit back
    through the combine call is provably the number that gets spent. Zero for
    a task that is not chunked, which makes no note call at all."""

    @property
    def kind(self) -> str:
        return self.spec.kind

    @property
    def chunked(self) -> bool:
        """True when this needs a map-reduce: more than one chunk, so more than
        one call and a combine at the end."""
        return len(self.chunks) > 1

    @property
    def note_question(self) -> str | None:
        """The question the chunk notes are taken for, when there is one.

        One property read by both the key (`chunk_kind`) and the instruction
        (`chunk_goal`), because those two must never disagree: a question in
        the prompt but not in the key is the worse of the two bugs it fixes -
        notes deliberately taken for question A, handed to question B.
        """
        return self.custom_prompt if self.spec.needs_prompt else None


@dataclass(frozen=True)
class TaskResult:
    """What the calls produced, before it is a row."""

    content: str
    """Exactly what will be stored: canonical JSON for a kind with a schema,
    the model's own text for `custom`."""
    payload: BaseModel | str
    response: ChatResponse
    chunk_output_ids: tuple[int, ...] = ()
    calls: int = 0


def plan_task(
    conn: sqlite3.Connection,
    *,
    media_id: int,
    kind: str,
    provider_name: str,
    model: str | None = None,
    custom_prompt: str | None = None,
    run_id: int | None = None,
    context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    budget_tokens: int | None = None,
    overlap_segments: int = 1,
) -> TaskPlan:
    """Decide what to ask, of which model, in how many calls.

    The order of the first three checks is deliberate: an unknown kind or a
    missing custom prompt is a caller's mistake and costs nothing to catch; the
    privacy check comes next, so a pinned recording's words are never even read
    into this process for a call that `llm.chat()` was going to refuse anyway.
    """
    spec = task_spec(kind)
    if spec.needs_prompt and not (custom_prompt or "").strip():
        raise ValueError(f"the {kind!r} task needs a prompt: there is no question without one")

    provider_cls = llm.provider_class(provider_name)
    privacy.assert_allowed(conn, media_id, provider_cls)

    chosen_model = (model or "").strip() or provider_cls.default_model
    if not chosen_model:
        raise ValueError(f"provider {provider_name!r} has no default model; name one explicitly")

    doc = docs.load(conn, media_id, run_id)
    # Resolved whether or not `budget_tokens` was given: the window is a
    # property of the model, and a caller slicing the transcript smaller than
    # it has to has not made the window smaller.
    window = context_tokens or context_tokens_for(provider_cls, chosen_model)
    # The spec's ceiling, clamped to what this window affords. A caller who
    # named a number is clamped too: the window is the window, and a request
    # for more answer than it holds is the failure TASK-084 was about.
    output_tokens = fit_output_tokens(
        spec,
        context_tokens=window,
        max_output_tokens=max_output_tokens,
        title=doc.title,
        duration=doc.duration,
        custom_prompt=custom_prompt,
    )
    if budget_tokens is None:
        budget_tokens = budget_for(
            spec,
            context_tokens=window,
            max_output_tokens=output_tokens,
            title=doc.title,
            duration=doc.duration,
            custom_prompt=custom_prompt,
        )

    if spec.combine == "concat":
        # Each chunk comes back about as long as it went in, so a chunk is cut
        # to at most CONCAT_CHUNK_TOKENS - never more than the answer cap -
        # which leaves a model that reasons inside the cap room to do it; and a
        # seam repeated in two answers would be a sentence said twice, so no
        # overlap.
        #
        # Measured 2026-09-10: cleanup failing with finish_reason='length'
        # against openrouter/auto is reasoning, not runaway - 22,515 reasoning
        # tokens for a cleaning deepseek-v4-flash also gave in 1,171 with
        # reasoning off. TASK-029's rule: the kind asks for no reasoning
        # (`TaskSpec.reasoning_off`); a model that ignores that either finishes
        # each chunk inside its cap, or ends 'length' and is refused before
        # anything is stored (`_refuse_a_cut_off_part`); and on an endpoint that
        # does not enforce the cap only the hint bounds it, which each row
        # records (`reasoning_record`). The one true runaway (168 words in,
        # 5,742 out) was a transcript that is itself a 112-word "La, la" loop;
        # the cleaning gate refuses that kind.
        budget_tokens = min(budget_tokens, output_tokens, CONCAT_CHUNK_TOKENS)
        overlap_segments = 0
    chunks = chunking.plan(
        doc,
        budget_tokens=budget_tokens,
        overlap_segments=overlap_segments,
        speakers=spec.with_speakers,
    )
    if not chunks:
        raise docs.NoTranscript(
            f"media {media_id} has a transcript with no segments; there is nothing to ask about"
        )

    note_budget = 0 if spec.combine == "concat" else plan_notes(
        spec,
        chunks=len(chunks),
        context_tokens=window,
        budget_tokens=budget_tokens,
        max_output_tokens=output_tokens,
        title=doc.title,
        duration=doc.duration,
        custom_prompt=custom_prompt,
    )

    return TaskPlan(
        media_id=media_id,
        run_id=int(doc.run["id"]),
        words_hash=words_fingerprint(doc.words),
        spec=spec,
        provider_name=provider_name,
        model=chosen_model,
        chunks=tuple(chunks),
        title=doc.title,
        duration=doc.duration,
        budget_tokens=budget_tokens,
        max_output_tokens=output_tokens,
        custom_prompt=(custom_prompt or "").strip() or None,
        known_labels=vocabulary(conn) if spec.kind == "labels" else (),
        provider_supports_schema=bool(provider_cls.supports_json_schema),
        note_budget=note_budget,
    )


def plan_notes(
    spec: TaskSpec,
    *,
    chunks: int,
    context_tokens: int,
    budget_tokens: int,
    max_output_tokens: int,
    title: str = "",
    duration: float = 0.0,
    custom_prompt: str | None = None,
) -> int:
    """The answer cap for one chunk's notes - or a refusal, before any spend.

    Two bounds, and the whole point is that they can conflict:

    * the notes must fit back through the combine call (`combine_capacity`
      shared out between the chunks), because that call is the only one that
      sees all of them;
    * a note is a compression of its chunk, so asking for a longer note than
      the chunk it came from is asking the model to pad. `budget_tokens` is
      that bound, and it only ever binds when a caller sliced the transcript
      smaller than the window required.

    Under both sits `MIN_NOTE_TOKENS`, which is not a preference but a
    measurement: a smaller cap buys an empty answer from the shipped local
    model, so a budget below it is not a smaller answer, it is no answer.

    When the floor does not fit, this configuration has none - and saying so
    here costs nothing, where saying so in the combine call costs every map
    call first and then caches notes that make the retry a no-op.
    """
    if chunks <= 1:
        return 0  # no note call is made; there is nothing to budget

    capacity = combine_capacity(
        spec,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        chunks=chunks,
        title=title,
        duration=duration,
        custom_prompt=custom_prompt,
    )
    # What one note may be, given that all of them have to come back through
    # one combine call. `MIN_NOTE_TOKENS` is what a note is *worth* asking for;
    # `affordable` is what there is room for. Taking the smaller of the two -
    # rather than refusing whenever they disagree - is what makes a long
    # recording work in a small window at all (Robert, 2026-09-19: adapt the
    # content length rather than assume a large context).
    # Only the *capacity* is shared between the chunks. `budget_tokens` is a
    # per-chunk bound - a note should not be longer than the chunk it
    # compresses - and dividing it too was how a 128000-token window came to
    # report room for 25 tokens a chunk.
    affordable = capacity // max(chunks, 1)
    note_budget = min(MIN_NOTE_TOKENS, affordable, max(budget_tokens, MIN_NOTE_FLOOR))

    if note_budget < MIN_NOTE_FLOOR:
        # Now it really is impossible: room for fewer than a sentence or two
        # per chunk is not a smaller answer, it is no answer. Say so with the
        # numbers, and name something that would work.
        raise base.ContextTooLong(
            f"this recording needs {chunks} chunks, and a {context_tokens}-token window leaves "
            f"room for {capacity} tokens of notes to come back through one combine call - "
            f"{affordable} per chunk, where {MIN_NOTE_FLOOR} is the least that is still a note. "
            f"Nothing has been sent and nothing will be. {suggest_bigger_window(context_tokens)} "
            "Chat still works here either way - it retrieves the parts it needs instead of "
            "reading all of it."
        )
    return note_budget


# --- reading what came back ------------------------------------------------------------------------

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(?P<body>.*?)```", re.DOTALL)


def clip(text: str, limit: int = RAW_TEXT_LIMIT) -> str:
    """`text`, short enough to travel in an error message."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [{len(text) - limit} more characters]"


def repair_json(text: str) -> dict:
    """The JSON object in `text`, however the model wrapped it.

    Three passes, cheapest first: the whole answer, then the contents of a code
    fence, then the outermost `{...}`. Every one of these has been seen from a
    real model - fencing is what chat models are trained to do with structured
    output, and an apology before the object is what a small local model does
    when the instruction and its manners disagree.

    A list, a number or a string is *not* accepted as a repair: the kinds all
    promise an object, and quietly treating a bare array as one would guess at
    which field the caller meant.
    """
    candidates = [text]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group("body"))
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload

    raise base.BadResponse(
        "the model did not answer with a JSON object. What it said: " + clip(text)
    )


def parse_answer(spec: TaskSpec, text: str) -> BaseModel | str:
    """`text` as the kind promises it, or a `BadResponse` carrying `text`."""
    stripped = (text or "").strip()
    if not stripped:
        raise base.BadResponse(
            f"the model returned nothing for the {spec.kind!r} task; there is no answer to store"
        )
    if spec.schema is None:
        return stripped

    payload = repair_json(stripped)
    try:
        return spec.schema.model_validate(payload)
    except ValidationError as exc:
        raise base.BadResponse(
            f"the model's answer is not a valid {spec.kind!r} output ({exc.error_count()} "
            f"problem(s): {exc.errors()[0].get('msg', '')} at "
            f"{'.'.join(str(p) for p in exc.errors()[0].get('loc', ()))}). "
            "What it said: " + clip(stripped)
        ) from None


def content_for(spec: TaskSpec, payload: BaseModel | str) -> str:
    """What goes in the row: canonical JSON, or the text for a kind with no schema.

    Storing the validated object rather than the raw answer means the repair
    happens once, here, and every later reader of the table gets something that
    parses. The model's own wording survives inside the values; only its
    packaging is normalised.
    """
    if isinstance(payload, BaseModel):
        return json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return str(payload)


# --- storage ------------------------------------------------------------------------------------------


def _insert_output(
    conn: sqlite3.Connection,
    *,
    media_id: int,
    run_id: int | None,
    kind: str,
    provider: str,
    model: str,
    content: str,
    response: ChatResponse,
    params: dict,
) -> int:
    """One `llm_output` row. Always an insert - see the module docstring."""
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO llm_output(media_id, run_id, kind, provider, model, prompt_version,"
            " content, prompt_tokens, completion_tokens, params_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                media_id,
                run_id,
                kind,
                provider,
                model,
                PROMPT_VERSION,
                content,
                response.prompt_tokens,
                response.completion_tokens,
                json.dumps(params, ensure_ascii=False),
                time.time(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def stored_chunk(conn: sqlite3.Connection, plan: TaskPlan, chunk: Chunk) -> dict | None:
    """The stored answer for `chunk` of this exact task, if there is one.

    The whole key matters. The run is in it because the chunk boundaries are a
    property of the segments, so an answer about chunk 3 of a previous
    transcription is an answer about different words; the model and the prompt
    version are in it because notes taken by one model are not notes taken by
    another; and for `custom` the question is in it (inside the kind, via
    `chunk_kind`) because notes taken for one question are not notes about a
    different one.

    **And the segments.** Within one run the boundaries still move when the
    chunk budget does, so the newest row under the key is reused only when its
    `segment_ids` are this chunk's; otherwise the chunk is asked again. A row
    that does not name its segments cannot prove it covers these, and is asked
    again too. The receipt is row 14: media 12's cleanup part 0, made on
    6,000-token chunks, covers 184 segments. Reused by its index under
    3,000-token chunks it would stand in for a shorter stretch, the next part
    would repeat what lies between, and the gate would refuse media 12 on every
    run. Only the newest row is looked at. An older row with the same segments
    would be a correct reuse too; not looking for it costs one call after the
    budget has moved twice, and keeps this one query.

    **The job id is deliberately not in it, and that has a consequence worth
    stating**: resuming an interrupted task and deliberately re-running a
    finished one are the same operation here. A second run of a chunked task
    with the same provider, model and prompt version reuses every note and
    makes only the combine call. That is what makes an interrupted job cheap,
    and it means a "regenerate" button (Task 6) will look like it did almost
    nothing on a long recording - the final row is new, but the notes it was
    built from are the ones that were already bought. Changing the model, the
    provider or `PROMPT_VERSION` is what asks the chunks again, because those
    are the things that would make a different answer.
    """
    with db.LOCK:
        row = conn.execute(
            "SELECT id, content, params_json FROM llm_output"
            " WHERE media_id=? AND run_id IS ? AND kind=? AND provider=? AND model=?"
            "   AND prompt_version=? ORDER BY id DESC LIMIT 1",
            (
                plan.media_id,
                plan.run_id,
                chunk_kind(plan.kind, chunk.index, plan.note_question),
                plan.provider_name,
                plan.model,
                PROMPT_VERSION,
            ),
        ).fetchone()
    if row is None:
        return None
    try:
        params = json.loads(row["params_json"] or "{}")
    except ValueError:
        return None
    if not isinstance(params, dict) or params.get("segment_ids") != list(chunk.segment_ids):
        return None
    if int(row["id"]) in refused_part_ids(conn, plan):
        return None
    return {"id": row["id"], "content": row["content"]}


# --- TASK-088: a refused reading's parts are asked again ----------------------


def refused_part_ids(conn: sqlite3.Connection, plan: TaskPlan) -> set[int]:
    """The parts that went into a reading the gate refused, in this run.

    ADR-010 decided the refusal of a copied part together with its repair.
    `stored_chunk` keys a part by provider, model, prompt version and
    segments, and did not ask whether the reading it went into was published,
    so a rerun pulled a refused reading's parts back in and was refused again:
    repairable only by emptying the cache. A part named in the
    `chunk_output_ids` of a final row whose `gate` says it was not published
    is asked again instead - every part of that reading, not only the one a
    reason names, because the verdict is about the reading.

    A final row without a `gate` (made before TASK-055, or by a kind the gate
    does not judge) refuses nothing. An interrupted run has no final row, so
    resuming it still reuses every part. A reading that was published before
    this rule existed and would be refused now takes two reruns to repair: the
    first reuses its parts and is refused, the second asks them again.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT params_json FROM llm_output WHERE media_id=? AND run_id IS ? AND kind=?",
            (plan.media_id, plan.run_id, plan.kind),
        ).fetchall()
    refused: set[int] = set()
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
        except (TypeError, ValueError):
            continue
        gate = params.get("gate") if isinstance(params, dict) else None
        if isinstance(gate, dict) and gate.get("published") is False:
            ids = params.get("chunk_output_ids") or []
            refused.update(int(i) for i in ids if isinstance(i, int))
    return refused


def reasoning_record(response: ChatResponse, cap: int) -> dict[str, Any]:
    """What one call's reasoning cost, and what can honestly be said about it.

    Rows 6, 17 and 19 spent 5,651, 12,767 and 21,401 tokens against caps of
    4,000 and 8,000 and came back 'stop', and nothing on them said the cap had
    not held or that reasoning was why. So every row now carries the four
    reported numbers - `hint_sent`, `reasoning_tokens`, `reasoning_chars`,
    `upstream`, each None when unreported - and two flags derived from them:

    * `hint_ignored` - the hint went on the wire (`hint_sent` True) and the
      model reasoned anyway. Judged only when a count came back: reasoning
      tokens from a cloud provider, thinking characters from Ollama. A dropped
      hint (`hint_sent` False) is not an ignored one, and stays None.
    * `cap_not_enforced` - more completion tokens came back than `cap` allowed,
      so the endpoint did not hold the line, and only the hint bounded the
      spend. None when no count came back.

    `cap` is the cap *this call* was asked with: the note budget for a note,
    the kind's answer cap for everything else. No schema change: `params_json`
    is only ever read by key.
    """
    spent = (
        response.reasoning_tokens
        if response.reasoning_tokens is not None
        else response.reasoning_chars
    )
    completion = response.completion_tokens
    return {
        "hint_sent": response.hint_sent,
        "reasoning_tokens": response.reasoning_tokens,
        "reasoning_chars": response.reasoning_chars,
        "upstream": response.upstream,
        "hint_ignored": spent > 0 if response.hint_sent is True and spent is not None else None,
        "cap_not_enforced": completion > cap if completion is not None else None,
    }


def store_output(conn: sqlite3.Connection, plan: TaskPlan, result: TaskResult) -> int:
    """Write the final row; returns its id.

    The `model` column holds the model that was *asked for*, because that column
    is a key: the resume above reads it, and a provider answering with a dated
    snapshot id (`gpt-4o-mini-2024-07-18`) would otherwise split one key into
    one per snapshot. What actually served the request is provenance and goes
    into `params_json` next to it - and so does what its reasoning cost
    (`reasoning_record`). A joined concat row carries None there: it was no
    single call, and its parts' rows hold the numbers.
    """
    params: dict[str, Any] = {
        "chunks": len(plan.chunks),
        "budget_tokens": plan.budget_tokens,
        "max_output_tokens": plan.max_output_tokens,
        "served_model": result.response.model,
        "finish_reason": result.response.raw_finish_reason,
        "calls": result.calls,
        **reasoning_record(result.response, plan.max_output_tokens),
    }
    if result.chunk_output_ids:
        params["chunk_output_ids"] = list(result.chunk_output_ids)
    if plan.custom_prompt:
        params["custom_prompt"] = plan.custom_prompt
    return _insert_output(
        conn,
        media_id=plan.media_id,
        run_id=plan.run_id,
        kind=plan.kind,
        provider=plan.provider_name,
        model=plan.model,
        content=result.content,
        response=result.response,
        params=params,
    )


def outputs_for(conn: sqlite3.Connection, media_id: int, kind: str) -> list[dict]:
    """Every stored answer of one kind for one media, newest first.

    The read the AI panel does, and the reason `idx_llm_output_media_kind`
    exists. Chunk rows are excluded: they are working notes that happen to be
    durable, and a panel listing them would be showing its own scaffolding.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT * FROM llm_output WHERE media_id=? AND kind=? ORDER BY id DESC",
            (media_id, kind),
        ).fetchall()
    return [dict(row) for row in rows]


# --- making the calls -------------------------------------------------------------------------------------


EXCERPT_CHARS = 700
"""How much of a prompt or a reply a live-log event carries, per end.

The size question is the whole design of TASK-085. A chunk of transcript is
thousands of tokens and a chunked job makes a dozen calls, so writing every
prompt in full would put megabytes into `job_event` rows and down a stream the
page replays on every reload. Both ends and a count of the middle is what a
person watching actually reads: how the prompt starts, how it ends, and how
much they are not being shown."""


MARKER_WORTH_IT = 200
"""The least a cut must hide before it is worth saying it was cut."""


def excerpt(text: str, *, limit: int = EXCERPT_CHARS) -> str:
    """``text``, or its two ends with the middle counted.

    Never raises and never returns None: this feeds a log, and ADR-014 is that
    the log is observation - it must not become the thing that fails a job.
    """
    text = text or ""
    dropped = len(text) - limit * 2
    # A marker that hides less than it costs to read is noise: a system prompt
    # cut by three characters came out as "... [3 characters not shown] ...",
    # which reads as breakage rather than as an excerpt.
    if dropped < MARKER_WORTH_IT:
        return text
    return f"{text[:limit]}\n... [{dropped} characters not shown] ...\n{text[-limit:]}"


def conclusion(kind: str, answer: str) -> str:
    """What this kind concluded, in one line a person can read.

    A blob of JSON in a live log is not an answer to "what did it decide"; for
    `speakers` the decision is which cluster became which name, and that is
    what gets said. Anything unreadable is reported as such rather than raised.
    """
    try:
        data = json.loads(answer) if isinstance(answer, str) else (answer or {})
    except Exception:  # noqa: BLE001 - an answer that will not parse is still news
        return f"answer could not be read as JSON ({len(answer or '')} characters)"
    if not isinstance(data, dict):
        return f"answer was {type(data).__name__}, not an object"

    if kind == "speakers":
        rows = data.get("speakers") or []
        named = [
            f"{row.get('cluster') or row.get('label') or '?'} = {row.get('name') or 'unnamed'}"
            + (f" ({row.get('role')})" if row.get("role") else "")
            for row in rows
            if isinstance(row, dict)
        ]
        return "; ".join(named) if named else "no speaker could be named from this transcript"
    if kind == "labels":
        rows = data.get("labels") or []
        return "; ".join(str(row.get("label")) for row in rows if isinstance(row, dict)) or "no labels"

    text = next((str(v) for v in data.values() if isinstance(v, str) and v.strip()), "")
    return excerpt(text, limit=200) if text else f"{len(json.dumps(data))} characters of answer"


def _ask(
    conn: sqlite3.Connection,
    plan: TaskPlan,
    *,
    system: str,
    user: str,
    max_output_tokens: int,
    json_schema: dict | None,
    phase: str = "single",
    on_call: Callable[[str, ChatRequest, ChatResponse], None] | None = None,
    **provider_kwargs: Any,
) -> ChatResponse:
    """One call, through the front door.

    `llm.chat` and not a provider directly: the pin, the registry and the retry
    policy all live behind it, and a caller that goes around it goes around all
    three.

    The kind's reasoning hint rides on every call made here - a note, a part,
    a single answer, a combine - so no call of a hinted kind goes out without it.
    """
    request = ChatRequest(
        system=system,
        user=user,
        model=plan.model,
        max_output_tokens=max_output_tokens,
        json_schema=json_schema,
        reasoning_off=plan.spec.reasoning_off,
    )
    def watched(response: ChatResponse | None) -> None:
        """Tell the watcher, and never let it stop the work (ADR-014)."""
        if on_call is None:
            return
        try:
            on_call(phase, request, response)
        except Exception:  # noqa: BLE001 - watching must never break the work
            pass

    # Before the call, not only after it. The prompt is knowable the moment it
    # goes out and the reply can be minutes later on a local 27B model: telling
    # the watcher only once, afterwards, is a live log that says nothing while
    # the one slow thing is happening - which is exactly what job 127 showed,
    # sitting on `generate` with no prompt in sight.
    watched(None)
    response = llm.chat(
        conn,
        media_id=plan.media_id,
        provider_name=plan.provider_name,
        request=request,
        **provider_kwargs,
    )
    watched(response)
    return response


def generate(
    conn: sqlite3.Connection,
    plan: TaskPlan,
    *,
    on_progress: Callable[[float], None] | None = None,
    on_call: Callable[[str, ChatRequest, ChatResponse], None] | None = None,
    **provider_kwargs: Any,
) -> TaskResult:
    """Make the calls the plan asks for and validate the last one.

    One call when the transcript fits. Otherwise one call per chunk producing
    notes - stored as they arrive, so an interruption keeps them - and one
    final call combining the notes into the kind's shape.

    `on_progress` is called after each call with 0..1, a reused chunk counting
    as progress: from the outside, work that does not have to be done again is
    work that is done.

    `on_call` is handed every call this makes, with the phase it belongs to -
    what the live log is written from (TASK-085). It watches; it decides
    nothing, and an exception in it is swallowed at the door in `_ask`.
    """
    total_calls = len(plan.chunks) + (1 if plan.chunked else 0)
    done = 0

    def step() -> None:
        nonlocal done
        done += 1
        if on_progress is not None:
            on_progress(done / total_calls)

    schema = schema_payload(plan.spec, plan.provider_supports_schema)

    if not plan.chunked:
        response = _ask(
            conn,
            plan,
            system=system_for(plan.spec),
            user=user_prompt(
                plan.spec,
                transcript=plan.chunks[0].text,
                title=plan.title,
                duration=plan.duration,
                custom_prompt=plan.custom_prompt,
                known_labels=plan.known_labels,
            ),
            max_output_tokens=plan.max_output_tokens,
            json_schema=schema,
            **provider_kwargs,
            phase="single",
            on_call=on_call,
        )
        step()
        if plan.spec.combine == "concat":
            # The same refusal `_collect_parts` makes, for the recording short
            # enough to be one part. Row 15 (2026-09-11) is why: media 7's
            # cleanup came back at 12,000 of 12,000 tokens, finish 'length',
            # and was stored as the cleaning. `custom` is left as it was - its
            # row is stored and the panel says it was cut off - and a schema
            # kind's cut-off object already fails `repair_json`.
            _refuse_a_cut_off_part(plan, plan.chunks[0], response)
        payload = parse_answer(plan.spec, response.text)
        return TaskResult(
            content=content_for(plan.spec, payload),
            payload=payload,
            response=response,
            calls=1,
        )

    if plan.spec.combine == "concat":
        parts, chunk_ids, calls = _collect_parts(conn, plan, step, on_call, **provider_kwargs)
        text = "\n\n".join(parts)
        payload = parse_answer(plan.spec, text)
        return TaskResult(
            content=content_for(plan.spec, payload),
            payload=payload,
            # No single response stands for the whole: the parts were separate
            # calls, and their token counts live on their own rows.
            response=ChatResponse(
                text=text,
                model=plan.model,
                provider=plan.provider_name,
                prompt_tokens=None,
                completion_tokens=None,
                raw_finish_reason="stop",
            ),
            chunk_output_ids=tuple(chunk_ids),
            calls=calls,
        )

    notes, chunk_ids, calls = _collect_notes(conn, plan, step, on_call, **provider_kwargs)

    body = user_prompt(
        plan.spec,
        transcript="\n\n".join(notes),
        title=plan.title,
        duration=plan.duration,
        custom_prompt=plan.custom_prompt,
        notes_from=len(notes),
        known_labels=plan.known_labels,
    )
    response = _ask(
        conn,
        plan,
        system=system_for(plan.spec),
        user=render_prompt(
            chunking.MAP_REDUCE_PROMPTS["combine"], count=len(notes), body=body
        ),
        max_output_tokens=plan.max_output_tokens,
        json_schema=schema,
        **provider_kwargs,
        phase="combine",
        on_call=on_call,
    )
    step()
    payload = parse_answer(plan.spec, response.text)
    return TaskResult(
        content=content_for(plan.spec, payload),
        payload=payload,
        response=response,
        chunk_output_ids=tuple(chunk_ids),
        calls=calls + 1,
    )


def _collect_notes(
    conn: sqlite3.Connection,
    plan: TaskPlan,
    step: Callable[[], None],
    on_call: Callable[[str, ChatRequest, ChatResponse], None] | None = None,
    **provider_kwargs: Any,
) -> tuple[list[str], list[int], int]:
    """Notes for every chunk, asking only for the ones not already stored.

    Each chunk's notes are committed as they arrive rather than at the end,
    which is the entire point: the row is what a resumed job finds. The
    per-chunk answer budget was decided in `plan_notes`, and is read here
    rather than recomputed: the number that was proved to fit back through the
    combine call has to be the number that is actually spent, and two
    computations of one budget is exactly how those come apart.
    """
    note_budget = plan.note_budget
    notes: list[str] = []
    chunk_ids: list[int] = []
    calls = 0

    for chunk in plan.chunks:
        existing = stored_chunk(conn, plan, chunk)
        if existing is not None:
            notes.append(existing["content"])
            chunk_ids.append(existing["id"])
            step()
            continue

        response = _ask(
            conn,
            plan,
            system=SYSTEM,
            user=render_prompt(
                chunking.MAP_REDUCE_PROMPTS["chunk"],
                index=chunk.index + 1,  # one-based: the model is reading, not indexing
                count=len(plan.chunks),
                start=render.format_ts(chunk.start),
                end=render.format_ts(chunk.end),
                goal=chunk_goal(plan.spec, plan.note_question),
                transcript=chunk.text,
            ),
            max_output_tokens=note_budget,
            json_schema=None,  # notes are prose; only the combine has a shape to keep
            **provider_kwargs,
            phase="note",
            on_call=on_call,
        )
        calls += 1
        where = f"({render.format_ts(chunk.start)}-{render.format_ts(chunk.end)})"
        text = (response.text or "").strip()
        if not text:
            raise base.BadResponse(
                f"the model returned nothing for chunk {chunk.index} of the {plan.kind!r} task "
                f"{where}"
            )
        # Emptiness used to be the only thing checked, and a note that stopped
        # mid-word because it hit `note_budget` looks exactly like a short one.
        # Storing it is what makes it permanent: the row is the cache, so every
        # later run of this task - including the one asked because the answer
        # looked wrong - is built from half a sentence. Refusing *before* the
        # insert is the load-bearing half; raising is how the job says so.
        if response.raw_finish_reason == TRUNCATED_FINISH:
            raise base.BadResponse(
                f"the model ran out of room writing its notes for chunk {chunk.index} of the "
                f"{plan.kind!r} task {where}: it stopped at the {note_budget}-token cap "
                f"({_spent(response)}) rather than finishing, so the "
                "notes are cut off mid-sentence and have not been stored. What it managed: "
                + clip(text)
            )
        chunk_ids.append(
            _insert_output(
                conn,
                media_id=plan.media_id,
                run_id=plan.run_id,
                kind=chunk_kind(plan.kind, chunk.index, plan.note_question),
                provider=plan.provider_name,
                model=plan.model,
                content=text,
                response=response,
                params={
                    "chunk": chunk.index,
                    "chunks": len(plan.chunks),
                    "start": chunk.start,
                    "end": chunk.end,
                    "segment_ids": list(chunk.segment_ids),
                    "tokens": chunk.tokens,
                    "oversized": chunk.oversized,
                    "served_model": response.model,
                    # The final row has always carried this; a chunk row is the
                    # one nobody looks at again, which is exactly why it needs
                    # to say how the model stopped.
                    "finish_reason": response.raw_finish_reason,
                    # Judged against the note budget this call was asked with.
                    **reasoning_record(response, note_budget),
                },
            )
        )
        notes.append(text)
        step()

    return notes, chunk_ids, calls


def _collect_parts(
    conn: sqlite3.Connection,
    plan: TaskPlan,
    step: Callable[[], None],
    on_call: Callable[[str, ChatRequest, ChatResponse], None] | None = None,
    **provider_kwargs: Any,
) -> tuple[list[str], list[int], int]:
    """The kind's own answer for every chunk, for a `concat` task.

    Same resume rule as `_collect_notes`: a chunk already answered under this
    key is a row, and is reused. The prompt is the kind's own over the chunk,
    because the answer wanted is the chunk rewritten, not notes about it.
    """
    parts: list[str] = []
    chunk_ids: list[int] = []
    calls = 0
    for chunk in plan.chunks:
        existing = stored_chunk(conn, plan, chunk)
        if existing is not None:
            parts.append(existing["content"])
            chunk_ids.append(existing["id"])
            step()
            continue
        response = _ask(
            conn,
            plan,
            system=system_for(plan.spec),
            user=user_prompt(
                plan.spec,
                transcript=chunk.text,
                title=plan.title,
                duration=plan.duration,
                custom_prompt=plan.custom_prompt,
            ),
            max_output_tokens=plan.max_output_tokens,
            json_schema=None,
            **provider_kwargs,
            phase="part",
            on_call=on_call,
        )
        calls += 1
        where = f"({render.format_ts(chunk.start)}-{render.format_ts(chunk.end)})"
        text = (response.text or "").strip()
        if not text:
            raise base.BadResponse(
                f"the model returned nothing for chunk {chunk.index} of the {plan.kind!r} task {where}"
            )
        _refuse_a_cut_off_part(plan, chunk, response)
        chunk_ids.append(
            _insert_output(
                conn,
                media_id=plan.media_id,
                run_id=plan.run_id,
                kind=chunk_kind(plan.kind, chunk.index, plan.note_question),
                provider=plan.provider_name,
                model=plan.model,
                content=text,
                response=response,
                params={
                    "chunk": chunk.index,
                    "chunks": len(plan.chunks),
                    "start": chunk.start,
                    "end": chunk.end,
                    "segment_ids": list(chunk.segment_ids),
                    "tokens": chunk.tokens,
                    "oversized": chunk.oversized,
                    "served_model": response.model,
                    "finish_reason": response.raw_finish_reason,
                    **reasoning_record(response, plan.max_output_tokens),
                },
            )
        )
        parts.append(text)
        step()
    return parts, chunk_ids, calls


def _refuse_a_cut_off_part(plan: TaskPlan, chunk: Chunk, response: ChatResponse) -> None:
    """Refuse, before it is stored, a concat answer that stopped at its cap.

    A concat kind's answer is a stretch of the transcript rewritten, so one
    that ran out of room has lost its tail without a word, and a stored part is
    reused by every later run. One helper for both places a part is asked for -
    each chunk in `_collect_parts`, and `generate`'s single call when the
    recording is one chunk - so the two cannot drift into different rules.

    The message carries what the row would have (`reasoning_record`'s
    numbers, and the hint in words), because a refused part writes no row and
    this error is then the jobs board's only record of the call. Measured
    2026-09-11: openai/gpt-5-mini spent 5,632 of its 6,000 tokens reasoning,
    upstream Azure, after the hint was refused - and the board said only the
    cap and 'length', which cannot tell a reasoner from a runaway.
    """
    if response.raw_finish_reason != TRUNCATED_FINISH:
        return
    where = f"({render.format_ts(chunk.start)}-{render.format_ts(chunk.end)})"
    raise base.BadResponse(
        f"the model ran out of room on chunk {chunk.index} of the {plan.kind!r} task "
        f"{where}: it stopped at the {plan.max_output_tokens}-token cap "
        f"({_spent(response)}) rather than finishing, so the part "
        "is cut off and has not been stored. What it managed: " + clip(response.text or "")
    )


def _spent(response: ChatResponse) -> str:
    """What a call spent, in the words a refusal quotes.

    The fields `reasoning_record` would have written to the row, because the
    two refusals that use this - a cut-off part and a cut-off note - write no
    row, and their error is then the jobs board's only record of the call.
    """
    return (
        f"finish_reason={response.raw_finish_reason!r}, "
        f"completion_tokens={response.completion_tokens}, "
        f"reasoning_tokens={response.reasoning_tokens}, "
        f"reasoning_chars={response.reasoning_chars}, upstream={response.upstream!r}, "
        f"{base.hint_words(response.hint_sent)}"
    )


def run_task(
    conn: sqlite3.Connection,
    *,
    media_id: int,
    kind: str,
    provider_name: str,
    model: str | None = None,
    custom_prompt: str | None = None,
    on_progress: Callable[[float], None] | None = None,
    run_id: int | None = None,
    context_tokens: int | None = None,
    max_output_tokens: int | None = None,
    budget_tokens: int | None = None,
    **provider_kwargs: Any,
) -> int:
    """Plan, call, store, apply; returns the new `llm_output.id`.

    The steps are separate functions because the `llm` job runs them as
    stages, and one composed function is the only way the job and a direct
    caller cannot drift apart.

    The apply step is here for that reason and no other. It has no production
    callers - every real request goes through the job - so this function exists
    to be the same thing the job is. A version of it that stopped at `store`
    would quietly make every test that uses it a test of three quarters of the
    pipeline, and the quarter it skipped is the one that changes the library.
    """
    plan = plan_task(
        conn,
        media_id=media_id,
        kind=kind,
        provider_name=provider_name,
        model=model,
        custom_prompt=custom_prompt,
        run_id=run_id,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        budget_tokens=budget_tokens,
    )
    result = generate(conn, plan, on_progress=on_progress, **provider_kwargs)
    output_id = store_output(conn, plan, result)
    if plan.spec.apply is not None:
        plan.spec.apply(conn, plan, result.payload, output_id)
    return output_id


# --- the labels a recording carries ------------------------------------------------------

MAX_NEW_LABELS = 3
"""How many labels one pass may add once the vocabulary is established.

The prompt asks the model to reuse what exists; this is what makes it true.
A prompt rule is a request and a code rule is a guarantee, and the thing being
capped is exactly the thing that would be doing the self-reporting - so the
model is never asked whether a label is new. `apply_labels` decides that
against the vocabulary it read, and stops at the allowance.

Three because the failure it prevents is one-sided: a recording that gets one
label too few is found by its other labels and by full-text search, while a
vocabulary that grows a label per recording stops being a filter at all - fifty
episodes of one podcast would end in "hacking", "hackers", "hacker culture",
"hacker history" and nothing to click on."""

MAX_NEW_LABELS_COLD = 6
"""The allowance while the library is still learning its own words.

Measured on the first real run, 2026-09-10: an episode answered into an *empty*
library with six labels that were all good, and a flat cap of three dropped
"incident response" and "computer forensics" for want of room they were not
competing for. Nothing can be reused when there is nothing to reuse, so a cap
there does not prevent fragmentation - it only loses what the pass found. The
next episode, with three labels to work from, reused two and invented three,
and dropped nothing.

Six rather than unlimited because a cold library is exactly where a talkative
model would do the most damage: every label it invents becomes the vocabulary
the next recording is asked to reuse."""

VOCABULARY_ESTABLISHED = 20
"""Where "still learning" ends and "has words of its own" begins.

A judgement, not a measurement, and the reasoning is worth more than the
number: the risk the cap exists for is a subject arriving under four
spellings, and that risk needs something to fragment *against*. Two episodes
of one podcast produced six distinct labels here, so twenty leaves room for a
second and third source to establish their own words before the cap tightens.
Moving it is one constant and breaks nothing."""


def new_label_allowance(known: int) -> int:
    """How many new labels a pass may add, given how many the library has."""
    return MAX_NEW_LABELS if known >= VOCABULARY_ESTABLISHED else MAX_NEW_LABELS_COLD

MAX_LABEL_LENGTH = 40
"""A label is a subject, not a sentence. Longer than this is the model
answering the wrong question, and a sidebar cannot show it anyway."""


def vocabulary(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every label the library already uses, most-used first.

    Ordered by use because it is handed to a model with a budget: when the list
    ever grows past what a prompt should carry, the labels that earn their place
    are the ones that already describe the most recordings.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT l.name AS name, COUNT(ml.media_id) AS uses"
            " FROM label l LEFT JOIN media_label ml ON ml.label_id = l.id"
            " GROUP BY l.id, l.name ORDER BY uses DESC, l.name"
        ).fetchall()
    return tuple(str(row["name"]) for row in rows)


def _clean_label(raw: Any) -> str:
    """One label as it should be stored, or "" when it is not one at all."""
    text = " ".join(str(raw or "").split())
    text = text.strip().strip(".,;:!?").strip()
    return text[:MAX_LABEL_LENGTH].strip()


def apply_labels(
    conn: sqlite3.Connection,
    media_id: int,
    payload: Any,
    *,
    now: float | None = None,
) -> dict[str, list[str]]:
    """Write a labels answer onto a recording, and say what it did.

    Reuse is free and invention is capped: a label that matches one the library
    already holds - case-insensitively, because "Hacking" and "hacking" are the
    same subject - lands on the existing row however many there are, while a
    label nobody has used before is taken only while under `MAX_NEW_LABELS`.

    Nothing here overwrites: the link is written `INSERT OR IGNORE`, so a pair
    that exists already keeps the `source` it has. That is the whole mechanism
    protecting a label a person typed from a later automatic run - the row a
    human made simply survives, and this function never needs to know which
    rows those are.

    Returns the three lists a caller wants to report: what was reused, what was
    created, and what was dropped for want of room.
    """
    stamp = time.time() if now is None else now
    known = {name.casefold(): name for name in vocabulary(conn)}
    allowance = new_label_allowance(len(known))

    reused: list[str] = []
    created: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()

    guesses = getattr(payload, "labels", None) or []
    for guess in guesses:
        name = _clean_label(getattr(guess, "label", None) or (guess or {}).get("label"))
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        if name.casefold() in known:
            reused.append(known[name.casefold()])
        elif len(created) < allowance:
            created.append(name)
        else:
            dropped.append(name)

    with db.LOCK:
        for name in created:
            conn.execute(
                "INSERT OR IGNORE INTO label(name, created_at) VALUES (?, ?)", (name, stamp)
            )
        for name in reused + created:
            conn.execute(
                "INSERT OR IGNORE INTO media_label(media_id, label_id, source, created_at)"
                " SELECT ?, id, 'llm', ? FROM label WHERE name = ? COLLATE NOCASE",
                (media_id, stamp, name),
            )
        conn.commit()

    return {"reused": reused, "created": created, "dropped": dropped}


def _apply_labels(
    conn: sqlite3.Connection, plan: "TaskPlan", payload: Any, output_id: int
) -> dict:
    """The `labels` kind's apply hook. `output_id` is unused here: which
    analysis chose a label is not recorded on the link today, unlike a speaker
    name, because a label is a word several passes may reach independently
    while a name is one decision about one person."""
    return apply_labels(conn, plan.media_id, payload)


def labels_for(conn: sqlite3.Connection, media_id: int) -> list[dict]:
    """The labels on one recording, with who decided each."""
    with db.LOCK:
        rows = conn.execute(
            "SELECT l.name AS name, ml.source AS source FROM media_label ml"
            " JOIN label l ON l.id = ml.label_id WHERE ml.media_id = ?"
            " ORDER BY l.name",
            (media_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# The apply hooks, attached here rather than in the TASKS literal because the
# functions they name are defined below it. `dataclasses.replace` keeps the
# spec frozen: the entry is swapped for a new one, never mutated.
TASKS["labels"] = replace(TASKS["labels"], apply=_apply_labels)


# --- naming the speakers ------------------------------------------------------------------


# The role words prompts/speakers.md offers for `name` when the transcript
# gives no name, and the placeholders a model reaches for instead; with an
# article or a number they are still a role ("The host", "Guest 2").
ROLE_WORDS = frozenset({
    "host", "cohost", "guest", "expert", "other", "speaker", "unknown",
    "interviewer", "interviewee", "moderator", "presenter", "narrator",
    "the", "a", "an",
})


def is_role_word(name: str) -> bool:
    """True when `name` says what somebody is, not who: every word of it, case,
    hyphens and numbers aside, is in ROLE_WORDS."""
    words = re.sub(r"[^a-z\s]", "", name.casefold().replace("-", "")).split()
    return bool(words) and all(word in ROLE_WORDS for word in words)


def apply_speakers(
    conn: sqlite3.Connection, plan: "TaskPlan", payload: Any, output_id: int
) -> dict:
    """Write the confident names onto the run the analysis was made from.

    Three rules, and each of them is the answer to a way this could go wrong.

    **Only above the threshold.** A cluster the model was not sure about keeps
    its "Speaker 2" and stays a suggestion the panel offers. The bar is
    exclusive: 90 does not clear 90, because a number chosen as the bar should
    not also be the first value that passes it.

    **Never over a person.** A row whose source is 'human' is left exactly as
    it is, however sure the model claims to be. This is the rule that makes
    running the pass unattended safe: the worst it can do to a name somebody
    typed is nothing. The write itself says so too, so a rename the web
    process commits after the human rows were read still stands.

    **A role is not a name.** "Guest" replaces "Speaker 2" with something no
    more informative and harder to spot as a default, so a guess with no actual
    name is skipped whatever its confidence - an empty name, or a role word in
    the name field, which is where the prompt asks for one (`is_role_word`).
    Since a re-transcription asks again (TASK-037), this is also what keeps
    "Host" off a cluster that inherited "Arthur".

    Every row written records `llm_output_id` and the confidence, so a name can
    be traced back to the analysis that chose it and the quote that analysis
    rested on. That trace is the real safeguard here - a model's confidence is
    a claim about itself, not a probability.
    """
    run_id = plan.run_id
    guesses = getattr(payload, "speakers", None) or []

    with db.LOCK:
        human = {
            str(row["cluster_label"])
            for row in conn.execute(
                "SELECT cluster_label FROM speaker_label WHERE run_id=? AND source='human'",
                (run_id,),
            )
        }

        # TASK-104.01: only clusters the run's words carry. A confident answer
        # about SPEAKER_07 used to become a label, and a label alone is enough
        # for the transcript page to list a speaker - one with no words.
        clusters = {
            str(row["speaker"])
            for row in conn.execute(
                "SELECT DISTINCT speaker FROM word WHERE run_id=? AND speaker IS NOT NULL", (run_id,)
            )
        }
        said = _what_the_recording_says(conn, run_id)

        named: list[str] = []
        left: list[str] = []
        for guess in guesses:
            cluster = str(getattr(guess, "cluster", "") or "").strip()
            name = " ".join(str(getattr(guess, "name", "") or "").split())
            confidence = float(getattr(guess, "confidence", 0.0) or 0.0)
            if not cluster or cluster not in clusters:
                continue
            if cluster in human:
                left.append(cluster)
                continue
            if not name or is_role_word(name) or confidence <= SPEAKER_CONFIDENCE_THRESHOLD:
                left.append(cluster)
                continue
            if not name_is_supported(name, said):
                # TASK-104.02: sure, by its own account, of a name the
                # recording never mentions. A suggestion, not a write.
                left.append(cluster)
                continue
            written = conn.execute(
                "INSERT INTO speaker_label(run_id, cluster_label, display_name, source,"
                " llm_output_id, confidence) VALUES (?, ?, ?, 'llm', ?, ?)"
                " ON CONFLICT(run_id, cluster_label) DO UPDATE SET"
                " display_name=excluded.display_name, source='llm',"
                " llm_output_id=excluded.llm_output_id, confidence=excluded.confidence"
                " WHERE speaker_label.source <> 'human'",
                (run_id, cluster, name[:library_max_name()], output_id, confidence),
            ).rowcount
            (named if written else left).append(cluster)
        conn.commit()

    _clear_speaker_pass_note(conn, run_id)
    return {"named": named, "left": left, "threshold": SPEAKER_CONFIDENCE_THRESHOLD}


def _what_the_recording_says(conn: sqlite3.Connection, run_id: int) -> set[str]:
    """Every word the recording offers as evidence for a name, case-folded:
    the transcript's words, its title and its file name (TASK-104.02). Read
    under the caller's lock."""
    words = set()
    for row in conn.execute("SELECT text FROM word WHERE run_id=?", (run_id,)):
        words.update(_name_words(row["text"]))
    media = conn.execute(
        "SELECT m.title, m.orig_name FROM run r JOIN media m ON m.id = r.media_id WHERE r.id=?",
        (run_id,),
    ).fetchone()
    if media is not None:
        words.update(_name_words(media["title"] or ""))
        words.update(_name_words(Path(media["orig_name"] or "").stem))
    return words


def _name_words(text: str) -> list[str]:
    return [w.casefold() for w in re.findall(r"[^\W\d_]{2,}", text or "")]


def name_is_supported(name: str, said: set[str]) -> bool:
    """Whether at least one word of ``name`` occurs in what the recording says.

    TASK-104.02. A model's confidence is a claim about itself; a name said
    aloud ("Marvin says ...") or given by the title ("Interview with Arthur
    Dent") is something the recording contains. One word is enough, because
    people are introduced by their first name and titled by their full one.
    """
    return any(word in said for word in _name_words(name))


def _clear_speaker_pass_note(conn: sqlite3.Connection, run_id: int) -> None:
    """Take off the note that says nobody could be asked who these people are.

    Imported lazily and through a function, the way `library_max_name` is and
    for the same reason: `scribe.stages.finalize` is a stage, and a top-level
    import here would be a cycle for the sake of one call.

    Unconditional, and that is the whole of it. The note is cleared where the
    answer lands rather than only where a pass is queued, because the panel is
    what its own sentence tells the reader to press - and the startup sweep
    skips any recording that has a `speakers` answer, so after this nothing
    ever visits that run again. Nor does it wait for a confident name: what
    shuts the sweep out is the `llm_output` row, so a vague answer would leave
    "Ollama is not running" standing beside the clusters for good. That is the
    TASK-089.08 defect, and this is the path the note recommends
    (TASK-089.10).
    """
    from scribe.stages import finalize

    finalize.clear_speaker_pass_note(conn, run_id)


def library_max_name() -> int:
    """The cap a display name shares with every other name in this app.

    Imported lazily and through a function: `scribe.web.library` imports this
    module's siblings, and a top-level import here would be a cycle for the
    sake of one integer.
    """
    from scribe.web import library

    return int(library.MAX_NAME)


TASKS["speakers"] = replace(TASKS["speakers"], apply=apply_speakers)


# --- the cleaning gate --------------------------------------------------------------------

CLEAN_MIN_RATIO = 0.55
"""How short a cleaned reading may be, as a share of the words that went in.

Robert's rule: a clean version stays as close to the original as it can and
must not collapse in length; when it does, the cleaning is undone.

This number is ARGUED, not measured, and the honest reason is written here
rather than implied. The plan was to measure it - run cleanup over real
episodes, take the ratio of answers a person judged good, put the floor under
the worst of them. That measurement could not be taken on 2026-09-10: nothing
produced an honest cleaning to measure. openrouter/auto died twice on
finish_reason='length'; ollama qwen3.5:9b twice spent its entire answer budget,
21-24k characters for inputs of 837 and 168 words. Clean transcripts, two
providers, three models.

So the number comes from what the two failures cost instead. Honest cleaning
deletes real words - filler, repetition, false starts, restarts - and on spoken
English that is commonly a fifth of them and can be more in a rambling stretch.
A summary of the same material is a different order of magnitude: a tenth, a
twentieth. 0.55 sits in the gap with room on both sides. It forgives a cleaner
that removed nearly half of what was said, and refuses anything that kept less
than half, which no cleaning does and every summary does.

Replace it the day there is a measurement, and cite the run here."""

CLEAN_MAX_RATIO = 1.15
"""How long a cleaned reading may be, for the same reason in the other
direction: a rewrite that comes back longer than what went in did not clean, it
invented. Punctuation and a spelled-out contraction cost a few words; a
sixth more is already somebody writing rather than tidying.

Not hypothetical. The runaway measured here failed on this side - a
recording of 168 words came back as 5742, because its transcript was a
112-word "La, la" loop and the model carried the loop on."""


# --- TASK-088: a copied part that skipped the grouping ------------------------

_SOURCE_LINE = re.compile(r"^\[(?:\d+:)?\d{1,2}:\d{2}\]\s+(?:(SPEAKER_\d+):\s)?")
"""A line as `chunking.timestamped_line` writes it: `[m:ss]` or `[h:mm:ss]`,
then pyannote's cluster label when the run was diarized. The label is matched
by its shape, so an undiarized line whose text starts "Note:" has none."""


def continuing_lines(source: str) -> tuple[int, int]:
    """How many of `source`'s stamped lines continue the speaker before them,
    and how many stamped lines there are.

    A line continues when it carries the same label as the stamped line just
    before it. An unlabelled line never does: an undiarized run does not say
    who spoke, so it does not say the speaker stayed either. Those are the
    lines `cleanup.md` asks to fold into the stretch before them, keeping only
    the stamp and label that start it.
    """
    labels = [
        match.group(1)
        for match in (_SOURCE_LINE.match(line) for line in source.splitlines())
        if match
    ]
    continuing = sum(
        1 for before, label in zip(labels, labels[1:]) if label is not None and label == before
    )
    return continuing, len(labels)


def check_cleaning(source: Sequence[str], cleaned: Sequence[str]) -> dict:
    """Does this cleaned reading still say what the transcript said?

    Word counts, per chunk and overall. Per chunk matters because a concat task
    joins its parts: one chunk that collapsed into a summary hides inside an
    otherwise healthy total, and the overall ratio would pass while a page of
    the transcript had quietly become a paragraph.

    Returns a verdict with its numbers, so a refusal can say which chunk and by
    how much rather than only that it happened.

    **And whether anything was cleaned at all.** Each part records
    `unchanged`: it came back as it went in, whitespace aside. A word count
    cannot tell a copy from a cleaning, because a copy sits in the middle of
    the band. A reading in which every part came back unchanged is refused:
    nothing was cleaned, and it would publish the transcript under the name
    of its cleaning.

    What the comparison sees. Whitespace is the only difference ignored, so a
    copy that only re-spaced its lines - a blank line between them - is still
    a copy. The `[m:ss]` stamps and `SPEAKER_xx:` labels count as words, and
    `cleanup.md` does not ask for whitespace: its paragraphs keep only the
    stamp and label that start each stretch, so a regrouped answer drops the
    ones inside it. The rule therefore catches a copy that kept every line's
    stamp and label, and no other: a copy regrouped the way the prompt asks,
    its words untouched, passes - a known gap. Punctuation is not ignored:
    fixing it is the job, so a comparison blind to it would call honest work
    a copy.

    It can refuse an honest answer, in one narrow case: every line starts its
    own stretch (a one-segment recording, or speakers alternating line by
    line) and the text needs no fixing, so what `cleanup.md` asks for is the
    input with blank lines added. That reading is stored and not published,
    and the recording reads as it did before - its transcript, or an earlier
    cleaning of the same run, since `apply_cleanup` writes `clean_reading`
    only on a pass and a refusal leaves the older row in place.

    **And one copied part, when it had grouping to do** (TASK-088, ADR-010
    as decided on 2026-09-19). A part that came back unchanged is refused on
    its own when its source has a line continuing the speaker before it
    (`continuing_lines`): `cleanup.md` asks to fold such a line into the
    stretch before it, and a copy did not. The rule keys on that line, not on
    sameness: a copied part of one line, of alternating speakers or of
    unlabelled lines had no such line to fold, and passes. A rerun can repair
    the refusal, because `stored_chunk` does not reuse a refused reading's
    parts (`refused_part_ids`).

    The whole-reading rule came first and guards a total copy; the live check
    of TASK-029 produced none. The copy it did produce is the shape the part
    rule was built from. Measured 2026-09-11 on a copy of
    the library: qwen3.5:4b with think:false answered media 12's part 0 with
    its input's counts, twice - 963 words in and out, 45 of 45 stamps, and
    5,486 characters against the chunk's 5,442, one more for each of its 44
    line breaks. The answers were not kept, so "a copy with blank lines
    added" is inferred from those counts, not compared. The other ten parts
    changed words (ratios 0.79-0.999), so not every part was a copy and the
    reading passed, at 0.894 and 0.902, under the whole-reading rule alone.
    That part had work to do: 43 of its 45 lines continue the speaker before
    them, and `cleanup.md` keeps only the stamp that starts each stretch. The
    part rule refuses that reading.
    """
    # A part is compared with its own part only. When the counts differ the
    # mapping is not what the caller thinks it is, so no part is paired at
    # all: a ratio, a copy check or a per-part reason over the wrong pairs
    # would be a number about nothing. The totals count every part on both
    # sides either way - they are what the refusal is stored with and what
    # the page quotes, and zip() used to keep only the common prefix
    # (TASK-080).
    aligned = len(source) == len(cleaned)
    per_chunk = [
        {
            "index": i,
            "words_in": len(a.split()),
            "words_out": len(b.split()),
            "ratio": len(b.split()) / len(a.split()) if a.split() else 0.0,
            "unchanged": a.split() == b.split(),
        }
        for i, (a, b) in enumerate(zip(source, cleaned))
    ] if aligned else []
    total_in = sum(len(a.split()) for a in source)
    total_out = sum(len(b.split()) for b in cleaned)
    overall = total_out / total_in if total_in else 0.0

    reasons: list[str] = []
    if not aligned:
        reasons.append(
            f"the cleaning came back in {len(cleaned)} part(s) where the transcript "
            f"was cut into {len(source)}"
        )
    if overall < CLEAN_MIN_RATIO:
        reasons.append(f"the whole reading kept {overall:.0%} of the words, under {CLEAN_MIN_RATIO:.0%}")
    if overall > CLEAN_MAX_RATIO:
        reasons.append(f"the whole reading is {overall:.0%} of the words, over {CLEAN_MAX_RATIO:.0%}")
    # `per_chunk and`: all() of nothing is True, and no parts is not a copy.
    if per_chunk and all(part["unchanged"] for part in per_chunk):
        reasons.append(
            f"every part came back as it went in ({len(per_chunk)} of {len(per_chunk)}, "
            "whitespace aside): nothing was cleaned"
        )
    for part in per_chunk:
        # TASK-088: one copied part refuses the reading when its source had
        # lines to fold into the stretch before them.
        if part["unchanged"]:
            continuing, lines = continuing_lines(source[part["index"]])
            if continuing:
                reasons.append(
                    f"part {part['index']} came back as it went in, though {continuing} of its "
                    f"{lines} lines continue the speaker before them: the grouping cleanup "
                    "asks for was skipped"
                )
        if part["ratio"] < CLEAN_MIN_RATIO:
            reasons.append(
                f"part {part['index']} kept {part['ratio']:.0%} of its words "
                f"({part['words_in']} -> {part['words_out']})"
            )
        elif part["ratio"] > CLEAN_MAX_RATIO:
            reasons.append(
                f"part {part['index']} is {part['ratio']:.0%} of its words "
                f"({part['words_in']} -> {part['words_out']})"
            )

    return {
        "ok": not reasons,
        "overall": overall,
        "words_in": total_in,
        "words_out": total_out,
        "chunks": per_chunk,
        "reasons": reasons,
    }


def cleaned_parts(
    conn: sqlite3.Connection, plan: "TaskPlan", payload: Any, output_id: int
) -> list[str]:
    """The cleaning, in the same pieces the transcript was cut into.

    A one-call cleaning is one part and it is the answer itself. A chunked one
    was stored piece by piece as it was made - that is what makes the job
    resumable - so the pieces are read back rather than recovered by splitting
    the joined text, which would come apart on any part that contains a blank
    line.

    **Which pieces: the ones the final row names** (`chunk_output_ids`, in
    chunk order), never the newest row per index. One run can hold parts from
    several providers, models and prompt versions, and `stored_chunk` reuses a
    part only under its own provider, model and segments - so the newest part
    at an index can belong to somebody else. Measured 2026-09-11 on a copy of
    the library: media 12 cleaned on the cloud (7 parts), then locally (11),
    then on the cloud again; the last run reused its own 7 parts, the gate
    read the newer local ones and refused a good cleaning ('part 1 kept 48%').
    Where the other model's parts pass the gate, that read publishes them
    under this row's id instead: the rerun test in
    `tests/test_llm_cleaning_gate.py` is red against it.
    Read by position in the list, not by row id: a resumed run can name an
    old part after a new one. A missing or unreadable list yields no parts,
    and the gate refuses the count - the safe direction.
    """
    if len(plan.chunks) <= 1:
        return [str(payload)]
    with db.LOCK:
        final = conn.execute(
            "SELECT params_json FROM llm_output WHERE id=?", (output_id,)
        ).fetchone()
        try:
            ids = json.loads(final["params_json"] or "{}").get("chunk_output_ids") or []
        except (TypeError, ValueError, AttributeError):
            ids = []
        rows = conn.execute(
            f"SELECT id, content FROM llm_output WHERE media_id=? AND run_id=?"
            f" AND id IN ({','.join('?' * len(ids))})",
            (plan.media_id, plan.run_id, *ids),
        ).fetchall() if ids else []
    by_id = {int(row["id"]): str(row["content"]) for row in rows}
    return [by_id[i] for i in ids if i in by_id]


def apply_cleanup(
    conn: sqlite3.Connection, plan: "TaskPlan", payload: Any, output_id: int
) -> dict:
    """Publish the cleaned reading, or refuse it and publish nothing.

    Robert's rule, and the whole reason the reading is derived rather than a
    replacement: when a cleaning collapses in length, or pads itself out, the
    cleaning is undone. Undoing costs nothing here because nothing was replaced
    - the words are untouched either way, and refusing means this row is not
    written. A recording whose cleaning was refused reads exactly as it did
    before, and says so.

    The verdict is returned whatever it decides, and the job records it, so a
    refusal can be read afterwards with the numbers it rested on. The refused
    answer itself is already stored: `store_output` ran before this stage, and
    that is deliberate - "this cleaning was refused for shrinking part three to
    22 percent" is only checkable while the thing that was refused survives.
    """
    parts = cleaned_parts(conn, plan, payload, output_id)
    verdict = check_cleaning([chunk.text for chunk in plan.chunks], parts)
    _keep_verdict(conn, output_id, {
        "published": bool(verdict["ok"]),
        **{key: verdict[key] for key in ("words_in", "words_out", "reasons") if key in verdict},
    })

    if not verdict["ok"]:
        return {"published": False, **verdict}

    with db.LOCK:
        conn.execute(
            "INSERT INTO clean_reading(run_id, text, llm_output_id, words_in, words_out,"
            " created_at, words_hash) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(run_id) DO UPDATE SET text=excluded.text,"
            " llm_output_id=excluded.llm_output_id, words_in=excluded.words_in,"
            " words_out=excluded.words_out, created_at=excluded.created_at,"
            " words_hash=excluded.words_hash",
            (
                plan.run_id,
                "\n\n".join(parts),
                output_id,
                verdict["words_in"],
                verdict["words_out"],
                time.time(),
                plan.words_hash or None,
            ),
        )
        conn.commit()
    return {"published": True, **verdict}


def _keep_verdict(conn: sqlite3.Connection, output_id: int, gate: dict) -> None:
    """Write the gate's verdict into the answer row's `params_json` as `gate`
    (TASK-055), next to what `store_output` put there. The transcript page
    says why a cleaning is not shown from this, because the job's events are
    pruned and an answer made by `run_task` has no job at all. An id without
    a row - a caller checking a verdict by hand - writes nothing."""
    with db.LOCK:
        row = conn.execute("SELECT params_json FROM llm_output WHERE id=?", (output_id,)).fetchone()
        if row is None:
            return
        try:
            params = json.loads(row["params_json"] or "{}")
        except (TypeError, ValueError):
            params = {}
        params["gate"] = gate
        conn.execute("UPDATE llm_output SET params_json=? WHERE id=?", (json.dumps(params), output_id))
        conn.commit()


def words_fingerprint(words: Sequence[Any]) -> str:
    """What a reading was made from, as one hash: the text of every word in
    order, through the correction layer when the words came from `docs.load`.

    The separator cannot occur inside a word, a folded word's empty text still
    holds its place, and whitespace inside a word counts because it is the
    word's - so the same words give the same print and any edit to any word
    gives a different one (TASK-071).
    """
    joined = "\x1f".join(str(word["text"] or "") for word in words)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def run_words_fingerprint(conn: sqlite3.Connection, run_id: int) -> str:
    """The run's words as they read now: the same join the transcript view and
    the exports use, so a glossary pass and a retype both move it and a
    rename does not."""
    with db.LOCK:
        rows = conn.execute(
            f"SELECT {glossary.CORRECTED_TEXT} AS text FROM word w {glossary.CORRECTION_JOIN}"
            " WHERE w.run_id=? ORDER BY w.idx",
            (run_id,),
        ).fetchall()
    return words_fingerprint(rows)


def clean_reading(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """The cleaned reading for a run, when one was published, with `stale`:
    the words it was made from are no longer the words the run reads.

    Keyed by run, the row cannot know that the words under it moved; the
    fingerprint it carries can (TASK-071). It is not recomputed and not
    dropped - a reading is a paid answer with a receipt, not a cache - so the
    page shows it and says so. A reading without a fingerprint was made before
    the column existed, and not knowing what it read is not evidence that the
    words moved: never stale.
    """
    with db.LOCK:
        row = conn.execute(
            "SELECT * FROM clean_reading WHERE run_id=?", (run_id,)
        ).fetchone()
    if row is None:
        return None
    reading = dict(row)
    made_from = reading.get("words_hash")
    reading["stale"] = bool(made_from) and made_from != run_words_fingerprint(conn, run_id)
    return reading


def cleanup_status(conn: sqlite3.Connection, media_id: int, run_id: int | None) -> dict | None:
    """The newest finished cleanup answer about this run, and what the gate
    said about it: ``{output_id, created_at, provider, model, gate}``.

    ``gate`` is None for an answer made before verdicts were kept - the two in
    the library were written on 2026-09-10, before the gate existed - which
    was never checked, not refused. A part (``cleanup:chunk:<i>``) is not an
    answer, and an answer whose transcript is gone (run_id null) is not about
    the words on the page; neither counts. The gate is never recomputed here:
    chunk boundaries have moved since, and an answer judged against parts it
    was not made from would be judged wrongly.
    """
    if run_id is None:
        return None
    with db.LOCK:
        row = conn.execute(
            "SELECT id, created_at, provider, model, params_json FROM llm_output"
            " WHERE media_id=? AND run_id=? AND kind='cleanup' ORDER BY id DESC LIMIT 1",
            (media_id, run_id),
        ).fetchone()
    if row is None:
        return None
    try:
        gate = json.loads(row["params_json"] or "{}").get("gate")
    except (TypeError, ValueError, AttributeError):
        gate = None
    return {
        "output_id": row["id"],
        "created_at": row["created_at"],
        "provider": row["provider"],
        "model": row["model"],
        "gate": gate if isinstance(gate, dict) else None,
    }


TASKS["cleanup"] = replace(TASKS["cleanup"], apply=apply_cleanup)
