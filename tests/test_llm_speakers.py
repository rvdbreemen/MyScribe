"""Two kinds ported from WHYcast-transcribe: `speakers` and `cleanup`.

`speakers` asks who each `SPEAKER_XX` label is and answers with a mapping;
`cleanup` rewrites the transcript for reading. Both see the cluster label on
every line - the summary kinds do not - and `cleanup` is the first `concat`
task: a long transcript is rewritten chunk by chunk and the parts joined,
because notes about a chunk would lose the words the task exists to keep.

The fake provider is the one `test_llm_tasks` uses, registered the same way,
so the privacy pin and the registry stay in the path.
"""

from __future__ import annotations

import dataclasses
import json
import re
from types import SimpleNamespace

import pytest

from scribe.exports import doc as docs
from scribe import db
from scribe.llm import base, chunking, privacy, tasks
from tests.seed import seed_media, seed_run
from tests.test_llm_tasks import (
    CHUNK_MARKER,
    conn,  # noqa: F401  (fixture)
    fake_provider,
    register,
    rows,
    seed_long_run,
    truncated,
)

SPEAKERS_ANSWER = json.dumps(
    {
        "format": "conversation",
        "speakers": [
            {
                "cluster": "SPEAKER_00",
                "name": "Arthur",
                "role": "host",
                "confidence": "high",
                "evidence": "[0:00] \"Don't panic\"",
            },
            {"cluster": "SPEAKER_01", "name": "Marvin", "role": "guest", "confidence": "medium"},
            {"cluster": "SPEAKER_07", "name": "Nobody", "role": "other", "confidence": "low"},
        ],
    }
)


@pytest.fixture
def media(conn):
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    return media_id


# --- the transcript the model sees ---------------------------------------------------------


def test_segment_speakers_credits_each_segment_to_the_cluster_most_of_its_words_carry(conn, media):
    doc = docs.load(conn, media)
    assert chunking.segment_speakers(doc) == ["SPEAKER_00", "SPEAKER_00", "SPEAKER_01", "SPEAKER_01"]


def test_the_speakers_kind_sees_cluster_labels_and_the_summary_kind_does_not(conn, media, monkeypatch):
    provider, calls = fake_provider([SPEAKERS_ANSWER, tasks_answer("summary")])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    tasks.run_task(conn, media_id=media, kind="summary", provider_name="fake", model="fake-1")

    assert "] SPEAKER_00: Don't panic" in calls[0].user
    assert "] SPEAKER_01: Marvin says" in calls[0].user
    assert "SPEAKER_0" not in calls[1].user


def tasks_answer(kind: str) -> str:
    from tests.test_llm_tasks import ANSWERS

    return ANSWERS[kind]


# --- speakers ------------------------------------------------------------------------


def test_a_speakers_answer_is_stored_as_the_validated_mapping(conn, media, monkeypatch):
    provider, _calls = fake_provider([SPEAKERS_ANSWER])
    register(monkeypatch, provider)

    output_id = tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    (row,) = rows(conn, media, "speakers")
    assert row["id"] == output_id
    stored = json.loads(row["content"])
    assert stored["format"] == "conversation"
    assert [s["cluster"] for s in stored["speakers"]] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_07"]
    assert stored["speakers"][1]["evidence"] == ""  # defaulted, not refused


def test_a_speakers_answer_that_names_half_still_validates_and_prose_fails_with_its_text(
    conn, media, monkeypatch
):
    partial = json.dumps({"speakers": [{"cluster": "SPEAKER_00"}]})
    provider, _ = fake_provider([partial, "I cannot tell who these people are."])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    stored = json.loads(rows(conn, media, "speakers")[0]["content"])
    # Confidence defaults to 0 since TASK-024, not to the word "low": an answer
    # that did not say how sure it was has not earned an automatic write, and a
    # default that could clear the threshold is how a gate stops being one.
    assert stored["speakers"] == [
        {"cluster": "SPEAKER_00", "name": "", "role": "", "confidence": 0.0, "evidence": "", "notes": ""}
    ]

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    assert "I cannot tell who these people are" in str(caught.value)
    assert len(rows(conn, media, "speakers")) == 1


