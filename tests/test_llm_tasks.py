"""The six preset outputs, as resumable map-reduce jobs (Phase 5 Task 4).

Everything here runs against a **fake provider registered in
`llm.PROVIDERS`** rather than against a fake `chat()`. That choice is the
point: `run_task` then goes through the real front door, so the private-mode
pin, the retry policy and the provider registry are all still in the path a
test exercises. A fake `chat()` would have proved that `tasks.py` calls
something, and nothing about what it calls.

What is asserted, and why each one is worth a test:

* **one row per answer, under the key that produced it.** A better model later
  is a new row (`(media, kind, provider, model, prompt_version)`), and the
  stored content parses as its own schema - the repair pass happens once, at
  write time, so every reader afterwards can trust the row.
* **nothing is overwritten.** Re-running the same task twice leaves two rows.
  The output of a model is evidence about what that model said; replacing it
  silently would make the table a claim about the present rather than a record.
* **a resumed job makes only the missing calls.** Chunk answers are rows, so
  an interrupted forty-minute map-reduce costs the chunks it had already paid
  for exactly once. The resume key includes the run: chunk 3 of a
  re-transcribed recording is not chunk 3 of the old one.
* **a model that answers badly fails loudly, with its text kept.** Fenced JSON
  is repaired; prose where JSON was required is a `BadResponse` carrying the
  raw text, because "the model refused" is something a user must be able to
  read on the jobs board.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from scribe import db, jobs, llm, paths, runner
from scribe.llm import base, tasks
from scribe.stages import llm_stage
from tests.seed import seed_media, seed_run

# --- a provider that answers from a script -------------------------------------------


def fake_provider(
    script,
    *,
    name: str = "fake",
    is_local: bool = False,
    supports_json_schema: bool = False,
):
    """A `Provider` subclass answering from `script`, and the calls it received.

    `script` is either a list of answers consumed in order (an `Exception` in
    the list is raised instead of returned) or a callable taking the
    `ChatRequest`. Registered into `llm.PROVIDERS` by the tests, so `chat()`
    resolves it exactly as it resolves a real one.
    """
    calls: list[base.ChatRequest] = []
    remaining = list(script) if not callable(script) else None

    class Fake(base.Provider):
        pass

    Fake.name = name
    Fake.is_local = is_local
    Fake.default_model = f"{name}-1"
    Fake.key_env_vars = ()
    Fake.supports_json_schema = supports_json_schema

    def __init__(self, conn=None, **kwargs):
        self.conn = conn
        self.kwargs = kwargs

    def available(self):
        return True, "fake"

    def models(self):
        return [Fake.default_model]

    def complete(self, req):
        calls.append(req)
        if remaining is None:
            answer = script(req)
        else:
            assert remaining, f"{name} was called more often than the script allows"
            answer = remaining.pop(0)
        if isinstance(answer, Exception):
            raise answer
        # A whole ChatResponse, for the tests that care about a field the
        # default below fixes - `raw_finish_reason` above all, which is how a
        # provider says it stopped writing because it ran out of room.
        if isinstance(answer, base.ChatResponse):
            return answer
        return base.ChatResponse(
            text=answer,
            model=f"{req.model}-2026-09-02",  # a snapshot id, as the real APIs return
            provider=name,
            prompt_tokens=11,
            completion_tokens=7,
            raw_finish_reason="stop",
        )

    Fake.__init__ = __init__
    Fake.available = available
    Fake.models = models
    Fake.complete = complete
    Fake.__abstractmethods__ = frozenset()
    return Fake, calls


def register(monkeypatch, provider_cls) -> str:
    monkeypatch.setitem(llm.PROVIDERS, provider_cls.name, provider_cls)
    return provider_cls.name


# --- answers a fake model gives -------------------------------------------------------

ANSWERS: dict[str, str] = {
    "summary": json.dumps(
        {
            "paragraph": "Two travellers discuss towels and the answer to everything.",
            "bullets": ["A towel is the most useful thing", "The answer is forty-two"],
        }
    ),
    "action_items": json.dumps(
        {
            "items": [
                {"text": "Pack a towel", "owner": "Ford", "evidence_ts": 12.5},
                {"text": "Ask Deep Thought again", "owner": None, "evidence_ts": None},
            ]
        }
    ),
    "chapters": json.dumps(
        {"chapters": [{"start": 0.0, "title": "Towels"}, {"start": 12.5, "title": "Forty-two"}]}
    ),
    "minutes": json.dumps(
        {
            "agenda": ["Towels", "The answer"],
            "decisions": ["Always bring a towel"],
            "actions": [{"text": "Pack a towel", "owner": "Ford", "evidence_ts": 12.5}],
        }
    ),
    "blog": json.dumps({"title": "Why a towel", "body": "## Why\n\nBecause it is useful."}),
    "custom": "Plain text, because a custom prompt has no schema to satisfy.",
}


def answer_for(kind: str):
    """A script that answers whatever it is asked, correctly for `kind`."""
    return [ANSWERS[kind]]


CHUNK_MARKER = "You are reading excerpt"
"""The first words of `map_chunk.md`. A script tells a chunk call from the
combine call by looking for it, rather than by counting: how many chunks a
document plans into is the chunker's business, and a test that hard-codes the
number is a test that breaks when the budget arithmetic improves."""


def map_reduce_script(final_answer: str, note=lambda i: f"notes {i}"):
    """Notes for every chunk call, `final_answer` for the combine call.

    `note(i)` may return an exception to raise instead of an answer, which is
    how the resume test interrupts a run part-way through.
    """
    seen = {"chunks": 0}

    def answer(req: base.ChatRequest):
        if CHUNK_MARKER in req.user:
            index = seen["chunks"]
            seen["chunks"] += 1
            return note(index)
        return final_answer

    return answer


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


@pytest.fixture
def media(conn):
    """A media with the default four-segment transcript."""
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    return media_id


def seed_long_run(conn, media_id, *, n_segments: int = 12, words_per: int = 10) -> int:
    """A run with `n_segments` segments of `words_per` six-letter words each.

    Sized by hand like `test_llm_chunking`'s documents, so a budget in the
    tests picks a known number of chunks rather than whatever the default
    transcript happens to cost.
    """
    words: list[dict] = []
    segments: list[dict] = []
    t = 0.0
    for i in range(n_segments):
        first = len(words)
        for j in range(words_per):
            words.append(
                {
                    "idx": len(words),
                    "start": t,
                    "end": t + 0.4,
                    "text": f" word{j:02d}",
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
    return seed_run(conn, media_id, words=words, segments=segments)


def rows(conn, media_id, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM llm_output WHERE media_id=?"
    args: list = [media_id]
    if kind is not None:
        sql += " AND kind=?"
        args.append(kind)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


# --- the six kinds ----------------------------------------------------------------------


def test_the_kinds_are_the_ones_the_rail_offers_with_custom_last():
    assert set(tasks.TASKS) == {
        "summary", "action_items", "chapters", "minutes", "blog", "speakers", "labels",
        "cleanup", "custom",
    }
    assert tasks.KINDS[-1] == "custom"
    assert tasks.PROMPT_VERSION and isinstance(tasks.PROMPT_VERSION, str)
    for kind, spec in tasks.TASKS.items():
        assert spec.kind == kind
        assert (tasks.PROMPTS_DIR / f"{spec.template}.md").is_file()


def test_the_map_reduce_templates_are_the_ones_chunking_names():
    """`chunking.MAP_REDUCE_PROMPTS` is the single spelling of these two names,
    so a rename there cannot leave `tasks.py` reading a file that is gone."""
    from scribe.llm import chunking

    for name in chunking.MAP_REDUCE_PROMPTS.values():
        assert (tasks.PROMPTS_DIR / f"{name}.md").is_file()


PROMPTS_DIGEST = "c707d10a72676e6efdbfaa7423fcad91ec0c0d48bd2f7b89af13886a6f9c10a4"
"""sha256 over the prompt templates, for PROMPT_VERSION "3" (TASK-039: the
speakers prompt asks for the full name). Line endings are normalised first,
because git rewrites them on checkout here.

