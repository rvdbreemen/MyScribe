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
import re
from dataclasses import replace

import pytest

from scribe import db, jobs, llm, paths, runner
from scribe.llm import base, chat_tool, selftest, tasks
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


PROMPTS_DIGEST = "8caa7d01408d090994a9955f80b707e67078ef0049aadb9c5cb8492d0f75cfaf"
"""sha256 over everything a prompt is made of, for PROMPT_VERSION "6": the
templates in prompts/, SYSTEM and JSON_SYSTEM, every kind's goal and note
rule, and the source line `source_label` writes.

Version "6" (TASK-126, 2026-10-25): map_chunk.md gained the second ask of a
note that broke `MAX_NOTE_POINTS` or ran out of its budget (the `again`
slot). The note rules render the same text, now from the constant. Was
80f59da4... under "5".

Version "5" (TASK-124, 2026-10-25): minutes, action_items and chapters got a
note rule like blog's (points in their own words, no line copied from the
excerpt), after live notes on media 61 copied the excerpt. Was fe13c07f...
under "4".

Version "4" (TASK-124, 2026-10-25): map_chunk.md gained the note-rule slot,
blog got a new goal and a note rule (points in its own words, no line copied
from the excerpt). Was a310926f... under "3". Line endings are normalised first, because
git rewrites them on checkout here.

Until TASK-105.02 (2026-10-06) only the templates were hashed, so a change to
SYSTEM or a goal string reused answers to the old question. Recording the
wider digest moved nothing: the material is what version "2" already asked.
Version "3" (TASK-105.01, the same day) added LANGUAGE_SYSTEM: answers now
come in the transcript's language. Moved again within "3", before it was
released, by TASK-105.03 (map_chunk keeps the speaker): no answer was ever
stored under the version in between.

Moved once without a bump, on 2026-09-06, when `speakers.md` and `cleanup.md`
were *added*: the six templates that existed were byte-identical before and
after, so every stored answer is still an answer to the question its row
names. Adding a kind is not editing a question."""


def prompt_material() -> bytes:
    """Every piece of text a prompt is built from, in a fixed order."""
    parts: list[str] = []
    for path in sorted(tasks.PROMPTS_DIR.glob("*.md")):
        parts += [path.name, path.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))]
    parts += ["SYSTEM", tasks.SYSTEM, "JSON_SYSTEM", tasks.JSON_SYSTEM, "LANGUAGE_SYSTEM", tasks.LANGUAGE_SYSTEM]
    for kind in sorted(tasks.TASKS):
        parts += [f"goal:{kind}", tasks.TASKS[kind].goal]
        if tasks.TASKS[kind].note_rule:  # only set kinds, so version "3"'s material is unchanged
            parts += [f"note_rule:{kind}", tasks.TASKS[kind].note_rule]
    parts += ["source_label", tasks.source_label("Title", 61.0),
              tasks.source_label("Title", 61.0, notes_from=3)]
    return chr(0).join(parts).encode("utf-8")


def test_editing_a_prompt_means_bumping_the_prompt_version():
    """`PROMPT_VERSION` is part of the key every answer is stored under, so that
    answers to two different questions never sit in one list pretending to be
    comparable. The plan states the rule; without something that fails, it is a
    rule nobody is told they broke.

    If this fails and you did edit a prompt - a template, SYSTEM, JSON_SYSTEM,
    LANGUAGE_SYSTEM, a goal, a note rule, the source line: bump `tasks.PROMPT_VERSION`, then put the new digest above. If
    you did not edit one, something else did.

    *Adding* a kind is the other case, and it has the opposite answer: record
    the new digest and leave the version alone. A new kind has no stored
    answers to be confused with, while bumping the version would change the key
    of every *other* kind - orphaning answers that were paid for and are still
    answers to exactly the question that was asked. TASK-023 added `labels.md`
    on 2026-09-10 and did not bump.
    """
    digest = hashlib.sha256(prompt_material()).hexdigest()

    assert digest == PROMPTS_DIGEST, (
        f"a prompt changed while PROMPT_VERSION is still "
        f"{tasks.PROMPT_VERSION!r}: bump it, then record the new digest ({digest})"
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
    "reasoning_cap",
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


@pytest.mark.parametrize("is_local", [True, False])
def test_a_blog_asks_only_its_combine_not_to_reason_and_only_on_a_local_model(
    conn, media, monkeypatch, is_local
):
    """Robert's decisions of 2026-10-25 (TASK-124, ADR-035): blog's combine
    carries the reasoning hint on a local model. On media 61 qwen3.5:4b spent
    16,866 characters thinking in the combine of job 430 on the round-5 library
    copy and stopped at the 6000-token cap with half a post. The notes keep the
    provider's default - they were not where the budget went - and so does a
    recording short enough for one call, which has no combine. A cloud model
    keeps ADR-030's behaviour: no hint, so the reasoning limit is sent."""
    assert {kind: spec.combine_reasoning_off for kind, spec in tasks.TASKS.items()} == {
        "summary": False,
        "action_items": False,
        "chapters": False,
        "minutes": False,
        "blog": True,
        "speakers": False,
        "labels": False,
        "cleanup": False,
        "custom": False,
    }

    long_id = seed_media(conn, title="Long one")
    seed_long_run(conn, long_id, n_segments=12)
    provider, calls = fake_provider(map_reduce_script(ANSWERS["blog"]), is_local=is_local)
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="blog", provider_name="fake", model="fake-1")
    assert [c.reasoning_off for c in calls] == [False], "one call, no combine, no hint"

    del calls[:]
    # A window as roomy as Ollama's 32768 is in practice, so the local plan is
    # not refused for the 8192 default before the hint is ever asked about.
    tasks.run_task(
        conn, media_id=long_id, kind="blog", provider_name="fake", model="fake-1",
        budget_tokens=100, context_tokens=32768,
    )
    notes = [c for c in calls if CHUNK_MARKER in c.user]
    combines = [c for c in calls if CHUNK_MARKER not in c.user]
    assert len(notes) >= 2 and len(combines) == 1
    assert [c.reasoning_off for c in notes] == [False] * len(notes)
    assert combines[0].reasoning_off is is_local


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