def test_a_private_recording_refuses_a_cloud_speakers_pass_before_reading_words(conn, media, monkeypatch):
    provider, calls = fake_provider([SPEAKERS_ANSWER], is_local=False)
    register(monkeypatch, provider)
    with db.LOCK:
        conn.execute("UPDATE media SET private=1 WHERE id=?", (media,))
        conn.commit()

    with pytest.raises(privacy.PrivacyRefused):
        tasks.plan_task(conn, media_id=media, kind="speakers", provider_name="fake")
    assert calls == []


def test_a_long_recording_takes_speaker_notes_per_chunk_and_combines_them(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)

    def script(req):
        if CHUNK_MARKER in req.user:
            assert "SPEAKER_00:" in req.user
            return "[0:00] SPEAKER_00 says word00 a lot; nobody is named"
        return SPEAKERS_ANSWER

    provider, calls = fake_provider(script)
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media_id, kind="speakers", provider_name="fake", model="fake-1", budget_tokens=100)

    assert len([c for c in calls if CHUNK_MARKER in c.user]) >= 2
    assert "who each SPEAKER_XX label is" in calls[0].user


# --- cleanup, a concat task ------------------------------------------------------------------


def test_cleanup_over_a_short_recording_is_one_call_and_stores_the_text(conn, media, monkeypatch):
    provider, calls = fake_provider(["**Arthur:** Don't panic.\n\n**Marvin:** I am depressed."])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    assert len(calls) == 1
    assert "Clean this transcript up" in calls[0].user
    assert "SPEAKER_00:" in calls[0].user
    (row,) = rows(conn, media, "cleanup")
    assert row["content"].startswith("**Arthur:**")


def test_cleanup_over_a_long_recording_rewrites_each_chunk_and_joins_the_parts(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)

    def script(req):
        assert CHUNK_MARKER not in req.user, "a concat task never asks for notes"
        first = req.user.split("\n")[-1][:24]
        return f"cleaned <{first}>"

    provider, calls = fake_provider(script)
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100)

    parts = [r for r in rows(conn, media_id) if r["kind"].startswith("cleanup:chunk:")]
    (final,) = rows(conn, media_id, "cleanup")
    assert len(parts) >= 2
    assert len(calls) == len(parts)
    assert final["content"] == "\n\n".join(p["content"] for p in parts)
    params = json.loads(final["params_json"])
    assert params["calls"] == len(parts)
    assert params["chunk_output_ids"] == [p["id"] for p in parts]
    # No overlap: a seam rewritten twice would be a sentence said twice.
    ids = [json.loads(p["params_json"])["segment_ids"] for p in parts]
    for a, b in zip(ids, ids[1:]):
        assert not set(a) & set(b)


def test_a_resumed_cleanup_reuses_the_parts_it_already_paid_for(conn, monkeypatch):
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    seen = {"n": 0}

    def flaky(req):
        seen["n"] += 1
        if seen["n"] >= 2:  # every call after the first, so the retry policy gives up too
            raise base.Unreachable("the endpoint went away")
        return f"part {seen['n']}"

    provider, _ = fake_provider(flaky)
    register(monkeypatch, provider)
    with pytest.raises(base.Unreachable):
        tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100)
    before = len([r for r in rows(conn, media_id) if r["kind"].startswith("cleanup:chunk:")])
    assert before == 1

    again, calls = fake_provider(lambda req: "later part")
    register(monkeypatch, again)
    tasks.run_task(conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100)

    parts = [r for r in rows(conn, media_id) if r["kind"].startswith("cleanup:chunk:")]
    assert len(calls) == len(parts) - before  # the first part was not asked again
    assert parts[0]["content"] == "part 1"


STAMP = re.compile(r"^\[(\d+:\d{2})\] ", re.MULTILINE)
"""The `[m:ss] ` a transcript line starts with. seed_long_run starts a segment
every five seconds, so in these tests a stamp names exactly one segment."""


