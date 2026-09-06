"""The settings page's "test this provider" button (Phase 5 Task 6).

A provider test answers one question a settings table cannot: *does this
actually work from here, right now* - is the daemon up, is the key the one that
resolves, is the model id real on this account. `available()` only says whether
a key was found, and `models()` only that a list came back; neither has ever
completed anything.

It runs as an `llm` job, like every other call in this app, because ADR-001
gives the web process no way to reach a provider. Three facts shape the file
and each one is a test below.

**It is about no recording, and that is the whole privacy story.** `llm.chat()`
is the front door for a transcript's words and it takes a `media_id` so
`assert_allowed` can refuse. A probe has no media id, opens no transcript and
interpolates nothing: its prompt is two module constants. There is no text of
the user's in it to refuse, which is why it does not go through that door -
and why `test_a_probe_carries_nothing_of_the_users` asserts the request that
reaches the provider is *exactly* those constants, rather than arguing it.

**The result is recorded before the verdict.** `doctor.gpu_stage` set this
precedent: a red check still leaves its detail where the settings page reads
it, and only then does the job fail. A test that fails is the most useful test
there is, and losing its reason because the job stopped would be perverse.

**One attempt, not three.** `with_retry` is right when a caller wants an
answer; a button that asks "is this working" wants the truth about now. Three
attempts with backoff would turn a down daemon into fifteen seconds of silence
and a rate limit into a lie.
"""

from __future__ import annotations

import json

import pytest

from scribe import db, jobs, llm, paths, runner
from scribe.llm import base, selftest
from scribe.stages import llm_stage
from tests.seed import seed_media, seed_run
from tests.test_llm_tasks import fake_provider, register


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A tmp database that `runner.main()` also finds via `paths.DB_PATH`."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


def queue_test(conn, provider_name: str, model: str | None = None) -> int:
    params = {"kind": selftest.KIND, "provider": provider_name}
    if model:
        params["model"] = model
    return jobs.enqueue(conn, llm_stage.JOB_TYPE, None, params)


def job_row(conn, job_id: int) -> dict:
    return dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())


# --- what a probe asks, and of whom ---------------------------------------------------


def test_a_probe_asks_for_one_word_and_records_what_came_back(conn, monkeypatch):
    provider, calls = fake_provider(["ok"])
    register(monkeypatch, provider)

    result = selftest.probe(conn, provider_name="fake")

    assert result.ok is True
    assert result.provider == "fake"
    assert result.model == "fake-1"  # the provider's own default
    assert result.answer == "ok"
    assert result.completion_tokens == 7
    assert len(calls) == 1


def test_a_probe_carries_nothing_of_the_users(conn, monkeypatch):
    """The request is the two constants, and the assertion is equality rather
    than absence: "the transcript is not in it" would still pass if some other
    text of the user's crept in."""
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    provider, calls = fake_provider(["ok"])
    register(monkeypatch, provider)

    selftest.probe(conn, provider_name="fake")

    assert calls[0].system == selftest.PROBE_SYSTEM
    assert calls[0].user == selftest.PROBE_USER
    assert calls[0].json_schema is None


def test_a_pinned_recording_on_the_same_machine_does_not_refuse_a_probe(conn, monkeypatch):
    """The pin protects a recording's text from leaving. A probe carries none,
    so there is nothing here to refuse - and the fake provider is deliberately
    not local, so a refusal would have to come from somewhere."""
    media_id = seed_media(conn, title="Secret")
    conn.execute("UPDATE media SET private=1 WHERE id=?", (media_id,))
    conn.commit()
    provider, _calls = fake_provider(["ok"], is_local=False)
    register(monkeypatch, provider)

    assert selftest.probe(conn, provider_name="fake").ok is True


def test_a_named_model_is_the_one_asked(conn, monkeypatch):
    provider, calls = fake_provider(["ok"])
    register(monkeypatch, provider)

    result = selftest.probe(conn, provider_name="fake", model="fake-9")

    assert calls[0].model == "fake-9"
    assert result.model == "fake-9"


def test_a_provider_that_cannot_answer_is_a_result_rather_than_an_exception(conn, monkeypatch):
    """`probe` returning instead of raising is what lets the stage record the
    reason before failing the job."""
    provider, _calls = fake_provider([base.Unreachable("Ollama is not running at 127.0.0.1")])
    register(monkeypatch, provider)

    result = selftest.probe(conn, provider_name="fake")

    assert result.ok is False
    assert "Ollama is not running" in result.detail
    assert result.answer == ""


