"""Real-endpoint smoke tests (Phase 5 Task 7).

Every other `test_llm_*` module runs against a fake transport, which is the
Global Constraint working as intended: the unit suite must not depend on
somebody's API being up, or on a card being free. What that buys in speed it
gives up in coverage of exactly the things a fake cannot have an opinion about -
whether the key on this machine resolves, whether the shipped default model id
exists on this account, whether a local daemon is running, and what the endpoint
does with a request nobody has sent it before. This module is the one place that
asks, and it is `gpu`-marked at module level so the default run never does.

**A skip here is a result; a silent pass is not.** Every skip names the provider
and the reason `available()` gave, so "openai skipped" is never allowed to mean
"openai quietly did nothing".

Three things are checked, and they are different questions:

* **Each configured provider can summarise a transcript.** Parametrised over
  `llm.PROVIDERS` rather than over a list of three names, because that registry
  *is* the set of providers (Global Constraints), so a provider added later is
  smoke-tested the day it is registered and not the day somebody remembers.
  The call goes through `tasks.run_task`, which is the composed front door: the
  privacy pin, the chunker, the prompt templates, the retry policy, the schema
  repair and the `llm_output` row are all in the path, so a green test means
  the shipped path works against the shipped default model.
* **Ollama gives the window it was asked for.** The provider's whole claim is
  that `num_ctx` is a promise rather than a suggestion, and only the live daemon
  can say whether it is. Both halves are here: a prompt over the default window
  read whole when the window is raised to hold it, and a prompt that does not
  fit refused - at a window below Ollama's allocation floor *and* at the 8192
  the app actually ships with, which is the case that matters and the one that
  did not work until `ollama._refuse_a_prompt_that_cannot_fit` was written.
* **A budget too small for a thinking model fails with the budget named.**
  The shipped local default deliberates for thousands of tokens before it
  writes a word, so an under-sized `max_output_tokens` produces an empty
  answer rather than a short one. That is a fact about this machine's model
  that no fake can hold an opinion about, and the error a user meets has to
  point at the number that fixes it.

Timings land in `stage_perf` under stage `llm` - the plan's ETA point. Note what
that is *not*: the runner already files a row per stage name (`prepare`,
`generate`, `store`) for a real job. This is the whole task's wall time under
the job type, recorded here because a live run is the only place a number for a
real model on this machine can come from at all.

**Expect a stack dump in a slow run, and do not read it as a crash.**
`pytest.ini` sets `faulthandler_timeout = 120` so that the Windows stall
documented there says where it stopped; a local summary here is legitimately
allowed to take longer than that (measured: 124.5 s when `qwen3.5:4b` spent
6021 completion tokens on two sentences). The dump is printed and the run
carries on - the summary line at the end is what says whether anything failed.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from scribe import db, env, jobs, llm
from scribe.llm import base, chunking, ollama, tasks
from tests.seed import seed_media, seed_run

pytestmark = pytest.mark.gpu
"""The whole module, so nothing in it can be picked up by a default run - and,
just as importantly, so nothing in it is silently *deselected* from the `-m gpu`
run this task is reported from."""


@pytest.fixture(autouse=True)
def dotenv():
    """`.env`, the way the app reads it - and only for the tests in this module.

    `scribe/__main__.py` calls this before anything touches the environment;
    pytest does not, so without it `OPENAI_API_KEY` - which lives in `.env` on
    this machine - never resolves and the openai case skips for a reason that is
    an artefact of the test runner rather than a fact about the machine. It fills
    only what the process has no value for, so a variable that is really set
    still wins.

    The snapshot is not ceremony. `load_dotenv` mutates the process
    environment with no way back, and this module is `gpu`-marked rather than
    quarantined: nothing stops someone running it in the same session as the
    rest of the suite, where leaked `.env` values would then be visible to
    every test that follows and to none of the tests that ran before - the
    kind of ordering-dependent difference that costs an afternoon to find.
    """
    before = dict(os.environ)
    try:
        env.load_dotenv()
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


@pytest.fixture
def conn(tmp_path):
    """A tmp database. Nothing here touches the real library."""
    c = db.connect(tmp_path / "live.db")
    db.migrate(c)
    yield c
    c.close()


# --- a two-sentence transcript -------------------------------------------------------------

SENTENCES = (
    "Don't panic, the towel is still the most important item.",
    "The answer to life, the universe and everything is forty-two.",
)
"""Two sentences, as the plan asks. Short enough that a cloud call costs a
fraction of a cent and a local 4B model answers in under a minute.

