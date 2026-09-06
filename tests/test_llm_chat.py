"""Chat with a transcript, with citations that seek the player (Phase 5 Task 5).

Chat is the one LLM feature where the *input* is chosen at request time: a
question decides which part of a long recording the model gets to read. So the
tests here are mostly about retrieval and about what comes back attached to it.

What is asserted, and why each is worth a test:

* **a transcript that fits is passed whole.** Retrieval that runs when it does
  not have to is a way to lose the answer: the model cannot cite a line it was
  never shown.
* **a transcript that does not fit is retrieved, not truncated.** The segments
  that match the question, one neighbour either side, in time order - a hit
  without its neighbours is half a sentence, and out of time order a model
  reading `[3:00]` before `[1:00]` will narrate the recording backwards.
* **the ids that come back are run-scoped `segment.idx`, not FTS rowids.** The
  two number spaces look alike and are not; the fixtures here seed a decoy run
  first so they cannot coincide, because in a fresh database they otherwise do.
* **a citation that cannot be seeked is dropped.** `[m:ss]` is a link to a
  moment in the player. A hallucinated `[99:00]` on a three-minute recording
  would render as a link to nowhere, which is worse than no link.
* **the pin holds here too.** Chat goes through `llm.chat()` like everything
  else, so a pinned recording refuses a cloud provider before a request exists.
"""

from __future__ import annotations

import json

import pytest

from scribe import db, jobs, llm, paths, runner
from scribe.exports import doc as docs
from scribe.llm import base, chat_tool
from scribe.stages import llm_stage
from tests.seed import seed_media, seed_run

# --- a provider that answers from a script ---------------------------------------------


def fake_provider(script, *, name: str = "fake", is_local: bool = False):
    """A `Provider` subclass answering from `script`, and the calls it received.

    Local to this file rather than imported from `test_llm_tasks`: that one
    answers JSON for six schemas, this one answers prose with timestamps in it,
    and a test module that reaches into another test module's internals ties two
    tasks' tests together for the sake of thirty lines.

    `script` is a list consumed in order (an `Exception` in it is raised) or a
    callable taking the `ChatRequest`. Registered into `llm.PROVIDERS` by the
    tests, so `chat()` resolves it exactly as it resolves a real one.
    """
    calls: list[base.ChatRequest] = []
    remaining = None if callable(script) else list(script)

    class Fake(base.Provider):
        def __init__(self, conn=None, **kwargs):
            self.conn = conn
            self.kwargs = kwargs

        def available(self):
            return True, "fake"

        def models(self):
            return [type(self).default_model]

        def complete(self, req):
            calls.append(req)
            answer = script(req) if remaining is None else remaining.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return base.ChatResponse(
                text=answer,
                model=f"{req.model}-2026-09-02",
                provider=type(self).name,
                prompt_tokens=13,
                completion_tokens=5,
                raw_finish_reason="stop",
            )

    Fake.name = name
    Fake.is_local = is_local
    Fake.default_model = f"{name}-1"
    Fake.key_env_vars = ()
    return Fake, calls


def register(monkeypatch, provider_cls) -> str:
    monkeypatch.setitem(llm.PROVIDERS, provider_cls.name, provider_cls)
    return provider_cls.name


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A tmp database that `runner.main()` also finds via `paths.DB_PATH`."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


TOPICS = (
    "towels",
    "vogons",
    "improbability",
    "restaurants",
    "dolphins",
    "mice",
    "petunias",
    "babelfish",
    "guides",
    "pangalactic",
    "magrathea",
    "krikkit",
)
"""One rare word per segment, so a question can be aimed at a known segment and
the assertion can name the segment it must reach."""