def echo_the_lines(req: base.ChatRequest) -> str:
    """A cleaning that is exactly the transcript lines it was given, so the
    joined answer shows which segments each part covered."""
    return "\n".join(line for line in req.user.splitlines() if STAMP.match(line))


def test_a_one_call_cleanup_cut_off_at_the_cap_is_refused_and_nothing_is_stored(
    conn, media, monkeypatch
):
    """Row 15 (2026-09-11): media 7's cleanup, gpt-5.6-luna, 12,000 of 12,000
    tokens, finish 'length' - stored, and offered to the gate as a cleaning.

    `_collect_parts` has refused a part cut off at the cap since TASK-026, but
    a recording short enough for one call never reaches it: `generate`'s
    single-call branch stored whatever came back. For a concat kind the answer
    *is* the transcript, so a cut-off one has lost its tail without a word -
    and this one keeps 65% of the words, which the 0.55 gate lets through.
    """
    assert [kind for kind, spec in tasks.TASKS.items() if spec.combine == "concat"] == [
        "cleanup"
    ], "the refusal is scoped to concat kinds; cleanup is the only one today"
    cut_off = (
        "[0:00] SPEAKER_00: Don't panic, the towel is still the most important item. "
        "The answer to life, the universe and everything is forty-two.\n\n"
        "[0:10] SPEAKER_01: Marvin says the improbability drive makes him"
    )
    provider, calls = fake_provider([truncated(cut_off)])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    assert len(calls) == 1
    assert "length" in str(caught.value)
    assert rows(conn, media) == [], "a cleaning the model never finished was stored anyway"
    assert tasks.clean_reading(conn, _run_id(conn, media)) is None