def test_the_row_and_a_cut_off_refusal_name_the_reasoning_limit_sent():
    """TASK-123: OpenRouter calls carry a reasoning limit, and the rerun that
    proves it helped has to be able to show it went out - on the row
    (`reasoning_record`) and in a refusal, which writes no row."""
    answer = replace(spent("x", completion_tokens=6000, reasoning_tokens=2400), reasoning_cap=2400)

    assert tasks.reasoning_record(answer, 6000)["reasoning_cap"] == 2400
    assert tasks.reasoning_record(spent("x"), 6000)["reasoning_cap"] is None
    assert "reasoning_cap=2400" in tasks._spent(answer)


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
    """A map-reduce that cannot end is still refused before it starts.

    The rule this pins moved on 2026-09-19 and the reason it exists did not.
    It used to refuse whenever notes at `MIN_NOTE_TOKENS` would not fit back
    through the combine call; now `plan_notes` shortens the notes to what the
    window affords, because a terse note is a real answer where a refusal is
    not. What still cannot be done is a recording so long that even
    `MIN_NOTE_FLOOR` per chunk does not fit - so the recording here is longer
    than it was, and the refusal is the same refusal: before the first call,
    with nothing bought and nothing cached.

    The original reason, unchanged: the old order paid for every map call
    *first* (around 35 s each on `qwen3.5:4b`), then discovered the combine
    could not happen, and cached the notes - so every retry refused instantly
    without a new call and the task was permanently unrecoverable.
    """
    media_id = seed_media(conn, title="A very long meeting")
    seed_long_run(conn, media_id, n_segments=3000, words_per=50)
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
    the calls go out with another.

    24,000 tokens rather than the 16,000 this had before TASK-124: `word07`
    costs four tokens now (its digits are a token each), so 16,000 no longer
    leaves a full note per chunk, which is the case this test is about."""
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
        context_tokens=24_000,
    )
    tasks.generate(conn, plan)

    chunk_calls = [c for c in calls if CHUNK_MARKER in c.user]
    assert plan.note_budget >= tasks.MIN_NOTE_TOKENS
    assert plan.note_budget * len(plan.chunks) <= tasks.combine_capacity(
        plan.spec,
        context_tokens=24_000,
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


CLOCK_FIELDS = {"action_items": ["items", "evidence_ts"], "minutes": ["actions", "evidence_ts"],
                "chapters": ["chapters", "start"]}


def clock_node(schema: dict, kind: str) -> dict:
    """The schema node of the one timestamp field `kind` carries."""
    array, field = CLOCK_FIELDS[kind]
    ref = schema["properties"][array]["items"]["$ref"].rsplit("/", 1)[-1]
    return schema["$defs"][ref]["properties"][field]


def types_of(node: dict) -> set[str]:
    return {node["type"]} if "type" in node else {alt["type"] for alt in node["anyOf"]}


@pytest.mark.parametrize("kind", sorted(CLOCK_FIELDS))
def test_a_grammar_provider_is_never_offered_a_number_for_a_timestamp(kind):
    """TASK-124, ADR-034, measured 2026-10-25 on Ollama 0.40.8 with qwen3.5:4b:
    with `evidence_ts` typed `number` (or `number` or text) the grammar let the
    model start `2:16:10` as the number 2 and finish it as `2.0` - valid JSON,
    a moment two hours off. As a pattern-bound string it wrote `"2:16:10"`,
    which `parse_answer` reads as 8170 s. Text only: no number type at all."""
    schema = tasks.schema_payload(tasks.TASKS[kind], True, clock_as_text=True)["schema"]
    node = clock_node(schema, kind)

    assert "number" not in types_of(node)
    texts = [alt for alt in node.get("anyOf", [node]) if alt["type"] == "string"]
    assert len(texts) == 1
    pattern = re.compile(texts[0]["pattern"])
    for written in ("2:16:29", "1:23", "83.0", "83", "0:00"):
        assert pattern.fullmatch(written), written
        assert tasks._clock_seconds(written) >= 0
    for written in ("{2:16:10}", "[1:23]", "2:16:", "about an hour in", ""):
        assert not pattern.fullmatch(written), written
    # `[0-9]`, never `\d`: with `\d` the daemon let `"{2:16:10}"` through
    # (measured 2026-10-25), so the class it does honour is spelt out.
    assert r"\d" not in texts[0]["pattern"]
    assert ("null" in types_of(node)) is (kind != "chapters"), "nullable exactly where pydantic is"
    assert "x-myscribe-clock" not in json.dumps(schema)


@pytest.mark.parametrize("kind", SCHEMA_KINDS)
def test_the_cloud_schema_is_unchanged_by_the_clock_marker(kind):
    """The strict schema OpenRouter validated live keeps `number` timestamps
    and carries no private keyword: a strict validator may refuse one."""
    schema = tasks.schema_payload(tasks.TASKS[kind], True)["schema"]

    assert "x-myscribe-clock" not in json.dumps(schema)
    assert '"pattern"' not in json.dumps(schema)
    if kind in CLOCK_FIELDS:
        assert "number" in types_of(clock_node(schema, kind))


def test_the_combine_carries_the_text_timestamps_and_the_notes_no_schema(conn, media, monkeypatch):
    """Through the front door: a provider that says its schema is a grammar
    gets the clock-as-text schema on the combine, and the notes stay prose."""
    strict, calls = fake_provider(
        map_reduce_script(ANSWERS["minutes"]), name="grammar", supports_json_schema=True
    )
    strict.schema_clock_as_text = True
    register(monkeypatch, strict)
    long_id = seed_media(conn, title="Long one")
    seed_long_run(conn, long_id, n_segments=12)

    tasks.run_task(
        conn, media_id=long_id, kind="minutes", provider_name="grammar", model="grammar-1",
        budget_tokens=100,
    )

    notes = [c for c in calls if CHUNK_MARKER in c.user]
    combines = [c for c in calls if CHUNK_MARKER not in c.user]
    assert notes and all(c.json_schema is None for c in notes)
    assert len(combines) == 1
    assert "number" not in types_of(clock_node(combines[0].json_schema["schema"], "minutes"))


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
    # Longer than it was: a 150-segment recording now shortens its notes and
    # succeeds, which is the point of the change. What belongs in `prepare` is
    # the case that genuinely cannot be done at any note length.
    media_id = seed_media(conn, title="A very long meeting")
    seed_long_run(conn, media_id, n_segments=3000, words_per=50)
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
    # Not LLM_FAILED any more: a window that cannot hold this recording's notes
    # at any usable length is a verdict about the model, and the board says so
    # in those words (Robert, 2026-09-19).
    assert row["error_code"] == "MODEL_UNSUITABLE"
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


# --- every kind has to fit the window it plans against (TASK-084) -------------------


@pytest.mark.parametrize("kind", sorted(tasks.TASKS))
def test_every_kind_fits_the_smallest_local_window(kind):
    """The regression this file did not have.

    `speakers` carried `max_output_tokens=8000`, measured against a cloud
    window of 128k where it is free. Against the local floor of 8192 the same
    number left -852 tokens for the transcript, so the task could not run at
    all on Ollama - and nothing failed until a user queued it and got a
    RUNTIME error out of the prepare stage. A kind whose own answer budget
    does not fit the smallest window it may be planned against is broken on
    arrival, and that is a property of the table, not of a run.
    """
    spec = tasks.TASKS[kind]
    prompt = "a question" if spec.needs_prompt else None

    output = tasks.fit_output_tokens(
        spec, context_tokens=tasks.LOCAL_CONTEXT_TOKENS, custom_prompt=prompt
    )
    budget = tasks.budget_for(
        spec,
        context_tokens=tasks.LOCAL_CONTEXT_TOKENS,
        max_output_tokens=output,
        custom_prompt=prompt,
    )

    assert budget >= tasks.MIN_TRANSCRIPT_TOKENS, (
        f"{kind} leaves only {budget} tokens for the transcript at the local floor of "
        f"{tasks.LOCAL_CONTEXT_TOKENS}; lower its max_output_tokens or raise the floor"
    )


def test_a_window_too_small_for_any_answer_is_refused_in_words():
    """The other half of TASK-084: when clamping cannot save the call, the
    refusal has to name the numbers and what to change, not subtract its way
    to a negative and say the context 'leaves nothing'."""
    spec = tasks.TASKS["speakers"]

    with pytest.raises(ValueError) as exc:
        tasks.fit_output_tokens(spec, context_tokens=2048)

    message = str(exc.value)
    assert "2048" in message and "speakers" in message
    assert "bigger window" in message


def test_a_caller_asking_for_more_answer_than_the_window_holds_is_clamped():
    """A number from a caller is a preference, not a licence to overrun."""
    spec = tasks.TASKS["summary"]

    fitted = tasks.fit_output_tokens(spec, context_tokens=8192, max_output_tokens=99_000)

    assert tasks.MIN_OUTPUT_TOKENS <= fitted <= 8192 - tasks.MIN_TRANSCRIPT_TOKENS


@pytest.mark.parametrize("window", [4096, 8192])
@pytest.mark.parametrize("kind", sorted(tasks.TASKS))
def test_every_kind_runs_in_a_small_window(kind, window):
    """Plan for a limited context, not for what a model claims (Robert,
    2026-09-19). 4 KB is the smallest window worth supporting, and every kind
    has to work there - in more calls over smaller chunks, which is the honest
    cost of a small window rather than a failure."""
    spec = tasks.TASKS[kind]
    prompt = "a question" if spec.needs_prompt else None

    output = tasks.fit_output_tokens(spec, context_tokens=window, custom_prompt=prompt)
    budget = tasks.budget_for(spec, context_tokens=window, max_output_tokens=output, custom_prompt=prompt)

    assert output >= tasks.MIN_OUTPUT_TOKENS
    assert budget >= tasks.MIN_TRANSCRIPT_TOKENS, (
        f"{kind} leaves only {budget} transcript tokens in a {window}-token window"
    )


def test_a_long_recording_shortens_its_notes_rather_than_refusing():
    """Robert, 2026-09-19, from a job that failed on his own recording: adapt
    the content length dynamically. Ten chunks used to be a refusal because a
    note "worth answering" was fixed at 4000 tokens; now the note is whatever
    the combine call can actually hold."""
    budget = tasks.plan_notes(
        tasks.TASKS["summary"], chunks=10, context_tokens=8192,
        budget_tokens=3000, max_output_tokens=4000,
    )

    assert tasks.MIN_NOTE_FLOOR <= budget < tasks.MIN_NOTE_TOKENS
    assert budget * 10 <= tasks.combine_capacity(
        tasks.TASKS["summary"], context_tokens=8192, max_output_tokens=4000, chunks=10
    )


def test_a_roomy_window_still_gets_a_full_note():
    """Adapting downwards must not cost anything where there is room."""
    budget = tasks.plan_notes(
        tasks.TASKS["summary"], chunks=3, context_tokens=128_000,
        budget_tokens=100_000, max_output_tokens=4000,
    )

    assert budget == tasks.MIN_NOTE_TOKENS


def test_when_it_truly_cannot_be_done_the_error_suggests_a_way_out(monkeypatch):
    """"If it is simply not possible with the model, give that error and
    suggest another model" - and the suggestion has to be actionable."""
    monkeypatch.setattr(tasks, "suggest_bigger_window", lambda ctx: "Try the roomy-model here.")

    with pytest.raises(Exception) as exc:
        tasks.plan_notes(
            tasks.TASKS["summary"], chunks=200, context_tokens=8192,
            budget_tokens=3000, max_output_tokens=4000,
        )

    message = str(exc.value)
    assert "Nothing has been sent" in message
    assert "Try the roomy-model here." in message
    assert str(tasks.MIN_NOTE_FLOOR) in message