def seed_searchable_run(
    conn, media_id, *, topics=TOPICS, words_per: int = 8, filler: str = "filler", current=True
) -> int:
    """A run whose segment `i` is the only one containing `topics[i]`.

    Sized by hand like the chunking tests' documents: `words_per` short words a
    segment, so a budget in a test picks a known number of segments rather than
    whatever the default transcript happens to cost. `filler` names the padding,
    so a second run holding the same topics can still be told apart in the text
    that comes back.
    """
    words: list[dict] = []
    segments: list[dict] = []
    t = 0.0
    for i, topic in enumerate(topics):
        first = len(words)
        tokens = [topic] + [f"{filler}{j:02d}" for j in range(words_per - 1)]
        for token in tokens:
            words.append(
                {
                    "idx": len(words),
                    "start": t,
                    "end": t + 0.4,
                    "text": " " + token,
                    "probability": 0.9,
                    "speaker": "SPEAKER_00",
                }
            )
            t += 0.5
        segments.append(
            {
                "idx": i,
                "start": words[first]["start"],
                "end": words[-1]["end"],
                "text": " ".join(w["text"].strip() for w in words[first:]),
            }
        )
    return seed_run(conn, media_id, words=words, segments=segments, current=current)


DECOY_TOPICS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta")
"""Eight, deliberately: enough that the recording seeded after it starts at
rowid 9, past every `idx` the assertions below name. A shorter decoy would let
the two number spaces overlap again in exactly the range under test."""


@pytest.fixture
def decoy(conn):
    """A whole other recording, transcribed first.

    Its segments take the first rowids, so for every media seeded afterwards
    `segment.id` and `segment.idx` differ by an offset no test states. Without
    this, a fresh database numbers the first run's segments 1..n against idx
    0..n-1 and *every* assertion below would pass whichever of the two number
    spaces the implementation returned.
    """
    media_id = seed_media(conn, title="Decoy")
    seed_searchable_run(conn, media_id, topics=DECOY_TOPICS)
    return media_id


@pytest.fixture
def media(conn, decoy):
    """A twelve-segment recording whose segment ids are offset from its idxs."""
    media_id = seed_media(conn, title="The Guide", duration=48.0)
    seed_searchable_run(conn, media_id)
    return media_id


@pytest.fixture
def long_media(conn, decoy):
    """Forty segments: far more than any budget in this file allows, so the
    retrieval path is the only one it can take."""
    media_id = seed_media(conn, title="The long one", duration=160.0)
    seed_searchable_run(conn, media_id, topics=tuple(f"topic{i:02d}" for i in range(40)))
    return media_id


@pytest.fixture
def short_media(conn, decoy):
    """The default four-segment transcript: it fits in any sane budget."""
    media_id = seed_media(conn, title="Clip")
    seed_run(conn, media_id)
    return media_id


def messages(conn, media_id) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chat_message WHERE media_id=? ORDER BY id", (media_id,)
        )
    ]


# --- what the model is given to read ---------------------------------------------------


def test_a_transcript_that_fits_is_passed_whole(conn, short_media):
    """Retrieval that runs when it does not have to is a way to lose the answer:
    a model cannot cite a line it was never shown."""
    doc = docs.load(conn, short_media)

    text, segment_ids = chat_tool.context_for(
        conn, short_media, "what about towels?", budget_tokens=100_000
    )

    assert segment_ids == [int(s["idx"]) for s in doc.segments]
    assert "Don't panic" in text
    assert "Vogon poetry" in text
    assert "[0:00]" in text  # the clock the player already seeks by
    assert chat_tool.GAP_MARKER not in text  # nothing was left out


RETRIEVAL_BUDGET = 85
"""Room for three of these segments and not four: each is eight short words
plus its `[m:ss]`, about 26 tokens by `chunking.estimate_tokens`, and the fill
also charges a token per segment for the `…` markers. Sized by hand like the
chunking tests' documents, so an assertion below names the segments the
retrieval chose rather than however many happened to fit."""


def test_a_long_transcript_is_retrieved_around_the_question(conn, media):
    """The hit, one neighbour either side, in time order - and nothing else."""
    text, segment_ids = chat_tool.context_for(
        conn, media, "what did they say about dolphins?", budget_tokens=RETRIEVAL_BUDGET
    )

    assert segment_ids == [3, 4, 5]  # `dolphins` is segment 4; the neighbours frame it
    assert "dolphins" in text
    assert "krikkit" not in text  # the far end of the recording was not sent
    assert chat_tool.GAP_MARKER in text  # and the model is told something is missing