def test_a_part_cut_off_at_the_cap_is_refused_and_the_parts_before_it_are_kept(
    conn, monkeypatch
):
    """The chunked half of the same rule (TASK-026), which now shares its
    helper with the one-call half: the cut-off part is not stored, the parts
    already paid for are, and no joined reading is written."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    seen = {"n": 0}

    def second_is_cut_off(req):
        seen["n"] += 1
        return truncated("[0:15] SPEAKER_00: word00 word01") if seen["n"] == 2 else echo_the_lines(req)

    provider, calls = fake_provider(second_is_cut_off)
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(
            conn, media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1",
            budget_tokens=100,
        )

    assert "length" in str(caught.value)
    assert len(calls) == 2, "the first cut-off part ends the run"
    assert [r["kind"] for r in rows(conn, media_id)] == ["cleanup:chunk:0"]


@pytest.mark.parametrize(
    "spent, expected",
    [
        # Measured 2026-09-11 in the live check of TASK-029: openai/gpt-5-mini
        # through OpenRouter spent 5,632 of 6,000 tokens reasoning, upstream
        # Azure, after the hint was refused - and the board named only the cap
        # and finish_reason='length'.
        (
            dict(completion_tokens=6000, reasoning_tokens=5632, upstream="Azure", hint_sent=False),
            ["completion_tokens=6000", "reasoning_tokens=5632", "upstream='Azure'",
             "reasoning hint refused and dropped"],
        ),
        # Ollama's shape, numbers constructed: thinking characters are the only
        # reasoning measure it gives, and its hint is never dropped.
        (
            dict(completion_tokens=6000, reasoning_chars=21000, hint_sent=True),
            ["completion_tokens=6000", "reasoning_chars=21000", "reasoning hint sent"],
        ),
    ],
    ids=["gpt-5-mini-azure", "ollama-shaped"],
)
def test_a_cut_off_part_names_what_it_spent_and_whether_the_hint_went(
    conn, media, monkeypatch, spent, expected
):
    """A refused part writes no row, so the error is the only record the jobs
    board keeps of the call: it has to carry what the row would have - the
    spend, the upstream and the hint - or the reason it ran out is lost."""
    cut_off = dataclasses.replace(truncated("[0:00] SPEAKER_00: Don't panic, the towel"), **spent)
    provider, _ = fake_provider([cut_off])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse) as caught:
        tasks.run_task(conn, media_id=media, kind="cleanup", provider_name="fake", model="fake-1")

    message = str(caught.value)
    assert "finish_reason='length'" in message
    missing = [words for words in expected if words not in message]
    assert missing == [], message


def test_a_stored_part_cut_on_other_boundaries_is_asked_again(conn, monkeypatch):
    """A part is reused only for the segments it was made from.

    The live receipt is row 14: media 12's cleanup part 0, made on 6,000-token
    chunks, carries 184 segment ids. Under 3,000-token chunks part 0 covers
    fewer segments; reused by its index it would stand in for them, the next
    part would repeat the stretch in between, and the gate would refuse media
    12 on every run - the wrong words, paid for once and served forever.
    """
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    kwargs = dict(media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1")

    first, first_calls = fake_provider(echo_the_lines)
    register(monkeypatch, first)
    tasks.run_task(conn, budget_tokens=100, **kwargs)

    again, again_calls = fake_provider(echo_the_lines)
    register(monkeypatch, again)
    tasks.run_task(conn, budget_tokens=60, **kwargs)

    wide = tasks.plan_task(conn, budget_tokens=100, **kwargs).chunks
    narrow = tasks.plan_task(conn, budget_tokens=60, **kwargs).chunks
    assert wide[0].segment_ids != narrow[0].segment_ids, "the test needs boundaries that moved"
    # Every part of the narrow plan differs from the wide part stored under its
    # index (or has none), so every one of them is asked.
    assert len(again_calls) == len(narrow), "a part cut on other boundaries was reused"

    final = rows(conn, media_id, "cleanup")[-1]["content"]
    stamps = STAMP.findall(final)
    assert len(stamps) == 12 and len(set(stamps)) == 12, (
        f"each segment belongs in the reading once, got {stamps}"
    )


def test_a_cleanup_chunk_is_cut_to_the_concat_limit_not_the_answer_cap(conn, monkeypatch):
    """A part at the gate's ceiling is about 1.07 x its chunk, so a 5,999-token
    chunk needs about 6,420 tokens of answer from a 6,000-token cap: no room
    even with no reasoning at all. At 3,000 it needs about 3,210 and leaves
    about 2,790 for reasoning.

    The limit is its own number rather than a share of the cap, so a bigger
    cap buys reasoning room instead of bigger chunks - which is what planning
    the same recording at 12,000 shows. Planning only: nothing is sent."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=300)
    provider, calls = fake_provider(echo_the_lines)  # a cloud provider: a 128k window
    register(monkeypatch, provider)
    kwargs = dict(media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1")

    at_the_cap = max(chunk.tokens for chunk in tasks.plan_task(conn, **kwargs).chunks)
    assert at_the_cap <= 3000, f"a chunk as big as the answer cap: {at_the_cap} tokens"

    bigger_cap = tasks.plan_task(conn, max_output_tokens=12_000, **kwargs)
    assert bigger_cap.max_output_tokens == 12_000
    assert max(chunk.tokens for chunk in bigger_cap.chunks) <= 3000, "the chunks grew with the cap"

    assert tasks.CONCAT_CHUNK_TOKENS == 3000
    assert calls == []


def test_a_stored_part_that_does_not_name_its_segments_is_asked_again(conn, monkeypatch):
    """The safe direction for a row that cannot be judged: a part with no
    `segment_ids` cannot prove it covers these segments, so it is asked again
    rather than trusted."""
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=12)
    kwargs = dict(
        media_id=media_id, kind="cleanup", provider_name="fake", model="fake-1", budget_tokens=100
    )
    first, _ = fake_provider(echo_the_lines)
    register(monkeypatch, first)
    tasks.run_task(conn, **kwargs)

    with db.LOCK:
        for row in conn.execute(
            "SELECT id, params_json FROM llm_output WHERE kind LIKE 'cleanup:chunk:%'"
        ).fetchall():
            params = json.loads(row["params_json"])
            params.pop("segment_ids")
            conn.execute(
                "UPDATE llm_output SET params_json=? WHERE id=?", (json.dumps(params), row["id"])
            )
        conn.commit()

    again, again_calls = fake_provider(echo_the_lines)
    register(monkeypatch, again)
    tasks.run_task(conn, **kwargs)

    assert len(again_calls) == len(tasks.plan_task(conn, **kwargs).chunks)