They are recognisable rather than generic on purpose - in practice every
provider's answer has mentioned the towel and forty-two - but nothing below
asserts that, and the docstring should not imply otherwise. Asserting a model
says a particular word is a flaky test dressed as a strict one; the assertion
that is worth having is that a *schema-valid, non-empty* summary came back."""

LIVE_MAX_OUTPUT_TOKENS = 7000
"""The answer budget these smoke tests ask for, and the one place they depart
from the shipped defaults. Stated plainly, because it is a workaround: **the
shipped default is under-sized for this machine's local model**, and the test
above would flip a coin at that value.

`tasks.DEFAULT_MAX_OUTPUT_TOKENS` is 4000, chosen against the four-sentence seed
transcript where `qwen3.5:4b` deliberated for 2674 tokens before writing a word.
Summarising the two sentences above through the same path on 2026-09-03, four
runs at that default: 2473 ok, 3729 ok, **empty answer** (`done_reason:
"length"`), **empty answer**. So 4000 is not a floor for this model, it is the
middle of its distribution - and a shorter transcript does not make it
deliberate less. At 7000 the same four runs completed in 2088, 2614, 2827 and
3427 tokens: twice the headroom over the worst observed answer, and still inside
the shipped 8192-token window once the ~360-token prompt is accounted for, so
nothing here depends on a larger `num_ctx` than the app ships with. A cap costs
a cloud provider nothing - both bill what was generated, not what was allowed -
so it is applied uniformly rather than only to the local one, and no test here
branches on a provider name.

**Why this is not held open as a strict xfail, where the `num_ctx` defect was.**
That one is deterministic: at 8192 an overflowing prompt was truncated every
time, so a test could demand the right outcome and go red until it arrived.
This one is a coin flip - roughly half the runs at 4000 succeed - and
`xfail(strict=True)` on a coin flip is a build that fails at random, which
teaches a reader to ignore it. Nor is raising the shipped default available as
a fix: `tasks.budget_for` subtracts `max_output_tokens` from the context window,
and `LOCAL_CONTEXT_TOKENS` is Ollama's 8192, so a 7000-token default would leave
about a thousand tokens for the transcript itself and break every local job.
The real fix is a bigger local window or a model that thinks less, and neither
is a Task 7 change.