def test_the_ids_are_run_scoped_idxs_and_not_fts_rowids(conn, media):
    """`segment.idx` is the run's own numbering and `segment.id` is the rowid an
    FTS hit gives; they look alike and are not. The decoy run offsets them, so
    this assertion can only pass one way."""
    rowids = [
        r["id"]
        for r in conn.execute(
            "SELECT s.id FROM segment s JOIN run r ON r.id=s.run_id"
            " WHERE r.media_id=? ORDER BY s.idx",
            (media,),
        )
    ]
    assert min(rowids) > 8, "the decoy run did not take the first rowids"

    _text, segment_ids = chat_tool.context_for(
        conn, media, "tell me about mice", budget_tokens=RETRIEVAL_BUDGET
    )

    assert segment_ids == [4, 5, 6]
    assert set(segment_ids).isdisjoint(rowids)  # not one id from the rowid space


def elsewhere_topics(word: str, at: int = 9) -> tuple[str, ...]:
    """Twelve topics none of which the recording under test uses, except that
    `word` sits at position `at`."""
    topics = [f"elsewhere{i:02d}" for i in range(12)]
    topics[at] = word
    return tuple(topics)


def test_retrieval_reads_this_recordings_current_run_and_nothing_else(conn, media):
    """`segment_fts` is one index over every recording in the library and every
    run of each, and a hit carries an `idx` that means something only inside its
    own run. A hit from elsewhere would therefore not merely be useless - `idx`
    9 of another recording would select segment 9 of *this* one, and the answer
    would be built on a passage nothing matched.

    The word is in another recording and in a replaced run of this one, and
    nowhere in the transcript being read. Scoped, there are no hits and the
    opening is sent; unscoped, the foreign `idx` picks segments 8 to 10 - two
    outcomes that cannot be confused for one another.
    """
    intruder = seed_media(conn, title="Someone else's meeting")
    seed_searchable_run(conn, intruder, topics=elsewhere_topics("swordfish"), filler="intruder")
    seed_searchable_run(
        conn, media, topics=elsewhere_topics("swordfish"), filler="stale", current=False
    )

    text, segment_ids = chat_tool.context_for(
        conn, media, "what about the swordfish?", budget_tokens=RETRIEVAL_BUDGET
    )

    assert segment_ids == [0, 1, 2]  # the opening, because nothing here matched
    assert "intruder" not in text and "stale" not in text
    assert "filler00" in text  # this run's own words


def _timestamps(text: str) -> list[str]:
    return [line.split("]")[0] + "]" for line in text.splitlines() if line.startswith("[")]


def test_retrieval_is_in_time_order_however_the_hits_ranked(conn, media):
    """A model reading `[3:00]` before `[1:00]` narrates the recording
    backwards. Rank decides what is sent; time decides the order it is sent in.

    Two hits at opposite ends of the recording, and a budget that fits both
    with their neighbours but not the transcript between them - the case where
    an internal `…` is the only thing telling the model that eight minutes it
    cannot see went by.
    """
    text, segment_ids = chat_tool.context_for(
        conn, media, "krikkit and towels", budget_tokens=150
    )

    assert segment_ids == [0, 1, 10, 11]  # both hits, each with its neighbour
    assert text.count(chat_tool.GAP_MARKER) == 1  # between the two stretches
    assert chat_tool.GAP_MARKER in text.split("[0:40]")[0].split("[0:04]")[1]
    seconds = chat_tool.parse_citations(" ".join(_timestamps(text)))
    assert seconds == sorted(seconds)
    assert "towels" in text and "krikkit" in text


def test_a_question_nothing_matches_falls_back_to_the_opening(conn, media):
    """An answerless question still gets a recording to answer from: the first
    budget's worth of it, on segment boundaries. Sending nothing would make the
    model say "the transcript is empty", which is a lie about the recording."""
    text, segment_ids = chat_tool.context_for(
        conn, media, "what about quantum chromodynamics?", budget_tokens=RETRIEVAL_BUDGET
    )

    assert segment_ids and segment_ids[0] == 0
    assert "towels" in text


def test_a_question_with_no_usable_terms_does_not_reach_the_index(conn, media):
    """`segment_fts MATCH ''` is an OperationalError, not an empty result, so
    the emptiness has to be caught before the query rather than around it."""
    text, segment_ids = chat_tool.context_for(conn, media, "?? !!", budget_tokens=RETRIEVAL_BUDGET)

    assert segment_ids and segment_ids[0] == 0
    assert text.strip()