Moved once without a bump, on 2026-09-06, when `speakers.md` and `cleanup.md`
were *added*: the six templates that existed were byte-identical before and
after, so every stored answer is still an answer to the question its row
names. Adding a kind is not editing a question."""


def test_editing_a_template_means_bumping_the_prompt_version():
    """`PROMPT_VERSION` is part of the key every answer is stored under, so that
    answers to two different questions never sit in one list pretending to be
    comparable. The plan states the rule; without something that fails, it is a
    rule nobody is told they broke.

    If this fails and you did edit a template: bump `tasks.PROMPT_VERSION`, then
    put the new digest below. If you did not edit one, something else did.

    *Adding* a template is the other case, and it has the opposite answer:
    record the new digest and leave the version alone. A new kind has no stored
    answers to be confused with, while bumping the version would change the key
    of every *other* kind - orphaning answers that were paid for and are still
    answers to exactly the question that was asked. TASK-023 added `labels.md`
    on 2026-09-10 and did not bump.
    """
    digest = hashlib.sha256()
    for path in sorted(tasks.PROMPTS_DIR.glob("*.md")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8"))

    assert digest.hexdigest() == PROMPTS_DIGEST, (
        f"the prompt templates changed while PROMPT_VERSION is still "
        f"{tasks.PROMPT_VERSION!r}: bump it, then record the new digest here"
    )


def test_every_schema_kind_names_its_own_fields_in_its_own_template():
    """The template shows the model an example of the shape it must return, and
    the schema validates what comes back. Two spellings of one contract drift;
    this fails the day a field is renamed in one of them only."""
    for spec in tasks.TASKS.values():
        if spec.schema is None:
            continue
        text = (tasks.PROMPTS_DIR / f"{spec.template}.md").read_text(encoding="utf-8")
        for field in spec.schema.model_fields:
            assert field in text, f"{spec.kind} template never mentions {field!r}"


@pytest.mark.parametrize("kind", sorted(ANSWERS))
def test_each_kind_stores_one_validated_row_under_its_own_key(conn, media, monkeypatch, kind):
    provider, calls = fake_provider(answer_for(kind))
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn,
        media_id=media,
        kind=kind,
        provider_name="fake",
        model="fake-1",
        custom_prompt="What did they decide?" if kind == "custom" else None,
    )

    assert len(calls) == 1  # a four-segment transcript fits in one call
    stored = rows(conn, media)
    assert [r["id"] for r in stored] == [output_id]
    row = stored[0]
    assert row["kind"] == kind
    assert row["provider"] == "fake"
    assert row["model"] == "fake-1"
    assert row["prompt_version"] == tasks.PROMPT_VERSION
    assert row["content"].strip()

    spec = tasks.TASKS[kind]
    if spec.schema is not None:
        spec.schema.model_validate_json(row["content"])  # the row is valid for its kind


def test_the_prompt_carries_the_timestamped_transcript(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    sent = calls[0].user
    assert "[0:00]" in sent  # the clock style the player already seeks by
    assert "Don't panic" in sent
    assert "Vogon poetry" in sent
    assert "Guide" in sent  # the recording's title, so the model knows what this is


def test_a_custom_task_needs_a_prompt_and_sends_it(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("custom"))
    register(monkeypatch, provider)

    with pytest.raises(ValueError):
        tasks.run_task(conn, media_id=media, kind="custom", provider_name="fake", model="fake-1")
    assert calls == []

    tasks.run_task(
        conn,
        media_id=media,
        kind="custom",
        provider_name="fake",
        model="fake-1",
        custom_prompt="List every towel mentioned.",
    )
    assert "List every towel mentioned." in calls[0].user


def test_a_chunked_custom_task_shows_every_chunk_reader_the_question(conn, monkeypatch):
    """The one thing a note-taker has to know is what the notes are for.

    A `custom` task over a recording too long for one call is a map-reduce like
    any other, and the chunk-level reader is told the *goal* rather than shown
    the transcript task. For the other five kinds the goal is a constant, and
    saying "list everything somebody agreed to do" is the whole instruction.
    For `custom` the goal *is* the user's question, and a generic "answer the
    question the user asked" tells the reader nothing about which question - so
    every note is taken blind and the combine call then answers a specific
    question from notes that were never about it.
    """
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    question = "What did they decide about the budget?"
    provider, calls = fake_provider(map_reduce_script(ANSWERS["custom"]))
    register(monkeypatch, provider)

    tasks.run_task(
        conn,
        media_id=media_id,
        kind="custom",
        provider_name="fake",
        model="fake-1",
        custom_prompt=question,
        budget_tokens=100,
    )

    chunk_calls = [c for c in calls if CHUNK_MARKER in c.user]
    assert len(chunk_calls) >= 2, "this transcript was supposed to need chunking"
    for call in chunk_calls:
        assert question in call.user


def test_a_second_custom_question_takes_its_own_notes(conn, monkeypatch):
    """Notes taken for question A are not notes about question B.

    The chunk rows are keyed on the work that produced them, and for `custom`
    the question is part of that work: without it in the key, asking a second
    thing about a long recording reuses every note taken for the first and
    makes only the combine call - a confident answer built from a reader who
    was asked something else.
    """
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    kwargs = dict(
        media_id=media_id,
        kind="custom",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )

    first, first_calls = fake_provider(map_reduce_script(ANSWERS["custom"]))
    register(monkeypatch, first)
    tasks.run_task(conn, custom_prompt="What did they decide about the budget?", **kwargs)

    again, again_calls = fake_provider(map_reduce_script(ANSWERS["custom"]))
    register(monkeypatch, again)
    tasks.run_task(conn, custom_prompt="Who spoke the most, and about what?", **kwargs)

    chunks = len(first_calls) - 1
    assert chunks >= 2
    assert len(again_calls) == chunks + 1, "the second question reused the first question's notes"
    # And the first question's notes survive: a re-run of *that* question is
    # still cheap, which is the whole point of storing them.
    third, third_calls = fake_provider(map_reduce_script(ANSWERS["custom"]))
    register(monkeypatch, third)
    tasks.run_task(conn, custom_prompt="What did they decide about the budget?", **kwargs)
    assert len(third_calls) == 1


def test_an_unknown_kind_is_refused_before_anything_is_read(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)

    with pytest.raises(ValueError) as exc:
        tasks.run_task(conn, media_id=media, kind="haiku", provider_name="fake", model="fake-1")

    assert "haiku" in str(exc.value)
    assert calls == []


# --- the key, and what it means ------------------------------------------------------------


def test_rerunning_the_same_task_adds_a_row_and_leaves_the_old_one(conn, media, monkeypatch):
    """A stored answer is evidence about what a model said on a day. Overwriting
    it would make the table a claim about the present instead of a record."""
    first_answer = json.dumps({"paragraph": "First take.", "bullets": ["one"]})
    second_answer = json.dumps({"paragraph": "Second take.", "bullets": ["two"]})
    provider, _calls = fake_provider([first_answer, second_answer])
    register(monkeypatch, provider)

    kwargs = dict(media_id=media, kind="summary", provider_name="fake", model="fake-1")
    first_id = tasks.run_task(conn, **kwargs)
    second_id = tasks.run_task(conn, **kwargs)

    stored = rows(conn, media, "summary")
    assert [r["id"] for r in stored] == [first_id, second_id]
    assert "First take." in stored[0]["content"]
    assert "Second take." in stored[1]["content"]
    assert stored[0]["created_at"] <= stored[1]["created_at"]


def test_the_row_records_the_run_the_transcript_came_from(conn, monkeypatch):
    """Which words were summarised is part of what an output means: a
    re-transcription with a better model is a different transcript."""
    media_id = seed_media(conn, title="Guide")
    run_id = seed_run(conn, media_id)
    provider, _calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media_id, kind="summary", provider_name="fake", model="fake-1")

    assert rows(conn, media_id)[0]["run_id"] == run_id


def test_the_row_records_the_tokens_the_provider_reported(conn, media, monkeypatch):
    provider, _calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    row = rows(conn, media)[0]
    assert row["prompt_tokens"] == 11
    assert row["completion_tokens"] == 7


def test_the_model_column_is_the_model_asked_for_and_the_served_one_is_kept(conn, media, monkeypatch):
    """The column is a key - the resume and the "same key" rule read it, and a
    provider that answers with a dated snapshot id must not split that key. What
    actually served the request is provenance, and goes in `params_json`."""
    provider, _calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    row = rows(conn, media)[0]
    assert row["model"] == "fake-1"
    assert json.loads(row["params_json"])["served_model"] == "fake-1-2026-09-02"


# --- the reasoning hint, and what reasoning cost (TASK-029) ------------------------------------

REASONING_KEYS = (
    "hint_sent",
    "reasoning_tokens",
    "reasoning_chars",
    "upstream",
    "hint_ignored",
    "cap_not_enforced",
)


def spent(
    text: str,
    *,
    completion_tokens: int | None = 7,
    hint_sent: bool | None = None,
    reasoning_tokens: int | None = None,
    reasoning_chars: int | None = None,
    upstream: str | None = None,
) -> base.ChatResponse:
    """An answer that says what it cost, the way the real providers now do."""
    return base.ChatResponse(
        text=text,
        model="fake-1-2026-09-02",
        provider="fake",
        prompt_tokens=11,
        completion_tokens=completion_tokens,
        raw_finish_reason="stop",
        hint_sent=hint_sent,
        reasoning_tokens=reasoning_tokens,
        reasoning_chars=reasoning_chars,
        upstream=upstream,
    )


def test_the_hint_follows_the_kind(conn, media, monkeypatch):
    """cleanup is the one kind that asks for no reasoning (TASK-029's
    decision): a mechanical rewrite that spent 22,515 reasoning tokens on 837
    words, and got the same cleaning in 1,171 tokens without. The rest keep
    the provider's default until they have measurements of their own.

    Every call a kind makes carries its hint - a part, a one-call answer, a
    note and a combine - which is proved for note and combine by giving
    summary the hint for the length of this test."""
    assert {kind: spec.reasoning_off for kind, spec in tasks.TASKS.items()} == {
        "summary": False,
        "action_items": False,
        "chapters": False,
        "minutes": False,
        "blog": False,
        "speakers": False,
        "labels": False,
        "cleanup": True,
        "custom": False,
    }

    long_id = seed_media(conn, title="Long one")
    seed_long_run(conn, long_id, n_segments=12)
    provider, calls = fake_provider(lambda req: "a part")
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")
    tasks.run_task(
        conn, media_id=long_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100
    )
    cleanup_calls = len(calls)
    assert cleanup_calls >= 3, "one single call and at least two parts"
    assert [c.reasoning_off for c in calls] == [True] * cleanup_calls

    notes_then_combine, summary_calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, notes_then_combine)
    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")
    tasks.run_task(
        conn, media_id=long_id, kind="summary", provider_name="fake", model="fake-1", budget_tokens=100
    )
    assert summary_calls and [c.reasoning_off for c in summary_calls] == [False] * len(summary_calls)

    monkeypatch.setitem(tasks.TASKS, "summary", replace(tasks.TASKS["summary"], reasoning_off=True))
    hinted, hinted_calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, hinted)
    tasks.run_task(
        conn, media_id=long_id, kind="summary", provider_name="fake", model="fake-2", budget_tokens=100
    )
    assert any(CHUNK_MARKER in c.user for c in hinted_calls), "notes were asked for"
    assert [c.reasoning_off for c in hinted_calls] == [True] * len(hinted_calls)


@pytest.mark.parametrize(
    "answer, cap, expected",
    [
        # Row 19, speakers at cap 8,000: 21,401 completion tokens, finish
        # 'stop'. That endpoint did not enforce the cap.
        (dict(completion_tokens=21401), 8000, dict(cap_not_enforced=True, hint_ignored=None)),
        (dict(completion_tokens=5000), 6000, dict(cap_not_enforced=False)),
        # The hint went on the wire and the model reasoned anyway.
        (
            dict(completion_tokens=1200, hint_sent=True, reasoning_tokens=500),
            6000,
            dict(hint_ignored=True, cap_not_enforced=False),
        ),
        (dict(hint_sent=True, reasoning_tokens=0), 6000, dict(hint_ignored=False)),
        # Refused and dropped: the model reasoned at its default, and that is
        # not the hint being ignored - it was never there to ignore.
        (dict(hint_sent=False, reasoning_tokens=500), 6000, dict(hint_ignored=None)),
        # Honoured, but the endpoint reported no count: nobody can say.
        (dict(hint_sent=True), 6000, dict(hint_ignored=None)),
        # Ollama counts thinking in characters.
        (dict(hint_sent=True, reasoning_chars=0), 6000, dict(hint_ignored=False)),
        (dict(hint_sent=True, reasoning_chars=12), 6000, dict(hint_ignored=True)),
        # No usage at all: both flags unknown, never False.
        (dict(completion_tokens=None), 6000, dict(hint_ignored=None, cap_not_enforced=None)),
    ],
    ids=[
        "past-the-cap",
        "under-the-cap",
        "hint-ignored",
        "hint-honoured",
        "hint-dropped",
        "hint-unmeasured",
        "ollama-honoured",
        "ollama-ignored",
        "no-usage",
    ],
)
def test_reasoning_record_says_what_happened_and_nothing_it_cannot_know(answer, cap, expected):
    record = tasks.reasoning_record(spent("x", **answer), cap)

    assert set(record) == set(REASONING_KEYS)
    for key, value in expected.items():
        assert record[key] is value, f"{key}: {record[key]!r}, expected {value!r}"


def test_every_row_records_what_was_sent_and_what_reasoning_cost(conn, media, monkeypatch):
    """Rows 6, 17 and 19 spent 5,651, 12,767 and 21,401 tokens against caps of
    4,000 and 8,000 and finished 'stop'; nothing on them said the cap had not
    held, or whether reasoning was the reason. Now every row says both.

    A note is judged against the note budget it was asked with, not the kind's
    answer cap: a 5,000-token note against a 4,000-token note budget broke the
    cap even though the same number would sit under blog's 6,000."""
    long_id = seed_media(conn, title="Long one")
    seed_long_run(conn, long_id, n_segments=12)

    def blog(req):
        if CHUNK_MARKER in req.user:
            return spent("notes", completion_tokens=5000, upstream="Wafer")
        return spent(ANSWERS["blog"], completion_tokens=21401, upstream="Wafer")

    provider, _ = fake_provider(blog)
    register(monkeypatch, provider)
    tasks.run_task(
        conn, media_id=long_id, kind="blog", provider_name="fake", model="fake-1", budget_tokens=100
    )

    notes = [r for r in rows(conn, long_id) if r["kind"].startswith("blog:chunk:")]
    assert notes
    for row in notes:
        params = json.loads(row["params_json"])
        assert set(REASONING_KEYS) <= set(params)
        assert params["upstream"] == "Wafer"
        assert params["hint_sent"] is None, "blog asks for no hint"
        assert tasks.MIN_NOTE_TOKENS < 5000 < tasks.TASKS["blog"].max_output_tokens
        assert params["cap_not_enforced"] is True, "judged against the note budget"
    (final,) = rows(conn, long_id, "blog")
    assert json.loads(final["params_json"])["cap_not_enforced"] is True

    parts, _ = fake_provider(
        lambda req: spent("a part", completion_tokens=1200, hint_sent=True, reasoning_tokens=500)
    )
    register(monkeypatch, parts)
    tasks.run_task(
        conn, media_id=long_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100
    )
    part_rows = [r for r in rows(conn, long_id) if r["kind"].startswith("cleanup:chunk:")]
    assert part_rows
    for row in part_rows:
        params = json.loads(row["params_json"])
        assert (params["hint_sent"], params["reasoning_tokens"], params["hint_ignored"]) == (
            True,
            500,
            True,
        )
        assert params["cap_not_enforced"] is False
    (joined,) = rows(conn, long_id, "cleanup")
    joined_params = json.loads(joined["params_json"])
    assert set(REASONING_KEYS) <= set(joined_params)
    assert all(joined_params[k] is None for k in REASONING_KEYS), "the parts carry the numbers"

    dropped, _ = fake_provider([spent("cleaned", hint_sent=False, reasoning_tokens=500)])
    register(monkeypatch, dropped)
    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")
    (single,) = rows(conn, media, "cleanup")
    params = json.loads(single["params_json"])
    assert (params["hint_sent"], params["reasoning_tokens"], params["hint_ignored"]) == (
        False,
        500,
        None,
    )