What *is* deterministic in this defect is pinned instead, one test below:
a budget too small to reach an answer fails with the budget named. Measured
4 runs out of 4 at `num_predict: 200`."""


def seed_two_sentences(conn) -> int:
    """A media whose current run is exactly `SENTENCES`; returns the media id."""
    words: list[dict] = []
    segments: list[dict] = []
    t = 0.0
    for sentence in SENTENCES:
        first = t
        for token in sentence.split(" "):
            words.append(
                {
                    "idx": len(words),
                    "start": t,
                    "end": t + 0.4,
                    "text": " " + token,
                    "probability": 0.95,
                    "speaker": "SPEAKER_00",
                }
            )
            t += 0.5
        segments.append(
            {"idx": len(segments), "start": first, "end": t - 0.1, "text": sentence}
        )

    media_id = seed_media(conn, title="Guide", duration=round(t, 1))
    seed_run(conn, media_id, words=words, segments=segments)
    return media_id


# --- one provider, one summary ---------------------------------------------------------------


@pytest.mark.parametrize("provider_name", sorted(llm.PROVIDERS))
def test_a_configured_provider_summarises_a_two_sentence_transcript(conn, provider_name):
    """The shipped path against the shipped default model, or a named skip.

    `model=None` on purpose: the provider's own `default_model` is what ships,
    so this is the test that would have caught `anthropic/claude-3.5-haiku` -
    the id that reads as real and 404s - had it ever been made a default.

    One thing here is *not* as shipped, and it is not hidden: the answer budget
    is `LIVE_MAX_OUTPUT_TOKENS`, not `tasks.DEFAULT_MAX_OUTPUT_TOKENS`. Read
    that constant - the shipped value fails about half the time on this
    machine's local model, which is a defect of its own with its own test
    below, and letting it flip a coin here would only make this test unable to
    report on anything else.

    `available()` is asked first because its answer is the skip reason. For a
    cloud provider it is "is there a key" and costs nothing; for Ollama it is a
    2 s loopback probe that distinguishes a stopped daemon from an unpulled
    model. Either way the skip line says which of those it was.
    """
    provider = llm.get_provider(provider_name, conn)
    ok, why = provider.available()
    if not ok:
        pytest.skip(f"{provider_name}: {why}")

    media_id = seed_two_sentences(conn)
    duration = conn.execute(
        "SELECT duration FROM media WHERE id=?", (media_id,)
    ).fetchone()["duration"]

    started = time.perf_counter()
    output_id = tasks.run_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name=provider_name,
        # No model: the shipped default is the thing under test.
        max_output_tokens=LIVE_MAX_OUTPUT_TOKENS,  # not shipped - see the constant
    )
    wall = time.perf_counter() - started

    row = dict(conn.execute("SELECT * FROM llm_output WHERE id=?", (output_id,)).fetchone())
    assert row["provider"] == provider_name
    assert row["model"] == provider.default_model
    assert row["prompt_version"] == tasks.PROMPT_VERSION
    assert row["kind"] == "summary"

    # Parsed as its own schema, not merely non-empty: `store_output` writes the
    # validated object, so a row that will not re-validate means the guarantee
    # every later reader relies on is not true.
    summary = tasks.Summary.model_validate(json.loads(row["content"]))
    assert summary.paragraph.strip(), f"{provider_name} returned an empty summary paragraph"

    # The plan's ETA point. Two assertions rather than one, because a
    # record-then-read-back pair that shares both literals proves only that
    # `stage_perf` is a table: it would pass just as happily under the stage
    # name "banana". The second reads a *different* stage and requires nothing
    # there, which is what makes the string "llm" load-bearing - the timing has
    # to be filed under the stage the ETA machinery will later ask for.
    #
    # What this is not: the row lands in the tmp_path database and dies with
    # it. Writing live timings into the user's real library from a test would
    # be the defect, so the ETA machinery does not in fact learn anything here
    # - what is verified is that the call the runner makes is the call that
    # would teach it.
    jobs.record_stage_perf(conn, "llm", provider.default_model, float(duration), wall)
    assert jobs.eta_seconds(conn, "llm", provider.default_model, float(duration)) is not None
    assert jobs.eta_seconds(conn, "transcribe", provider.default_model, float(duration)) is None

    print(
        f"\n[live] {provider_name} / {provider.default_model}: {wall:.1f}s, "
        f"{row['prompt_tokens']} prompt + {row['completion_tokens']} completion tokens\n"
        f"       {summary.paragraph.strip()[:200]}"
    )


# --- the window Ollama was asked for ------------------------------------------------------------


PARAGRAPH = (
    "Don't panic, the towel is still the most important item. "
    "The answer to life, the universe and everything is forty-two. "
    "Marvin says the improbability drive makes him even more depressed. "
    "Vogon poetry is the third worst in the known universe. "
    "The ships hung in the sky in much the same way that bricks don't. "
)
"""Natural prose, and that is a requirement rather than decoration. Filler like
`word001 word002` tokenizes at about three real tokens a word (`word` + `0` +
`01`), which would make every count below meaningless: measured on this machine,
443 words of this paragraph are 567 real tokens, or 1.28 tokens a word."""

BIG_WINDOW = 16_384
"""Twice the default, and chosen by measurement rather than by taste: 24576 was
tried first and the model runner died with HTTP 500 (`dial tcp 127.0.0.1:29959`)
because a 4B model's KV cache at that size does not fit beside what else holds
this card. 16384 loaded and answered in 22.6 s."""


def prose(words_wanted: int) -> str:
    """About `words_wanted` whitespace-separated words of `PARAGRAPH`."""
    out: list[str] = []
    while len(" ".join(out).split()) < words_wanted:
        out.append(PARAGRAPH)
    return " ".join(out)


def word_floor(*texts: str) -> int:
    """A *lower* bound on the real token count of `texts`, and the one number in
    this file that has to be sound rather than close.

    Deliberately its own copy rather than a call to
    `ollama.OllamaProvider.word_floor`, which now makes the same argument for
    the pre-flight guard: this is the bound the *test* measures the daemon
    against, and importing the implementation's version would mean a mistake in
    it could no longer be seen from here.

    No token spans a whitespace boundary in the BPE these models use: the
    pre-tokenizer splits before each space-prefixed word, so merges cannot cross
    it and a word costs at least one token. Counting words therefore under-counts
    and never over-counts, which is the direction that matters - a prompt read to
    fewer tokens than it has words was read short, with no room for argument
    about tokenizer ratios. (The bound is weakest for text with no spaces in it,
    CJK above all; this prompt is English prose.)
    """
    return sum(len(text.split()) for text in texts)


def ollama_or_skip(conn, **kwargs) -> ollama.OllamaProvider:
    provider = ollama.OllamaProvider(conn, **kwargs)
    ok, why = provider.available()
    if not ok:
        pytest.skip(f"ollama: {why}")
    return provider


def test_ollama_reads_the_whole_prompt_into_the_window_it_asked_for(conn):
    """A prompt over the default window, with the window raised to hold it.

    This is the plan's "success with the requested `num_ctx`", and the only way
    to show it positively: the prompt is bigger than `DEFAULT_NUM_CTX`, so a
    daemon that ignored the request and used its default could not have read it
    all. Measured 2026-09-03 - 9029 words, `num_ctx` 16384, `num_predict` 4000 ->
    **11220 prompt tokens**, `done_reason: "stop"`, a real answer in 34.1 s, and
    `/api/ps` reporting a 16384-token window. 11220 is both more than the 8192
    default and more than the 9029-word floor, so the window was granted and the
    whole prompt went through it.

    The other assertion is the plan's "never a truncated answer presented as
    complete", and it is the one worth having, because a truncated prompt is not
    an error to Ollama. Measured on the same machine, with the prompt over the
    window: **HTTP 200**, `prompt_eval_count` exactly `num_ctx / 2 + 2` (4098 at
    8192, 2050 at 4096 - the daemon discards half the context and keeps the tail)
    and `/api/ps` reporting the requested window allocated *exactly*. So the
    escape hatch `ollama._answer`'s comment offers - "a daemon that allocated
    less than requested" - is not what happens here: the window is honoured and
    the prompt is shortened to fit it, and `prompt_eval_count` comes back
    *smaller* than `num_ctx` rather than larger. `word_floor` is what sees that,
    and this is the assertion that would go red if it started happening. The
    prompt is sized so it can: truncation at this window would report 8194
    tokens, and the floor is 9029, so a shortened prompt cannot pass for a whole
    one here.

    `Unreachable` is a skip rather than a failure: measured, a window this size
    can fail to start a runner on a card that is busy (HTTP 500 at 24576), and
    "there was no VRAM" is a fact about the machine, not about the code.
    """
    system = "You summarise documents."
    # 9000 words rather than a round 7000: the floor has to clear both the 8192
    # default *and* the 8194 tokens a truncated read at this window would report,
    # or neither of the two assertions below can tell the cases apart.
    user = prose(9000) + "\n\nSummarise the text above in one short sentence."
    floor = word_floor(system, user)
    assert floor > ollama.DEFAULT_NUM_CTX, (
        f"the prompt must be bigger than the default window for this to prove anything: "
        f"{floor} words vs {ollama.DEFAULT_NUM_CTX}"
    )

    provider = ollama_or_skip(conn, num_ctx=BIG_WINDOW)
    request = base.ChatRequest(
        system=system,
        user=user,
        model=provider.default_model,
        temperature=0.0,
        max_output_tokens=tasks.DEFAULT_MAX_OUTPUT_TOKENS,
    )

    try:
        response = provider.complete(request)
    except base.ContextTooLong:
        # The plan's other allowed outcome: refused, loudly, rather than answered
        # from half a prompt. Nothing further to assert - nothing was returned.
        return
    except base.Unreachable as exc:
        pytest.skip(f"ollama could not start a {BIG_WINDOW}-token runner: {exc}")

    assert response.prompt_tokens is not None, "no prompt token count: truncation cannot be seen"
    assert response.prompt_tokens >= floor, (
        f"ollama read {response.prompt_tokens} prompt tokens for a prompt of at least {floor}: "
        f"the prompt was shortened and the answer is written from part of it"
    )
    assert response.prompt_tokens > ollama.DEFAULT_NUM_CTX, (
        f"only {response.prompt_tokens} prompt tokens: this prompt does not need the "
        f"{BIG_WINDOW}-token window, so it proves nothing about num_ctx being honoured"
    )
    assert response.text.strip()

    print(
        f"\n[live] ollama num_ctx={BIG_WINDOW}: {response.prompt_tokens} prompt tokens "
        f"read (floor {floor}, default window {ollama.DEFAULT_NUM_CTX}), "
        f"finish={response.raw_finish_reason}"
    )


def test_ollama_refuses_a_prompt_that_overflowed_the_window_it_asked_for(conn):
    """The refusal half of the same promise, against the daemon rather than a fake.

    `tests/test_llm_ollama.py` already asserts this mapping from a hand-written
    payload; what it cannot assert is that the daemon still produces such a
    payload. This does: measured 2026-09-03, `num_ctx: 512` with 443 words came
    back HTTP 200 with `prompt_eval_count: 567` - Ollama will not allocate a
    window below its 2048 minimum, so the prompt fitted the window it *got* and
    overflowed the one it *asked for*, which is precisely the case
    `ContextTooLong` exists to name.

    A plain success would mean the daemon had started honouring windows below
    2048, and then this test is the thing that says so instead of a truncated
    answer being stored as a summary.
    """
    small = 512
    system = "You summarise documents."
    user = prose(400) + "\n\nSummarise the text above in one short sentence."
    provider = ollama_or_skip(conn, num_ctx=small)

    request = base.ChatRequest(
        system=system,
        user=user,
        model=provider.default_model,
        temperature=0.0,
        max_output_tokens=tasks.DEFAULT_MAX_OUTPUT_TOKENS,
    )

    with pytest.raises(base.ContextTooLong) as caught:
        provider.complete(request)

    message = str(caught.value)
    assert str(small) in message, f"the refusal must name the window that was asked for: {message}"
    assert chunking.estimate_tokens(user) > small  # the prompt really was too big for it
    print(f"\n[live] ollama num_ctx={small}: {message}")


def test_ollama_refuses_an_overflowing_prompt_at_the_window_the_app_ships_with(conn):
    """The same refusal, in the configuration the app actually runs in - which
    is the one that counts, and the one that did not work.

    The test above gets its `ContextTooLong` by asking for a window *below* the
    2048 Ollama will allocate, which is a corner rather than a use. This is the
    real case: `DEFAULT_NUM_CTX`, the window every local job gets, with a prompt
    that does not fit it.

    It was an `xfail(strict=True)` when it was written, and the reason is worth
    keeping: the daemon allocates exactly 8192 (`/api/ps` says so), discards
    half the conversation, reports `prompt_eval_count: 4098` - `num_ctx / 2 + 2`
    - and answers 200. 4098 is *below* 8192, so `_answer`'s post-hoc guard is
    structurally unable to see the case it exists for. Run un-xfailed on
    2026-09-03 it did not even fail as a `BadResponse`: the daemon returned a
    fluent summary of whichever half of the prompt survived, which is exactly
    the "truncated answer presented as complete" the plan forbids.

    The fix was `ollama._refuse_a_prompt_that_cannot_fit`, and it is not the
    same check: a word count is a lower bound on tokens, so a prompt with more
    words than the whole window certainly cannot fit and is refused *before*
    the request. The two guards catch opposite failures - the daemon allocating
    more than asked (post-hoc) and honouring the window while shortening the
    prompt (pre-flight) - and this test is the one that says the second one is
    still there.
    """
    system = "You summarise documents."
    user = prose(9000) + "\n\nSummarise the text above in one short sentence."
    provider = ollama_or_skip(conn)  # the shipped DEFAULT_NUM_CTX

    request = base.ChatRequest(
        system=system,
        user=user,
        model=provider.default_model,
        temperature=0.0,
        max_output_tokens=tasks.DEFAULT_MAX_OUTPUT_TOKENS,
    )

    with pytest.raises(base.ContextTooLong) as caught:
        provider.complete(request)

    message = str(caught.value)
    assert str(ollama.DEFAULT_NUM_CTX) in message, (
        f"the refusal must name the window the app ships with: {message}"
    )
    print(f"\n[live] ollama num_ctx={ollama.DEFAULT_NUM_CTX}: {message}")


# --- a budget too small to reach an answer ---------------------------------------------------


SMALL_BUDGET = 200
"""An answer budget this machine's local model provably cannot finish in.