def test_retrieval_never_exceeds_the_budget_it_was_given(conn, media):
    from scribe.llm.chunking import estimate_tokens

    text, _ids = chat_tool.context_for(
        conn, media, "dolphins and mice", budget_tokens=RETRIEVAL_BUDGET
    )

    assert estimate_tokens(text) <= RETRIEVAL_BUDGET


TIGHT_BUDGET = 78
"""One token under what three of these segments plus their two `…` markers
cost. Measured, not guessed: at this budget an implementation that charged for
the lines and forgot the markers would send 80 tokens' worth of context for a
78-token budget, and one that charges for both sends two segments."""


def test_the_gap_markers_are_paid_for_out_of_the_budget(conn, media):
    """The budget is a promise about the string that gets sent, and the markers
    are part of that string. A budget that only counted the transcript lines
    would be over by one token per stretch - small here, and exactly as wrong on
    a window the prompt only just fits into."""
    from scribe.llm.chunking import estimate_tokens

    text, segment_ids = chat_tool.context_for(
        conn, media, "dolphins and mice", budget_tokens=TIGHT_BUDGET
    )

    assert chat_tool.GAP_MARKER in text
    assert len(segment_ids) == 2  # the third would only fit if the markers were free
    assert estimate_tokens(text) <= TIGHT_BUDGET


# --- citations -------------------------------------------------------------------------


def test_parse_citations_reads_both_clock_shapes_and_ignores_the_rest():
    text = "They agree at [1:23], again at [01:02:03], and someone said [abc] too."

    assert chat_tool.parse_citations(text) == [83.0, 3723.0]


def test_parse_citations_drops_a_timestamp_past_the_end(conn):
    """A hallucinated timestamp must not produce a seek to nowhere."""
    text = "It is at [0:10], and also at [99:00]."

    assert chat_tool.parse_citations(text, duration=60.0) == [10.0]
    assert chat_tool.parse_citations(text) == [10.0, 5940.0]  # no duration, no opinion


def test_parse_citations_keeps_the_order_it_read_them_in_without_repeats():
    text = "First [2:00], then [0:30], then [2:00] again."

    assert chat_tool.parse_citations(text) == [120.0, 30.0]


# --- asking -----------------------------------------------------------------------------


ANSWER = "They talk about dolphins at [0:16] and mention mice at [0:20]."


def test_ask_sends_the_question_and_the_transcript_and_asks_for_citations(
    conn, short_media, monkeypatch
):
    provider, calls = fake_provider([ANSWER])
    register(monkeypatch, provider)

    answer = chat_tool.ask(
        conn,
        media_id=short_media,
        question="What is the answer to everything?",
        provider_name="fake",
        model="fake-1",
        history=[],
    )

    assert answer.text == ANSWER
    assert answer.citations == [16.0, 20.0]
    sent = calls[0]
    assert "What is the answer to everything?" in sent.user
    assert "Vogon poetry" in sent.user  # the transcript it must answer from
    assert "[m:ss]" in sent.system  # the citation contract
    assert "Clip" in sent.user  # what the recording is


def test_ask_stores_the_pair_and_it_round_trips(conn, short_media, monkeypatch):
    provider, _calls = fake_provider([ANSWER])
    register(monkeypatch, provider)

    chat_tool.ask(
        conn,
        media_id=short_media,
        question="Who mentions the mice?",
        provider_name="fake",
        model="fake-1",
        history=[],
    )

    stored = messages(conn, short_media)
    assert [row["role"] for row in stored] == ["user", "assistant"]
    assert stored[0]["content"] == "Who mentions the mice?"
    assert stored[1]["content"] == ANSWER
    assert json.loads(stored[1]["citations_json"]) == [16.0, 20.0]
    assert stored[0]["created_at"] <= stored[1]["created_at"]

    history = chat_tool.history_for(conn, short_media)
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "Who mentions the mice?"),
        ("assistant", ANSWER),
    ]