# --- confidence as a number (TASK-024) ---------------------------------------------


def test_a_numeric_confidence_survives_validation(conn):
    payload = tasks.Speakers.model_validate_json(
        json.dumps({"speakers": [{"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 96}]})
    )

    assert payload.speakers[0].confidence == 96.0


def test_the_old_word_scale_still_validates_but_lands_below_the_bar(conn):
    """A model that answers 'high' where a number was asked for has not given
    the evidence the threshold needs, so the word maps to just under it: the
    guess is still shown and can still be accepted by hand, but nothing is
    written unattended on the strength of a word.

    Tolerated rather than refused because refusing would turn a model that
    answered the older way into a failed job, and the answer is still useful.
    """
    payload = tasks.Speakers.model_validate_json(
        json.dumps(
            {
                "speakers": [
                    {"cluster": "SPEAKER_00", "name": "A", "confidence": "high"},
                    {"cluster": "SPEAKER_01", "name": "B", "confidence": "medium"},
                    {"cluster": "SPEAKER_02", "name": "C", "confidence": "low"},
                ]
            }
        )
    )

    high, medium, low = [s.confidence for s in payload.speakers]
    assert high < tasks.SPEAKER_CONFIDENCE_THRESHOLD
    assert medium < high and low < medium


def test_a_confidence_that_means_nothing_is_no_confidence_at_all(conn):
    """Empty, absent or unparseable: zero, which is below every threshold.
    Never a default that would let something through."""
    payload = tasks.Speakers.model_validate_json(
        json.dumps(
            {
                "speakers": [
                    {"cluster": "SPEAKER_00", "name": "A"},
                    {"cluster": "SPEAKER_01", "name": "B", "confidence": ""},
                    {"cluster": "SPEAKER_02", "name": "C", "confidence": "very sure indeed"},
                ]
            }
        )
    )

    assert [s.confidence for s in payload.speakers] == [0.0, 0.0, 0.0]


def test_a_percentage_written_as_a_fraction_is_read_as_one(conn):
    """0.96 and 96 mean the same thing to a person and this must not be the
    difference between applying a name and not."""
    payload = tasks.Speakers.model_validate_json(
        json.dumps({"speakers": [{"cluster": "SPEAKER_00", "name": "A", "confidence": 0.96}]})
    )

    assert payload.speakers[0].confidence == 96.0


def test_the_speakers_prompt_asks_for_a_number(conn):
    body = tasks.render_prompt("speakers", transcript="x", source_label="y", prompt="", known_labels=[])

    assert "0-100" in body or "0 to 100" in body


# --- applying the mapping (TASK-024) -----------------------------------------------


def _named(conn, run_id):
    return {
        row["cluster_label"]: (row["display_name"], row["source"], row["confidence"])
        for row in conn.execute(
            "SELECT cluster_label, display_name, source, confidence FROM speaker_label"
            " WHERE run_id=?",
            (run_id,),
        )
    }


def _answer(*speakers) -> str:
    return json.dumps({"speakers": list(speakers)})


def _run_id(conn, media_id):
    return conn.execute(
        "SELECT id FROM run WHERE media_id=? AND is_current=1", (media_id,)
    ).fetchone()["id"]


def test_a_confident_name_is_written_by_the_job_itself(conn, media, monkeypatch):
    """The whole point of TASK-024: no click. And the row says where the name
    came from, so 'why does this say Arthur' is answerable from the row."""
    provider, _ = fake_provider(
        [_answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 96})]
    )
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1"
    )

    named = _named(conn, _run_id(conn, media))
    assert named["SPEAKER_00"] == ("Arthur", "llm", 96.0)
    row = conn.execute("SELECT llm_output_id FROM speaker_label").fetchone()
    assert row["llm_output_id"] == output_id


def test_a_name_at_or_below_the_threshold_is_not_written(conn, media, monkeypatch):
    """90 is the bar and it is not inclusive-by-accident: a cluster that did
    not clear it keeps its default and stays a suggestion."""
    provider, _ = fake_provider(
        [
            _answer(
                {"cluster": "SPEAKER_00", "name": "Maybe", "confidence": 90},
                {"cluster": "SPEAKER_01", "name": "Unsure", "confidence": 89.9},
                {"cluster": "SPEAKER_02", "name": "Sure", "confidence": 90.1},
            )
        ]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert set(_named(conn, _run_id(conn, media))) == {"SPEAKER_02"}


def test_a_name_a_person_typed_is_never_overwritten(conn, media, monkeypatch):
    """The rule that makes running this unattended safe at all."""
    run_id = _run_id(conn, media)
    with db.LOCK:
        conn.execute(
            "INSERT INTO speaker_label(run_id, cluster_label, display_name, source)"
            " VALUES (?, 'SPEAKER_00', 'Ford', 'human')",
            (run_id,),
        )
        conn.commit()
    provider, _ = fake_provider(
        [_answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 99})]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert _named(conn, run_id)["SPEAKER_00"][:2] == ("Ford", "human")


def test_a_cluster_with_only_a_role_is_left_alone(conn, media, monkeypatch):
    """"Guest" is not a name. Writing it would replace "Speaker 2" with
    something no more informative and harder to notice as a default."""
    provider, _ = fake_provider(
        [_answer({"cluster": "SPEAKER_00", "name": "", "role": "guest", "confidence": 99})]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert _named(conn, _run_id(conn, media)) == {}


def test_a_role_in_the_name_field_is_not_a_name(conn, media, monkeypatch):
    """The prompt asks for a role word - Host, Co-host, Guest, Expert - in
    `name` when no name is known, and a model can be sure of a role. Found in
    review 2026-09-11: "Host" at 95 was written over an inherited "Arthur",
    which every re-transcription now puts in front of the pass (TASK-037)."""
    run_id = _run_id(conn, media)
    with db.LOCK:
        conn.execute(
            "INSERT INTO speaker_label(run_id, cluster_label, display_name, source, confidence)"
            " VALUES (?, 'SPEAKER_00', 'Arthur', 'llm', 95.0)",
            (run_id,),
        )
        conn.commit()
    provider, _ = fake_provider([_answer(
        {"cluster": "SPEAKER_00", "name": "Host", "role": "host", "confidence": 95},
        {"cluster": "SPEAKER_01", "name": "Guest 2", "role": "guest", "confidence": 97},
    )])
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert _named(conn, run_id) == {"SPEAKER_00": ("Arthur", "llm", 95.0)}


@pytest.mark.parametrize("name", ["Host", "co-host", "The host", "Guest 2", "Expert", "Speaker 1", "Unknown"])
def test_role_words_are_recognised_whatever_their_case_or_number(name):
    assert tasks.is_role_word(name)


@pytest.mark.parametrize("name", ["Sarah", "Josh Bressers", "Dr. Smith", "Ad", "Guestrin"])
def test_a_name_is_not_a_role_word(name):
    assert not tasks.is_role_word(name)


class _RenamedMidway:
    """A connection on which a person renames SPEAKER_00 in the web process
    between apply_speakers reading the human rows and writing its own."""

    def __init__(self, conn, run_id):
        self._conn, self._run_id = conn, run_id

    def execute(self, sql, *args):
        cursor = self._conn.execute(sql, *args)
        if sql.lstrip().upper().startswith("SELECT") and "source='human'" in sql:
            rows = cursor.fetchall()
            self._conn.execute(
                "INSERT INTO speaker_label(run_id, cluster_label, display_name, source)"
                " VALUES (?, 'SPEAKER_00', 'Trillian', 'human')",
                (self._run_id,),
            )
            self._conn.commit()
            return iter(rows)
        return cursor

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_rename_that_lands_while_the_pass_writes_is_kept(conn, media):
    """The human rows are read, then the names are written; the web process can
    commit a rename in between. Found in review 2026-09-11. The write itself
    refuses a human row, so the rename stands and the cluster is reported as
    left, not named."""
    run_id = _run_id(conn, media)
    with db.LOCK:
        output_id = conn.execute(
            "INSERT INTO llm_output(media_id, kind, provider, model, prompt_version, content, created_at)"
            " VALUES (?, 'speakers', 'p', 'm', '1', '{}', 0.0) RETURNING id",
            (media,),
        ).fetchone()["id"]
        conn.commit()
    plan = SimpleNamespace(run_id=run_id)  # apply_speakers reads nothing else of it
    payload = tasks.Speakers(speakers=[tasks.SpeakerGuess(cluster="SPEAKER_00", name="Arthur", confidence=99)])

    result = tasks.apply_speakers(_RenamedMidway(conn, run_id), plan, payload, output_id)

    assert _named(conn, run_id)["SPEAKER_00"][:2] == ("Trillian", "human")
    assert (result["named"], result["left"]) == ([], ["SPEAKER_00"])


def test_running_it_again_updates_rather_than_duplicates(conn, media, monkeypatch):
    """speaker_label is UNIQUE(run_id, cluster_label); a second confident pass
    is a correction, not a second opinion to store beside the first."""
    provider, _ = fake_provider(
        [
            _answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 95}),
            _answer({"cluster": "SPEAKER_00", "name": "Zaphod", "confidence": 97}),
        ]
    )
    register(monkeypatch, provider)

    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")
    tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-2")

    named = _named(conn, _run_id(conn, media))
    assert named == {"SPEAKER_00": ("Zaphod", "llm", 97.0)}