def test_the_suggestion_survives_a_daemon_that_is_down(monkeypatch):
    """A suggestion is a nicety and must never become a second failure."""
    import scribe.llm.ollama as ollama

    monkeypatch.setattr(ollama.OllamaProvider, "models", lambda self: (_ for _ in ()).throw(OSError("down")))

    said = tasks.suggest_bigger_window(8192)

    assert "bigger window" in said


# --- ADR-016: nothing is sent until somebody has chosen -------------------------------
#
# The row is `llm_provider`. What these assert is that a row that is not there
# reads as "nobody chose", everywhere - in the answer `default_provider` gives
# and in what the runner does with a job whose params name no provider.


def _store_provider(conn, name: str) -> None:
    with db.LOCK:
        conn.execute(
            "INSERT INTO setting(key, value) VALUES ('llm_provider', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (name,),
        )
        conn.commit()


def _job_row(conn, job_id: int) -> dict:
    return dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())


def _nothing_leaves_the_machine(monkeypatch, script) -> list:
    """Register a fake under the name the fall-through picks, for the red run.

    These three are red against code that still falls through, and that
    fall-through *sends*: a key resolves from this machine's registry without
    anybody having typed one, which is the fact ADR-016 exists for. So the name
    it picks answers from a script here, and what the red state proves is that
    the job was not refused - never that a request went out.
    """
    provider, calls = fake_provider(script, name="openrouter")
    register(monkeypatch, provider)
    return calls


def test_a_missing_provider_row_means_no_provider_is_chosen(conn):
    """An install where every question was skipped has no row, and a row that
    is not there is not a choice of whatever this version happens to ship."""
    assert llm.default_provider(conn) == ""


def test_a_stored_name_this_version_no_longer_has_is_no_provider_either(conn):
    """A provider a later version removed leaves its row behind. That row has
    stopped naming anything, and reading it must not raise: every transcript
    page asks this question."""
    _store_provider(conn, "a-provider-this-version-does-not-have")

    assert llm.default_provider(conn) == ""


def test_a_stored_name_that_is_registered_is_still_what_a_request_opens_with(conn):
    """The other half of ADR-016: a machine whose row exists does not notice."""
    _store_provider(conn, "ollama")

    assert llm.default_provider(conn) == "ollama"


def test_a_preset_job_that_names_no_provider_dies_in_the_runner_with_a_sentence(
    conn, media, monkeypatch
):
    """`task_prepare`. The params are the request, and a request that names
    nobody is refused where `_kind` and `_media_id` are - before a token."""
    _nothing_leaves_the_machine(monkeypatch, answer_for("summary"))
    job_id = jobs.enqueue(conn, llm_stage.JOB_TYPE, media, {"media_id": media, "kind": "summary"})
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = _job_row(conn, job_id)
    assert row["status"] == "failed"
    assert "needs a provider" in (row["error_detail"] or "")