# --- long transcripts -----------------------------------------------------------------------


def test_a_long_transcript_makes_one_call_per_chunk_and_one_to_combine(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, calls = fake_provider(
        map_reduce_script(ANSWERS["summary"], note=lambda i: f"[0:0{i}] notes from excerpt {i}")
    )
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,  # four ten-word segments per chunk, as in the chunking tests
    )

    chunk_rows = [r for r in rows(conn, media_id) if r["kind"].startswith("summary:chunk:")]
    final = [r for r in rows(conn, media_id) if r["kind"] == "summary"]
    assert len(chunk_rows) >= 2
    assert len(calls) == len(chunk_rows) + 1
    assert [r["id"] for r in final] == [output_id]
    assert [r["kind"] for r in chunk_rows] == [
        f"summary:chunk:{i}" for i in range(len(chunk_rows))
    ]
    # The combine call sees the notes, not the transcript again.
    assert "notes from excerpt 0" in calls[-1].user
    assert "word00" not in calls[-1].user


def test_chunk_rows_say_which_part_of_the_recording_they_cover(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, _calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)

    tasks.run_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )

    chunk_rows = [r for r in rows(conn, media_id) if r["kind"].startswith("summary:chunk:")]
    assert len(chunk_rows) >= 2
    for i, row in enumerate(chunk_rows):
        params = json.loads(row["params_json"])
        assert params["chunk"] == i
        assert params["segment_ids"]
        assert params["end"] >= params["start"]
        assert row["content"] == f"notes {i}"