def test_an_answer_citing_past_the_end_stores_only_what_can_be_seeked(
    conn, short_media, monkeypatch
):
    provider, _calls = fake_provider(["Certainly: [0:01] and then [4:20:00]."])
    register(monkeypatch, provider)

    answer = chat_tool.ask(
        conn,
        media_id=short_media,
        question="When?",
        provider_name="fake",
        model="fake-1",
        history=[],
    )

    assert answer.citations == [1.0]
    assert json.loads(messages(conn, short_media)[1]["citations_json"]) == [1.0]


def test_the_history_is_carried_into_the_prompt_and_capped(conn, short_media, monkeypatch):
    provider, calls = fake_provider(lambda req: "Yes. [0:01]")
    register(monkeypatch, provider)
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
        for i in range(chat_tool.MAX_HISTORY_MESSAGES + 4)
    ]

    chat_tool.ask(
        conn,
        media_id=short_media,
        question="And now?",
        provider_name="fake",
        model="fake-1",
        history=history,
    )

    sent = calls[0].user
    assert "turn 0" not in sent  # the oldest turns fell off the front
    assert f"turn {len(history) - 1}" in sent


def test_a_long_history_shrinks_the_transcript_rather_than_the_window(
    conn, long_media, monkeypatch
):
    """History folds into the user prompt - `ChatRequest` has one - so it is
    spent out of the same window the transcript is. Left unaccounted for, turn
    twelve of a chat silently overflows the model instead of reading less."""
    provider, _calls = fake_provider(lambda req: "Yes. [0:01]")
    register(monkeypatch, provider)
    long_history = [
        {"role": "user", "content": "a previous question about dolphins. " * 40},
        {"role": "assistant", "content": "a previous answer about dolphins. " * 40},
    ]
    kwargs = dict(
        media_id=long_media,
        # A word every segment holds, so every segment is a hit and the budget
        # is the only thing deciding how many of them are sent. A question that
        # matched one segment would answer "three either way" and prove nothing.
        question="what do they say about filler03?",
        provider_name="fake",
        model="fake-1",
        context_tokens=1500,
        max_output_tokens=100,
    )

    fresh = chat_tool.plan_chat(conn, history=[], **kwargs)
    burdened = chat_tool.plan_chat(conn, history=long_history, **kwargs)

    assert burdened.budget_tokens < fresh.budget_tokens
    assert len(burdened.segment_ids) < len(fresh.segment_ids)
    assert burdened.segment_ids  # but it still gets something to read


# --- the pin, from here too -------------------------------------------------------------


def test_a_private_media_refuses_a_cloud_provider_here_too(conn, short_media, monkeypatch):
    provider, calls = fake_provider([ANSWER])
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (short_media,))
        conn.commit()

    with pytest.raises(llm.PrivacyRefused):
        chat_tool.ask(
            conn,
            media_id=short_media,
            question="Anything at all?",
            provider_name="fake",
            model="fake-1",
            history=[],
        )

    assert calls == []
    assert messages(conn, short_media) == []


def test_a_private_media_still_chats_with_a_local_provider(conn, short_media, monkeypatch):
    provider, calls = fake_provider([ANSWER], name="localfake", is_local=True)
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (short_media,))
        conn.commit()

    chat_tool.ask(
        conn,
        media_id=short_media,
        question="Anything at all?",
        provider_name="localfake",
        model="localfake-1",
        history=[],
    )

    assert len(calls) == 1
    assert [row["role"] for row in messages(conn, short_media)] == ["user", "assistant"]


def test_a_media_without_a_transcript_is_refused_before_a_call(conn, monkeypatch):
    provider, calls = fake_provider([ANSWER])
    register(monkeypatch, provider)
    media_id = seed_media(conn, title="Not transcribed")

    with pytest.raises(docs.NoTranscript):
        chat_tool.ask(
            conn,
            media_id=media_id,
            question="What is this?",
            provider_name="fake",
            model="fake-1",
            history=[],
        )
    assert calls == []


def test_a_question_with_no_words_in_it_is_refused(conn, short_media, monkeypatch):
    provider, calls = fake_provider([ANSWER])
    register(monkeypatch, provider)

    with pytest.raises(ValueError):
        chat_tool.ask(
            conn,
            media_id=short_media,
            question="   ",
            provider_name="fake",
            model="fake-1",
            history=[],
        )
    assert calls == []


# --- as a job ---------------------------------------------------------------------------