def test_a_chat_job_that_names_no_provider_dies_in_the_runner_with_a_sentence(
    conn, media, monkeypatch
):
    """`chat_prepare`, the same refusal through the other door."""
    _nothing_leaves_the_machine(monkeypatch, ["Arthur and Ford."])
    job_id = jobs.enqueue(
        conn,
        llm_stage.JOB_TYPE,
        media,
        {"media_id": media, "kind": chat_tool.CHAT_KIND, "question": "Who said what?"},
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = _job_row(conn, job_id)
    assert row["status"] == "failed"
    assert "needs a provider" in (row["error_detail"] or "")


def test_a_provider_test_that_names_no_provider_dies_in_the_runner_with_a_sentence(
    conn, monkeypatch
):
    """`probe_prepare`. A provider test with no provider is the emptiest case
    of all, and it used to test OpenRouter."""
    _nothing_leaves_the_machine(monkeypatch, ["ok"])
    job_id = jobs.enqueue(conn, llm_stage.JOB_TYPE, None, {"kind": selftest.KIND})
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = _job_row(conn, job_id)
    assert row["status"] == "failed"
    assert "needs a provider" in (row["error_detail"] or "")


def test_the_speakers_pass_may_answer_in_16000_tokens_against_a_cloud_window():
    """TASK-099, decided with Robert on 2026-09-27: 8,000 was not enough for
    a 2h28m, five-speaker recording - jobs 418 and 419 spent all 8,000 tokens
    reasoning at BaseTen and came back empty. A cap, not a spend: a cloud
    provider bills what was generated. The local window still clamps it
    (`test_every_kind_fits_the_smallest_local_window`)."""
    spec = tasks.TASKS["speakers"]
    assert tasks.fit_output_tokens(spec, context_tokens=tasks.CLOUD_CONTEXT_TOKENS) == 16_000


# --- TASK-105.01: the answer comes back in the recording's language -------------------


def _system_sent(conn, monkeypatch, media_id, *, language, task="transcribe"):
    """The system prompt one summary call went out with, for a run in `language`."""
    with db.LOCK:
        conn.execute("UPDATE run SET language=?, task=? WHERE media_id=?", (language, task, media_id))
        conn.commit()
    provider, calls = fake_provider([ANSWERS["summary"]])
    register(monkeypatch, provider)
    plan = tasks.plan_task(conn, media_id=media_id, kind="summary", provider_name="fake", model="fake-1")
    tasks.generate(conn, plan)
    return calls[-1].system


def test_a_dutch_recording_is_answered_in_dutch(conn, monkeypatch, media):
    """Every prompt is English and none named the transcript's language, so a
    Dutch recording was likely summarised in English (the review of
    2026-10-05). The run knows its language; the model is now told."""
    system = _system_sent(conn, monkeypatch, media, language="nl")

    assert "Dutch" in system and "write your answer in Dutch" in system


def test_a_translated_run_is_answered_in_english(conn, monkeypatch, media):
    """translate writes an English transcript of a recording in another
    language; the answer follows the transcript, not the audio."""
    system = _system_sent(conn, monkeypatch, media, language="nl", task="translate")

    assert "write your answer in English" in system and "Dutch" not in system


def test_an_unknown_language_adds_nothing(conn, monkeypatch, media):
    system = _system_sent(conn, monkeypatch, media, language=None)

    assert system.startswith(tasks.SYSTEM) and "write your answer in" not in system


# --- TASK-105.03: the kinds that ask who, see who ---------------------------------------


def _diarized(conn, *, title="Standup"):
    media_id = seed_media(conn, title=title)
    words = []
    for i, (who, text) in enumerate([("SPEAKER_00", "I will send the report on Friday."),
                                      ("SPEAKER_01", "Then I book the room.")]):
        for j, token in enumerate(text.split()):
            words.append({"start": i * 5 + j * 0.4, "end": i * 5 + j * 0.4 + 0.3,
                          "text": (" " if j else "") + token, "speaker": who})
    segments = [{"start": 0.0, "end": 4.0, "text": "I will send the report on Friday."},
                {"start": 5.0, "end": 8.0, "text": "Then I book the room."}]
    seed_run(conn, media_id, words=words, segments=segments, labels={"SPEAKER_00": "Arthur Dent"})
    return media_id


def _user_sent(conn, monkeypatch, media_id, kind):
    provider, calls = fake_provider([ANSWERS[kind]])
    register(monkeypatch, provider)
    plan = tasks.plan_task(conn, media_id=media_id, kind=kind, provider_name="fake", model="fake-1")
    tasks.generate(conn, plan)
    return calls[-1].user


@pytest.mark.parametrize("kind", ["action_items", "minutes"])
def test_the_kinds_that_ask_for_an_owner_see_the_speakers_by_name(conn, monkeypatch, kind):
    """action_items and minutes ask for an owner 'by name or speaker label' but
    were sent lines without any speaker - an owner the model cannot see. They
    get the names the page shows: the one somebody set, else 'Speaker N'."""
    user = _user_sent(conn, monkeypatch, _diarized(conn), kind)

    assert "Arthur Dent: I will send the report" in user
    assert "Speaker 2: Then I book the room" in user
    assert "SPEAKER_0" not in user, "a raw cluster label would end up as the owner"


def test_a_summary_still_reads_plain_lines(conn, monkeypatch):
    user = _user_sent(conn, monkeypatch, _diarized(conn), "summary")

    assert "Arthur Dent" not in user and "SPEAKER_00" not in user


@pytest.mark.parametrize("speakers, kept", [(True, True), ("names", True), (False, False)])
def test_notes_keep_the_speaker_when_the_lines_carry_one(speakers, kept):
    """map_chunk did not ask to keep the speaker, so a long recording's notes
    could drop it - and the speaker pass, which reads those notes, then has no
    SPEAKER_XX left to name."""
    text = tasks.render_prompt("map_chunk", index=1, count=2, start="0:00", end="5:00",
                               goal="g", transcript="[0:01] x", speakers=speakers,
                               note_rule="", again="", max_points=tasks.MAX_NOTE_POINTS)

    assert ("speaker" in text.lower()) is kept


# --- a blog note says it in its own words; it does not copy the excerpt (TASK-124) ------

BLOG_NOTE_RULE = (
    "Write at most 30 notes. Each note is one point in your own words, starting with the "
    "`[m:ss]` of the moment it is about. Never copy a line from the excerpt: a note that "
    "repeats a line word for word is not a note. Where the exact words matter, quote only "
    "those few words, in quotation marks, inside your own sentence."
)
"""Pinned word for word. Jobs 430-432 on a copy of Robert's library
(2026-10-24): qwen3.5:4b and :9b, asked for blog notes on media 61, wrote the
excerpt back line for line from `[0:01] is deze licht en ze ligt?` until the
4000-token note budget ran out - the shared instruction 'plain lines, each
keeping the [m:ss] it came from' plus the goal 'gather the material' read as
'copy the lines'. Robert's choice (option A): the blog notes are asked as a
bounded number of points in the model's own words, with a short quote allowed
and a whole line not."""


def _blog_chunk_prompt(kind: str = "blog") -> str:
    return tasks.chunk_prompt(tasks.task_spec(kind), index=1, count=3, start="0:01",
                              end="55:37", transcript="[0:01] is deze licht en ze ligt?")


def test_a_blog_note_is_asked_as_points_in_its_own_words_never_a_copied_line():
    text = _blog_chunk_prompt()

    assert BLOG_NOTE_RULE in text, text
    assert "own words" in tasks.chunk_goal(tasks.task_spec("blog"))
    assert "gather the material" not in tasks.chunk_goal(tasks.task_spec("blog"))


MINUTES_NOTE_RULE = (
    "Write at most 30 notes. Each note is one point in your own words, written as "
    "`[m:ss] Name: point` - the moment it is about and who it is about, as the excerpt names "
    "them - saying what was discussed, decided or agreed to do. Never copy a line from the "
    "excerpt: a note that repeats a line word for word is not a note. Where the exact words "
    "matter, such as a decision or a promise, quote only those few words, in quotation "
    "marks, inside your own sentence."
)
"""Pinned word for word. Job 433 on the round-6 library copy (2026-10-25):
qwen3.5:4b, asked for minutes notes on media 61, wrote chunk 2 back line for
line from `[1:38:25] Henry: Het is goed.` until the 4000-token note budget ran
out, and chunk 1 came back as 93 lines for a 47-minute stretch. Robert's
decision of 2026-10-25: minutes gets a note rule like blog's. The order
`[m:ss] Name:` is the transcript's own, so the stamp `copied_lines` strips
still leads and the speaker clause of map_chunk.md is not contradicted."""


def test_a_minutes_note_is_asked_as_points_in_its_own_words_never_a_copied_line():
    text = _blog_chunk_prompt("minutes")

    assert MINUTES_NOTE_RULE in text, text
    # Still asked to name who spoke: an owner is part of what minutes need.
    assert "Begin every note with the speaker" in text


def test_a_minutes_note_cut_off_while_copying_the_excerpt_says_it_was_copying(
    conn, monkeypatch
):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    # Chunk 0 of this plan is one line once the speaker name is on it, so the
    # note is that line alone: one of one copied.
    provider, _ = fake_provider(_copying(own=0))
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="minutes", provider_name="fake",
                       model="fake-1", budget_tokens=100)

    assert "copied word for word" in str(caught.value), str(caught.value)