def truncated(text: str) -> base.ChatResponse:
    """A note the model was still writing when it hit its cap."""
    return base.ChatResponse(
        text=text,
        model="fake-1-2026-09-02",
        provider="fake",
        prompt_tokens=11,
        completion_tokens=7,
        raw_finish_reason="length",
    )


def test_chunk_rows_record_how_the_model_stopped(conn, monkeypatch):
    """The final row records `finish_reason`; a chunk row did not, and a chunk
    row is the one that gets reused without anybody looking at it."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, _calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)

    tasks.run_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )

    chunk_rows = [r for r in rows(conn, media_id) if tasks.is_chunk_kind(r["kind"])]
    assert chunk_rows
    for row in chunk_rows:
        assert json.loads(row["params_json"])["finish_reason"] == "stop"


def test_a_note_cut_off_mid_sentence_is_not_stored_and_not_reused(conn, monkeypatch):
    """A truncated note is half a sentence, and it is cached forever.

    Emptiness was the only thing checked, so a note that stopped mid-word
    because it hit its cap was stored as a complete answer - and then reused by
    every later run of that task, including the ones asked precisely because
    the answer looked wrong. Not caching it is the load-bearing half: a
    `BadResponse` a retry can actually recover from.
    """
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    kwargs = dict(
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )
    cut_off, first_calls = fake_provider(
        map_reduce_script(ANSWERS["summary"], note=lambda i: truncated(f"notes {i} which stop mid-"))
    )
    register(monkeypatch, cut_off)

    with pytest.raises(base.BadResponse) as exc:
        tasks.run_task(conn, **kwargs)

    assert "length" in str(exc.value) or "room" in str(exc.value)
    assert rows(conn, media_id) == [], "a note the model never finished was stored anyway"

    # And the retry it invites actually retries: nothing was cached, so the
    # chunk is asked again rather than answered from the truncated note.
    whole, again_calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, whole)
    tasks.run_task(conn, **kwargs)

    assert len(again_calls) > 1, "the retry reused the truncated note"
    assert len(rows(conn, media_id, "summary")) == 1


def test_a_cut_off_note_names_what_it_spent_and_whether_the_hint_went(conn, monkeypatch):
    """A refused note writes no row either, so, like a refused cleanup part,
    its error is the jobs board's only record of the call and has to carry
    the spend, the upstream and the hint - or a reasoner that ran out of room
    reads the same as a runaway."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    spent = dict(completion_tokens=400, reasoning_tokens=388, upstream="Azure", hint_sent=None)
    cut_off, _ = fake_provider(
        map_reduce_script(
            ANSWERS["summary"],
            note=lambda i: replace(truncated(f"notes {i} which stop mid-"), **spent),
        )
    )
    register(monkeypatch, cut_off)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(
            conn, media_id=media_id, kind="summary", provider_name="fake", model="fake-1",
            budget_tokens=100,
        )

    message = str(caught.value)
    assert "finish_reason='length'" in message
    missing = [words for words in ("completion_tokens=400", "reasoning_tokens=388", "upstream='Azure'")
               if words not in message]
    assert missing == [], message