def test_a_failed_analysis_leaves_the_speakers_as_they_were(conn, media, monkeypatch):
    """The failure path, seen twice for real on 2026-09-10 when two episodes
    came back truncated. Nothing half-applied, nothing silently renamed: the
    job fails carrying the model's own words, and the run keeps its defaults
    for a person to sort out."""
    provider, _ = fake_provider(["I could not work out who anybody is, sorry."])
    register(monkeypatch, provider)

    with pytest.raises(base.BadResponse):
        tasks.run_task(conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1")

    assert _named(conn, _run_id(conn, media)) == {}


def test_the_analysis_is_still_readable_after_its_names_were_applied(conn, media, monkeypatch):
    """Accountability outlives the act. The row that got the name points at
    the analysis, and the analysis still holds the quote it rested on."""
    provider, _ = fake_provider(
        [
            _answer(
                {
                    "cluster": "SPEAKER_00",
                    "name": "Arthur",
                    "confidence": 97,
                    "evidence": '[0:07] "My name is Arthur."',
                }
            )
        ]
    )
    register(monkeypatch, provider)

    output_id = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1"
    )

    (row,) = rows(conn, media, "speakers")
    assert row["id"] == output_id
    stored = json.loads(row["content"])
    assert stored["speakers"][0]["evidence"] == '[0:07] "My name is Arthur."'

    label = conn.execute(
        "SELECT llm_output_id, confidence FROM speaker_label WHERE cluster_label='SPEAKER_00'"
    ).fetchone()
    assert (label["llm_output_id"], label["confidence"]) == (output_id, 97.0)


def test_a_second_analysis_does_not_erase_the_first(conn, media, monkeypatch):
    """One key, never an overwrite. What an earlier model decided is evidence
    about that model on that day, and a table that replaced it would be making
    a claim about the present instead of keeping a record."""
    provider, _ = fake_provider(
        [
            _answer({"cluster": "SPEAKER_00", "name": "Arthur", "confidence": 95}),
            _answer({"cluster": "SPEAKER_00", "name": "Zaphod", "confidence": 99}),
        ]
    )
    register(monkeypatch, provider)

    first = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-1"
    )
    second = tasks.run_task(
        conn, media_id=media, kind="speakers", provider_name="fake", model="fake-2"
    )

    stored = rows(conn, media, "speakers")
    assert {row["id"] for row in stored} == {first, second}
    assert first != second