def test_which_kinds_ask_their_notes_in_their_own_words():
    """Every kind that combines notes shares map_chunk.md, and its sentence
    'plain lines, each keeping the [m:ss]' read as 'copy the lines' to
    qwen3.5:4b on media 61 for each kind measured (round-7 library copy,
    2026-10-25: action_items job 430 copied 21 of 24 note lines, chapters job
    431 copied 25 of 26, both until the 4000-token cap). `speakers` must keep
    its quotes; `summary` and `labels` were not measured, so they stay as they
    were; `cleanup` and `custom` take no such notes."""
    assert {kind: bool(spec.note_rule) for kind, spec in tasks.TASKS.items()} == {
        "summary": False,
        "action_items": True,
        "chapters": True,
        "minutes": True,
        "blog": True,
        "speakers": False,
        "labels": False,
        "cleanup": False,
        "custom": False,
    }


@pytest.mark.parametrize("kind", ["action_items", "chapters"])
def test_action_items_and_chapters_notes_are_never_a_copied_line(kind):
    text = _blog_chunk_prompt(kind)

    assert tasks.task_spec(kind).note_rule in text
    assert "Never copy a line from the excerpt" in text
    assert "Write at most 30 notes" in text


def test_the_speaker_notes_keep_their_quotes():
    """The other kinds share map_chunk. `speakers` asks for 'the quote and its
    time', so a rule against copying words would take away what it needs."""
    assert "Never copy a line" not in _blog_chunk_prompt("speakers")


def test_copied_lines_counts_note_lines_that_are_transcript_lines_word_for_word():
    """A line under four words ('Ja.') says nothing about copying, so it is not
    counted either way; a bullet or the stamp in front of a line does not
    disguise it."""
    transcript = (
        "[0:01] is deze licht en ze ligt?\n"
        "[0:02] ik heb hem net in de signal van Terrein gegooid\n"
        "[0:08] Ja.\n"
    )
    note = (
        "[0:01] is deze licht en ze ligt?\n"
        "- [0:02]  ik heb hem net in de signal van Terrein gegooid\n"
        "[0:08] Ja.\n"
        "[0:02] Iemand deelt de link naar het terrein in Signal.\n"
    )

    assert tasks.copied_lines(note, transcript) == (2, 3)
    assert tasks.copied_lines("notes 0", transcript) == (0, 0)


def _copying(own: int = 0, cut_off: bool = True):
    """A note that writes the excerpt's own lines back, and `own` of its own."""

    def answer(req: base.ChatRequest):
        if CHUNK_MARKER not in req.user:
            return ANSWERS["blog"]
        excerpt = req.user.split("Excerpt:", 1)[1]
        lines = [line for line in excerpt.splitlines() if line.startswith("[")]
        text = "\n".join(lines + ["[0:00] Somebody counts to nine, twice over."] * own)
        return truncated(text) if cut_off else replace(truncated(text), raw_finish_reason="stop")

    return answer


def test_a_note_cut_off_while_copying_the_excerpt_says_it_was_copying(conn, monkeypatch):
    """Job 430's refusal said only 'it stopped at the 4000-token cap', which
    reads as 'raise the budget'. The note was the transcript written back; a
    bigger budget buys a longer copy."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, _ = fake_provider(_copying(own=1))
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="blog", provider_name="fake",
                       model="fake-1", budget_tokens=100)

    message = str(caught.value)
    # Chunk 0 of this plan is two transcript lines, both written back.
    assert "2 of its 3 lines" in message and "copied word for word" in message, message


def test_a_note_cut_off_in_its_own_words_does_not_say_copying(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, _ = fake_provider(
        map_reduce_script(ANSWERS["blog"], note=lambda i: truncated(
            "[0:00] Somebody counts to nine, twice over, and then stops mid-"))
    )
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="blog", provider_name="fake",
                       model="fake-1", budget_tokens=100)

    assert "copied" not in str(caught.value), str(caught.value)


def test_a_speaker_note_cut_off_on_its_quotes_is_not_called_copying(conn, monkeypatch):
    """Only blog's note rule forbids copying. `speakers` is asked for 'the
    quote and its time', so its notes are transcript lines by design, and a
    refusal saying it 'was writing the transcript back' would blame it for
    doing what it was asked."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)

    def answer(req: base.ChatRequest):
        excerpt = req.user.split("Excerpt:", 1)[1]
        lines = [line for line in excerpt.splitlines() if line.startswith("[")]
        return truncated(chr(10).join(lines))

    provider, _ = fake_provider(answer)
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="speakers", provider_name="fake",
                       model="fake-1", budget_tokens=100)

    assert "ran out of room writing its notes" in str(caught.value), str(caught.value)
    assert "copied word for word" not in str(caught.value), str(caught.value)