def test_a_one_call_custom_answer_cut_off_at_the_cap_is_still_stored(conn, media, monkeypatch):
    """The refusal of a cut-off one-call answer belongs to the concat kinds,
    whose answer is a stretch of the transcript. `custom` keeps today's rule:
    the row is stored and the panel marks it (`ai_ui.was_truncated`), because
    half an answer to a free question is still worth reading once the page
    says where it stopped."""
    provider, calls = fake_provider([truncated("They agreed to raise the budget by")])
    register(monkeypatch, provider)

    tasks.run_task(
        conn,
        media_id=media,
        kind="custom",
        provider_name="fake",
        model="fake-1",
        custom_prompt="What did they decide about the budget?",
    )

    (row,) = rows(conn, media, "custom")
    assert len(calls) == 1
    assert row["content"] == "They agreed to raise the budget by"
    assert json.loads(row["params_json"])["finish_reason"] == "length"


def test_a_resumed_task_only_makes_the_calls_whose_chunk_rows_are_missing(conn, monkeypatch):
    """The reason chunk answers are rows at all: an interrupted map-reduce over
    a four-hour recording must not pay for the chunks it already bought."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    kwargs = dict(
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )

    # A BadResponse rather than a transient failure, so the count below is the
    # number of chunks that were asked for and not three attempts at one of them:
    # `with_retry` retries Unreachable, and this test is about resume, not retry.
    def dies_on_the_third(i):
        return base.BadResponse("nothing came back") if i == 2 else f"notes {i}"

    first, first_calls = fake_provider(
        map_reduce_script(ANSWERS["summary"], note=dies_on_the_third)
    )
    register(monkeypatch, first)
    with pytest.raises(base.BadResponse):
        tasks.run_task(conn, **kwargs)
    made_before = len(first_calls)
    stored_before = [r for r in rows(conn, media_id) if r["kind"].startswith("summary:chunk:")]
    assert len(stored_before) == 2

    resumed, resumed_calls = fake_provider(
        map_reduce_script(ANSWERS["summary"], note=lambda i: f"notes {i + 2}")
    )
    register(monkeypatch, resumed)
    tasks.run_task(conn, **kwargs)

    chunk_rows = [r for r in rows(conn, media_id) if r["kind"].startswith("summary:chunk:")]
    # Every chunk has exactly one row: the two from the first attempt were reused.
    assert [r["kind"] for r in chunk_rows] == [
        f"summary:chunk:{i}" for i in range(len(chunk_rows))
    ]
    assert len(resumed_calls) == len(chunk_rows) - 2 + 1
    assert made_before == 3  # two answers and the failure
    assert rows(conn, media_id, "summary")


def test_a_resumed_task_ignores_chunk_rows_from_another_run(conn, monkeypatch):
    """Chunk 3 of a re-transcribed recording is not chunk 3 of the old one: the
    seams move with the segments. Reusing across runs would combine notes about
    text that is no longer there."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    kwargs = dict(
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )
    first, _ = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, first)
    tasks.run_task(conn, **kwargs)
    n_chunks = len([r for r in rows(conn, media_id) if r["kind"].startswith("summary:chunk:")])

    seed_long_run(conn, media_id, n_segments=12)  # a second transcription, now current

    again, again_calls = fake_provider(
        map_reduce_script(ANSWERS["summary"], note=lambda i: f"fresh {i}")
    )
    register(monkeypatch, again)
    tasks.run_task(conn, **kwargs)

    assert len(again_calls) == n_chunks + 1  # nothing reused across the two runs