Measured 2026-09-03, `qwen3.5:4b` at `num_predict: 200` through `/api/chat`:
4 runs out of 4 returned HTTP 200, `done_reason: "length"`, `eval_count: 200`,
`content: ""` and 772-793 characters of `message.thinking`. Deterministic, and
that determinism is the premise of the test below - a pin on a coin flip would
be worse than no pin."""


def test_ollama_says_the_budget_ran_out_rather_than_blaming_the_model(conn):
    """The deterministic half of the `DEFAULT_MAX_OUTPUT_TOKENS` finding.

    A thinking model can spend every token it is allowed on its own preamble
    and emit no `content` at all. Nothing is broken when that happens, but the
    message the caller used to get - "returned an empty answer ... nothing was
    generated to store" - sends the reader hunting a dead model, when the fix
    is one number. This asserts against the live daemon that the error names
    the budget, names the knob and says where the tokens went.

    It is the executable form of what `LIVE_MAX_OUTPUT_TOKENS` describes in
    prose, at the value where the outcome is certain rather than at 4000 where
    it is a coin flip. Only the message is pinned here; the shipped default
    being under-sized for this model is stated there and is not a Task 7 fix.
    """
    provider = ollama_or_skip(conn)
    request = base.ChatRequest(
        system="You summarise recordings.",
        user=f"Transcript:\n{SENTENCES[0]}\n{SENTENCES[1]}\n\nSummarise it in one paragraph.",
        model=provider.default_model,
        temperature=0.0,
        max_output_tokens=SMALL_BUDGET,
    )

    with pytest.raises(base.BadResponse) as caught:
        provider.complete(request)

    message = str(caught.value)
    assert str(SMALL_BUDGET) in message, f"the budget that ran out must be named: {message}"
    assert "max_output_tokens" in message, f"and the knob that fixes it: {message}"
    assert "thinking" in message.lower(), f"and where the budget went: {message}"
    print(f"\n[live] ollama num_predict={SMALL_BUDGET}: {message}")