def test_a_finished_note_records_how_much_of_it_was_copied(conn, monkeypatch):
    """Reported, not refused: a copied note that finished is the excerpt's own
    words, which the combine can still use, and refusing it would fail a job
    that can finish. The chunk row says how much was copied, so a run shows
    whether a model follows the note instruction."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, _ = fake_provider(_copying(own=1, cut_off=False))
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media_id, kind="blog", provider_name="fake", model="fake-1",
                   budget_tokens=100)

    chunk_rows = [r for r in rows(conn, media_id) if tasks.is_chunk_kind(r["kind"])]
    assert chunk_rows
    for row in chunk_rows:
        params = json.loads(row["params_json"])
        excerpt_lines = len(params["segment_ids"])
        assert (params["copied_lines"], params["note_lines"]) == (
            excerpt_lines, excerpt_lines + 1), params


# --- a cut-off object answer says it was cut off, and who answered (TASK-123) ------------


def test_a_one_call_object_answer_cut_off_at_the_cap_says_so_and_names_the_model(
    conn, media, monkeypatch
):
    """Job 427 on Robert's library (2026-10-22): a blog answer stopped at its
    6000-token cap in the middle of a JSON string, and the board said 'the
    model did not answer with a JSON object' - which reads as a model that
    ignored the format, not one that ran out of room. Under openrouter/auto
    the model that answered is the other half of the diagnosis."""
    cut = replace(
        truncated('{"title": "Why a towel", "body": "## Why\n\nBecause it is'),
        model="deepseek/deepseek-v4-flash", completion_tokens=6000, reasoning_tokens=5400,
        upstream="BaseTen",
    )
    provider, _ = fake_provider([cut])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="blog", provider_name="fake", model="fake-1")

    message = str(caught.value)
    assert "ran out of room" in message and "-token cap" in message, message
    assert "model='deepseek/deepseek-v4-flash'" in message, message
    assert "not answer with a JSON object" not in message, message


def test_a_combined_object_answer_cut_off_at_the_cap_says_so(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    cut = replace(truncated('{"paragraph": "Two travellers discuss'), model="x/y")
    provider, _ = fake_provider(map_reduce_script(cut))
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(
            conn, media_id=media_id, kind="summary", provider_name="fake", model="fake-1",
            budget_tokens=100,
        )

    assert "ran out of room" in str(caught.value), str(caught.value)
    assert "model='x/y'" in str(caught.value)


def test_a_refused_cut_off_part_names_the_model_that_answered(conn, monkeypatch):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    provider, _ = fake_provider([replace(truncated("[0:01] Ford: Don't"), model="z/model")])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1")

    assert "model='z/model'" in str(caught.value), str(caught.value)


# --- a full window is not a spent cap (TASK-124) ---------------------------------------------

WINDOW = 32768


def window_full(text: str, *, prompt: int, completion: int) -> base.ChatResponse:
    """An answer that stopped because prompt + answer filled the window.

    Job 436 on a copy of Robert's library (2026-10-23): a blog answer read
    32,016 prompt tokens and wrote 752 = 32,768, `done_reason: length`, and the
    board said it 'stopped at the 4000-token cap' - which it never reached.
    """
    return replace(truncated(text), prompt_tokens=prompt, completion_tokens=completion)


def assert_says_window(message: str, *numbers: int) -> None:
    assert "window" in message, message
    assert "-token cap" not in message, message
    for number in numbers:
        assert str(number) in message, message


def test_an_object_answer_that_filled_the_window_says_the_window_not_the_cap(
    conn, media, monkeypatch
):
    provider, _ = fake_provider(
        [window_full('{"title": "Terrein", "body": "## Wat', prompt=32016, completion=752)]
    )
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="blog", provider_name="fake", model="fake-1",
                       context_tokens=WINDOW)

    assert_says_window(str(caught.value), WINDOW, 32016, 752)


def test_a_note_that_filled_the_window_says_the_window_not_the_cap(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, _ = fake_provider(
        map_reduce_script(
            ANSWERS["summary"],
            note=lambda i: window_full(f"notes {i} which stop", prompt=32568, completion=200),
        )
    )
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="summary", provider_name="fake",
                       model="fake-1", budget_tokens=100, context_tokens=WINDOW)

    assert_says_window(str(caught.value), WINDOW, 32568, 200)


def test_a_part_that_filled_the_window_says_the_window_not_the_cap(conn, monkeypatch):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    provider, _ = fake_provider([window_full("[0:01] Ford: Don't", prompt=32016, completion=752)])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake",
                       model="fake-1", context_tokens=WINDOW)

    assert_says_window(str(caught.value), WINDOW, 32016, 752)


def test_an_answer_that_spent_its_cap_still_says_the_cap_even_past_the_planned_window(
    conn, media, monkeypatch
):
    """The cap is checked first. On a cloud provider the plan's window is the
    128k planning floor, not the model's, so a prompt past it with the whole
    cap spent is a cap, and calling it a full window would send Robert after
    the wrong number."""
    plan_cap = tasks.task_spec("blog").max_output_tokens
    provider, _ = fake_provider(
        [window_full('{"title": "x", "body": "y', prompt=130_000, completion=plan_cap)]
    )
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="blog", provider_name="fake", model="fake-1")

    assert f"{plan_cap}-token cap" in str(caught.value), str(caught.value)


def test_a_cloud_answer_cut_short_past_the_planning_floor_is_not_called_a_full_window(
    conn, media, monkeypatch
):
    """Review of TASK-124: on a cloud provider the plan's 128k is a planning
    floor, not the model's window, so prompt + answer past it proves nothing
    about a window. A routed model whose own output limit is below our cap
    stops at `length` with the cap unspent; saying 'the window was full' would
    send Robert to shorten a prompt the model never refused, and saying 'the
    4000-token cap' names a number it never reached."""
    plan_cap = tasks.task_spec("blog").max_output_tokens
    provider, _ = fake_provider(
        [window_full('{"title": "x", "body": "y', prompt=127_000, completion=2_000)]
    )
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="blog", provider_name="fake", model="fake-1")

    message = str(caught.value)
    assert "window was full" not in message, message
    assert f"stopped at the {plan_cap}-token cap" not in message, message
    assert "2000" in message and str(plan_cap) in message, message


def test_a_local_answer_past_the_floor_window_still_says_the_window(conn, media, monkeypatch):
    """The other side: a local provider runs the window it was planned for, so
    there a full window is a fact and is named as one."""
    window = tasks.LOCAL_CONTEXT_TOKENS
    provider, _ = fake_provider(
        [window_full('{"title": "x", "body": "y', prompt=window - 500, completion=500)],
        name="localfake",
        is_local=True,
    )
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="blog", provider_name="localfake",
                       model="fake-1")

    assert_says_window(str(caught.value), window, window - 500, 500)