def test_rerunning_a_chunked_task_reuses_its_notes_and_only_combines_again(conn, monkeypatch):
    """The consequence of keying the notes on the work rather than on the job:
    resuming an interruption and deliberately re-running are the same operation.
    A second run gets a new final row - nothing is overwritten - but it is built
    from the notes that were already paid for, which is what a "regenerate"
    button will feel like on a long recording."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    kwargs = dict(
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )
    first, first_calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, first)
    tasks.run_task(conn, **kwargs)

    again, again_calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, again)
    tasks.run_task(conn, **kwargs)

    assert len(first_calls) > 1
    assert len(again_calls) == 1, "the second run asked a chunk again"
    assert len(rows(conn, media_id, "summary")) == 2


def test_a_window_too_small_to_combine_the_notes_is_refused_before_any_call(conn, monkeypatch):
    """A map-reduce that cannot end is refused before it starts.

    The chunk notes have a floor (`MIN_NOTE_TOKENS`, measured: below it the
    shipped local model spends its whole budget thinking and answers nothing).
    On a small window a long recording needs so many chunks that the notes at
    that floor cannot fit back into one combine call - and the old order of
    operations paid for every map call *first* and only then discovered it,
    around 35 s each on `qwen3.5:4b`. Worse, the notes were then cached, so
    every retry refused instantly without making a single new call: the task
    was permanently unrecoverable under that key.

    The arithmetic is knowable before the first call, so it is done there.
    """
    media_id = seed_media(conn, title="A long meeting")
    seed_long_run(conn, media_id, n_segments=150, words_per=50)
    provider, calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)

    with pytest.raises(base.ContextTooLong) as exc:
        tasks.run_task(
            conn,
            media_id=media_id,
            kind="summary",
            provider_name="fake",
            model="fake-1",
            context_tokens=tasks.LOCAL_CONTEXT_TOKENS,
        )

    assert calls == [], "the refusal came after the map calls were already paid for"
    assert rows(conn, media_id) == [], "notes were stored for a task that cannot finish"
    assert str(tasks.LOCAL_CONTEXT_TOKENS) in str(exc.value)


def test_a_window_that_can_combine_the_notes_is_not_refused(conn, monkeypatch):
    """The other side of the same check, on the same recording.

    Without this, "refuse when the notes cannot fit" is one arithmetic slip
    away from "refuse every chunked task": the note budget and the combine
    capacity are within a few tokens of each other by construction, so a check
    that forgot the floor divide would turn a working cloud map-reduce into a
    refusal and nothing here would notice.
    """
    media_id = seed_media(conn, title="A long meeting")
    seed_long_run(conn, media_id, n_segments=150, words_per=50)
    provider, calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)

    tasks.run_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        context_tokens=16_000,
    )

    chunk_rows = [r for r in rows(conn, media_id) if tasks.is_chunk_kind(r["kind"])]
    assert len(chunk_rows) >= 2
    assert len(calls) == len(chunk_rows) + 1


@pytest.mark.parametrize("chunks", [2, 3, 4, 8, 16])
@pytest.mark.parametrize("kind", sorted(tasks.TASKS))
def test_the_shipped_cloud_window_admits_a_chunked_task(kind, chunks):
    """The other half of the refusal, on the path that ships.

    `combine_capacity` and `budget_tokens` differ only by the few tokens
    `map_combine.md` wraps around the task prompt, so a floor divide that
    forgot the wrapper would refuse *every* chunked cloud task - strictly worse
    than the bug being fixed, and invisible to a test that only feeds it a
    window too small. Sixteen chunks at this window is about 2M tokens of
    transcript; past that the refusal is correct.
    """
    spec = tasks.TASKS[kind]
    asked = "What did they decide about the budget?" if spec.needs_prompt else None
    shared = dict(
        context_tokens=tasks.CLOUD_CONTEXT_TOKENS,
        max_output_tokens=spec.max_output_tokens,
        title="A long meeting",
        duration=9000.0,
        custom_prompt=asked,
    )
    budget = tasks.budget_for(spec, **shared)

    note_budget = tasks.plan_notes(spec, chunks=chunks, budget_tokens=budget, **shared)

    assert note_budget >= tasks.MIN_NOTE_TOKENS
    assert note_budget * chunks <= tasks.combine_capacity(spec, chunks=chunks, **shared)


def test_the_note_budget_checked_is_the_note_budget_spent(conn, monkeypatch):
    """The number `plan_task` proved would fit is the number `generate` asks
    for. Two computations of one budget is how a check passes on one value and
    the calls go out with another."""
    media_id = seed_media(conn, title="A long meeting")
    seed_long_run(conn, media_id, n_segments=150, words_per=50)
    provider, calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)

    plan = tasks.plan_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        context_tokens=16_000,
    )
    tasks.generate(conn, plan)

    chunk_calls = [c for c in calls if CHUNK_MARKER in c.user]
    assert plan.note_budget >= tasks.MIN_NOTE_TOKENS
    assert plan.note_budget * len(plan.chunks) <= tasks.combine_capacity(
        plan.spec,
        context_tokens=16_000,
        max_output_tokens=plan.max_output_tokens,
        chunks=len(plan.chunks),
        title=plan.title,
        duration=plan.duration,
    )
    assert {c.max_output_tokens for c in chunk_calls} == {plan.note_budget}


def test_outputs_for_lists_finished_answers_and_not_the_working_notes(conn, monkeypatch):
    """What the panel reads. A chunk row is scaffolding: listing it would show a
    user the inside of the map-reduce and call it an answer."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, _calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)
    tasks.run_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
    )

    listed = tasks.outputs_for(conn, media_id, "summary")

    assert [row["kind"] for row in listed] == ["summary"]
    assert not any(tasks.is_chunk_kind(row["kind"]) for row in listed)
    assert tasks.is_chunk_kind("summary:chunk:0") and not tasks.is_chunk_kind("summary")


def test_progress_is_reported_once_per_call_and_ends_at_one(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)
    seen: list[float] = []

    tasks.run_task(
        conn,
        media_id=media_id,
        kind="summary",
        provider_name="fake",
        model="fake-1",
        budget_tokens=100,
        on_progress=seen.append,
    )

    assert len(seen) == len(calls)
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


def test_a_transcript_that_fits_is_one_call_and_no_chunk_rows(conn, media, monkeypatch):
    """The direction the chunker's `needs_chunking` promises: a short recording
    costs one call and leaves one row, not a map-reduce over a single chunk."""
    provider, calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    assert len(calls) == 1
    assert [r["kind"] for r in rows(conn, media)] == ["summary"]


def test_a_media_without_a_transcript_is_refused_before_a_call(conn, monkeypatch):
    from scribe.exports import doc as docs

    media_id = seed_media(conn, title="Not transcribed")
    provider, calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)

    with pytest.raises(docs.NoTranscript):
        tasks.run_task(conn, media_id=media_id, kind="summary", provider_name="fake", model="fake-1")
    assert calls == []


