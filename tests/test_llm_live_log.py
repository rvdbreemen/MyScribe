"""An llm job shows its work while it runs (TASK-085).

ADR-014: the log is observation - nothing here decides anything, and no test
asserts that it does. What these pin down is that every call is seen, that
what is written stays bounded, and that the conclusion is stated in the kind's
own terms rather than as a blob.
"""

from __future__ import annotations

import json

import pytest

from scribe.llm import tasks


def test_an_excerpt_keeps_both_ends_and_says_what_it_dropped():
    """A transcript chunk is thousands of tokens; the useful parts of a prompt
    for a person watching are how it starts and how it ends."""
    text = "A" * 400 + "MIDDLE" + "Z" * 400

    got = tasks.excerpt(text, limit=100)

    assert got.startswith("A") and got.rstrip().endswith("Z")
    assert "MIDDLE" not in got
    assert "606" in got, "it must say how many characters were left out"
    assert len(got) < len(text)


def test_a_short_prompt_is_not_excerpted_at_all():
    assert tasks.excerpt("short enough", limit=100) == "short enough"


def test_a_call_is_announced_before_it_is_made_and_again_when_it_answers(monkeypatch):
    """Twice, and the first time matters most. The prompt is knowable the
    moment it goes out and the reply can be minutes later on a local 27B
    model - job 127 sat on `generate` with nothing in its log because both
    events were emitted after the answer.

    `_ask` is the one door: a note, a part, a single answer and a combine all
    pass through it, so a hook there cannot miss a call either way.
    """
    seen: list[tuple] = []

    class Reply:
        text = '{"speakers": []}'
        prompt_tokens = 10
        completion_tokens = 3
        raw_finish_reason = "stop"
        model = "m"

    monkeypatch.setattr(tasks.llm, "chat", lambda *a, **k: Reply())

    tasks._ask(
        None,
        _plan(),
        system="sys",
        user="usr",
        max_output_tokens=100,
        json_schema=None,
        phase="single",
        on_call=lambda *args: seen.append(args),
    )

    assert len(seen) == 2, "one before the call, one after it"
    before, after = seen
    assert before[0] == "single" and before[1].user == "usr"
    assert before[2] is None, "there is no reply yet when the call goes out"
    assert after[2].text == '{"speakers": []}'


def _plan():
    return tasks.TaskPlan(
        media_id=1,
        run_id=1,
        spec=tasks.TASKS["speakers"],
        provider_name="ollama",
        model="m",
        chunks=(),
        title="t",
        duration=1.0,
        budget_tokens=1000,
        max_output_tokens=500,
    )


def test_the_conclusion_is_stated_in_the_kinds_own_terms():
    """"Who is speaking" concluding something means a cluster became a name,
    and that is what a person watching wants to read."""
    answer = json.dumps({"speakers": [
        {"cluster": "SPEAKER_00", "name": "Ad", "role": "co-host"},
        {"cluster": "SPEAKER_02", "name": "Nancy", "role": "host"},
    ]})

    said = tasks.conclusion("speakers", answer)

    assert "SPEAKER_00" in said and "Ad" in said
    assert "SPEAKER_02" in said and "Nancy" in said


def test_a_conclusion_that_cannot_be_read_says_so_rather_than_raising():
    """The log must never be the thing that fails a job (ADR-014)."""
    assert tasks.conclusion("speakers", "not json at all")
    assert tasks.conclusion("summary", json.dumps({"text": "x" * 5000}))


# --- what the job page actually receives -------------------------------------------


def test_the_stage_emits_prompt_reply_and_conclusion(monkeypatch):
    """Two events per call, not one: the prompt is knowable when the call goes
    out, and on a local 27B model the reply can be five minutes later."""
    from scribe.stages import llm_stage

    emitted: list[tuple] = []

    class Ctx:
        conn = None
        job = {"id": 7}
        state: dict = {}

        def report(self, fraction):  # noqa: D401 - a stand-in
            pass

        def cancelled(self):
            return False

    monkeypatch.setattr(llm_stage.jobs, "emit", lambda conn, job_id, kind, **p: emitted.append((kind, p)))

    class Request:
        model = "m"
        system = "S" * 5000
        user = "U" * 5000

    class Reply:
        text = "R" * 5000
        prompt_tokens = 11
        completion_tokens = 22
        raw_finish_reason = "stop"

    watch = llm_stage._watch(Ctx())
    watch("note", Request(), None)   # the call goes out
    watch("note", Request(), Reply())  # and answers

    kinds = [kind for kind, _ in emitted]
    assert kinds == ["llm-prompt", "llm-reply"]
    prompt = dict(emitted[0][1])
    reply = dict(emitted[1][1])
    assert prompt["phase"] == "note" and prompt["prompt_chars"] == 5000
    assert "not shown" in prompt["prompt"], "a 5000-character prompt must be excerpted"
    assert reply["prompt_tokens"] == 11 and reply["finish_reason"] == "stop"


def test_one_call_costs_kilobytes_not_megabytes(monkeypatch):
    """The ceiling that makes this safe to leave on: a dozen calls over a long
    recording must not fill job_event with the recording."""
    from scribe.stages import llm_stage

    emitted: list[tuple] = []
    monkeypatch.setattr(llm_stage.jobs, "emit", lambda conn, job_id, kind, **p: emitted.append((kind, p)))

    class Ctx:
        conn = None
        job = {"id": 7}

    class Request:
        model = "m"
        system = "S" * 200_000
        user = "U" * 200_000

    class Reply:
        text = "R" * 200_000
        prompt_tokens = 1
        completion_tokens = 1
        raw_finish_reason = "stop"

    watch = llm_stage._watch(Ctx())
    watch("single", Request(), None)
    watch("single", Request(), Reply())

    written = sum(len(json.dumps(payload)) for _kind, payload in emitted)
    assert written < 10_000, f"one call wrote {written} characters into the live log"


def test_watching_never_fails_the_job(monkeypatch):
    """ADR-014, at its sharpest: the log is observation. A broken watcher must
    not be able to stop the work it is watching."""
    class Reply:
        text = "ok"
        prompt_tokens = 1
        completion_tokens = 1
        raw_finish_reason = "stop"
        model = "m"

    monkeypatch.setattr(tasks.llm, "chat", lambda *a, **k: Reply())

    def explode(*_args):
        raise RuntimeError("the watcher is broken")

    response = tasks._ask(
        None, _plan(), system="s", user="u", max_output_tokens=10,
        json_schema=None, phase="single", on_call=explode,
    )

    assert response.text == "ok"


# --- "this model cannot do this job" is an outcome ---------------------------------


def test_a_window_that_cannot_hold_the_job_is_its_own_outcome():
    """Robert, 2026-09-19: the conclusion can also be that the model is not
    suitable, and the job's status has to be able to say that. LLM_FAILED
    covers a missing key, a bad model id and an answer of the wrong shape -
    things to fix. This one is a fact about the model that was chosen."""
    from scribe import runner
    from scribe.llm import base

    assert runner._error_code(base.ContextTooLong("no room")) == "MODEL_UNSUITABLE"
    assert runner._error_code(base.BadResponse("nonsense")) == "LLM_FAILED"


def test_a_cut_too_small_to_matter_is_not_announced():
    """A system prompt three characters over the limit came out as
    "... [3 characters not shown] ...", which reads as breakage."""
    text = "A" * 203

    assert tasks.excerpt(text, limit=100) == text
    assert "not shown" in tasks.excerpt("A" * 1000, limit=100)