def test_repair_json_accepts_a_raw_newline_inside_a_string():
    """Models write real line breaks inside a JSON string more often than the
    escaped kind; strict parsing stopped job 427's reply at char 700."""
    assert tasks.repair_json('{"body": "line one\nline two"}') == {"body": "line one\nline two"}


def test_repair_json_says_why_it_could_not_parse():
    with pytest.raises(base.BadResponse) as caught:
        tasks.repair_json('{"body": "never closed')
    assert "json:" in str(caught.value), str(caught.value)


# --- the note count is bounded in code, not only asked (TASK-126) ---------------------------

ASKED_AGAIN = "Your first notes for this excerpt"
"""The first words of the second ask: how a script tells it from the first."""


def _points(n: int, start: int = 0) -> str:
    """`n` notes in a model's own words, each with its own clock."""
    return "\n".join(
        f"[{(start + k) // 60}:{(start + k) % 60:02d}] Point {start + k} in the model's own words."
        for k in range(n)
    )


def _notes(first, again, kind: str = "minutes"):
    """`first` for a chunk's first ask, `again` for the second, and the kind's own
    answer for the combine. Each is a string or a ChatResponse."""

    def answer(req: base.ChatRequest):
        if CHUNK_MARKER not in req.user:
            return ANSWERS.get(kind, json.dumps({"speakers": []}))
        return again if ASKED_AGAIN in req.user else first

    return answer


def _run_minutes(conn, monkeypatch, first, again, kind: str = "minutes"):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    provider, calls = fake_provider(_notes(first, again, kind))
    register(monkeypatch, provider)
    tasks.run_task(conn, media_id=media_id, kind=kind, provider_name="fake", model="fake-1",
                   budget_tokens=100)
    chunk_rows = [r for r in rows(conn, media_id) if tasks.is_chunk_kind(r["kind"])]
    assert chunk_rows
    return chunk_rows, calls


def test_the_note_rules_ask_for_the_bound_the_code_keeps():
    """The number in the prompt and the number in the code are one constant, so
    the bound a note is cut to is the bound it was asked for."""
    for kind, spec in tasks.TASKS.items():
        if spec.note_rule:
            assert f"Write at most {tasks.MAX_NOTE_POINTS} notes." in spec.note_rule, kind


def test_an_85_note_chunk_is_asked_again_and_then_bounded(conn, monkeypatch):
    """Job 430 on the round-8 library copy (TASK-124, 2026-10-25): qwen3.5:9b
    wrote 85 minutes notes on chunk 0 against 'at most 30'. Asked once more, a
    model that still writes 85 has them cut to 30 in code - spread over the
    whole note, the last one kept, each line untouched with its clock."""
    chunk_rows, calls = _run_minutes(conn, monkeypatch, _points(85), _points(85))

    again = [c for c in calls if ASKED_AGAIN in c.user]
    assert len(again) == len(chunk_rows), "every 85-note chunk is asked a second time"
    assert "85 notes" in again[0].user and str(tasks.MAX_NOTE_POINTS) in again[0].user
    written = _points(85).splitlines()
    for row in chunk_rows:
        kept = row["content"].splitlines()
        assert len(kept) == tasks.MAX_NOTE_POINTS, row["content"]
        assert kept[0] == written[0] and kept[-1] == written[-1], "the chunk's end is kept"
        assert all(line in written for line in kept), "a kept line was rewritten"
        params = json.loads(row["params_json"])
        assert params["points_written"] == 85 and params["points_kept"] == tasks.MAX_NOTE_POINTS
        assert params["asked_again"] is True


def test_a_note_that_keeps_the_bound_when_asked_again_is_stored_whole(conn, monkeypatch):
    chunk_rows, calls = _run_minutes(conn, monkeypatch, _points(85), _points(20))

    for row in chunk_rows:
        assert row["content"] == _points(20)
        params = json.loads(row["params_json"])
        assert (params["points_written"], params["points_kept"]) == (20, 20)
        assert params["asked_again"] is True


def test_a_note_within_the_bound_is_asked_once(conn, monkeypatch):
    chunk_rows, calls = _run_minutes(conn, monkeypatch, _points(30), _points(5))

    assert not [c for c in calls if ASKED_AGAIN in c.user]
    for row in chunk_rows:
        assert row["content"] == _points(30)
        assert json.loads(row["params_json"])["asked_again"] is False


def test_a_note_cut_off_at_its_cap_is_asked_again_and_the_finished_one_stored(
    conn, monkeypatch
):
    """Job 433 (chapters, qwen3.5:4b, round-8 copy) ran out of its 4000-token
    budget at 31:15 of a 60-minute chunk, writing fragments. The second ask
    says so and asks for the whole excerpt within the bound. Twenty notes and
    cut off: within the bound, and asked again all the same, because the cap
    is what kept it from the end of the excerpt."""
    chunk_rows, calls = _run_minutes(
        conn, monkeypatch, truncated(_points(20) + "\n[0:20] Sti"), _points(12), kind="chapters",
    )

    again = [c for c in calls if ASKED_AGAIN in c.user]
    assert len(again) == len(chunk_rows)
    assert "ran out of room" in again[0].user
    for row in chunk_rows:
        assert row["content"] == _points(12)


def test_a_note_cut_off_twice_is_refused_and_not_stored(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    cut = truncated(_points(20) + "\n[0:20] Sti")
    provider, calls = fake_provider(_notes(cut, cut, "chapters"))
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="chapters", provider_name="fake",
                       model="fake-1", budget_tokens=100)

    assert "ran out of room" in str(caught.value) and "asked twice" in str(caught.value)
    assert rows(conn, media_id) == []
    assert len(calls) == 2, "one chunk, asked twice, then refused"


def test_speaker_notes_are_not_bounded(conn, monkeypatch):
    """`speakers` keeps its quotes and has no note rule, so no bound either."""
    chunk_rows, calls = _run_minutes(conn, monkeypatch, _points(85), _points(5),
                                     kind="speakers")

    assert not [c for c in calls if ASKED_AGAIN in c.user]
    for row in chunk_rows:
        assert row["content"] == _points(85)


def test_bounded_points_spreads_the_kept_lines_over_the_whole_note():
    note = _points(85)
    kept = tasks.bounded_points(note, 30).splitlines()
    lines = note.splitlines()

    assert len(kept) == 30
    assert kept[0] == lines[0] and kept[-1] == lines[-1]
    assert [lines.index(line) for line in kept] == sorted({lines.index(line) for line in kept})
    # Spread, not the first thirty: something from the middle third is kept.
    assert any(28 <= lines.index(line) <= 56 for line in kept)
    assert tasks.bounded_points(_points(30), 30) == _points(30)
    # Blank lines are not points and are not kept.
    assert tasks.bounded_points("[0:01] a\n\n[0:02] b\n", 30) == "[0:01] a\n[0:02] b"