# --- what comes back, and what to do about it ------------------------------------------------


def test_a_fenced_json_answer_is_repaired(conn, media, monkeypatch):
    """Every chat model has been trained to fence its code. The fence is not
    part of the answer, and a task that failed on one would fail on most local
    models most of the time."""
    fenced = "```json\n" + ANSWERS["summary"] + "\n```"
    provider, _calls = fake_provider([fenced])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    row = rows(conn, media)[0]
    assert json.loads(row["content"])["bullets"]
    assert "```" not in row["content"]


def test_prose_around_the_object_is_recovered(conn, media, monkeypatch):
    chatty = "Certainly! Here is the summary you asked for:\n\n" + ANSWERS["summary"] + "\n\nHope that helps!"
    provider, _calls = fake_provider([chatty])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    assert json.loads(rows(conn, media)[0]["content"])["paragraph"].startswith("Two travellers")


def test_prose_where_json_was_required_fails_with_the_text_preserved(conn, media, monkeypatch):
    """The failure a user has to be able to read: the job's `error_detail` is
    the only place the model's actual words survive, and "it did not parse"
    without them is unactionable."""
    prose = "I'm afraid I can't summarise that, but I can tell you about Vogon poetry."
    provider, _calls = fake_provider([prose])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as exc:
        tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    assert "Vogon poetry" in str(exc.value)
    assert rows(conn, media) == []  # nothing invalid was stored


def test_json_that_does_not_match_the_schema_fails_with_its_text(conn, media, monkeypatch):
    wrong = json.dumps({"summary": "wrong field names", "points": []})
    provider, _calls = fake_provider([wrong])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as exc:
        tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    assert "wrong field names" in str(exc.value)


def test_an_empty_answer_is_a_bad_response_rather_than_an_empty_row(conn, media, monkeypatch):
    provider, _calls = fake_provider(["   \n  "])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse):
        tasks.run_task(
            conn,
            media_id=media,
            kind="custom",
            provider_name="fake",
            model="fake-1",
            custom_prompt="Say something.",
        )
    assert rows(conn, media) == []


def test_a_clock_timestamp_is_read_as_seconds(conn, media, monkeypatch):
    """Models cite `[1:23]` because that is what the transcript shows them. A
    schema that only accepted a number would turn the most likely answer into a
    `BadResponse`."""
    spoken = json.dumps({"chapters": [{"start": "0:00", "title": "Towels"}, {"start": "1:23", "title": "Later"}]})
    provider, _calls = fake_provider([spoken])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="chapters", provider_name="fake", model="fake-1")

    stored = json.loads(rows(conn, media)[0]["content"])
    assert [c["start"] for c in stored["chapters"]] == [0.0, 83.0]


# --- schema-constrained decoding ------------------------------------------------------------


def test_the_schema_goes_on_the_wire_only_where_the_provider_takes_one(conn, media, monkeypatch):
    """A capability read off the provider class, never a check on its name: a
    provider added later is covered by whichever branch its own attribute says."""
    strict, strict_calls = fake_provider(answer_for("summary"), name="strict", supports_json_schema=True)
    loose, loose_calls = fake_provider(answer_for("summary"), name="loose", supports_json_schema=False)
    register(monkeypatch, strict)
    register(monkeypatch, loose)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="strict", model="strict-1")
    tasks.run_task(conn, media_id=media, kind="summary", provider_name="loose", model="loose-1")

    assert strict_calls[0].json_schema is not None
    assert "paragraph" in json.dumps(strict_calls[0].json_schema)
    assert loose_calls[0].json_schema is None


def test_the_prompt_asks_for_json_even_when_the_schema_is_sent(conn, media, monkeypatch):
    """Belt and braces on purpose: `response_format` constrains the grammar, and
    the instruction stops a model wrapping a valid object in an apology."""
    strict, calls = fake_provider(answer_for("summary"), name="strict", supports_json_schema=True)
    register(monkeypatch, strict)

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="strict", model="strict-1")

    asked = (calls[0].system + calls[0].user).lower()
    assert "json" in asked