def test_a_probe_tries_once(conn, monkeypatch):
    provider, calls = fake_provider(
        [base.RateLimited("slow down"), base.RateLimited("slow down"), "ok"]
    )
    register(monkeypatch, provider)

    assert selftest.probe(conn, provider_name="fake").ok is False
    assert len(calls) == 1


def test_an_unknown_provider_is_a_value_error_not_a_result(conn):
    with pytest.raises(ValueError, match="unknown LLM provider"):
        selftest.probe(conn, provider_name="deep-thought")


# --- what the settings page reads back -------------------------------------------------


def test_a_result_round_trips_through_its_setting_row(conn, monkeypatch):
    provider, _calls = fake_provider(["ok"])
    register(monkeypatch, provider)

    selftest.store_result(conn, selftest.probe(conn, provider_name="fake", job_id=42))
    read = selftest.last_result(conn, "fake")

    assert read is not None
    assert (read.ok, read.answer, read.model, read.job_id) == (True, "ok", "fake-1", 42)


def test_each_provider_keeps_its_own_result(conn, monkeypatch):
    one, _ = fake_provider(["ok"], name="one")
    two, _ = fake_provider([base.AuthError("no key for two")], name="two")
    register(monkeypatch, one)
    register(monkeypatch, two)

    selftest.store_result(conn, selftest.probe(conn, provider_name="one"))
    selftest.store_result(conn, selftest.probe(conn, provider_name="two"))

    assert selftest.last_result(conn, "one").ok is True
    assert selftest.last_result(conn, "two").ok is False
    assert selftest.last_result(conn, "three") is None


def test_a_row_that_cannot_be_read_is_no_result_rather_than_a_broken_page(conn):
    conn.execute(
        "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
        (selftest.setting_key("fake"), "{not json"),
    )
    conn.commit()

    assert selftest.last_result(conn, "fake") is None


# --- as a job, through the runner -----------------------------------------------------


def test_a_provider_test_runs_as_a_job_with_no_recording(conn, monkeypatch):
    """End to end through `runner.main`, because the enqueue is the easy half.

    An `llm` job normally needs a media id - `llm_stage._media_id` raises
    without one - so this is the test that proves a probe job walks the same
    three stages without one.
    """
    provider, calls = fake_provider(["ok"])
    register(monkeypatch, provider)
    job_id = queue_test(conn, "fake")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    row = job_row(conn, job_id)
    assert row["status"] == "done"
    assert row["media_id"] is None
    assert len(calls) == 1
    stored = selftest.last_result(conn, "fake")
    assert stored.ok is True and stored.job_id == job_id


def test_a_failed_provider_test_fails_its_job_and_still_leaves_the_reason(conn, monkeypatch):
    provider, _calls = fake_provider([base.AuthError("no key found for fake")])
    register(monkeypatch, provider)
    job_id = queue_test(conn, "fake")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = job_row(conn, job_id)
    assert row["status"] == "failed"
    assert "no key found for fake" in (row["error_detail"] or "")
    stored = selftest.last_result(conn, "fake")
    assert stored.ok is False and "no key found for fake" in stored.detail


def test_the_probe_job_says_on_the_board_what_it_asked_and_what_it_got(conn, monkeypatch):
    provider, _calls = fake_provider(["ok"])
    register(monkeypatch, provider)
    job_id = queue_test(conn, "fake", model="fake-9")
    jobs.claim_next(conn)
    runner.main([str(job_id)])

    events = [
        dict(row)
        for row in conn.execute(
            "SELECT kind, payload_json FROM job_event WHERE job_id=? ORDER BY id", (job_id,)
        )
    ]
    payloads = [json.loads(e["payload_json"]) for e in events if e["kind"] == "llm-test"]
    assert payloads, [e["kind"] for e in events]
    assert payloads[-1]["provider"] == "fake"
    assert payloads[-1]["model"] == "fake-9"
    assert payloads[-1]["ok"] is True


def test_the_probe_handler_is_its_own_and_not_the_preset_one(conn):
    assert llm_stage.handler_for(selftest.KIND) is not llm_stage.handler_for("summary")
    assert llm_stage.handler_for(selftest.KIND) is not llm_stage.handler_for("chat")