@pytest.mark.parametrize("again", ["cut off", "empty"])
def test_a_finished_note_over_the_bound_is_kept_when_the_second_ask_fails(
    conn, monkeypatch, again
):
    """The second ask must not turn a job that finished into one that fails.
    Job 430's chunk 0 (TASK-124 round 8, qwen3.5:9b minutes) finished with 85
    notes and was stored; if its second ask runs out of room or says nothing,
    the finished first note is kept, thinned to the bound."""
    second = truncated(_points(10) + "\n[0:10] Sti") if again == "cut off" else ""
    chunk_rows, calls = _run_minutes(conn, monkeypatch, _points(85), second)

    assert [c for c in calls if ASKED_AGAIN in c.user], "the bound was not asked again"
    written = _points(85).splitlines()
    for row in chunk_rows:
        kept = row["content"].splitlines()
        assert len(kept) == tasks.MAX_NOTE_POINTS and kept[-1] == written[-1], row["content"]
        params = json.loads(row["params_json"])
        assert params["stored_from_ask"] == 1 and params["finish_reason"] == "stop", params


# --- a point is a stamped line and what hangs under it (TASK-126 review) ---------------------

LEAD_IN = "Hier zijn de notities uit het transcript, gericht op de onderwerpen:"
"""The line qwen3.5:4b opened chunk 1 of job 430 with (TASK-126 live run): not a
point, so it may neither count against the bound nor take a kept slot."""


def _two_line_points(n: int) -> str:
    """A lead-in, then `n` points of a stamped head and an unstamped sub-bullet:
    the shape the review probed, `2n + 1` lines for `n` notes."""
    body = "\n".join(
        f"[{k // 60}:{k % 60:02d}] Point {k} in the model's own words.\n  - detail of point {k}"
        for k in range(n)
    )
    return f"{LEAD_IN}\n\n{body}"


def test_a_point_is_its_stamped_line_and_the_lines_under_it():
    points = tasks.note_points(_two_line_points(20))

    assert len(points) == 20
    assert all(p.startswith("[") and "\n  - detail of point" in p for p in points)
    # qwen3.5:4b writes the clock without its closing bracket (TASK-124 round 8):
    # still a stamp, so still a point of its own.
    assert len(tasks.note_points("[2:26:09 Henry: one\n[2:27:10 Ruud: two")) == 2


def test_a_note_without_stamps_is_counted_by_line():
    """No stamp to group by - a 4B note that left them out: each line is a point,
    and nothing is dropped as a lead-in."""
    note = "\n".join(f"Christel: point {k} in her own words." for k in range(40))
    assert len(tasks.note_points(note)) == 40
    # One stamp among forty lines is not a note made of stamped points either.
    assert len(tasks.note_points("[0:01] first\n" + note)) == 41


def test_twenty_two_line_points_after_a_lead_in_are_asked_once_and_stored_whole(
    conn, monkeypatch
):
    """Twenty notes written as head plus sub-bullet were 41 lines, and 41 lines
    were asked again as if they were 41 notes."""
    note = _two_line_points(20)
    chunk_rows, calls = _run_minutes(conn, monkeypatch, note, _points(5))

    assert not [c for c in calls if ASKED_AGAIN in c.user]
    for row in chunk_rows:
        assert row["content"] == note
        assert json.loads(row["params_json"])["points_written"] == 20


def test_forty_two_line_points_are_thinned_by_point_not_by_line(conn, monkeypatch):
    note = _two_line_points(40)
    chunk_rows, calls = _run_minutes(conn, monkeypatch, note, note)

    heads = [line for line in note.splitlines() if line.startswith("[")]
    for row in chunk_rows:
        lines = row["content"].splitlines()
        assert LEAD_IN not in row["content"], "the lead-in took a kept slot"
        assert lines[0].startswith("["), lines[0]
        kept_heads = [line for line in lines if line.startswith("[")]
        assert len(kept_heads) == tasks.MAX_NOTE_POINTS
        assert kept_heads[-1] == heads[-1], "the chunk's last point is kept"
        # Every head keeps its own sub-bullet, and no sub-bullet is kept alone.
        assert len(lines) == 2 * tasks.MAX_NOTE_POINTS
        for head, sub in zip(lines[::2], lines[1::2]):
            k = head.split("Point ")[1].split(" ")[0]
            assert sub == f"  - detail of point {k}", (head, sub)
        params = json.loads(row["params_json"])
        assert (params["points_written"], params["points_kept"]) == (40, tasks.MAX_NOTE_POINTS)


def test_one_point_over_the_bound_is_asked_again_and_cut_to_it(conn, monkeypatch):
    """The edge: 31 is over, 30 is not (a `MAX_NOTE_POINTS + 1` slip survived
    the 85- and 20-point tests)."""
    over = _points(tasks.MAX_NOTE_POINTS + 1)
    chunk_rows, calls = _run_minutes(conn, monkeypatch, over, over)

    again = [c for c in calls if ASKED_AGAIN in c.user]
    assert len(again) == len(chunk_rows)
    assert f"{tasks.MAX_NOTE_POINTS + 1} notes" in again[0].user
    for row in chunk_rows:
        assert len(row["content"].splitlines()) == tasks.MAX_NOTE_POINTS
        params = json.loads(row["params_json"])
        assert params["points_kept"] == tasks.MAX_NOTE_POINTS


def test_a_cut_off_note_whose_second_ask_is_empty_says_it_ran_out_of_room(conn, monkeypatch):
    """The first ask ran out of room, the second said nothing: the job fails on
    the cause that started it, not on 'returned nothing' as if asked once."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    cut = truncated(_points(20) + "\n[0:20] Sti")
    provider, calls = fake_provider(_notes(cut, "", "chapters"))
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media_id, kind="chapters", provider_name="fake",
                       model="fake-1", budget_tokens=100)

    message = str(caught.value)
    assert "ran out of room" in message and "asked twice" in message, message
    assert "second answer" in message and "returned nothing" in message, message
    assert not message.startswith("the model returned nothing"), message
    assert rows(conn, media_id) == []


def test_a_second_ask_records_what_the_other_ask_cost(conn, monkeypatch):
    """Both asks are paid for on a cloud provider; the stored row carries one
    response's tokens, so it names the other ask's too."""
    first = base.ChatResponse(text=_points(85), model="fake-1-2026-09-02", provider="fake",
                              prompt_tokens=900, completion_tokens=850,
                              raw_finish_reason="stop")
    chunk_rows, calls = _run_minutes(conn, monkeypatch, first, _points(20))

    for row in chunk_rows:
        params = json.loads(row["params_json"])
        assert params["stored_from_ask"] == 2
        assert params["other_ask"] == {"prompt_tokens": 900, "completion_tokens": 850,
                                       "finish_reason": "stop"}, params
    chunk_rows, _ = _run_minutes(conn, monkeypatch, _points(5), _points(5))
    assert json.loads(chunk_rows[-1]["params_json"])["other_ask"] is None