def every_object(node):
    """Every object node in a JSON Schema, `$defs` included."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield node
        for value in node.values():
            yield from every_object(value)
    elif isinstance(node, list):
        for item in node:
            yield from every_object(item)


SCHEMA_KINDS = sorted(kind for kind, spec in tasks.TASKS.items() if spec.schema is not None)


@pytest.mark.parametrize("kind", SCHEMA_KINDS)
def test_the_schema_sent_is_the_strict_one_the_endpoint_demands(kind):
    """Found against the live API, not in a fake: a pydantic schema sent as
    pydantic writes it came back HTTP 400 from `openai/gpt-5.6-luna` through
    OpenRouter - *"'additionalProperties' is required to be supplied and to be
    false"*. A structured-output endpoint validates against OpenAI's strict
    subset, and pydantic does not write that subset.

    Every kind, not the one that was caught: `chapters` and `minutes` carry
    shapes `action_items` does not - a nested model in `$defs`, a defaulted
    list - and the round trip that found this cost a real 400.
    """
    payload = tasks.schema_payload(tasks.TASKS[kind], True)

    assert payload["name"] == kind
    assert payload["strict"] is True
    schema = payload["schema"]

    objects = list(every_object(schema))
    assert objects, f"{kind} has no object in its schema"
    for node in objects:
        assert node["additionalProperties"] is False
        # Strict mode has no optional keys: an optional field stays optional by
        # being nullable, which `owner` and `evidence_ts` already are.
        assert sorted(node["required"]) == sorted(node.get("properties") or {})
    assert '"default"' not in json.dumps(schema)


def test_the_nested_models_reach_the_strict_rewrite_too():
    """`$defs` is where a nested model lands, and an object the walk did not
    reach is an object the endpoint will reject."""
    schema = tasks.schema_payload(tasks.TASKS["minutes"], True)["schema"]

    assert "$defs" in schema
    for name, node in schema["$defs"].items():
        assert node["additionalProperties"] is False, f"$defs.{name} was not rewritten"


def test_the_strict_rewrite_leaves_the_pydantic_model_alone():
    """The rewrite is about the wire format, not the data: `parse_answer` still
    validates with pydantic's own rules, which is what makes a provider without
    constrained decoding produce the same objects as one with it."""
    tasks.schema_payload(tasks.TASKS["action_items"], True)

    fresh = tasks.ActionItems.model_json_schema()
    assert "additionalProperties" not in fresh
    assert tasks.ActionItems.model_validate({"items": [{"text": "still optional"}]}).items[0].owner is None


def test_a_custom_task_never_sends_a_schema(conn, media, monkeypatch):
    strict, calls = fake_provider(answer_for("custom"), name="strict", supports_json_schema=True)
    register(monkeypatch, strict)

    tasks.run_task(
        conn,
        media_id=media,
        kind="custom",
        provider_name="strict",
        model="strict-1",
        custom_prompt="Anything you like.",
    )

    assert calls[0].json_schema is None


# --- the pin, from here too --------------------------------------------------------------------


def test_a_private_media_refuses_a_cloud_task_before_a_call_is_made(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("summary"), is_local=False)
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media,))
        conn.commit()

    with pytest.raises(llm.PrivacyRefused):
        tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    assert calls == []
    assert rows(conn, media) == []


def test_a_private_media_still_runs_on_a_local_provider(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("summary"), name="localfake", is_local=True)
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media,))
        conn.commit()

    tasks.run_task(conn, media_id=media, kind="summary", provider_name="localfake", model="localfake-1")

    assert len(calls) == 1
    assert rows(conn, media)[0]["provider"] == "localfake"


# --- the job type ---------------------------------------------------------------------------------


def test_the_runner_knows_the_llm_job_type_and_its_four_stages():
    """`apply` joined the three in TASK-024. It is a no-op for the kinds that
    only answer a question, and the step that used to be a person clicking for
    the kinds that change something."""
    assert [name for name, _fn in runner.STAGES["llm"]] == [
        "prepare",
        "generate",
        "store",
        "apply",
    ]
    assert runner.STAGES["llm"] is llm_stage.STAGES
    assert all(callable(fn) for _name, fn in llm_stage.STAGES)


def test_an_llm_job_runs_end_to_end_and_leaves_one_output_row(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=media,
        params={"media_id": media, "kind": "summary", "provider": "fake", "model": "fake-1"},
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert len(calls) == 1
    stored = rows(conn, media)
    assert [r["kind"] for r in stored] == ["summary"]
    stage_names = [
        e["payload"]["name"] for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "stage"
    ]
    assert stage_names == ["prepare", "generate", "store", "apply"]
    finished = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "llm"]
    assert finished and finished[-1]["payload"]["output_id"] == stored[0]["id"]


def test_an_llm_job_passes_a_custom_prompt_through_its_params(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("custom"))
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=media,
        params={
            "media_id": media,
            "kind": "custom",
            "provider": "fake",
            "model": "fake-1",
            "prompt": "Name every planet mentioned.",
        },
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert "Name every planet mentioned." in calls[0].user
    assert rows(conn, media)[0]["kind"] == "custom"


def test_an_llm_job_for_a_private_media_fails_without_making_a_call(conn, media, monkeypatch):
    """The pin has to hold at the job level too - a queued job is the one path
    where nobody is watching the screen when it runs."""
    provider, calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media,))
        conn.commit()
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=media,
        params={"media_id": media, "kind": "summary", "provider": "fake", "model": "fake-1"},
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error_code"] == "PRIVACY_REFUSED"
    assert calls == []
    assert rows(conn, media) == []


def test_a_job_that_cannot_combine_its_notes_fails_in_prepare_without_spending(conn, monkeypatch):
    """Where the plan says such a refusal belongs: `prepare`, the stage whose
    whole job is to catch what costs nothing to catch. The board says `prepare`,
    so a user reading it knows nothing was bought."""
    media_id = seed_media(conn, title="A long meeting")
    seed_long_run(conn, media_id, n_segments=150, words_per=50)
    provider, calls = fake_provider(map_reduce_script(ANSWERS["summary"]))
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=media_id,
        params={
            "media_id": media_id,
            "kind": "summary",
            "provider": "fake",
            "model": "fake-1",
            "context_tokens": tasks.LOCAL_CONTEXT_TOKENS,
        },
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["stage"] == "prepare"
    assert row["error_code"] == "LLM_FAILED"
    assert calls == []
    assert rows(conn, media_id) == []


def test_a_job_whose_model_answers_badly_fails_with_the_text_on_the_board(conn, media, monkeypatch):
    provider, _calls = fake_provider(["Sorry, I would rather recite Vogon poetry."])
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=media,
        params={"media_id": media, "kind": "summary", "provider": "fake", "model": "fake-1"},
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["error_code"] == "LLM_FAILED"
    assert "Vogon poetry" in row["error_detail"]


def test_a_job_without_a_kind_says_so_rather_than_guessing(conn, media, monkeypatch):
    provider, calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)
    job_id = jobs.enqueue(conn, "llm", media_id=media, params={"media_id": media})
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "kind" in (row["error_detail"] or "")
    assert calls == []


def test_a_cancelled_job_stops_between_calls_and_keeps_what_it_paid_for(conn, monkeypatch):
    """Between calls is the only place a cancel can be honoured - a request
    already with a provider is not ours to interrupt - and a job that stopped
    after four of twenty chunks must not throw those four away."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=media_id,
        params={
            "media_id": media_id,
            "kind": "summary",
            "provider": "fake",
            "model": "fake-1",
            "budget_tokens": 100,
        },
    )

    def cancel_after_the_first(req):
        answer = f"notes for call {len(calls)}"
        if len(calls) == 1:
            jobs.request_cancel(conn, job_id)
        return answer

    provider, calls = fake_provider(cancel_after_the_first)
    register(monkeypatch, provider)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 2

    row = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "cancelled"
    assert len(calls) == 1  # the second chunk was never asked for
    chunk_rows = [r for r in rows(conn, media_id) if r["kind"].startswith("summary:chunk:")]
    assert [r["kind"] for r in chunk_rows] == ["summary:chunk:0"]
    assert rows(conn, media_id, "summary") == []  # no final answer was invented


def test_the_llm_stages_file_their_timings_under_the_model_that_answered(conn, media, monkeypatch):
    """Task 7 reads these back to learn how slow a local model is on this
    machine, so the stage_perf rows have to name the LLM model, not a Whisper
    tier."""
    provider, _calls = fake_provider(answer_for("summary"))
    register(monkeypatch, provider)
    job_id = jobs.enqueue(
        conn,
        "llm",
        media_id=media,
        params={"media_id": media, "kind": "summary", "provider": "fake", "model": "fake-1"},
    )
    jobs.claim_next(conn)
    assert runner.main([str(job_id)]) == 0

    perf = [dict(r) for r in conn.execute("SELECT stage, model FROM stage_perf ORDER BY id")]
    assert [r["stage"] for r in perf] == ["prepare", "generate", "store", "apply"]
    assert {r["model"] for r in perf} == {"fake-1"}