def test_a_chat_job_runs_end_to_end_and_stores_the_pair(conn, short_media, monkeypatch):
    """Chat is a kind of the `llm` job type, not a job type of its own: same
    queue, same GPU lease, same privacy check, same board."""
    provider, calls = fake_provider([ANSWER])
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=short_media,
        params={
            "media_id": short_media,
            "kind": "chat",
            "provider": "fake",
            "model": "fake-1",
            "question": "Who mentions the mice?",
        },
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert len(calls) == 1
    stored = messages(conn, short_media)
    assert [r["role"] for r in stored] == ["user", "assistant"]
    assert stored[1]["content"] == ANSWER

    events = jobs.events_after(conn, job_id, 0)
    assert [e["payload"]["name"] for e in events if e["kind"] == "stage"] == [
        "prepare",
        "generate",
        "store",
    ]
    done = [e for e in events if e["kind"] == "chat"]
    assert done and done[-1]["payload"]["message_id"] == stored[1]["id"]
    assert done[-1]["payload"]["citations"] == [16.0, 20.0]


def test_a_chat_job_continues_the_conversation_it_finds(conn, short_media, monkeypatch):
    """The question arrives in the params; the history is what is already
    stored. A chat that forgot the previous turn is not a chat."""
    provider, calls = fake_provider(lambda req: "Still yes. [0:01]")
    register(monkeypatch, provider)
    chat_tool.store_exchange(
        conn,
        media_id=short_media,
        question="Was there a towel?",
        answer=chat_tool.ChatAnswer(text="Yes, at [0:00].", citations=[0.0]),
    )
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=short_media,
        params={
            "media_id": short_media,
            "kind": "chat",
            "provider": "fake",
            "model": "fake-1",
            "question": "Are you sure?",
        },
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert "Was there a towel?" in calls[0].user
    stored = messages(conn, short_media)
    # Two seeded plus exactly two new: the job runs plan, ask and store as its
    # three stages, so the exchange is written once. `ask` composes the same
    # three for a direct caller, and a job that called it would store twice.
    assert len(stored) == 4
    assert [r["content"] for r in stored][-2:] == ["Are you sure?", "Still yes. [0:01]"]


def test_a_chat_job_files_its_timings_under_the_model_that_answered(
    conn, short_media, monkeypatch
):
    """Without this the runner files them under `transcribe.perf_model_for`,
    which reads the same "model" param through the Whisper tier rules."""
    provider, _calls = fake_provider([ANSWER])
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=short_media,
        params={
            "media_id": short_media,
            "kind": "chat",
            "provider": "fake",
            "model": "fake-1",
            "question": "Who?",
        },
    )
    jobs.claim_next(conn)
    assert runner.main([str(job_id)]) == 0

    perf = [dict(r) for r in conn.execute("SELECT stage, model FROM stage_perf ORDER BY id")]
    assert [r["stage"] for r in perf] == ["prepare", "generate", "store"]
    assert {r["model"] for r in perf} == {"fake-1"}


def test_a_chat_job_for_a_private_media_fails_without_making_a_call(
    conn, short_media, monkeypatch
):
    provider, calls = fake_provider([ANSWER])
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (short_media,))
        conn.commit()
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=short_media,
        params={
            "media_id": short_media,
            "kind": "chat",
            "provider": "fake",
            "model": "fake-1",
            "question": "Anything?",
        },
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error_code"] == "PRIVACY_REFUSED"
    assert calls == []
    assert messages(conn, short_media) == []


def test_a_chat_job_without_a_question_says_so_rather_than_guessing(
    conn, short_media, monkeypatch
):
    provider, calls = fake_provider([ANSWER])
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=short_media,
        params={"media_id": short_media, "kind": "chat", "provider": "fake"},
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "question" in (row["error_detail"] or "")
    assert calls == []


def test_chat_is_a_kind_of_the_llm_job_and_not_one_of_the_six_tasks(conn):
    """The kinds the rail offers are `tasks.KINDS`; chat is the seventh thing an
    `llm` job can be asked to do, and it is not on that list."""
    from scribe.llm import tasks

    assert chat_tool.CHAT_KIND not in tasks.KINDS
    assert llm_stage.handler_for(chat_tool.CHAT_KIND) is not llm_stage.handler_for("summary")
